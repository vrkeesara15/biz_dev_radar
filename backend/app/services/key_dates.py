"""Key dates per pursuit (SPEC 9, M6-02): create them, keep them honest, acknowledge them.

    await sync_auto_dates(session, pursuit, opportunity, now=now)   # on pursue / watch
    await recalculate_for_opportunity(session, opportunity, now=now)  # on an amendment
    install_key_date_recalc(bus, database)                          # opportunity.amended

Rules
- every auto row comes from `app.core.key_dates.auto_dates`; a row a human created or
  edited (`source = 'user'`) is never moved by the system;
- when an amendment moves the response deadline, auto rows shift, the pursuit's internal
  deadline follows, and any acknowledgement on a shifted row is CLEARED with a note —
  "I have seen this" was about the old date;
- an auto kind the notice no longer supports (the buyer withdrew a Q&A date) is deleted,
  unless somebody had already edited it.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import Database, get_database
from app.core.key_dates import (
    AUTO_KINDS,
    SOURCE_AUTO,
    KeyDate,
    NoticeDates,
    auto_dates,
)
from app.models import Opportunity, Pursuit, PursuitDate
from app.services import pursuits as pursuit_svc
from app.services.events import OPPORTUNITY_AMENDED, Event, EventBus

log = structlog.get_logger(__name__)

DEADLINE_MOVED_NOTE = "the buyer moved the deadline; re-acknowledge this date"


def notice_dates(opportunity: Opportunity) -> NoticeDates:
    return NoticeDates(
        response_due_at=opportunity.response_due_at,
        questions_due_at=opportunity.questions_due_at,
        prebid_meeting_at=opportunity.prebid_meeting_at,
        source_tz=opportunity.source_tz,
    )


@dataclass
class SyncResult:
    created: list[PursuitDate] = field(default_factory=list)
    moved: list[PursuitDate] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)  # user-edited rows left alone

    @property
    def changed(self) -> bool:
        return bool(self.created or self.moved or self.removed)


async def list_dates(session: AsyncSession, pursuit_id: uuid.UUID) -> Sequence[PursuitDate]:
    return (
        (
            await session.execute(
                select(PursuitDate)
                .where(PursuitDate.pursuit_id == pursuit_id)
                .order_by(PursuitDate.at, PursuitDate.kind)
            )
        )
        .scalars()
        .all()
    )


async def sync_auto_dates(
    session: AsyncSession,
    pursuit: Pursuit,
    opportunity: Opportunity,
    *,
    now: datetime | None = None,
    reset_acknowledgements: bool = False,
    calendar_push: bool = False,
) -> SyncResult:
    """Create / shift / drop the auto rows so they match the notice. Never touches a
    `user` row. Also keeps pursuits.internal_due_at (due - 48 h) in step.

    With `calendar_push`, every row that appears, moves or disappears is mirrored into
    the owner's connected Google / Outlook calendar (M6-04); a provider failure is
    recorded on the row and never breaks the recalculation."""
    moment = now or datetime.now(UTC)
    wanted: dict[str, KeyDate] = {
        d.kind: d for d in auto_dates(notice_dates(opportunity), str(opportunity.region), moment)
    }
    existing = {row.kind: row for row in await list_dates(session, pursuit.id)}
    result = SyncResult()

    for kind, target in wanted.items():
        row = existing.get(kind)
        if row is None:
            row = PursuitDate(
                tenant_id=pursuit.tenant_id,
                pursuit_id=pursuit.id,
                kind=kind,
                at=target.at,
                buyer_tz=target.buyer_tz,
                source=SOURCE_AUTO,
                label=target.label,
                note=target.note,
            )
            session.add(row)
            result.created.append(row)
            continue
        if row.source != SOURCE_AUTO:
            result.kept.append(kind)
            continue
        row.buyer_tz = target.buyer_tz
        if row.at == target.at:
            continue
        row.at = target.at
        row.sequence += 1  # RFC 5545: calendars only accept a higher SEQUENCE (M6-04)
        row.note = DEADLINE_MOVED_NOTE if reset_acknowledgements else target.note
        if reset_acknowledgements:
            row.acknowledged_at = None
            row.acknowledged_by = None
        result.moved.append(row)

    for kind, row in existing.items():
        if kind in wanted or kind not in AUTO_KINDS or row.source != SOURCE_AUTO:
            continue
        if calendar_push:  # drop the provider event before the row it maps to goes away
            await push_calendar(session, row, action=CALENDAR_DELETE)
        await session.delete(row)
        result.removed.append(kind)

    pursuit.internal_due_at = pursuit_svc.internal_due_at(opportunity.response_due_at)
    await session.flush()
    if calendar_push:
        for row in (*result.created, *result.moved):
            await push_calendar(session, row)
    return result


CALENDAR_UPSERT = "upsert"
CALENDAR_DELETE = "delete"


async def push_calendar(
    session: AsyncSession, date: PursuitDate, *, action: str = CALENDAR_UPSERT
) -> None:
    """Mirror one key date into the owner's connected calendars (M6-04), best effort:
    a calendar problem must never abort the recalculation the deadline asked for."""
    from app.core.config import get_settings
    from app.services.calendar import sync_date

    try:
        await sync_date(session, get_settings(), date, action=action)
    except Exception as exc:  # the feed stays correct either way
        log.warning("key_dates.calendar_push_failed", date_id=str(date.id), error=str(exc))


async def recalculate_for_opportunity(
    session: AsyncSession, opportunity: Opportunity, *, now: datetime | None = None
) -> dict[str, SyncResult]:
    """Re-derive every tracking tenant's auto dates after an amendment moved the deadline."""
    pursuits = (
        (await session.execute(select(Pursuit).where(Pursuit.opportunity_id == opportunity.id)))
        .scalars()
        .all()
    )
    out: dict[str, SyncResult] = {}
    for pursuit in pursuits:
        out[str(pursuit.id)] = await sync_auto_dates(
            session,
            pursuit,
            opportunity,
            now=now,
            reset_acknowledgements=True,
            calendar_push=True,
        )
    return out


