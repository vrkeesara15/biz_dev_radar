"""M2-07: USAspending adapter over respx (no network)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx
from app.adapters.http import MemoryArchiver, PoliteClient
from app.adapters.registry import get_adapter_class
from app.adapters.usaspending import SEARCH_URL, UsaSpendingAdapter, UsaSpendingApiError
from app.core.config import Settings
from app.core.politeness import PolicyTable

FIXTURES = Path(__file__).parent / "fixtures" / "usaspending"
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
PAGE1 = json.loads((FIXTURES / "spending_by_award_page1.json").read_text())
PAGE2 = json.loads((FIXTURES / "spending_by_award_page2.json").read_text())


def make_adapter(**kwargs: object) -> UsaSpendingAdapter:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    client = PoliteClient(
        settings=settings,
        archiver=MemoryArchiver(),
        clock=lambda: NOW.timestamp(),
        sleep=lambda s: None,
        policies=PolicyTable(default_rate=1e6),
        now=lambda: NOW,
    )
    return UsaSpendingAdapter(
        client=client, settings=settings, now=lambda: NOW, page_size=6, **kwargs
    )  # type: ignore[arg-type]


def _robots() -> None:
    respx.get("https://api.usaspending.gov/robots.txt").mock(return_value=httpx.Response(404))


def _mock_pages(always_next: bool = False) -> respx.Route:
    def responder(request: httpx.Request) -> httpx.Response:
        page = json.loads(request.content)["page"]
        body = json.loads(json.dumps(PAGE1 if page == 1 else PAGE2))
        if always_next:
            body["page_metadata"]["hasNext"] = True
            body["page_metadata"]["page"] = page
        return httpx.Response(200, json=body)

    return respx.post(SEARCH_URL).mock(side_effect=responder)


def test_registered_weekly_with_dod_lag_note() -> None:
    assert get_adapter_class("usaspending") is UsaSpendingAdapter
    assert UsaSpendingAdapter.schedule.split()[-1] == "1"  # weekly on Mondays
    health = make_adapter().health()
    assert health.status.value == "ok" and "DoD" in (health.message or "")
    assert "90 days" in (health.message or "")


@respx.mock
def test_fetch_pages_until_has_next_false_with_three_fy_window() -> None:
    _robots()
    route = _mock_pages()
    adapter = make_adapter()
    records = list(adapter.fetch(NOW - timedelta(days=3000), None))
    assert route.call_count == 2
    bodies = [json.loads(c.request.content) for c in route.calls]
    assert bodies[0]["page"] == 1 and bodies[1]["page"] == 2
    assert bodies[0]["limit"] == 6
    assert bodies[0]["filters"]["time_period"] == [
        {"start_date": "2023-10-01", "end_date": "2026-09-26"}
    ]
    assert bodies[0]["filters"]["award_type_codes"] == ["A", "B", "C", "D"]
    assert len(records) == len(PAGE1["results"]) + len(PAGE2["results"])
    assert records[0].external_id == PAGE1["results"][0]["generated_internal_id"]
    assert records[0].meta["award"].award_id == PAGE1["results"][0]["Award ID"]
    assert json.loads(records[0].meta["cursor"]) == {"page": 1}
    assert json.loads(records[5].meta["cursor"]) == {"page": 2}
    assert all(r.raw_ref is not None for r in records)
    opp = adapter.normalize(records[0])
    assert opp.notice_type.value == "award" and opp.source_id == "usaspending"
    assert adapter.fetch_documents(records[0]) == []
    assert adapter.health().status.value == "ok"


@respx.mock
def test_later_since_narrows_the_window_and_default_page_size_is_100() -> None:
    _robots()
    route = _mock_pages()
    adapter = make_adapter()
    list(adapter.fetch(NOW - timedelta(days=10), None))
    body = json.loads(route.calls[0].request.content)
    assert body["filters"]["time_period"][0]["start_date"] == "2026-09-16"
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert UsaSpendingAdapter(settings=settings).page_size == 100
    assert UsaSpendingAdapter(settings=settings, page_size=500).page_size == 100


@respx.mock
def test_stops_at_result_cap_even_when_has_next() -> None:
    _robots()
    route = _mock_pages(always_next=True)
    adapter = make_adapter(max_results=12)  # 12 / 6 per page = 2 pages
    records = list(adapter.fetch(NOW - timedelta(days=30), None))
    assert route.call_count == 2 and len(records) == 10
    # the real cap is 50,000 results
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert UsaSpendingAdapter(settings=settings).max_results == 50_000


@respx.mock
def test_resume_from_cursor_and_errors() -> None:
    _robots()
    route = _mock_pages()
    adapter = make_adapter()
    records = list(adapter.fetch(NOW - timedelta(days=30), json.dumps({"page": 2})))
    assert route.call_count == 1 and len(records) == len(PAGE2["results"])
    respx.post(SEARCH_URL).mock(return_value=httpx.Response(422, json={"detail": "bad filter"}))
    with pytest.raises(UsaSpendingApiError) as exc:
        list(adapter.fetch(NOW - timedelta(days=30), None))
    assert exc.value.status == 422
    health = adapter.health()
    assert health.status.value == "failing" and "DoD" in (health.message or "")
    with pytest.raises(NotImplementedError):
        adapter.fetch_detail("x")
