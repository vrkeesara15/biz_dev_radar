"""M4-13: quiet hours, digest times and the weekly roll-up across DST and IST.

Frozen clocks throughout; the DST boundaries are America/New_York's 2026 transitions
(spring forward 2026-03-08 02:00 -> 03:00, fall back 2026-11-01 02:00 -> 01:00) and
Asia/Kolkata, which has a half-hour offset and no DST at all.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest
from app.notify.scheduling import (
    QUIET_HOURS_OVERRIDE_HOURS,
    DeliveryPlan,
    SchedulePrefs,
    deliver_at,
    digest_window,
    is_digest_due,
    is_weekly_due,
    localize,
    next_digest_at,
    next_weekly_at,
    parse_hhmm,
    zone,
)
from freezegun import freeze_time

NY = "America/New_York"
IST = "Asia/Kolkata"
SPRING_FORWARD = date(2026, 3, 8)  # 02:00 EST -> 03:00 EDT
FALL_BACK = date(2026, 11, 1)  # 02:00 EDT -> 01:00 EST


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def _local(moment: datetime, tz: str) -> str:
    return moment.astimezone(zone(tz)).strftime("%Y-%m-%d %H:%M %Z")


# --- localize ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "clock", "tz", "expected"),
    [
        # ordinary days
        (date(2026, 1, 15), "08:00", NY, "2026-01-15 08:00 EST"),
        (date(2026, 7, 15), "08:00", NY, "2026-07-15 08:00 EDT"),
        (date(2026, 1, 15), "08:00", IST, "2026-01-15 08:00 IST"),
        # the digest hour is unaffected by either transition: still 08:00 local
        (SPRING_FORWARD, "08:00", NY, "2026-03-08 08:00 EDT"),
        (FALL_BACK, "08:00", NY, "2026-11-01 08:00 EST"),
        # a wall clock inside the spring-forward gap moves to the first instant that exists
        (SPRING_FORWARD, "02:30", NY, "2026-03-08 03:00 EDT"),
        (SPRING_FORWARD, "02:00", NY, "2026-03-08 03:00 EDT"),
        (SPRING_FORWARD, "01:59", NY, "2026-03-08 01:59 EST"),
        (SPRING_FORWARD, "03:00", NY, "2026-03-08 03:00 EDT"),
        # an ambiguous wall clock takes the FIRST (daylight) reading
        (FALL_BACK, "01:30", NY, "2026-11-01 01:30 EDT"),
        (FALL_BACK, "01:00", NY, "2026-11-01 01:00 EDT"),
    ],
)
def test_localize_is_dst_safe(day: date, clock: str, tz: str, expected: str) -> None:
    assert _local(localize(day, parse_hhmm(clock), zone(tz)), tz) == expected


def test_ambiguous_time_takes_the_earlier_of_the_two_instants() -> None:
    first = localize(FALL_BACK, time(1, 30), zone(NY))
    assert first == _utc("2026-11-01T05:30:00")  # EDT, not the 06:30 EST repeat


def test_unknown_zone_falls_back_to_utc_and_bad_times_to_the_default() -> None:
    assert zone("Mars/Olympus").key == "UTC"
    assert parse_hhmm("not a time") == time(8, 0)
    assert parse_hhmm(None) == time(8, 0)
    assert parse_hhmm("", default="21:45") == time(21, 45)


# --- quiet hours --------------------------------------------------------------------------------


NIGHT = SchedulePrefs(tz=NY, quiet_hours_start="20:00", quiet_hours_end="07:00")
IST_NIGHT = SchedulePrefs(tz=IST, quiet_hours_start="22:00", quiet_hours_end="06:30")


@pytest.mark.parametrize(
    ("now", "expected_reason", "expected_local"),
    [
        # 14:00 EDT: outside quiet hours, instant
        ("2026-06-15T18:00:00", "instant", "2026-06-15 14:00 EDT"),
        # 23:00 EDT: inside, queued to 07:00 the next morning
        ("2026-06-16T03:00:00", "quiet_hours", "2026-06-16 07:00 EDT"),
        # 02:00 EDT: inside, queued to 07:00 the SAME morning
        ("2026-06-16T06:00:00", "quiet_hours", "2026-06-16 07:00 EDT"),
        # exactly at the end of quiet hours: already awake
        ("2026-06-16T11:00:00", "instant", "2026-06-16 07:00 EDT"),
        # exactly at the start: silenced
        ("2026-06-16T00:00:00", "quiet_hours", "2026-06-16 07:00 EDT"),
    ],
)
def test_quiet_hours_defer_to_the_next_morning(
    now: str, expected_reason: str, expected_local: str
) -> None:
    plan = deliver_at(NIGHT, now=_utc(now))
    assert plan.reason == expected_reason
    assert _local(plan.send_at, NY) == expected_local
    assert plan.deferred is (expected_reason == "quiet_hours")


def test_quiet_hours_across_the_spring_forward_night() -> None:
    # 01:00 EST on the morning the clocks jump: still quiet, released at 07:00 EDT
    plan = deliver_at(NIGHT, now=_utc("2026-03-08T06:00:00"))
    assert plan.reason == "quiet_hours"
    assert _local(plan.send_at, NY) == "2026-03-08 07:00 EDT"
    assert plan.send_at == _utc("2026-03-08T11:00:00")  # 11:00 UTC, not 12:00

    # a quiet-hours end inside the gap is pulled to the first instant that exists
    gap = SchedulePrefs(tz=NY, quiet_hours_start="20:00", quiet_hours_end="02:30")
    plan = deliver_at(gap, now=_utc("2026-03-08T05:00:00"))
    assert _local(plan.send_at, NY) == "2026-03-08 03:00 EDT"


def test_quiet_hours_across_the_fall_back_night() -> None:
    # 00:30 EDT on the morning the clocks repeat an hour: released at 07:00 EST
    plan = deliver_at(NIGHT, now=_utc("2026-11-01T04:30:00"))
    assert plan.reason == "quiet_hours"
    assert _local(plan.send_at, NY) == "2026-11-01 07:00 EST"
    assert plan.send_at == _utc("2026-11-01T12:00:00")  # 12:00 UTC, not 11:00


def test_quiet_hours_in_ist() -> None:
    # 23:30 IST -> released 06:30 IST (01:00 UTC)
    plan = deliver_at(IST_NIGHT, now=_utc("2026-06-15T18:00:00"))
    assert plan.reason == "quiet_hours"
    assert _local(plan.send_at, IST) == "2026-06-16 06:30 IST"
    assert plan.send_at == _utc("2026-06-16T01:00:00")
    # 11:00 IST is the working day
    assert deliver_at(IST_NIGHT, now=_utc("2026-06-15T05:30:00")).reason == "instant"


def test_a_deadline_inside_72_hours_ignores_quiet_hours() -> None:
    now = _utc("2026-06-16T03:00:00")  # 23:00 EDT
    urgent = deliver_at(
        NIGHT, now=now, response_due_at=now + timedelta(hours=QUIET_HOURS_OVERRIDE_HOURS - 1)
    )
    assert urgent.reason == "urgent" and urgent.send_at == now

    patient = deliver_at(
        NIGHT, now=now, response_due_at=now + timedelta(hours=QUIET_HOURS_OVERRIDE_HOURS + 1)
    )
    assert patient.reason == "quiet_hours"
    # exactly 72 h away is not yet "less than 72 h"
    boundary = deliver_at(
        NIGHT, now=now, response_due_at=now + timedelta(hours=QUIET_HOURS_OVERRIDE_HOURS)
    )
    assert boundary.reason == "quiet_hours"
    assert deliver_at(NIGHT, now=now, urgent=True).reason == "urgent"


def test_without_quiet_hours_everything_is_instant() -> None:
    prefs = SchedulePrefs(tz=NY)
    now = _utc("2026-06-16T03:00:00")
    assert deliver_at(prefs, now=now) == DeliveryPlan(now, "no_quiet_hours")
    half = SchedulePrefs(tz=NY, quiet_hours_start="20:00")
    assert deliver_at(half, now=now).reason == "no_quiet_hours"


def test_naive_datetimes_are_rejected() -> None:
    with pytest.raises(ValueError, match="now must be an aware datetime"):
        deliver_at(NIGHT, now=datetime(2026, 6, 16, 3, 0))
    with pytest.raises(ValueError, match="response_due_at must be an aware datetime"):
        deliver_at(
            NIGHT, now=_utc("2026-06-16T03:00:00"), response_due_at=datetime(2026, 6, 17, 3, 0)
        )


# --- digest -------------------------------------------------------------------------------------


MORNING_NY = SchedulePrefs(tz=NY, digest_time="08:00")
MORNING_IST = SchedulePrefs(tz=IST, digest_time="08:00")


@freeze_time("2026-06-15T18:00:00Z")  # 14:00 EDT, already past 08:00
def test_next_digest_is_tomorrow_when_today_has_passed() -> None:
    assert _local(next_digest_at(MORNING_NY, datetime.now(UTC)), NY) == "2026-06-16 08:00 EDT"


@freeze_time("2026-06-15T10:00:00Z")  # 06:00 EDT, still before 08:00
def test_next_digest_is_today_when_it_is_still_ahead() -> None:
    assert _local(next_digest_at(MORNING_NY, datetime.now(UTC)), NY) == "2026-06-15 08:00 EDT"


@pytest.mark.parametrize(
    ("now", "expected_utc"),
    [
        # the day before spring forward: tomorrow's 08:00 is 12:00 UTC, not 13:00
        ("2026-03-07T18:00:00", "2026-03-08T12:00:00"),
        # the day before fall back: tomorrow's 08:00 is 13:00 UTC, not 12:00
        ("2026-10-31T18:00:00", "2026-11-01T13:00:00"),
    ],
)
def test_digest_time_holds_its_local_hour_across_dst(now: str, expected_utc: str) -> None:
    assert next_digest_at(MORNING_NY, _utc(now)) == _utc(expected_utc)


def test_digest_time_in_ist_is_a_half_hour_offset() -> None:
    assert next_digest_at(MORNING_IST, _utc("2026-06-15T18:00:00")) == _utc("2026-06-16T02:30:00")


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        ("2026-06-15T12:00:00", True),  # 08:00 EDT exactly
        ("2026-06-15T12:10:00", True),  # 08:10, inside the 15-minute beat window
        ("2026-06-15T12:20:00", False),  # 08:20, the window has closed
        ("2026-06-15T11:50:00", False),  # 07:50, not due yet
        # the spring-forward day: 08:00 EDT is 12:00 UTC
        ("2026-03-08T12:05:00", True),
        ("2026-03-08T13:05:00", False),
        # the fall-back day: 08:00 EST is 13:00 UTC
        ("2026-11-01T13:05:00", True),
        ("2026-11-01T12:05:00", False),
    ],
)
def test_is_digest_due_matches_the_beat_window(now: str, expected: bool) -> None:
    assert is_digest_due(MORNING_NY, _utc(now)) is expected


def test_is_digest_due_is_not_repeated_for_the_same_day() -> None:
    now = _utc("2026-06-15T12:10:00")
    due_at = _utc("2026-06-15T12:00:00")
    assert is_digest_due(MORNING_NY, now, last_sent_at=due_at - timedelta(days=1)) is True
    assert is_digest_due(MORNING_NY, now, last_sent_at=due_at) is False
    assert is_digest_due(MORNING_NY, now, last_sent_at=due_at + timedelta(minutes=1)) is False


def test_is_digest_due_just_after_local_midnight() -> None:
    """A digest time right after midnight belongs to the new local day, not yesterday's."""
    late = SchedulePrefs(tz=NY, digest_time="00:05")
    assert is_digest_due(late, _utc("2026-06-15T04:10:00")) is True  # 00:10 EDT
    assert is_digest_due(late, _utc("2026-06-15T04:30:00")) is False


