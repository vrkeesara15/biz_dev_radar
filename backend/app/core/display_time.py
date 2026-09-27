"""Dual time-zone rendering shared by the API, email templates and the UI (SPEC 7, 9). Pure.

    tz_fields(due_utc, "America/New_York", user_tz="Asia/Kolkata")
        -> TzDateOut(utc=..., buyer_tz=..., buyer_local="2026-10-14T14:00:00-04:00",
                     buyer_display="Oct 14, 2:00 PM EDT", user_local=..., user_display=...,
                     display="Oct 14, 2:00 PM EDT = 11:30 PM IST")
    render_tz(due_utc, buyer_tz, user_tz)     -> "Oct 14, 2:00 PM EDT = 11:30 PM IST"
    countdown(now, due)                       -> "3d 4h" | "6h 12m" | "45m" | "due now"
                                                 | "overdue 2h"
    render_due(due_utc, buyer_tz, user_tz, now) -> "... (in 3d 4h)"

API responses carry every date as a TzDateOut (UTC plus the buyer's zone, and the user's
when known); email and UI use the plain-string helpers so all three agree. Every input
must be an aware datetime; naive values raise ValueError (SPEC 5.3: stored UTC).
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

from app.core.dates import dual_tz, format_local, in_tz

_MINUTE = 60
_HOUR = 3600
_DAY = 86400


class TzDateOut(BaseModel):
    """One deadline in every zone the reader cares about (JSON: utc as ISO-8601 'Z')."""

    model_config = ConfigDict(frozen=True)

    utc: datetime
    buyer_tz: str
    buyer_local: str  # ISO-8601 with offset, for calendars and the UI's own formatting
    buyer_display: str  # "Oct 14, 2:00 PM EDT"
    user_tz: str | None = None
    user_local: str | None = None
    user_display: str | None = None  # "Oct 14, 11:30 PM IST"
    display: str  # "Oct 14, 2:00 PM EDT = 11:30 PM IST" (buyer alone without a user zone)


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be an aware datetime")
    return value.astimezone(UTC)


def render_tz(
    value: datetime, buyer_tz: str, user_tz: str | None = None, *, with_year: bool = False
) -> str:
    """Plain string for email/UI: buyer zone with the user's alongside (core.dates.dual_tz)."""
    return dual_tz(_aware(value, "value"), buyer_tz, user_tz, with_year=with_year)


def render_local(
    value: datetime, tz: str, *, with_date: bool = True, with_year: bool = False
) -> str:
    """Plain string in one zone: 'Oct 14, 2:00 PM EDT'."""
    return format_local(_aware(value, "value"), tz, with_date=with_date, with_year=with_year)


def tz_fields(
    value: datetime, buyer_tz: str, user_tz: str | None = None, *, with_year: bool = False
) -> TzDateOut:
    """API representation of one instant: UTC, the buyer's zone and (optionally) the user's."""
    at = _aware(value, "value")
    buyer = in_tz(at, buyer_tz)
    user = None if user_tz is None else in_tz(at, user_tz)
    return TzDateOut(
        utc=at,
        buyer_tz=buyer_tz,
        buyer_local=buyer.isoformat(),
        buyer_display=format_local(at, buyer_tz, with_year=with_year),
        user_tz=user_tz,
        user_local=None if user is None else user.isoformat(),
        user_display=None if user_tz is None else format_local(at, user_tz, with_year=with_year),
        display=dual_tz(at, buyer_tz, user_tz, with_year=with_year),
    )


def tz_fields_or_none(
    value: datetime | None, buyer_tz: str, user_tz: str | None = None, *, with_year: bool = False
) -> TzDateOut | None:
    """Convenience for nullable date columns (questions_due_at, prebid_meeting_at, ...)."""
    if value is None:
        return None
    return tz_fields(value, buyer_tz, user_tz, with_year=with_year)


def _humanize(seconds: int) -> str:
    """Floor to the two most significant units: '3d 4h', '6h 12m', '45m'."""
    days, rest = divmod(seconds, _DAY)
    hours, rest = divmod(rest, _HOUR)
    minutes = rest // _MINUTE
    if days:
        return f"{days}d {hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    return f"{minutes}m"


def countdown(now: datetime, due: datetime) -> str:
    """Humanized time until `due`: '3d 4h', '6h 12m', '45m'; 'due now' within a minute
    either way; 'overdue 2h' once past. Both datetimes must be aware."""
    remaining = int((_aware(due, "due") - _aware(now, "now")).total_seconds())
    if abs(remaining) < _MINUTE:
        return "due now"
    if remaining < 0:
        return f"overdue {_humanize(-remaining)}"
    return _humanize(remaining)


def render_due(
    value: datetime,
    buyer_tz: str,
    user_tz: str | None,
    now: datetime,
    *,
    with_year: bool = False,
) -> str:
    """'Oct 14, 2:00 PM EDT = 11:30 PM IST (in 3d 4h)' for reminders and digests."""
    remaining = countdown(now, value)
    suffix = remaining if remaining.startswith(("overdue", "due now")) else f"in {remaining}"
    return f"{render_tz(value, buyer_tz, user_tz, with_year=with_year)} ({suffix})"
