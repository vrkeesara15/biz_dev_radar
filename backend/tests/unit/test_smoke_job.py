"""M2-14: `make smoke` entrypoint: skipped without BIDRADAR_LIVE, >= 1 record per adapter."""

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from app.jobs import smoke
from app.jobs.smoke import SmokeResult, main, run_smoke

from tests.adapters.fixture_adapter import FixtureAdapter, raw_record

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


class HealthyStub(FixtureAdapter):
    source_id = "healthy"

    def __init__(self) -> None:
        super().__init__([raw_record("a", posted_at=NOW), raw_record("b", posted_at=NOW)])


class EmptyStub(FixtureAdapter):
    source_id = "empty"

    def __init__(self) -> None:
        super().__init__([])


class BrokenStub(FixtureAdapter):
    source_id = "broken"

    def __init__(self) -> None:
        super().__init__([], fail_fetch=RuntimeError("portal down"))


class Disabled(FixtureAdapter):
    source_id = "disabled"
    enabled = False


def test_skipped_without_live_flag(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    monkeypatch.delenv(smoke.LIVE_ENV, raising=False)
    assert main([]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "skipped" and smoke.LIVE_ENV in out["reason"]


def test_run_smoke_reports_each_enabled_adapter() -> None:
    adapters: dict[str, type[Any]] = {
        "healthy": HealthyStub,
        "empty": EmptyStub,
        "broken": BrokenStub,
        "disabled": Disabled,
    }
    results = {r.source_id: r for r in run_smoke(adapters, now=NOW, days=7)}
    assert set(results) == {"healthy", "empty", "broken"}  # disabled stubs are skipped
    assert results["healthy"].ok and results["healthy"].records == 1  # stops after the first
    assert not results["empty"].ok and results["empty"].records == 0
    assert not results["broken"].ok and "portal down" in (results["broken"].message or "")
    only = run_smoke(adapters, only=["healthy"], now=NOW)
    assert [r.source_id for r in only] == ["healthy"]


def test_main_live_exit_code_and_report(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    monkeypatch.setenv(smoke.LIVE_ENV, "1")
    monkeypatch.setattr(
        smoke,
        "run_smoke",
        lambda **kw: [
            SmokeResult("a", ok=True, records=1, health="ok"),
            SmokeResult("b", ok=False, message="x"),
        ],
    )
    assert main(["--days", "3"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "failed" and report["failed"] == ["b"] and report["checked"] == 2
    monkeypatch.setattr(
        smoke, "run_smoke", lambda **kw: [SmokeResult("a", ok=True, records=1, health="ok")]
    )
    assert main([]) == 0
    monkeypatch.setattr(smoke, "run_smoke", lambda **kw: [])
    assert main([]) == 1  # nothing checked is a failure, not a pass
