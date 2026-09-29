"""Pursuits API (SPEC 10.3, M5): the pursuit record with its cost meter, the agent runs
and the budget approval that resumes a run the cost guard paused.

    POST /api/v1/opportunities/{id}/pursue|watch|pass      create / track / drop a pursuit
    GET  /api/v1/pursuits                                  board + table listing with filters
    POST /api/v1/pursuits                                  {profile_id, opportunity_id}
    GET  /api/v1/pursuits/{pursuit_id}                     cost_so_far, cap, budget, latest run
    PATCH /api/v1/pursuits/{pursuit_id}                    {stage?, owner_user_id?} (409 rules)
    GET  /api/v1/pursuits/{pursuit_id}/matrix              matrix + format rules + checklist
    GET  /api/v1/pursuits/{pursuit_id}/packet              uploads, portal, signatures, deadline
    GET  /api/v1/pursuits/{pursuit_id}/artifacts           latest stored agent output per kind
    GET  /api/v1/pursuits/{pursuit_id}/artifacts/{art_id}  one stored version by id
    POST /api/v1/pursuits/{pursuit_id}/agents/run          {step: collect|...|all}
    POST /api/v1/pursuits/{pursuit_id}/agents/approve-budget {additional_usd, reason?}
    POST /api/v1/pursuits/{pursuit_id}/decision            {decision: bid|no_bid, note?}
    POST /api/v1/pursuits/{pursuit_id}/approve-package      Gate 2: the reviewed package

Every stage move -- a board drag, a bid decision or a package approval -- goes through
`services.pursuits.move_stage`, so `app.core.pursuit_stages` is the single set of rules
and a refusal always answers 409 with the reason the board shows.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import pipeline
from app.agents.cost_guard import CostSnapshot, cost_snapshot
from app.agents.llm import LLMClient
from app.agents.runner import AgentRunner
from app.agents.services import AgentServices, services_from_settings
from app.api.deps import TENANT_ROLES, CurrentUser, SettingsDep, TenantSessionDep, require_role
from app.core import pursuit_stages as stages
from app.core.compliance import (
    ARTIFACT_CHECKLIST,
    ARTIFACT_FORMAT_RULES,
    ARTIFACT_KINDS,
    ARTIFACT_RED_TEAM,
    ARTIFACT_SCORECARD,
    ChecklistItem,
    FormatRules,
)
from app.core.config import Region, Settings
from app.core.display_time import TzDateOut, tz_fields_or_none
from app.core.packet import Packet, PacketContext, build_packet
from app.core.plan import Resource
from app.core.pursuit_stages import DECISION_BID, DECISIONS
from app.core.roles import Role
from app.jobs import run_agents as run_agents_job_module
from app.models import (
    AgentRun,
    CompanyProfile,
    ComplianceItem,
    Opportunity,
    Pursuit,
    PursuitArtifact,
    Requirement,
    User,
)
from app.models.agents import RUN_NEEDS_APPROVAL, RUN_PAUSED, RUN_QUEUED
from app.services import drafts as draft_svc
from app.services import key_dates as key_date_svc
from app.services import pursuits as pursuit_svc
from app.services.audit import AuditHint, audit
from app.services.drafts import DraftsSummary
from app.services.events import PURSUIT_DECIDED, get_event_bus
from app.services.plan import PlanService
from app.services.users import ensure_user_membership

router = APIRouter(prefix="/pursuits", tags=["pursuits"])
# SPEC 10.3 puts the board actions on the opportunity; they live here with the rest of
# the pursuit logic and are mounted next to the opportunities router.
opportunity_router = APIRouter(prefix="/opportunities", tags=["pursuits"])

# SPEC 3: bid managers (and owners) decide, assign and approve; every tenant role reads.
MANAGER_ROLES = (Role.TENANT_OWNER, Role.BID_MANAGER)
# Watching costs nothing and starts no agent, so a writer may do it (SPEC 3 writer row).
WRITER_ROLES = (Role.TENANT_OWNER, Role.BID_MANAGER, Role.WRITER)
ManagerDep = Annotated[CurrentUser, Depends(require_role(*MANAGER_ROLES))]
WriterDep = Annotated[CurrentUser, Depends(require_role(*WRITER_ROLES))]
ReaderDep = Annotated[CurrentUser, Depends(require_role(*TENANT_ROLES))]

# SPEC 11: reading generated content is audited. The scorecard is a judgement about the
# tenant and the red-team report is the criticism of their own draft, so both are logged
# like a draft read; the mechanical artifacts (rules, checklist, packet) are not.
AUDIT_ARTIFACT_READ = "pursuit_artifact.read"
AUDITED_ARTIFACT_KINDS: frozenset[str] = frozenset({ARTIFACT_SCORECARD, ARTIFACT_RED_TEAM})

MAX_LIST_PAGE_SIZE = 200
DEFAULT_LIST_PAGE_SIZE = 50

MAX_APPROVAL_USD = Decimal("10000")
_NO_LLM = run_agents_job_module.NoLLM()  # steps that need a model fail loudly, others run


class PursuitCreateIn(BaseModel):
    profile_id: uuid.UUID
    opportunity_id: uuid.UUID


class RunOut(BaseModel):
    id: uuid.UUID
    kind: str
    step: str | None
    status: str
    pause_reason: str | None
    paused_at: str | None
    gate: str | None
    cost_usd: Decimal
    tokens_in: int
    tokens_out: int
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class DraftsSummaryOut(BaseModel):
    """Draft state of the pursuit; `unsupported_claims_count` is the grounding validator's
    tally over the current version of every section (SPEC 8, M5-11)."""

    count: int = 0
    approved: int = 0
    in_review: int = 0
    unsupported_claims_count: int = 0
    needs_input_count: int = 0
    flagged_sections: int = 0


class PursuitOut(BaseModel):
    id: uuid.UUID
    profile_id: uuid.UUID
    opportunity_id: uuid.UUID
    stage: str
    owner_user_id: uuid.UUID | None
    decision: str | None
    decided_by: uuid.UUID | None
    decided_at: datetime | None
    decision_note: str | None
    # Gate 2 (SPEC 8): the human who approved the reviewed draft package
    package_approved_by: uuid.UUID | None
    package_approved_at: datetime | None
    internal_due_at: datetime | None
    watch: bool
    pass_reason: str | None
    submitted_at: datetime | None
    activity_at: datetime
    matrix_recheck_required: bool
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    # cost meter (SPEC 8): spend so far vs the pursuit cap and the tenant's monthly budget
    cost_so_far_usd: Decimal
    cost_cap_usd: Decimal
    budget_month_limit_usd: Decimal | None  # None = unlimited
    budget_month_spent_usd: Decimal
    budget_month_remaining_usd: Decimal | None
    drafts: DraftsSummaryOut
    run: RunOut | None


class DecisionIn(BaseModel):
    decision: str = Field(max_length=16)
    note: str | None = Field(default=None, max_length=2000)
    # run the resumed pipeline in the API process (tests / no worker); otherwise queued
    inline: bool = False

    @field_validator("decision")
    @classmethod
    def _known(cls, value: str) -> str:
        if value not in DECISIONS:
            raise ValueError(f"decision must be one of {list(DECISIONS)}")
        return value


class DecisionOut(BaseModel):
    pursuit: PursuitOut
    decision: str
    previous_stage: str
    resumed_run_id: uuid.UUID | None
    mode: str  # queued | inline | none
    task_id: str | None = None
    result: dict[str, Any] | None = None


class MatrixRowOut(BaseModel):
    """One compliance item joined with the requirement it answers (SPEC 8: the citation
    travels with the row so the UI can link to the document page)."""

    id: uuid.UUID
    requirement_id: uuid.UUID
    req_id: str
    text: str
    type: str
    volume: str | None
    document_id: uuid.UUID
    page: int
    quote: str
    section: str
    reason: str
    owner_user_id: uuid.UUID | None
    status: str
    notes: str | None


class PursuitMatrixOut(BaseModel):
    pursuit_id: uuid.UUID
    items: list[MatrixRowOut]
    format_rules: FormatRules | None
    format_rules_version: int | None
    checklist: list[ChecklistItem]
    checklist_version: int | None
    generated_at: datetime | None  # when the matrix agent last ran


class PursuitPacketOut(BaseModel):
    pursuit_id: uuid.UUID
    opportunity_id: uuid.UUID
    packet: Packet
    checklist: list[ChecklistItem]
    checklist_version: int | None
    generated_at: datetime | None


class PursuitArtifactOut(BaseModel):
    """One stored `pursuit_artifacts` row (SPEC 8: every agent output is stored and
    versioned). `data` is the agent's own JSON payload, unwrapped."""

    id: uuid.UUID
    kind: str
    version: int
    data: dict[str, Any]
    created_by: str  # agent | user
    created_at: datetime


