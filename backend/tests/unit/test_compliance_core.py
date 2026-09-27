"""M5-05: pure compliance logic: requirement -> section, format rules and the region's
submission checklist."""

from __future__ import annotations

from decimal import Decimal
from typing import get_args

import pytest
from app.agents.matrix import SectionName
from app.core.compliance import (
    SECTION_COVER,
    SECTION_ELIGIBILITY,
    SECTION_EXEC,
    SECTION_MANAGEMENT,
    SECTION_PAST_PERFORMANCE,
    SECTION_PRICE,
    SECTION_STAFFING,
    SECTION_SUBMISSION,
    SECTION_TECHNICAL,
    SECTIONS,
    Assignment,
    ChecklistContext,
    Req,
    apply_model_sections,
    assign_sections,
    build_checklist,
    citations,
    extract_format_rules,
    section_counts,
    section_for,
)


def _req(text: str, kind: str = "shall", volume: str | None = None, rid: str = "R-001") -> Req:
    return Req(req_id=rid, text=text, type=kind, volume=volume)


def test_volume_wins_over_type_and_keywords() -> None:
    cases = [
        ("Technical Volume", SECTION_TECHNICAL),
        ("Volume II - Past Performance", SECTION_PAST_PERFORMANCE),
        ("Cost/Price Volume", SECTION_PRICE),
        ("Cover Page", SECTION_COVER),
        ("Executive Summary", SECTION_EXEC),
        ("Staffing and Resumes", SECTION_STAFFING),
        ("Management Plan", SECTION_MANAGEMENT),
        ("Financial Bid", SECTION_PRICE),
    ]
    for volume, section in cases:
        got = section_for(_req("Anything at all.", volume=volume))
        assert (got.section, got.reason, got.ambiguous) == (section, "volume", False), volume
    # a volume the map does not know falls through to the type/keyword rules
    assert section_for(_req("Provide a widget.", volume="Annex Z")).reason != "volume"


def test_type_decides_eligibility_format_and_submission() -> None:
    for kind, section in (
        ("eligibility", SECTION_ELIGIBILITY),
        ("format", SECTION_SUBMISSION),
        ("submission", SECTION_SUBMISSION),
    ):
        got = section_for(_req("Respondents must be registered in SAM.gov.", kind))
        assert (got.section, got.reason, got.ambiguous) == (section, "type", False)


def test_keywords_place_obligations_and_evaluation_criteria() -> None:
    cases = [
        ("All personnel must complete background investigations.", "must", SECTION_STAFFING),
        (
            "Responses are reviewed for demonstrated experience migrating 200 workloads.",
            "evaluation",
            SECTION_PAST_PERFORMANCE,
        ),
        ("Provide a transition-out plan 90 days before contract end.", "shall", SECTION_MANAGEMENT),
        ("Invoice monthly in arrears against the agreed rate card.", "shall", SECTION_PRICE),
        ("Maintain a Recovery Time Objective of four hours.", "shall", SECTION_TECHNICAL),
    ]
    for text, kind, section in cases:
        got = section_for(_req(text, kind))
        assert (got.section, got.reason, got.ambiguous) == (section, "keyword", False), text


def test_undecidable_obligations_are_marked_ambiguous_with_a_default() -> None:
    vague = section_for(_req("The contractor shall comply with clause 7.4.", "shall"))
    assert vague.section == SECTION_TECHNICAL and vague.reason == "default" and vague.ambiguous
    evaluation = section_for(_req("Factors are of equal weight.", "evaluation"))
    assert evaluation.ambiguous
    # a type nothing covers is not sent to the model: it keeps the default silently
    other = section_for(_req("Anything.", "note"))
    assert other.section == SECTION_TECHNICAL and not other.ambiguous


def test_model_answers_only_override_ambiguous_assignments() -> None:
    reqs = [
        _req("The contractor shall comply with clause 7.4.", "shall", rid="R-001"),
        _req("Respondents must be registered in SAM.gov.", "eligibility", rid="R-002"),
        _req("All personnel must hold clearances.", "must", rid="R-003"),
    ]
    assignments = assign_sections(reqs)
    assert [a.ambiguous for a in assignments] == [True, False, False]
    merged = apply_model_sections(
        assignments,
        {
            "R-001": SECTION_MANAGEMENT,  # ambiguous: taken
            "R-002": SECTION_PRICE,  # not ambiguous: ignored
            "R-003": SECTION_PRICE,  # not ambiguous: ignored
            "R-404": SECTION_PRICE,  # unknown req: ignored
        },
    )
    assert [(a.section, a.reason) for a in merged] == [
        (SECTION_MANAGEMENT, "model"),
        (SECTION_ELIGIBILITY, "type"),
        (SECTION_STAFFING, "keyword"),
    ]
    # an invented section never lands in the matrix
    invented = apply_model_sections(assignments, {"R-001": "Appendix Q"})
    assert invented[0].section == SECTION_TECHNICAL and invented[0].reason == "default"
    assert section_counts(merged) == {
        SECTION_MANAGEMENT: 1,
        SECTION_ELIGIBILITY: 1,
        SECTION_STAFFING: 1,
    }
    assert section_counts([]) == {}


