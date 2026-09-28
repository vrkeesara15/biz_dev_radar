"""M5-07: the pure outline validator (app.core.outline) and citation tokens."""

from __future__ import annotations

import uuid

import pytest
from app.core.citations import (
    Citation,
    find_tokens,
    kb_token,
    parse_token,
    profile_token,
    strip_tokens,
)
from app.core.outline import (
    MAX_WIN_THEMES,
    MIN_WIN_THEMES,
    Outline,
    OutlineSection,
    OutlineVolume,
    WinTheme,
    mentions_section_l_m,
    normalise,
)

PP = uuid.uuid4()
CERT = uuid.uuid4()
PP_TOKEN = profile_token("past_performance", PP)
CERT_TOKEN = profile_token("certification", CERT)


def _outline(**overrides: object) -> Outline:
    base = Outline(
        volumes=[
            OutlineVolume(
                name="Volume I - Technical",
                sections=[
                    OutlineSection(
                        id="technical-approach",
                        title="Technical Approach",
                        maps_requirements=["R-001", "R-002"],
                        evaluation_criterion="Factor 1 - Technical",
                        page_budget=6,
                    ),
                    OutlineSection(
                        id="management-plan",
                        title="Management Plan",
                        maps_requirements=["R-003"],
                        page_budget=4,
                    ),
                ],
            )
        ],
        win_themes=[
            WinTheme(
                theme="Zero-downtime migration",
                discriminator="We migrated 400 workloads without an outage",
                evidence_citations=[PP_TOKEN],
            ),
            WinTheme(
                theme="Cleared staff on day one",
                discriminator="Our team already holds the clearances",
                evidence_citations=[CERT_TOKEN],
            ),
            WinTheme(
                theme="Fixed-price certainty",
                discriminator="Our rate card is public",
                evidence_citations=[PP_TOKEN, CERT_TOKEN],
            ),
        ],
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


ALL_TOKENS = {PP_TOKEN, CERT_TOKEN}


# --- citation tokens ---------------------------------------------------------------------


def test_tokens_round_trip() -> None:
    token = kb_token("past_performance", PP, 2)
    assert token == f"KB:past_performance:{PP}#2"
    parsed = parse_token(token)
    assert parsed == Citation("KB", "past_performance", PP, 2)
    assert parsed is not None and parsed.bracketed == f"[{token}]"
    assert parse_token(f"[ {PP_TOKEN} ]") == Citation("PROFILE", "past_performance", PP, None)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "KB:past_performance",
        "KB:past_performance:not-a-uuid#1",
        f"NOPE:past_performance:{PP}",
        f"KB:Past Performance:{PP}#1",
    ],
)
def test_malformed_tokens_do_not_parse(raw: str) -> None:
    assert parse_token(raw) is None


def test_find_and_strip_tokens_in_body_text() -> None:
    body = (
        f"We migrated 400 workloads [KB:past_performance:{PP}#2] on time "
        f"and hold ISO 9001 [{CERT_TOKEN}]."
    )
    assert find_tokens(body) == [f"KB:past_performance:{PP}#2", CERT_TOKEN]
    assert strip_tokens(body) == "We migrated 400 workloads on time and hold ISO 9001 ."
    assert find_tokens("no citations here") == []


# --- outline validation ------------------------------------------------------------------


def test_a_complete_outline_has_no_warnings() -> None:
    plan, report = normalise(
        _outline(),
        req_ids=["R-001", "R-002", "R-003"],
        region="us",
        page_limit=10,
        evidence_tokens=ALL_TOKENS,
    )
    assert report.ok and report.warnings == []
    assert report.mapped == ("R-001", "R-002", "R-003") and report.unmapped == ()
    assert plan.unmapped_requirements == []
    assert plan.section_ids() == ["technical-approach", "management-plan"]


def test_unmapped_requirements_are_recomputed_not_trusted() -> None:
    plan, report = normalise(
        _outline(unmapped_requirements=["R-999"]),  # the model's own list is ignored
        req_ids=["R-001", "R-002", "R-003", "R-004"],
        region="us",
        evidence_tokens=ALL_TOKENS,
    )
    assert report.unmapped == ("R-004",)
    assert plan.unmapped_requirements == ["R-004"]
    assert any("R-004" in w and "not mapped" in w for w in report.warnings)


