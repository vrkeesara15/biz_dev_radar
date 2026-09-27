"""Pursuits API (SPEC 10.3, M5): the pursuit record with its cost meter, the agent runs
and the budget approval that resumes a run the cost guard paused.

    POST /api/v1/pursuits                                  {profile_id, opportunity_id}
    GET  /api/v1/pursuits/{pursuit_id}                     cost_so_far, cap, budget, latest run
    GET  /api/v1/pursuits/{pursuit_id}/matrix              matrix + format rules + checklist
    GET  /api/v1/pursuits/{pursuit_id}/packet              uploads, portal, signatures, deadline
    POST /api/v1/pursuits/{pursuit_id}/agents/run          {step: collect|...|all}
    POST /api/v1/pursuits/{pursuit_id}/agents/approve-budget {additional_usd, reason?}
    PATCH /api/v1/pursuits/{pursuit_id}                    {stage}
    POST /api/v1/pursuits/{pursuit_id}/decision            {decision: bid|no_bid, note?}
    POST /api/v1/pursuits/{pursuit_id}/approve-package      Gate 2: the reviewed package

M6-01 adds POST /opportunities/{id}/pursue|watch|pass and the full stage rules on top of
`services.pursuits.check_stage_transition`; the second M5 pass adds drafts and exports.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import pipeline
from app.agents.cost_guard import CostSnapshot, cost_snapshot
from app.agents.llm import LLMClient
from app.agents.runner import AgentRunner
from app.agents.services import AgentServices, services_from_settings
from app.api.deps import TENANT_ROLES, CurrentUser, SettingsDep, TenantSessionDep, require_role
from app.core.compliance import (
    ARTIFACT_CHECKLIST,
    ARTIFACT_FORMAT_RULES,
    ARTIFACT_RED_TEAM,
    ChecklistItem,
    FormatRules,
)
from app.core.config import Settings
from app.core.packet import Packet, PacketContext, build_packet
from app.core.roles import Role
from app.jobs import run_agents as run_agents_job_module
from app.models import (
    AgentRun,
    CompanyProfile,
    ComplianceItem,
    Opportunity,
    Pursuit,
    Requirement,
    User,
)
from app.models.agents import RUN_NEEDS_APPROVAL, RUN_PAUSED, RUN_QUEUED
from app.models.pursuit import DECISION_BID, DECISIONS
from app.services import drafts as draft_svc
from app.services import pursuits as pursuit_svc
from app.services.audit import AuditHint
from app.services.drafts import DraftsSummary
from app.services.events import PURSUIT_DECIDED, get_event_bus
from app.services.users import ensure_user_membership

router = APIRouter(prefix="/pursuits", tags=["pursuits"])

# SPEC 3: bid managers (and owners) decide, assign and approve; every tenant role reads.
MANAGER_ROLES = (Role.TENANT_OWNER, Role.BID_MANAGER)
ManagerDep = Annotated[CurrentUser, Depends(require_role(*MANAGER_ROLES))]
ReaderDep = Annotated[CurrentUser, Depends(require_role(*TENANT_ROLES))]

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


class PursuitPatchIn(BaseModel):
    """M5-06 keeps this small on purpose: M6-01 adds key dates and the rest of the board."""

    stage: str | None = Field(default=None, max_length=32)
    owner_user_id: uuid.UUID | None = None


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


@router.patch("/{pursuit_id}", response_model=PursuitOut)
async def patch_pursuit(
    pursuit_id: uuid.UUID,
    body: PursuitPatchIn,
    session: TenantSessionDep,
    user: ManagerDep,
    request: Request,
) -> PursuitOut:
    """Move a pursuit through the pipeline stages (SPEC 9; M6-01 adds the full board).

    The stage rules live in `services.pursuits.check_stage_transition`: entering Drafting
    without an approved bid decision is 409 (Gate 1).
    """
    pursuit = await pursuit_svc.get_pursuit(session, pursuit_id)
    meta: dict[str, Any] = {}
    if body.stage is not None:
        meta["previous_stage"] = pursuit_svc.move_stage(pursuit, body.stage)
        meta["stage"] = body.stage
    if body.owner_user_id is not None:
        pursuit.owner_user_id = body.owner_user_id
        meta["owner_user_id"] = str(body.owner_user_id)
    request.state.audit = AuditHint(
        action="pursuit.updated", object_type="pursuit", object_id=str(pursuit.id), meta=meta
    )
    await session.flush()
    await session.refresh(pursuit)  # updated_at is a server-side onupdate
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
    previous_stage = pursuit_svc.record_decision(pursuit, body.decision, user.id, note=body.note)
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
    previous_stage = pursuit_svc.approve_package(pursuit, user.id)
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
