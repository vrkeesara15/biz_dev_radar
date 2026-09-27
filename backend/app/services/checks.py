"""Recurring checks (SPEC 9, 4.1, 7; M6-06).

    await expiry_checks_for_tenant(session, settings, dispatcher, tenant_id, today=today)
    await stale_pursuits_for_tenant(session, settings, dispatcher, tenant_id, now=now)
    install_matrix_recheck(bus, database)      # opportunity.amended

Three jobs SPEC 9 asks for:

1. registrations, certifications and insurance expiring in 60 / 30 / 7 days email the
   tenant owner (SPEC 7's "Registration expiring" row). An expired SAM registration (US)
   or DSC (IN) additionally sets `company_profiles.blocked_for_bids`, and pursuing that
   profile then answers 409 until it is renewed and the block is cleared;
2. a pursuit with no activity for five days notifies its owner. "Activity" is
   `pursuits.activity_at`, bumped by `services.pursuits.touch` from every stage move,
   task, comment, key-date edit and agent run;
3. an amendment on a notice whose pursuit is at drafting or later sets
   `matrix_recheck_required` and tells the assignee — the compliance matrix was built
   against the old documents.

One notification per window / per day / per version: the dedupe key does the work, so a
job that runs twice, or catches up after a missed day, never repeats itself.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import pursuit_stages as stages
from app.core.config import Settings
from app.core.db import Database, get_database
from app.core.expiry import (
    KIND_CERTIFICATION,
    KIND_INSURANCE,
    KIND_REGISTRATION,
    ExpiringItem,
    blocks_bidding,
    is_expired,
    window_for,
)
from app.core.preferences import NotificationEvent
from app.core.roles import Role
from app.models import (
    Certification,
    CompanyProfile,
    Insurance,
    Membership,
    Opportunity,
    Pursuit,
    Registration,
    User,
    UserNotificationPrefs,
)
from app.notify.core import Dispatcher, Recipient
from app.notify.core import NotificationEvent as DispatchEvent
from app.notify.unsubscribe import load_unsubscribed
from app.services.events import OPPORTUNITY_AMENDED, Event, EventBus

log = structlog.get_logger(__name__)

EXPIRY_EVENT = NotificationEvent.REGISTRATION_EXPIRY.value
STALE_EVENT = NotificationEvent.PURSUIT_UPDATE.value
RECHECK_EVENT = NotificationEvent.AGENT_QUESTION.value

EXPIRY_SCHEDULE = "0 7 * * *"  # daily, 07:00 UTC
STALE_SCHEDULE = "30 7 * * *"  # daily, just after the expiry sweep
STALE_AFTER_DAYS = 5  # SPEC 9

# the stage from which an amendment forces a matrix re-check
RECHECK_FROM_STAGE = stages.STAGE_DRAFTING
DEFAULT_CHANNELS: tuple[str, ...] = ("in_app", "email")


@dataclass
class CheckRun:
    scanned: int = 0
    notified: int = 0
    blocked: int = 0

    def as_dict(self) -> dict[str, int]:
        return {"scanned": self.scanned, "notified": self.notified, "blocked": self.blocked}


# --- recipients -------------------------------------------------------------------------------


async def _recipient(session: AsyncSession, user_id: uuid.UUID) -> Recipient | None:
    user = await session.get(User, user_id)
    if user is None:
        return None
    prefs = (
        await session.execute(
            select(UserNotificationPrefs).where(UserNotificationPrefs.user_id == user_id)
        )
    ).scalar_one_or_none()
    return Recipient(
        user_id=user_id,
        channels=DEFAULT_CHANNELS,
        email=user.email,
        name=user.name,
        tz=(prefs.tz if prefs is not None else None) or user.tz,
        unsubscribed=await load_unsubscribed(session, user_id),
    )


async def tenant_owners(session: AsyncSession) -> list[uuid.UUID]:
    rows = (
        (
            await session.execute(
                select(Membership.user_id).where(Membership.role == Role.TENANT_OWNER)
            )
        )
        .scalars()
        .all()
    )
    return [uuid.UUID(str(row)) for row in rows]


# --- 1. expiry --------------------------------------------------------------------------------


async def expiring_items(session: AsyncSession) -> list[ExpiringItem]:
    """Every dated credential in the tenant, flattened (SPEC 4.1 / 4.5 tables)."""
    out: list[ExpiringItem] = []
    for row in (await session.execute(select(Registration))).scalars().all():
        if row.expires_on is not None:
            out.append(
                ExpiringItem(
                    kind=KIND_REGISTRATION,
                    subkind=str(row.kind),
                    label=f"{str(row.kind).upper()} registration",
                    expires_on=row.expires_on,
                    profile_id=str(row.profile_id),
                    item_id=str(row.id),
                )
            )
    for cert in (await session.execute(select(Certification))).scalars().all():
        if cert.expires_on is not None:
            out.append(
                ExpiringItem(
                    kind=KIND_CERTIFICATION,
                    subkind=str(cert.kind),
                    label=f"{str(cert.kind).replace('_', ' ')} certification",
                    expires_on=cert.expires_on,
                    profile_id=str(cert.profile_id),
                    item_id=str(cert.id),
                )
            )
    for policy in (await session.execute(select(Insurance))).scalars().all():
        if policy.expires_on is not None:
            out.append(
                ExpiringItem(
                    kind=KIND_INSURANCE,
                    subkind=str(policy.kind),
                    label=f"{str(policy.kind).replace('_', ' ')} insurance",
                    expires_on=policy.expires_on,
                    profile_id=str(policy.profile_id),
                    item_id=str(policy.id),
                )
            )
    # SPEC 4.1 keeps the SAM expiry on the profile itself as well as in `registrations`
    for profile in (await session.execute(select(CompanyProfile))).scalars().all():
        if profile.sam_expires_on is not None:
            out.append(
                ExpiringItem(
                    kind=KIND_REGISTRATION,
                    subkind="sam",
                    label="SAM registration",
                    expires_on=profile.sam_expires_on,
                    profile_id=str(profile.id),
                    item_id=f"profile:{profile.id}",
                )
            )
    return out


async def apply_bid_block(session: AsyncSession, today: date) -> list[uuid.UUID]:
    """SPEC 4.1: an expired SAM or DSC blocks the profile for bids. Returns the blocked
    profile ids. Renewing clears the block on the next daily run."""
    expired_by_profile: dict[uuid.UUID, list[str]] = {}
    for item in await expiring_items(session):
        if item.kind != KIND_REGISTRATION or not blocks_bidding(item.subkind):
            continue
        if is_expired(item.expires_on, today):
            expired_by_profile.setdefault(uuid.UUID(item.profile_id), []).append(item.subkind)
    blocked: list[uuid.UUID] = []
    for profile in (await session.execute(select(CompanyProfile))).scalars().all():
        should_block = profile.id in expired_by_profile
        if profile.blocked_for_bids != should_block:
            profile.blocked_for_bids = should_block
        if should_block:
            blocked.append(profile.id)
    await session.flush()
    return blocked


async def expiry_checks_for_tenant(
    session: AsyncSession,
    settings: Settings,
    dispatcher: Dispatcher,
    tenant_id: uuid.UUID,
    *,
    today: date | None = None,
) -> CheckRun:
    """Daily: 60/30/7-day reminders to the tenant owner, plus the SAM/DSC bid block."""
    day = today or datetime.now(UTC).date()
    run = CheckRun()
    owners = [r for r in [await _recipient(session, u) for u in await tenant_owners(session)] if r]
    for item in await expiring_items(session):
        run.scanned += 1
        window = window_for(item.expires_on, day)
        if window is None or not owners:
            continue
        event = DispatchEvent(
            event_type=EXPIRY_EVENT,
            tenant_id=tenant_id,
            payload={
                "title": item.label,
                "buyer": None,
                **item.as_payload(window, day),
            },
            occurred_at=datetime.combine(day, datetime.min.time(), tzinfo=UTC),
            dedupe_key=f"expiry:{item.item_id}:{window}",
        )
        result = await dispatcher.dispatch(session, event, owners)
        run.notified += len(result.notifications)
    run.blocked = len(await apply_bid_block(session, day))
    return run


# --- 2. stale pursuits --------------------------------------------------------------------------


async def stale_pursuits(
    session: AsyncSession, *, now: datetime, after_days: int = STALE_AFTER_DAYS
) -> Sequence[Pursuit]:
    cutoff = now - timedelta(days=after_days)
    return (
        (
            await session.execute(
                select(Pursuit)
                .where(Pursuit.stage.in_(list(stages.OPEN_STAGES)), Pursuit.activity_at <= cutoff)
                .order_by(Pursuit.activity_at)
            )
        )
        .scalars()
        .all()
    )


async def stale_pursuits_for_tenant(
    session: AsyncSession,
    settings: Settings,
    dispatcher: Dispatcher,
    tenant_id: uuid.UUID,
    *,
    now: datetime | None = None,
    after_days: int = STALE_AFTER_DAYS,
) -> CheckRun:
    """SPEC 9: a pursuit nobody has touched for five days nudges its owner, once a day."""
    moment = now or datetime.now(UTC)
    run = CheckRun()
    for pursuit in await stale_pursuits(session, now=moment, after_days=after_days):
        run.scanned += 1
        if pursuit.owner_user_id is None:
            continue
        recipient = await _recipient(session, pursuit.owner_user_id)
        if recipient is None:  # pragma: no cover - the FK guarantees the user
            continue
        opportunity = await session.get(Opportunity, pursuit.opportunity_id)
        idle_days = (moment - pursuit.activity_at).days
        event = DispatchEvent(
            event_type=STALE_EVENT,
            tenant_id=tenant_id,
            opportunity_id=pursuit.opportunity_id,
            pursuit_id=pursuit.id,
            payload={
                "title": "a tracked pursuit" if opportunity is None else opportunity.title,
                "buyer": None if opportunity is None else opportunity.buyer_org,
                "reason": "stale",
                "stage": pursuit.stage,
                "idle_days": idle_days,
                "message": f"no activity for {idle_days} days",
            },
            occurred_at=moment,
            response_due_at=None if opportunity is None else opportunity.response_due_at,
            dedupe_key=f"stale:{pursuit.id}:{moment.date().isoformat()}",
        )
        result = await dispatcher.dispatch(session, event, [recipient])
        run.notified += len(result.notifications)
    return run


# --- 3. amended after drafting started -----------------------------------------------------------


def past_drafting(stage: str) -> bool:
    """True once the package is being written (SPEC 9's "amended after drafting started")."""
    if stage not in stages.LADDER:
        return False
    return stages.LADDER.index(stage) >= stages.LADDER.index(RECHECK_FROM_STAGE)


async def flag_matrix_recheck(
    session: AsyncSession,
    settings: Settings,
    dispatcher: Dispatcher,
    tenant_id: uuid.UUID,
    opportunity: Opportunity,
    *,
    version: int,
    changes: list[str] | None = None,
    now: datetime | None = None,
) -> CheckRun:
    moment = now or datetime.now(UTC)
    run = CheckRun()
    rows = (
        (await session.execute(select(Pursuit).where(Pursuit.opportunity_id == opportunity.id)))
        .scalars()
        .all()
    )
    for pursuit in rows:
        run.scanned += 1
        if not past_drafting(pursuit.stage):
            continue
        pursuit.matrix_recheck_required = True
        pursuit.activity_at = moment
        if pursuit.owner_user_id is None:
            continue
        recipient = await _recipient(session, pursuit.owner_user_id)
        if recipient is None:  # pragma: no cover - the FK guarantees the user
            continue
        event = DispatchEvent(
            event_type=RECHECK_EVENT,
            tenant_id=tenant_id,
            opportunity_id=opportunity.id,
            pursuit_id=pursuit.id,
            version=version,
            payload={
                "title": opportunity.title,
                "buyer": opportunity.buyer_org,
                "question": (
                    "This solicitation was amended while the package was being written. "
                    "Re-run the compliance matrix and re-check the affected sections."
                ),
                "stage": pursuit.stage,
                "changes": list(changes or []),
                "matrix_recheck_required": True,
            },
            occurred_at=moment,
            response_due_at=opportunity.response_due_at,
        )
        result = await dispatcher.dispatch(session, event, [recipient])
        run.notified += len(result.notifications)
    await session.flush()
    return run


class MatrixRecheckSubscriber:
    """opportunity.amended -> flag every pursuit that is already drafting."""

    def __init__(self, settings: Settings, database: Database | None = None) -> None:
        self.settings = settings
        self.database = database

    async def __call__(self, event: Event) -> None:
        payload = dict(event.payload)
        raw_id = payload.get("opportunity_id")
        if raw_id is None:
            return
        opportunity_id = uuid.UUID(str(raw_id))
        version = int(payload.get("version") or 1)
        changes = [str(c) for c in (payload.get("changes") or [])]
        db = self.database or get_database()
        from app.notify.registry import build_dispatcher

        dispatcher = build_dispatcher(self.settings, db)
        for tenant_id in await self._tenants(db, opportunity_id):
            async with db.session(tenant_id) as session:
                opportunity = await session.get(Opportunity, opportunity_id)
                if opportunity is None:  # pragma: no cover - the event carries a real id
                    continue
                await flag_matrix_recheck(
                    session,
                    self.settings,
                    dispatcher,
                    tenant_id,
                    opportunity,
                    version=version,
                    changes=changes,
                )

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


def install_matrix_recheck(
    settings: Settings, bus: EventBus, database: Database | None = None
) -> MatrixRecheckSubscriber:
    subscriber = MatrixRecheckSubscriber(settings, database)
    bus.subscribe(OPPORTUNITY_AMENDED, subscriber)
    return subscriber