def deadline_moved(payload: dict[str, object]) -> bool:
    """True when an opportunity.amended payload says the response deadline changed."""
    changes = payload.get("changes") or []
    if isinstance(changes, list | tuple) and "deadline_moved" in changes:
        return True
    diff = payload.get("diff") or {}
    return isinstance(diff, dict) and "response_due_at" in diff


class KeyDateRecalculator:
    """Subscriber: an amendment that moves the deadline re-dates every pursuit of the
    notice. Failures are logged, never raised — ingestion must not break on a reminder."""

    def __init__(self, database: Database | None = None) -> None:
        self.database = database

    async def __call__(self, event: Event) -> None:
        payload = dict(event.payload)
        if not deadline_moved(payload):
            return
        raw_id = payload.get("opportunity_id")
        if raw_id is None:
            return
        opportunity_id = uuid.UUID(str(raw_id))
        session = event.context.get("session")
        if isinstance(session, AsyncSession):
            await self._run(session, opportunity_id)
            return
        db = self.database or get_database()
        for tenant_id in await self._tenants(db, opportunity_id):
            async with db.session(tenant_id) as scoped:
                await self._run(scoped, opportunity_id)

    async def _tenants(self, db: Database, opportunity_id: uuid.UUID) -> list[uuid.UUID]:
        async with db.owner_session() as session:
            rows = (
                await session.execute(
                    select(Pursuit.tenant_id)
                    .where(Pursuit.opportunity_id == opportunity_id)
                    .distinct()
                )
            ).scalars()
            return [uuid.UUID(str(row)) for row in rows]

    async def _run(self, session: AsyncSession, opportunity_id: uuid.UUID) -> None:
        opportunity = await session.get(Opportunity, opportunity_id)
        if opportunity is None:  # pragma: no cover - the event carries a real id
            return
        results = await recalculate_for_opportunity(session, opportunity)
        for pursuit_id, result in results.items():
            if result.changed:
                log.info(
                    "key_dates.recalculated",
                    pursuit_id=pursuit_id,
                    created=len(result.created),
                    moved=len(result.moved),
                    removed=result.removed,
                    kept=result.kept,
                )


def install_key_date_recalc(bus: EventBus, database: Database | None = None) -> KeyDateRecalculator:
    """Wire the recalculator onto an event bus (API lifespan and the Celery worker)."""
    recalculator = KeyDateRecalculator(database)
    bus.subscribe(OPPORTUNITY_AMENDED, recalculator)
    return recalculator
