"""M5-10 eval (SPEC 12: "zero fabricated company facts"): the red-team reviewer's single
auto-revision must leave no unsupported company claim standing on the golden drafting set.

Pure: recorded draft bodies, a recorded review and a recorded revision per section
(evals/golden/drafting/), replayed through `app.core.grounding` and `app.core.red_team`
with a small profile fixture. No database, no network -- `make eval` runs it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from app.core.grounding import validate
from app.core.page_estimate import estimate_pages
from app.core.red_team import (
    ISSUE_UNSUPPORTED_CLAIM,
    RedTeamReport,
    RevisionCheck,
    SectionFacts,
    SectionReview,
    normalise,
    remaining_issues,
)

GOLDEN = Path(__file__).resolve().parents[3] / "evals" / "golden" / "drafting"
FABRICATION_BAR = 0  # SPEC 12: zero fabricated company facts


def _fixture() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    profile = json.loads((GOLDEN / "profile.json").read_text())
    sections = json.loads((GOLDEN / "sections.json").read_text())
    return profile, sections


def _grounding(profile: dict[str, Any], body: str) -> Any:
    return validate(body, [], frozenset(profile["tokens"]), tuple(profile["facts"]))


def test_the_drafting_golden_set_is_present() -> None:
    profile, sections = _fixture()
    assert profile["tokens"] and profile["facts"]
    assert len(sections) >= 3
    assert {s["section_id"] for s in sections} == {
        "technical-approach",
        "quality-management",
        "past-performance",
    }


def test_the_first_drafts_really_do_contain_fabrications() -> None:
    """The bar only means something if the unrevised drafts fail it."""
    profile, sections = _fixture()
    before = sum(_grounding(profile, s["before"]).unsupported_count for s in sections)
    assert before >= 3, "the golden drafts must carry unsupported claims to fix"


@pytest.mark.parametrize("section", _fixture()[1], ids=lambda s: str(s["section_id"]))
def test_every_reviewed_section_is_clean_after_the_single_revision(
    section: dict[str, Any],
) -> None:
    profile, _sections = _fixture()
    body = section["before"] if section["revision"] is None else section["revision"]["after"]
    report = _grounding(profile, body)
    assert report.unsupported_count == FABRICATION_BAR, [f.as_dict() for f in report.flags]
    assert report.unresolved_tokens == (), report.unresolved_tokens


def test_the_review_findings_are_closed_or_escalated() -> None:
    """Every issue is either fixed by the one revision or survives as a review comment --
    nothing is silently dropped, and nothing fabricated survives."""
    profile, sections = _fixture()
    facts = [
        SectionFacts(
            section_id=s["section_id"],
            title=s["title"],
            body_text=s["before"],
            maps_requirements=tuple(s["maps_requirements"]),
            page_budget=s["page_budget"],
            unsupported_count=_grounding(profile, s["before"]).unsupported_count,
            has_version=True,
        )
        for s in sections
    ]
    report = RedTeamReport(
        sections=[SectionReview.model_validate(s["report"]) for s in sections],
        overall_score=66,
    )
    normalised = normalise(
        report,
        facts,
        requirement_ids=[r for s in sections for r in s["maps_requirements"]],
        font_size_pt=12,
    )
    assert normalised.warnings == [], normalised.warnings

    fabrications = surviving = 0
    for section, review in zip(sections, normalised.report.sections, strict=True):
        assert review.section_id == section["section_id"]
        revision = section["revision"]
        after = section["before"] if revision is None else revision["after"]
        grounding = _grounding(profile, after)
        fabrications += grounding.unsupported_count
        check = RevisionCheck(
            revised=revision is not None,
            addressed=frozenset((revision or {}).get("addressed", ())),
            before_text=section["before"],
            after_text=after,
            after_unsupported=grounding.unsupported_count,
            estimate=estimate_pages(after, font_size_pt=12, limit=section["page_budget"]),
        )
        left = remaining_issues(review.issues, check)
        surviving += len(left)
        assert not [i for _n, i in left if i.kind == ISSUE_UNSUPPORTED_CLAIM], (
            f"{section['section_id']} still asserts an uncited company fact"
        )
    assert fabrications == FABRICATION_BAR
    assert surviving == 0, "the golden revisions close every recorded finding"
    print(f"drafting golden set: {len(sections)} sections, fabrications {fabrications}")
