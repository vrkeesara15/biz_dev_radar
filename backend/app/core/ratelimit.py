"""In-memory fixed-window rate limiter. Pure logic; the clock is injectable.

Used for auth failures per IP (SPEC section 11). Per-process by design for M0; a
Redis-backed limiter with the same interface can replace it behind the API.
"""

from __future__ import annotations

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