class PursuitArtifactListOut(BaseModel):
    pursuit_id: uuid.UUID
    items: list[PursuitArtifactOut]
    count: int
    kind: str | None = None  # echoes the filter, so a cached body names what it holds


class RunAgentsIn(BaseModel):
    """`step` is one agent name or "all"; "all" runs every implemented step in order and
    pauses at the first one a later task still has to add."""

    step: str = Field(default=pipeline.STEP_ALL, max_length=32)
    # run in the API process (tests / no worker); otherwise queued on Celery
    inline: bool = False

    @field_validator("step")
    @classmethod
    def _known(cls, value: str) -> str:
        if not pipeline.valid_step(value):
            raise ValueError(
                f"unknown step {value!r}; one of {(pipeline.STEP_ALL, *pipeline.PIPELINE_ORDER)}"
            )
        return value


class RunAgentsOut(BaseModel):
    run_id: uuid.UUID
    step: str
    steps: list[str]  # the implemented steps this run will execute, in order
    mode: str  # queued | inline
    task_id: str | None = None
    result: dict[str, Any] | None = None
    pursuit: PursuitOut


class ApproveBudgetIn(BaseModel):
    additional_usd: Decimal = Field(gt=0, le=MAX_APPROVAL_USD)
    reason: str | None = Field(default=None, max_length=500)
    # run the resumed pipeline in the API process (tests / no worker); otherwise queued
    inline: bool = False


class ApproveBudgetOut(BaseModel):
    pursuit: PursuitOut
    previous_cap_usd: Decimal
    new_cap_usd: Decimal
    resumed_run_id: uuid.UUID | None
    mode: str  # queued | inline | none
    task_id: str | None = None
    result: dict[str, Any] | None = None


def run_out(run: AgentRun) -> RunOut:
    params = dict(run.params or {})
    return RunOut(
        id=run.id,
        kind=run.kind,
        step=params.get("step"),
        status=run.status,
        pause_reason=run.pause_reason,
        paused_at=params.get("paused_at"),
        gate=params.get("gate"),
        cost_usd=Decimal(run.cost_usd),
        tokens_in=run.tokens_in,
        tokens_out=run.tokens_out,
        started_at=run.started_at,
        finished_at=run.finished_at,
        created_at=run.created_at,
    )


def pursuit_out(
    pursuit: Pursuit,
    costs: CostSnapshot,
    run: AgentRun | None,
    drafts: DraftsSummary | None = None,
) -> PursuitOut:
    return PursuitOut(
        id=pursuit.id,
        profile_id=pursuit.profile_id,
        opportunity_id=pursuit.opportunity_id,
        stage=pursuit.stage,
        owner_user_id=pursuit.owner_user_id,
        decision=pursuit.decision,
        decided_by=pursuit.decided_by,
        decided_at=pursuit.decided_at,
        decision_note=pursuit.decision_note,
        package_approved_by=pursuit.package_approved_by,
        package_approved_at=pursuit.package_approved_at,
        internal_due_at=pursuit.internal_due_at,
        watch=pursuit.watch,
        pass_reason=pursuit.pass_reason,
        submitted_at=pursuit.submitted_at,
        activity_at=pursuit.activity_at,
        matrix_recheck_required=pursuit.matrix_recheck_required,
        created_by=pursuit.created_by,
        created_at=pursuit.created_at,
        updated_at=pursuit.updated_at,
        cost_so_far_usd=costs.pursuit_cost_usd,
        cost_cap_usd=costs.pursuit_cap_usd,
        budget_month_limit_usd=costs.month_budget_usd,
        budget_month_spent_usd=costs.month_spent_usd,
        budget_month_remaining_usd=costs.month_remaining_usd,
        drafts=DraftsSummaryOut(**(drafts or DraftsSummary()).as_dict()),
        run=None if run is None else run_out(run),
    )


