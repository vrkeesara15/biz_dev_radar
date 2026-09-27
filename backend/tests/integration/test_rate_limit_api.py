"""M7-06: API rate limiting per tenant and per IP, with 429 and the headers (SPEC 11).

Three layers are covered here:

* the middleware, against an in-process bucket, so the behaviour is deterministic;
* the Redis Lua script, against the compose Redis, so the shared-bucket claim is real
  rather than a mock agreeing with itself (skipped when Redis is unreachable);
* the degraded path, where Redis is down and requests are still served.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
from app.api.ratelimit_middleware import RateLimitMiddleware, tenant_from_scope, tightest
from app.core.auth import encode_token
from app.core.config import Settings
from app.core.ratelimit import RateLimitDecision, RateLimitPolicy
from app.core.roles import Role
from app.services.ratelimit import (
    InMemoryTokenBucketLimiter,
    NoopRateLimiter,
    RedisTokenBucketLimiter,
    limiter_from_settings,
    policies_from_settings,
)
from fastapi import FastAPI

SECRET = "test-secret-0123456789abcdef0123456789abcdef"


def _app(
    *,
    limiter: Any,
    tenant_limit: int = 100,
    ip_limit: int = 100,
    exempt: tuple[str, ...] = ("/healthz",),
) -> FastAPI:
    app = FastAPI()

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/thing")
    async def thing() -> dict[str, bool]:
        return {"ok": True}

    app.add_middleware(
        RateLimitMiddleware,
        limiter=limiter,
        tenant_policy=RateLimitPolicy(limit=tenant_limit, window_seconds=60),
        ip_policy=RateLimitPolicy(limit=ip_limit, window_seconds=60),
        auth_secret=SECRET,
        exempt_paths=exempt,
    )
    return app


def _token(tenant_id: uuid.UUID) -> str:
    return encode_token(
        user_id=uuid.uuid4(),
        email="a@example.com",
        tenant_id=tenant_id,
        role=Role.BID_MANAGER,
        secret=SECRET,
    )


async def _get(app: FastAPI, path: str = "/api/v1/thing", **kwargs: Any) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, client=("203.0.113.9", 1234))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path, **kwargs)


# ------------------------------------------------------------------ middleware


async def test_requests_under_the_limit_carry_the_headers() -> None:
    app = _app(limiter=InMemoryTokenBucketLimiter(), ip_limit=5)
    response = await _get(app)
    assert response.status_code == 200
    assert response.headers["X-RateLimit-Limit"] == "5"
    assert response.headers["X-RateLimit-Remaining"] == "4"
    assert int(response.headers["X-RateLimit-Reset"]) > 0
    assert "Retry-After" not in response.headers


async def test_exceeding_the_ip_limit_is_429_with_retry_after() -> None:
    app = _app(limiter=InMemoryTokenBucketLimiter(), ip_limit=3)
    for _ in range(3):
        assert (await _get(app)).status_code == 200
    response = await _get(app)
    assert response.status_code == 429
    assert response.json()["detail"] == "rate limit exceeded"
    assert int(response.headers["Retry-After"]) >= 1
    assert response.headers["X-RateLimit-Remaining"] == "0"
    assert response.headers["content-type"] == "application/json"


async def test_the_remaining_header_counts_down() -> None:
    app = _app(limiter=InMemoryTokenBucketLimiter(), ip_limit=4)
    seen = [int((await _get(app)).headers["X-RateLimit-Remaining"]) for _ in range(4)]
    assert seen == [3, 2, 1, 0]


async def test_the_tenant_bucket_is_separate_from_the_ip_bucket() -> None:
    """One tenant exhausting its own allowance must not refuse another tenant."""
    app = _app(limiter=InMemoryTokenBucketLimiter(), tenant_limit=2, ip_limit=1000)
    noisy = _token(uuid.uuid4())
    quiet = _token(uuid.uuid4())
    for _ in range(2):
        assert (await _get(app, headers={"Authorization": f"Bearer {noisy}"})).status_code == 200
    assert (await _get(app, headers={"Authorization": f"Bearer {noisy}"})).status_code == 429
    assert (await _get(app, headers={"Authorization": f"Bearer {quiet}"})).status_code == 200


async def test_an_ip_flood_is_refused_even_with_a_generous_tenant_limit() -> None:
    app = _app(limiter=InMemoryTokenBucketLimiter(), tenant_limit=1000, ip_limit=2)
    token = _token(uuid.uuid4())
    for _ in range(2):
        assert (await _get(app, headers={"Authorization": f"Bearer {token}"})).status_code == 200
    assert (await _get(app, headers={"Authorization": f"Bearer {token}"})).status_code == 429


async def test_the_reported_headers_describe_the_emptiest_bucket() -> None:
    app = _app(limiter=InMemoryTokenBucketLimiter(), tenant_limit=3, ip_limit=100)
    token = _token(uuid.uuid4())
    response = await _get(app, headers={"Authorization": f"Bearer {token}"})
    assert response.headers["X-RateLimit-Limit"] == "3", "the tenant bucket is the binding one"


async def test_healthz_is_never_limited() -> None:
    """Cloud Run's probe must not be refused; a refused probe recycles the container."""
    app = _app(limiter=InMemoryTokenBucketLimiter(), ip_limit=1)
    for _ in range(10):
        assert (await _get(app, path="/healthz")).status_code == 200


