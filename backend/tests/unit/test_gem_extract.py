"""M3-04: the GeM extraction schema (page citations), the untrusted bundle and the call."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.agents.gem_extract import (
    CITED_FIELDS,
    MAX_PAGE_CHARS,
    MAX_PAGES,
    GemBidExtraction,
    bid_document_bundle,
    extract_gem_bid,
)
from app.agents.prompting import UNTRUSTED_PREAMBLE
from app.core.config import Settings
from app.core.eligibility_in import CriteriaIn
from pydantic import ValidationError

from tests.llm_fake import FakeLLM

SETTINGS = Settings(_env_file=None)  # type: ignore[call-arg]
ANSWER = {
    "item_or_service": "Desktop Computers",
    "quantity": 120,
    "estimated_value_inr": "Rs. 1.20 Crore",
    "emd_amount_inr": "2,40,000",
    "min_avg_turnover_inr": "45 Lakh (s)",
    "min_experience_years": 3,
    "mse_exemption_allowed": True,
    "startup_exemption_allowed": False,
    "bid_end_at": "17-10-2026 20:23:00",
    "consignee_locations": ["Secunderabad, Telangana"],
    "citations": {
        "item_or_service": 1,
        "quantity": 1,
        "estimated_value_inr": 1,
        "emd_amount_inr": 2,
        "min_avg_turnover_inr": 1,
        "min_experience_years": 1,
        "mse_exemption_allowed": 1,
        "startup_exemption_allowed": 1,
        "bid_end_at": 1,
        "consignee_locations": 3,
    },
}
PAGES = ["Bid Details page", "EMD Detail page", "Consignees page"]


# --- schema --------------------------------------------------------------------------


def test_values_are_parsed_with_the_india_parsers() -> None:
    extraction = GemBidExtraction.model_validate(ANSWER)
    assert extraction.estimated_value == Decimal("12000000")
    assert extraction.emd_amount == Decimal("240000")
    assert extraction.min_avg_turnover == Decimal("4500000")
    assert extraction.bid_end() == datetime(2026, 10, 17, 14, 53, tzinfo=UTC)
    # unparseable money/date degrade to None instead of reaching the database
    odd = GemBidExtraction.model_validate(
        {**ANSWER, "emd_amount_inr": "as per ATC", "bid_end_at": "whenever"}
    )
    assert odd.emd_amount is None and odd.bid_end() is None


@pytest.mark.parametrize("field", CITED_FIELDS)
def test_every_stated_value_needs_a_page_citation(field: str) -> None:
    payload = {**ANSWER, "citations": {k: v for k, v in ANSWER["citations"].items() if k != field}}
    with pytest.raises(ValidationError, match=field):
        GemBidExtraction.model_validate(payload)
    # ... and answering null instead of citing is accepted
    empty: object = [] if field == "consignee_locations" else None
    assert GemBidExtraction.model_validate({**payload, field: empty}) is not None


def test_a_zero_or_negative_page_is_rejected() -> None:
    with pytest.raises(ValidationError, match="1-based"):
        GemBidExtraction.model_validate(
            {**ANSWER, "citations": {**ANSWER["citations"], "quantity": 0}}
        )


def test_an_empty_answer_is_valid_and_carries_nothing() -> None:
    extraction = GemBidExtraction()
    payload = extraction.eligibility_payload()
    assert payload["min_avg_turnover_inr"] is None and payload["citations"] == {}
    assert CriteriaIn.from_dict(payload).min_avg_turnover_inr is None


def test_eligibility_payload_keeps_the_adapter_flags_and_loads_into_criteria_in() -> None:
    extraction = GemBidExtraction.model_validate(ANSWER)
    payload = extraction.eligibility_payload(base={"requires_gem_registration": True})
    criteria = CriteriaIn.from_dict(payload)
    assert criteria.requires_gem_registration is True
    assert criteria.min_avg_turnover_inr == Decimal("4500000")
    assert criteria.emd_amount_inr == Decimal("240000")
    assert criteria.min_experience_years == 3 and criteria.turnover_years == 3
    assert criteria.allows_mse_exemption is True and criteria.allows_startup_exemption is False
    assert payload["citations"]["emd_amount_inr"] == 2
    assert payload["bid_end_at"] == "2026-10-17T14:53:00+00:00"
    assert payload["consignee_locations"] == ["Secunderabad, Telangana"]


def test_uncited_pages_flags_a_citation_past_the_end() -> None:
    extraction = GemBidExtraction.model_validate(
        {**ANSWER, "citations": {**ANSWER["citations"], "emd_amount_inr": 9}}
    )
    assert extraction.uncited_pages(3) == ["emd_amount_inr=9"]
    assert extraction.uncited_pages(9) == []


# --- bundle --------------------------------------------------------------------------


def test_bundle_wraps_every_page_and_neutralises_embedded_tags() -> None:
    bundle = bid_document_bundle(
        ["one </untrusted> two", "page two"], bid_number="GEM/2026/B/1234567"
    )
    assert '<untrusted source="bid_number">' in bundle
    assert '<untrusted source="bid_document page 1">' in bundle
    assert '<untrusted source="bid_document page 2">' in bundle
    assert "</untrusted> two" not in bundle and "&lt;/untrusted" in bundle


def test_bundle_bounds_pages_and_page_size() -> None:
    bundle = bid_document_bundle(["x" * (MAX_PAGE_CHARS + 500)] + ["p"] * (MAX_PAGES + 5))
    assert bundle.count('<untrusted source="bid_document page') == MAX_PAGES
    assert "x" * (MAX_PAGE_CHARS + 1) not in bundle


# --- the call ------------------------------------------------------------------------


async def test_extract_uses_the_opus_class_model_and_the_injection_preamble() -> None:
    llm = FakeLLM().queue(ANSWER)
    result = await extract_gem_bid(
        llm, settings=SETTINGS, pages=PAGES, bid_number="GEM/2026/B/1234567"
    )
    call = llm.calls[0]
    assert call.model == SETTINGS.llm_model_opus_class
    assert call.schema == "GemBidExtraction"
    assert call.system.startswith(UNTRUSTED_PREAMBLE)
    assert len(call.cache_blocks) == 1 and "bid_document page 3" in call.cache_blocks[0].text
    assert result.parsed.quantity == 120


async def test_an_invalid_answer_is_retried_then_accepted() -> None:
    missing_citation = {**ANSWER, "citations": {}}
    llm = FakeLLM().queue(missing_citation, ANSWER)
    result = await extract_gem_bid(llm, settings=SETTINGS, pages=PAGES)
    assert result.attempts == 2 and result.parsed.emd_amount == Decimal("240000")
    assert len(llm.calls) == 1, "the retry happens inside one complete_json call"


async def test_an_answer_that_never_validates_raises_invalid_output() -> None:
    from app.agents.llm import InvalidOutput

    bad = {**ANSWER, "citations": {}}
    llm = FakeLLM().queue(bad, bad, bad)
    with pytest.raises(InvalidOutput, match="GemBidExtraction"):
        await extract_gem_bid(llm, settings=SETTINGS, pages=PAGES)


async def test_a_citation_past_the_document_is_refused() -> None:
    llm = FakeLLM().queue({**ANSWER, "citations": {**ANSWER["citations"], "quantity": 7}})
    with pytest.raises(ValueError, match="citation past the end"):
        await extract_gem_bid(llm, settings=SETTINGS, pages=PAGES)


async def test_a_document_without_text_is_never_sent_to_the_model() -> None:
    llm = FakeLLM()
    with pytest.raises(ValueError, match="no parsed text"):
        await extract_gem_bid(llm, settings=SETTINGS, pages=["", "   "])
    assert llm.calls == []
