"""Agent 3: compliance matrix and submission checklist (SPEC 8). Pipeline step "matrix".

Turns the pursuit's requirements into

- compliance_items: one row per requirement with the proposal section it belongs to, its
  owner (unset) and its status (open). The section comes from app.core.compliance, which
  decides from the requirement's volume, its type and its keywords; only the obligations
  and evaluation criteria nothing decided are sent to the classification model
  (Haiku-class, one cheap call for the whole batch),
- a `format_rules` artifact: page limit, font, margins, file types, file naming, copies,
  portal and the submission method, each citing the requirement it was read from,
- a `checklist` artifact: the region's submission items -- US forms (SF-33 / SF-1449,
  reps & certs, SAM active) or India's EMD/BG, affidavit, turnover certificate and
  DSC-signed covers -- each pointing at the requirements that mention it.

Re-running replaces the matrix rows and appends new artifact versions.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, Literal

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.agents.llm import CacheBlock, LLMClient
from app.agents.pipeline import STEP_MATRIX, register
from app.agents.prompting import system_prompt, untrusted_block
from app.agents.routing import AgentRole, model_for
from app.agents.runner import GuardContext, StepContext
from app.core.compliance import (
    ARTIFACT_CHECKLIST,
    ARTIFACT_FORMAT_RULES,
    SECTIONS,
    STATUS_OPEN,
    Assignment,
    ChecklistContext,
    ChecklistItem,
    FormatRules,
    Req,
    apply_model_sections,
    assign_sections,
    build_checklist,
    extract_format_rules,
    section_counts,
)
from app.core.config import Settings, get_settings
from app.core.cost_guard import StepEstimate
from app.models import ComplianceItem, Opportunity, Pursuit, Requirement
from app.services.pursuits import store_artifact

log = structlog.get_logger(__name__)

MAX_OUTPUT_TOKENS = 4096
OUTPUT_TOKENS_PER_ITEM = 40  # projection only

SectionName = Literal[
    "Cover Letter",
    "Executive Summary",
    "Technical Approach",
    "Management Plan",
    "Staffing and Key Personnel",
    "Past Performance",
    "Price",
    "Eligibility and Certifications",
    "Submission Package",
]

INSTRUCTIONS = """You assign solicitation requirements to the proposal section that will
answer them. The requirements are inside an <untrusted> block, one per line as
"<req id> [<type>] <text>". For every req id in the block return exactly one assignment
with the section that should carry the response. Allowed sections (use them verbatim):
{sections}.
Guidance: obligations about people, clearances or resumes go to Staffing and Key
Personnel; about prior contracts or references to Past Performance; about schedule,
risk, reporting, governance, transition or subcontracting to Management Plan; about
rates, invoicing or taxes to Price; everything else about how the work is done to
Technical Approach. Never invent a req id and never return a section outside the
list.""".replace("{sections}", "; ".join(SECTIONS))


class SectionAssignment(BaseModel):
    req_id: str = Field(min_length=1, max_length=16)
    section: SectionName


class ClassificationOutput(BaseModel):
    assignments: list[SectionAssignment]


class MatrixItemOut(BaseModel):
    req_id: str
    requirement_id: uuid.UUID
    section: str
    reason: str
    type: str
    volume: str | None
    document_id: uuid.UUID
    page: int
    status: str = STATUS_OPEN


class MatrixOutput(BaseModel):
    items: list[MatrixItemOut] = Field(default_factory=list)
    sections: dict[str, int] = Field(default_factory=dict)
    format_rules: FormatRules = Field(default_factory=FormatRules)
    checklist: list[ChecklistItem] = Field(default_factory=list)
    requirements: int = 0
    classified: int = 0  # requirements the classification model decided
    format_rules_version: int | None = None
    checklist_version: int | None = None


def as_req(row: Requirement) -> Req:
    return Req(req_id=row.req_id, text=row.text, type=row.type, volume=row.volume)


def classification_prompt(reqs: Sequence[Req]) -> tuple[str, str]:
    """(cache block, user message). The requirement text is data, never instructions."""
    lines = "\n".join(f"{r.req_id} [{r.type}] {r.text}" for r in reqs)
    block = untrusted_block("requirements extracted from the solicitation", lines)
    user = (
        "Assign a proposal section to each of these req ids by calling the emit tool: "
        f"{', '.join(r.req_id for r in reqs)}."
    )
    return block, user


async def classify_sections(
    llm: LLMClient | Any, reqs: Sequence[Req], *, settings: Settings
) -> dict[str, str]:
    """Ask the classification model about the requirements the heuristics could not place."""
    if not reqs:
        return {}
    block, user = classification_prompt(reqs)
    result = await llm.complete_json(
        model=model_for(AgentRole.CLASSIFICATION, settings),
        system=system_prompt(INSTRUCTIONS),
        messages=[{"role": "user", "content": user}],
        schema=ClassificationOutput,
        cache_blocks=[CacheBlock(block)],
        max_tokens=MAX_OUTPUT_TOKENS,
        temperature=0.0,
    )
    parsed: ClassificationOutput = result.parsed
    known = {r.req_id for r in reqs}
    return {a.req_id: a.section for a in parsed.assignments if a.req_id in known}


async def build_matrix(
    llm: LLMClient | Any,
    reqs: Sequence[Req],
    ctx: ChecklistContext,
    *,
    settings: Settings | None = None,
    portal_hint: str | None = None,
) -> tuple[list[Assignment], FormatRules, list[ChecklistItem], int]:
    """(assignments, format rules, checklist, how many the model classified). No database."""
    settings = settings or get_settings()
    assignments = assign_sections(reqs)
    ambiguous = [r for r, a in zip(reqs, assignments, strict=True) if a.ambiguous]
    chosen = await classify_sections(llm, ambiguous, settings=settings)
    assignments = apply_model_sections(assignments, chosen)
    rules = extract_format_rules(reqs, portal_hint=portal_hint)
    checklist = build_checklist(ctx, reqs)
    classified = sum(1 for a in assignments if a.reason == "model")
    return assignments, rules, checklist, classified


# --- pipeline step -------------------------------------------------------------------


async def _pursuit_requirements(
    session: Any, pursuit_id: uuid.UUID | None
) -> tuple[Pursuit, Opportunity, list[Requirement]]:
    if pursuit_id is None:
        raise RuntimeError("matrix needs a pursuit")
    pursuit = await session.get(Pursuit, pursuit_id)
    if pursuit is None:
        raise LookupError(f"pursuit {pursuit_id} not found")
    opportunity = await session.get(Opportunity, pursuit.opportunity_id)
    if opportunity is None:
        raise LookupError(f"opportunity {pursuit.opportunity_id} not found")
    rows = list(
        (
            await session.execute(
                select(Requirement)
                .where(Requirement.pursuit_id == pursuit_id)
                .order_by(Requirement.req_id)
            )
        )
        .scalars()
        .all()
    )
    return pursuit, opportunity, rows


def checklist_context(opportunity: Opportunity) -> ChecklistContext:
    return ChecklistContext(
        region=str(opportunity.region),
        notice_type=str(opportunity.notice_type),
        source_id=opportunity.source_id,
        set_aside=opportunity.set_aside,
        currency=opportunity.currency,
        emd_amount=opportunity.emd_amount,
        tender_fee=opportunity.tender_fee,
    )


async def estimate_matrix(ctx: GuardContext) -> StepEstimate | None:
    """Only the ambiguous requirements reach the model, so only they are projected."""
    _, _opportunity, rows = await _pursuit_requirements(ctx.session, ctx.run.pursuit_id)
    reqs = [as_req(row) for row in rows]
    ambiguous = [r for r, a in zip(reqs, assign_sections(reqs), strict=True) if a.ambiguous]
    if not ambiguous:
        return None
    settings = ctx.services.settings if ctx.services else None
    return StepEstimate(
        model=model_for(AgentRole.CLASSIFICATION, settings),
        input_chars=sum(len(r.text) for r in ambiguous) + len(INSTRUCTIONS),
        output_tokens=OUTPUT_TOKENS_PER_ITEM * len(ambiguous),
    )


async def store_matrix(
    session: Any,
    tenant_id: uuid.UUID,
    pursuit_id: uuid.UUID,
    rows: Sequence[Requirement],
    assignments: Sequence[Assignment],
) -> list[ComplianceItem]:
    """Replace the pursuit's matrix rows (owner and status start clean)."""
    await session.execute(delete(ComplianceItem).where(ComplianceItem.pursuit_id == pursuit_id))
    by_req = {a.req_id: a for a in assignments}
    items = [
        ComplianceItem(
            tenant_id=tenant_id,
            pursuit_id=pursuit_id,
            requirement_id=row.id,
            section=by_req[row.req_id].section,
            reason=by_req[row.req_id].reason,
            status=STATUS_OPEN,
        )
        for row in rows
    ]
    session.add_all(items)
    await session.flush()
    return items


