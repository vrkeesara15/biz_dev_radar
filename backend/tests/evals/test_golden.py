"""M5-15: the golden set and its SPEC 12 pass bars, enforced by `make eval`.

`evals/run.py` is the runner a human calls to see the table; this module imports it so
the same measurements gate CI. It also checks the shape of the set itself (10 US
solicitations including the IRS sources-sought pattern, 10 Indian tenders across GeM,
CPPP and the state portals) and proves the metrics can fail, so a broken measurement
cannot pass quietly.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
GOLDEN = REPO_ROOT / "evals" / "golden"


def _runner() -> Any:
    spec = importlib.util.spec_from_file_location("evals_run", REPO_ROOT / "evals" / "run.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


run = _runner()


@pytest.fixture(scope="module")
async def summary() -> Any:
    return await run.summarise()


# --- the set itself -----------------------------------------------------------------------


def test_the_golden_set_has_ten_us_and_ten_indian_items() -> None:
    ids = [run.item_id(item) for item in run.golden_items()]
    us = [i for i in ids if i.startswith("us/")]
    india = [i for i in ids if i.startswith("in/")]
    assert len(us) == run.US_ITEMS_EXPECTED, us
    assert len(india) == run.IN_ITEMS_EXPECTED, india
    # SPEC 12 names the IRS sources-sought pattern explicitly
    assert "us/irs_sources_sought" in us


def test_the_us_set_covers_the_shapes_a_bidder_meets() -> None:
    kinds = {
        json.loads((item / "labels.json").read_text()).get("notice_type")
        for item in run.golden_items()
        if item.parent.name == "us"
    }
    assert {"rfp", "rfq", "sources_sought", "combined_synopsis", "grant"} <= kinds, kinds


def test_the_indian_set_covers_gem_cppp_and_the_state_portals() -> None:
    ids = [run.item_id(i) for i in run.golden_items() if i.parent.name == "in"]
    assert sum(1 for i in ids if "gem" in i) >= 5, ids
    assert sum(1 for i in ids if "cppp" in i) >= 3, ids
    assert sum(1 for i in ids if "gepnic" in i) >= 2, ids


def test_every_item_has_labels_a_notice_and_a_recorded_answer() -> None:
    for item in run.golden_items():
        labels = json.loads((item / "labels.json").read_text())
        assert (item / "notice.pdf").exists(), item
        assert (item / "recorded.json").exists(), item
        assert labels["requirements"], item
        assert labels["eligibility"], item
        assert labels["region"] in ("us", "in")
        for row in labels["requirements"]:
            assert int(row["page"]) >= 1
            assert row["type"] in (
                "shall",
                "must",
                "should",
                "eligibility",
                "format",
                "submission",
                "evaluation",
            )


def test_the_indian_items_label_turnover_emd_and_experience() -> None:
    """SPEC 12's eligibility bar names exactly these three fields."""
    for item in run.golden_items():
        if item.parent.name != "in":
            continue
        eligibility = json.loads((item / "labels.json").read_text())["eligibility"]
        for field in ("min_avg_turnover_inr", "emd_amount_inr", "min_experience_years"):
            assert eligibility.get(field), f"{run.item_id(item)} has no {field}"


def test_a_recorded_answer_is_not_a_copy_of_the_labels() -> None:
    """If it were, the pass bars would be a tautology."""
    for item in run.golden_items():
        labels = json.loads((item / "labels.json").read_text())
        recorded = json.loads((item / "recorded.json").read_text())
        labelled = {row["text"] for row in labels["requirements"]}
        predicted = {r["text"] for b in recorded["batches"] for r in b["requirements"]}
        assert not predicted <= labelled, run.item_id(item)
        assert recorded["expected"]["missed_labels"], run.item_id(item)


# --- the pass bars ------------------------------------------------------------------------


async def test_the_pass_bars_are_met(summary: Any) -> None:
    from tests.evals.conftest import record_table

    record_table(run.render(summary))  # printed by pytest_terminal_summary
    assert summary.failures() == []
    assert summary.recall >= run.RECALL_BAR, f"recall {summary.recall:.1%}"
    assert summary.precision >= run.PRECISION_BAR, f"precision {summary.precision:.1%}"
    assert summary.citation_coverage >= run.CITATION_BAR
    assert summary.page_accuracy >= run.CITATION_BAR
    assert summary.eligibility_rate >= run.ELIGIBILITY_BAR
    assert summary.fabrications == run.FABRICATION_BAR


