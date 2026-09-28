"""M5-10: the pure part of the red-team review -- normalising the model's report and
deciding which issues survive the single auto-revision."""

from __future__ import annotations

from app.core.page_estimate import estimate_pages
from app.core.red_team import (
    ISSUE_CLARITY,
    ISSUE_NON_COMPLIANT,
    ISSUE_PAGE_LIMIT,
    ISSUE_UNSUPPORTED_CLAIM,
    Issue,
    RedTeamReport,
    RevisionCheck,
    SectionFacts,
    SectionReview,
    already_revised,
    comment_body,
    flags_for_version,
    normalise,
    remaining_issues,
)


def words(n: int, prefix: str = "word") -> str:
    return " ".join(f"{prefix}{i}" for i in range(n))


def facts(section_id: str = "technical-approach", **kwargs: object) -> SectionFacts:
    base: dict[str, object] = {
        "section_id": section_id,
        "title": "Technical Approach",
        "body_text": "We will migrate the workloads. [KB:past_performance:x#1]",
        "maps_requirements": ("R-001",),
        "has_version": True,
    }
    base.update(kwargs)
    return SectionFacts(**base)  # type: ignore[arg-type]


def issue(kind: str = ISSUE_NON_COMPLIANT, **kwargs: object) -> Issue:
    base: dict[str, object] = {"kind": kind, "fix_suggestion": "answer R-001 explicitly"}
    base.update(kwargs)
    return Issue(**base)  # type: ignore[arg-type]


def report(*reviews: SectionReview, **kwargs: object) -> RedTeamReport:
    return RedTeamReport(sections=list(reviews), **kwargs)  # type: ignore[arg-type]


# --- normalise ---------------------------------------------------------------------------


def test_a_clean_report_survives_untouched() -> None:
    review = SectionReview(section_id="technical-approach", score=82, issues=[issue()])
    out = normalise(report(review, overall_score=80), [facts()], requirement_ids=["R-001"])
    assert out.warnings == []
    assert [s.section_id for s in out.report.sections] == ["technical-approach"]
    assert out.report.sections[0].score == 82
    assert out.report.overall_score == 80
    assert out.report.missing_requirements == []


def test_a_review_of_a_section_that_is_not_in_the_outline_is_dropped() -> None:
    out = normalise(
        report(SectionReview(section_id="invented", score=10, issues=[issue()])),
        [facts()],
        requirement_ids=["R-001"],
    )
    assert out.report.sections == []
    assert out.unknown_sections == ("invented",)
    assert any("not in the outline" in w for w in out.warnings)
    assert any("skipped drafted sections" in w for w in out.warnings)


def test_an_invented_requirement_id_is_stripped_from_the_issue_but_the_issue_stays() -> None:
    review = SectionReview(
        section_id="technical-approach", score=50, issues=[issue(requirement_id="R-404")]
    )
    out = normalise(report(review), [facts()], requirement_ids=["R-001"])
    assert out.unknown_requirements == ("R-404",)
    kept = out.report.sections[0].issues[0]
    assert kept.requirement_id is None and kept.kind == ISSUE_NON_COMPLIANT
    assert any("never extracted" in w for w in out.warnings)


def test_the_models_page_limit_opinion_is_replaced_by_the_measurement() -> None:
    review = SectionReview(
        section_id="technical-approach",
        score=60,
        issues=[issue(ISSUE_PAGE_LIMIT, fix_suggestion="feels long, trim it")],
    )
    out = normalise(
        report(review), [facts(body_text=words(300), page_budget=2)], requirement_ids=["R-001"]
    )
    # the section fits, so the model's hunch produced no issue at all
    assert out.report.sections[0].issues == []
    assert out.estimates["technical-approach"].pages == 0.6


def test_a_measured_overrun_becomes_an_issue_even_when_the_model_missed_it() -> None:
    out = normalise(
        report(SectionReview(section_id="technical-approach", score=90)),
        [facts(body_text=words(1600), page_budget=2)],
        requirement_ids=["R-001"],
    )
    overrun = out.report.sections[0].issues[0]
    assert overrun.kind == ISSUE_PAGE_LIMIT
    assert "3.2 pages against a 2-page limit" in overrun.fix_suggestion
    assert "cut about 600 words" in overrun.fix_suggestion


def test_the_font_and_margin_rules_change_the_measurement() -> None:
    sections = [facts(body_text=words(1300), page_budget=2)]
    tight = normalise(report(), sections, requirement_ids=["R-001"])
    loose = normalise(
        report(), sections, requirement_ids=["R-001"], font_size_pt=10, margins="0.5 inch"
    )
    assert tight.report.sections and tight.report.sections[0].issues[0].kind == ISSUE_PAGE_LIMIT
    assert loose.report.sections == []


