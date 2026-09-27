"""Adapter contract tests (SPEC 5.1 / 12, M2-14, M3-09).

Every adapter in the registry is exercised over three fixture kinds:

- normal:        the recorded first page -> >= 1 record, every record normalises to an
                 OpportunityIn, health ok
- malformed:     a 200 page with broken records/types -> fetch() never raises, the
                 good record still normalises, health DEGRADED with a message
- layout_change: a 200 page whose keys/columns were renamed -> no records, no exception,
                 health DEGRADED naming the missing key

HTTP failures and non-JSON/HTML bodies are a different class (transport/API errors): they
raise so the runner keeps the watermark and marks the run `failing` (adapter tests).

Adding an adapter: register it, record fixtures under tests/adapters/fixtures/<source_id>/
(<normal file>, malformed.<ext>, layout_change.<ext>) and add a ContractSpec below. HTML
portals list their secondary pages as `extra` routes (served with their own fixture on
the normal and malformed pages, and with the layout-change page on the layout-change
run, as a portal redesign would). A registered adapter without a spec fails here on
purpose. Disabled adapters (stubs, robots-disallowed portal configs) are skipped
explicitly.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from app.adapters import registry
from app.adapters.base import AdapterStatus, OpportunityIn, RawRecord, SourceAdapter
from app.adapters.cppp import CpppAdapter
from app.adapters.grants_gov import SEARCH_URL as GRANTS_SEARCH_URL
from app.adapters.grants_gov import GrantsGovAdapter
from app.adapters.http import MemoryArchiver, PoliteClient
from app.adapters.registry import load_builtin_adapters
from app.adapters.sam_awards import SamAwardsAdapter
from app.adapters.sam_opps import SEARCH_URL as SAM_SEARCH_URL
from app.adapters.sam_opps import SamOpportunitiesAdapter
from app.adapters.usaspending import SEARCH_URL as USA_SEARCH_URL
from app.adapters.usaspending import UsaSpendingAdapter
from app.core.config import Settings
from app.core.normalize.cppp import LATEST_ACTIVE_URL as CPPP_LATEST_URL
from app.core.politeness import PolicyTable

FIXTURES = Path(__file__).resolve().parent / "fixtures"
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
KINDS = ("normal", "malformed", "layout_change")
CRON_RE = re.compile(r"^(\S+\s+){4}\S+$")

SETTINGS = Settings(  # type: ignore[call-arg]
    _env_file=None,
    sam_api_key="contract-test-key",
    sam_awards_naics="541512,541519",
    cppp_max_orgs_per_run=2,
)
CONTENT_TYPES = {".json": "application/json", ".html": "text/html; charset=utf-8"}


def polite_client() -> PoliteClient:
    return PoliteClient(
        settings=SETTINGS,
        archiver=MemoryArchiver(),
        clock=lambda: NOW.timestamp(),
        sleep=lambda s: None,
        rng=lambda: 1.0,
        policies=PolicyTable(default_rate=1e6, quotas={"api.sam.gov": 10_000}),
        now=lambda: NOW,
    )


@dataclass(frozen=True)
class ExtraRoute:
    """A secondary page an HTML adapter reads (organisation index, drill-down listing)."""

    url: str  # exact URL, or a regex when `regex` is True
    file: str  # fixture served on the normal and malformed runs
    regex: bool = False


@dataclass(frozen=True)
class ContractSpec:
    build: Callable[[], SourceAdapter]
    method: str
    url: str
    normal_file: str
    layout_key: str  # word the layout-change health message must mention
    extra: tuple[ExtraRoute, ...] = ()


SPECS: dict[str, ContractSpec] = {
    "sam_opps": ContractSpec(
        build=lambda: SamOpportunitiesAdapter(
            client=polite_client(), settings=SETTINGS, now=lambda: NOW, page_size=1000
        ),
        method="GET",
        url=SAM_SEARCH_URL,
        normal_file="page1.json",
        layout_key="opportunitiesData",
    ),
    "grants_gov": ContractSpec(
        build=lambda: GrantsGovAdapter(
            client=polite_client(),
            settings=SETTINGS,
            now=lambda: NOW,
            page_size=1000,
            with_details=False,
        ),
        method="POST",
        url=GRANTS_SEARCH_URL,
        normal_file="search2_page1.json",
        layout_key="oppHits",
    ),
    "usaspending": ContractSpec(
        build=lambda: UsaSpendingAdapter(
            client=polite_client(), settings=SETTINGS, now=lambda: NOW, page_size=100
        ),
        method="POST",
        url=USA_SEARCH_URL,
        normal_file="spending_by_award_page1.json",
        layout_key="results",
    ),
    "sam_awards": ContractSpec(
        build=lambda: SamAwardsAdapter(
            client=polite_client(), settings=SETTINGS, now=lambda: NOW, page_size=100
        ),
        method="GET",
        url=SETTINGS.sam_awards_api_url,
        normal_file="page1.json",
        layout_key="layout change",
    ),
    "cppp": ContractSpec(
        build=lambda: CpppAdapter(client=polite_client(), settings=SETTINGS, now=lambda: NOW),
        method="GET",
        url=CPPP_LATEST_URL,
        normal_file="latest_active.html",
        layout_key="Title",
        extra=(
            ExtraRoute(SETTINGS.cppp_by_org_url, "byorg.html"),
            ExtraRoute(
                r"https://eprocure\.gov\.in/cppp/tendersbyorganisation/.+",
                "org_listing.html",
                regex=True,
            ),
        ),
    ),
}


def fixture_path(source_id: str, kind: str) -> Path:
    spec = SPECS[source_id]
    suffix = Path(spec.normal_file).suffix
    name = spec.normal_file if kind == "normal" else f"{kind}{suffix}"
    return FIXTURES / source_id / name


def _cases() -> list[Any]:
    load_builtin_adapters()
    return [
        pytest.param(source_id, kind, id=f"{source_id}-{kind}")
        for source_id in registry.registered()
        for kind in KINDS
    ]


def _response(path: Path) -> httpx.Response:
    return httpx.Response(
        200,
        content=path.read_bytes(),
        headers={"content-type": CONTENT_TYPES.get(path.suffix, "application/octet-stream")},
    )


def _serve(router: respx.MockRouter, source_id: str, spec: ContractSpec, kind: str) -> None:
    """Routes go on the test's own router (never the global one: it would leak into the
    other adapter tests' @respx.mock decorators)."""
    main = fixture_path(source_id, kind)
    hosts = {httpx.URL(spec.url).host}
    for extra in spec.extra:
        if not extra.regex:
            hosts.add(httpx.URL(extra.url).host)
    for host in hosts:
        router.get(f"https://{host}/robots.txt").mock(return_value=httpx.Response(404))
    router.route(method=spec.method, url=spec.url).mock(return_value=_response(main))
    for extra in spec.extra:
        served = main if kind == "layout_change" else FIXTURES / source_id / extra.file
        if extra.regex:
            router.get(url__regex=extra.url).mock(return_value=_response(served))
        else:
            router.get(extra.url).mock(return_value=_response(served))


