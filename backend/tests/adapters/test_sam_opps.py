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
    respx.get("https://sam.gov/robots.txt").mock(return_value=httpx.Response(404))
    respx.head(url__startswith="https://sam.gov/api/").mock(return_value=httpx.Response(405))
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


DESCRIPTION = json.loads((FIXTURES / "description_3f9c1a2b.json").read_text())
NOT_FOUND = json.loads((FIXTURES / "description_not_found.json").read_text())
DESC_URL = "https://api.sam.gov/prod/opportunities/v1/noticedesc"


@respx.mock
def test_fetch_detail_pulls_description_text(adapter: SamOpportunitiesAdapter) -> None:
    _robots()
    item = PAGE1["opportunitiesData"][0]
    respx.get(SEARCH_URL, params={"noticeid": item["noticeId"]}).mock(
        return_value=httpx.Response(
            200, json={"totalRecords": 1, "limit": 1, "offset": 0, "opportunitiesData": [item]}
        )
    )
    desc = respx.get(DESC_URL, params={"noticeid": item["noticeId"]}).mock(
        return_value=httpx.Response(200, json=DESCRIPTION)
    )
    raw = adapter.fetch_detail(item["noticeId"])
    assert desc.call_count == 1
    assert dict(desc.calls.last.request.url.params)["api_key"] == "test-key"
    text = raw.meta["description_text"]
    assert text.startswith("Enterprise Cloud Migration and Managed Services")
    assert "- Anticipated NAICS: 541512" in text
    assert "alert(" not in text and "&ndash;" not in text and "\xa0" not in text
    assert raw.meta["description_raw_ref"] is not None
    assert raw.meta["description_raw_ref"] != raw.raw_ref
    opp = adapter.normalize(raw)
    assert opp.description_text == text
    assert opp.detail_status.value == "full"
    # the rest of the detail mapping (SPEC 5.2/5.3)
    assert (opp.buyer_org, opp.buyer_sub_org, opp.buyer_office) == (
        "DEPT OF DEFENSE",
        "DEPT OF THE ARMY",
        "ACC-APG RTP DIV",
    )
    assert len(opp.buyer_hierarchy) == 6
    assert [c.email for c in opp.contacts] == [
        "contracting.officer@army.mil",
        "specialist@army.mil",
    ]
    assert opp.set_aside == "SBA" and opp.naics == ["541512"] and opp.psc == ["DA01"]
    assert opp.place_of_performance is not None
    assert (opp.place_of_performance.city, opp.place_of_performance.state) == ("Durham", "NC")
    assert opp.response_due_at == datetime(2026, 10, 20, 18, 0, tzinfo=UTC)
    assert opp.source_tz == "America/New_York"


@respx.mock
def test_fetch_detail_without_description(adapter: SamOpportunitiesAdapter) -> None:
    _robots()
    item = PAGE1["opportunitiesData"][2]
    route = respx.get(SEARCH_URL, params={"noticeid": item["noticeId"]}).mock(
        return_value=httpx.Response(
            200, json={"totalRecords": 1, "limit": 1, "offset": 0, "opportunitiesData": [item]}
        )
    )
    respx.get(DESC_URL, params={"noticeid": item["noticeId"]}).mock(
        return_value=httpx.Response(200, json=NOT_FOUND)
    )
    raw = adapter.fetch_detail(item["noticeId"])
    assert raw.external_id == item["noticeId"] and raw.payload == item
    assert route.call_count == 1
    assert raw.meta["description_text"] is None
    assert adapter.normalize(raw).detail_status.value == "pending"
    # description endpoint erroring is not fatal either
    item2 = PAGE1["opportunitiesData"][3]
    respx.get(SEARCH_URL, params={"noticeid": item2["noticeId"]}).mock(
        return_value=httpx.Response(200, json={"totalRecords": 1, "opportunitiesData": [item2]})
    )
    respx.get(DESC_URL, params={"noticeid": item2["noticeId"]}).mock(
        return_value=httpx.Response(404, json={"error": "no"})
    )
    raw2 = adapter.fetch_detail(item2["noticeId"])
    assert "description_text" not in raw2.meta
    # page-2 record has description "null": no request is made at all
    item3 = PAGE2["opportunitiesData"][0]
    respx.get(SEARCH_URL, params={"noticeid": item3["noticeId"]}).mock(
        return_value=httpx.Response(200, json={"totalRecords": 1, "opportunitiesData": [item3]})
    )
    assert "description_text" not in adapter.fetch_detail(item3["noticeId"]).meta


@respx.mock
def test_fetch_documents_infers_names_from_headers_or_url(
    adapter: SamOpportunitiesAdapter,
) -> None:
    _robots()
    respx.get("https://sam.gov/robots.txt").mock(return_value=httpx.Response(404))
    item = PAGE1["opportunitiesData"][1]
    urls = item["resourceLinks"]
    respx.head(urls[0]).mock(
        return_value=httpx.Response(
            200,
            headers={
                "Content-Disposition": 'attachment; filename="PWS_Cloud_Migration.pdf"',
                "Content-Type": "application/pdf",
                "Content-Length": "482113",
            },
        )
    )
    respx.head(urls[1]).mock(
        return_value=httpx.Response(
            200,
            headers={
                "Content-Disposition": "attachment; filename*=UTF-8''Q%26A%20Log.xlsx",
                "Content-Type": (
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    "; charset=utf-8"
                ),
            },
        )
    )
    respx.head(urls[2]).mock(return_value=httpx.Response(405))
    raw = next(iter([r for r in _records_from(adapter) if r.external_id == item["noticeId"]]))
    docs = adapter.fetch_documents(raw)
    assert [d.url for d in docs] == urls
    assert docs[0].file_name == "PWS_Cloud_Migration.pdf"
    assert docs[0].mime_type == "application/pdf" and docs[0].size == 482113
    assert docs[1].file_name == "Q&A Log.xlsx"
    assert docs[1].mime_type.startswith("application/vnd.openxmlformats")  # type: ignore[union-attr]
    assert docs[2].file_name == "2c3d4e5f60718293a4b5c6d7e8f9a0b1"  # URL fallback
    assert docs[2].size is None
    assert all(d.kind.value == "attachment" for d in docs)
    # probing can be switched off (cheap listing-only mode)
    adapter.probe_document_headers = False
    assert [d.file_name for d in adapter.fetch_documents(raw)] == [
        "0a1b2c3d4e5f60718293a4b5c6d7e8f9",
        "1b2c3d4e5f60718293a4b5c6d7e8f9a0",
        "2c3d4e5f60718293a4b5c6d7e8f9a0b1",
    ]


def _records_from(adapter: SamOpportunitiesAdapter) -> list:  # type: ignore[type-arg]
    _page(0, PAGE1)
    _page(5, PAGE2)
    return list(adapter.fetch(NOW - timedelta(days=30), None))
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