def test_invented_requirement_references_are_dropped() -> None:
    outline = _outline()
    outline.volumes[0].sections[0].maps_requirements = ["R-001", "R-404", "r-002"]
    plan, report = normalise(
        outline, req_ids=["R-001", "R-002", "R-003"], region="us", evidence_tokens=ALL_TOKENS
    )
    assert report.unknown_requirements == ("R-404",)
    assert plan.volumes[0].sections[0].maps_requirements == ["R-001", "R-002"]  # case-normalised
    assert any("do not exist" in w for w in report.warnings)


def test_win_theme_citations_must_resolve_to_a_profile_record() -> None:
    outline = _outline()
    outline.win_themes[0].evidence_citations = [
        PP_TOKEN,
        profile_token("past_performance", uuid.uuid4()),
    ]
    plan, report = normalise(
        outline, req_ids=["R-001", "R-002", "R-003"], region="us", evidence_tokens=ALL_TOKENS
    )
    assert len(report.dropped_citations) == 1
    assert plan.win_themes[0].evidence_citations == [PP_TOKEN]
    assert any("resolve to no profile record" in w for w in report.warnings)


def test_a_theme_with_no_evidence_is_flagged() -> None:
    outline = _outline()
    outline.win_themes[2].evidence_citations = []
    _, report = normalise(
        outline, req_ids=["R-001", "R-002", "R-003"], region="us", evidence_tokens=ALL_TOKENS
    )
    assert any("cite no profile record" in w for w in report.warnings)


def test_win_theme_count_is_checked() -> None:
    outline = _outline()
    outline.win_themes = outline.win_themes[:1]
    _, report = normalise(outline, req_ids=[], region="us", evidence_tokens=ALL_TOKENS)
    assert any(f"{MIN_WIN_THEMES}-{MAX_WIN_THEMES}" in w for w in report.warnings)


def test_duplicate_section_ids_are_made_unique_and_slugged() -> None:
    outline = _outline()
    outline.volumes[0].sections[1].id = "Technical Approach"
    plan, report = normalise(
        outline, req_ids=["R-001", "R-002", "R-003"], region="us", evidence_tokens=ALL_TOKENS
    )
    assert plan.section_ids() == ["technical-approach", "technical-approach-2"]
    assert report.renamed_sections == ("Technical Approach -> technical-approach-2",)


def test_page_budget_over_the_limit_warns() -> None:
    _, report = normalise(
        _outline(),
        req_ids=["R-001", "R-002", "R-003"],
        region="us",
        page_limit=5,
        evidence_tokens=ALL_TOKENS,
    )
    assert any("page budgets add up to 10" in w for w in report.warnings)


def test_india_needs_a_technical_and_a_financial_cover() -> None:
    _, report = normalise(
        _outline(), req_ids=["R-001", "R-002", "R-003"], region="in", evidence_tokens=ALL_TOKENS
    )
    assert any("no financial cover" in w for w in report.warnings)
    covers = Outline(
        volumes=[
            OutlineVolume(name="Technical Bid (Cover 1)", sections=[]),
            OutlineVolume(name="Financial Bid (Cover 2)", sections=[]),
        ],
        win_themes=_outline().win_themes,
    )
    _, ok = normalise(covers, req_ids=[], region="in", evidence_tokens=ALL_TOKENS)
    assert ok.warnings == []


def test_an_empty_outline_warns() -> None:
    _, report = normalise(Outline(), req_ids=["R-001"], region="us")
    assert any("no volumes" in w for w in report.warnings)
    assert any("not mapped" in w for w in report.warnings)


@pytest.mark.parametrize(
    ("texts", "expected"),
    [
        (["Submit per Section L instructions."], True),
        (["Evaluation factors for award are listed in Section M."], True),
        (["instructions to offerors follow"], True),
        (["Submit the technical bid in cover 1."], False),
        ([], False),
    ],
)
def test_section_l_m_detection(texts: list[str], expected: bool) -> None:
    assert mentions_section_l_m(texts) is expected