def test_the_model_schema_offers_exactly_the_core_sections() -> None:
    assert set(get_args(SectionName)) == set(SECTIONS)


FORMAT_REQS = [
    _req("Responses shall not exceed 10 pages, excluding the cover page.", "format", rid="R-011"),
    _req(
        "Use 12-point Times New Roman font with one-inch margins on letter-size paper.",
        "format",
        rid="R-012",
    ),
    _req("Submit the capability statement as a single PDF file.", "format", rid="R-013"),
    _req("File names shall follow the pattern CompanyName_IRS_SS_0042.pdf.", "format", rid="R-014"),
    _req(
        "Responses must be emailed to market.research@irs.example.gov and uploaded to SAM.gov.",
        "submission",
        rid="R-016",
    ),
    _req("Provide 3 hard copies at the pre-bid meeting.", "submission", rid="R-017"),
]


def test_extract_format_rules_reads_every_field_and_cites_it() -> None:
    rules = extract_format_rules(FORMAT_REQS)
    assert rules.page_limit == 10
    assert rules.font == "Times New Roman" and rules.font_size_pt == 12
    assert rules.margins == "one-inch margins"
    assert rules.file_types == ["PDF"]
    assert rules.file_naming == "CompanyName_IRS_SS_0042.pdf"
    assert rules.copies == 3
    assert rules.portal == "SAM.gov"
    assert rules.email == "market.research@irs.example.gov"
    assert rules.submission_method == "email"
    assert rules.sources == {
        "page_limit": "R-011",
        "font": "R-012",
        "font_size_pt": "R-012",
        "margins": "R-012",
        "file_types": "R-013",
        "file_naming": "R-014",
        "email": "R-016",
        "portal": "R-016",
        "copies": "R-017",
    }


def test_format_rules_invent_nothing_and_accept_other_phrasings() -> None:
    empty = extract_format_rules([])
    assert empty.model_dump() == {
        "page_limit": None,
        "font": None,
        "font_size_pt": None,
        "margins": None,
        "file_types": [],
        "file_naming": None,
        "copies": None,
        "portal": None,
        "submission_method": None,
        "email": None,
        "sources": {},
    }
    other = extract_format_rules(
        [
            _req("The technical bid has a 25-page limit.", "format"),
            _req("Set the body text in Arial 11 point with 2.5 cm margins.", "format"),
            _req("Upload the signed DOCX and XLSX covers on eprocure.gov.in.", "submission"),
        ]
    )
    assert other.page_limit == 25 and other.font == "Arial" and other.font_size_pt == 11
    assert other.margins == "2.5 cm margins" and other.file_types == ["DOCX", "XLSX"]
    assert other.portal == "CPPP (eprocure.gov.in)" and other.submission_method == "portal"
    assert other.email is None
    # the source URL is only a fallback for the portal
    hinted = extract_format_rules([_req("Respond by the due date.", "submission")], portal_hint="x")
    assert hinted.portal == "x" and "portal" not in hinted.sources
    # with no format/submission requirements at all, every requirement is scanned
    fallback = extract_format_rules([_req("Proposals shall not exceed 5 pages.", "shall")])
    assert fallback.page_limit == 5


US_CTX = ChecklistContext(region="us", notice_type="rfp", source_id="sam_opps")


