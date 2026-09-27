"""Pursuits API (SPEC 10.3, M5): the pursuit record with its cost meter, the agent runs
and the budget approval that resumes a run the cost guard paused.

    POST /api/v1/pursuits                                  {profile_id, opportunity_id}
    GET  /api/v1/pursuits/{pursuit_id}                     cost_so_far, cap, budget, latest run
    GET  /api/v1/pursuits/{pursuit_id}/matrix              matrix + format rules + checklist
    POST /api/v1/pursuits/{pursuit_id}/agents/approve-budget {additional_usd, reason?}

M6-01 adds POST /opportunities/{id}/pursue|watch|pass, PATCH (stages) and the decision
route; the second M5 pass adds drafts, matrix comments and exports.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.cost_guard import CostSnapshot, cost_snapshot
from app.agents.llm import LLMClient
from app.agents.services import AgentServices, services_from_settings
from app.api.deps import TENANT_ROLES, CurrentUser, SettingsDep, TenantSessionDep, require_role
from app.core.compliance import (
    ARTIFACT_CHECKLIST,
    ARTIFACT_FORMAT_RULES,
    ChecklistItem,
    FormatRules,
)
from app.core.config import Settings
from app.core.roles import Role
from app.jobs import run_agents as run_agents_job_module
from app.models import AgentRun, ComplianceItem, Pursuit, Requirement
from app.models.agents import RUN_NEEDS_APPROVAL, RUN_QUEUED
from app.services import pursuits as pursuit_svc
from app.services.audit import AuditHint

router = APIRouter(prefix="/pursuits", tags=["pursuits"])

# SPEC 3: bid managers (and owners) decide, assign and approve; every tenant role reads.
MANAGER_ROLES = (Role.TENANT_OWNER, Role.BID_MANAGER)
ManagerDep = Annotated[CurrentUser, Depends(require_role(*MANAGER_ROLES))]
ReaderDep = Annotated[CurrentUser, Depends(require_role(*TENANT_ROLES))]

MAX_APPROVAL_USD = Decimal("10000")


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
    cost_usd: Decimal
    tokens_in: int
    tokens_out: int
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class PursuitOut(BaseModel):
    id: uuid.UUID
    profile_id: uuid.UUID
    opportunity_id: uuid.UUID
    stage: str
    owner_user_id: uuid.UUID | None
    decision: str | None
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
    run: RunOut | None


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
        cost_usd=Decimal(run.cost_usd),
        tokens_in=run.tokens_in,
        tokens_out=run.tokens_out,
        started_at=run.started_at,
        finished_at=run.finished_at,
        created_at=run.created_at,
    )


def pursuit_out(pursuit: Pursuit, costs: CostSnapshot, run: AgentRun | None) -> PursuitOut:
    return PursuitOut(
        id=pursuit.id,
        profile_id=pursuit.profile_id,
        opportunity_id=pursuit.opportunity_id,
        stage=pursuit.stage,
        owner_user_id=pursuit.owner_user_id,
        decision=pursuit.decision,
        internal_due_at=pursuit.internal_due_at,
        created_by=pursuit.created_by,
        created_at=pursuit.created_at,
        updated_at=pursuit.updated_at,
        cost_so_far_usd=costs.pursuit_cost_usd,
        cost_cap_usd=costs.pursuit_cap_usd,
        budget_month_limit_usd=costs.month_budget_usd,
        budget_month_spent_usd=costs.month_spent_usd,
        budget_month_remaining_usd=costs.month_remaining_usd,
        run=None if run is None else run_out(run),
    )


async def load_pursuit_out(
    session: AsyncSession, pursuit: Pursuit, tenant_id: uuid.UUID
) -> PursuitOut:
    tenant = await pursuit_svc.get_tenant(session, tenant_id)
    costs = await cost_snapshot(session, pursuit, tenant)
    return pursuit_out(pursuit, costs, await pursuit_svc.latest_run(session, pursuit.id))


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
    rules_artifact = await pursuit_svc.latest_artifact(session, pursuit.id, ARTIFACT_FORMAT_RULES)
    checklist_artifact = await pursuit_svc.latest_artifact(session, pursuit.id, ARTIFACT_CHECKLIST)
    generated = max(
        (a.created_at for a in (rules_artifact, checklist_artifact) if a is not None),
        default=None,
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
        format_rules=None
        if rules_artifact is None
        else FormatRules.model_validate(rules_artifact.data),
        format_rules_version=None if rules_artifact is None else rules_artifact.version,
        checklist=[]
        if checklist_artifact is None
        else [
            ChecklistItem.model_validate(row)
            for row in (checklist_artifact.data or {}).get("items", [])
        ],
        checklist_version=None if checklist_artifact is None else checklist_artifact.version,
        generated_at=generated,
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
