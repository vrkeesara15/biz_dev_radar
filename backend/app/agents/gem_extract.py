"""GeM bid-document extraction (SPEC 5.2, 5.3, 8; M3-04).

    result = await extract_gem_bid(ctx.llm, settings=settings, bid_number=..., pages=pages)
    extraction: GemBidExtraction = result.parsed
    eligibility = extraction.eligibility_payload()      # loads into CriteriaIn.from_dict

A GeM bid PDF states the eligibility of the bid in a fixed layout: bid details and the
turnover / experience criteria on the first page, the EMD block on the second, the
consignee table and the Additional Terms and Conditions after that. One Opus-class call
(SPEC 8: extraction is an Opus role) reads the parsed pages and emits a
`GemBidExtraction` through the JSON-schema `emit` tool; `app/agents/llm.py` retries an
answer that does not validate and raises `InvalidOutput` after the retry budget, which
flags the agent step.

Guardrails (SPEC 8, 11):

- every page is wrapped in an <untrusted> block and the system prompt carries the
  injection preamble: bid documents are DATA (real GeM ATC text has been seen to carry
  instruction-shaped lines);
- money and dates are returned **as printed** and parsed here with `core.money.parse_inr`
  / `core.dates.parse_in`, so a hallucinated number cannot bypass our parsers;
- every value must carry a page citation (`citations["emd_amount_inr"] = 2`); a value
  without one is a schema error, so the model must either cite the page or answer null.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.agents.llm import CacheBlock, LLMResult
from app.agents.prompting import system_prompt, untrusted_block
from app.agents.routing import AgentRole, model_for
from app.core.config import Settings
from app.core.dates import IST, parse_in
from app.core.money import parse_inr

MAX_PAGES = 12
MAX_PAGE_CHARS = 6_000
MAX_OUTPUT_TOKENS = 2_000
TURNOVER_YEARS = 3  # GeM prints "Minimum Average Annual Turnover of the bidder (For 3 Years)"

# Values that must be accompanied by a page citation when they are not null. Everything
# the extractor reports is a claim about the document, so everything is cited; the money
# and count fields are the ones a wrong answer would cost money on.
MONEY_FIELDS: tuple[str, ...] = ("estimated_value_inr", "emd_amount_inr", "min_avg_turnover_inr")
CITED_FIELDS: tuple[str, ...] = (
    "item_or_service",
    "quantity",
    *MONEY_FIELDS,
    "min_experience_years",
    "mse_exemption_allowed",
    "startup_exemption_allowed",
    "bid_end_at",
    "consignee_locations",
)

INSTRUCTIONS = f"""You read Government e-Marketplace (GeM) bid documents and extract the
bid's commercial and eligibility terms for a bidder's go/no-go decision.

Return exactly what the document states, field by field:
- item_or_service: the "Item Category" / service description.
- quantity: "Total Quantity" as an integer.
- estimated_value_inr: the estimated bid value EXACTLY as printed ("Rs. 1.20 Crore",
  "66,00,000"); do not convert or reformat it.
- emd_amount_inr: the EMD amount exactly as printed; null when EMD is not required.
- min_avg_turnover_inr: the minimum average annual turnover criterion exactly as printed
  ("45 Lakh (s)"); null when the document states none.
- min_experience_years: the years of past experience required, as an integer.
- mse_exemption_allowed / startup_exemption_allowed: true only when the document says the
  MSE / Startup exemption for experience and turnover is allowed ("Yes").
- bid_end_at: the bid end date and time exactly as printed ("17-10-2026 20:23:00").
- consignee_locations: one "City, State" string per consignee row, in document order.

Rules:
- Use only the <untrusted> pages. Never infer, average or convert a value; if a field is
  not stated, answer null (or an empty list) rather than guessing.
- citations maps every field you answer to the 1-based page number it was read from.
  A non-null value without a citation is invalid: cite the page or answer null.