async def load_pursuit_out(
    session: AsyncSession, pursuit: Pursuit, tenant_id: uuid.UUID
) -> PursuitOut:
    # a handler that mutated the row leaves server-side columns (updated_at) expired;
    # refresh them here rather than letting Pydantic trigger a sync lazy load
    await session.flush()
    await session.refresh(pursuit)
    tenant = await pursuit_svc.get_tenant(session, tenant_id)
    costs = await cost_snapshot(session, pursuit, tenant)
    return pursuit_out(
        pursuit,
        costs,
        await pursuit_svc.latest_run(session, pursuit.id),
        await draft_svc.summarise(session, pursuit.id),
    )


def app_llm(request: Request) -> LLMClient | None:
    llm: LLMClient | None = getattr(request.app.state, "llm", None)
    return llm


def app_services(request: Request, settings: Settings) -> AgentServices:
    services: AgentServices | None = getattr(request.app.state, "agent_services", None)
    if services is None:
        services = services_from_settings(
            settings, storage=request.app.state.storage_router, scanner=request.app.state.scanner
        )
        request.app.state.agent_services = services
    return services


async def dispatch_run(
    request: Request, settings: Settings, run_id: uuid.UUID, tenant_id: uuid.UUID, *, inline: bool
) -> tuple[str, str | None, dict[str, Any] | None]:
    """Queue the run through Celery or execute it in-process. Returns (mode, task_id, result).
    Eager Celery would call asyncio.run inside the API loop, so eager also means inline."""
    if not inline and not settings.celery_task_always_eager:
        task_id = run_agents_job_module.enqueue_agents(run_id, tenant_id)
        if task_id is not None:
            return "queued", task_id, None
    result = await run_agents_job_module.run_agents_job(
        run_id,
        tenant_id,
        settings=settings,
        llm=app_llm(request),
        services=app_services(request, settings),
    )
    return "inline", None, result


@router.post("", response_model=PursuitOut)
async def create_pursuit(
    body: PursuitCreateIn, session: TenantSessionDep, user: ManagerDep, request: Request
) -> JSONResponse:
    """Idempotent: the (profile, opportunity) pair has one pursuit; 201 when created."""
    pursuit, created = await pursuit_svc.get_or_create(
        session, body.profile_id, body.opportunity_id, user
    )
    request.state.audit = AuditHint(
        action="pursuit.created" if created else "pursuit.reused",
        object_type="pursuit",
        object_id=str(pursuit.id),
    )
    out = await load_pursuit_out(session, pursuit, user.tenant_id)
    code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return JSONResponse(status_code=code, content=out.model_dump(mode="json"))


@router.get("/{pursuit_id}", response_model=PursuitOut)
async def get_pursuit(
    pursuit_id: uuid.UUID, session: TenantSessionDep, user: ReaderDep
) -> PursuitOut:
    pursuit = await pursuit_svc.get_pursuit(session, pursuit_id)
    return await load_pursuit_out(session, pursuit, user.tenant_id)


@router.post("/{pursuit_id}/decision", response_model=DecisionOut)
async def record_decision(
    pursuit_id: uuid.UUID,
    body: DecisionIn,
    session: TenantSessionDep,
    user: ReaderDep,
    request: Request,
    settings: SettingsDep,
) -> DecisionOut:
    """Gate 1 (SPEC 8, 9): an approver records bid or no-bid.

    Only the roles the profile's `required_approver_roles` names (plus the tenant owner)
    may decide. A `bid` moves the pursuit to Drafting and resumes the run the pipeline
    left paused at Gate 1; a `no_bid` closes the pursuit and resumes nothing.
    """
    pursuit = await pursuit_svc.get_pursuit(session, pursuit_id)
    profile = await session.get(CompanyProfile, pursuit.profile_id)
    allowed = pursuit_svc.approver_roles(profile)
    if user.role not in allowed:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail=(
                f"role {user.role.value} may not approve bid/no-bid; "
                f"allowed: {sorted(r.value for r in allowed)}"
            ),
        )
    # decided_by references users: make sure the JIT row exists (OQ-16)
    await ensure_user_membership(
        user_id=user.id, email=user.email, tenant_id=user.tenant_id, role=user.role
    )
    previous_stage = pursuit_svc.record_decision(
        pursuit, body.decision, user.id, role=user.role, note=body.note
    )
    run = await pursuit_svc.latest_run(session, pursuit.id)
    resumed: AgentRun | None = None
    if body.decision == DECISION_BID and run is not None and run.status == RUN_PAUSED:
        run.status = RUN_QUEUED
        resumed = run
    request.state.audit = AuditHint(
        action="pursuit.decided",
        object_type="pursuit",
        object_id=str(pursuit.id),
        meta={
            "decision": body.decision,
            "previous_stage": previous_stage,
            "stage": pursuit.stage,
            "note": body.note,
            "run_id": None if resumed is None else str(resumed.id),
        },
    )
    await session.commit()  # the worker / inline job reads the run in its own session
    await get_event_bus().publish(
        PURSUIT_DECIDED,
        {
            "tenant_id": str(user.tenant_id),
            "pursuit_id": str(pursuit.id),
            "profile_id": str(pursuit.profile_id),
            "opportunity_id": str(pursuit.opportunity_id),
            "decision": body.decision,
            "decided_by": str(user.id),
            "stage": pursuit.stage,
        },
    )
    mode, task_id, result = "none", None, None
    if resumed is not None:
        mode, task_id, result = await dispatch_run(
            request, settings, resumed.id, user.tenant_id, inline=body.inline
        )
    session.expire_all()
    pursuit = await pursuit_svc.get_pursuit(session, pursuit_id)
    return DecisionOut(
        pursuit=await load_pursuit_out(session, pursuit, user.tenant_id),
        decision=body.decision,
        previous_stage=previous_stage,
        resumed_run_id=None if resumed is None else resumed.id,
        mode=mode,
        task_id=task_id,
        result=result,
    )