async def test_an_exempt_prefix_covers_its_children() -> None:
    middleware = RateLimitMiddleware(
        _app(limiter=InMemoryTokenBucketLimiter()),
        limiter=InMemoryTokenBucketLimiter(),
        tenant_policy=RateLimitPolicy(limit=1),
        ip_policy=RateLimitPolicy(limit=1),
        auth_secret=SECRET,
        exempt_paths=("/healthz", "/api/v1/webhooks"),
    )
    assert middleware.is_exempt("/api/v1/webhooks/stripe")
    assert middleware.is_exempt("/healthz")
    assert not middleware.is_exempt("/api/v1/webhooksomething")
    assert not middleware.is_exempt("/api/v1/opportunities")


async def test_a_bad_token_falls_back_to_the_ip_bucket_and_still_reaches_the_401() -> None:
    """A malformed token is the auth layer's business; the limiter must not swallow it."""
    app = _app(limiter=InMemoryTokenBucketLimiter(), tenant_limit=1, ip_limit=50)
    for _ in range(5):
        response = await _get(app, headers={"Authorization": "Bearer not-a-jwt"})
        assert response.status_code == 200, "no tenant bucket, so only the IP limit applies"


def test_tenant_extraction_never_raises() -> None:
    scope: dict[str, Any] = {"headers": []}
    assert tenant_from_scope(scope, SECRET) is None
    assert tenant_from_scope({"headers": [(b"authorization", b"Bearer junk")]}, SECRET) is None
    assert tenant_from_scope({"headers": [(b"authorization", b"Basic x")]}, SECRET) is None
    tenant = uuid.uuid4()
    header = f"Bearer {_token(tenant)}".encode()
    assert tenant_from_scope({"headers": [(b"authorization", header)]}, SECRET) == str(tenant)
    # A token signed with a different secret is not a tenant.
    other = encode_token(
        user_id=uuid.uuid4(),
        email="a@example.com",
        tenant_id=tenant,
        role=Role.VIEWER,
        secret="a-different-secret-aaaaaaaaaaaaaaaaaaaaaa",
    )
    assert (
        tenant_from_scope({"headers": [(b"authorization", f"Bearer {other}".encode())]}, SECRET)
        is None
    )


def test_the_tightest_decision_wins() -> None:
    ok_low = RateLimitDecision(allowed=True, limit=10, remaining=1, retry_after=0, reset_after=5)
    ok_high = RateLimitDecision(allowed=True, limit=99, remaining=90, retry_after=0, reset_after=1)
    assert tightest([ok_high, ok_low]) is ok_low

    soon = RateLimitDecision(allowed=False, limit=10, remaining=0, retry_after=2, reset_after=9)
    later = RateLimitDecision(allowed=False, limit=10, remaining=0, retry_after=30, reset_after=60)
    assert tightest([ok_high, soon, later]) is later


# ------------------------------------------------------------------ settings wiring


def test_the_limiter_is_off_when_the_setting_is_off() -> None:
    settings = Settings(_env_file=None, rate_limit_enabled=False)  # type: ignore[call-arg]
    assert isinstance(limiter_from_settings(settings), NoopRateLimiter)


def test_policies_come_from_settings() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, rate_limit_tenant_per_minute=77, rate_limit_ip_per_minute=11
    )
    tenant, ip = policies_from_settings(settings)
    assert (tenant.limit, ip.limit) == (77, 11)
    assert (tenant.window_seconds, ip.window_seconds) == (60.0, 60.0)


