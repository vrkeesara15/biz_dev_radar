"""Platform-admin routes (SPEC 10.3, console screen 10.4.9). Every request here goes
through the audited owner-role session; platform admins never see tenant data without
support access, and the one route that reads tenant-scoped rows (the tenant audit log)
is gated on an unexpired support_access_grants row."""

from __future__ import annotations

import uuid
from collections.abc import Container
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.adapters import registry
from app.adapters.registry import AdapterNotFoundError, load_builtin_adapters
from app.api.deps import (
    AdminSessionDep,
    AdminWriteSessionDep,
    CurrentUser,
    CurrentUserDep,
    SettingsDep,
    client_ip,
    get_app_settings,
    require_role,
)
from app.core.admin import (
    DEFAULT_SUPPORT_MINUTES,
    MAX_SUPPORT_MINUTES,
    MIN_SUPPORT_MINUTES,
    clamp_support_minutes,
    parse_period,
    usd_from_microusd,
)
from app.core.config import Region
from app.core.db import get_database
from app.core.plan import Plan
from app.core.roles import Role
from app.jobs import run_source as run_source_job_module
from app.models import Membership, Source, SourceRun, SupportAccessGrant, Tenant
from app.services import admin as admin_svc
from app.services import sources as source_svc
from app.services.audit import (
    SUPPORT_ACCESS_ACTION,
    AuditHint,
    TenantNotFoundError,
    support_access_session,
    write_audit,
)

router = APIRouter(prefix="/admin", tags=["admin"])

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100

PageParam = Annotated[int, Query(ge=1)]
PageSizeParam = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]

TENANT_UPDATE_ACTION = "admin.tenant.update"