class ApprovePackageIn(BaseModel):
    note: str | None = Field(default=None, max_length=2000)
    # run the resumed pipeline in the API process (tests / no worker); otherwise queued
    inline: bool = False


class ApprovePackageOut(BaseModel):
    pursuit: PursuitOut
    previous_stage: str
    approved_sections: int
    resumed_run_id: uuid.UUID | None
    mode: str  # queued | inline | none
    task_id: str | None = None
    result: dict[str, Any] | None = None


@router.post("/{pursuit_id}/approve-package", response_model=ApprovePackageOut)
async def approve_package(
    pursuit_id: uuid.UUID,
    body: ApprovePackageIn,
    session: TenantSessionDep,
    user: ManagerDep,
    request: Request,
    settings: SettingsDep,
) -> ApprovePackageOut:
    """Gate 2 (SPEC 8): a human reviewed, edited and approved the whole draft package.

    Records who approved it and when, marks every section approved, moves the pursuit to
    Final approval and resumes the run the red-team step left paused at Gate 2. Exports
    keep their "DRAFT - internal" footer until the package is additionally marked final.
    """
    pursuit = await pursuit_svc.get_pursuit(session, pursuit_id)
    report = await pursuit_svc.latest_artifact(session, pursuit.id, ARTIFACT_RED_TEAM)
    if report is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="the red-team reviewer has not run yet; there is no package to approve",
        )
    await ensure_user_membership(
        user_id=user.id, email=user.email, tenant_id=user.tenant_id, role=user.role
    )
    previous_stage = pursuit_svc.approve_package(pursuit, user.id, role=user.role)
    approved = await pursuit_svc.approve_drafts(session, pursuit.id, user.id)
    run = await pursuit_svc.latest_run(session, pursuit.id)
    resumed: AgentRun | None = None
    if run is not None and run.status == RUN_PAUSED:
        run.status = RUN_QUEUED
        resumed = run
    request.state.audit = AuditHint(
        action="pursuit.package_approved",
        object_type="pursuit",
        object_id=str(pursuit.id),
        meta={
            "previous_stage": previous_stage,
            "stage": pursuit.stage,
            "approved_sections": approved,
            "red_team_version": report.version,
            "note": body.note,
            "run_id": None if resumed is None else str(resumed.id),
        },
    )
    await session.commit()  # the worker / inline job reads the run in its own session
    mode, task_id, result = "none", None, None
    if resumed is not None:
        mode, task_id, result = await dispatch_run(
            request, settings, resumed.id, user.tenant_id, inline=body.inline
        )
    session.expire_all()
    pursuit = await pursuit_svc.get_pursuit(session, pursuit_id)
    return ApprovePackageOut(
        pursuit=await load_pursuit_out(session, pursuit, user.tenant_id),
        previous_stage=previous_stage,
        approved_sections=approved,
        resumed_run_id=None if resumed is None else resumed.id,
        mode=mode,
        task_id=task_id,
        result=result,
    )


async def _matrix_artifacts(
    session: AsyncSession, pursuit: Pursuit
) -> tuple[FormatRules, list[ChecklistItem], int | None, int | None, datetime | None]:
    rules_row = await pursuit_svc.latest_artifact(session, pursuit.id, ARTIFACT_FORMAT_RULES)
    checklist_row = await pursuit_svc.latest_artifact(session, pursuit.id, ARTIFACT_CHECKLIST)
    rules = FormatRules() if rules_row is None else FormatRules.model_validate(rules_row.data)
    checklist = (
        []
        if checklist_row is None
        else [
            ChecklistItem.model_validate(row) for row in (checklist_row.data or {}).get("items", [])
        ]
    )
    generated = max(
        (a.created_at for a in (rules_row, checklist_row) if a is not None), default=None
    )
    return (
        rules,
        checklist,
        None if rules_row is None else rules_row.version,
        None if checklist_row is None else checklist_row.version,
        generated,
    )


@router.get("/{pursuit_id}/matrix", response_model=PursuitMatrixOut)
async def get_matrix(
    pursuit_id: uuid.UUID, session: TenantSessionDep, user: ReaderDep
) -> PursuitMatrixOut:
    """The compliance matrix with the solicitation's format rules and the region's
    submission checklist. Empty until the matrix agent has run."""
    pursuit = await pursuit_svc.get_pursuit(session, pursuit_id)
    rows = (
        await session.execute(
            select(ComplianceItem, Requirement)
            .join(Requirement, Requirement.id == ComplianceItem.requirement_id)
            .where(ComplianceItem.pursuit_id == pursuit.id)
            .order_by(Requirement.req_id)
        )
    ).all()
    rules, checklist, rules_version, checklist_version, generated = await _matrix_artifacts(
        session, pursuit
    )
    return PursuitMatrixOut(
        pursuit_id=pursuit.id,
        items=[
            MatrixRowOut(
                id=item.id,
                requirement_id=req.id,
                req_id=req.req_id,
                text=req.text,
                type=req.type,
                volume=req.volume,
                document_id=req.document_id,
                page=req.page,
                quote=req.quote,
                section=item.section,
                reason=item.reason,
                owner_user_id=item.owner_user_id,
                status=item.status,
                notes=item.notes,
            )
            for item, req in rows
        ],
        format_rules=None if rules_version is None else rules,
        format_rules_version=rules_version,
        checklist=checklist,
        checklist_version=checklist_version,
        generated_at=generated,
    )