@register(STEP_MATRIX, estimate=estimate_matrix)
async def matrix(ctx: StepContext) -> MatrixOutput:
    pursuit, opportunity, rows = await _pursuit_requirements(ctx.session, ctx.run.pursuit_id)
    ctx.step.input_ref = f"pursuit:{pursuit.id}:requirements={len(rows)}"
    settings = ctx.services.settings if ctx.services else None
    reqs = [as_req(row) for row in rows]
    assignments, rules, checklist, classified = await build_matrix(
        ctx.llm,
        reqs,
        checklist_context(opportunity),
        settings=settings,
        portal_hint=opportunity.source_url,
    )
    items = await store_matrix(ctx.session, ctx.tenant_id, pursuit.id, rows, assignments)
    by_req = {a.req_id: a for a in assignments}
    rules_artifact = await store_artifact(
        ctx.session,
        ctx.tenant_id,
        pursuit.id,
        ARTIFACT_FORMAT_RULES,
        rules.model_dump(mode="json"),
        scope=ctx.scope,
    )
    checklist_artifact = await store_artifact(
        ctx.session,
        ctx.tenant_id,
        pursuit.id,
        ARTIFACT_CHECKLIST,
        {"items": [item.model_dump(mode="json") for item in checklist]},
        scope=ctx.scope,
    )
    output = MatrixOutput(
        items=[
            MatrixItemOut(
                req_id=row.req_id,
                requirement_id=row.id,
                section=by_req[row.req_id].section,
                reason=by_req[row.req_id].reason,
                type=row.type,
                volume=row.volume,
                document_id=row.document_id,
                page=row.page,
                status=item.status,
            )
            for row, item in zip(rows, items, strict=True)
        ],
        sections=section_counts(assignments),
        format_rules=rules,
        checklist=checklist,
        requirements=len(rows),
        classified=classified,
        format_rules_version=rules_artifact.version,
        checklist_version=checklist_artifact.version,
    )
    log.info(
        "matrix.done",
        pursuit_id=str(pursuit.id),
        requirements=len(rows),
        classified=classified,
        sections=output.sections,
        checklist=len(checklist),
    )
    return output
