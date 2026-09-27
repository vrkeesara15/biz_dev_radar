"""Incremental-fetch windows (SPEC 5.1: overlap each window by 2 days)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

OVERLAP = timedelta(days=2)
DEFAULT_LOOKBACK = timedelta(days=30)
MAX_WINDOW = timedelta(days=365)


def since_from_watermark(
    watermark: datetime | None,
    *,
    now: datetime | None = None,
    overlap: timedelta = OVERLAP,
    default_lookback: timedelta = DEFAULT_LOOKBACK,
) -> datetime:
    """`since` for the next fetch: watermark - overlap, or now - default_lookback when
    the source has never run. Never in the future."""
    now = now or datetime.now(UTC)
    if watermark is None:
        return now - default_lookback
    since = watermark.astimezone(UTC) - overlap
    return min(since, now)


@dataclass(frozen=True, slots=True)
class Window:
    start: datetime
    end: datetime

    @property
    def length(self) -> timedelta:
        return self.end - self.start


def bounded_windows(
    since: datetime, until: datetime | None = None, *, max_window: timedelta = MAX_WINDOW
) -> list[Window]:
    """Split [since, until] into consecutive windows no longer than `max_window`.

    SAM.gov rejects postedFrom/postedTo spans over one year, so a source that has been
    idle longer than that is caught up in year-sized steps.
    """
    until = until or datetime.now(UTC)
    since = since.astimezone(UTC)
    until = until.astimezone(UTC)
    if until <= since:
        return [Window(since, since)]
    windows: list[Window] = []
    start = since
    while start < until:
        end = min(start + max_window, until)
        windows.append(Window(start, end))
        start = end
    return windows


def advance_watermark(current: datetime | None, seen: datetime | None) -> datetime | None:
    """New watermark = max(current, latest posted_at seen). None-safe."""
    if seen is None:
        return current
    seen = seen.astimezone(UTC)
    if current is None:
        return seen
    return max(current.astimezone(UTC), seen)