def test_us_checklist_carries_the_standard_forms_and_registrations() -> None:
    items = {i.key: i for i in build_checklist(US_CTX, [])}
    assert set(items) == {
        "sam_registration",
        "reps_certs",
        "sf_33",
        "sf_1449",
        "set_aside_eligibility",
        "capability_statement",
    }
    assert items["sam_registration"].required and items["reps_certs"].required
    # an RFP needs SF-33, not SF-1449
    assert items["sf_33"].required and not items["sf_1449"].required
    assert items["sf_33"].note is None
    assert not items["set_aside_eligibility"].required
    assert not items["capability_statement"].required

    quote = {
        i.key: i for i in build_checklist(US_CTX.model_copy(update={"notice_type": "rfq"}), [])
    }
    assert quote["sf_1449"].required and not quote["sf_33"].required

    research = build_checklist(
        US_CTX.model_copy(update={"notice_type": "sources_sought", "set_aside": "small_business"}),
        [],
    )
    by_key = {i.key: i for i in research}
    assert by_key["capability_statement"].required
    assert by_key["set_aside_eligibility"].required
    assert "small_business" in (by_key["set_aside_eligibility"].note or "")
    # neither standard form applies to a sources-sought notice, so both say to check
    assert not by_key["sf_33"].required and by_key["sf_33"].note is not None


def test_india_checklist_covers_emd_bg_dsc_affidavit_and_turnover() -> None:
    ctx = ChecklistContext(
        region="in",
        notice_type="gem_bid",
        source_id="gem_bids",
        currency="INR",
        emd_amount=Decimal("250000"),
        tender_fee=Decimal("1000"),
    )
    items = {i.key: i for i in build_checklist(ctx, [])}
    assert set(items) == {
        "dsc",
        "dsc_signed_covers",
        "emd",
        "bank_guarantee",
        "tender_fee",
        "turnover_certificate",
        "affidavit",
        "power_of_attorney",
        "pan_gst",
        "gem_seller",
        "cppp_enrolment",
        "msme_udyam",
    }
    assert items["dsc"].required and items["dsc_signed_covers"].required
    assert "private key" in (items["dsc"].note or "")
    assert items["emd"].required and "₹2,50,000" in (items["emd"].note or "")
    assert items["tender_fee"].required and "₹1,000" in (items["tender_fee"].note or "")
    assert items["turnover_certificate"].required and items["affidavit"].required
    assert items["power_of_attorney"].required and items["pan_gst"].required
    assert items["gem_seller"].required and not items["cppp_enrolment"].required
    assert not items["msme_udyam"].required and not items["bank_guarantee"].required

    cppp = {
        i.key: i
        for i in build_checklist(
            ctx.model_copy(update={"source_id": "cppp", "emd_amount": None, "tender_fee": None}), []
        )
    }
    assert cppp["cppp_enrolment"].required and not cppp["gem_seller"].required
    assert not cppp["emd"].required and cppp["emd"].note is None
    assert not cppp["tender_fee"].required

    with pytest.raises(ValueError):
        build_checklist(ctx.model_copy(update={"region": "eu"}), [])


def test_requirements_cite_checklist_items_and_promote_conditional_ones() -> None:
    reqs = [
        _req("Complete and sign SF-1449 block 17.", "submission", rid="R-001"),
        _req("Furnish EMD of INR 2,50,000 or a bank guarantee.", "eligibility", rid="R-002"),
        _req("Bidders must not be blacklisted by any government body.", "eligibility", rid="R-003"),
        _req("Average annual turnover of INR 5 crore is required.", "eligibility", rid="R-004"),
        _req("Covers must be signed with a Class 3 DSC.", "submission", rid="R-005"),
        _req("The debugging log is not part of the bid.", "shall", rid="R-006"),
    ]
    cited = citations(reqs)
    assert cited["sf_1449"] == ["R-001"] and cited["emd"] == ["R-002"]
    assert cited["bank_guarantee"] == ["R-002"] and cited["affidavit"] == ["R-003"]
    assert cited["turnover_certificate"] == ["R-004"] and cited["dsc"] == ["R-005"]
    # "bg" must not match inside "debugging"
    assert "R-006" not in cited.get("bank_guarantee", [])

    us = {i.key: i for i in build_checklist(US_CTX, reqs)}
    # a cited conditional item becomes required; an always-optional one does not
    assert us["sf_1449"].required and us["sf_1449"].source_req_ids == ["R-001"]
    india = {i.key: i for i in build_checklist(ChecklistContext(region="in", currency="INR"), reqs)}
    assert india["emd"].required and india["bank_guarantee"].required
    assert india["affidavit"].source_req_ids == ["R-003"]
    assert not india["msme_udyam"].required  # never promoted by a citation


def test_assignment_is_hashable_and_frozen() -> None:
    item = Assignment("R-001", SECTION_PRICE, "volume")
    assert hash(item) and item.req_id == "R-001"
    with pytest.raises(AttributeError):
        item.section = SECTION_COVER  # type: ignore[misc]