async def test_the_bars_are_not_met_by_a_replay(summary: Any) -> None:
    """Real gaps: the set misses labels, over-extracts and gets items rejected."""
    assert summary.matched < summary.labels, "a perfect recall means the answers were copied"
    assert sum(i.rejected for i in summary.items) >= len(summary.items), (
        "every item should exercise the citation validator"
    )
    assert sum(i.duplicates_removed for i in summary.items) > 0
    assert 0.90 <= summary.recall < 1.0 and 0.85 <= summary.precision < 1.0


async def test_every_item_clears_the_bars_on_its_own(summary: Any) -> None:
    weak = [i.item_id for i in summary.items if not i.ok]
    assert weak == [], weak


async def test_india_turnover_emd_and_experience_are_exact(summary: Any) -> None:
    hits, total = summary.eligibility("in")
    assert total == 3 * run.IN_ITEMS_EXPECTED
    assert hits / total >= run.ELIGIBILITY_BAR, f"{hits}/{total}"


async def test_the_injected_solicitation_changes_nothing(summary: Any) -> None:
    """us/injected_rfp carries a prompt-injection line in its own PDF text (SPEC 11).

    It must score like any other item and the marker must never reach a requirement.
    """
    item = next(i for i in run.golden_items() if i.name == "injected_rfp")
    from app.core.parsing import parse_document

    text = " ".join(p.text for p in parse_document((item / "notice.pdf").read_bytes()).pages)
    assert "COMPROMISED" in text, "the golden PDF must carry the injection line"

    result = next(i for i in summary.items if i.item_id == "us/injected_rfp")
    assert result.ok
    labels = json.loads((item / "labels.json").read_text())
    assert all("COMPROMISED" not in row["text"] for row in labels["requirements"])
    recorded = json.loads((item / "recorded.json").read_text())
    predicted = [r["text"] for b in recorded["batches"] for r in b["requirements"]]
    assert all("COMPROMISED" not in t for t in predicted)
    assert all("optional" not in t.lower() for t in predicted)


# --- the measurements can fail ---------------------------------------------------------------


def test_the_eligibility_checker_notices_a_missing_number() -> None:
    from types import SimpleNamespace

    labels = {
        "region": "in",
        "eligibility": {
            "min_avg_turnover_inr": "4500000",
            "emd_amount_inr": "240000",
            "min_experience_years": 3,
        },
    }
    good = [
        SimpleNamespace(
            type="eligibility",
            text="The bidder shall have a minimum average annual turnover of Rs. 45,00,000.",
        ),
        SimpleNamespace(
            type="eligibility", text="The bidder shall furnish an EMD of Rs. 2,40,000."
        ),
        SimpleNamespace(
            type="eligibility", text="The bidder shall have at least 3 years of experience."
        ),
    ]
    assert all(check.ok for check in run.eligibility_checks(labels, good))

    wrong = [
        SimpleNamespace(
            type="eligibility",
            text="The bidder shall have a minimum average annual turnover of Rs. 4,50,000.",
        ),
        *good[1:],
    ]
    checks = run.eligibility_checks(labels, wrong)
    assert [c.field for c in checks if not c.ok] == ["min_avg_turnover_inr"]
    assert run.eligibility_checks(labels, []) == [
        run.FieldCheck("min_avg_turnover_inr", "4500000", ""),
        run.FieldCheck("emd_amount_inr", "240000", ""),
        run.FieldCheck("min_experience_years", "3", ""),
    ]


def test_the_us_eligibility_checker_notices_a_missing_naics_or_sam() -> None:
    from types import SimpleNamespace

    labels = {
        "region": "us",
        "eligibility": {
            "naics": "541512",
            "set_aside": "small_business",
            "sam_registration_required": True,
        },
    }
    good = [
        SimpleNamespace(
            type="eligibility",
            text="This is a total small business set-aside under NAICS 541512.",
        ),
        SimpleNamespace(type="eligibility", text="Offerors must be registered in SAM.gov."),
    ]
    assert all(c.ok for c in run.eligibility_checks(labels, good))
    partial = run.eligibility_checks(labels, good[:1])
    assert [c.field for c in partial if not c.ok] == ["sam_registration"]


def test_the_summary_reports_a_bar_it_fails() -> None:
    weak = run.ItemResult(
        item_id="us/fake",
        region="us",
        labels=10,
        predicted=10,
        matched=5,
        recall=0.5,
        precision=0.5,
        page_accuracy=1.0,
        cited=10,
    )
    summary = run.Summary(items=[weak], fabrications=2, sections=1)
    problems = " ".join(summary.failures())
    assert "recall" in problems and "precision" in problems
    assert "fabricated" in problems
    assert "us golden set has 1 items" in problems