@router.get("/{pursuit_id}/packet", response_model=PursuitPacketOut)
async def get_packet(
    pursuit_id: uuid.UUID, session: TenantSessionDep, user: ReaderDep
) -> PursuitPacketOut:
    """What to upload where, the portal link, the signatures / DSC steps and the final
    deadline in the buyer's and the reader's time zone.

    Read-only by construction: the route makes no outbound call and nothing it returns
    submits anything (SPEC 1 -- a human always submits on the portal).
    """
    pursuit = await pursuit_svc.get_pursuit(session, pursuit_id)
    opportunity = await session.get(Opportunity, pursuit.opportunity_id)
    if opportunity is None:  # pragma: no cover - the FK guarantees it
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="opportunity not found")
    rules, checklist, _rules_version, checklist_version, generated = await _matrix_artifacts(
        session, pursuit
    )
    reader = await session.get(User, user.id)
    packet = build_packet(
        PacketContext(
            region=str(opportunity.region),
            notice_type=str(opportunity.notice_type),
            title=opportunity.title,
            solicitation_number=opportunity.solicitation_number,
            portal_url=opportunity.source_url,
            buyer_tz=opportunity.source_tz,
            user_tz=None if reader is None else reader.tz,
            response_due_at=opportunity.response_due_at,
        ),
        rules,
        checklist,
    )
    return PursuitPacketOut(
        pursuit_id=pursuit.id,
        opportunity_id=opportunity.id,
        packet=packet,
        checklist=checklist,
        checklist_version=checklist_version,
        generated_at=generated,
    )


def _artifact_out(row: PursuitArtifact) -> PursuitArtifactOut:
    return PursuitArtifactOut(
        id=row.id,
        kind=row.kind,
        version=row.version,
        data=row.data or {},
        created_by=row.created_by,
        created_at=row.created_at,
    )


async def _audit_artifact_reads(
    session: AsyncSession, pursuit: Pursuit, rows: Sequence[PursuitArtifact], user: CurrentUser
) -> None:
    """SPEC 11 audits the reading of generated content, not the listing of file names:
    the scorecard (a bid/no-bid judgement about the tenant) and the red-team report (the
    criticism of their own draft) are logged per row read. Format rules, the checklist,
    the packet, the outline and the pricing template are mechanical and are not."""
    for row in rows:
        if row.kind not in AUDITED_ARTIFACT_KINDS:
            continue
        await audit(
            session,
            AUDIT_ARTIFACT_READ,
            row,
            user_id=user.id,
            meta={"pursuit_id": str(pursuit.id), "kind": row.kind, "version": row.version},
        )
    await session.commit()


@router.get("/{pursuit_id}/artifacts", response_model=PursuitArtifactListOut)
async def list_artifacts(
    pursuit_id: uuid.UUID,
    session: TenantSessionDep,
    user: ReaderDep,
    kind: Annotated[str | None, Query(max_length=32)] = None,
) -> PursuitArtifactListOut:
    """The pursuit's stored agent outputs: the LATEST version of each kind, or of `kind`
    alone (one item, or none at all — an empty list, never a 404, so a panel that probes
    for a scorecard that has not been generated renders its empty state).

    Any member of the tenant may read them; RLS keeps the list inside the tenant and
    `pursuit_id` inside the pursuit. Reading a scorecard or a red-team report writes an
    audit row (SPEC 11). Older versions are reachable by id (OQ-147, OQ-151).
    """
    pursuit = await pursuit_svc.get_pursuit(session, pursuit_id)
    if kind is not None and kind not in ARTIFACT_KINDS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"unknown artifact kind {kind!r}; one of {list(ARTIFACT_KINDS)}",
        )
    rows = await pursuit_svc.latest_artifacts(session, pursuit.id, kind)
    await _audit_artifact_reads(session, pursuit, rows, user)
    return PursuitArtifactListOut(
        pursuit_id=pursuit.id,
        items=[_artifact_out(row) for row in rows],
        count=len(rows),
        kind=kind,
    )


@router.get("/{pursuit_id}/artifacts/{artifact_id}", response_model=PursuitArtifactOut)
async def get_artifact(
    pursuit_id: uuid.UUID,
    artifact_id: uuid.UUID,
    session: TenantSessionDep,
    user: ReaderDep,
) -> PursuitArtifactOut:
    """One stored version by id — how an older version of a re-run agent's output is
    read back. 404 when the id belongs to another pursuit or another tenant."""
    pursuit = await pursuit_svc.get_pursuit(session, pursuit_id)
    row = await pursuit_svc.get_artifact(session, pursuit.id, artifact_id)
    await _audit_artifact_reads(session, pursuit, [row], user)
    return _artifact_out(row)


@router.post(
    "/{pursuit_id}/agents/run", response_model=RunAgentsOut, status_code=status.HTTP_202_ACCEPTED
)
async def run_agents(
    pursuit_id: uuid.UUID,
    body: RunAgentsIn,
    session: TenantSessionDep,
    user: ManagerDep,
    request: Request,
    settings: SettingsDep,
) -> RunAgentsOut:
    """Start the pursuit pipeline (SPEC 8, 10.3). The run is queued on Celery
    (`bidradar.run_agents`) or executed in the API process; the cost guard still decides
    before every step, so a run can come back needs_approval having spent nothing."""
    pursuit = await pursuit_svc.get_pursuit(session, pursuit_id)
    specs, _finish = pipeline.plan_steps(
        body.step, gates_cleared=pursuit_svc.cleared_gates(pursuit)
    )
    runner = AgentRunner(tenant_id=user.tenant_id, llm=app_llm(request) or _NO_LLM)
    run_id = await runner.start(
        kind=pursuit_svc.PIPELINE_RUN_KIND, pursuit_id=pursuit.id, params={"step": body.step}
    )
    await session.commit()  # the worker / inline job reads the run in its own session
    request.state.audit = AuditHint(
        action="pursuit.agents_run",
        object_type="pursuit",
        object_id=str(pursuit.id),
        meta={"step": body.step, "run_id": str(run_id)},
    )
    mode, task_id, result = await dispatch_run(
        request, settings, run_id, user.tenant_id, inline=body.inline
    )
    session.expire_all()
    pursuit = await pursuit_svc.get_pursuit(session, pursuit_id)
    return RunAgentsOut(
        run_id=run_id,
        step=body.step,
        steps=[spec.agent for spec in specs],
        mode=mode,
        task_id=task_id,
        result=result,
        pursuit=await load_pursuit_out(session, pursuit, user.tenant_id),
    )


