"""M2-08: SAM.gov awards adapter over respx (no network)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx
from app.adapters.http import MemoryArchiver, PoliteClient
from app.adapters.registry import get_adapter_class
from app.adapters.sam_awards import SamAwardsAdapter, SamAwardsApiError
from app.core.config import Settings
from app.core.politeness import PolicyTable

FIXTURES = Path(__file__).parent / "fixtures" / "sam_awards"
PAGE1 = json.loads((FIXTURES / "page1.json").read_text())
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
URL = "https://api.sam.gov/contract-awards/v1/search"


def make_adapter(**overrides: object) -> SamAwardsAdapter:
    settings = Settings(_env_file=None, sam_api_key="test-key", sam_awards_naics="541512,541519")  # type: ignore[call-arg]
    client = PoliteClient(
        settings=settings,
        archiver=MemoryArchiver(),
        clock=lambda: NOW.timestamp(),
        sleep=lambda s: None,
        policies=PolicyTable(default_rate=1e6),
        now=lambda: NOW,
    )
    return SamAwardsAdapter(client=client, settings=settings, now=lambda: NOW, **overrides)  # type: ignore[arg-type]


def _robots() -> None:
    respx.get("https://api.sam.gov/robots.txt").mock(return_value=httpx.Response(404))


def test_registered_daily_and_naics_from_settings() -> None:
    assert get_adapter_class("sam_awards") is SamAwardsAdapter
    assert SamAwardsAdapter.schedule == "0 4 * * *"
    assert make_adapter().naics_codes == ["541512", "541519"]
    assert make_adapter(naics_codes=["236220"]).naics_codes == ["236220"]


@respx.mock
def test_fetch_by_naics_list_with_api_key_and_offset_pagination() -> None:
    _robots()
    page1 = json.loads(json.dumps(PAGE1))
    page1["totalRecords"] = 6
    page1["limit"] = 4
    page2 = {"totalRecords": 6, "limit": 4, "offset": 4, "awardsData": PAGE1["awardsData"][:2]}

    def responder(request: httpx.Request) -> httpx.Response:
        offset = request.url.params["offset"]
        return httpx.Response(200, json=page1 if offset == "0" else page2)

    route = respx.get(URL).mock(side_effect=responder)
    adapter = make_adapter(page_size=4)
    records = list(adapter.fetch(NOW - timedelta(days=25), None))
    assert route.call_count == 2 and len(records) == 6
    params = dict(route.calls[0].request.url.params)
    assert params["api_key"] == "test-key" and params["naics"] == "541512,541519"
    assert params["signedDateFrom"] == "09/01/2026" and params["signedDateTo"] == "09/26/2026"
    assert params["limit"] == "4" and params["offset"] == "0"
    assert dict(route.calls[1].request.url.params)["offset"] == "4"
    assert records[0].meta["award"].piid == "W911NF20C0007"
    assert json.loads(records[3].meta["cursor"]) == {"offset": 4}
    assert all(r.raw_ref is not None for r in records)
    assert adapter.normalize(records[0]).notice_type.value == "award"
    assert adapter.fetch_documents(records[0]) == []
    assert adapter.health().status.value == "ok"


@respx.mock
def test_windows_capped_at_one_year_and_cursor_resume() -> None:
    _robots()
    route = respx.get(URL).mock(
        return_value=httpx.Response(200, json={"totalRecords": 0, "awardsData": []})
    )
    adapter = make_adapter()
    assert list(adapter.fetch(NOW - timedelta(days=500), None)) == []
    assert route.call_count == 2
    spans = [
        (dict(c.request.url.params)["signedDateFrom"], dict(c.request.url.params)["signedDateTo"])
        for c in route.calls
    ]
    assert spans[0][1] == spans[1][0]
    route.reset()
    list(adapter.fetch(NOW - timedelta(days=5), json.dumps({"offset": 300})))
    assert dict(route.calls[0].request.url.params)["offset"] == "300"


def test_missing_key_or_naics_is_degraded_and_fetches_nothing() -> None:
    no_key = SamAwardsAdapter(settings=Settings(_env_file=None, sam_api_key=""), now=lambda: NOW)  # type: ignore[call-arg]
    assert list(no_key.fetch(NOW - timedelta(days=1), None)) == []
    assert no_key.health().status.value == "degraded"
    assert "SAM_API_KEY" in (no_key.health().message or "")
    no_naics = SamAwardsAdapter(settings=Settings(_env_file=None, sam_api_key="k"), now=lambda: NOW)  # type: ignore[call-arg]
    assert list(no_naics.fetch(NOW - timedelta(days=1), None)) == []
    assert "NAICS" in (no_naics.health().message or "")


@respx.mock
def test_api_error_marks_failing() -> None:
    _robots()
    respx.get(URL).mock(return_value=httpx.Response(403, json={"error": "invalid key"}))
    adapter = make_adapter()
    with pytest.raises(SamAwardsApiError):
        list(adapter.fetch(NOW - timedelta(days=1), None))
    assert adapter.health().status.value == "failing"
    with pytest.raises(NotImplementedError):
        adapter.fetch_detail("x")


def test_alternate_result_keys_are_accepted() -> None:
    assert SamAwardsAdapter._rows({"results": [{"a": 1}, "junk"]}) == [{"a": 1}]
    assert SamAwardsAdapter._rows({"contractAwards": []}) == []
    assert SamAwardsAdapter._rows({"nothing": 1}) == []
