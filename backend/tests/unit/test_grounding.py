"""M5-11: the grounding validator (app.core.grounding) -- 100% of the module."""

from __future__ import annotations

import uuid

import pytest
from app.core.citations import kb_token, profile_token
from app.core.grounding import (
    REASON_CERTIFICATION,
    REASON_NAME,
    REASON_NUMBERS,
    REASON_PAST_PERFORMANCE,
    REASON_UNSUPPORTED_CLAIM,
    REASONS,
    Flag,
    GroundingReport,
    cited_tokens,
    is_heading,
    split_lines,
    split_sentences,
    validate,
)

PP = uuid.uuid4()
PP_TOKEN = profile_token("past_performance", PP)
KB_TOKEN = kb_token("boilerplate", uuid.uuid4(), 0)
RESOLVABLE = {PP_TOKEN, KB_TOKEN}
FACTS = ("Alpha Federal LLC", "US Treasury", "iso 9001")


def reasons(report: GroundingReport) -> set[str]:
    return {flag.reason for flag in report.flags}


# --- splitting ---------------------------------------------------------------------------


def test_split_lines_marks_bullets_and_drops_blanks() -> None:
    assert split_lines("  one  \n\n- two\n* three\n• four\n-   \n") == [
        ("one", False),
        ("two", True),
        ("three", True),
        ("four", True),
    ]
    assert split_lines("") == []


def test_split_sentences() -> None:
    assert split_sentences("A first one. And a second! A third?\n- bullet text") == [
        "A first one.",
        "And a second!",
        "A third?",
        "bullet text",
    ]


@pytest.mark.parametrize(
    ("line", "bullet", "expected"),
    [
        ("Technical Approach", False, True),
        ("Past Performance", False, True),
        ("We employ 250 engineers.", False, False),
        ("ISO 9001 certified", True, False),  # a bullet is a claim however short
        (
            "A heading this long with more than eight words is not a heading",
            False,
            False,
        ),
    ],
)
def test_is_heading(line: str, bullet: bool, expected: bool) -> None:
    assert is_heading(line, bullet) is expected


def test_cited_tokens_splits_resolvable_from_invented() -> None:
    ghost = profile_token("certification", uuid.uuid4())
    resolved, unresolved = cited_tokens(f"text [{PP_TOKEN}] more [{ghost}]", RESOLVABLE)
    assert resolved == [PP_TOKEN] and unresolved == [ghost]


# --- supported / unsupported / placeholder -------------------------------------------------


def test_a_cited_sentence_is_supported() -> None:
    report = validate(
        f"We migrated 400 workloads for the US Treasury [{PP_TOKEN}].",
        resolvable_tokens=RESOLVABLE,
        profile_facts=FACTS,
    )
    assert report.ok and report.flags == []
    assert (report.supported_count, report.unsupported_count) == (1, 0)
    assert report.sentences == 1


def test_a_citation_from_the_stored_list_also_counts() -> None:
    other = kb_token("past_performance", uuid.uuid4(), 3)
    report = validate(
        f"Our uptime was 99.98% last year [{other}].",
        citations=[{"token": other, "quote": "uptime"}],
        resolvable_tokens=set(),
    )
    assert report.supported_count == 1 and report.flags == []


def test_numbers_without_a_citation_are_flagged() -> None:
    report = validate("We employ 250 engineers across 4 sites.", resolvable_tokens=RESOLVABLE)
    assert reasons(report) == {REASON_NUMBERS}
    assert report.unsupported_count == 1 and report.supported_count == 0
    assert report.flags[0].detail.startswith("250")
    assert report.flags[0].as_dict()["reason"] == REASON_NUMBERS


@pytest.mark.parametrize(
    "sentence",
    [
        "Delivery starts in 2026.",
        "See paragraph 3.",
    ],
)
def test_a_bare_year_or_small_reference_is_not_a_claim(sentence: str) -> None:
    assert validate(sentence, resolvable_tokens=RESOLVABLE).flags == []


def test_certifications_are_flagged_without_a_citation() -> None:
    report = validate("We are CMMI Level 3 appraised and FedRAMP authorised.")
    assert REASON_CERTIFICATION in reasons(report)
    assert any(f.detail == "cmmi" for f in report.flags)


