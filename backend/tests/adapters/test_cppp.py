"""M3-02: CPPP adapter over respx (no network): two listing pages only, CAPTCHA pages
never requested, 1 req/s for *.gov.in, robots honoured, detail = manual."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx
from app.adapters.base import AdapterStatus, SourceAdapter
from app.adapters.cppp import CpppAdapter, CpppPortalError
from app.adapters.http import (
    MemoryArchiver,
    PoliteClient,
    RetryExhaustedError,
    RobotsDisallowedError,
    policy_table_from_settings,
)
from app.adapters.registry import get_adapter_class, load_builtin_adapters
from app.core.config import Settings
from app.core.normalize.cppp import LATEST_ACTIVE_URL, SEARCH_URL
from app.core.politeness import PolicyTable

FIXTURES = Path(__file__).parent / "fixtures" / "cppp"
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
SINCE = NOW - timedelta(days=30)
LATEST = (FIXTURES / "latest_active.html").read_text()
BYORG = (FIXTURES / "byorg.html").read_text()
ORG_LISTING = (FIXTURES / "org_listing.html").read_text()
BY_ORG_URL = "https://eprocure.gov.in/cppp/tendersbyorganisation"
ORG_RE = r"https://eprocure\.gov\.in/cppp/tendersbyorganisation/[A-Z]+"


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
def settings() -> Settings:
    return Settings(_env_file=None, cppp_max_orgs_per_run=2)  # type: ignore[call-arg]


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


def _client(
    settings: Settings, clock: FakeClock, policies: PolicyTable | None = None
) -> PoliteClient:
    return PoliteClient(
        settings=settings,
        archiver=MemoryArchiver(),
        clock=clock,
        sleep=clock.sleep,
        rng=lambda: 1.0,
        now=lambda: datetime.fromtimestamp(clock.t, tz=UTC),
        policies=policies or PolicyTable(default_rate=1e6),
    )


@pytest.fixture()
def adapter(settings: Settings, clock: FakeClock) -> CpppAdapter:
    return CpppAdapter(client=_client(settings, clock), settings=settings, now=lambda: NOW)


def _html(body: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, text=body, headers={"content-type": "text/html; charset=utf-8"})


def _robots(body: str | None = None) -> None:
    if body is None:
        respx.get("https://eprocure.gov.in/robots.txt").mock(return_value=httpx.Response(404))
    else:
        respx.get("https://eprocure.gov.in/robots.txt").mock(
            return_value=httpx.Response(200, text=body)
        )


def test_registered_with_spec_schedule_and_protocol() -> None:
    load_builtin_adapters()
    assert get_adapter_class("cppp") is CpppAdapter
    assert CpppAdapter.schedule == "0 */3 * * *" and CpppAdapter.region == "in"
    assert isinstance(CpppAdapter(), SourceAdapter)


def test_gov_in_hosts_are_throttled_to_one_request_per_second(settings: Settings) -> None:
    table = policy_table_from_settings(settings)
    assert (
        table.for_host("eprocure.gov.in").rate_per_sec == settings.http_gov_in_rate_per_sec == 1.0
    )


@respx.mock
def test_fetch_reads_only_the_two_listing_pages_and_follows_organisations(
    adapter: CpppAdapter,
) -> None:
    _robots()
    latest = respx.get(LATEST_ACTIVE_URL).mock(return_value=_html(LATEST))
    byorg = respx.get(BY_ORG_URL).mock(return_value=_html(BYORG))
    org = respx.get(url__regex=ORG_RE).mock(return_value=_html(ORG_LISTING))
    search = respx.route(url__startswith=SEARCH_URL).mock(return_value=_html("captcha"))

    records = list(adapter.fetch(SINCE, None))

    assert latest.call_count == 1 and byorg.call_count == 1
    assert org.call_count == 2  # cppp_max_orgs_per_run=2 of the 3 organisations with tenders
    assert search.call_count == 0, "the CAPTCHA search page must never be requested"
    ids = [r.external_id for r in records]
    assert len(ids) == len(set(ids)), "an organisation listing repeating a tender is deduped"
    assert "14156660" in ids  # HPCL branding tender (latest page)
    assert "14157001" in ids  # from the organisation listing
    assert "14150001" not in ids  # published 10-Aug, before `since`
    pages = {r.meta["page"] for r in records}
    assert pages == {"latest_active", "by_organisation"}
    assert all(r.raw_ref and r.raw_ref.startswith("raw/cppp/2026/09/28/") for r in records)
    assert all(r.content_type == "text/html" for r in records)
    assert adapter.health().status is AdapterStatus.OK

    opps = [adapter.normalize(r) for r in records]
    assert all(o.detail_status.value == "manual" for o in opps)
    assert all(o.extra["manual_detail_url"] == SEARCH_URL for o in opps)
    assert all(o.region.value == "in" and o.currency == "INR" for o in opps)
    corr = next(o for o in opps if o.external_id == "14157001")
    assert corr.notice_type.value == "corrigendum"
    assert corr.buyer_hierarchy[0] == "Ministry of Petroleum and Natural Gas"


@respx.mock
def test_by_organisation_page_missing_only_degrades(adapter: CpppAdapter) -> None:
    _robots()
    respx.get(LATEST_ACTIVE_URL).mock(return_value=_html(LATEST))
    respx.get(BY_ORG_URL).mock(return_value=_html("<h1>Oops! Page not found</h1>", status=404))
    records = list(adapter.fetch(SINCE, None))
    assert len(records) == 6
    health = adapter.health()
    assert health.status is AdapterStatus.DEGRADED
    assert health.message and "HTTP 404" in health.message


@respx.mock
def test_by_organisation_page_can_list_tenders_directly(adapter: CpppAdapter) -> None:
    _robots()
    respx.get(LATEST_ACTIVE_URL).mock(return_value=_html(LATEST))
    respx.get(BY_ORG_URL).mock(return_value=_html(ORG_LISTING))
    org = respx.get(url__regex=ORG_RE).mock(return_value=_html(ORG_LISTING))
    records = list(adapter.fetch(SINCE, None))
    assert org.call_count == 0
    assert {r.meta["page"] for r in records} == {"latest_active", "by_organisation"}
    assert adapter.health().status is AdapterStatus.OK


@respx.mock
def test_by_organisation_disabled_by_empty_setting(clock: FakeClock) -> None:
    settings = Settings(_env_file=None, cppp_by_org_url="")  # type: ignore[call-arg]
    adapter = CpppAdapter(client=_client(settings, clock), settings=settings, now=lambda: NOW)
    _robots()
    latest = respx.get(LATEST_ACTIVE_URL).mock(return_value=_html(LATEST))
    assert len(list(adapter.fetch(SINCE, None))) == 6 and latest.call_count == 1


@respx.mock
def test_primary_page_errors_mark_the_adapter_failing(adapter: CpppAdapter) -> None:
    _robots()
    respx.get(LATEST_ACTIVE_URL).mock(return_value=_html("maintenance", status=503))
    with pytest.raises(RetryExhaustedError):
        list(adapter.fetch(SINCE, None))
    assert adapter.health().status is AdapterStatus.FAILING
    respx.get(LATEST_ACTIVE_URL).mock(return_value=_html("forbidden", status=403))
    with pytest.raises(CpppPortalError, match="HTTP 403"):
        list(adapter.fetch(SINCE, None))


@respx.mock
def test_robots_disallow_stops_the_fetch_before_any_page_request(adapter: CpppAdapter) -> None:
    _robots("User-agent: *\nDisallow: /cppp/\n")
    latest = respx.get(LATEST_ACTIVE_URL).mock(return_value=_html(LATEST))
    with pytest.raises(RobotsDisallowedError):
        list(adapter.fetch(SINCE, None))
    assert latest.call_count == 0
    assert adapter.health().status is AdapterStatus.FAILING


@respx.mock
def test_since_filter_uses_the_published_date(adapter: CpppAdapter) -> None:
    _robots()
    respx.get(LATEST_ACTIVE_URL).mock(return_value=_html(LATEST))
    respx.get(BY_ORG_URL).mock(return_value=_html("", status=404))
    assert list(adapter.fetch(NOW + timedelta(days=1), None)) == []
    # e-published 26-Sep 19:33 / 20:42 IST (2 rows) and 27-Sep 09:00 / 10:00 IST (4 rows)
    assert len(list(adapter.fetch(datetime(2026, 9, 27, 4, 0, tzinfo=UTC), None))) == 1
    assert len(list(adapter.fetch(datetime(2026, 9, 27, 3, 0, tzinfo=UTC), None))) == 4
    assert len(list(adapter.fetch(datetime(2026, 9, 26, 12, 0, tzinfo=UTC), None))) == 6


def test_fetch_detail_and_documents_never_touch_the_network(adapter: CpppAdapter) -> None:
    with respx.mock(assert_all_called=False) as router:
        router.route().mock(side_effect=AssertionError("no request expected"))
        raw = adapter.fetch_detail("14156660")
        assert raw.payload == {"detail_status": "manual", "manual_detail_url": SEARCH_URL}
        assert raw.meta == {"captcha": True}
        assert adapter.fetch_documents(raw) == []


@respx.mock
def test_polite_client_throttles_gov_in_at_one_per_second(
    settings: Settings, clock: FakeClock
) -> None:
    client = _client(settings, clock, policies=policy_table_from_settings(settings))
    adapter = CpppAdapter(client=client, settings=settings, now=lambda: NOW, max_orgs=1)
    _robots()
    respx.get(LATEST_ACTIVE_URL).mock(return_value=_html(LATEST))
    respx.get(BY_ORG_URL).mock(return_value=_html(BYORG))
    respx.get(url__regex=ORG_RE).mock(return_value=_html(ORG_LISTING))
    list(adapter.fetch(SINCE, None))
    # robots + latest + byorg + one org listing = 4 requests, so 3 one-second waits
    assert len(clock.sleeps) == 3 and all(abs(s - 1.0) < 0.01 for s in clock.sleeps)
