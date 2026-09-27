"""Date/time parsing for Indian portals and dual time-zone rendering (SPEC 5.3, 9, 12). Pure.

    parse_in("17-Jul-2026 08:23 PM")           -> ParsedDateTime(utc=2026-07-17T14:53Z,
                                                   source_tz="Asia/Kolkata", has_time=True)
    dual_tz(due_utc, "America/New_York", IST)  -> "Oct 14, 2:00 PM EDT = 11:30 PM IST"

Portal strings are day-first (DD-MM-YYYY, DD/MM/YYYY, 17-Jul-2026, 17 July 2026) with an
optional 24 h or AM/PM time and trailing "Hrs"/"IST" noise; ISO 8601 is accepted too.
Everything is stored as aware UTC with the source zone kept alongside (SPEC 5.3).
Bad input returns None, never raises; an unknown zone name raises ValueError.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

from app.core.timezones import known_timezones

IST = "Asia/Kolkata"

_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_NUMERIC = re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})(?:[ T]+(.+))?$")
_TEXT_MONTH = re.compile(r"^(\d{1,2})[-/ .]([A-Za-z]{3,9})[-/ .]?,?\s*(\d{4})(?:[ T,]+(.+))?$")
_TIME = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(am|pm)?$", re.IGNORECASE)
_TIME_NOISE = re.compile(r"\s*(?:hrs?\.?|hours|ist|\(ist\))$", re.IGNORECASE)
_OFFSET_NAME = re.compile(r"^UTC([+-])(\d{2}):(\d{2})$")


@dataclass(frozen=True, slots=True)
class ParsedDateTime:
    utc: datetime
    source_tz: str  # IANA name, or "UTC±HH:MM" when the text carried a foreign offset
    has_time: bool  # False when the text was a bare date (stored as local midnight)

    @property
    def local(self) -> datetime:
        return self.utc.astimezone(_zone_or_offset(self.source_tz))


def _zone(name: str) -> ZoneInfo:
    candidate = name.strip()
    if candidate not in known_timezones():
        raise ValueError(f"unknown time zone {name!r}")
    return ZoneInfo(candidate)


def _zone_or_offset(name: str) -> tzinfo:
    match = _OFFSET_NAME.match(name)
    if match:
        sign = 1 if match.group(1) == "+" else -1
        delta = timedelta(hours=int(match.group(2)), minutes=int(match.group(3)))
        return timezone(sign * delta)
    return _zone(name)


def _offset_name(offset: timedelta | None) -> str:
    seconds = int((offset or timedelta()).total_seconds())
    if seconds == 0:
        return "UTC"
    sign = "+" if seconds > 0 else "-"
    hours, rest = divmod(abs(seconds), 3600)
    return f"UTC{sign}{hours:02d}:{rest // 60:02d}"


def to_utc(value: datetime, tz: str = IST) -> datetime:
    """Aware UTC datetime; a naive value is interpreted in `tz`."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=_zone(tz))
    return value.astimezone(UTC)


def in_tz(value: datetime, tz: str) -> datetime:
    """Convert an aware datetime into `tz`. Naive input is an error (ambiguous)."""
    if value.tzinfo is None:
        raise ValueError("in_tz needs an aware datetime")
    return value.astimezone(_zone(tz))


def _parse_time(text: str) -> tuple[int, int, int] | None:
    part = text.strip()
    while True:
        cleaned = _TIME_NOISE.sub("", part)
        if cleaned == part:
            break
        part = cleaned
    match = _TIME.match(part)
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    second = int(match.group(3) or 0)
    meridiem = (match.group(4) or "").lower()
    if minute > 59 or second > 59:
        return None
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if meridiem == "pm" else 0)
    elif hour > 23:
        return None
    return hour, minute, second


