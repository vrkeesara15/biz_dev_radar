"""M7-06: the token bucket behind the API rate limiter. Pure maths, no clock, no Redis."""

from __future__ import annotations

import pytest
from app.core.ratelimit import (
    BucketState,
    RateLimitDecision,
    RateLimitPolicy,
    consume,
)


def test_a_fresh_key_starts_full() -> None:
    decision, state = consume(RateLimitPolicy(limit=10), None, now=100.0)
    assert decision.allowed
    assert decision.remaining == 9
    assert state.tokens == pytest.approx(9.0)


def test_the_allowance_is_spent_then_refused() -> None:
    policy = RateLimitPolicy(limit=3, window_seconds=60)
    state = None
    for expected_remaining in (2, 1, 0):
        decision, state = consume(policy, state, now=0.0)
        assert decision.allowed
        assert decision.remaining == expected_remaining

    decision, state = consume(policy, state, now=0.0)
    assert not decision.allowed
    assert decision.remaining == 0
    # 3 per 60s is one token every 20s.
    assert decision.retry_after == 20
    assert decision.reset_after == 60


def test_a_rejection_does_not_push_the_caller_further_into_debt() -> None:
    """Hammering must not extend the ban, or a retrying client never recovers."""
    policy = RateLimitPolicy(limit=2, window_seconds=60)
    state = BucketState(tokens=0.0, updated_at=0.0)
    for _ in range(50):
        decision, state = consume(policy, state, now=0.0)
        assert not decision.allowed
    assert state.tokens == pytest.approx(0.0)
    # After one refill period the caller gets exactly one request back.
    decision, state = consume(policy, state, now=30.0)
    assert decision.allowed


def test_tokens_refill_continuously_not_in_a_jump() -> None:
    policy = RateLimitPolicy(limit=60, window_seconds=60)  # one per second
    state = BucketState(tokens=0.0, updated_at=0.0)
    decision, state = consume(policy, state, now=10.0)
    assert decision.allowed
    assert decision.remaining == 9, "ten seconds buys ten tokens, one of which we spent"


def test_the_bucket_never_overfills() -> None:
    policy = RateLimitPolicy(limit=5, window_seconds=60)
    state = BucketState(tokens=5.0, updated_at=0.0)
    decision, state = consume(policy, state, now=100_000.0)
    assert decision.remaining == 4
    assert state.tokens == pytest.approx(4.0)


def test_a_clock_that_went_backwards_refills_nothing_and_drains_nothing() -> None:
    policy = RateLimitPolicy(limit=5, window_seconds=60)
    state = BucketState(tokens=2.0, updated_at=1000.0)
    decision, new_state = consume(policy, state, now=900.0)
    assert decision.allowed
    assert new_state.tokens == pytest.approx(1.0)


def test_a_burst_cannot_straddle_two_windows() -> None:
    """The reason this is a token bucket and not a fixed window.

    A fixed window lets a client spend its whole allowance at 59s and again at 61s. Here
    the second burst is refused until the tokens have actually refilled.
    """
    policy = RateLimitPolicy(limit=10, window_seconds=60)
    state = None
    for _ in range(10):
        decision, state = consume(policy, state, now=59.0)
        assert decision.allowed
    allowed_in_the_next_second = 0
    for _ in range(10):
        decision, state = consume(policy, state, now=61.0)
        allowed_in_the_next_second += int(decision.allowed)
    assert allowed_in_the_next_second == 0


def test_cost_can_exceed_one() -> None:
    policy = RateLimitPolicy(limit=10, window_seconds=60)
    decision, state = consume(policy, None, now=0.0, cost=4)
    assert decision.allowed and decision.remaining == 6
    decision, state = consume(policy, state, now=0.0, cost=7)
    assert not decision.allowed


def test_headers_are_the_ones_clients_read() -> None:
    allowed = RateLimitDecision(
        allowed=True, limit=100, remaining=42, retry_after=0, reset_after=17
    )
    assert allowed.headers() == {
        "X-RateLimit-Limit": "100",
        "X-RateLimit-Remaining": "42",
        "X-RateLimit-Reset": "17",
    }
    rejected = RateLimitDecision(
        allowed=False, limit=100, remaining=0, retry_after=9, reset_after=60
    )
    assert rejected.headers()["Retry-After"] == "9"


@pytest.mark.parametrize(("limit", "window"), [(0, 60), (-1, 60), (10, 0), (10, -5)])
def test_a_nonsensical_policy_is_refused_at_construction(limit: int, window: float) -> None:
    with pytest.raises(ValueError):
        RateLimitPolicy(limit=limit, window_seconds=window)
