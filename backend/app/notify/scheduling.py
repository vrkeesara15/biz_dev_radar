"""Quiet hours, digest times and the weekly roll-up (SPEC 7, 4.6). Pure, no I/O.

    plan = deliver_at(prefs, now=..., response_due_at=..., urgent=False)
    plan.send_at      # aware UTC instant the delivery is due
    plan.reason       # "instant" | "quiet_hours" | "urgent" | "no_quiet_hours"

    next_digest_at(prefs, now)     # next digest_time in the user's zone
    next_weekly_at(prefs, now)     # next Monday at digest_time in the user's zone
    digest_window(prefs, now)      # (from, to) covered by the digest being sent now
    is_digest_due(prefs, now, last_sent_at)

Everything is computed in the user's own time zone, so a digest at 08:00 stays at 08:00
across a DST change instead of drifting by an hour. Two local clock times are unsafe:

  * the hour that does not exist on a spring-forward day (02:30 on 2026-03-08 in
    America/New_York) - we move forward to the first instant that does exist, so an
    08:00 digest is never skipped and a 02:30 quiet-hours end becomes 03:00;
  * the hour that happens twice on a fall-back day (01:30 on 2026-11-01) - we take the
    FIRST occurrence (fold=0), the earlier of the two, so nothing is delayed by an hour.

SPEC 7's exception wins over quiet hours: a notice whose response is due in less than
QUIET_HOURS_OVERRIDE_HOURS (72 h) goes out instantly whatever the clock says.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.preferences import DEFAULT_DIGEST_TIME, in_quiet_hours

QUIET_HOURS_OVERRIDE_HOURS = 72
MONDAY = 0
_ONE_MINUTE = timedelta(minutes=1)
_ONE_DAY = timedelta(days=1)
_GAP_SEARCH_MINUTES = 24 * 60


@dataclass(frozen=True, slots=True)
class SchedulePrefs:
    """The subset of user_notification_prefs the scheduler needs (plain, not the ORM row)."""

    tz: str = "UTC"
    quiet_hours_start: str | None = None
    quiet_hours_end: str | None = None
    digest_time: str = DEFAULT_DIGEST_TIME

    @classmethod
    def from_row(cls, row: object) -> SchedulePrefs:
        return cls(
            tz=str(getattr(row, "tz", None) or "UTC"),
            quiet_hours_start=getattr(row, "quiet_hours_start", None),
            quiet_hours_end=getattr(row, "quiet_hours_end", None),
            digest_time=str(getattr(row, "digest_time", None) or DEFAULT_DIGEST_TIME),
        )


@dataclass(frozen=True, slots=True)
class DeliveryPlan:
    send_at: datetime  # aware, UTC
    reason: str

    @property
    def deferred(self) -> bool:
        return self.reason == "quiet_hours"


def zone(name: str) -> ZoneInfo:
    """The user's zone, falling back to UTC for a name the host does not know."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def parse_hhmm(value: str | None, default: str = DEFAULT_DIGEST_TIME) -> time:
    text = (value or default).strip()
    try:
        hour, minute = (int(part) for part in text.split(":", 1))
        return time(hour, minute)
    except ValueError:
        hour, minute = (int(part) for part in default.split(":", 1))
        return time(hour, minute)


def localize(day: date, clock: time, tz: ZoneInfo) -> datetime:
    """A local wall-clock time as a real instant (UTC), DST-safe (see the module docstring).

    fold=0 already picks the FIRST of two ambiguous readings. A wall clock inside a
    spring-forward gap does not round-trip, and there is no arithmetic shortcut to the
    transition, so we step forward a minute at a time from the earliest plausible instant
    and return the first one whose local clock has reached what was asked for.
    """
    naive = datetime.combine(day, clock)
    candidate = naive.replace(tzinfo=tz, fold=0)
    if candidate.astimezone(UTC).astimezone(tz).replace(tzinfo=None) == naive:
        return candidate.astimezone(UTC)
    probe = naive.replace(tzinfo=tz, fold=1).astimezone(UTC)
    for _ in range(_GAP_SEARCH_MINUTES):
        if probe.astimezone(tz).replace(tzinfo=None) >= naive:
            return probe
        probe += _ONE_MINUTE
    return candidate.astimezone(UTC)  # pragma: no cover - no zone has a gap this long


def _aware(moment: datetime, label: str = "now") -> datetime:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"{label} must be an aware datetime")
    return moment.astimezone(UTC)