def test_past_performance_language_is_flagged() -> None:
    report = validate("We delivered a similar programme last year.")
    assert REASON_PAST_PERFORMANCE in reasons(report)


def test_marketing_superlatives_are_flagged() -> None:
    report = validate("We are the industry-leading provider with a proven track record.")
    assert REASON_UNSUPPORTED_CLAIM in reasons(report)


def test_third_party_names_are_flagged_but_the_company_itself_is_not() -> None:
    own = validate("Alpha Federal LLC is pleased to respond.", profile_facts=FACTS)
    assert own.flags == []
    third = validate("Northstar Systems will subcontract the help desk.", profile_facts=FACTS)
    assert REASON_NAME in reasons(third)
    assert third.flags[0].detail == "Northstar Systems"


def test_a_known_customer_name_is_not_a_third_party_flag() -> None:
    assert validate("Work for the US Treasury continues.", profile_facts=FACTS).flags == []


def test_part_of_a_known_fact_is_still_the_company_itself() -> None:
    # "Alpha Federal" is part of the profile's legal name, not a third party
    assert validate("Alpha Federal runs the programme.", profile_facts=FACTS).flags == []


def test_sentence_initial_capitals_alone_are_not_a_name() -> None:
    assert validate("The system will be monitored.", profile_facts=FACTS).flags == []
    assert validate("Our Engineers work remotely.", profile_facts=FACTS).flags == []


def test_a_single_capitalised_org_suffix_word_still_counts() -> None:
    report = validate("Contoso Ltd will host the platform.", profile_facts=FACTS)
    assert REASON_NAME in reasons(report)


def test_a_placeholder_sentence_is_neither_supported_nor_flagged() -> None:
    report = validate(
        "[NEEDS INPUT: how many engineers hold clearances?] We employ 250 engineers.",
        resolvable_tokens=RESOLVABLE,
    )
    assert report.placeholder_count == 1
    assert report.supported_count == 0 and report.unsupported_count == 0
    assert report.flags == []


def test_headings_are_skipped_and_bullets_are_not() -> None:
    body = "Technical Approach\n\n- We hold ISO 27001 certification\nStaffing Plan"
    report = validate(body)
    assert report.sentences == 1  # both headings are skipped, the bullet is not
    assert REASON_CERTIFICATION in reasons(report)
    assert {f.sentence for f in report.flags} == {"We hold ISO 27001 certification"}


def test_one_sentence_can_raise_several_reasons_but_counts_once() -> None:
    report = validate(
        "We delivered 400 workloads for Northstar Systems under our CMMI Level 5 appraisal."
    )
    assert reasons(report) == {
        REASON_NUMBERS,
        REASON_PAST_PERFORMANCE,
        REASON_CERTIFICATION,
        REASON_NAME,
    }
    assert report.unsupported_count == 1
    assert {f.sentence for f in report.flags} == {
        "We delivered 400 workloads for Northstar Systems under our CMMI Level 5 appraisal."
    }


def test_unresolved_tokens_are_reported_once() -> None:
    ghost = profile_token("certification", uuid.uuid4())
    report = validate(
        f"We are ISO 9001 certified [{ghost}]. Our QMS is audited [{ghost}].",
        resolvable_tokens=RESOLVABLE,
    )
    assert report.unresolved_tokens == (ghost,)  # the same invented token, listed once
    # neither sentence is cited; only the one that asserts something is flagged
    assert report.supported_count == 0 and report.unsupported_count == 1
    assert report.flags[0].sentence.startswith("We are ISO 9001 certified")


def test_empty_body_is_an_empty_report() -> None:
    report = validate("")
    assert report.as_dict() == {
        "flags": [],
        "supported_count": 0,
        "unsupported_count": 0,
        "placeholder_count": 0,
        "sentences": 0,
        "unresolved_tokens": [],
    }
    assert report.ok


def test_reason_vocabulary_is_stable() -> None:
    assert REASONS == ("numbers", "name", "certification", "past_performance", "unsupported_claim")
    assert Flag("s", REASON_NAME).as_dict() == {"sentence": "s", "reason": "name", "detail": ""}
