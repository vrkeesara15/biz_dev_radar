"""M3-09: every India adapter has fixtures and a contract spec, and `make smoke` covers
the enabled ones while naming the disabled portals explicitly."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from app.adapters import registry
from app.adapters.gepnic import PORTAL_ADAPTERS
from app.adapters.registry import load_builtin_adapters
from app.jobs.smoke import run_smoke, skipped_sources

from tests.adapters.contract import KINDS, SPECS, fixture_path
from tests.adapters.fixture_adapter import FixtureAdapter, raw_record

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
ENABLED_IN = ("cppp", "gem", "gepnic_tn", "gepnic_up", "gepnic_central")
DISABLED_IN = {"gepnic_mh": "robots_disallowed", "gepnic_ts": "not_implemented"}


def _india_sources() -> dict[str, type[Any]]:
    load_builtin_adapters()
    return {sid: cls for sid, cls in registry.registered().items() if cls.region == "in"}


def test_the_india_sources_are_the_expected_set() -> None:
    sources = _india_sources()
    enabled = {sid for sid, cls in sources.items() if registry.is_enabled(cls)}
    assert enabled == set(ENABLED_IN)
    assert set(DISABLED_IN) <= set(sources)
    for sid, status in DISABLED_IN.items():
        assert not registry.is_enabled(sources[sid])
        assert sources[sid]().health().status.value == status


@pytest.mark.parametrize("source_id", ENABLED_IN)
def test_every_enabled_india_adapter_has_a_spec_and_all_three_fixtures(source_id: str) -> None:
    spec = SPECS.get(source_id)
    assert spec is not None, f"{source_id} has no ContractSpec"
    for kind in KINDS:
        path = fixture_path(source_id, kind)
        assert path.is_file() and path.stat().st_size > 0, f"missing fixture {path}"
    for extra in spec.extra:
        secondary = fixture_path(source_id, "normal").parent / extra.file
        assert secondary.is_file(), f"missing secondary fixture {secondary}"


def test_adding_a_gepnic_state_adds_its_contract_spec_automatically() -> None:
    enabled_portals = {sid for sid, cls in PORTAL_ADAPTERS.items() if cls.enabled}
    assert enabled_portals <= set(SPECS)
    assert set(DISABLED_IN).isdisjoint(SPECS), "a disabled portal is never contract-tested"


# --- make smoke ------------------------------------------------------------------------


def _stub(cls: type[Any]) -> FixtureAdapter:
    """A stand-in with the real source id: the smoke wiring is under test, not the portal."""
    adapter = FixtureAdapter([raw_record("a", posted_at=NOW)])
    adapter.source_id = cls.source_id
    return adapter


def test_make_smoke_covers_the_india_adapters_and_skips_the_disabled_portals() -> None:
    results = {r.source_id: r for r in run_smoke(factory=_stub, now=NOW)}
    assert set(ENABLED_IN) <= set(results)
    assert all(results[sid].ok for sid in ENABLED_IN)
    assert set(DISABLED_IN).isdisjoint(results)

    skipped = {row["source_id"]: row for row in skipped_sources()}
    for sid, status in DISABLED_IN.items():
        assert skipped[sid]["status"] == status
        assert skipped[sid]["reason"], "a skipped source must say why"
    assert "robots" in skipped["gepnic_mh"]["reason"].lower()


def test_only_restricts_the_run_to_one_india_portal() -> None:
    results = run_smoke(factory=_stub, only=["gepnic_up"], now=NOW)
    assert [r.source_id for r in results] == ["gepnic_up"]
