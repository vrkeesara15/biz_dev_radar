"""Expiry windows and the bid block (SPEC 4.1, 7; M6-06). Pure."""

from __future__ import annotations

from datetime import date

import pytest
from app.core.expiry import (
    BLOCKING_REGISTRATIONS,
    EXPIRY_OFFSETS,
    ITEM_KINDS,
    ExpiringItem,
    blocked_reason,
    blocks_bidding,
    days_left,
    is_expired,
    window_for,
)

TODAY = date(2026, 10, 2)


def test_spec_7_windows() -> None:
    assert EXPIRY_OFFSETS == (60, 30, 7)
    assert ITEM_KINDS == ("registration", "certification", "insurance")


@pytest.mark.parametrize(
    ("expires_on", "expected"),
    [
        (date(2026, 12, 2), None),  # 61 days out: too early
        (date(2026, 12, 1), 60),  # exactly 60
        (date(2026, 11, 15), 60),
        (date(2026, 11, 1), 30),  # exactly 30
        (date(2026, 10, 20), 30),
        (date(2026, 10, 9), 7),  # exactly 7
        (date(2026, 10, 3), 7),
        (date(2026, 10, 2), 7),  # expires today: still the 7-day window
        (date(2026, 10, 1), None),  # already expired: a block, not a reminder
        (None, None),
    ],
)
def test_window_for(expires_on: date | None, expected: int | None) -> None:
    assert window_for(expires_on, TODAY) == expected


def test_the_tightest_window_wins_after_a_missed_run() -> None:
    # a job that last ran at the 60-day mark and catches up inside the 7-day window
    assert window_for(date(2026, 10, 5), TODAY, already_sent=60) == 7
    assert window_for(date(2026, 10, 5), TODAY, already_sent=7) is None
    assert window_for(date(2026, 11, 20), TODAY, already_sent=60) is None


def test_expiry_is_inclusive_of_the_last_day() -> None:
    assert not is_expired(date(2026, 10, 2), TODAY)
    assert is_expired(date(2026, 10, 1), TODAY)
    assert not is_expired(None, TODAY)
    assert days_left(date(2026, 10, 9), TODAY) == 7
    assert days_left(date(2026, 9, 30), TODAY) == -2


def test_only_sam_and_dsc_block_bidding() -> None:
    assert BLOCKING_REGISTRATIONS == ("sam", "dsc")
    assert blocks_bidding("sam") and blocks_bidding("DSC")
    assert not blocks_bidding("gem")
    assert not blocks_bidding("state_portal")
    assert "DSC and SAM" in blocked_reason(["sam", "dsc", "sam"])
    assert "blocked for bids" in blocked_reason(["sam"])


def test_the_payload_a_reminder_carries() -> None:
    item = ExpiringItem(
        kind="registration",
        subkind="sam",
        label="SAM registration",
        expires_on=date(2026, 10, 9),
        profile_id="p1",
        item_id="i1",
    )
    payload = item.as_payload(7, TODAY)
    assert payload == {
        "kind": "registration",
        "subkind": "sam",
        "label": "SAM registration",
        "expires_on": "2026-10-09",
        "days_left": 7,
        "window": 7,
        "profile_id": "p1",
        "item_id": "i1",
        "blocks_bids": True,
    }
    other = ExpiringItem(
        kind="insurance",
        subkind="general_liability",
        label="general liability insurance",
        expires_on=date(2026, 10, 9),
        profile_id="p1",
        item_id="i2",
    )
    assert other.as_payload(7, TODAY)["blocks_bids"] is False
