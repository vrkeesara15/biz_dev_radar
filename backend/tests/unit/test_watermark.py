"""M2-01: watermark helper (SPEC 5.1: overlap each window by 2 days)."""

from datetime import UTC, datetime, timedelta, timezone
from itertools import pairwise

from app.core.watermark import advance_watermark, bounded_windows, since_from_watermark

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


def test_since_is_watermark_minus_two_days() -> None:
    wm = datetime(2026, 9, 20, 8, 30, tzinfo=UTC)
    assert since_from_watermark(wm, now=NOW) == wm - timedelta(days=2)


def test_since_converts_source_zone_to_utc() -> None:
    ist = timezone(timedelta(hours=5, minutes=30))
    wm = datetime(2026, 9, 20, 14, 0, tzinfo=ist)
    assert since_from_watermark(wm, now=NOW) == datetime(2026, 9, 18, 8, 30, tzinfo=UTC)


def test_first_run_uses_default_lookback_and_never_future() -> None:
    assert since_from_watermark(None, now=NOW) == NOW - timedelta(days=30)
    assert since_from_watermark(NOW + timedelta(days=10), now=NOW) == NOW


def test_bounded_windows_cap_at_one_year() -> None:
    since = NOW - timedelta(days=800)
    windows = bounded_windows(since, NOW)
    assert [w.length.days for w in windows] == [365, 365, 70]
    assert windows[0].start == since and windows[-1].end == NOW
    assert all(a.end == b.start for a, b in pairwise(windows))
    single = bounded_windows(NOW - timedelta(days=3), NOW)
    assert len(single) == 1 and single[0].length == timedelta(days=3)
    empty = bounded_windows(NOW, NOW - timedelta(days=1))
    assert len(empty) == 1 and empty[0].length == timedelta(0)


def test_advance_watermark() -> None:
    assert advance_watermark(None, None) is None
    assert advance_watermark(None, NOW) == NOW
    assert advance_watermark(NOW, None) == NOW
    later = NOW + timedelta(hours=1)
    assert advance_watermark(NOW, later) == later
    assert advance_watermark(later, NOW) == later
