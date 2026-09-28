"""Golden-set eval runner (SPEC 12 pass bars). Prints a table and exits non-zero below a bar.

    cd backend && uv run python ../evals/run.py
    cd backend && BIDRADAR_LIVE_EVAL=1 ANTHROPIC_API_KEY=... uv run python ../evals/run.py

What it measures over `evals/golden/<region>/<item>/`:

| metric                | how                                                        | bar  |
| --------------------- | ---------------------------------------------------------- | ---- |
| requirement recall    | greedy fuzzy match of stored requirements to the labels     | 90%  |
| requirement precision | the same match, over what was stored                        | 85%  |
| page citations        | stored requirements carrying a document + page              | 100% |
| eligibility exact     | US: NAICS / set-aside / SAM. India: turnover, EMD, years    | 90%  |
| fabricated facts      | unsupported company claims after the red team's revision    | 0    |

Recorded model answers are replayed through `tests.llm_fake.FakeLLM` by default, so the
run never touches the network. `BIDRADAR_LIVE_EVAL=1` with `ANTHROPIC_API_KEY` switches
the extractor to the real model; the bars are the same either way.

`backend/tests/evals/test_golden.py` imports this module and asserts the same bars, so
`make eval` enforces them too.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN = REPO_ROOT / "evals" / "golden"
BACKEND = REPO_ROOT / "backend"
DRAFTING = GOLDEN / "drafting"

RECALL_BAR = 0.90
PRECISION_BAR = 0.85
CITATION_BAR = 1.0
ELIGIBILITY_BAR = 0.90
FABRICATION_BAR = 0

US_ITEMS_EXPECTED = 10
IN_ITEMS_EXPECTED = 10

_DOC_ID = "00000000-0000-0000-0000-000000000001"


def _ensure_backend_on_path() -> None:
    for path in (str(BACKEND),):
        if path not in sys.path:
            sys.path.insert(0, path)


def load_scoring() -> Any:
    spec = importlib.util.spec_from_file_location(
        "evals_scoring", REPO_ROOT / "evals" / "scoring.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


def golden_items() -> list[Path]:
    """Every golden item directory, US first then India, in a stable order."""
    return sorted(
        (p.parent for p in GOLDEN.glob("*/*/labels.json")),
        key=lambda p: (p.parent.name != "us", p.parent.name, p.name),
    )


def item_id(item: Path) -> str:
    return f"{item.parent.name}/{item.name}"


def live() -> bool:
    return os.environ.get("BIDRADAR_LIVE_EVAL") == "1" and bool(os.environ.get("ANTHROPIC_API_KEY"))


# --- eligibility ---------------------------------------------------------------------------

_YEARS_RE = re.compile(r"(?<![\d.])(\d{1,2})\s*(?:\+\s*)?year", re.IGNORECASE)


def _money(texts: list[str], *keywords: str) -> Decimal | None:
    """The first INR amount in a sentence that mentions one of `keywords`."""
    from app.core.money import parse_inr

    for text in texts:
        lowered = text.lower()
        if not any(word in lowered for word in keywords):
            continue
        amount = parse_inr(text)
        if amount is not None:
            return amount
    return None


def _years(texts: list[str], *keywords: str) -> int | None:
    for text in texts:
        lowered = text.lower()
        if not any(word in lowered for word in keywords):
            continue
        found = _YEARS_RE.search(text)
        if found:
            return int(found.group(1))
    return None


@dataclass(frozen=True, slots=True)
class FieldCheck:
    field: str
    expected: str
    actual: str

    @property
    def ok(self) -> bool:
        return self.expected == self.actual


def eligibility_checks(labels: dict[str, Any], requirements: list[Any]) -> list[FieldCheck]:
    """Did the extractor capture the eligibility facts SPEC 12's bar names?

    India: the three numbers (average annual turnover, EMD, years of experience) must be
    extracted exactly. US: the NAICS code, the set-aside and the SAM registration
    obligation must be present in the eligibility requirements.
    """
    eligibility = dict(labels.get("eligibility") or {})
    texts = [r.text for r in requirements if r.type == "eligibility"]
    joined = " ".join(texts)
    checks: list[FieldCheck] = []
    if labels.get("region") == "in":
        turnover = _money(texts, "turnover")
        emd = _money(texts, "emd", "earnest money")
        years = _years(texts, "experience")
        for name, expected, actual in (
            ("min_avg_turnover_inr", eligibility.get("min_avg_turnover_inr"), turnover),
            ("emd_amount_inr", eligibility.get("emd_amount_inr"), emd),
            ("min_experience_years", eligibility.get("min_experience_years"), years),
        ):
            checks.append(
                FieldCheck(
                    name,
                    "" if expected is None else str(Decimal(str(expected))),
                    "" if actual is None else str(Decimal(str(actual))),
                )
            )
        return checks
    naics = str(eligibility.get("naics") or "")
    if naics:
        checks.append(FieldCheck("naics", naics, naics if naics in joined else ""))
    set_aside = str(eligibility.get("set_aside") or "")
    if set_aside and set_aside != "none":
        needle = set_aside.replace("_", " ").lower()
        aliases = {
            "small business": ("small business",),
            "8a": ("8(a)",),
            "hubzone": ("hubzone",),
            "sdvosb": ("service-disabled veteran", "sdvosb"),
            "idiq holders": ("alliant", "governmentwide acquisition"),
        }
        found = any(alias in joined.lower() for alias in aliases.get(needle, (needle,)))
        checks.append(FieldCheck("set_aside", set_aside, set_aside if found else ""))
    if eligibility.get("sam_registration_required"):
        checks.append(FieldCheck("sam_registration", "yes", "yes" if "SAM" in joined else ""))
    # a notice with no NAICS and no set-aside (a city RFP, say) declares the phrases that
    # carry its eligibility instead, so every item is measured on something
    for keyword in eligibility.get("eligibility_keywords") or ():
        text = str(keyword)
        found = text.lower() in joined.lower()
        checks.append(FieldCheck(f"keyword:{text}", text, text if found else ""))
    return checks


# --- one item ---------------------------------------------------------------------------------


@dataclass(slots=True)
class ItemResult:
    item_id: str
    region: str
    labels: int = 0
    predicted: int = 0
    matched: int = 0
    recall: float = 0.0
    precision: float = 0.0
    page_accuracy: float = 0.0
    cited: int = 0
    uncited: int = 0
    rejected: int = 0
    duplicates_removed: int = 0
    eligibility: list[FieldCheck] = field(default_factory=list)

    @property
    def eligibility_hits(self) -> int:
        return sum(1 for check in self.eligibility if check.ok)

    @property
    def citation_coverage(self) -> float:
        total = self.cited + self.uncited
        return 1.0 if total == 0 else self.cited / total

    @property
    def ok(self) -> bool:
        return (
            self.recall >= RECALL_BAR
            and self.precision >= PRECISION_BAR
            and self.page_accuracy >= CITATION_BAR
            and self.citation_coverage >= CITATION_BAR
        )


async def run_item(item: Path, scoring: Any) -> ItemResult:
    _ensure_backend_on_path()
    from app.agents.extractor import extract_requirements
    from app.core.config import Settings
    from app.core.parsing import parse_document
    from app.core.requirements import DocText, PageText

    labels = json.loads((item / "labels.json").read_text())
    parsed = parse_document((item / "notice.pdf").read_bytes())
    doc = DocText(
        document_id=_DOC_ID,
        name="notice.pdf",
        pages=tuple(PageText(p.number, p.text) for p in parsed.pages),
    )
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    if live():
        from app.agents.llm import AnthropicLLM

        llm: Any = AnthropicLLM(Settings())  # type: ignore[call-arg]
    else:
        from tests.llm_fake import FakeLLM

        recorded = json.loads((item / "recorded.json").read_text())
        llm = FakeLLM()
        for batch in recorded["batches"]:
            llm.queue(batch)
    output = (await extract_requirements(llm, [doc], settings=settings)).to_output()

    predicted = [(r.text, r.page) for r in output.requirements]
    labelled = [(row["text"], int(row["page"])) for row in labels["requirements"]]
    score = scoring.score_requirements([t for t, _ in predicted], [t for t, _ in labelled])
    return ItemResult(
        item_id=item_id(item),
        region=str(labels.get("region") or item.parent.name),
        labels=len(labelled),
        predicted=len(predicted),
        matched=len(score.matched),
        recall=score.recall,
        precision=score.precision,
        page_accuracy=scoring.page_accuracy(predicted, labelled, score),
        cited=sum(1 for r in output.requirements if r.page >= 1 and r.document_id),
        uncited=sum(1 for r in output.requirements if not (r.page >= 1 and r.document_id)),
        rejected=len(output.rejected),
        duplicates_removed=output.duplicates_removed,
        eligibility=eligibility_checks(labels, list(output.requirements)),
    )


# --- fabricated company facts -------------------------------------------------------------


def fabricated_facts() -> tuple[int, int]:
    """(fabrications after the red team's one revision, sections checked).

    Uses the recorded drafting golden set: the first drafts carry unsupported claims and
    the recorded revision must leave none (SPEC 12: zero fabricated company facts).
    """
    _ensure_backend_on_path()
    from app.core.grounding import validate

    profile = json.loads((DRAFTING / "profile.json").read_text())
    sections = json.loads((DRAFTING / "sections.json").read_text())
    tokens = frozenset(profile["tokens"])
    facts = tuple(profile["facts"])
    total = 0
    for section in sections:
        revision = section.get("revision")
        body = section["before"] if revision is None else revision["after"]
        total += validate(body, [], tokens, facts).unsupported_count
    return total, len(sections)


# --- the table ------------------------------------------------------------------------------


@dataclass(slots=True)
class Summary:
    items: list[ItemResult]
    fabrications: int
    sections: int

    @property
    def labels(self) -> int:
        return sum(i.labels for i in self.items)

    @property
    def predicted(self) -> int:
        return sum(i.predicted for i in self.items)

    @property
    def matched(self) -> int:
        return sum(i.matched for i in self.items)

    @property
    def recall(self) -> float:
        return self.matched / self.labels if self.labels else 0.0

    @property
    def precision(self) -> float:
        return self.matched / self.predicted if self.predicted else 0.0

    @property
    def citation_coverage(self) -> float:
        cited = sum(i.cited for i in self.items)
        total = cited + sum(i.uncited for i in self.items)
        return 1.0 if total == 0 else cited / total

    @property
    def page_accuracy(self) -> float:
        if not self.items:
            return 0.0
        return sum(i.page_accuracy * i.matched for i in self.items) / max(1, self.matched)

    def eligibility(self, region: str | None = None) -> tuple[int, int]:
        rows = [i for i in self.items if region is None or i.region == region]
        hits = sum(i.eligibility_hits for i in rows)
        total = sum(len(i.eligibility) for i in rows)
        return hits, total

    @property
    def eligibility_rate(self) -> float:
        hits, total = self.eligibility()
        return hits / total if total else 0.0

    def region_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.items:
            counts[row.region] = counts.get(row.region, 0) + 1
        return counts

    def failures(self) -> list[str]:
        problems: list[str] = []
        counts = self.region_counts()
        if counts.get("us", 0) < US_ITEMS_EXPECTED:
            problems.append(f"us golden set has {counts.get('us', 0)} items, expected 10")
        if counts.get("in", 0) < IN_ITEMS_EXPECTED:
            problems.append(f"in golden set has {counts.get('in', 0)} items, expected 10")
        if self.recall < RECALL_BAR:
            problems.append(f"recall {self.recall:.1%} < {RECALL_BAR:.0%}")
        if self.precision < PRECISION_BAR:
            problems.append(f"precision {self.precision:.1%} < {PRECISION_BAR:.0%}")
        if self.citation_coverage < CITATION_BAR:
            problems.append(f"page citations {self.citation_coverage:.1%} < 100%")
        if self.page_accuracy < CITATION_BAR:
            problems.append(f"page accuracy {self.page_accuracy:.1%} < 100%")
        if self.eligibility_rate < ELIGIBILITY_BAR:
            problems.append(f"eligibility exact match {self.eligibility_rate:.1%} < 90%")
        if self.fabrications > FABRICATION_BAR:
            problems.append(f"{self.fabrications} fabricated company fact(s)")
        for row in self.items:
            if not row.ok:
                problems.append(
                    f"{row.item_id}: recall {row.recall:.0%} precision {row.precision:.0%} "
                    f"pages {row.page_accuracy:.0%}"
                )
        return problems


def render(summary: Summary) -> str:
    head = f"{'item':34} {'labels':>6} {'kept':>5} {'recall':>7} {'prec':>7} {'pages':>6} {'elig':>6}"
    lines = [head, "-" * len(head)]
    for row in summary.items:
        hits = row.eligibility_hits
        total = len(row.eligibility)
        lines.append(
            f"{row.item_id:34} {row.labels:6d} {row.predicted:5d} "
            f"{row.recall:6.1%} {row.precision:6.1%} {row.page_accuracy:5.0%} "
            f"{f'{hits}/{total}':>6}"
        )
    lines.append("-" * len(head))
    us_hits, us_total = summary.eligibility("us")
    in_hits, in_total = summary.eligibility("in")
    lines.append(
        f"{'TOTAL':34} {summary.labels:6d} {summary.predicted:5d} "
        f"{summary.recall:6.1%} {summary.precision:6.1%} {summary.page_accuracy:5.0%} "
        f"{f'{us_hits + in_hits}/{us_total + in_total}':>6}"
    )
    lines.append("")
    lines.append(f"items                 {len(summary.items)} ({summary.region_counts()})")
    lines.append(f"page citation coverage {summary.citation_coverage:.1%}  (bar 100%)")
    lines.append(
        f"eligibility exact      {summary.eligibility_rate:.1%}  (bar 90%); "
        f"US {us_hits}/{us_total}, India turnover/EMD/experience {in_hits}/{in_total}"
    )
    lines.append(
        f"fabricated facts       {summary.fabrications} over {summary.sections} drafted "
        "sections  (bar 0)"
    )
    lines.append(f"mode                   {'LIVE model' if live() else 'recorded answers'}")
    return "\n".join(lines)


async def summarise() -> Summary:
    scoring = load_scoring()
    items = golden_items()
    results = [await run_item(item, scoring) for item in items]
    fabrications, sections = fabricated_facts()
    return Summary(items=results, fabrications=fabrications, sections=sections)


async def main() -> int:
    summary = await summarise()
    print(render(summary))
    problems = summary.failures()
    if problems:
        print("\nFAILED:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("\nall pass bars met")
    return 0


if __name__ == "__main__":
    _ensure_backend_on_path()
    raise SystemExit(asyncio.run(main()))