# --- weekly roll-up -------------------------------------------------------------------------------


def test_weekly_roll_up_is_the_next_monday_at_digest_time() -> None:
    # Wednesday 2026-06-17 14:00 EDT -> Monday 2026-06-22 08:00 EDT
    assert _local(next_weekly_at(MORNING_NY, _utc("2026-06-17T18:00:00")), NY) == (
        "2026-06-22 08:00 EDT"
    )
    # Monday before 08:00 -> today
    assert _local(next_weekly_at(MORNING_NY, _utc("2026-06-22T10:00:00")), NY) == (
        "2026-06-22 08:00 EDT"
    )
    # Monday after 08:00 -> next week
    assert _local(next_weekly_at(MORNING_NY, _utc("2026-06-22T18:00:00")), NY) == (
        "2026-06-29 08:00 EDT"
    )
    # IST keeps its own Monday
    assert next_weekly_at(MORNING_IST, _utc("2026-06-17T18:00:00")) == _utc("2026-06-22T02:30:00")


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        ("2026-06-22T12:05:00", True),  # Monday 08:05 EDT
        ("2026-06-23T12:05:00", False),  # Tuesday
        ("2026-06-22T14:05:00", False),  # Monday, window closed
        ("2026-06-21T12:05:00", False),  # Sunday
        # a Monday tick just after local midnight still names Sunday's Monday-less day
        ("2026-11-02T13:05:00", True),  # Monday 08:05 EST after the fall-back weekend
    ],
)
def test_is_weekly_due(now: str, expected: bool) -> None:
    assert is_weekly_due(MORNING_NY, _utc(now)) is expected


def test_is_weekly_due_is_not_repeated() -> None:
    now = _utc("2026-06-22T12:05:00")
    assert is_weekly_due(MORNING_NY, now, last_sent_at=_utc("2026-06-22T12:00:00")) is False
    assert is_weekly_due(MORNING_NY, now, last_sent_at=_utc("2026-06-15T12:00:00")) is True


def test_digest_window_covers_the_previous_period() -> None:
    now = _utc("2026-06-22T12:05:00")
    assert digest_window(MORNING_NY, now) == (now - timedelta(days=1), now)
    assert digest_window(MORNING_NY, now, weekly=True) == (now - timedelta(days=7), now)


def test_schedule_prefs_read_a_prefs_row() -> None:
    class Row:
        tz = IST
        quiet_hours_start = "22:00"
        quiet_hours_end = "06:30"
        digest_time = "09:15"

    assert SchedulePrefs.from_row(Row()) == SchedulePrefs(IST, "22:00", "06:30", "09:15")
    assert SchedulePrefs.from_row(object()) == SchedulePrefs("UTC", None, None, "08:00")
