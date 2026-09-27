"""Status job (SPEC 5.4): every 15 minutes open -> closing_soon (<= 7 days) -> closed, and
cancellation / award notices roll their status up to the parent notice.

Every transition is a real amendment: version += 1, an opportunity_versions row with the
status diff (change kind `status_changed`, or `cancelled` / `awarded` when a terminal
status propagates) and an `opportunity.amended` event.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.changes import classify_changes
from app.core.opportunity import OpportunityStatus
from app.core.status import CLOSING_SOON_WINDOW, TERMINAL_STATUSES, is_terminal, next_status
from app.models import Opportunity, OpportunityVersion
from app.services.events import OPPORTUNITY_AMENDED, EventBus, get_event_bus

log = structlog.get_logger(__name__)

SCHEDULE = "*/15 * * * *"


@dataclass(slots=True)
class StatusRollResult:
    closing_soon: int = 0
    closed: int = 0
    reopened: int = 0
    propagated: int = 0

    @property
    def total(self) -> int:
        return self.closing_soon + self.closed + self.reopened + self.propagated


async def transition(
    session: AsyncSession,
    row: Opportunity,
    new_status: OpportunityStatus,
    *,
    bus: EventBus | None = None,
    reason: str = "status_job",
) -> OpportunityVersion | None:
    """Move `row` to `new_status` with a version diff and an amended event."""
    old = OpportunityStatus(row.status)
    if old is new_status:
        return None
    bus = bus or get_event_bus()
    diff = {"status": {"old": old.value, "new": new_status.value}}
    changes = classify_changes(diff)
    row.status = new_status
    row.version += 1
    version = OpportunityVersion(
        opportunity_id=row.id,
        version=row.version,
        diff=diff,
        changes=changes,
        content_hash=row.content_hash or "",
    )
    session.add(version)
    await session.flush()
    await bus.publish(
        OPPORTUNITY_AMENDED,
        {
            "opportunity_id": str(row.id),
            "source_id": row.source_id,
            "external_id": row.external_id,
            "version": row.version,
            "changes": changes,
            "diff": diff,
            "parent_opportunity_id": (
                str(row.parent_opportunity_id) if row.parent_opportunity_id else None
            ),
            "duplicate_of": str(row.duplicate_of) if row.duplicate_of else None,
            "reason": reason,
        },
    )
    log.info(
        "opportunity.status_changed",
        opportunity_id=str(row.id),
        old=old.value,
        new=new_status.value,
        reason=reason,
    )
    return version


async def propagate_terminal_status(
    session: AsyncSession, child: Opportunity, *, bus: EventBus | None = None
) -> bool:
    """A cancellation / award notice sets the same status on its parent notice."""
    if child.parent_opportunity_id is None or not is_terminal(child.status):
        return False
    parent = await session.get(Opportunity, child.parent_opportunity_id)
    if parent is None or OpportunityStatus(parent.status) is OpportunityStatus(child.status):
        return False
    await transition(
        session, parent, OpportunityStatus(child.status), bus=bus, reason="child_notice"
    )
    return True


async def roll_status(
    session: AsyncSession, now: datetime | None = None, *, bus: EventBus | None = None
) -> StatusRollResult:
    now = now or datetime.now(UTC)
    bus = bus or get_event_bus()
    result = StatusRollResult()

    # Derived statuses that may need to move: everything not terminal with a deadline.
    stmt = (
        select(Opportunity)
        .where(
            Opportunity.status.not_in(list(TERMINAL_STATUSES)),
            Opportunity.response_due_at.is_not(None),
        )
        .where(
            # open rows inside the window or past, and closing_soon/closed rows that may
            # have to move on or back; anything far in the future and open is right.
            (Opportunity.response_due_at <= now + CLOSING_SOON_WINDOW)
            | (Opportunity.status != OpportunityStatus.OPEN)
        )
        .order_by(Opportunity.response_due_at.asc())
    )
    for row in (await session.execute(stmt)).scalars().all():
        wanted = next_status(row.status, row.response_due_at, now)
        if wanted is None:
            continue
        old = OpportunityStatus(row.status)
        await transition(session, row, wanted, bus=bus)
        if wanted is OpportunityStatus.CLOSED:
            result.closed += 1
        elif wanted is OpportunityStatus.CLOSING_SOON and old is OpportunityStatus.OPEN:
            result.closing_soon += 1
        else:
            result.reopened += 1

    # Terminal children whose parent has not caught up yet (catch-up for out-of-order
    # ingestion; the ingest pipeline propagates immediately in the normal case).
    parent = aliased(Opportunity)
    stmt = (
        select(Opportunity)
        .join(parent, parent.id == Opportunity.parent_opportunity_id)
        .where(
            Opportunity.status.in_(list(TERMINAL_STATUSES)),
            parent.status != Opportunity.status,
        )
        .order_by(Opportunity.posted_at.asc().nulls_last())
    )
    for child in (await session.execute(stmt)).scalars().all():
        if await propagate_terminal_status(session, child, bus=bus):
            result.propagated += 1
    return result
