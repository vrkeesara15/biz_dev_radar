"""Agent 2: requirements extractor with page citations (SPEC 8, 11, 12). Pipeline step
"extract", Opus-class model (AgentRole.EXTRACTION).

    result = await extract_requirements(llm, documents, settings=settings)   # pure-ish core
    (the pipeline step loads the pursuit's parsed documents, runs it and stores rows)

Every parsed document is cut into page-tagged batches (~12k chars); each batch is one
JSON call whose answer must cite a page inside the batch and quote text found on that
page, or the item is rejected (app.core.requirements.validate_candidates). Batches are
merged, de-duplicated and numbered R-001... The solicitation text is DATA: it travels in
an <untrusted> block inside a cache block and the system prompt carries the injection
preamble, so embedded instructions cannot change the output.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from app.agents.llm import CacheBlock, LLMClient
from app.agents.pipeline import STEP_EXTRACT, register
from app.agents.prompting import system_prompt, untrusted_block
from app.agents.routing import AgentRole, model_for
from app.agents.runner import GuardContext, StepContext
from app.core.config import Settings, get_settings
from app.core.cost_guard import StepEstimate
from app.core.requirements import (
    BATCH_CHARS,
    Batch,
    Candidate,
    Cited,
    DocText,
    PageText,
    Rejection,
    RequirementType,
    build_batches,
    estimate_batches,
    merge,
    validate_candidates,
)
from app.models import DocumentChunk, Opportunity, OpportunityDocument, Pursuit, Requirement
from app.services.documents import STATUS_PARSED, load_parsed_text

log = structlog.get_logger(__name__)

MAX_OUTPUT_TOKENS = 8192
OUTPUT_TOKENS_PER_BATCH = 2_500  # projection only
MAX_QUOTE_CHARS = 400

INSTRUCTIONS = """You extract requirements from public-sector solicitation documents for a
proposal team. The document pages are inside an <untrusted> block; each page starts with
a marker like [Page 7]. Read every page and list EVERY distinct obligation, condition or
criterion the buyer states, as items with:
- text: the requirement restated as one clear sentence (keep numbers, dates, names).
- page: the integer from the [Page N] marker of the page the requirement appears on.
- type: one of shall | must | should | eligibility | format | submission | evaluation.
  shall/must/should = performance or delivery obligations by their modal verb;
  eligibility = who may respond (set-aside, size standard, NAICS, registrations,
  certifications, turnover, experience thresholds, EMD/tender fee);
  format = page limits, fonts, margins, file types, file naming, copies, volumes;
  submission = where/how/when to submit, portal, email, subject lines, questions deadline,
  signatures, DSC;
  evaluation = how responses are scored, factors, weights, past-performance relevance.
- volume: the proposal volume or section it belongs to when the document says or
  implies one (e.g. "Technical", "Past Performance", "Price", "Cover Page"), else null.
- quote: a verbatim excerpt (at most {max_quote} characters) copied exactly from that page
  that contains the requirement. Never paraphrase the quote.