def _pages(total: int, page_size: int) -> int:
    return max(1, -(-total // page_size))


class TenantOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    region: Region
    plan: Plan
    is_internal: bool


class TenantRowOut(TenantOut):
    data_residency: Region
    member_count: int
    profile_count: int
    created_at: datetime
    deleted_at: datetime | None


class TenantPage(BaseModel):
    items: list[TenantRowOut]
    total: int
    page: int
    page_size: int
    pages: int


class UsageRowOut(BaseModel):
    tenant_id: uuid.UUID
    slug: str
    name: str
    plan: Plan
    region: Region
    tokens_in: int
    tokens_out: int
    # usage_ledger meters LLM spend in integer micro-dollars (OQ-46); cost_usd is derived.
    cost_microusd: int
    cost_usd: float
    agent_runs: int
    # SPEC 10.4 screen 9 also lists notifications sent; the table arrives with M4.
    notifications: int | None = None


class UsagePage(BaseModel):
    period: str
    items: list[UsageRowOut]
    total_tokens_in: int
    total_tokens_out: int
    total_cost_microusd: int
    total_cost_usd: float
    total_agent_runs: int


class BillingStatusOut(BaseModel):
    provider: str
    status: str
    plan: Plan | None
    current_period_end: datetime | None
    has_subscription: bool


class SupportGrantOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    admin_user_id: uuid.UUID
    reason: str
    granted_at: datetime
    expires_at: datetime


class TenantDetailOut(BaseModel):
    tenant: TenantRowOut
    plan_limits: dict[str, int | None]
    period: str
    usage: UsageRowOut
    billing: BillingStatusOut | None
    support_access: SupportGrantOut | None


class TenantPatchIn(BaseModel):
    plan: Plan | None = None
    is_internal: bool | None = None


class SupportAccessIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
    minutes: int | None = Field(
        default=DEFAULT_SUPPORT_MINUTES, ge=MIN_SUPPORT_MINUTES, le=MAX_SUPPORT_MINUTES
    )


class SupportAccessOut(BaseModel):
    tenant: TenantOut
    member_count: int
    reason: str
    grant: SupportGrantOut


class AuditRowOut(BaseModel):
    id: uuid.UUID
    at: datetime
    user_id: uuid.UUID | None
    action: str
    object_type: str | None
    object_id: str | None
    ip: str | None
    request_id: str | None


class AuditLogPage(BaseModel):
    items: list[AuditRowOut]
    total: int
    page: int
    page_size: int
    pages: int
    grant: SupportGrantOut


def _tenant_out(t: Tenant) -> TenantOut:
    return TenantOut(
        id=t.id, slug=t.slug, name=t.name, region=t.region, plan=t.plan, is_internal=t.is_internal
    )


def _tenant_row_out(row: admin_svc.TenantRow) -> TenantRowOut:
    t = row.tenant
    return TenantRowOut(
        id=t.id,
        slug=t.slug,
        name=t.name,
        region=t.region,
        plan=t.plan,
        is_internal=t.is_internal,
        data_residency=t.data_residency,
        member_count=row.member_count,
        profile_count=row.profile_count,
        created_at=t.created_at,
        deleted_at=t.deleted_at,
    )


def _usage_out(row: admin_svc.UsageRow) -> UsageRowOut:
    return UsageRowOut(
        tenant_id=row.tenant_id,
        slug=row.slug,
        name=row.name,
        plan=row.plan,
        region=row.region,
        tokens_in=row.tokens_in,
        tokens_out=row.tokens_out,
        cost_microusd=row.cost_microusd,
        cost_usd=float(row.cost_usd),
        agent_runs=row.agent_runs,
        notifications=row.notifications,
    )


def _grant_out(grant: SupportAccessGrant) -> SupportGrantOut:
    return SupportGrantOut(
        id=grant.id,
        tenant_id=grant.target_tenant_id,
        admin_user_id=grant.admin_user_id,
        reason=grant.reason,
        granted_at=grant.granted_at,
        expires_at=grant.expires_at,
    )


# --- tenants (SPEC 10.3 GET /admin/tenants) --------------------------------------------


@router.get("/tenants", response_model=TenantPage)
async def list_tenants(
    session: AdminSessionDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
    page: PageParam = 1,
    page_size: PageSizeParam = DEFAULT_PAGE_SIZE,
    include_deleted: bool = False,
) -> TenantPage:
    rows, total = await admin_svc.list_tenants(
        session, q=q, page=page, page_size=page_size, include_deleted=include_deleted
    )
    return TenantPage(
        items=[_tenant_row_out(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
        pages=_pages(total, page_size),
    )


@router.get("/tenants/{tenant_id}", response_model=TenantDetailOut)
async def get_tenant(
    tenant_id: uuid.UUID,
    session: AdminSessionDep,
    user: CurrentUserDep,
    period: Annotated[str | None, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")] = None,
) -> TenantDetailOut:
    row = await admin_svc.get_tenant(session, tenant_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="tenant not found")
    resolved = parse_period(period)
    usage = await admin_svc.tenant_usage(session, tenant_id, resolved)
    if usage is None:  # pragma: no cover - the tenant exists, so it is always in the list
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="tenant not found")
    billing = await admin_svc.billing_status(session, tenant_id)
    grant = await admin_svc.active_support_grant(
        session, tenant_id=tenant_id, admin_user_id=user.id
    )
    return TenantDetailOut(
        tenant=_tenant_row_out(row),
        plan_limits=await admin_svc.plan_limits_for(session, row.tenant.plan),
        period=resolved,
        usage=_usage_out(usage),
        billing=BillingStatusOut(**billing) if billing else None,
        support_access=_grant_out(grant) if grant else None,
    )


@router.patch("/tenants/{tenant_id}", response_model=TenantRowOut)
async def update_tenant(
    tenant_id: uuid.UUID,
    body: TenantPatchIn,
    session: AdminWriteSessionDep,
    user: CurrentUserDep,
    request: Request,
    settings: SettingsDep,
) -> TenantRowOut:
    """Change a tenant's plan or internal flag. SPEC 3: managing tenants and plans is
    the platform admin's job; the change is audited by the middleware and, when it
    actually changes something, inside the tenant's own trail too."""
    row = await admin_svc.get_tenant(session, tenant_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="tenant not found")
    tenant = row.tenant
    changes: dict[str, Any] = {}
    if body.plan is not None and body.plan is not tenant.plan:
        changes["plan"] = {"from": tenant.plan.value, "to": body.plan.value}
        tenant.plan = body.plan
    if body.is_internal is not None and body.is_internal != tenant.is_internal:
        changes["is_internal"] = {"from": tenant.is_internal, "to": body.is_internal}
        tenant.is_internal = body.is_internal
    request.state.audit = AuditHint(
        action=TENANT_UPDATE_ACTION,
        object_type="tenant",
        object_id=str(tenant_id),
        meta={"changes": changes},
    )
    if changes:
        # The tenant's own audit trail must show who changed their plan, not only ours.
        await write_audit(
            session,
            tenant_id=tenant_id,
            user_id=user.id,
            action=TENANT_UPDATE_ACTION,
            object_type="tenant",
            object_id=str(tenant_id),
            ip=client_ip(request, settings),
            meta={"changes": changes},
        )
    await session.flush()
    return _tenant_row_out(
        admin_svc.TenantRow(
            tenant=tenant, member_count=row.member_count, profile_count=row.profile_count
        )
    )


@router.post(
    "/tenants/{tenant_id}/support-access",
    response_model=SupportAccessOut,
    status_code=status.HTTP_200_OK,
)
async def support_access(
    tenant_id: uuid.UUID, body: SupportAccessIn, user: CurrentUserDep, request: Request
) -> SupportAccessOut:
    """Open an audited, time-boxed support-access session into a tenant (SPEC 3, 11)."""
    if user.role is not Role.PLATFORM_ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="platform admin only")
    settings = get_app_settings(request)
    try:
        async with support_access_session(
            tenant_id=tenant_id,
            actor_user_id=user.id,
            reason=body.reason,
            ip=client_ip(request, settings),
        ) as session:
            tenant = await session.get(Tenant, tenant_id)
            if tenant is None:  # pragma: no cover - the service already checked
                raise TenantNotFoundError(str(tenant_id))
            members = (
                await session.execute(
                    select(func.count())
                    .select_from(Membership)
                    .where(Membership.tenant_id == tenant_id)
                )
            ).scalar_one()
            grant = await admin_svc.create_support_grant(
                session,
                tenant_id=tenant_id,
                admin_user_id=user.id,
                reason=body.reason,
                minutes=body.minutes,
            )
            grant_out = _grant_out(grant)
            tenant_out = _tenant_out(tenant)
    except TenantNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="tenant not found") from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    request.state.audit = AuditHint(
        action=SUPPORT_ACCESS_ACTION,
        object_type="tenant",
        object_id=str(tenant_id),
        meta={"minutes": clamp_support_minutes(body.minutes), "grant_id": str(grant_out.id)},
    )
    return SupportAccessOut(
        tenant=tenant_out, member_count=members, reason=body.reason, grant=grant_out
    )


async def require_support_access(
    tenant_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(require_role(Role.PLATFORM_ADMIN))],
) -> SupportAccessGrant:
    """SPEC 3: a platform admin reaches tenant-scoped rows only through a live grant."""
    async with get_database().owner_session(tenant_id) as session:
        grant = await admin_svc.active_support_grant(
            session, tenant_id=tenant_id, admin_user_id=user.id
        )
    if grant is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="support access to this tenant has not been granted (or has expired)",
        )
    return grant


