"""Shared API rate limiting (SPEC 11: "rate limiting on auth and API").

The token-bucket maths lives in `app/core/ratelimit.consume`; this module is the two
places it runs:

* `RedisTokenBucketLimiter` — one Lua script, evaluated on Redis, so all Cloud Run
  instances share one bucket. Lua is not an optimisation here: read-modify-write from
  several instances would let N instances each grant a full allowance (the problem OQ-17
  records for the in-process auth limiter).
* `InMemoryTokenBucketLimiter` — the same decision per process. Used in tests and as the
  fallback when Redis is unreachable.

**Redis down means requests are ALLOWED.** A rate limiter protects availability; it is
not an authorisation control, and failing closed would turn a Redis blip into a total
outage. The fallback keeps limiting per instance so a stampede is still blunted, and the
degradation is logged once per cooldown rather than per request.
"""

from __future__ import annotations

import time
from typing import Any, Protocol, runtime_checkable

from app.core.config import Settings
from app.core.ratelimit import BucketState, RateLimitDecision, RateLimitPolicy, consume
from app.logging import get_logger

log = get_logger(__name__)

KEY_PREFIX = "rl"
#: how long a bucket key outlives its last use, in whole seconds
IDLE_TTL_MULTIPLIER = 2
#: seconds between "Redis is down" warnings
DEGRADE_LOG_INTERVAL = 60.0

# Mirror of app.core.ratelimit.consume. KEYS[1] is the bucket hash; ARGV is
# (capacity, refill_per_second, now, cost, ttl). Returns
# {allowed, remaining, retry_after, reset_after}.
TOKEN_BUCKET_LUA = """
local key       = KEYS[1]
local capacity  = tonumber(ARGV[1])
local refill    = tonumber(ARGV[2])
local now       = tonumber(ARGV[3])
local cost      = tonumber(ARGV[4])
local ttl       = tonumber(ARGV[5])

local bucket = redis.call('HMGET', key, 'tokens', 'updated_at')
local tokens = tonumber(bucket[1])
local updated = tonumber(bucket[2])

if tokens == nil or updated == nil then
  tokens = capacity
else
  local elapsed = now - updated
  if elapsed < 0 then elapsed = 0 end
  tokens = math.min(capacity, tokens + elapsed * refill)
end

local allowed = 0
if tokens >= cost then
  allowed = 1
  tokens = tokens - cost
end

redis.call('HSET', key, 'tokens', tokens, 'updated_at', now)
redis.call('EXPIRE', key, ttl)

local retry_after = 0
if allowed == 0 then
  retry_after = math.ceil((cost - tokens) / refill)
  if retry_after < 1 then retry_after = 1 end
end
local reset_after = math.ceil((capacity - tokens) / refill)
if reset_after < 0 then reset_after = 0 end

return {allowed, math.floor(tokens), retry_after, reset_after}
"""


@runtime_checkable
class RateLimiter(Protocol):
    name: str

    async def check(
        self, key: str, policy: RateLimitPolicy, cost: float = 1.0
    ) -> RateLimitDecision:
        """Spend `cost` tokens from `key`'s bucket and say whether the caller may proceed."""
        ...


class NoopRateLimiter:
    """Everything is allowed. Used when RATE_LIMIT_ENABLED is false."""

    name = "noop"

    async def check(
        self, key: str, policy: RateLimitPolicy, cost: float = 1.0
    ) -> RateLimitDecision:
        return RateLimitDecision(
            allowed=True,
            limit=policy.limit,
            remaining=policy.limit,
            retry_after=0,
            reset_after=0,
        )


class InMemoryTokenBucketLimiter:
    """Per-process buckets. Honest in tests, and the fallback when Redis is unreachable."""

    name = "memory"

    def __init__(self, clock: Any = time.time) -> None:
        self._buckets: dict[str, BucketState] = {}
        self._clock = clock

    async def check(
        self, key: str, policy: RateLimitPolicy, cost: float = 1.0
    ) -> RateLimitDecision:
        decision, state = consume(policy, self._buckets.get(key), self._clock(), cost)
        self._buckets[key] = state
        return decision

    def reset(self) -> None:
        self._buckets.clear()


class RedisTokenBucketLimiter:
    """One bucket per key across every instance, via a Lua script."""

    name = "redis"

    def __init__(self, redis: Any, *, fallback: InMemoryTokenBucketLimiter | None = None) -> None:
        self._redis = redis
        self._script: Any | None = None
        self._fallback = fallback or InMemoryTokenBucketLimiter()
        self._last_degrade_log = 0.0

    async def check(
        self, key: str, policy: RateLimitPolicy, cost: float = 1.0
    ) -> RateLimitDecision:
        if self._script is None:
            self._script = self._redis.register_script(TOKEN_BUCKET_LUA)
        now = time.time()
        ttl = max(1, int(policy.window_seconds) * IDLE_TTL_MULTIPLIER)
        try:
            raw = await self._script(
                keys=[f"{KEY_PREFIX}:{key}"],
                args=[policy.limit, policy.refill_per_second, now, cost, ttl],
            )
        except Exception as exc:
            self._log_degraded(exc)
            return await self._fallback.check(key, policy, cost)
        allowed, remaining, retry_after, reset_after = (int(value) for value in raw)
        return RateLimitDecision(
            allowed=bool(allowed),
            limit=policy.limit,
            remaining=max(0, remaining),
            retry_after=retry_after,
            reset_after=reset_after,
        )

    def _log_degraded(self, exc: BaseException) -> None:
        now = time.monotonic()
        if now - self._last_degrade_log < DEGRADE_LOG_INTERVAL:
            return
        self._last_degrade_log = now
        log.warning(
            "ratelimit.redis_unavailable",
            error=str(exc)[:200],
            consequence="per-instance limits only; requests are still served",
        )


def limiter_from_settings(settings: Settings) -> RateLimiter:
    """Redis when configured and enabled, else a no-op (local dev without Redis)."""
    if not settings.rate_limit_enabled:
        return NoopRateLimiter()
    try:
        from redis.asyncio import Redis
    except ImportError:  # pragma: no cover - redis is a hard dependency
        log.warning("ratelimit.redis_missing", consequence="per-instance limits only")
        return InMemoryTokenBucketLimiter()
    # No connection is made here: redis-py connects lazily, so an unreachable Redis costs
    # one failed command per request and the fallback answers, rather than a failed boot.
    return RedisTokenBucketLimiter(Redis.from_url(settings.redis_url, decode_responses=True))


def policies_from_settings(settings: Settings) -> tuple[RateLimitPolicy, RateLimitPolicy]:
    """(per-tenant, per-IP) policies."""
    return (
        RateLimitPolicy(limit=settings.rate_limit_tenant_per_minute, window_seconds=60.0),
        RateLimitPolicy(limit=settings.rate_limit_ip_per_minute, window_seconds=60.0),
    )