@router.post("/{pursuit_id}/agents/approve-budget", response_model=ApproveBudgetOut)
async def approve_budget(
    pursuit_id: uuid.UUID,
    body: ApproveBudgetIn,
    session: TenantSessionDep,
    user: ManagerDep,
    request: Request,
    settings: SettingsDep,
) -> ApproveBudgetOut:
    """Raise the pursuit's cap by `additional_usd` (audited) and resume the run the cost
    guard left in needs_approval, if any. Never bypasses the tenant's monthly budget."""
    pursuit = await pursuit_svc.get_pursuit(session, pursuit_id)
    tenant = await pursuit_svc.get_tenant(session, user.tenant_id)
    previous = (
        Decimal(pursuit.cost_cap_usd)
        if pursuit.cost_cap_usd is not None
        else Decimal(tenant.pursuit_cost_cap_usd)
    )
    new_cap = pursuit_svc.approve_budget(pursuit, tenant, body.additional_usd)
    run = await pursuit_svc.latest_run(session, pursuit.id)
    resumed: AgentRun | None = None
    if run is not None and run.status == RUN_NEEDS_APPROVAL:
        run.status = RUN_QUEUED
        resumed = run
    await session.commit()  # the worker / inline job reads the run in its own session
    request.state.audit = AuditHint(
        action="pursuit.budget_approved",
        object_type="pursuit",
        object_id=str(pursuit.id),
        meta={
            "additional_usd": str(body.additional_usd),
            "previous_cap_usd": str(previous),
            "new_cap_usd": str(new_cap),
            "reason": body.reason,
            "run_id": None if resumed is None else str(resumed.id),
        },
    )
    mode, task_id, result = "none", None, None
    if resumed is not None:
        mode, task_id, result = await dispatch_run(
            request, settings, resumed.id, user.tenant_id, inline=body.inline
        )
    session.expire_all()  # re-read the run/cost rows the job just wrote
    pursuit = await pursuit_svc.get_pursuit(session, pursuit_id)
    return ApproveBudgetOut(
        pursuit=await load_pursuit_out(session, pursuit, user.tenant_id),
        previous_cap_usd=previous,
        new_cap_usd=new_cap,
        resumed_run_id=None if resumed is None else resumed.id,
        mode=mode,
        task_id=task_id,
        result=result,
    )


# --- M6-01: board actions, stage moves and the pipeline listing -----------------------------


class PursueIn(BaseModel):
    """`profile_id` is optional while the tenant has exactly one profile."""

    profile_id: uuid.UUID | None = None
    # pursuing starts the agent pipeline (SPEC 9); set false to only open the card
    run_agents: bool = True


class WatchIn(BaseModel):
    profile_id: uuid.UUID | None = None


class PassIn(BaseModel):
    profile_id: uuid.UUID | None = None
    reason: str = Field(min_length=1, max_length=1000)
    # qualifying / bid_decision pursuits may be recorded as a formal no-bid instead
    no_bid: bool = False


class PursuitPatchIn(BaseModel):
    stage: str | None = None
    owner_user_id: uuid.UUID | None = None
    internal_due_at: datetime | None = None
    watch: bool | None = None

    @field_validator("stage")
    @classmethod
    def _known_stage(cls, value: str | None) -> str | None:
        if value is not None and not stages.is_stage(value):
            raise ValueError(f"unknown stage {value!r}; one of {', '.join(stages.STAGES)}")
        return value


class AgentsQueuedOut(BaseModel):
    """What POST /pursue did with the pipeline. A board action never runs it inline."""

    enqueued: bool
    run_id: uuid.UUID | None = None
    task_id: str | None = None
    reason: str | None = None


class PursuitActionOut(BaseModel):
    pursuit: PursuitOut
    created: bool
    agents: AgentsQueuedOut | None = None


class PursuitListItem(BaseModel):
    """One card on the board / row in the table (SPEC 9, 10.4 screen 5)."""

    id: uuid.UUID
    profile_id: uuid.UUID
    opportunity_id: uuid.UUID
    stage: str
    owner_user_id: uuid.UUID | None
    decision: str | None
    watch: bool
    pass_reason: str | None
    internal_due_at: TzDateOut | None
    submitted_at: datetime | None
    activity_at: datetime
    matrix_recheck_required: bool
    created_at: datetime
    updated_at: datetime
    # the notice behind the card, so the board needs no second round trip
    title: str
    buyer_org: str | None
    region: Region
    currency: str
    estimated_value_min: Decimal | None
    estimated_value_max: Decimal | None
    estimated_value_min_usd: Decimal | None
    estimated_value_max_usd: Decimal | None
    source_tz: str
    response_due_at: TzDateOut | None


class PursuitPage(BaseModel):
    items: list[PursuitListItem]
    total: int
    page: int
    page_size: int
    pages: int
    # counts per stage over the WHOLE filtered set, so the board header is not paginated
    by_stage: dict[str, int]


async def reader_tz(session: AsyncSession, user: CurrentUser) -> str | None:
    row = await session.get(User, user.id)
    return None if row is None else row.tz


