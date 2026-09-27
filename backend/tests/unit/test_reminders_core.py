"""The reminder ladder (SPEC 9, M6-03): rungs, overdue slots and escalation. Pure."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.core.reminders import (
    ESCALATION_MANAGER,
    ESCALATION_OWNER,
    ESCALATION_TENANT_OWNER,
    LABELS,
    MAX_OVERDUE_PER_PASS,
    OVERDUE_EVERY,
    describe,
    escalation_level,
    is_label,
    label_order,
    ladder,
    overdue_slots,
)

DUE = datetime(2026, 10, 14, 18, 0, tzinfo=UTC)


def test_the_ladder_is_the_five_spec_9_rungs_in_order() -> None:
    rungs = ladder(DUE)
    assert [label for label, _ in rungs] == ["7d", "3d", "24h", "4h", "1h"]
    assert [at for _, at in rungs] == [
        DUE - timedelta(days=7),
        DUE - timedelta(days=3),
        DUE - timedelta(hours=24),
        DUE - timedelta(hours=4),
        DUE - timedelta(hours=1),
    ]
    assert LABELS == ("7d", "3d", "24h", "4h", "1h", "overdue")
    assert all(is_label(label) for label in LABELS)
    assert not is_label("30d")


def test_rungs_already_behind_us_are_dropped() -> None:
    late = DUE - timedelta(days=2)  # opened two days before the deadline
    assert [label for label, _ in ladder(DUE, late)] == ["24h", "4h", "1h"]
    # exactly on a rung counts as past (it fires now through the overdue-free path)
    assert [label for label, _ in ladder(DUE, DUE - timedelta(hours=24))] == ["4h", "1h"]
    assert ladder(DUE, DUE) == []
    assert ladder(DUE, DUE + timedelta(days=1)) == []


def test_overdue_fires_every_four_hours() -> None:
    now = DUE + timedelta(hours=9)
    slots = overdue_slots(DUE, now)
    assert slots == [
        ("overdue", DUE + timedelta(hours=4)),
        ("overdue", DUE + timedelta(hours=8)),
    ]
    assert overdue_slots(DUE, DUE) == []
    assert overdue_slots(DUE, DUE + timedelta(hours=3, minutes=59)) == []
    assert overdue_slots(DUE, DUE + OVERDUE_EVERY) == [("overdue", DUE + OVERDUE_EVERY)]


def test_overdue_resumes_after_the_last_recorded_slot() -> None:
    last = DUE + timedelta(hours=8)
    now = DUE + timedelta(hours=17)
    assert overdue_slots(DUE, now, after=last) == [
        ("overdue", DUE + timedelta(hours=12)),
        ("overdue", DUE + timedelta(hours=16)),
    ]
    assert overdue_slots(DUE, now, after=now) == []


def test_a_long_forgotten_deadline_is_capped_per_pass() -> None:
    slots = overdue_slots(DUE, DUE + timedelta(days=30))
    assert len(slots) == MAX_OVERDUE_PER_PASS
    assert slots[0][1] == DUE + timedelta(hours=4)
    assert overdue_slots(DUE, DUE + timedelta(days=30), limit=1) == [
        ("overdue", DUE + timedelta(hours=4))
    ]
    assert overdue_slots(DUE, DUE + timedelta(days=30), limit=0) == []


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("7d", ESCALATION_OWNER),
        ("3d", ESCALATION_OWNER),
        ("24h", ESCALATION_MANAGER),
        ("4h", ESCALATION_TENANT_OWNER),
        ("1h", ESCALATION_TENANT_OWNER),
        ("overdue", ESCALATION_TENANT_OWNER),
        ("nonsense", ESCALATION_OWNER),
    ],
)
def test_escalation_follows_spec_9(label: str, expected: int) -> None:
    assert escalation_level(label) == expected


def test_acknowledging_stops_every_escalation() -> None:
    for label in LABELS:
        assert escalation_level(label, acknowledged=True) == ESCALATION_OWNER


def test_label_order_and_descriptions() -> None:
    assert sorted(["overdue", "1h", "7d", "24h"], key=label_order) == [
        "7d",
        "24h",
        "1h",
        "overdue",
    ]
    assert label_order("unknown") == len(LABELS)
    assert describe("24h") == "24 hours to go"
    assert describe("overdue") == "overdue"
    assert describe("weird") == "weird"


def test_naive_datetimes_are_refused() -> None:
    naive = datetime(2026, 10, 14, 18, 0)
    with pytest.raises(ValueError, match="due_at must be an aware datetime"):
        ladder(naive)
    with pytest.raises(ValueError, match="now must be an aware datetime"):
        ladder(DUE, naive)
    with pytest.raises(ValueError, match="aware"):
        overdue_slots(DUE, naive)
    with pytest.raises(ValueError, match="after must be an aware datetime"):
        overdue_slots(DUE, DUE + timedelta(days=1), after=naive)


def test_the_ladder_is_time_zone_agnostic() -> None:
    """A deadline is one instant: a US and an Indian reader are reminded together."""
    from zoneinfo import ZoneInfo

    in_ist = DUE.astimezone(ZoneInfo("Asia/Kolkata"))
    in_edt = DUE.astimezone(ZoneInfo("America/New_York"))
    assert [at for _, at in ladder(in_ist)] == [at for _, at in ladder(in_edt)]
