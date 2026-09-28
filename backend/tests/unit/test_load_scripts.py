"""M7-10: the SPEC 12 load scripts (`scripts/load/{seed,score,search}.py`).

Three things are worth asserting without running a real load:

1. the argument surface and the budget arithmetic (a scaled run must not silently
   inherit the 600 s SPEC gate, and the full size must not silently lose it);
2. the generators are deterministic in `--seed` and produce the distribution the
   scripts claim (both regions, noise NAICS, past-due and undated notices);
3. the three scripts really run end to end — so this module also drives them at
   `--scale 0.01` (500 notices x 2 profiles) against the test database and asserts the
   fields the README and the CI artifact promise. That costs a few seconds, which is
   the price of a load script that is never run until the night it is needed.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from tests.db import TEST_DATABASE_URL, TEST_DATABASE_URL_OWNER

LOAD_DIR = Path(__file__).resolve().parents[3] / "scripts" / "load"


def _load_module(name: str) -> Any:
    """Import a load script by file name (they are scripts, not an installed package)."""
    import importlib

    if str(LOAD_DIR) not in sys.path:
        sys.path.insert(0, str(LOAD_DIR))
    return importlib.import_module(name)


@pytest.fixture(scope="module")
def loadlib() -> Any:
    return _load_module("loadlib")


@pytest.fixture(scope="module")
def seed_script() -> Any:
    return _load_module("seed")


@pytest.fixture(scope="module")
def score_script() -> Any:
    return _load_module("score")


@pytest.fixture(scope="module")
def search_script() -> Any:
    return _load_module("search")


def _db_args() -> list[str]:
    return [
        "--database-url",
        TEST_DATABASE_URL,
        "--owner-database-url",
        TEST_DATABASE_URL_OWNER,
    ]


# --- the scripts exist and are self-describing -------------------------------------------


def test_the_three_entry_points_are_there() -> None:
    for name in ("seed.py", "score.py", "search.py", "loadlib.py", "README.md"):
        assert (LOAD_DIR / name).is_file(), f"scripts/load/{name} is missing"


# --- argument parsing ----------------------------------------------------------------------


def test_seed_defaults_are_the_spec_12_size(seed_script: Any) -> None:
    args = seed_script.build_parser().parse_args([])
    assert (args.profiles, args.opportunities, args.tenants) == (200, 50_000, 20)
    assert args.scale == 1.0 and args.reset is False
    assert seed_script.resolve_sizes(args) == (200, 50_000, 20)


def test_seed_scale_gives_the_ci_smoke_size(seed_script: Any) -> None:
    args = seed_script.build_parser().parse_args(["--scale", "0.1", "--reset"])
    assert seed_script.resolve_sizes(args) == (20, 5_000, 2)
    assert args.reset is True


def test_seed_explicit_sizes_win_over_the_defaults(seed_script: Any) -> None:
    args = seed_script.build_parser().parse_args(
        ["--profiles", "7", "--opportunities", "300", "--tenants", "3"]
    )
    assert seed_script.resolve_sizes(args) == (7, 300, 3)


def test_seed_refuses_to_truncate_a_database_that_is_not_disposable(seed_script: Any) -> None:
    """--reset is a TRUNCATE; the dev database must not be one typo away from it."""
    code = seed_script.main(
        ["--reset", "--database-url", "postgresql+asyncpg://u:p@localhost:5433/bidradar"]
    )
    assert code == 2


def test_score_budget_is_the_spec_gate_at_full_size(score_script: Any, loadlib: Any) -> None:
    budget, why = score_script.budget_for(loadlib.FULL_PROFILES * loadlib.FULL_OPPORTUNITIES, None)
    assert budget == loadlib.SCORE_BUDGET_SECONDS == 600.0
    assert "SPEC 12" in why


def test_score_budget_is_scaled_with_a_floor_below_full_size(
    score_script: Any, loadlib: Any
) -> None:
    budget, why = score_script.budget_for(20 * 5_000, None)
    assert budget == loadlib.MIN_SCALED_BUDGET_SECONDS
    assert "scaled guard" in why and "600" in why
    assert score_script.budget_for(1, 42.0) == (42.0, "explicit --budget-seconds")


def test_search_defaults(search_script: Any) -> None:
    args = search_script.build_parser().parse_args([])
    assert args.queries == 500
    assert args.p95_ms == 500.0
    assert args.base_url is None and args.explain is False


# --- generators are deterministic and shaped like the docstring says ----------------------


def test_profiles_are_deterministic_in_the_seed(loadlib: Any) -> None:
    first = loadlib.generate_profiles(profiles=40, tenants=4, seed=7)
    again = loadlib.generate_profiles(profiles=40, tenants=4, seed=7)
    other = loadlib.generate_profiles(profiles=40, tenants=4, seed=8)
    assert [p.as_dict() for p in first] == [p.as_dict() for p in again]
    assert [p.profile_id for p in first] != [p.profile_id for p in other]


def test_profiles_mix_regions_and_carry_what_matching_needs(loadlib: Any) -> None:
    specs = loadlib.generate_profiles(profiles=200, tenants=20, seed=1)
    assert len(specs) == 200
    assert len({s.profile_id for s in specs}) == 200
    assert len({s.tenant_id for s in specs}) == 20
    regions = Counter(s.region for s in specs)
    assert regions["us"] == regions["in"] == 100
    for spec in specs:
        assert len(spec.codes) >= 3, "what_we_sell wants >= 3 codes"
        assert len(spec.keywords) >= 5, "completeness wants >= 5 include keywords"
        assert spec.exclude_keyword
        assert spec.wanted_types and len(spec.states) == 2
        # a profile only ever wants notice types its own region publishes
        assert set(spec.wanted_types) <= {t for t, _ in loadlib.NOTICE_TYPES[spec.region]}


def test_opportunities_are_deterministic_in_the_seed(loadlib: Any) -> None:
    first = [o.as_dict() for o in loadlib.generate_opportunities(count=200, seed=3)]
    again = [o.as_dict() for o in loadlib.generate_opportunities(count=200, seed=3)]
    other = [o.as_dict() for o in loadlib.generate_opportunities(count=200, seed=4)]
    assert first == again
    assert first != other
    # the first N of a bigger run are the same rows: the corpus grows, it does not shift
    bigger = [o.as_dict() for o in loadlib.generate_opportunities(count=400, seed=3)]
    assert bigger[:200] == first


def test_opportunity_distribution_is_realistic(loadlib: Any) -> None:
    specs = list(loadlib.generate_opportunities(count=4_000, seed=11))
    regions = Counter(s.region for s in specs)
    assert regions["us"] == regions["in"] == 2_000
    statuses = Counter(s.status for s in specs)
    assert statuses["open"] > statuses["closed"] > 0
    assert set(statuses) <= {s for s, _ in loadlib.STATUSES}
    noise = sum(1 for s in specs if set(s.naics) & set(loadlib.NOISE_NAICS))
    assert 0.2 < noise / len(specs) < 0.4, "roughly 30% of the corpus is unrelated NAICS"
    assert any(s.due_offset_days is None for s in specs), "some notices have no deadline"
    assert any((s.due_offset_days or 0) < 0 for s in specs), "some notices are past due"
    assert max(s.due_offset_days or 0 for s in specs) <= 90, "deadlines spread over 90 days"
    assert all(s.title and s.description for s in specs)
    assert len({s.external_id for s in specs}) == len(specs)


def test_search_queries_are_deterministic_and_valid(search_script: Any) -> None:
    first = search_script.make_queries(200, 5)
    assert first == search_script.make_queries(200, 5)
    assert first != search_script.make_queries(200, 6)
    for params in first:
        assert params["page_size"] <= 100, "the API caps page_size at 100"
        assert params.get("page", 1) >= 1
        assert 0 <= params.get("min_score", 0) <= 100
        assert len(params.get("q", "")) <= 500
    kinds = Counter(k for params in first for k in params)
    for key in ("q", "region", "type", "naics", "status", "min_score", "page"):
        assert kinds[key] > 0, f"no query exercised {key}"


# --- small helpers -----------------------------------------------------------------------


def test_percentile_is_nearest_rank(loadlib: Any) -> None:
    values = [float(n) for n in range(1, 101)]
    assert loadlib.percentile(values, 50) == 50.0
    assert loadlib.percentile(values, 95) == 95.0
    assert loadlib.percentile(values, 99) == 99.0
    assert loadlib.percentile([], 95) == 0.0


def test_shards_are_round_robin_and_lose_nothing(loadlib: Any) -> None:
    groups = loadlib.shards(list(range(10)), 3)
    assert [len(g) for g in groups] == [4, 3, 3]
    assert sorted(x for g in groups for x in g) == list(range(10))
    assert loadlib.shards([1, 2], 8) == [[1], [2]], "never more shards than items"
    assert loadlib.shards([], 4) == []


def test_disposable_database_guard(loadlib: Any) -> None:
    assert loadlib.is_disposable("postgresql+asyncpg://u:p@h/bidradar_load")
    assert loadlib.is_disposable("postgresql+asyncpg://u:p@h/bidradar_test")
    assert not loadlib.is_disposable("postgresql+asyncpg://u:p@h/bidradar")
    assert loadlib.database_name("postgresql+asyncpg://u:p@h:5433/bidradar_load") == "bidradar_load"


# --- end to end at 1% of the SPEC size ------------------------------------------------------


@pytest.mark.usefixtures("clean_db")
def test_seed_score_and_search_run_end_to_end(
    tmp_path: Path, seed_script: Any, score_script: Any, search_script: Any
) -> None:
    """500 notices x 2 profiles through all three scripts, against the test database."""
    from app.core.db import get_database, set_database

    reports = tmp_path / "load-report"
    assert (
        seed_script.main(
            ["--scale", "0.01", "--reset", "--report", str(reports / "seed.json"), *_db_args()]
        )
        == 0
    )
    seed_report = json.loads((reports / "seed.json").read_text())
    assert seed_report["opportunities"] == 500
    assert seed_report["profiles"] == 2
    assert seed_report["matchable_profiles"] == 2, "every seeded profile must be scoreable"
    assert seed_report["kb_chunks"] >= 2 and seed_report["ok"] is True
    assert seed_report["rows_per_second"] > 0
    assert "opportunities" in seed_report["stages_seconds"]

    assert (
        score_script.main(
            [
                "--scale",
                "0.01",
                "--workers",
                "1",
                "--report",
                str(reports / "score.json"),
                *_db_args(),
            ]
        )
        == 0
    )
    score_report = json.loads((reports / "score.json").read_text())
    assert score_report["profiles"] == 2
    assert score_report["opportunities"] == 500
    assert score_report["nominal_pairs"] == 1_000
    assert score_report["created"] > 0, "the run must actually write matches"
    assert 0 < score_report["prefilter_survival"] < 1, "stage 1 must drop some pairs"
    assert score_report["budget_seconds"] == 180.0  # the scaled floor, not the SPEC gate
    assert score_report["errors"] == []
    assert score_report["ok"] is True
    for key in ("wall_seconds", "nominal_pairs_per_second", "scored_pairs_per_second"):
        assert key in score_report

    previous = get_database()
    try:
        assert (
            search_script.main(
                [
                    "--queries",
                    "40",
                    "--warmup",
                    "3",
                    "--report",
                    str(reports / "search.json"),
                    *_db_args(),
                ]
            )
            == 0
        )
    finally:
        set_database(previous)
    search_report = json.loads((reports / "search.json").read_text())
    assert search_report["ok_responses"] == 40
    assert search_report["failures"] == []
    assert 0 < search_report["p50_ms"] <= search_report["p95_ms"] <= search_report["p99_ms"]
    assert search_report["p95_ms"] <= search_report["budget_p95_ms"]
    assert search_report["items_returned"] > 0
    assert search_report["ok"] is True
