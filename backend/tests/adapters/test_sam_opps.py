"""M2-04: SAM.gov opportunities adapter over respx (no network)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx
from app.adapters.http import MemoryArchiver, PoliteClient, RetryExhaustedError
from app.adapters.registry import get_adapter_class
from app.adapters.sam_opps import SEARCH_URL, SamApiError, SamOpportunitiesAdapter, decode_cursor
from app.core.config import Settings
from app.core.normalize.sam import link_amendments
from app.core.politeness import PolicyTable

FIXTURES = Path(__file__).parent / "fixtures" / "sam_opps"
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
PAGE1 = json.loads((FIXTURES / "page1.json").read_text())
PAGE2 = json.loads((FIXTURES / "page2.json").read_text())


class FakeClock:
    def __init__(self) -> None:
        self.t = NOW.timestamp()
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        if seconds >= 0.001:
            self.sleeps.append(seconds)
        self.t += seconds


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def settings() -> Settings:
    return Settings(_env_file=None, sam_api_key="test-key", sam_daily_quota=1000)  # type: ignore[call-arg]


@pytest.fixture()
def adapter(settings: Settings, clock: FakeClock) -> SamOpportunitiesAdapter:
    client = PoliteClient(
        settings=settings,
        archiver=MemoryArchiver(),
        clock=clock,
        sleep=clock.sleep,
        rng=lambda: 1.0,
        now=lambda: datetime.fromtimestamp(clock.t, tz=UTC),
        policies=PolicyTable(default_rate=1e6, quotas={"api.sam.gov": 1000}),
    )
    return SamOpportunitiesAdapter(client=client, settings=settings, now=lambda: NOW, page_size=5)


def _robots() -> None:
    respx.get("https://api.sam.gov/robots.txt").mock(return_value=httpx.Response(404))


def _page(offset: int, body: dict) -> respx.Route:  # type: ignore[type-arg]
    return respx.get(SEARCH_URL, params={"offset": str(offset)}).mock(
        return_value=httpx.Response(200, json=body)
    )


def test_registered_with_spec_schedule() -> None:
    assert get_adapter_class("sam_opps") is SamOpportunitiesAdapter
    assert SamOpportunitiesAdapter.schedule == "*/30 * * * *"
    assert SamOpportunitiesAdapter.region == "us"


@respx.mock
def test_fetch_paginates_by_offset_until_total_records(adapter: SamOpportunitiesAdapter) -> None:
    _robots()
    page1 = _page(0, PAGE1)
    page2 = _page(5, PAGE2)
    since = NOW - timedelta(days=30)
    records = list(adapter.fetch(since, None))
    assert [r.external_id for r in records] == [
        item["noticeId"] for item in [*PAGE1["opportunitiesData"], *PAGE2["opportunitiesData"]]
    ]
    assert page1.call_count == 1 and page2.call_count == 1
    params = dict(page1.calls.last.request.url.params)
    assert params["limit"] == "5" and params["api_key"] == "test-key"
    assert params["postedFrom"] == "08/27/2026" and params["postedTo"] == "09/26/2026"
    assert all(r.raw_ref is not None for r in records)
    assert records[0].raw_ref != records[5].raw_ref
    # cursor: page records carry the page offset, the last record of a page the next offset
    assert decode_cursor(records[0].meta["cursor"]) == {
        "from": "08/27/2026",
        "to": "09/26/2026",
        "offset": 0,
    }
    assert decode_cursor(records[4].meta["cursor"])["offset"] == 5  # type: ignore[index]
    assert decode_cursor(records[5].meta["cursor"])["offset"] == 6  # type: ignore[index]
    assert adapter.health().status.value == "ok"


@respx.mock
def test_posted_from_is_since_in_mm_dd_yyyy_and_window_capped_at_one_year(
    adapter: SamOpportunitiesAdapter,
) -> None:
    _robots()
    route = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200, json={"totalRecords": 0, "limit": 5, "offset": 0, "opportunitiesData": []}
        )
    )
    since = NOW - timedelta(days=800)
    assert list(adapter.fetch(since, None)) == []
    windows = [
        (dict(c.request.url.params)["postedFrom"], dict(c.request.url.params)["postedTo"])
        for c in route.calls
    ]
    assert len(windows) == 3
    assert windows[0][0] == "07/18/2024"
    assert windows[-1][1] == "09/26/2026"
    for start, end in windows:
        a = datetime.strptime(start, "%m/%d/%Y")
        b = datetime.strptime(end, "%m/%d/%Y")
        assert timedelta(0) < b - a <= timedelta(days=366)
    # consecutive windows chain
    assert windows[0][1] == windows[1][0] and windows[1][1] == windows[2][0]


@respx.mock
def test_fixture_normalizes_with_type_codes_and_amendment_link(
    adapter: SamOpportunitiesAdapter,
) -> None:
    _robots()
    _page(0, PAGE1)
    _page(5, PAGE2)
    opps = link_amendments(
        adapter.normalize(r) for r in adapter.fetch(NOW - timedelta(days=30), None)
    )
    assert [o.notice_type.value for o in opps] == [
        "presolicitation",
        "rfp",
        "combined",
        "sources_sought",
        "award",
        "special",
    ]
    assert opps[1].parent_external_id == opps[0].external_id
    assert opps[1].solicitation_number == opps[0].solicitation_number == "W911NF-26-R-0007"
    assert opps[0].parent_external_id is None
    docs = adapter.fetch_documents(next(iter(adapter.fetch(NOW - timedelta(days=30), None))))
    assert len(docs) == 2 and all(d.url.startswith("https://sam.gov/api/") for d in docs)


@respx.mock
def test_429_triggers_backoff_and_continues(
    adapter: SamOpportunitiesAdapter, clock: FakeClock
) -> None:
    _robots()
    _page(0, PAGE1)
    page2 = respx.get(SEARCH_URL, params={"offset": "5"}).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "3"}),
            httpx.Response(200, json=PAGE2),
        ]
    )
    records = list(adapter.fetch(NOW - timedelta(days=30), None))
    assert len(records) == 6
    assert page2.call_count == 2
    assert clock.sleeps == pytest.approx([3.0])


@respx.mock
def test_persistent_429_aborts_and_resumes_from_cursor(
    adapter: SamOpportunitiesAdapter, clock: FakeClock
) -> None:
    _robots()
    page1 = _page(0, PAGE1)
    page2 = respx.get(SEARCH_URL, params={"offset": "5"}).mock(return_value=httpx.Response(429))
    received: list[str] = []
    with pytest.raises(RetryExhaustedError):
        for record in adapter.fetch(NOW - timedelta(days=30), None):
            received.append(record.meta["cursor"])
    assert len(received) == 5 and page2.call_count == 5
    assert adapter.health().status.value == "failing"
    assert "429" in (adapter.health().message or "")
    cursor = received[-1]  # what the runner persists: last record processed
    assert decode_cursor(cursor) == {"from": "08/27/2026", "to": "09/26/2026", "offset": 5}

    # Next run resumes at offset 5 of the same window; page 0 is not fetched again.
    page2.mock(return_value=httpx.Response(200, json=PAGE2))
    resumed = list(adapter.fetch(NOW - timedelta(days=3), cursor))
    assert [r.external_id for r in resumed] == ["8e4b6f7a2d3c9b0f4a5d6e7f8091a2b3"]
    assert page1.call_count == 1
    assert adapter.health().status.value == "ok"


@respx.mock
def test_non_200_raises_sam_api_error(adapter: SamOpportunitiesAdapter) -> None:
    _robots()
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(403, json={"error": "bad key"}))
    with pytest.raises(SamApiError) as exc:
        list(adapter.fetch(NOW - timedelta(days=1), None))
    assert exc.value.status == 403


@respx.mock
def test_fetch_detail_by_notice_id(adapter: SamOpportunitiesAdapter) -> None:
    _robots()
    item = PAGE1["opportunitiesData"][2]
    route = respx.get(SEARCH_URL, params={"noticeid": item["noticeId"]}).mock(
        return_value=httpx.Response(
            200, json={"totalRecords": 1, "limit": 1, "offset": 0, "opportunitiesData": [item]}
        )
    )
    raw = adapter.fetch_detail(item["noticeId"])
    assert raw.external_id == item["noticeId"] and raw.payload == item
    assert route.call_count == 1
    respx.get(SEARCH_URL, params={"noticeid": "missing"}).mock(
        return_value=httpx.Response(200, json={"totalRecords": 0, "opportunitiesData": []})
    )
    with pytest.raises(LookupError):
        adapter.fetch_detail("missing")


def test_missing_api_key_is_degraded_and_fetches_nothing(clock: FakeClock) -> None:
    settings = Settings(_env_file=None, sam_api_key="")  # type: ignore[call-arg]
    adapter = SamOpportunitiesAdapter(settings=settings, now=lambda: NOW)
    assert list(adapter.fetch(NOW - timedelta(days=1), None)) == []
    health = adapter.health()
    assert health.status.value == "degraded" and "SAM_API_KEY" in (health.message or "")


def test_cursor_codec_rejects_garbage() -> None:
    assert decode_cursor(None) is None
    assert decode_cursor("not json") is None
    assert decode_cursor(json.dumps({"offset": 1})) is None