def _fetch_all(adapter: SourceAdapter) -> list[RawRecord]:
    """fetch() must never raise for a page the server answered with 200 JSON."""
    try:
        return list(adapter.fetch(NOW - timedelta(days=5), None))
    except Exception as exc:  # pragma: no cover - the assertion message is the point
        pytest.fail(f"{adapter.source_id}.fetch() raised on a malformed page: {exc!r}")


def _normalised(
    adapter: SourceAdapter, records: list[RawRecord]
) -> tuple[list[OpportunityIn], list[str]]:
    good: list[OpportunityIn] = []
    errors: list[str] = []
    for raw in records:
        assert raw.source_id == adapter.source_id
        assert raw.external_id, "every RawRecord needs an external_id"
        try:
            opp = adapter.normalize(raw)
        except Exception as exc:  # a bad record is the runner's per-record error path
            errors.append(f"{raw.external_id}: {type(exc).__name__}: {exc}")
            continue
        assert isinstance(opp, OpportunityIn)
        assert opp.source_id == adapter.source_id and opp.external_id == raw.external_id
        good.append(opp)
    return good, errors


@pytest.mark.parametrize(("source_id", "kind"), _cases())
def test_adapter_contract(source_id: str, kind: str) -> None:
    cls = registry.get_adapter_class(source_id)
    if not registry.is_enabled(cls):
        pytest.skip(f"{source_id} is a documented stub (enabled = False)")
    assert isinstance(cls.source_id, str) and cls.region in ("us", "in")
    assert CRON_RE.match(cls.schedule), f"{source_id}.schedule must be a 5-field cron"
    spec = SPECS.get(source_id)
    assert spec is not None, (
        f"adapter {source_id!r} has no ContractSpec in tests/adapters/contract.py; add one "
        "plus fixtures/<source_id>/{malformed,layout_change}.json"
    )
    path = fixture_path(source_id, kind)
    assert path.is_file(), f"missing fixture {path}"
    if path.suffix == ".json":
        json.loads(path.read_text())  # fixtures must stay valid JSON
    for extra in spec.extra:
        assert (FIXTURES / source_id / extra.file).is_file(), f"missing fixture {extra.file}"

    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        _serve(router, source_id, spec, kind)
        adapter = spec.build()
        assert isinstance(adapter, SourceAdapter)
        records = _fetch_all(adapter)
        health = adapter.health()
        good, errors = _normalised(adapter, records)

    if kind == "normal":
        assert records, "the recorded page must yield at least one record"
        assert not errors, (
            "every record of a normal page validates as OpportunityIn:\n" + "\n".join(errors)
        )
        assert health.status is AdapterStatus.OK, health
        assert len(good) == len(records)
    elif kind == "malformed":
        assert health.status is AdapterStatus.DEGRADED, health
        assert health.message, "degraded health must explain what was wrong"
        assert good, "the one well-formed record on a malformed page still normalises"
    else:  # layout_change
        assert records == [], "a renamed results key must not be mistaken for records"
        assert health.status is AdapterStatus.DEGRADED, health
        assert health.message and spec.layout_key in health.message, health.message
        assert "layout change" in (health.message or "")
    assert health.last_run_at is not None


def test_every_spec_matches_a_registered_adapter() -> None:
    load_builtin_adapters()
    unknown = set(SPECS) - set(registry.registered())
    assert not unknown, f"contract specs for unregistered adapters: {sorted(unknown)}"
