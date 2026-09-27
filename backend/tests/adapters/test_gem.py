"""M3-03: GeM adapter over respx (no network): public listing pages, no login, bid PDF
downloaded and hashed, gem_bid / reverse_auction, every 3 h."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from app.adapters.base import AdapterStatus, SourceAdapter
from app.adapters.gem import GemAdapter, GemApiError, decode_cursor, listing_payload
from app.adapters.http import MemoryArchiver, PoliteClient, RetryExhaustedError
from app.adapters.registry import get_adapter_class, load_builtin_adapters
from app.core.config import Settings
from app.core.paths import safe_segment
from app.core.politeness import PolicyTable

FIXTURES = Path(__file__).parent / "fixtures" / "gem"
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
SINCE = NOW - timedelta(days=30)
PAGE1 = json.loads((FIXTURES / "all_bids_page1.json").read_text())
PAGE2 = json.loads((FIXTURES / "all_bids_page2.json").read_text())
PDF = (FIXTURES / "GEM-2026-B-1234567.pdf").read_bytes()
LISTING_URL = "https://bidplus.gem.gov.in/all-bids-data"
PDF_URL = "https://bidplus.gem.gov.in/showbidDocument/7891234"


@pytest.fixture()
def settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.fixture()
def adapter(settings: Settings) -> GemAdapter:
    client = PoliteClient(
        settings=settings,
        archiver=MemoryArchiver(),
        clock=lambda: NOW.timestamp(),
        sleep=lambda s: None,
        rng=lambda: 1.0,
        now=lambda: NOW,
        policies=PolicyTable(default_rate=1e6),
    )
    return GemAdapter(client=client, settings=settings, now=lambda: NOW)


def _robots() -> None:
    respx.get("https://bidplus.gem.gov.in/robots.txt").mock(return_value=httpx.Response(404))


def _pages(*bodies: dict[str, Any]) -> respx.Route:
    by_page = dict(enumerate(bodies, start=1))

    def respond(request: httpx.Request) -> httpx.Response:
        form = dict(httpx.QueryParams(request.content.decode()))
        payload = json.loads(form["payload"])
        assert payload["filter"]["bidStatusType"] == "ongoing_bids"
        assert payload["param"] == {"searchBid": "", "searchType": "fullText"}
        body = by_page.get(payload["page"])
        if body is None:  # past the end Solr answers an empty page, not an error
            body = {"response": {"response": {"numFound": 0, "start": 0, "docs": []}}}
        return httpx.Response(200, json=body)

    return respx.post(LISTING_URL).mock(side_effect=respond)


def test_registered_with_spec_schedule_and_protocol() -> None:
    load_builtin_adapters()
    assert get_adapter_class("gem") is GemAdapter
    assert GemAdapter.schedule == "0 */3 * * *" and GemAdapter.region == "in"
    assert isinstance(GemAdapter(), SourceAdapter)
    assert decode_cursor(json.dumps({"page": 3})) == 3 and decode_cursor("junk") is None
    assert listing_payload(2)["page"] == 2


@respx.mock
def test_fetch_pages_the_public_listing_without_login(adapter: GemAdapter) -> None:
    _robots()
    route = _pages(PAGE1, PAGE2)
    records = list(adapter.fetch(SINCE, None))
    assert route.call_count == 2  # numFound 5 reached after page 2
    assert [r.external_id for r in records] == [
        "GEM/2026/B/1234567",
        "GEM/2026/B/1240022",
        "GEM/2026/B/1250910",
        "GEM/2026/B/1261144",
        "GEM/2026/R/1270031",
    ]
    assert records[0].meta["cursor"] == json.dumps({"page": 1})
    assert records[-1].meta == {"cursor": json.dumps({"page": 2}), "page": 2, "total": 5}
    assert all(r.raw_ref and r.raw_ref.startswith("raw/gem/2026/09/26/all-bids") for r in records)
    sent = route.calls[0].request
    assert sent.method == "POST" and "Cookie" not in sent.headers
    assert sent.headers["X-Requested-With"] == "XMLHttpRequest"
    assert adapter.health().status is AdapterStatus.OK

    opps = [adapter.normalize(r) for r in records]
    assert [o.notice_type.value for o in opps] == [
        "gem_bid",
        "reverse_auction",
        "gem_bid",
        "gem_bid",
        "reverse_auction",
    ]
    first = opps[0]
    assert (
        first.buyer_org == "Ministry of Railways" and first.buyer_sub_org == "South Central Railway"
    )
    assert first.response_due_at == datetime(2026, 10, 17, 14, 53, tzinfo=UTC)
    assert first.source_tz == "Asia/Kolkata"
    assert first.source_url == PDF_URL
    assert first.extra["gem_seller_registration_url"].startswith("https://gem.gov.in/")


@respx.mock
def test_since_filter_and_cursor_resume(adapter: GemAdapter) -> None:
    _robots()
    _pages(PAGE1, PAGE2)
    recent = list(adapter.fetch(datetime(2026, 9, 23, tzinfo=UTC), None))
    assert [r.external_id for r in recent] == ["GEM/2026/B/1261144", "GEM/2026/R/1270031"]
    resumed = list(adapter.fetch(SINCE, json.dumps({"page": 2})))
    assert [r.external_id for r in resumed] == ["GEM/2026/B/1261144", "GEM/2026/R/1270031"]


@respx.mock
def test_max_pages_bounds_the_crawl(settings: Settings) -> None:
    _robots()
    client = PoliteClient(
        settings=settings, archiver=MemoryArchiver(), policies=PolicyTable(default_rate=1e6)
    )
    adapter = GemAdapter(client=client, settings=settings, now=lambda: NOW, max_pages=1)
    route = _pages(PAGE1, PAGE2)
    assert len(list(adapter.fetch(SINCE, None))) == 3 and route.call_count == 1


@respx.mock
def test_fetch_documents_downloads_and_hashes_the_bid_pdf(adapter: GemAdapter) -> None:
    _robots()
    _pages(PAGE1, PAGE2)
    pdf = respx.get(PDF_URL).mock(
        return_value=httpx.Response(200, content=PDF, headers={"content-type": "application/pdf"})
    )
    raw = next(iter(adapter.fetch(SINCE, None)))
    docs = adapter.fetch_documents(raw)
    assert pdf.call_count == 1
    assert len(docs) == 1
    doc = docs[0]
    assert doc.url == PDF_URL and doc.file_name == "GEM-2026-B-1234567.pdf"
    assert doc.sha256 == hashlib.sha256(PDF).hexdigest() and doc.size == len(PDF)
    assert doc.mime_type == "application/pdf"
    archiver: MemoryArchiver = adapter.client.archiver  # type: ignore[assignment]
    archived = [k for k in archiver.objects if "document" in k]
    assert archived and archived[0].startswith(
        f"raw/gem/2026/09/26/{safe_segment('GEM/2026/B/1234567/document')}/"
    )


@respx.mock
def test_fetch_documents_without_download_and_on_failure(adapter: GemAdapter) -> None:
    _robots()
    _pages(PAGE1, PAGE2)
    raw = next(iter(adapter.fetch(SINCE, None)))
    adapter.download_documents = False
    (ref,) = adapter.fetch_documents(raw)
    assert ref.sha256 is None and ref.url == PDF_URL
    adapter.download_documents = True
    respx.get(PDF_URL).mock(return_value=httpx.Response(404))
    (ref,) = adapter.fetch_documents(raw)
    assert ref.sha256 is None  # the reference survives, the pipeline retries later


@respx.mock
def test_fetch_detail_searches_the_listing_by_bid_number(adapter: GemAdapter) -> None:
    _robots()

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(dict(httpx.QueryParams(request.content.decode()))["payload"])
        assert payload["param"]["searchBid"].startswith("GEM/2026/B/")
        return httpx.Response(200, json=PAGE1)

    respx.post(LISTING_URL).mock(side_effect=respond)
    raw = adapter.fetch_detail("GEM/2026/B/1240022")
    assert raw.external_id == "GEM/2026/B/1240022"
    assert adapter.normalize(raw).notice_type.value == "reverse_auction"
    with pytest.raises(LookupError):
        adapter.fetch_detail("GEM/2026/B/0000000")


@respx.mock
def test_errors_mark_the_adapter_failing(adapter: GemAdapter) -> None:
    _robots()
    respx.post(LISTING_URL).mock(return_value=httpx.Response(403, text="forbidden"))
    with pytest.raises(GemApiError, match="HTTP 403"):
        list(adapter.fetch(SINCE, None))
    assert adapter.health().status is AdapterStatus.FAILING
    respx.post(LISTING_URL).mock(return_value=httpx.Response(200, text="<html>maintenance</html>"))
    with pytest.raises(GemApiError, match="not JSON"):
        list(adapter.fetch(SINCE, None))
    respx.post(LISTING_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(RetryExhaustedError):
        list(adapter.fetch(SINCE, None))


@respx.mock
def test_malformed_and_layout_change_pages_degrade(adapter: GemAdapter) -> None:
    _robots()
    _pages(json.loads((FIXTURES / "malformed.json").read_text()))
    records = list(adapter.fetch(SINCE, None))
    assert [r.external_id for r in records] == ["GEM/2026/B/1234567"]
    health = adapter.health()
    assert health.status is AdapterStatus.DEGRADED and health.message
    assert "without a bid number" in health.message and "numFound" in health.message
    _pages(json.loads((FIXTURES / "layout_change.json").read_text()))
    assert list(adapter.fetch(SINCE, None)) == []
    assert "docs" in (adapter.health().message or "")
