"""M2-06: Grants.gov adapter over respx (no network)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx
from app.adapters.grants_gov import (
    FETCH_URL,
    SEARCH_URL,
    GrantsApiError,
    GrantsGovAdapter,
    decode_cursor,
)
from app.adapters.http import MemoryArchiver, PoliteClient
from app.adapters.registry import get_adapter_class
from app.core.config import Settings
from app.core.opportunity import NoticeType
from app.core.politeness import PolicyTable

FIXTURES = Path(__file__).parent / "fixtures" / "grants_gov"
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
PAGE1 = json.loads((FIXTURES / "search2_page1.json").read_text())
PAGE2 = json.loads((FIXTURES / "search2_page2.json").read_text())
DETAIL = json.loads((FIXTURES / "fetch_332894.json").read_text())
FORECAST = json.loads((FIXTURES / "fetch_forecast_334905.json").read_text())
ERROR = json.loads((FIXTURES / "fetch_error.json").read_text())


@pytest.fixture()
def adapter() -> GrantsGovAdapter:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    client = PoliteClient(
        settings=settings,
        archiver=MemoryArchiver(),
        clock=lambda: NOW.timestamp(),
        sleep=lambda s: None,
        policies=PolicyTable(default_rate=1e6),
        now=lambda: NOW,
    )
    return GrantsGovAdapter(client=client, settings=settings, now=lambda: NOW, page_size=5)


def _robots() -> None:
    respx.get("https://api.grants.gov/robots.txt").mock(return_value=httpx.Response(404))


def _generic_detail(opportunity_id: int) -> httpx.Response:
    """A minimal fetchOpportunity body for hits without a recorded detail."""
    body = json.loads(json.dumps(DETAIL))
    body["data"]["id"] = opportunity_id
    body["data"]["opportunityNumber"] = f"GEN-{opportunity_id}"
    return httpx.Response(200, json=body)


def _mock_details() -> respx.Route:
    def responder(request: httpx.Request) -> httpx.Response:
        opp_id = json.loads(request.content)["opportunityId"]
        if opp_id == 332894:
            return httpx.Response(200, json=DETAIL)
        if opp_id == 334905:
            return httpx.Response(200, json=FORECAST)
        return _generic_detail(opp_id)

    return respx.post(FETCH_URL).mock(side_effect=responder)


def _mock_search() -> respx.Route:
    def responder(request: httpx.Request) -> httpx.Response:
        start = json.loads(request.content)["startRecordNum"]
        return httpx.Response(200, json=PAGE1 if start == 0 else PAGE2)

    return respx.post(SEARCH_URL).mock(side_effect=responder)


def test_registered_every_two_hours() -> None:
    assert get_adapter_class("grants_gov") is GrantsGovAdapter
    assert GrantsGovAdapter.schedule == "0 */2 * * *"


@respx.mock
def test_search2_paginates_posted_and_forecasted_and_keeps_latest(
    adapter: GrantsGovAdapter,
) -> None:
    _robots()
    search = _mock_search()
    details = _mock_details()
    records = list(adapter.fetch(NOW - timedelta(days=10), None))
    assert search.call_count == 2
    bodies = [json.loads(c.request.content) for c in search.calls]
    assert bodies[0] == {
        "rows": 5,
        "startRecordNum": 0,
        "oppStatuses": "forecasted|posted",
        "sortBy": "openDate|desc",
        "dateRange": "14",
    }
    assert bodies[1]["startRecordNum"] == 5
    assert search.calls[0].request.headers["content-type"] == "application/json"
    # 8 hits, one duplicate number (forecast 999001 of W911NF21S0009) dropped -> 7 records
    assert len(records) == 7
    assert "999001" not in {r.external_id for r in records}
    assert details.call_count == 7
    assert all(r.raw_ref is not None for r in records)
    ids = [r.external_id for r in records]
    assert ids[0] == "332894" and "334905" in ids
    opps = [adapter.normalize(r) for r in records]
    by_id = {o.external_id: o for o in opps}
    assert by_id["332894"].notice_type is NoticeType.GRANT
    assert by_id["332894"].detail_status.value == "full"
    assert len(by_id["332894"].eligibility["applicant_types"]) == 8
    assert by_id["334905"].notice_type is NoticeType.FORECAST
    assert by_id["334905"].estimated_value_max is not None
    assert adapter.health().status.value == "ok"
    # cursor bookkeeping: page start, last record points past its page
    assert decode_cursor(records[0].meta["cursor"]) == {"start": 0, "dateRange": "14"}
    assert decode_cursor(records[-1].meta["cursor"])["start"] == 10  # type: ignore[index]


@respx.mock
def test_old_watermark_lists_everything_without_date_range(adapter: GrantsGovAdapter) -> None:
    _robots()
    search = _mock_search()
    _mock_details()
    adapter.with_details = False
    records = list(adapter.fetch(NOW - timedelta(days=120), None))
    assert "dateRange" not in json.loads(search.calls[0].request.content)
    assert len(records) == 7 and all(r.raw_ref is None for r in records)
    opp = adapter.normalize(records[0])
    assert opp.detail_status.value == "pending" and opp.aln == ["12.431"]


@respx.mock
def test_resume_from_cursor(adapter: GrantsGovAdapter) -> None:
    _robots()
    search = _mock_search()
    _mock_details()
    cursor = json.dumps({"start": 5, "dateRange": "7"})
    records = list(adapter.fetch(NOW - timedelta(days=1), cursor))
    assert search.call_count == 1
    body = json.loads(search.calls[0].request.content)
    assert body["startRecordNum"] == 5 and body["dateRange"] == "7"
    assert len(records) == 3  # page 2 only, no dedupe partner on this page


@respx.mock
def test_fetch_detail_and_documents(adapter: GrantsGovAdapter) -> None:
    _robots()
    _mock_details()
    raw = adapter.fetch_detail("332894")
    assert raw.external_id == "332894" and raw.raw_ref is not None
    docs = adapter.fetch_documents(raw)
    assert docs[0].file_name == "LQC BAA Final W911NF21S0009.pdf"
    assert docs[0].url == "https://apply07.grants.gov/grantsws/rest/opportunity/att/download/306813"
    opp = adapter.normalize(raw)
    assert opp.title == "LPS Qubit Collaboratory (LQC)" and "_raw_ref" not in opp.extra


@respx.mock
def test_api_errors(adapter: GrantsGovAdapter) -> None:
    _robots()
    respx.post(FETCH_URL).mock(return_value=httpx.Response(200, json=ERROR))
    with pytest.raises(GrantsApiError, match="Opportunity not found"):
        adapter.fetch_detail("1")
    respx.post(SEARCH_URL).mock(return_value=httpx.Response(500))
    with pytest.raises(Exception):  # noqa: B017 - RetryExhaustedError surfaces from the client
        list(adapter.fetch(NOW - timedelta(days=1), None))
    assert adapter.health().status.value == "failing"
    respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, text="<html>maintenance</html>"))
    with pytest.raises(GrantsApiError, match="not JSON"):
        list(adapter.fetch(NOW - timedelta(days=1), None))