def test_a_package_overrun_lands_on_the_longest_section() -> None:
    sections = [
        facts("exec-summary", title="Executive Summary", body_text=words(200)),
        facts("technical-approach", body_text=words(1500)),
    ]
    out = normalise(report(), sections, requirement_ids=["R-001"], package_page_limit=2)
    assert out.package is not None and out.package.over
    hit = {s.section_id: s.issues for s in out.report.sections}
    assert [i.kind for i in hit["technical-approach"]] == [ISSUE_PAGE_LIMIT]
    assert "The package is about 3.4 pages" in hit["technical-approach"][0].fix_suggestion
    assert "exec-summary" not in hit
    assert any("the package is 3.4 pages" in w for w in out.warnings)


def test_missing_requirements_are_recomputed_from_the_matrix() -> None:
    sections = [facts(maps_requirements=("R-001",)), facts("pricing", has_version=False)]
    # the model claims R-001 is missing (it is answered) and invents R-404; R-002 really
    # is answered by nothing that has a draft
    out = normalise(
        report(missing_requirements=["R-404"]),
        sections,
        requirement_ids=["R-001", "R-002"],
    )
    assert out.report.missing_requirements == ["R-002"]
    assert "R-404" in out.unknown_requirements
    assert any("not answered by any drafted section" in w for w in out.warnings)


def test_a_section_without_a_draft_is_not_measured() -> None:
    out = normalise(report(), [facts(has_version=False, body_text=words(5000))])
    assert out.estimates == {}
    assert out.report.sections == []


def test_an_absent_overall_score_falls_back_to_the_mean_of_the_sections() -> None:
    reviews = (
        SectionReview(section_id="technical-approach", score=80),
        SectionReview(section_id="exec-summary", score=62),
    )
    sections = [facts(), facts("exec-summary", title="Executive Summary")]
    assert normalise(report(*reviews), sections).report.overall_score == 71


# --- remaining issues after the one revision -------------------------------------------------


def test_nothing_is_resolved_when_the_section_was_not_revised() -> None:
    issues = [issue(), issue(ISSUE_CLARITY)]
    surviving = remaining_issues(issues, RevisionCheck(revised=False, addressed=frozenset({0, 1})))
    assert [i for i, _ in surviving] == [0, 1]


def test_a_page_limit_is_resolved_only_when_the_new_text_fits() -> None:
    issues = [issue(ISSUE_PAGE_LIMIT, fix_suggestion="cut 600 words")]
    still_long = RevisionCheck(revised=True, estimate=estimate_pages(words(1600), limit=2))
    now_short = RevisionCheck(revised=True, estimate=estimate_pages(words(400), limit=2))
    assert len(remaining_issues(issues, still_long)) == 1
    assert remaining_issues(issues, now_short) == []


def test_an_unsupported_sentence_is_resolved_only_when_it_is_gone() -> None:
    sentence = "We hold ISO 27001 certification."
    issues = [issue(ISSUE_UNSUPPORTED_CLAIM, sentence=sentence)]
    kept = RevisionCheck(
        revised=True, after_text=f"Intro. {sentence} Outro.", addressed=frozenset({0})
    )
    gone = RevisionCheck(revised=True, after_text="Intro. Outro.", addressed=frozenset())
    assert len(remaining_issues(issues, kept)) == 1, "the model claiming a fix is not enough"
    assert remaining_issues(issues, gone) == []


def test_an_unsupported_claim_without_a_sentence_falls_back_to_the_grounding_count() -> None:
    issues = [issue(ISSUE_UNSUPPORTED_CLAIM)]
    assert remaining_issues(issues, RevisionCheck(revised=True, after_unsupported=2))
    assert remaining_issues(issues, RevisionCheck(revised=True, after_unsupported=0)) == []


def test_the_other_kinds_take_the_revisions_word_for_it() -> None:
    issues = [issue(ISSUE_NON_COMPLIANT), issue(ISSUE_CLARITY)]
    check = RevisionCheck(revised=True, addressed=frozenset({0}))
    assert [i for i, _ in remaining_issues(issues, check)] == [1]


# --- comments and flags ---------------------------------------------------------------------


def test_a_comment_carries_the_kind_the_requirement_the_sentence_and_the_fix() -> None:
    body = comment_body(
        "Technical Approach",
        issue(ISSUE_UNSUPPORTED_CLAIM, requirement_id="R-001", sentence="We saved 40%."),
    )
    assert "Red team (unsupported_claim) on Technical Approach" in body
    assert "requirement R-001" in body
    assert "> We saved 40%." in body
    assert body.endswith("Suggested fix: answer R-001 explicitly")


def test_flags_mark_the_version_as_the_one_auto_revision() -> None:
    flags = flags_for_version(
        "technical-approach", [issue()], revised=True, estimate=estimate_pages(words(10), limit=1)
    )
    assert already_revised(flags)
    assert flags["red_team"]["issues"][0]["kind"] == ISSUE_NON_COMPLIANT
    assert flags["red_team"]["page_estimate"]["limit"] == 1
    assert not already_revised(None)
    assert not already_revised({"unsupported_count": 1})
    assert not already_revised(flags_for_version("s", [], revised=False, estimate=None))