- Turnover is the criterion for the bidder over {TURNOVER_YEARS} years, not the bid value.
"""


class GemBidExtraction(BaseModel):
    """Bid terms read off the document, money and dates still as printed."""

    item_or_service: str | None = None
    quantity: int | None = Field(default=None, ge=0)
    estimated_value_inr: str | None = None
    emd_amount_inr: str | None = None
    min_avg_turnover_inr: str | None = None
    min_experience_years: int | None = Field(default=None, ge=0)
    mse_exemption_allowed: bool | None = None
    startup_exemption_allowed: bool | None = None
    bid_end_at: str | None = None
    consignee_locations: list[str] = Field(default_factory=list)
    citations: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _cited(self) -> GemBidExtraction:
        for page in self.citations.values():
            if page < 1:
                raise ValueError("citations must be 1-based page numbers")
        missing = [
            name
            for name in CITED_FIELDS
            if _stated(getattr(self, name)) and name not in self.citations
        ]
        if missing:
            raise ValueError(
                "every stated value needs a page citation; missing citations for: "
                + ", ".join(missing)
                + " (cite the page it was read from, or answer null)"
            )
        return self

    # -- parsed values ---------------------------------------------------------------
    @property
    def estimated_value(self) -> Decimal | None:
        return parse_inr(self.estimated_value_inr)

    @property
    def emd_amount(self) -> Decimal | None:
        return parse_inr(self.emd_amount_inr)

    @property
    def min_avg_turnover(self) -> Decimal | None:
        return parse_inr(self.min_avg_turnover_inr)

    def bid_end(self, tz: str = IST) -> datetime | None:
        parsed = parse_in(self.bid_end_at, tz)
        return parsed.utc if parsed else None

    def uncited_pages(self, page_count: int) -> list[str]:
        """Citations pointing past the end of the document (a fabricated page)."""
        return [f"{k}={v}" for k, v in sorted(self.citations.items()) if v > page_count]

    def eligibility_payload(self, *, base: dict[str, Any] | None = None) -> dict[str, Any]:
        """The `opportunities.eligibility` jsonb: CriteriaIn.from_dict keys (parsed INR as
        decimal strings) plus the descriptive fields and the citations."""
        payload: dict[str, Any] = dict(base or {})
        payload.update(
            {
                "min_avg_turnover_inr": _money(self.min_avg_turnover),
                "turnover_years": TURNOVER_YEARS,
                "min_experience_years": self.min_experience_years,
                "emd_amount_inr": _money(self.emd_amount),
                "allows_mse_exemption": self.mse_exemption_allowed,
                "allows_startup_exemption": self.startup_exemption_allowed,
                "item_or_service": self.item_or_service,
                "quantity": self.quantity,
                "estimated_value_inr": _money(self.estimated_value),
                "bid_end_at": _iso(self.bid_end()),
                "consignee_locations": list(self.consignee_locations),
                "citations": dict(self.citations),
            }
        )
        return payload


def _stated(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value)
    return True


def _money(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def bid_document_bundle(pages: Sequence[str], *, bid_number: str | None = None) -> str:
    """The untrusted content block: one <untrusted> element per page, page numbers in the
    label so the model can cite them."""
    parts: list[str] = []
    if bid_number:
        parts.append(untrusted_block("bid_number", bid_number))
    for index, text in enumerate(pages[:MAX_PAGES], start=1):
        parts.append(untrusted_block(f"bid_document page {index}", (text or "")[:MAX_PAGE_CHARS]))
    return "\n\n".join(parts)


async def extract_gem_bid(
    llm: Any,
    *,
    settings: Settings,
    pages: Sequence[str],
    bid_number: str | None = None,
) -> LLMResult:
    """One Opus-class call; `result.parsed` is a validated GemBidExtraction."""
    if not any((page or "").strip() for page in pages):
        raise ValueError("the GeM bid document has no parsed text")
    bundle = bid_document_bundle(pages, bid_number=bid_number)
    result: LLMResult = await llm.complete_json(
        model=model_for(AgentRole.EXTRACTION, settings),
        system=system_prompt(INSTRUCTIONS),
        messages=[
            {
                "role": "user",
                "content": (
                    "Extract the bid terms from the <untrusted> pages by calling the emit "
                    "tool. Cite the 1-based page number of every value you report."
                ),
            }
        ],
        schema=GemBidExtraction,
        cache_blocks=[CacheBlock(bundle)],
        max_tokens=MAX_OUTPUT_TOKENS,
    )
    extraction: GemBidExtraction = result.parsed
    fabricated = extraction.uncited_pages(min(len(pages), MAX_PAGES))
    if fabricated:
        raise ValueError(
            f"citation past the end of the document ({len(pages)} pages): " + ", ".join(fabricated)
        )
    return result
