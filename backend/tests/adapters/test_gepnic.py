"""M3-05: one GePNICAdapter class configured from gepnic_configs.yaml; TN/UP/central
enabled, MH robots-disallowed and TS not-implemented registered but never fetched."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx
import yaml
from app.adapters import registry
from app.adapters.base import AdapterStatus, SourceAdapter
from app.adapters.gepnic import (
    CONFIG_PATH,
    PORTAL_ADAPTERS,
    PORTAL_CONFIGS,
    GePNICAdapter,
    GePNICPortalError,
    adapter_class,
    load_portal_configs,
)
from app.adapters.http import MemoryArchiver, PoliteClient, RobotsDisallowedError
from app.adapters.registry import load_builtin_adapters
from app.celery_app import build_beat_schedule
from app.core.config import Settings
from app.core.normalize.gepnic import PortalConfig
from app.core.politeness import PolicyTable

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
SINCE = NOW - timedelta(days=30)
ENABLED = ("gepnic_tn", "gepnic_up", "gepnic_central")
DISABLED = {"gepnic_mh": "robots_disallowed", "gepnic_ts": "not_implemented"}


def _client(settings: Settings) -> PoliteClient:
    return PoliteClient(
        settings=settings,
        archiver=MemoryArchiver(),
        clock=lambda: NOW.timestamp(),
        sleep=lambda s: None,
        rng=lambda: 1.0,
        now=lambda: NOW,
        policies=PolicyTable(default_rate=1e6),
    )


@pytest.fixture()
def settings() -> Settings:
    return Settings(_env_file=None, gepnic_max_orgs_per_run=2)  # type: ignore[call-arg]


def _html(body: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, text=body, headers={"content-type": "text/html; charset=utf-8"})


def _serve(portal: PortalConfig, *, home: str = "home.html") -> dict[str, respx.Route]:
    host = httpx.URL(portal.base_url).host
    fx = FIXTURES / portal.source_id
    return {
        "robots": respx.get(f"https://{host}/robots.txt").mock(return_value=httpx.Response(404)),
        # url__eq: the front page is the bare base_url, so a prefix match would also
        # swallow the ?page=... pages below
        "home": respx.get(url__eq=portal.home_url).mock(
            return_value=_html((fx / home).read_text())
        ),
        "index": respx.get(portal.org_list_url).mock(
            return_value=_html((fx / "org_index.html").read_text())
        ),
        "listing": respx.get(url__regex=rf"https://{host}/.*component=%24DirectLink.*").mock(
            return_value=_html((fx / "org_listing.html").read_text())
        ),
    }


def _adapter(source_id: str, settings: Settings) -> GePNICAdapter:
    return PORTAL_ADAPTERS[source_id](client=_client(settings), settings=settings, now=lambda: NOW)


# --- configuration -------------------------------------------------------------------------


def test_configs_load_from_yaml_and_register_one_adapter_per_entry() -> None:
    load_builtin_adapters()
    configs = load_portal_configs()
    assert [c.source_id for c in configs] == [
        "gepnic_tn",
        "gepnic_up",
        "gepnic_central",
        "gepnic_mh",
        "gepnic_ts",
    ]
    assert [c.source_id for c in PORTAL_CONFIGS] == [c.source_id for c in configs]
    registered = registry.registered()
    for cfg in configs:
        cls = registered[cfg.source_id]
        assert issubclass(cls, GePNICAdapter) and cls is PORTAL_ADAPTERS[cfg.source_id]
        assert cls.region == "in" and cls.schedule == "0 */3 * * *"
        assert registry.is_enabled(cls) is cfg.enabled
        assert cls.portal == cfg and cls.display_name == cfg.display_name
        assert isinstance(cls(), SourceAdapter)
        assert cfg.date_formats == ("%d-%b-%Y %I:%M %p", "%d-%b-%Y") and cfg.tz == "Asia/Kolkata"
    assert {c.source_id for c in configs if c.enabled} == set(ENABLED)
    assert {c.source_id: c.health_status for c in configs if not c.enabled} == DISABLED
    mh = next(c for c in configs if c.source_id == "gepnic_mh")
    assert mh.reason and "Disallow: /" in mh.reason
    assert CONFIG_PATH.read_text().count("# ") > 5, "the YAML documents its entries"


def test_adding_a_state_is_config_only(tmp_path: Path) -> None:
    raw = yaml.safe_load(CONFIG_PATH.read_text())
    raw["portals"].append(
        {
            "source_id": "gepnic_kl",
            "display_name": "Kerala e-Tenders (etenders.kerala.gov.in)",
            "state": "Kerala",
            "portal_home": "https://etenders.kerala.gov.in/",
            "base_url": "https://etenders.kerala.gov.in/nicgep/app",
            "date_formats": ["%d-%b-%Y %I:%M %p"],
            "enabled": True,
        }
    )
    path = tmp_path / "portals.yaml"
    path.write_text(yaml.safe_dump(raw))
    configs = load_portal_configs(path)
    kerala = configs[-1]
    cls = adapter_class(kerala)
    assert cls.source_id == "gepnic_kl" and cls.__name__ == "GePNICGepnicKl"
    assert cls.portal.org_list_url.endswith("FrontEndTendersByOrganisation&service=page")
    adapter = cls()
    assert adapter.health().status is AdapterStatus.OK and adapter.enabled
    raw["portals"].append(dict(raw["portals"][0]))  # duplicate id rejected
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="duplicate"):
        load_portal_configs(path)


def test_beat_schedule_covers_enabled_portals_only() -> None:
    load_builtin_adapters()
    schedule = build_beat_schedule()
    for sid in ENABLED:
        assert f"source:{sid}" in schedule
    for sid in DISABLED:
        assert f"source:{sid}" not in schedule


# --- disabled portals ----------------------------------------------------------------------


@pytest.mark.parametrize("source_id", sorted(DISABLED))
def test_disabled_portals_never_fetch_and_report_their_status(
    source_id: str, settings: Settings
) -> None:
    adapter = _adapter(source_id, settings)
    with respx.mock(assert_all_called=False) as router:
        router.route().mock(side_effect=AssertionError("a disabled portal must not be requested"))
        assert list(adapter.fetch(SINCE, None)) == []
    health = adapter.health()
    assert health.status.value == DISABLED[source_id]
    assert health.message and adapter.display_name in health.message
    if source_id == "gepnic_mh":
        assert "robots" in health.message.lower()


@respx.mock
def test_mahatenders_robots_would_block_the_polite_client_anyway(settings: Settings) -> None:
    respx.get("https://mahatenders.gov.in/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /\n")
    )
    page = respx.get("https://mahatenders.gov.in/nicgep/app").mock(return_value=_html("<html/>"))
    with pytest.raises(RobotsDisallowedError):
        _client(settings).get(
            "https://mahatenders.gov.in/nicgep/app", source_id="gepnic_mh", external_id="home"
        )
    assert page.call_count == 0


# --- enabled portals -----------------------------------------------------------------------


@respx.mock
@pytest.mark.parametrize("source_id", ENABLED)
def test_fetch_reads_home_and_organisation_pages_and_normalises(
    source_id: str, settings: Settings
) -> None:
    adapter = _adapter(source_id, settings)
    routes = _serve(adapter.portal)
    latest = respx.get(adapter.portal.page_url(adapter.portal.latest_page)).mock(
        return_value=_html("captcha")
    )
    records = list(adapter.fetch(SINCE, None))
    assert routes["home"].call_count == 1 and routes["index"].call_count == 1
    assert routes["listing"].call_count == 2  # gepnic_max_orgs_per_run = 2 (3 orgs have tenders)
    assert latest.call_count == 0, "the CAPTCHA-gated Latest Active Tenders page is never fetched"
    ids = [r.external_id for r in records]
    assert len(ids) == len(set(ids))
    pages = {r.meta["page"] for r in records}
    assert pages == {"organisation_listing", "home_latest"}
    assert all(r.raw_ref and r.raw_ref.startswith(f"raw/{source_id}/2026/09/27/") for r in records)
    assert adapter.health().status is AdapterStatus.OK

    opps = [adapter.normalize(r) for r in records]
    assert all(o.source_id == source_id and o.region.value == "in" for o in opps)
    assert all(o.detail_status.value == "manual" for o in opps)
    assert all(o.extra["portal_search_url"] == adapter.portal.search_url for o in opps)
    listed = [o for o in opps if o.extra["listing_page"] == "organisation_listing"]
    assert listed and all(o.extra["tender_id"] and o.buyer_org for o in listed)
    assert all(o.source_url and o.source_url.startswith(adapter.portal.base_url) for o in opps)
    home_rows = [o for o in opps if o.extra["listing_page"] == "home_latest"]
    assert home_rows and all(o.response_due_at is not None for o in home_rows)


@respx.mock
def test_front_page_duplicate_of_a_listed_tender_is_dropped(settings: Settings) -> None:
    adapter = _adapter("gepnic_tn", settings)
    _serve(adapter.portal)
    records = list(adapter.fetch(SINCE, None))
    titles = [r.payload["title"] for r in records if isinstance(r.payload, dict)]
    assert titles.count("Formation of park at KRG Nagar in Ward No 20 North Zone") == 1
    kept = next(r for r in records if r.payload["title"].startswith("Formation of park"))  # type: ignore[index]
    assert kept.external_id == "2026_TNCMC_871234_1"  # the listing form (with tender id) wins


@respx.mock
def test_organisation_index_missing_degrades_but_home_still_flows(settings: Settings) -> None:
    adapter = _adapter("gepnic_up", settings)
    routes = _serve(adapter.portal)
    routes["index"].mock(return_value=_html("<h1>maintenance</h1>", status=404))
    records = list(adapter.fetch(SINCE, None))
    assert len(records) == 5 and all(r.meta["page"] == "home_latest" for r in records)
    health = adapter.health()
    assert health.status is AdapterStatus.DEGRADED and "HTTP 404" in (health.message or "")


@respx.mock
def test_malformed_home_page_degrades_and_layout_change_yields_nothing(
    settings: Settings,
) -> None:
    adapter = _adapter("gepnic_central", settings)
    routes = _serve(adapter.portal, home="malformed.html")
    routes["index"].mock(return_value=_html("<html>no index</html>"))
    records = list(adapter.fetch(SINCE, None))
    titles = [r.payload["title"] for r in records]  # type: ignore[index]
    assert "Supply of chairs for the collectorate" in titles and len(records) == 2
    bad = adapter.normalize(
        next(r for r in records if "chairs" in r.external_id or "COL" in r.external_id)
    )
    assert bad.response_due_at is None
    assert adapter.health().status is AdapterStatus.DEGRADED
    routes["home"].mock(
        return_value=_html((FIXTURES / "gepnic_central" / "layout_change.html").read_text())
    )
    assert list(adapter.fetch(SINCE, None)) == []
    assert "activeTenders" in (adapter.health().message or "")


@respx.mock
def test_home_page_error_marks_failing(settings: Settings) -> None:
    adapter = _adapter("gepnic_tn", settings)
    routes = _serve(adapter.portal)
    routes["home"].mock(return_value=_html("forbidden", status=403))
    with pytest.raises(GePNICPortalError, match="HTTP 403"):
        list(adapter.fetch(SINCE, None))
    assert adapter.health().status is AdapterStatus.FAILING


def test_detail_and_documents_never_request(settings: Settings) -> None:
    adapter = _adapter("gepnic_tn", settings)
    with respx.mock(assert_all_called=False) as router:
        router.route().mock(side_effect=AssertionError("no request expected"))
        raw = adapter.fetch_detail("2026_TNCMC_871234_1")
        assert raw.payload == {
            "detail_status": "manual",
            "portal_search_url": adapter.portal.search_url,
        }
        assert adapter.fetch_documents(raw) == []


def test_ad_hoc_portal_instance_outside_the_registry(settings: Settings) -> None:
    cfg = PortalConfig(
        source_id="gepnic_adhoc",
        display_name="Ad hoc",
        state="X",
        portal_home="https://x.gov.in/",
        base_url="https://x.gov.in/nicgep/app",
        enabled=False,
        health_status="not_implemented",
        reason="testing",
    )
    adapter = GePNICAdapter(settings=settings, portal=cfg, max_orgs=0)
    assert adapter.source_id == "gepnic_adhoc" and adapter.max_orgs == 0
    assert adapter.health().status is AdapterStatus.NOT_IMPLEMENTED
