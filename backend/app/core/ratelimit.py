"""Rate-limit primitives. Pure logic; every clock is passed in.

Two of them (SPEC section 11 "rate limiting on auth and API"):

* `FixedWindowLimiter` counts FAILED AUTH attempts per IP. Per-process by design (M0,
  OQ-17): it protects the password path, where an attacker gains nothing from the
  per-instance slack.
* `consume` is the token bucket behind the API-wide limiter (M7-06), which is shared
  across instances through Redis in `app/services/ratelimit.py`.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class FixedWindowLimiter:
    limit: int
    window_seconds: float = 60.0
    clock: Callable[[], float] = time.monotonic
    _counts: dict[tuple[str, int], int] = field(default_factory=dict, init=False, repr=False)

    def _window(self) -> int:
        return int(self.clock() // self.window_seconds)

    def _prune(self, current: int) -> None:
        stale = [key for key in self._counts if key[1] < current]
        for key in stale:
            del self._counts[key]

    def count(self, key: str) -> int:
        return self._counts.get((key, self._window()), 0)

    def hit(self, key: str) -> int:
        """Record one event for `key`; returns the count in the current window."""
        current = self._window()
        self._prune(current)
        slot = (key, current)
        self._counts[slot] = self._counts.get(slot, 0) + 1
        return self._counts[slot]

    def is_limited(self, key: str) -> bool:
        """True once more than `limit` events were recorded in the current window."""
        return self.count(key) > self.limit

    def reset(self, key: str | None = None) -> None:
        if key is None:
            self._counts.clear()
            return
        for slot in [s for s in self._counts if s[0] == key]:
            del self._counts[slot]


def client_ip_from_headers(
    peer_host: str | None, forwarded_for: str | None, *, trust_proxy: bool
) -> str:
    """Pick the client IP: first X-Forwarded-For hop when a trusted proxy fronts the app."""
    if trust_proxy and forwarded_for:
        first = forwarded_for.split(",")[0].strip()
        if first:
            return first
    return peer_host or "unknown"


# --- token bucket (M7-06) ---------------------------------------------------------------
# The API limiter is a token bucket rather than the fixed window above: a fixed window lets
# a client spend its whole allowance in the last second of one window and again in the
# first second of the next, which is exactly the burst we are trying to survive. The maths
# is here, with no clock and no I/O, so the Redis Lua script and the in-process fallback in
# app/services/ratelimit.py can be checked against the same expectations.


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    """`limit` requests per `window_seconds`, refilled continuously."""

    limit: int
    window_seconds: float = 60.0

    def __post_init__(self) -> None:
        if self.limit <= 0:
            raise ValueError("rate limit must be positive")
        if self.window_seconds <= 0:
            raise ValueError("rate limit window must be positive")

    @property
    def refill_per_second(self) -> float:
        return self.limit / self.window_seconds


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    #: seconds a rejected caller should wait; 0 when allowed
    retry_after: int
    #: seconds until the bucket is full again
    reset_after: int

    def headers(self) -> dict[str, str]:
        """RateLimit headers every response carries, plus Retry-After on a rejection."""
        out = {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(self.remaining),
            "X-RateLimit-Reset": str(self.reset_after),
        }
        if not self.allowed:
            out["Retry-After"] = str(self.retry_after)
        return out


@dataclass(frozen=True, slots=True)
class BucketState:
    tokens: float
    updated_at: float


def consume(
    policy: RateLimitPolicy,
    state: BucketState | None,
    now: float,
    cost: float = 1.0,
) -> tuple[RateLimitDecision, BucketState]:
    """Spend `cost` tokens. Returns the decision and the state to persist.

    A missing state is a full bucket, so a first request is never rejected. Time moving
    backwards (a clock correction, or two Redis nodes disagreeing) refills nothing rather
    than draining the bucket.
    """
    capacity = float(policy.limit)
    if state is None:
        tokens = capacity
    else:
        elapsed = max(0.0, now - state.updated_at)
        tokens = min(capacity, state.tokens + elapsed * policy.refill_per_second)

    if tokens >= cost:
        remaining = tokens - cost
        return (
            RateLimitDecision(
                allowed=True,
                limit=policy.limit,
                remaining=int(remaining),
                retry_after=0,
                reset_after=_seconds_to_full(remaining, capacity, policy),
            ),
            BucketState(tokens=remaining, updated_at=now),
        )

    missing = cost - tokens
    retry_after = max(1, math.ceil(missing / policy.refill_per_second))
    return (
        RateLimitDecision(
            allowed=False,
            limit=policy.limit,
            remaining=int(tokens),
            retry_after=retry_after,
            reset_after=_seconds_to_full(tokens, capacity, policy),
        ),
        # A rejected request still costs nothing: refusing it must not push the caller
        # further into debt, or a hammering client would never recover.
        BucketState(tokens=tokens, updated_at=now),
    )


def _seconds_to_full(tokens: float, capacity: float, policy: RateLimitPolicy) -> int:
    return max(0, math.ceil((capacity - tokens) / policy.refill_per_second))
