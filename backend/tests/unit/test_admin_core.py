"""M7-08: pure helpers behind the admin console (app/core/admin.py)."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from app.core.admin import (
    DEFAULT_SUPPORT_MINUTES,
    MAX_SUPPORT_MINUTES,
    MIN_SUPPORT_MINUTES,
    clamp_support_minutes,
    current_period,
    grant_is_active,
    parse_period,
    period_bounds,
    shift_period,
    support_access_expiry,
    usd_from_microusd,
    worst_health,
)

JAN = datetime(2026, 1, 17, 9, 30, tzinfo=UTC)


def test_current_period_is_utc_yyyy_mm() -> None:
    assert current_period(JAN) == "2026-01"
    # a naive value is read as UTC; an offset value is converted first
    assert current_period(datetime(2026, 12, 31, 23, 0)) == "2026-12"
    # 01 Jan 2027 02:00 IST is still 31 Dec 2026 in UTC, which is the period that counts.
    ist = datetime(2027, 1, 1, 2, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    assert current_period(ist) == "2026-12"


def test_current_period_uses_now_when_omitted() -> None:
    assert current_period() == current_period(datetime.now(UTC))


@pytest.mark.parametrize("value", ["2026-01", "1999-12", "2026-10"])
def test_parse_period_accepts_valid_months(value: str) -> None:
    assert parse_period(value) == value


@pytest.mark.parametrize("value", ["2026-13", "2026-00", "26-01", "2026/01", "january", "2026-1"])
def test_parse_period_rejects_garbage(value: str) -> None:
    with pytest.raises(ValueError, match="YYYY-MM"):
        parse_period(value)


def test_parse_period_defaults_to_the_current_month() -> None:
    assert parse_period(None, now=JAN) == "2026-01"
    assert parse_period("", now=JAN) == "2026-01"
    assert parse_period("  2026-03  ") == "2026-03"


def test_shift_period_crosses_year_boundaries() -> None:
    assert shift_period("2026-01", -1) == "2025-12"
    assert shift_period("2026-12", 1) == "2027-01"
    assert shift_period("2026-06", 0) == "2026-06"
    assert shift_period("2026-01", -13) == "2024-12"
    with pytest.raises(ValueError, match="YYYY-MM"):
        shift_period("nope", 1)
    with pytest.raises(ValueError, match="out of range"):
        shift_period("0001-01", -1)


def test_period_bounds_is_half_open_and_utc() -> None:
    start, end = period_bounds("2026-02")
    assert start == datetime(2026, 2, 1, tzinfo=UTC)
    assert end == datetime(2026, 3, 1, tzinfo=UTC)
    start, end = period_bounds("2026-12")
    assert end == datetime(2027, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="YYYY-MM"):
        period_bounds("2026-99")


def test_usd_from_microusd_is_exact() -> None:
    # OQ-46: usage_ledger meters LLM spend as integer micro-dollars.
    assert usd_from_microusd(1_000_000) == Decimal("1.000000")
    assert usd_from_microusd(1) == Decimal("0.000001")
    assert usd_from_microusd(0) == Decimal("0")
    assert usd_from_microusd(None) == Decimal("0")
    assert usd_from_microusd(12_345_678) == Decimal("12.345678")


def test_clamp_support_minutes() -> None:
    assert clamp_support_minutes(None) == DEFAULT_SUPPORT_MINUTES
    assert clamp_support_minutes(0) == MIN_SUPPORT_MINUTES
    assert clamp_support_minutes(-5) == MIN_SUPPORT_MINUTES
    assert clamp_support_minutes(10_000) == MAX_SUPPORT_MINUTES
    assert clamp_support_minutes(30) == 30


def test_support_access_expiry_is_time_boxed() -> None:
    assert support_access_expiry(JAN, 30) == JAN + timedelta(minutes=30)
    assert support_access_expiry(JAN, None) == JAN + timedelta(minutes=DEFAULT_SUPPORT_MINUTES)
    assert support_access_expiry(JAN, 10_000) == JAN + timedelta(minutes=MAX_SUPPORT_MINUTES)


def test_grant_is_active_only_while_unrevoked_and_unexpired() -> None:
    later = JAN + timedelta(minutes=30)
    assert grant_is_active(later, None, JAN) is True
    assert grant_is_active(JAN, None, JAN) is False  # expiry is exclusive
    assert grant_is_active(later, JAN, JAN) is False  # revoked
    assert grant_is_active(JAN - timedelta(minutes=1), None, JAN) is False
    assert grant_is_active(datetime.now(UTC) + timedelta(hours=1), None) is True


def test_worst_health_rollup() -> None:
    assert worst_health([]) == "ok"
    assert worst_health(["ok", "ok"]) == "ok"
    assert worst_health(["ok", "degraded"]) == "degraded"
    assert worst_health(["degraded", "failing", "ok"]) == "failing"
    assert worst_health(["ok", "not_implemented"]) == "not_implemented"
    assert worst_health(["something-new"]) == "unknown"
