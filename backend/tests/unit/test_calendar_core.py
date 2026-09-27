"""iCalendar rendering (SPEC 9, 10.3; M6-04). Pure; validated with the icalendar library."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.core.calendar import (
    CalendarEntry,
    build_calendar,
    build_description,
    build_event,
    event_uid,
)
from icalendar import Calendar

AT = datetime(2026, 10, 14, 18, 0, tzinfo=UTC)  # 2:00 PM EDT / 11:30 PM IST
NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def _entry(**overrides: object) -> CalendarEntry:
    values: dict[str, object] = {
        "uid": "pursuit-date-abc@bidradar",
        "at": AT,
        "summary": "Portal submission due — Helpdesk services",
        "buyer_tz": "America/New_York",
        "user_tz": "Asia/Kolkata",
        "note": "the response deadline",
        "url": "http://localhost:3000/app/pursuits/1",
        "sequence": 0,
    }
    values.update(overrides)
    return CalendarEntry(**values)  # type: ignore[arg-type]


def test_uid_is_stable_per_key_date_row() -> None:
    row_id = uuid.uuid4()
    assert event_uid(row_id) == f"pursuit-date-{row_id}@bidradar"
    assert event_uid(row_id) == event_uid(row_id)


def test_description_carries_both_zones_the_note_and_the_link() -> None:
    text = build_description(_entry())
    assert text.splitlines()[0] == "Oct 14, 2026, 2:00 PM EDT = 11:30 PM IST"
    assert text.splitlines()[1] == "the response deadline"
    assert text.splitlines()[2] == "http://localhost:3000/app/pursuits/1"
    # without a user zone only the buyer's clock is shown
    assert build_description(_entry(user_tz=None, note=None, url=None)) == (
        "Oct 14, 2026, 2:00 PM EDT"
    )


def test_a_rendered_calendar_parses_back_with_every_field() -> None:
    ics = build_calendar([_entry()], name="BidRadar deadlines", now=NOW)
    assert ics.startswith(b"BEGIN:VCALENDAR")
    assert b"\r\n" in ics  # RFC 5545 line endings

    parsed = Calendar.from_ical(ics)
    assert parsed["prodid"] == "-//BidRadar//Key dates//EN"
    assert parsed["version"] == "2.0"
    assert str(parsed["x-wr-calname"]) == "BidRadar deadlines"
    events = list(parsed.walk("VEVENT"))
    assert len(events) == 1
    event = events[0]
    assert str(event["uid"]) == "pursuit-date-abc@bidradar"
    assert event.decoded("dtstart") == AT
    assert event.decoded("dtend") == AT + timedelta(minutes=30)
    assert event.decoded("dtstamp") == NOW
    assert str(event["summary"]) == "Portal submission due — Helpdesk services"
    assert "11:30 PM IST" in str(event["description"])
    assert int(event["sequence"]) == 0
    assert str(event["status"]) == "CONFIRMED"
    assert str(event["url"]) == "http://localhost:3000/app/pursuits/1"


def test_sequence_and_cancellation_travel_with_the_event() -> None:
    ics = build_calendar([_entry(sequence=7, cancelled=True)], now=NOW)
    event = next(iter(Calendar.from_ical(ics).walk("VEVENT")))
    assert int(event["sequence"]) == 7
    assert str(event["status"]) == "CANCELLED"


def test_last_modified_is_included_when_known() -> None:
    modified = NOW + timedelta(hours=3)
    event = build_event(_entry(last_modified=modified), now=NOW)
    assert event.decoded("last-modified") == modified
    assert "last-modified" not in build_event(_entry(), now=NOW)


def test_several_entries_keep_their_order_and_an_empty_feed_is_valid() -> None:
    entries = [
        _entry(uid="a@bidradar", at=AT - timedelta(days=5)),
        _entry(uid="b@bidradar", at=AT),
    ]
    events = list(Calendar.from_ical(build_calendar(entries, now=NOW)).walk("VEVENT"))
    assert [str(e["uid"]) for e in events] == ["a@bidradar", "b@bidradar"]

    empty = Calendar.from_ical(build_calendar([], now=NOW))
    assert list(empty.walk("VEVENT")) == []
    assert str(empty["prodid"]) == "-//BidRadar//Key dates//EN"


def test_naive_datetimes_are_refused() -> None:
    with pytest.raises(ValueError, match="aware"):
        build_calendar([_entry(at=datetime(2026, 10, 14, 18, 0))], now=NOW)
    with pytest.raises(ValueError, match="aware"):
        build_event(_entry(last_modified=datetime(2026, 10, 14, 18, 0)), now=NOW)


def test_a_zero_duration_still_produces_a_visible_block() -> None:
    event = build_event(_entry(duration_minutes=0), now=NOW)
    assert event.decoded("dtend") == AT + timedelta(minutes=1)
