"""Pure politeness primitives for the HTTP client (SPEC 5.1): token buckets, daily
quotas, backoff schedule and Retry-After parsing. No I/O; the clock is injected."""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

Clock = Callable[[], float]


class TokenBucket:
    """Classic token bucket: `rate` tokens/second up to `capacity`. `acquire()` returns how
    long the caller must wait before the request may go out (0.0 = now)."""

    def __init__(self, rate: float, capacity: int = 1, *, clock: Clock) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.rate = rate
        self.capacity = max(1, capacity)
        self._clock = clock
        self._tokens = float(self.capacity)
        self._updated = clock()

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
        self._updated = now

    def acquire(self) -> float:
        self._refill()
        if self._tokens >= 1:
            self._tokens -= 1
            return 0.0
        wait = (1 - self._tokens) / self.rate
        # Reserve the token now; the caller sleeps `wait` before sending.
        self._tokens -= 1
        return wait


class DailyQuota:
    """Requests allowed per UTC day (SAM.gov API keys). `take()` returns False when spent."""

    def __init__(self, limit: int, *, clock: Clock) -> None:
        self.limit = limit
        self._clock = clock
        self._day: str | None = None
        self.used = 0

    def _today(self) -> str:
        return datetime.fromtimestamp(self._clock(), tz=UTC).strftime("%Y-%m-%d")

    def _roll(self) -> None:
        today = self._today()
        if today != self._day:
            self._day = today
            self.used = 0

    @property
    def remaining(self) -> int:
        self._roll()
        return max(0, self.limit - self.used)

    def take(self) -> bool:
        self._roll()
        if self.used >= self.limit:
            return False
        self.used += 1
        return True


@dataclass(frozen=True, slots=True)
class HostPolicy:
    rate_per_sec: float
    burst: int = 1
    daily_quota: int | None = None


@dataclass(slots=True)
class PolicyTable:
    """Host -> policy resolution: exact host match, then `.gov.in` default, then default."""

    default_rate: float = 2.0
    gov_in_rate: float = 1.0
    per_host: dict[str, float] = field(default_factory=dict)
    quotas: dict[str, int] = field(default_factory=dict)

    def for_host(self, host: str) -> HostPolicy:
        host = host.lower()
        quota = self.quotas.get(host)
        if host in self.per_host:
            return HostPolicy(self.per_host[host], daily_quota=quota)
        if host.endswith(".gov.in") or host == "gov.in":
            return HostPolicy(self.gov_in_rate, daily_quota=quota)
        return HostPolicy(self.default_rate, daily_quota=quota)


RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


def is_retryable_status(status: int) -> bool:
    return status in RETRY_STATUSES


def backoff_delay(
    attempt: int,
    *,
    base: float = 1.0,
    cap: float = 60.0,
    rng: Callable[[], float] = random.random,
) -> float:
    """Exponential backoff with full jitter: uniform(0, min(cap, base * 2**attempt)).

    attempt is 0-based (0 = delay before the first retry)."""
    ceiling = min(cap, base * (2**attempt))
    return float(ceiling * rng())


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Retry-After as delay seconds (integer form) or an HTTP-date; None if absent/invalid."""
    if not value:
        return None
    text = value.strip()
    if text.isdigit():
        return float(text)
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    now = now or datetime.now(UTC)
    return max(0.0, (when - now).total_seconds())


def user_agent(version: str, contact_email: str) -> str:
    return f"BidRadar/{version} (+mailto:{contact_email})"
