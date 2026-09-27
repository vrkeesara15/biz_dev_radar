"""iCalendar rendering for a user's key dates (SPEC 9, 10.3). Pure, no I/O.

    ics = build_calendar([entry], name="BidRadar deadlines")
    # BEGIN:VCALENDAR ... BEGIN:VEVENT UID:pursuit-date-<uuid>@bidradar ...

Every VEVENT is one `pursuit_dates` row. The UID is stable per row, so a feed refresh
updates the same event instead of duplicating it, and SEQUENCE is bumped whenever the
row changes (RFC 5545 §3.8.7.4) so Google and Outlook accept the newer version.

DTSTART/DTEND are absolute UTC instants; the DESCRIPTION carries the dual time-zone
string SPEC 9 asks for ("Oct 14, 2:00 PM EDT = 11:30 PM IST") and a deep link, so a
reader whose calendar client shows its own zone still sees the buyer's clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from icalendar import Calendar, Event

from app.core.display_time import render_tz

PRODUCT_ID = "-//BidRadar//Key dates//EN"
UID_SUFFIX = "@bidradar"
DEFAULT_CALENDAR_NAME = "BidRadar deadlines"
# A deadline is an instant, not a meeting; give it a visible block in the day grid.
DEFAULT_DURATION_MINUTES = 30


def event_uid(pursuit_date_id: object) -> str:
    """Stable per key-date row, so a refresh updates rather than duplicates."""
    return f"pursuit-date-{pursuit_date_id}{UID_SUFFIX}"


@dataclass(frozen=True, slots=True)
class CalendarEntry:
    """One key date, ready to render (plain values: nothing here touches the ORM)."""

    uid: str
    at: datetime
    summary: str
    buyer_tz: str = "UTC"
    user_tz: str | None = None
    note: str | None = None
    url: str | None = None
    sequence: int = 0
    duration_minutes: int = DEFAULT_DURATION_MINUTES
    last_modified: datetime | None = None
    cancelled: bool = False


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be an aware datetime")
    return value.astimezone(UTC)


def build_description(entry: CalendarEntry) -> str:
    """Dual time zone first, then the note, then the deep link (SPEC 9)."""
    lines = [render_tz(_aware(entry.at, "at"), entry.buyer_tz, entry.user_tz, with_year=True)]
    if entry.note:
        lines.append(entry.note)
    if entry.url:
        lines.append(entry.url)
    return "\n".join(lines)


def build_event(entry: CalendarEntry, *, now: datetime | None = None) -> Event:
    start = _aware(entry.at, "at")
    event = Event()
    event.add("uid", entry.uid)
    event.add("dtstamp", _aware(now or datetime.now(UTC), "now"))
    event.add("dtstart", start)
    event.add("dtend", start + timedelta(minutes=max(1, entry.duration_minutes)))
    event.add("summary", entry.summary)
    event.add("description", build_description(entry))
    event.add("sequence", max(0, entry.sequence))
    event.add("status", "CANCELLED" if entry.cancelled else "CONFIRMED")
    event.add("transp", "TRANSPARENT")
    if entry.url:
        event.add("url", entry.url)
    if entry.last_modified is not None:
        event.add("last-modified", _aware(entry.last_modified, "last_modified"))
    return event


def build_calendar(
    entries: list[CalendarEntry],
    *,
    name: str = DEFAULT_CALENDAR_NAME,
    now: datetime | None = None,
) -> bytes:
    """A complete VCALENDAR document (CRLF-terminated, as RFC 5545 requires)."""
    calendar = Calendar()
    calendar.add("prodid", PRODUCT_ID)
    calendar.add("version", "2.0")
    calendar.add("calscale", "GREGORIAN")
    calendar.add("method", "PUBLISH")
    calendar.add("x-wr-calname", name)
    calendar.add("x-wr-timezone", "UTC")
    for entry in entries:
        calendar.add_component(build_event(entry, now=now))
    rendered: bytes = calendar.to_ical()
    return rendered