def list_item_out(
    pursuit: Pursuit, opportunity: Opportunity, user_tz: str | None
) -> PursuitListItem:
    buyer_tz = opportunity.source_tz
    return PursuitListItem(
        id=pursuit.id,
        profile_id=pursuit.profile_id,
        opportunity_id=pursuit.opportunity_id,
        stage=pursuit.stage,
        owner_user_id=pursuit.owner_user_id,
        decision=pursuit.decision,
        watch=pursuit.watch,
        pass_reason=pursuit.pass_reason,
        internal_due_at=tz_fields_or_none(pursuit.internal_due_at, buyer_tz, user_tz),
        submitted_at=pursuit.submitted_at,
        activity_at=pursuit.activity_at,
        matrix_recheck_required=pursuit.matrix_recheck_required,
        created_at=pursuit.created_at,
        updated_at=pursuit.updated_at,
        title=opportunity.title,
        buyer_org=opportunity.buyer_org,
        region=Region(opportunity.region),
        currency=opportunity.currency,
        estimated_value_min=opportunity.estimated_value_min,
        estimated_value_max=opportunity.estimated_value_max,
        estimated_value_min_usd=opportunity.estimated_value_min_usd,
        estimated_value_max_usd=opportunity.estimated_value_max_usd,
        source_tz=buyer_tz,
        response_due_at=tz_fields_or_none(opportunity.response_due_at, buyer_tz, user_tz),
    )


async def enqueue_pipeline(
    request: Request,
    settings: Settings,
    session: AsyncSession,
    pursuit: Pursuit,
    user: CurrentUser,
) -> AgentsQueuedOut:
    """Hand the whole pipeline to Celery. Never inline: Pursue is a board click, not a
    30-minute request. A plan with no agent budget opens the card and says so."""
    tenant = await pursuit_svc.get_tenant(session, user.tenant_id)
    budget = await PlanService(session).limit_for(tenant, Resource.AGENT_BUDGET_USD_MONTH)
    if budget is not None and budget <= 0:
        return AgentsQueuedOut(enqueued=False, reason=f"the {tenant.plan} plan has no agent budget")
    runner = AgentRunner(tenant_id=user.tenant_id, llm=app_llm(request) or _NO_LLM)
    run_id = await runner.start(
        kind=pursuit_svc.PIPELINE_RUN_KIND,
        pursuit_id=pursuit.id,
        params={"step": pipeline.STEP_ALL},
    )
    if settings.celery_task_always_eager:
        # an eager task would call asyncio.run inside this event loop (OQ-82)
        return AgentsQueuedOut(
            enqueued=False, run_id=run_id, reason="celery eager: run left queued"
        )
    task_id = run_agents_job_module.enqueue_agents(run_id, user.tenant_id)
    if task_id is None:
        return AgentsQueuedOut(
            enqueued=False, run_id=run_id, reason="broker unreachable: run left queued"
        )
    return AgentsQueuedOut(enqueued=True, run_id=run_id, task_id=task_id)


async def open_pursuit(
    session: AsyncSession,
    user: CurrentUser,
    opportunity_id: uuid.UUID,
    profile_id: uuid.UUID | None,
    *,
    require_biddable: bool = False,
) -> tuple[Pursuit, Opportunity, bool]:
    """Create or reuse the tenant's pursuit of this notice with the chosen profile."""
    opportunity = await session.get(Opportunity, opportunity_id)
    if opportunity is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="opportunity not found")
    profile = await pursuit_svc.resolve_profile(session, user, profile_id)
    if require_biddable:  # SPEC 4.1: an expired SAM / DSC blocks bidding (M6-06)
        pursuit_svc.ensure_not_blocked(profile)
    pursuit, created = await pursuit_svc.get_or_create(session, profile.id, opportunity.id, user)
    if created or pursuit.internal_due_at is None:
        pursuit.internal_due_at = pursuit_svc.internal_due_at(opportunity.response_due_at)
    if pursuit.owner_user_id is None:
        pursuit.owner_user_id = user.id
    # SPEC 9: the key dates exist from the moment the card does (M6-02)
    await key_date_svc.sync_auto_dates(session, pursuit, opportunity)
    return pursuit, opportunity, created


@opportunity_router.post("/{opportunity_id}/pursue", response_model=PursuitActionOut)
async def pursue_opportunity(
    opportunity_id: uuid.UUID,
    body: PursueIn,
    session: TenantSessionDep,
    user: ManagerDep,
    request: Request,
    settings: SettingsDep,
) -> JSONResponse:
    """Track this notice and start the agent workflow (SPEC 9).

    Idempotent per (profile, opportunity): pursuing again reuses the card, clears the
    watch flag and re-enqueues the pipeline.
    """
    pursuit, opportunity, created = await open_pursuit(
        session, user, opportunity_id, body.profile_id, require_biddable=True
    )
    pursuit.watch = False
    pursuit_svc.touch(pursuit)
    if stages.is_terminal(pursuit.stage):
        pursuit_svc.move_stage(pursuit, stages.STAGE_QUALIFYING, role=user.role)
        pursuit.pass_reason = None
    request.state.audit = AuditHint(
        action="pursuit.pursued",
        object_type="pursuit",
        object_id=str(pursuit.id),
        meta={"opportunity_id": str(opportunity.id), "created": created},
    )
    agents: AgentsQueuedOut | None = None
    if body.run_agents:
        pursuit_id = pursuit.id
        await session.commit()  # the runner opens its own session and needs the FK to exist
        pursuit = await pursuit_svc.get_pursuit(session, pursuit_id)
        agents = await enqueue_pipeline(request, settings, session, pursuit, user)
        session.expire_all()
        pursuit = await pursuit_svc.get_pursuit(session, pursuit_id)
    out = PursuitActionOut(
        pursuit=await load_pursuit_out(session, pursuit, user.tenant_id),
        created=created,
        agents=agents,
    )
    code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return JSONResponse(status_code=code, content=out.model_dump(mode="json"))


