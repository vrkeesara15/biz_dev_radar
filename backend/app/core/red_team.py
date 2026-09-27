"""Red-team review, pure part (SPEC 8 agent 8).

The Opus-class reviewer scores each section against its evaluation criterion and lists
the issues it found; this module is everything about that report that must NOT depend on
the model:

- `RedTeamReport` is the JSON schema the model answers with,
- `normalise()` drops invented section ids and requirement ids, RECOMPUTES the missing
  requirements from the compliance matrix and REPLACES the model's page-limit opinions
  with a deterministic estimate (app.core.page_estimate),
- `remaining_issues()` decides, after the single auto-revision, which issues are really
  gone (a page limit the revision now fits, an unsupported sentence it removed) and which
  must become review comments.

No I/O, no model call, so the numbers a reviewer sees are reproducible.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.page_estimate import PageEstimate, estimate_pages

ISSUE_NON_COMPLIANT = "non_compliant"
ISSUE_UNSUPPORTED_CLAIM = "unsupported_claim"
ISSUE_MISSING_REQUIREMENT = "missing_requirement"
ISSUE_PAGE_LIMIT = "page_limit"
ISSUE_CLARITY = "clarity"
ISSUE_KINDS: tuple[str, ...] = (
    ISSUE_NON_COMPLIANT,
    ISSUE_UNSUPPORTED_CLAIM,
    ISSUE_MISSING_REQUIREMENT,
    ISSUE_PAGE_LIMIT,
    ISSUE_CLARITY,
)

MIN_SCORE = 0
MAX_SCORE = 100


class Issue(BaseModel):
    kind: Literal[
        "non_compliant",
        "unsupported_claim",
        "missing_requirement",
        "page_limit",
        "clarity",
    ]
    # the requirement the issue is about (R-001 ...), when it is about one
    requirement_id: str | None = Field(default=None, max_length=16)
    # the exact sentence at fault, copied from the draft, when there is one
    sentence: str | None = Field(default=None, max_length=1000)
    fix_suggestion: str = Field(min_length=3, max_length=600)


class CriterionScore(BaseModel):
    criterion: str = Field(min_length=2, max_length=300)
    score: int = Field(ge=MIN_SCORE, le=MAX_SCORE)
    note: str = Field(default="", max_length=600)


class SectionReview(BaseModel):
    section_id: str = Field(min_length=1, max_length=64)
    score: int = Field(ge=MIN_SCORE, le=MAX_SCORE)
    criterion_scores: list[CriterionScore] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)


class RedTeamReport(BaseModel):
    """SPEC 8 agent 8: "scores each section against evaluation criteria and lists
    non-compliant or unsupported claims, missing requirements and page-limit overruns"."""

    sections: list[SectionReview] = Field(default_factory=list)
    overall_score: int = Field(default=0, ge=MIN_SCORE, le=MAX_SCORE)
    # req ids the package never answers (recomputed from the matrix by `normalise`)
    missing_requirements: list[str] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SectionFacts:
    """What the pure layer knows about one drafted section."""

    section_id: str
    title: str
    body_text: str = ""
    # req ids the outline mapped to this section
    maps_requirements: tuple[str, ...] = ()
    page_budget: int | None = None
    # grounding flags already on the current version (app.core.grounding)
    unsupported_count: int = 0
    has_version: bool = False
    # a version the red team already revised is never revised a second time (SPEC 8:
    # "drafts auto-revised once")
    already_revised: bool = False


@dataclass(frozen=True, slots=True)
class NormalisedReport:
    report: RedTeamReport
    estimates: dict[str, PageEstimate] = field(default_factory=dict)
    package: PageEstimate | None = None
    warnings: list[str] = field(default_factory=list)
    unknown_sections: tuple[str, ...] = ()
    unknown_requirements: tuple[str, ...] = ()

    @property
    def issues(self) -> list[tuple[str, Issue]]:
        return [(s.section_id, issue) for s in self.report.sections for issue in s.issues]


def _mean(values: Sequence[int]) -> int:
    return round(sum(values) / len(values)) if values else 0


def page_limit_issue(estimate: PageEstimate, *, scope: str) -> Issue:
    """The deterministic overrun issue (never the model's opinion of length)."""
    return Issue(
        kind=ISSUE_PAGE_LIMIT,
        requirement_id=None,
        sentence=None,
        fix_suggestion=(
            f"{scope} is about {estimate.pages} pages against a {estimate.limit}-page limit "
            f"({estimate.words} words at {estimate.words_per_page} words per page); "
            f"cut about {estimate.words_to_cut} words without dropping a requirement."
        ),
    )