def _from_parts(
    day: int, month: int, year: int, rest: str | None, zone: ZoneInfo, tz_name: str
) -> ParsedDateTime | None:
    clock = (0, 0, 0)
    if rest is not None:
        parsed_time = _parse_time(rest)
        if parsed_time is None:
            return None
        clock = parsed_time
    try:
        local = datetime(year, month, day, *clock, tzinfo=zone)
    except ValueError:
        return None
    return ParsedDateTime(local.astimezone(UTC), tz_name, rest is not None)


def _from_iso(text: str, zone: ZoneInfo, tz_name: str) -> ParsedDateTime | None:
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        return None
    has_time = not (len(text) == 10 and value.time() == datetime.min.time())
    if value.tzinfo is None:
        return ParsedDateTime(value.replace(tzinfo=zone).astimezone(UTC), tz_name, has_time)
    offset = value.utcoffset()
    source = tz_name if offset == value.astimezone(zone).utcoffset() else _offset_name(offset)
    return ParsedDateTime(value.astimezone(UTC), source, has_time)


def parse_in(text: str | None, tz: str = IST) -> ParsedDateTime | None:
    """Parse a portal date/time string in `tz` (default IST). None when unparseable."""
    zone = _zone(tz)
    tz_name = tz.strip()
    if not text:
        return None
    cleaned = " ".join(text.replace("\u00a0", " ").split())
    if not cleaned:
        return None
    if cleaned[:4].isdigit():
        return _from_iso(cleaned, zone, tz_name)
    numeric = _NUMERIC.match(cleaned)
    if numeric:
        day, month, year, rest = numeric.groups()
        return _from_parts(int(day), int(month), int(year), rest, zone, tz_name)
    textual = _TEXT_MONTH.match(cleaned)
    if textual:
        day, month_name, year, rest = textual.groups()
        month = _month(month_name)
        if month is None:
            return None
        return _from_parts(int(day), month, int(year), rest, zone, tz_name)
    return None


_FULL_MONTHS = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)


def _month(name: str) -> int | None:
    key = name.lower()
    if key in _MONTHS:
        return _MONTHS[key]
    if key in _FULL_MONTHS:
        return _FULL_MONTHS.index(key) + 1
    return None


# --- rendering (SPEC 9) --------------------------------------------------------------------


def _clock(local: datetime) -> str:
    hour = local.hour % 12 or 12
    meridiem = "AM" if local.hour < 12 else "PM"
    return f"{hour}:{local.minute:02d} {meridiem} {local.tzname()}"


def _date(local: datetime, with_year: bool) -> str:
    text = f"{local.strftime('%b')} {local.day}"
    return f"{text}, {local.year}" if with_year else text


def _render(local: datetime, *, with_date: bool, with_year: bool) -> str:
    clock = _clock(local)
    return f"{_date(local, with_year)}, {clock}" if with_date else clock


def format_local(
    value: datetime, tz: str, *, with_date: bool = True, with_year: bool = False
) -> str:
    """'Oct 14, 2:00 PM EDT' (with_year: 'Oct 14, 2026, 2:00 PM EDT'; no date: '2:00 PM EDT')."""
    return _render(in_tz(value, tz), with_date=with_date, with_year=with_year)


def dual_tz(value: datetime, buyer_tz: str, user_tz: str | None, *, with_year: bool = False) -> str:
    """Buyer-zone rendering with the user's zone alongside (SPEC 9).

    Same local date in both zones: "Oct 14, 2:00 PM EDT = 11:30 PM IST".
    Different dates (overnight): "Oct 14, 5:00 PM EDT = Oct 15, 2:30 AM IST".
    Same offset and abbreviation, or no user zone: the buyer rendering alone."""
    buyer = in_tz(value, buyer_tz)
    if user_tz is None:
        return _render(buyer, with_date=True, with_year=with_year)
    user = in_tz(value, user_tz)
    if buyer.utcoffset() == user.utcoffset() and buyer.tzname() == user.tzname():
        return _render(buyer, with_date=True, with_year=with_year)
    left = _render(buyer, with_date=True, with_year=with_year)
    right = _render(user, with_date=buyer.date() != user.date(), with_year=with_year)
    return f"{left} = {right}"