- confidence: 0.0-1.0.
Rules: never invent requirements or pages; skip background, marketing and boilerplate;
one requirement per item (split compound clauses); every item needs a real page and a
real quote. Return an empty list when the pages contain no requirements.""".replace(
    "{max_quote}", str(MAX_QUOTE_CHARS)
)


class ExtractedRequirement(BaseModel):
    text: str = Field(min_length=5, max_length=1000)
    page: int = Field(ge=1)
    type: RequirementType
    volume: str | None = Field(max_length=120)
    quote: str = Field(min_length=6, max_length=MAX_QUOTE_CHARS + 100)
    confidence: float = Field(ge=0.0, le=1.0)


class ExtractionOutput(BaseModel):
    requirements: list[ExtractedRequirement]


class RequirementOut(BaseModel):
    req_id: str
    text: str
    document_id: uuid.UUID
    document_name: str
    page: int
    type: str
    volume: str | None
    quote: str
    confidence: float


class RejectionOut(BaseModel):
    reason: str
    text: str
    page: int
    batch: int


class ExtractorOutput(BaseModel):
    requirements: list[RequirementOut] = Field(default_factory=list)
    rejected: list[RejectionOut] = Field(default_factory=list)
    documents: int = 0
    batches: int = 0
    duplicates_removed: int = 0

    def by_type(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.requirements:
            counts[item.type] = counts.get(item.type, 0) + 1
        return counts


@dataclass(slots=True)
class ExtractionResult:
    requirements: list[Cited]
    ids: list[str]
    rejected: list[tuple[int, Rejection]] = field(default_factory=list)
    batches: int = 0
    documents: int = 0
    duplicates_removed: int = 0

    def to_output(self) -> ExtractorOutput:
        return ExtractorOutput(
            requirements=[
                RequirementOut(
                    req_id=rid,
                    text=item.text,
                    document_id=uuid.UUID(item.document_id),
                    document_name=item.document_name,
                    page=item.page,
                    type=item.type,
                    volume=item.volume,
                    quote=item.quote,
                    confidence=item.confidence,
                )
                for rid, item in zip(self.ids, self.requirements, strict=True)
            ],
            rejected=[
                RejectionOut(reason=r.reason, text=r.text, page=r.page, batch=b)
                for b, r in self.rejected
            ],
            documents=self.documents,
            batches=self.batches,
            duplicates_removed=self.duplicates_removed,
        )


def batch_prompt(batch: Batch) -> tuple[str, str]:
    """(cache block text, user message) for one batch. The pages are data."""
    block = untrusted_block(
        f"document:{batch.name} pages {batch.first_page}-{batch.last_page}", batch.tagged_text()
    )
    user = (
        f"Extract every requirement from pages {batch.first_page}-{batch.last_page} of "
        f"{batch.name!r} in the <untrusted> block by calling the emit tool. Valid page "
        f"numbers for this batch: {sorted(batch.page_numbers)}."
    )
    return block, user


async def extract_batch(
    llm: LLMClient | Any, batch: Batch, *, settings: Settings
) -> tuple[list[Cited], list[Rejection]]:
    block, user = batch_prompt(batch)
    result = await llm.complete_json(
        model=model_for(AgentRole.EXTRACTION, settings),
        system=system_prompt(INSTRUCTIONS),
        messages=[{"role": "user", "content": user}],
        schema=ExtractionOutput,
        cache_blocks=[CacheBlock(block)],
        max_tokens=MAX_OUTPUT_TOKENS,
        temperature=0.0,
    )
    parsed: ExtractionOutput = result.parsed
    candidates = [
        Candidate(
            text=item.text,
            page=item.page,
            type=item.type,
            quote=item.quote,
            volume=item.volume,
            confidence=item.confidence,
        )
        for item in parsed.requirements
    ]
    return validate_candidates(batch, candidates)


async def extract_requirements(
    llm: LLMClient | Any,
    documents: Sequence[DocText],
    *,
    settings: Settings | None = None,
    max_chars: int = BATCH_CHARS,
) -> ExtractionResult:
    """Run the extractor over parsed documents (no database): batches -> validated
    candidates -> merged, de-duplicated, numbered requirements."""
    settings = settings or get_settings()
    batches = build_batches(list(documents), max_chars=max_chars)
    accepted_per_batch: list[list[Cited]] = []
    rejected: list[tuple[int, Rejection]] = []
    for batch in batches:
        accepted, bad = await extract_batch(llm, batch, settings=settings)
        accepted_per_batch.append(accepted)
        rejected.extend((batch.index, r) for r in bad)
        if bad:
            log.info(
                "extractor.rejected",
                batch=batch.index,
                document=batch.name,
                count=len(bad),
                reasons=[r.reason for r in bad][:5],
            )
    merged = merge(accepted_per_batch)
    total_accepted = sum(len(a) for a in accepted_per_batch)
    return ExtractionResult(
        requirements=merged.requirements,
        ids=merged.ids,
        rejected=rejected,
        batches=len(batches),
        documents=len(documents),
        duplicates_removed=total_accepted - len(merged.requirements),
    )


# --- pipeline step -------------------------------------------------------------------


async def _pursuit_documents(
    session: Any, pursuit_id: uuid.UUID | None
) -> tuple[Pursuit, Opportunity, list[OpportunityDocument]]:
    if pursuit_id is None:
        raise RuntimeError("extract needs a pursuit")
    pursuit = await session.get(Pursuit, pursuit_id)
    if pursuit is None:
        raise LookupError(f"pursuit {pursuit_id} not found")
    opportunity = (
        await session.execute(
            select(Opportunity)
            .options(selectinload(Opportunity.documents))
            .where(Opportunity.id == pursuit.opportunity_id)
        )
    ).scalar_one_or_none()
    if opportunity is None:
        raise LookupError(f"opportunity {pursuit.opportunity_id} not found")
    parsed = [
        d
        for d in sorted(opportunity.documents, key=lambda d: (d.created_at, d.url))
        if d.status == STATUS_PARSED and d.parsed_text_ref
    ]
    return pursuit, opportunity, parsed


async def load_doc_texts(storage: Any, documents: Sequence[OpportunityDocument]) -> list[DocText]:
    out: list[DocText] = []
    for doc in documents:
        pages = await load_parsed_text(storage, doc)
        out.append(
            DocText(
                document_id=str(doc.id),
                name=doc.file_name or doc.url.rsplit("/", 1)[-1] or str(doc.id),
                pages=tuple(PageText(i, text) for i, text in enumerate(pages, start=1)),
            )
        )
    return out


async def estimate_extract(ctx: GuardContext) -> StepEstimate | None:
    """Projected spend: every parsed character goes in once (chunks include ~6% overlap,
    a safe over-estimate) plus the instructions per batch."""
    _, _opportunity, docs = await _pursuit_documents(ctx.session, ctx.run.pursuit_id)
    if not docs:
        return None
    total: int = (
        await ctx.session.execute(
            select(func.coalesce(func.sum(func.length(DocumentChunk.text)), 0)).where(
                DocumentChunk.document_id.in_([d.id for d in docs])
            )
        )
    ).scalar_one()
    batches = estimate_batches(int(total))
    return StepEstimate(
        model=model_for(AgentRole.EXTRACTION, ctx.services.settings if ctx.services else None),
        input_chars=int(total) + batches * len(INSTRUCTIONS),
        output_tokens=OUTPUT_TOKENS_PER_BATCH * batches,
    )


async def store_requirements(
    session: Any, tenant_id: uuid.UUID, pursuit_id: uuid.UUID, output: ExtractorOutput
) -> list[Requirement]:
    """Replace the pursuit's requirement rows with this extraction."""
    await session.execute(delete(Requirement).where(Requirement.pursuit_id == pursuit_id))
    rows = [
        Requirement(
            tenant_id=tenant_id,
            pursuit_id=pursuit_id,
            req_id=item.req_id,
            text=item.text,
            document_id=item.document_id,
            page=item.page,
            type=item.type,
            volume=item.volume,
            quote=item.quote,
            confidence=Decimal(str(round(item.confidence, 3))),
        )
        for item in output.requirements
    ]
    session.add_all(rows)
    await session.flush()
    return rows


@register(STEP_EXTRACT, estimate=estimate_extract)
async def extract(ctx: StepContext) -> ExtractorOutput:
    services = ctx.require_services()
    pursuit, opportunity, docs = await _pursuit_documents(ctx.session, ctx.run.pursuit_id)
    ctx.step.input_ref = f"opportunity:{opportunity.id}@{opportunity.version}"
    texts = await load_doc_texts(services.storage_for(opportunity.region), docs)
    result = await extract_requirements(ctx.llm, texts, settings=services.settings)
    output = result.to_output()
    await store_requirements(ctx.session, ctx.tenant_id, pursuit.id, output)
    log.info(
        "extractor.done",
        pursuit_id=str(pursuit.id),
        requirements=len(output.requirements),
        rejected=len(output.rejected),
        batches=output.batches,
        by_type=output.by_type(),
    )
    return output