def normalise(
    report: RedTeamReport,
    sections: Sequence[SectionFacts],
    *,
    requirement_ids: Iterable[str] = (),
    font_size_pt: float | None = None,
    margins: str | None = None,
    package_page_limit: int | None = None,
) -> NormalisedReport:
    """Make the model's report trustworthy: known ids only, computed page limits, and a
    missing-requirements list recomputed from the matrix rather than taken on faith."""
    known_sections = {s.section_id: s for s in sections}
    known_reqs = {str(r) for r in requirement_ids}
    warnings: list[str] = []
    unknown_sections: list[str] = []
    unknown_requirements: list[str] = []

    reviews: dict[str, SectionReview] = {}
    for review in report.sections:
        if review.section_id not in known_sections:
            unknown_sections.append(review.section_id)
            continue
        issues: list[Issue] = []
        for issue in review.issues:
            if issue.kind == ISSUE_PAGE_LIMIT:
                continue  # replaced by the deterministic estimate below
            req = issue.requirement_id
            if req and req not in known_reqs:
                unknown_requirements.append(req)
                issue = issue.model_copy(update={"requirement_id": None})
            issues.append(issue)
        reviews[review.section_id] = review.model_copy(update={"issues": issues})

    # --- deterministic page limits ---------------------------------------------------
    estimates: dict[str, PageEstimate] = {}
    for facts in sections:
        if not facts.has_version:
            continue
        estimate = estimate_pages(
            facts.body_text,
            font_size_pt=font_size_pt,
            margins=margins,
            limit=facts.page_budget,
        )
        estimates[facts.section_id] = estimate
        if estimate.over:
            review = reviews.get(facts.section_id) or SectionReview(
                section_id=facts.section_id, score=_mean([]), criterion_scores=[], issues=[]
            )
            review.issues.append(page_limit_issue(estimate, scope=f"Section {facts.title!r}"))
            reviews[facts.section_id] = review

    package: PageEstimate | None = None
    if package_page_limit:
        body = "\n\n".join(f.body_text for f in sections if f.has_version)
        package = estimate_pages(
            body, font_size_pt=font_size_pt, margins=margins, limit=package_page_limit
        )
        if package.over and estimates:
            # the section a reviewer would cut first: the longest one
            target = max(estimates, key=lambda sid: (estimates[sid].words, sid))
            review = reviews.get(target) or SectionReview(section_id=target, score=0)
            review.issues.append(page_limit_issue(package, scope="The package"))
            reviews[target] = review
            warnings.append(
                f"the package is {package.pages} pages against a {package_page_limit}-page limit"
            )

    # --- missing requirements ----------------------------------------------------------
    answered: set[str] = set()
    for facts in sections:
        if facts.has_version:
            answered.update(facts.maps_requirements)
    unmapped = sorted(known_reqs - answered)
    model_missing = sorted({r for r in report.missing_requirements if r in known_reqs})
    for req in report.missing_requirements:
        if req not in known_reqs:
            unknown_requirements.append(req)
    missing = sorted(set(unmapped) | set(model_missing))

    if unknown_sections:
        warnings.append(
            f"the review named sections that are not in the outline: {unknown_sections}"
        )
    if unknown_requirements:
        unknown = sorted(set(unknown_requirements))
        warnings.append(f"the review named requirements that were never extracted: {unknown}")
    reviewed = [reviews[s.section_id] for s in sections if s.section_id in reviews]
    missing_reviews = [
        s.section_id for s in sections if s.has_version and s.section_id not in reviews
    ]
    if missing_reviews:
        warnings.append(f"the review skipped drafted sections: {missing_reviews}")
    if missing:
        warnings.append(f"{len(missing)} requirement(s) are not answered by any drafted section")

    normalised = report.model_copy(
        update={
            "sections": reviewed,
            "missing_requirements": missing,
            "overall_score": report.overall_score or _mean([r.score for r in reviewed]),
        }
    )
    return NormalisedReport(
        report=normalised,
        estimates=estimates,
        package=package,
        warnings=warnings,
        unknown_sections=tuple(dict.fromkeys(unknown_sections)),
        unknown_requirements=tuple(dict.fromkeys(unknown_requirements)),
    )


# --- after the single auto-revision ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class RevisionCheck:
    """What is known about one section after its (single) revision."""

    revised: bool = False
    # indexes into the section's issue list the revision claims to have addressed
    addressed: frozenset[int] = frozenset()
    before_text: str = ""
    after_text: str = ""
    after_unsupported: int = 0
    estimate: PageEstimate | None = None


def issue_resolved(index: int, issue: Issue, check: RevisionCheck) -> bool:
    """Whether the revision really fixed the issue.

    A page limit and an unsupported sentence are checked against the new body; for the
    rest the revision's own claim is taken, because only the model can say whether a
    requirement is now answered -- and a claim that turns out wrong still leaves the
    issue visible in the stored report.
    """
    if not check.revised:
        return False
    if issue.kind == ISSUE_PAGE_LIMIT:
        return check.estimate is not None and not check.estimate.over
    if issue.kind == ISSUE_UNSUPPORTED_CLAIM:
        if issue.sentence:
            sentence = issue.sentence.strip()
            return bool(sentence) and sentence not in check.after_text
        return check.after_unsupported == 0
    return index in check.addressed


def remaining_issues(issues: Sequence[Issue], check: RevisionCheck) -> list[tuple[int, Issue]]:
    """The issues that survive the auto-revision and must become review comments."""
    return [(i, issue) for i, issue in enumerate(issues) if not issue_resolved(i, issue, check)]


def comment_body(section_title: str, issue: Issue) -> str:
    """The review comment a surviving issue becomes (SPEC 8: "remaining issues become
    review comments")."""
    parts = [f"Red team ({issue.kind}) on {section_title}"]
    if issue.requirement_id:
        parts.append(f"requirement {issue.requirement_id}")
    body = " - ".join(parts)
    if issue.sentence:
        body += f"\n\n> {issue.sentence.strip()}"
    return f"{body}\n\nSuggested fix: {issue.fix_suggestion.strip()}"


def flags_for_version(
    section_id: str, issues: Sequence[Issue], *, revised: bool, estimate: PageEstimate | None
) -> dict[str, Any]:
    """The `red_team` block merged into draft_versions.flags by services.drafts."""
    return {
        "red_team": {
            "section_id": section_id,
            "revised": revised,
            "issues": [issue.model_dump(mode="json") for issue in issues],
            "page_estimate": None if estimate is None else estimate.as_dict(),
        }
    }


def already_revised(flags: Mapping[str, Any] | None) -> bool:
    """True when a stored version is itself the red team's one auto-revision."""
    block = (flags or {}).get("red_team")
    return bool(isinstance(block, Mapping) and block.get("revised"))
