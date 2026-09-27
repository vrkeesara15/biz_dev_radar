"""M2-11: status rules with frozen time."""

from datetime import UTC, datetime, timedelta

import pytest
from app.core.opportunity import OpportunityStatus
from app.core.status import CLOSING_SOON_WINDOW, derived_status, is_terminal, next_status
from freezegun import freeze_time

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("due", "expected"),
    [
        (None, OpportunityStatus.OPEN),
        (NOW + timedelta(days=30), OpportunityStatus.OPEN),
        (NOW + CLOSING_SOON_WINDOW + timedelta(seconds=1), OpportunityStatus.OPEN),
        (NOW + CLOSING_SOON_WINDOW, OpportunityStatus.CLOSING_SOON),
        (NOW + timedelta(hours=1), OpportunityStatus.CLOSING_SOON),
        (NOW, OpportunityStatus.CLOSING_SOON),
        (NOW - timedelta(seconds=1), OpportunityStatus.CLOSED),
        (NOW - timedelta(days=90), OpportunityStatus.CLOSED),
    ],
)
def test_derived_status(due: datetime | None, expected: OpportunityStatus) -> None:
    assert derived_status(due, NOW) is expected


@freeze_time("2026-09-26 12:00:00")
def test_derived_status_defaults_to_the_current_clock() -> None:
    assert derived_status(NOW + timedelta(days=3)) is OpportunityStatus.CLOSING_SOON
    assert derived_status(NOW - timedelta(minutes=1)) is OpportunityStatus.CLOSED


@pytest.mark.parametrize(
    ("current", "due", "expected"),
    [
        ("open", NOW + timedelta(days=3), OpportunityStatus.CLOSING_SOON),
        ("open", NOW - timedelta(days=1), OpportunityStatus.CLOSED),
        ("closing_soon", NOW - timedelta(days=1), OpportunityStatus.CLOSED),
        ("closing_soon", NOW + timedelta(days=3), None),
        ("open", NOW + timedelta(days=30), None),
        # a deadline moved into the future re-opens
        ("closed", NOW + timedelta(days=30), OpportunityStatus.OPEN),
        ("closed", NOW + timedelta(days=2), OpportunityStatus.CLOSING_SOON),
        ("closing_soon", NOW + timedelta(days=30), OpportunityStatus.OPEN),
        # terminal statuses are never touched by the clock
        ("cancelled", NOW - timedelta(days=1), None),
        ("awarded", NOW + timedelta(days=1), None),
        (OpportunityStatus.CANCELLED, None, None),
    ],
)
def test_next_status(
    current: str, due: datetime | None, expected: OpportunityStatus | None
) -> None:
    assert next_status(current, due, NOW) is expected


def test_is_terminal() -> None:
    assert is_terminal("cancelled") and is_terminal(OpportunityStatus.AWARDED)
    assert not is_terminal("closed")
