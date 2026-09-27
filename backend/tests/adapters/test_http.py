"""M2-02: PoliteClient over respx mocks (no network)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx
from app import __version__
from app.adapters.http import (
    MemoryArchiver,
    PoliteClient,
    QuotaExhaustedError,
    RetryExhaustedError,
    RobotsDisallowedError,
    StorageArchiver,
    policy_table_from_settings,
)
from app.core.config import Settings
from app.core.politeness import PolicyTable
from app.services.storage import LocalStorage

FIXED_NOW = datetime(2026, 9, 26, 10, 30, 15, tzinfo=UTC)


class FakeClock:
    def __init__(self) -> None:
        self.t = FIXED_NOW.timestamp()
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        # Sub-millisecond throttle waits (from the very high test default rate) are noise.
        if seconds >= 0.001:
            self.sleeps.append(seconds)
        self.t += seconds


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def settings() -> Settings:
    return Settings(_env_file=None, contact_email="ops@bidradar.test", sam_daily_quota=3)  # type: ignore[call-arg]


@pytest.fixture()
def client(settings: Settings, clock: FakeClock) -> PoliteClient:
    # Settings-derived table, but a very high default rate so backoff tests see only
    # backoff sleeps; .gov.in and the SAM quota keep their real values.
    policies = policy_table_from_settings(settings)
    policies.default_rate = 1e6
    return PoliteClient(
        settings=settings,
        archiver=MemoryArchiver(),
        clock=clock,
        sleep=clock.sleep,
        rng=lambda: 1.0,  # deterministic: maximum jitter
        now=lambda: datetime.fromtimestamp(clock.t, tz=UTC),
        policies=policies,
    )


def test_policy_table_from_settings(settings: Settings) -> None:
    table = policy_table_from_settings(settings)
    assert table.default_rate == 2.0 and table.gov_in_rate == 1.0
    assert table.quotas == {"api.sam.gov": 3}
    assert table.per_host == {}


def _allow_robots(host: str, body: str = "User-agent: *\nAllow: /\n") -> None:
    respx.get(f"https://{host}/robots.txt").mock(return_value=httpx.Response(200, text=body))


@respx.mock
def test_user_agent_and_archive_key(client: PoliteClient) -> None:
    _allow_robots("api.grants.gov")
    route = respx.post("https://api.grants.gov/v1/api/search2").mock(
        return_value=httpx.Response(
            200, json={"hitCount": 1}, headers={"content-type": "application/json"}
        )
    )
    result = client.post(
        "https://api.grants.gov/v1/api/search2",
        json={"rows": 25},
        source_id="grants_gov",
        external_id="search2/page-0",
    )
    assert result.status_code == 200 and result.json() == {"hitCount": 1}
    sent = route.calls.last.request
    assert sent.headers["user-agent"] == f"BidRadar/{__version__} (+mailto:ops@bidradar.test)"
    assert result.raw_ref == "raw/grants_gov/2026/09/26/search2%2Fpage-0/2026-09-26T10:30:15Z"
    archiver = client.archiver
    assert isinstance(archiver, MemoryArchiver)
    body, content_type = archiver.objects[result.raw_ref]
    assert body == b'{"hitCount":1}' or body == b'{"hitCount": 1}'
    assert content_type == "application/json"
    assert result.attempts == 1


@respx.mock
def test_gov_in_hosts_are_limited_to_one_request_per_second(
    client: PoliteClient, clock: FakeClock
) -> None:
    _allow_robots("eprocure.gov.in")
    respx.get("https://eprocure.gov.in/page").mock(return_value=httpx.Response(200, text="ok"))
    for _ in range(3):
        client.get("https://eprocure.gov.in/page", source_id="cppp", external_id="list")
    # robots fetch consumed the first token; three page fetches each wait ~1s
    assert clock.sleeps == pytest.approx([1.0, 1.0, 1.0])


@respx.mock
def test_per_host_rate_override_from_settings(clock: FakeClock) -> None:
    settings = Settings(_env_file=None, http_rate_limits='{"api.sam.gov": 0.5}')  # type: ignore[call-arg]
    client = PoliteClient(settings=settings, clock=clock, sleep=clock.sleep, rng=lambda: 0.0)
    _allow_robots("api.sam.gov")
    respx.get("https://api.sam.gov/opportunities/v2/search").mock(
        return_value=httpx.Response(200, json={})
    )
    client.get(
        "https://api.sam.gov/opportunities/v2/search", source_id="sam_opps", external_id="p0"
    )
    client.get(
        "https://api.sam.gov/opportunities/v2/search", source_id="sam_opps", external_id="p1"
    )
    assert clock.sleeps == pytest.approx([2.0, 2.0])
    assert client.policies.for_host("api.grants.gov").rate_per_sec == 2.0


@respx.mock
def test_sam_daily_quota_is_enforced(client: PoliteClient, clock: FakeClock) -> None:
    _allow_robots("api.sam.gov")
    respx.get("https://api.sam.gov/opportunities/v2/search").mock(
        return_value=httpx.Response(200, json={})
    )
    url = "https://api.sam.gov/opportunities/v2/search"
    assert client.remaining_quota("api.sam.gov") == 3
    for i in range(3):
        client.get(url, source_id="sam_opps", external_id=f"p{i}")
    assert client.remaining_quota("api.sam.gov") == 0
    with pytest.raises(QuotaExhaustedError):
        client.get(url, source_id="sam_opps", external_id="p3")
    clock.t += 86_400
    assert client.remaining_quota("api.sam.gov") == 3
    assert client.remaining_quota("api.grants.gov") is None


@respx.mock
def test_backoff_on_429_and_5xx_then_success(client: PoliteClient, clock: FakeClock) -> None:
    _allow_robots("api.usaspending.gov")
    route = respx.get("https://api.usaspending.gov/thing").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(503),
            httpx.Response(500),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    result = client.get(
        "https://api.usaspending.gov/thing", source_id="usaspending", external_id="x"
    )
    assert result.status_code == 200 and result.attempts == 4
    assert route.call_count == 4
    # Retry-After honoured verbatim, then exponential 2**1, 2**2 with rng=1.0 (max jitter)
    assert clock.sleeps == pytest.approx([7.0, 2.0, 4.0])


@respx.mock
def test_retry_after_http_date(client: PoliteClient, clock: FakeClock) -> None:
    _allow_robots("api.usaspending.gov")
    respx.get("https://api.usaspending.gov/thing").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "Sat, 26 Sep 2026 10:31:15 GMT"}),
            httpx.Response(200, json={}),
        ]
    )
    client.get("https://api.usaspending.gov/thing", source_id="usaspending", external_id="x")
    assert clock.sleeps == pytest.approx([60.0])


@respx.mock
def test_gives_up_after_five_attempts(client: PoliteClient, clock: FakeClock) -> None:
    _allow_robots("api.usaspending.gov")
    route = respx.get("https://api.usaspending.gov/thing").mock(return_value=httpx.Response(503))
    with pytest.raises(RetryExhaustedError) as exc:
        client.get("https://api.usaspending.gov/thing", source_id="usaspending", external_id="x")
    assert route.call_count == 5
    assert exc.value.attempts == 5
    assert isinstance(exc.value.last, httpx.Response) and exc.value.last.status_code == 503
    assert len(clock.sleeps) == 4, "no sleep after the final attempt"
    assert clock.sleeps == pytest.approx([1.0, 2.0, 4.0, 8.0])


@respx.mock
def test_transport_errors_are_retried(client: PoliteClient) -> None:
    _allow_robots("api.usaspending.gov")
    route = respx.get("https://api.usaspending.gov/thing").mock(
        side_effect=[httpx.ConnectError("boom"), httpx.Response(200, json={})]
    )
    result = client.get(
        "https://api.usaspending.gov/thing", source_id="usaspending", external_id="x"
    )
    assert result.attempts == 2 and route.call_count == 2


@respx.mock
def test_non_retryable_errors_are_returned_not_retried(client: PoliteClient) -> None:
    _allow_robots("api.usaspending.gov")
    route = respx.get("https://api.usaspending.gov/missing").mock(return_value=httpx.Response(404))
    result = client.get(
        "https://api.usaspending.gov/missing", source_id="usaspending", external_id="x"
    )
    assert result.status_code == 404 and route.call_count == 1
    assert result.raw_ref is not None, "error bodies are archived too (portal change audit)"


@respx.mock
def test_robots_disallow_raises_and_is_cached_per_host(client: PoliteClient) -> None:
    robots = respx.get("https://mahatenders.gov.in/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /nicgep/\nAllow: /\n")
    )
    page = respx.get("https://mahatenders.gov.in/nicgep/app").mock(
        return_value=httpx.Response(200, text="never")
    )
    ok = respx.get("https://mahatenders.gov.in/public").mock(return_value=httpx.Response(200))
    with pytest.raises(RobotsDisallowedError):
        client.get("https://mahatenders.gov.in/nicgep/app", source_id="gepnic_mh", external_id="x")
    assert page.call_count == 0, "the request must never be sent"
    client.get("https://mahatenders.gov.in/public", source_id="gepnic_mh", external_id="y")
    client.get("https://mahatenders.gov.in/public", source_id="gepnic_mh", external_id="z")
    assert robots.call_count == 1 and ok.call_count == 2
    assert client.allowed_by_robots("https://mahatenders.gov.in/nicgep/x") is False


@respx.mock
def test_robots_disallow_all(client: PoliteClient) -> None:
    respx.get("https://closed.gov.in/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /\n")
    )
    with pytest.raises(RobotsDisallowedError):
        client.get("https://closed.gov.in/", source_id="s", external_id="x")


@respx.mock
def test_missing_robots_allows_and_forbidden_robots_blocks(client: PoliteClient) -> None:
    respx.get("https://open.example/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://open.example/a").mock(return_value=httpx.Response(200))
    assert client.get("https://open.example/a", source_id="s", external_id="x").status_code == 200
    respx.get("https://locked.example/robots.txt").mock(return_value=httpx.Response(403))
    with pytest.raises(RobotsDisallowedError):
        client.get("https://locked.example/a", source_id="s", external_id="x")


@respx.mock
def test_robots_server_error_blocks_but_is_not_cached(client: PoliteClient) -> None:
    robots = respx.get("https://flaky.example/robots.txt").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, text="User-agent: *\nAllow: /\n")]
    )
    respx.get("https://flaky.example/a").mock(return_value=httpx.Response(200))
    with pytest.raises(RobotsDisallowedError):
        client.get("https://flaky.example/a", source_id="s", external_id="x")
    assert client.get("https://flaky.example/a", source_id="s", external_id="x").status_code == 200
    assert robots.call_count == 2


@respx.mock
def test_storage_archiver_outside_event_loop(
    tmp_path: Path, clock: FakeClock, settings: Settings
) -> None:
    storage = LocalStorage(tmp_path, "bidradar-us", signing_secret="s")
    client = PoliteClient(
        settings=settings,
        archiver=StorageArchiver(storage),
        clock=clock,
        sleep=clock.sleep,
        now=lambda: FIXED_NOW,
    )
    _allow_robots("api.sam.gov")
    respx.get("https://api.sam.gov/opp").mock(return_value=httpx.Response(200, text="body"))
    result = client.get("https://api.sam.gov/opp", source_id="sam_opps", external_id="abc")
    assert result.raw_ref == "raw/sam_opps/2026/09/26/abc/2026-09-26T10:30:15Z"
    assert (tmp_path / "bidradar-us" / result.raw_ref).read_bytes() == b"body"


@respx.mock
async def test_storage_archiver_inside_running_event_loop(
    tmp_path: Path, clock: FakeClock, settings: Settings
) -> None:
    storage = LocalStorage(tmp_path, "bidradar-us", signing_secret="s")
    archiver = StorageArchiver(storage)
    client = PoliteClient(
        settings=settings, archiver=archiver, clock=clock, sleep=clock.sleep, now=lambda: FIXED_NOW
    )
    _allow_robots("api.sam.gov")
    respx.get("https://api.sam.gov/opp").mock(return_value=httpx.Response(200, text="loop"))
    result = client.get("https://api.sam.gov/opp", source_id="sam_opps", external_id="abc")
    assert result.raw_ref is not None
    assert await storage.get(result.raw_ref) == b"loop"


@respx.mock
def test_archive_can_be_skipped_and_policy_injection(clock: FakeClock, settings: Settings) -> None:
    archiver = MemoryArchiver()
    client = PoliteClient(
        settings=settings,
        archiver=archiver,
        clock=clock,
        sleep=clock.sleep,
        policies=PolicyTable(default_rate=100.0),
    )
    _allow_robots("api.sam.gov")
    respx.get("https://api.sam.gov/opp").mock(return_value=httpx.Response(200, text="x"))
    result = client.get(
        "https://api.sam.gov/opp", source_id="sam_opps", external_id="abc", archive=False
    )
    assert result.raw_ref is None and archiver.objects == {}
    assert client.remaining_quota("api.sam.gov") is None
