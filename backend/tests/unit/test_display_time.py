"""M6-10: dual time-zone rendering shared by API (TzDateOut), email/UI plain strings and the
countdown helper; exercised across the America/New_York DST boundaries with IST users."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from app.core.dates import IST, parse_in
from app.core.display_time import (
    TzDateOut,
    countdown,
    render_due,
    render_local,
    render_tz,
    tz_fields,
    tz_fields_or_none,
)

NY = "America/New_York"


def utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=UTC)


# --- API fields ----------------------------------------------------------------------------


def test_tz_fields_carry_utc_buyer_and_user_views() -> None:
    out = tz_fields(utc(2026, 10, 14, 18, 0), NY, IST)
    assert isinstance(out, TzDateOut)
    assert out.utc == utc(2026, 10, 14, 18, 0) and out.utc.tzinfo is UTC
    assert out.buyer_tz == NY
    assert out.buyer_local == "2026-10-14T14:00:00-04:00"
    assert out.buyer_display == "Oct 14, 2:00 PM EDT"
    assert out.user_tz == IST
    assert out.user_local == "2026-10-14T23:30:00+05:30"
    assert out.user_display == "Oct 14, 11:30 PM IST"
    assert out.display == "Oct 14, 2:00 PM EDT = 11:30 PM IST"


def test_tz_fields_without_user_zone_and_json_shape() -> None:
    out = tz_fields(utc(2026, 10, 14, 18, 0), NY)
    assert out.user_tz is None and out.user_local is None and out.user_display is None
    assert out.display == "Oct 14, 2:00 PM EDT"
    payload = out.model_dump(mode="json")
    assert payload == {
        "utc": "2026-10-14T18:00:00Z",
        "buyer_tz": NY,
        "buyer_local": "2026-10-14T14:00:00-04:00",
        "buyer_display": "Oct 14, 2:00 PM EDT",
        "user_tz": None,
        "user_local": None,
        "user_display": None,
        "display": "Oct 14, 2:00 PM EDT",
    }
    assert TzDateOut.model_validate(payload) == out


def test_tz_fields_normalizes_non_utc_input_and_rejects_naive() -> None:
    kolkata = datetime(2026, 10, 14, 23, 30, tzinfo=ZoneInfo(IST))
    assert tz_fields(kolkata, NY, IST).utc == utc(2026, 10, 14, 18, 0)
    with pytest.raises(ValueError):
        tz_fields(datetime(2026, 10, 14, 18, 0), NY, IST)
    with pytest.raises(ValueError):
        tz_fields(utc(2026, 10, 14, 18, 0), "Nowhere/City", IST)


def test_tz_fields_or_none() -> None:
    assert tz_fields_or_none(None, NY, IST) is None
    out = tz_fields_or_none(utc(2026, 10, 14, 18, 0), NY, IST)
    assert out is not None and out.display == "Oct 14, 2:00 PM EDT = 11:30 PM IST"


# --- email / UI plain strings ----------------------------------------------------------------


def test_render_helpers_are_plain_strings() -> None:
    at = utc(2026, 10, 14, 21, 0)
    assert render_tz(at, NY, IST) == "Oct 14, 5:00 PM EDT = Oct 15, 2:30 AM IST"
    assert render_tz(at, NY, None) == "Oct 14, 5:00 PM EDT"
    assert render_tz(at, NY, IST, with_year=True) == (
        "Oct 14, 2026, 5:00 PM EDT = Oct 15, 2026, 2:30 AM IST"
    )
    assert render_local(at, IST) == "Oct 15, 2:30 AM IST"
    assert render_local(at, IST, with_date=False) == "2:30 AM IST"


def test_render_due_appends_countdown() -> None:
    due = utc(2026, 10, 14, 18, 0)
    now = utc(2026, 10, 11, 13, 48)
    assert render_due(due, NY, IST, now) == "Oct 14, 2:00 PM EDT = 11:30 PM IST (in 3d 4h)"
    later = utc(2026, 10, 14, 20, 0)
    assert render_due(due, NY, IST, later) == "Oct 14, 2:00 PM EDT = 11:30 PM IST (overdue 2h)"
    assert render_due(due, NY, None, due) == "Oct 14, 2:00 PM EDT (due now)"


# --- countdown -----------------------------------------------------------------------------

NOW = utc(2026, 10, 11, 12, 0)


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (timedelta(days=3, hours=4), "3d 4h"),
        (timedelta(days=3, hours=4, minutes=59), "3d 4h"),  # floors, never rounds up
        (timedelta(days=3), "3d"),
        (timedelta(days=1, minutes=5), "1d"),
        (timedelta(hours=6, minutes=12), "6h 12m"),
        (timedelta(hours=6), "6h"),
        (timedelta(hours=23, minutes=59, seconds=59), "23h 59m"),
        (timedelta(minutes=45), "45m"),
        (timedelta(minutes=1), "1m"),
        (timedelta(seconds=59), "due now"),
        (timedelta(0), "due now"),
        (timedelta(seconds=-59), "due now"),
        (timedelta(minutes=-1), "overdue 1m"),
        (timedelta(hours=-2), "overdue 2h"),
        (timedelta(hours=-2, minutes=-30), "overdue 2h 30m"),
        (timedelta(days=-1, hours=-3), "overdue 1d 3h"),
        (timedelta(days=-40), "overdue 40d"),
    ],
)
def test_countdown(delta: timedelta, expected: str) -> None:
    assert countdown(NOW, NOW + delta) == expected


def test_countdown_accepts_any_aware_zone_and_rejects_naive() -> None:
    due = datetime(2026, 10, 14, 23, 30, tzinfo=ZoneInfo(IST))  # 18:00Z
    assert countdown(utc(2026, 10, 14, 12, 0), due) == "6h"
    with pytest.raises(ValueError):
        countdown(datetime(2026, 10, 14), utc(2026, 10, 14, 12, 0))
    with pytest.raises(ValueError):
        countdown(utc(2026, 10, 14, 12, 0), datetime(2026, 10, 14))


# --- DST boundaries (America/New_York 2026: spring forward Mar 8, fall back Nov 1) -----------


def test_spring_forward_same_wall_clock_shifts_ist_by_an_hour() -> None:
    before = utc(2026, 3, 7, 17, 0)  # Sat Mar 7, noon EST
    after = utc(2026, 3, 8, 16, 0)  # Sun Mar 8, noon EDT
    assert render_tz(before, NY, IST) == "Mar 7, 12:00 PM EST = 10:30 PM IST"
    assert render_tz(after, NY, IST) == "Mar 8, 12:00 PM EDT = 9:30 PM IST"
    assert tz_fields(before, NY, IST).buyer_local == "2026-03-07T12:00:00-05:00"
    assert tz_fields(after, NY, IST).buyer_local == "2026-03-08T12:00:00-04:00"
    # the clock skips an hour, so "24 hours later on the wall" is 23 real hours
    assert countdown(before, after) == "23h"


def test_spring_forward_gap_time_from_a_portal_string() -> None:
    parsed = parse_in("08-03-2026 02:30", tz=NY)  # 2:30 AM does not exist that night
    assert parsed is not None
    # zoneinfo resolves the gap with the pre-transition offset (EST): 07:30Z = 3:30 AM EDT
    assert parsed.utc == utc(2026, 3, 8, 7, 30)
    assert render_local(parsed.utc, NY) == "Mar 8, 3:30 AM EDT"
    assert render_tz(parsed.utc, NY, IST) == "Mar 8, 3:30 AM EDT = 1:00 PM IST"


def test_spring_forward_indian_buyer_us_user() -> None:
    # 10:00 AM IST on Mar 8 is 04:30Z, still Mar 7 EST in New York (before the 07:00Z switch)
    early = utc(2026, 3, 8, 4, 30)
    assert render_tz(early, IST, NY) == "Mar 8, 10:00 AM IST = Mar 7, 11:30 PM EST"
    # 6:00 PM IST on Mar 8 is 12:30Z, after the switch: EDT
    late = utc(2026, 3, 8, 12, 30)
    assert render_tz(late, IST, NY) == "Mar 8, 6:00 PM IST = 8:30 AM EDT"
    out = tz_fields(late, IST, NY)
    assert (
        out.user_local == "2026-03-08T08:30:00-04:00" and out.user_display == "Mar 8, 8:30 AM EDT"
    )


def test_fall_back_repeated_hour_is_unambiguous_from_utc() -> None:
    first = utc(2026, 11, 1, 5, 30)  # 1:30 AM EDT (first pass)
    second = utc(2026, 11, 1, 6, 30)  # 1:30 AM EST (second pass)
    assert render_local(first, NY) == "Nov 1, 1:30 AM EDT"
    assert render_local(second, NY) == "Nov 1, 1:30 AM EST"
    assert tz_fields(first, NY, IST).buyer_local == "2026-11-01T01:30:00-04:00"
    assert tz_fields(second, NY, IST).buyer_local == "2026-11-01T01:30:00-05:00"
    assert render_tz(first, NY, IST) == "Nov 1, 1:30 AM EDT = 11:00 AM IST"
    assert render_tz(second, NY, IST) == "Nov 1, 1:30 AM EST = 12:00 PM IST"
    assert countdown(first, second) == "1h"


def test_fall_back_overnight_deadline_and_countdown() -> None:
    due = utc(2026, 11, 1, 22, 0)  # Sun Nov 1, 5:00 PM EST
    assert render_tz(due, NY, IST) == "Nov 1, 5:00 PM EST = Nov 2, 3:30 AM IST"
    day_before = utc(2026, 10, 31, 21, 0)  # Sat Oct 31, 5:00 PM EDT
    assert render_local(day_before, NY) == "Oct 31, 5:00 PM EDT"
    # the clock repeats an hour, so the same wall-clock time next day is 25 real hours away
    assert countdown(day_before, due) == "1d 1h"
    assert render_due(due, NY, IST, day_before) == (
        "Nov 1, 5:00 PM EST = Nov 2, 3:30 AM IST (in 1d 1h)"
    )


def test_ist_has_no_dst_so_indian_deadlines_are_stable() -> None:
    for month, day in ((3, 8), (11, 1), (7, 17)):
        at = utc(2026, month, day, 9, 30)  # 3:00 PM IST every time
        out = tz_fields(at, IST)
        assert out.buyer_display.endswith("3:00 PM IST")
        assert out.buyer_local.endswith("T15:00:00+05:30")
