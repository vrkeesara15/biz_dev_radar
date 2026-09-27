"""M3-01: Indian portal date parsing (IST -> UTC, source tz kept) and dual-tz rendering."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from app.core.dates import (
    IST,
    ParsedDateTime,
    dual_tz,
    format_local,
    in_tz,
    parse_in,
    to_utc,
)

KOLKATA = ZoneInfo(IST)
NEW_YORK = ZoneInfo("America/New_York")


def utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "expected_utc", "has_time"),
    [
        # SPEC 12: DD-MM-YYYY and "17-Jul-2026 08:23 PM" must parse as IST
        ("17-07-2026", utc(2026, 7, 16, 18, 30), False),
        ("17/07/2026", utc(2026, 7, 16, 18, 30), False),
        ("17.07.2026", utc(2026, 7, 16, 18, 30), False),
        ("17-07-2026 15:00", utc(2026, 7, 17, 9, 30), True),
        ("17/07/2026 15:00:30", utc(2026, 7, 17, 9, 30, 30), True),
        ("17-07-2026 15:00 Hrs", utc(2026, 7, 17, 9, 30), True),
        ("17-Jul-2026 08:23 PM", utc(2026, 7, 17, 14, 53), True),
        ("17-jul-2026 08:23 pm", utc(2026, 7, 17, 14, 53), True),
        ("17-Jul-2026 12:05 AM", utc(2026, 7, 16, 18, 35), True),
        ("17-Jul-2026 12:05 PM", utc(2026, 7, 17, 6, 35), True),
        ("17-Jul-2026 20:23", utc(2026, 7, 17, 14, 53), True),
        ("17-Jul-2026", utc(2026, 7, 16, 18, 30), False),
        ("17 July 2026 8:23 PM IST", utc(2026, 7, 17, 14, 53), True),
        ("17 Sept 2026", utc(2026, 9, 16, 18, 30), False),
        ("01/03/2026", utc(2026, 2, 28, 18, 30), False),  # DD/MM, never MM/DD
        ("2026-07-17", utc(2026, 7, 16, 18, 30), False),
        ("2026-07-17T20:23", utc(2026, 7, 17, 14, 53), True),
        ("2026-07-17 20:23:00", utc(2026, 7, 17, 14, 53), True),
        ("  17-07-2026\u00a015:00  ", utc(2026, 7, 17, 9, 30), True),
    ],
)
def test_parse_in_indian_formats_as_ist(text: str, expected_utc: datetime, has_time: bool) -> None:
    parsed = parse_in(text)
    assert parsed is not None, text
    assert parsed.utc == expected_utc
    assert parsed.utc.tzinfo is UTC
    assert parsed.source_tz == IST
    assert parsed.has_time is has_time
    assert parsed.local.tzinfo == KOLKATA
    assert parsed.local == expected_utc.astimezone(KOLKATA)


def test_parse_in_keeps_explicit_offsets_and_reports_them() -> None:
    aware = parse_in("2026-07-17T20:23:00+05:30")
    assert aware == ParsedDateTime(utc(2026, 7, 17, 14, 53), IST, True)
    other = parse_in("2026-07-17T20:23:00-04:00", tz="Asia/Kolkata")
    assert other is not None
    assert other.utc == utc(2026, 7, 18, 0, 23)
    assert other.source_tz == "UTC-04:00"
    assert other.local.utcoffset() == timedelta(hours=-4) and other.local.hour == 20
    zulu = parse_in("2026-07-17T20:23:00Z")
    assert zulu is not None and zulu.source_tz == "UTC" and zulu.utc == utc(2026, 7, 17, 20, 23)


def test_parse_in_other_source_zone() -> None:
    parsed = parse_in("10/14/2026 2:00 PM", tz="America/New_York")
    assert parsed is None  # MM/DD is not accepted even for US zones: adapters use ISO there
    parsed = parse_in("14-10-2026 2:00 PM", tz="America/New_York")
    assert parsed is not None
    assert parsed.utc == utc(2026, 10, 14, 18, 0) and parsed.source_tz == "America/New_York"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "TBD",
        "32-01-2026",
        "29-02-2026",
        "17-13-2026",
        "17-07-26",
        "17-Foo-2026",
        "17-07-2026 25:00",
        "17-07-2026 08:60 PM",
        "17-07-2026 13:00 PM",
        "17-07-2026 00:00 AM",
        "2026-02-30",
        "not a date at all 17",
        "17-07-2026 extra words here",
    ],
)
def test_parse_in_returns_none_for_unparseable(text: str) -> None:
    assert parse_in(text) is None


def test_parse_in_none_input_and_unknown_zone() -> None:
    assert parse_in(None) is None  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        parse_in("17-07-2026", tz="Mars/Olympus")


def test_to_utc_and_in_tz_helpers() -> None:
    naive = datetime(2026, 7, 17, 20, 23)
    assert to_utc(naive, IST) == utc(2026, 7, 17, 14, 53)
    aware = datetime(2026, 7, 17, 20, 23, tzinfo=KOLKATA)
    assert to_utc(aware, "America/New_York") == utc(2026, 7, 17, 14, 53)  # tz ignored when aware
    local = in_tz(utc(2026, 7, 17, 14, 53), IST)
    assert (local.hour, local.minute, local.tzname()) == (20, 23, "IST")
    with pytest.raises(ValueError):
        in_tz(naive, IST)


def test_format_local() -> None:
    at = utc(2026, 10, 14, 18, 0)
    assert format_local(at, "America/New_York") == "Oct 14, 2:00 PM EDT"
    assert format_local(at, IST) == "Oct 14, 11:30 PM IST"
    assert format_local(at, IST, with_date=False) == "11:30 PM IST"
    assert format_local(at, IST, with_year=True) == "Oct 14, 2026, 11:30 PM IST"
    assert format_local(utc(2026, 1, 5, 5, 0), IST) == "Jan 5, 10:30 AM IST"
    assert format_local(utc(2026, 1, 5, 18, 30), IST) == "Jan 6, 12:00 AM IST"
    assert format_local(utc(2026, 1, 5, 6, 30), IST) == "Jan 5, 12:00 PM IST"


def test_dual_tz_spec_example_same_local_date() -> None:
    # SPEC 9: "Oct 14, 2:00 PM EDT = 11:30 PM IST"
    at = utc(2026, 10, 14, 18, 0)
    assert dual_tz(at, "America/New_York", IST) == "Oct 14, 2:00 PM EDT = 11:30 PM IST"


def test_dual_tz_overnight_shows_both_dates() -> None:
    # 5:00 PM EDT on Oct 14 is already Oct 15 in India
    at = utc(2026, 10, 14, 21, 0)
    assert dual_tz(at, "America/New_York", IST) == "Oct 14, 5:00 PM EDT = Oct 15, 2:30 AM IST"
    # and the other way round for an Indian buyer with a US user
    at = utc(2026, 7, 17, 0, 30)  # 06:00 IST Jul 17 = 8:30 PM EDT Jul 16
    assert dual_tz(at, IST, "America/New_York") == "Jul 17, 6:00 AM IST = Jul 16, 8:30 PM EDT"


def test_dual_tz_winter_offsets_and_year_option() -> None:
    at = utc(2026, 12, 1, 19, 0)  # 2:00 PM EST = 12:30 AM IST next day
    assert dual_tz(at, "America/New_York", IST) == "Dec 1, 2:00 PM EST = Dec 2, 12:30 AM IST"
    assert (
        dual_tz(at, "America/New_York", IST, with_year=True)
        == "Dec 1, 2026, 2:00 PM EST = Dec 2, 2026, 12:30 AM IST"
    )


def test_dual_tz_collapses_when_user_zone_matches_buyer() -> None:
    at = utc(2026, 10, 14, 18, 0)
    assert dual_tz(at, IST, IST) == "Oct 14, 11:30 PM IST"
    assert dual_tz(at, IST, None) == "Oct 14, 11:30 PM IST"
    # same instant, same wall clock but different zone names: still one rendering
    assert dual_tz(at, "Asia/Kolkata", "Asia/Calcutta") == "Oct 14, 11:30 PM IST"


def test_dual_tz_requires_aware_input() -> None:
    with pytest.raises(ValueError):
        dual_tz(datetime(2026, 10, 14, 18, 0), "America/New_York", IST)
    with pytest.raises(ValueError):
        dual_tz(utc(2026, 10, 14, 18, 0), "Nowhere/City", IST)
