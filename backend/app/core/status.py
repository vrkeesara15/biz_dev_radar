"""Opportunity status rules (SPEC 5.4): open -> closing_soon (<= 7 days) -> closed.

Pure functions; the 15-minute job (services/status_job) and the ingest pipeline call
them with an explicit clock. `cancelled` and `awarded` are terminal: they come from the
source (cancellation / award notices) and are never overwritten by the clock.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.opportunity import OpportunityStatus

CLOSING_SOON_WINDOW = timedelta(days=7)
DERIVED_STATUSES = frozenset(
    {OpportunityStatus.OPEN, OpportunityStatus.CLOSING_SOON, OpportunityStatus.CLOSED}
)
TERMINAL_STATUSES = frozenset({OpportunityStatus.CANCELLED, OpportunityStatus.AWARDED})


def derived_status(
    response_due_at: datetime | None, now: datetime | None = None
) -> OpportunityStatus:
    """Status implied by the response deadline alone (no deadline -> open)."""
    now = now or datetime.now(UTC)
    if response_due_at is None:
        return OpportunityStatus.OPEN
    if response_due_at < now:
        return OpportunityStatus.CLOSED
    if response_due_at <= now + CLOSING_SOON_WINDOW:
        return OpportunityStatus.CLOSING_SOON
    return OpportunityStatus.OPEN


def next_status(
    current: OpportunityStatus | str,
    response_due_at: datetime | None,
    now: datetime | None = None,
) -> OpportunityStatus | None:
    """The status the row should move to, or None when it already is right.

    Terminal statuses never change here; derived ones follow the deadline in both
    directions (a deadline moved into the future re-opens a closed notice).
    """
    status = OpportunityStatus(current)
    if status in TERMINAL_STATUSES:
        return None
    wanted = derived_status(response_due_at, now)
    return None if wanted is status else wanted


def is_terminal(status: OpportunityStatus | str) -> bool:
    return OpportunityStatus(status) in TERMINAL_STATUSES