def _local(moment: datetime, tz: ZoneInfo) -> datetime:
    return _aware(moment).astimezone(tz)


def _hhmm(moment: datetime) -> str:
    return f"{moment.hour:02d}:{moment.minute:02d}"


def deliver_at(
    prefs: SchedulePrefs,
    *,
    now: datetime,
    response_due_at: datetime | None = None,
    urgent: bool = False,
) -> DeliveryPlan:
    """When an instant notification should actually go out (SPEC 7 quiet hours)."""
    tz = zone(prefs.tz)
    moment = _aware(now)
    if not prefs.quiet_hours_start or not prefs.quiet_hours_end:
        return DeliveryPlan(moment, "no_quiet_hours")
    local = _local(moment, tz)
    if not in_quiet_hours(_hhmm(local), prefs.quiet_hours_start, prefs.quiet_hours_end):
        return DeliveryPlan(moment, "instant")
    if urgent:
        return DeliveryPlan(moment, "urgent")
    if response_due_at is not None:
        remaining = _aware(response_due_at, "response_due_at") - moment
        if remaining < timedelta(hours=QUIET_HOURS_OVERRIDE_HOURS):
            return DeliveryPlan(moment, "urgent")
    return DeliveryPlan(next_local_time(prefs.quiet_hours_end, tz, local), "quiet_hours")


def next_local_time(hhmm: str, tz: ZoneInfo, local_now: datetime) -> datetime:
    """The next occurrence of a local wall-clock time, today if still ahead."""
    clock = parse_hhmm(hhmm)
    today = localize(local_now.date(), clock, tz)
    if today > _aware(local_now):
        return today
    return localize(local_now.date() + _ONE_DAY, clock, tz)


def next_digest_at(prefs: SchedulePrefs, now: datetime) -> datetime:
    """The next daily digest instant in the user's zone."""
    tz = zone(prefs.tz)
    return next_local_time(prefs.digest_time, tz, _local(now, tz))


def next_weekly_at(prefs: SchedulePrefs, now: datetime) -> datetime:
    """The next Monday-at-digest-time instant in the user's zone (SPEC 7 weekly roll-up)."""
    tz = zone(prefs.tz)
    local = _local(now, tz)
    clock = parse_hhmm(prefs.digest_time)
    for ahead in range(8):
        day = local.date() + timedelta(days=ahead)
        if day.weekday() != MONDAY:
            continue
        candidate = localize(day, clock, tz)
        if candidate > _aware(now):
            return candidate
    raise AssertionError("a Monday always falls within eight days")  # pragma: no cover


def is_digest_due(
    prefs: SchedulePrefs,
    now: datetime,
    *,
    last_sent_at: datetime | None = None,
    slack: timedelta = timedelta(minutes=15),
) -> bool:
    """True when the beat tick at `now` should send this user's daily digest.

    The beat runs every 15 minutes, so "due" means the digest instant fell inside the
    window (now - slack, now] and no digest has been sent since that instant.
    """
    tz = zone(prefs.tz)
    local = _local(now, tz)
    clock = parse_hhmm(prefs.digest_time)
    moment = _aware(now)
    for ahead in (0, -1):
        due = localize(local.date() + timedelta(days=ahead), clock, tz)
        if moment - slack < due <= moment:
            return last_sent_at is None or last_sent_at.astimezone(UTC) < due
    return False


def is_weekly_due(
    prefs: SchedulePrefs,
    now: datetime,
    *,
    last_sent_at: datetime | None = None,
    slack: timedelta = timedelta(minutes=15),
) -> bool:
    """Same window test for the Monday roll-up."""
    tz = zone(prefs.tz)
    local = _local(now, tz)
    clock = parse_hhmm(prefs.digest_time)
    moment = _aware(now)
    for ahead in (0, -1):
        day = local.date() + timedelta(days=ahead)
        if day.weekday() != MONDAY:
            continue
        due = localize(day, clock, tz)
        if moment - slack < due <= moment:
            return last_sent_at is None or last_sent_at.astimezone(UTC) < due
    return False


def digest_window(
    prefs: SchedulePrefs, now: datetime, *, weekly: bool = False
) -> tuple[datetime, datetime]:
    """What the digest being sent at `now` covers: the previous period up to now."""
    moment = _aware(now)
    span = timedelta(days=7) if weekly else _ONE_DAY
    return moment - span, moment
