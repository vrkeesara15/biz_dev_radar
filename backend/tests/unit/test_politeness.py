"""M2-02: pure politeness primitives."""

from datetime import UTC, datetime

import pytest
from app.core.politeness import (
    DailyQuota,
    PolicyTable,
    TokenBucket,
    backoff_delay,
    is_retryable_status,
    parse_retry_after,
    user_agent,
)


class FakeClock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def test_token_bucket_one_per_second() -> None:
    clock = FakeClock()
    bucket = TokenBucket(1.0, clock=clock)
    assert bucket.acquire() == 0.0
    assert bucket.acquire() == pytest.approx(1.0)
    assert bucket.acquire() == pytest.approx(2.0)
    clock.advance(2.0)
    assert bucket.acquire() == pytest.approx(1.0)
    clock.advance(10.0)
    assert bucket.acquire() == 0.0  # capacity caps at 1 token
    assert bucket.acquire() == pytest.approx(1.0)


def test_token_bucket_burst_and_validation() -> None:
    clock = FakeClock()
    bucket = TokenBucket(2.0, capacity=3, clock=clock)
    assert [bucket.acquire() for _ in range(3)] == [0.0, 0.0, 0.0]
    assert bucket.acquire() == pytest.approx(0.5)
    with pytest.raises(ValueError):
        TokenBucket(0, clock=clock)


def test_daily_quota_resets_at_utc_midnight() -> None:
    clock = FakeClock(datetime(2026, 9, 26, 23, 59, 50, tzinfo=UTC).timestamp())
    quota = DailyQuota(2, clock=clock)
    assert quota.remaining == 2
    assert quota.take() and quota.take()
    assert not quota.take()
    assert quota.remaining == 0
    clock.advance(20)  # past midnight UTC
    assert quota.remaining == 2
    assert quota.take()


def test_policy_table_resolution() -> None:
    table = PolicyTable(per_host={"api.sam.gov": 0.5}, quotas={"api.sam.gov": 10})
    assert table.for_host("eprocure.gov.in").rate_per_sec == 1.0
    assert table.for_host("TNTENDERS.GOV.IN").rate_per_sec == 1.0
    assert table.for_host("api.grants.gov").rate_per_sec == 2.0
    sam = table.for_host("api.sam.gov")
    assert sam.rate_per_sec == 0.5 and sam.daily_quota == 10
    assert table.for_host("api.grants.gov").daily_quota is None
    assert table.for_host("example.gov.in.evil.com").rate_per_sec == 2.0


def test_backoff_is_exponential_with_full_jitter_and_capped() -> None:
    assert backoff_delay(0, rng=lambda: 1.0) == 1.0
    assert backoff_delay(1, rng=lambda: 1.0) == 2.0
    assert backoff_delay(3, rng=lambda: 1.0) == 8.0
    assert backoff_delay(10, rng=lambda: 1.0) == 60.0
    assert backoff_delay(3, rng=lambda: 0.25) == 2.0
    assert 0.0 <= backoff_delay(2) <= 4.0


def test_retry_after_seconds_and_http_date() -> None:
    now = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
    assert parse_retry_after("7") == 7.0
    assert parse_retry_after(" 120 ") == 120.0
    assert parse_retry_after("Sat, 26 Sep 2026 12:00:30 GMT", now=now) == 30.0
    assert parse_retry_after("Sat, 26 Sep 2026 11:00:00 GMT", now=now) == 0.0
    assert parse_retry_after(None) is None
    assert parse_retry_after("") is None
    assert parse_retry_after("soon") is None


def test_retryable_statuses_and_user_agent() -> None:
    assert all(is_retryable_status(s) for s in (429, 500, 502, 503, 504))
    assert not any(is_retryable_status(s) for s in (200, 301, 400, 401, 403, 404, 422))
    assert user_agent("0.1.0", "ops@example.com") == "BidRadar/0.1.0 (+mailto:ops@example.com)"