def test_the_app_installs_the_limiter_above_the_routers() -> None:
    from app.main import create_app

    app = create_app(Settings(_env_file=None, rate_limit_enabled=False))  # type: ignore[call-arg]
    order = [entry.cls.__name__ for entry in app.user_middleware]
    assert order.index("RateLimitMiddleware") < order.index("AuditMiddleware")
    assert order.index("RequestIdMiddleware") < order.index("RateLimitMiddleware"), (
        "a 429 must still carry X-Request-ID"
    )
    # A browser client can only read the headers CORS exposes.
    cors = next(e for e in app.user_middleware if e.cls.__name__ == "CORSMiddleware")
    for header in ("X-RateLimit-Limit", "X-RateLimit-Remaining", "Retry-After"):
        assert header in cors.kwargs["expose_headers"]


def test_the_failed_auth_limiter_is_untouched() -> None:
    """SPEC 11 asks for rate limiting on auth AND api; M7-06 adds the second, not a swap."""
    from app.core.ratelimit import FixedWindowLimiter
    from app.main import create_app

    app = create_app(Settings(_env_file=None, rate_limit_enabled=False))  # type: ignore[call-arg]
    assert isinstance(app.state.auth_limiter, FixedWindowLimiter)


# ------------------------------------------------------------------ Redis (the real one)


@pytest.fixture
async def redis_client() -> AsyncIterator[Any]:
    redis_module = pytest.importorskip("redis.asyncio")
    import os

    url = os.environ.get("REDIS_URL", "redis://localhost:6380/0")
    client = redis_module.Redis.from_url(url, decode_responses=True)
    try:
        await client.ping()
    except Exception as exc:
        await client.aclose()
        pytest.skip(f"no Redis at {url}: {exc}")
    yield client
    await client.aclose()


@pytest.fixture
def bucket_key() -> Iterator[str]:
    yield f"test:{uuid.uuid4()}"


async def test_the_lua_script_agrees_with_the_python_maths(
    redis_client: Any, bucket_key: str
) -> None:
    limiter = RedisTokenBucketLimiter(redis_client)
    policy = RateLimitPolicy(limit=4, window_seconds=60)
    remaining = [(await limiter.check(bucket_key, policy)).remaining for _ in range(4)]
    assert remaining == [3, 2, 1, 0]
    refused = await limiter.check(bucket_key, policy)
    assert not refused.allowed
    assert refused.retry_after == 15  # 4 per 60s is one token every 15s
    assert refused.reset_after == 60


async def test_two_limiter_instances_share_one_bucket(redis_client: Any, bucket_key: str) -> None:
    """The whole point of Redis: two Cloud Run instances must not each grant a full
    allowance (the per-instance slack OQ-17 records for the auth limiter)."""
    policy = RateLimitPolicy(limit=3, window_seconds=60)
    instance_a = RedisTokenBucketLimiter(redis_client)
    instance_b = RedisTokenBucketLimiter(redis_client)
    assert (await instance_a.check(bucket_key, policy)).allowed
    assert (await instance_b.check(bucket_key, policy)).allowed
    assert (await instance_a.check(bucket_key, policy)).allowed
    assert not (await instance_b.check(bucket_key, policy)).allowed


async def test_the_bucket_key_expires_so_idle_tenants_cost_nothing(
    redis_client: Any, bucket_key: str
) -> None:
    limiter = RedisTokenBucketLimiter(redis_client)
    await limiter.check(bucket_key, RateLimitPolicy(limit=5, window_seconds=60))
    ttl = await redis_client.ttl(f"rl:{bucket_key}")
    assert 0 < ttl <= 120


async def test_redis_being_down_serves_the_request(bucket_key: str) -> None:
    """Fail OPEN: a rate limiter is availability protection, not an authorisation check."""

    class BrokenRedis:
        def register_script(self, _source: str) -> Any:
            async def call(**_kwargs: Any) -> Any:
                raise ConnectionError("redis is gone")

            return call

    limiter = RedisTokenBucketLimiter(BrokenRedis())
    policy = RateLimitPolicy(limit=2, window_seconds=60)
    first = await limiter.check(bucket_key, policy)
    assert first.allowed, "a Redis outage must not become a site outage"
    # ...but the in-process fallback still blunts a stampede.
    await limiter.check(bucket_key, policy)
    assert not (await limiter.check(bucket_key, policy)).allowed


async def test_a_middleware_over_real_redis_refuses_the_flood(
    redis_client: Any, bucket_key: str
) -> None:
    app = _app(limiter=RedisTokenBucketLimiter(redis_client), ip_limit=3, tenant_limit=1000)
    statuses = [(await _get(app)).status_code for _ in range(5)]
    assert statuses.count(200) == 3
    assert statuses.count(429) == 2
    for key in await redis_client.keys("rl:ip:203.0.113.9"):
        await redis_client.delete(key)