SupportAccessDep = Annotated[SupportAccessGrant, Depends(require_support_access)]


@router.get("/tenants/{tenant_id}/audit-log", response_model=AuditLogPage)
async def tenant_audit_log(
    tenant_id: uuid.UUID,
    grant: SupportAccessDep,
    session: AdminSessionDep,
    page: PageParam = 1,
    page_size: PageSizeParam = 50,
) -> AuditLogPage:
    rows, total = await admin_svc.tenant_audit_log(
        session, tenant_id, page=page, page_size=page_size
    )
    return AuditLogPage(
        items=[
            AuditRowOut(
                id=r.id,
                at=r.at,
                user_id=r.user_id,
                action=r.action,
                object_type=r.object_type,
                object_id=r.object_id,
                ip=r.ip,
                request_id=r.request_id,
            )
            for r in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
        pages=_pages(total, page_size),
        grant=_grant_out(grant),
    )


# --- usage and LLM cost (SPEC 10.3 GET /admin/usage) -----------------------------------


@router.get("/usage", response_model=UsagePage)
async def usage(
    session: AdminSessionDep,
    period: Annotated[str | None, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")] = None,
) -> UsagePage:
    resolved = parse_period(period)
    rows = await admin_svc.usage_by_tenant(session, resolved)
    total_micro = sum(r.cost_microusd for r in rows)
    return UsagePage(
        period=resolved,
        items=[_usage_out(r) for r in rows],
        total_tokens_in=sum(r.tokens_in for r in rows),
        total_tokens_out=sum(r.tokens_out for r in rows),
        total_cost_microusd=total_micro,
        total_cost_usd=float(usd_from_microusd(total_micro)),
        total_agent_runs=sum(r.agent_runs for r in rows),
    )


# --- sources (SPEC 10.3 admin: health and runs, "run now") ------------------------------


class SourceRunOut(BaseModel):
    id: uuid.UUID
    source_id: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    fetched: int
    upserted: int
    error_count: int
    last_error: str | None


class SourceRunPage(BaseModel):
    source_id: str
    items: list[SourceRunOut]
    total: int
    page: int
    page_size: int
    pages: int


class SourceOut(BaseModel):
    source_id: str
    region: Region
    schedule: str
    enabled: bool
    registered: bool
    health_status: str
    health_message: str | None
    last_run_at: datetime | None
    last_status: str | None
    watermark_at: datetime | None
    consecutive_failures: int
    runs: list[SourceRunOut]


class RunSourceIn(BaseModel):
    # inline runs the adapter in the API process and waits; otherwise the Celery task is
    # queued and only falls back to inline when no broker answers.
    inline: bool = False


class RunSourceOut(BaseModel):
    source_id: str
    mode: str  # queued | inline
    task_id: str | None = None
    result: dict[str, Any] | None = None


RECENT_RUNS = 5


def _run_out(run: SourceRun) -> SourceRunOut:
    errors = list(run.errors or [])
    return SourceRunOut(
        id=run.id,
        source_id=run.source_id,
        started_at=run.started_at,
        finished_at=run.finished_at,
        status=run.status,
        fetched=run.fetched,
        upserted=run.upserted,
        error_count=len(errors),
        last_error=str(errors[-1].get("message")) if errors else None,
    )


def _source_out(source: Source, registered: Container[str]) -> SourceOut:
    runs = sorted(source.runs, key=lambda r: r.started_at, reverse=True)[:RECENT_RUNS]
    return SourceOut(
        source_id=source.source_id,
        region=source.region,
        schedule=source.schedule,
        enabled=source.enabled,
        registered=source.source_id in registered,
        health_status=source.health_status,
        health_message=source.health_message,
        last_run_at=source.last_run_at,
        last_status=source.last_status,
        watermark_at=source.watermark_at,
        consecutive_failures=source.consecutive_failures,
        runs=[_run_out(r) for r in runs],
    )


@router.get("/sources", response_model=list[SourceOut])
async def list_sources(session: AdminSessionDep) -> list[SourceOut]:
    load_builtin_adapters()
    await source_svc.sync_sources(session)
    rows = (
        (
            await session.execute(
                select(Source).options(selectinload(Source.runs)).order_by(Source.source_id)
            )
        )
        .scalars()
        .all()
    )
    registered = registry.registered()
    return [_source_out(source, registered) for source in rows]


@router.get("/sources/{source_id}/runs", response_model=SourceRunPage)
async def list_source_runs(
    source_id: str,
    session: AdminSessionDep,
    page: PageParam = 1,
    page_size: PageSizeParam = DEFAULT_PAGE_SIZE,
) -> SourceRunPage:
    """Run history of one adapter, newest first (SPEC 10.4 screen 9 'run history')."""
    load_builtin_adapters()
    if (await session.get(Source, source_id)) is None and source_id not in registry.registered():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="unknown source")
    rows, total = await admin_svc.source_runs(session, source_id, page=page, page_size=page_size)
    return SourceRunPage(
        source_id=source_id,
        items=[_run_out(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
        pages=_pages(total, page_size),
    )


@router.post("/sources/{source_id}/run", response_model=RunSourceOut)
async def run_source_now(
    source_id: str,
    _admin: Annotated[CurrentUser, Depends(require_role(Role.PLATFORM_ADMIN))],
    request: Request,
    body: RunSourceIn | None = None,
) -> RunSourceOut:
    """Platform admins only. No owner session here: the job opens its own sessions and
    the audit middleware records the mutation (one row, like every other POST)."""
    load_builtin_adapters()
    try:
        cls = registry.get_adapter_class(source_id)
    except AdapterNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="unknown source") from exc
    if not registry.is_enabled(cls):
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"source {source_id!r} is disabled (stub)"
        )
    request.state.audit = AuditHint(
        action="admin.source.run", object_type="source", object_id=source_id
    )
    inline = bool(body and body.inline)
    if not inline:
        task_id = run_source_job_module.enqueue_run(source_id)
        if task_id is not None:
            return RunSourceOut(source_id=source_id, mode="queued", task_id=task_id)
    result = await run_source_job_module.run_source_job(source_id, mode="inline")
    return RunSourceOut(source_id=source_id, mode="inline", result=result)


# --- system health (SPEC 3 "view system health", screen 9 cards) ------------------------


class HealthCheckOut(BaseModel):
    name: str
    status: str
    detail: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class AdapterHealthOut(BaseModel):
    source_id: str
    region: Region
    enabled: bool
    health_status: str
    health_message: str | None
    last_run_at: datetime | None
    last_status: str | None
    consecutive_failures: int


class HealthOut(BaseModel):
    status: str
    checks: list[HealthCheckOut]
    adapters: list[AdapterHealthOut]


def _check_out(check: admin_svc.Check) -> HealthCheckOut:
    return HealthCheckOut(
        name=check.name, status=check.status, detail=check.detail, meta=check.meta
    )


@router.get("/health", response_model=HealthOut)
async def system_health(session: AdminSessionDep, settings: SettingsDep) -> HealthOut:
    load_builtin_adapters()
    db_check = await admin_svc.check_database(session)
    adapters_check, sources = await admin_svc.adapter_health(session)
    checks = [
        db_check,
        admin_svc.check_broker(settings),
        admin_svc.check_storage(settings),
        adapters_check,
    ]
    worst = "ok"
    for candidate in ("failing", "unconfigured", "degraded"):
        if any(c.status == candidate for c in checks):
            worst = candidate
            break
    return HealthOut(
        status=worst,
        checks=[_check_out(c) for c in checks],
        adapters=[
            AdapterHealthOut(
                source_id=s.source_id,
                region=s.region,
                enabled=s.enabled,
                health_status=s.health_status,
                health_message=s.health_message,
                last_run_at=s.last_run_at,
                last_status=s.last_status,
                consecutive_failures=s.consecutive_failures,
            )
            for s in sources
        ],
    )
