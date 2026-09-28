"""Golden-set eval for the requirements extractor (SPEC 12: recall >= 90%, precision >= 85%,
every requirement cites a page). Runs with recorded LLM answers by default; set
BIDRADAR_LIVE_EVAL=1 with ANTHROPIC_API_KEY to call the live model.

Golden items: evals/golden/<region>/<item>/{notice.pdf, labels.json, recorded.json}.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from app.agents.extractor import extract_requirements
from app.core.config import Settings
from app.core.parsing import parse_document
from app.core.requirements import DocText, PageText

from tests.llm_fake import FakeLLM

REPO_ROOT = Path(__file__).resolve().parents[3]
GOLDEN = REPO_ROOT / "evals" / "golden"
RECALL_BAR = 0.90
PRECISION_BAR = 0.85
PAGE_BAR = 1.0  # every matched requirement cites the labelled page


def _scoring() -> Any:
    spec = importlib.util.spec_from_file_location(
        "evals_scoring", REPO_ROOT / "evals" / "scoring.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


def _runner() -> Any:
    """evals/run.py, which owns the shared metric definitions."""
    spec = importlib.util.spec_from_file_location("evals_run", REPO_ROOT / "evals" / "run.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def golden_items() -> list[Path]:
    return sorted(p.parent for p in GOLDEN.glob("*/*/labels.json"))


def _llm(item: Path) -> tuple[Any, dict[str, Any]]:
    """(client, replay expectations). Live runs have no expectations to check."""
    if os.environ.get("BIDRADAR_LIVE_EVAL") == "1" and os.environ.get("ANTHROPIC_API_KEY"):
        from app.agents.llm import AnthropicLLM

        return AnthropicLLM(Settings()), {}
    recorded = json.loads((item / "recorded.json").read_text())
    llm = FakeLLM()
    for batch in recorded["batches"]:
        llm.queue(batch)
    return llm, dict(recorded.get("expected") or {})


def test_golden_set_has_the_irs_sources_sought_item() -> None:
    names = [f"{p.parent.name}/{p.name}" for p in golden_items()]
    assert "us/irs_sources_sought" in names, names


@pytest.mark.parametrize("item", golden_items(), ids=lambda p: f"{p.parent.name}/{p.name}")
async def test_extraction_recall_precision_and_citations(item: Path) -> None:
    scoring = _scoring()
    labels = json.loads((item / "labels.json").read_text())
    parsed = parse_document((item / "notice.pdf").read_bytes())
    doc = DocText(
        document_id="00000000-0000-0000-0000-000000000001",
        name="notice.pdf",
        pages=tuple(PageText(p.number, p.text) for p in parsed.pages),
    )
    llm, expected = _llm(item)
    result = await extract_requirements(llm, [doc], settings=Settings(_env_file=None))  # type: ignore[call-arg]
    output = result.to_output()

    predicted = [(r.text, r.page) for r in output.requirements]
    labelled = [(row["text"], int(row["page"])) for row in labels["requirements"]]
    score = scoring.score_requirements([t for t, _ in predicted], [t for t, _ in labelled])
    pages = scoring.page_accuracy(predicted, labelled, score)
    print(f"{item.parent.name}/{item.name}: {score.summary()}, page accuracy {pages:.0%}")
    assert score.recall >= RECALL_BAR, score.summary()
    assert score.precision >= PRECISION_BAR, score.summary()
    assert pages >= PAGE_BAR, "a requirement cites the wrong page"
    # SPEC 8: every extracted requirement carries its document + page, none was uncited
    assert all(r.page >= 1 and r.document_id for r in output.requirements)
    assert {r.type for r in output.requirements} <= {
        "shall",
        "must",
        "should",
        "eligibility",
        "format",
        "submission",
        "evaluation",
    }
    # the labelled eligibility facts are represented among the eligibility requirements.
    # The check is the shared, region-aware one in evals/run.py: US items must state the
    # NAICS, the set-aside and the SAM obligation; Indian items must carry the turnover,
    # EMD and experience numbers exactly (SPEC 12's eligibility bar).
    checks = _runner().eligibility_checks(labels, list(output.requirements))
    assert checks, "every golden item declares eligibility facts"
    assert [c.field for c in checks if not c.ok] == [], [
        (c.field, c.expected, c.actual) for c in checks if not c.ok
    ]

    # The recorded answer is not a copy of the labels: it restates the requirements, misses
    # one, over-extracts once and includes items with a page outside the batch or an
    # invented quote. Those must be rejected, never stored, and the repeat de-duplicated --
    # that is what keeps precision above the bar.
    if expected:
        assert len(output.rejected) == expected["rejected"], [r.reason for r in output.rejected]
        assert output.duplicates_removed == expected["duplicates_removed"]
        rejected_texts = {r.text for r in output.rejected}
        assert rejected_texts.isdisjoint({r.text for r in output.requirements})
        assert 0.0 < score.recall < 1.0 and 0.0 < score.precision < 1.0, score.summary()
