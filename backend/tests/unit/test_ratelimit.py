"""M0-07: fixed-window limiter (pure)."""

from app.core.ratelimit import FixedWindowLimiter, client_ip_from_headers


def test_window_counts_and_rollover() -> None:
    now = [1000.0]
    limiter = FixedWindowLimiter(limit=3, window_seconds=60, clock=lambda: now[0])
    for i in range(1, 4):
        assert limiter.hit("ip") == i
        assert not limiter.is_limited("ip")
    assert limiter.hit("ip") == 4
    assert limiter.is_limited("ip")
    assert not limiter.is_limited("other")
    now[0] += 60
    assert limiter.count("ip") == 0
    assert not limiter.is_limited("ip")
    assert limiter.hit("ip") == 1
    # old windows were pruned
    assert len(limiter._counts) == 1


def test_reset() -> None:
    limiter = FixedWindowLimiter(limit=1, window_seconds=60, clock=lambda: 0.0)
    limiter.hit("a")
    limiter.hit("a")
    limiter.hit("b")
    assert limiter.is_limited("a")
    limiter.reset("a")
    assert not limiter.is_limited("a") and limiter.count("b") == 1
    limiter.reset()
    assert limiter.count("b") == 0


def test_client_ip_selection() -> None:
    assert client_ip_from_headers("1.1.1.1", "9.9.9.9, 10.0.0.1", trust_proxy=False) == "1.1.1.1"
    assert client_ip_from_headers("1.1.1.1", "9.9.9.9, 10.0.0.1", trust_proxy=True) == "9.9.9.9"
    assert client_ip_from_headers("1.1.1.1", " , ", trust_proxy=True) == "1.1.1.1"
    assert client_ip_from_headers(None, None, trust_proxy=True) == "unknown"