@opportunity_router.post("/{opportunity_id}/watch", response_model=PursuitActionOut)
async def watch_opportunity(
    opportunity_id: uuid.UUID,
    body: WatchIn,
    session: TenantSessionDep,
    user: WriterDep,
    request: Request,
) -> JSONResponse:
    """Follow the notice for amendments and reminders without starting any agent work."""
    pursuit, opportunity, created = await open_pursuit(
        session, user, opportunity_id, body.profile_id
    )
    pursuit.watch = True
    pursuit_svc.touch(pursuit)
    request.state.audit = AuditHint(
        action="pursuit.watched",
        object_type="pursuit",
        object_id=str(pursuit.id),
        meta={"opportunity_id": str(opportunity.id), "created": created},
    )
    out = PursuitActionOut(
        pursuit=await load_pursuit_out(session, pursuit, user.tenant_id), created=created
    )
    code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return JSONResponse(status_code=code, content=out.model_dump(mode="json"))


@opportunity_router.post("/{opportunity_id}/pass", response_model=PursuitActionOut)
async def pass_opportunity(
    opportunity_id: uuid.UUID,
    body: PassIn,
    session: TenantSessionDep,
    user: ManagerDep,
    request: Request,
) -> JSONResponse:
    """Drop the notice with a reason: `cancelled` by default, `no_bid` when the pursuit
    is still at qualifying / bid_decision and the caller asks for a formal no-bid."""
    pursuit, opportunity, created = await open_pursuit(
        session, user, opportunity_id, body.profile_id
    )
    pursuit.watch = False
    pursuit.pass_reason = body.reason
    target = stages.STAGE_NO_BID if body.no_bid else stages.STAGE_CANCELLED
    pursuit_svc.move_stage(pursuit, target, role=user.role)
    request.state.audit = AuditHint(
        action="pursuit.passed",
        object_type="pursuit",
        object_id=str(pursuit.id),
        meta={
            "opportunity_id": str(opportunity.id),
            "reason": body.reason,
            "stage": pursuit.stage,
            "created": created,
        },
    )
    out = PursuitActionOut(
        pursuit=await load_pursuit_out(session, pursuit, user.tenant_id), created=created
    )
    code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return JSONResponse(status_code=code, content=out.model_dump(mode="json"))


@router.get("", response_model=PursuitPage)
async def list_pursuits(
    session: TenantSessionDep,
    user: ReaderDep,
    owner: Annotated[uuid.UUID | None, Query(description="owner_user_id")] = None,
    stage: Annotated[str | None, Query(description="stages, comma-separated")] = None,
    due_before: datetime | None = None,
    region: Region | None = None,
    min_value: Annotated[Decimal | None, Query(ge=0, description="USD")] = None,
    watch: bool | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_LIST_PAGE_SIZE)] = DEFAULT_LIST_PAGE_SIZE,
) -> PursuitPage:
    """The pipeline, filtered by owner / stage / due date / region / value (SPEC 9).

    The board groups by stage in the browser; `by_stage` counts the whole filtered set so
    the column headers do not change with the page.
    """
    wanted = [part.strip() for part in (stage or "").split(",") if part.strip()]
    for name in wanted:
        if not stages.is_stage(name):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"unknown stage {name!r}; one of {', '.join(stages.STAGES)}",
            )
    filters: dict[str, Any] = {
        "owner_user_id": owner,
        "stage": wanted,
        "due_before": due_before,
        "region": None if region is None else region.value,
        "min_value_usd": min_value,
        "watch": watch,
    }
    base = pursuit_svc.list_statement(**filters)
    total = (
        await session.execute(
            select(func.count()).select_from(base.with_only_columns(Pursuit.id).subquery())
        )
    ).scalar_one()
    count_rows = (
        await session.execute(
            base.with_only_columns(Pursuit.stage, func.count()).group_by(Pursuit.stage)
        )
    ).all()
    by_stage = {str(row[0]): int(row[1]) for row in count_rows}
    rows = (
        await session.execute(
            base.order_by(Opportunity.response_due_at.asc().nullslast(), Pursuit.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    tz = await reader_tz(session, user)
    return PursuitPage(
        items=[list_item_out(pursuit, opportunity, tz) for pursuit, opportunity in rows],
        total=total,
        page=page,
        page_size=page_size,
        pages=max(1, -(-total // page_size)),
        by_stage=by_stage,
    )


@router.patch("/{pursuit_id}", response_model=PursuitOut)
async def update_pursuit(
    pursuit_id: uuid.UUID,
    body: PursuitPatchIn,
    session: TenantSessionDep,
    user: WriterDep,
    request: Request,
) -> PursuitOut:
    """Move the card and reassign it. A refused move answers 409 with
    {error: stage_transition, from, to, reason} — the board shows `reason` on the drop."""
    pursuit = await pursuit_svc.get_pursuit(session, pursuit_id)
    before = pursuit.stage
    meta: dict[str, Any] = {}
    if body.owner_user_id is not None:
        if user.role not in MANAGER_ROLES:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, detail="only a bid manager may assign a pursuit"
            )
        owner = await session.get(User, body.owner_user_id)
        if owner is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="owner not found")
        pursuit.owner_user_id = owner.id
        meta["owner_user_id"] = str(owner.id)
    if body.internal_due_at is not None:
        if body.internal_due_at.tzinfo is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="internal_due_at must carry a time zone offset",
            )
        pursuit.internal_due_at = body.internal_due_at.astimezone(UTC)
        meta["internal_due_at"] = pursuit.internal_due_at.isoformat()
    if body.watch is not None:
        pursuit.watch = body.watch
        meta["watch"] = body.watch
    pursuit_svc.touch(pursuit)
    if body.stage is not None and pursuit_svc.move_stage(pursuit, body.stage, role=user.role):
        meta["stage"] = {"from": before, "to": pursuit.stage}
    request.state.audit = AuditHint(
        action="pursuit.updated", object_type="pursuit", object_id=str(pursuit.id), meta=meta
    )
    return await load_pursuit_out(session, pursuit, user.tenant_id)
