"""Platform-admin routes (SPEC 10.3). Every request here goes through the audited
owner-role session; platform admins never see tenant data without support access."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.adapters import registry
from app.adapters.registry import AdapterNotFoundError, load_builtin_adapters
from app.api.deps import (
    AdminSessionDep,
    CurrentUser,
    CurrentUserDep,
    client_ip,
    get_app_settings,
    require_role,
)
from app.core.config import Region
from app.core.plan import Plan
from app.core.roles import Role
from app.jobs import run_source as run_source_job_module
from app.models import Membership, Source, SourceRun, Tenant
from app.services import sources as source_svc
from app.services.audit import (
    SUPPORT_ACCESS_ACTION,
    AuditHint,
    TenantNotFoundError,
    support_access_session,
)

router = APIRouter(prefix="/admin", tags=["admin"])


class TenantOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    region: Region
    plan: Plan
    is_internal: bool


class SupportAccessIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class SupportAccessOut(BaseModel):
    tenant: TenantOut
    member_count: int
    reason: str


@router.get("/tenants", response_model=list[TenantOut])
async def list_tenants(session: AdminSessionDep) -> list[TenantOut]:
    rows = (await session.execute(select(Tenant).order_by(Tenant.slug))).scalars().all()
    return [_tenant_out(t) for t in rows]


@router.post(
    "/tenants/{tenant_id}/support-access",
    response_model=SupportAccessOut,
    status_code=status.HTTP_200_OK,
)
async def support_access(
    tenant_id: uuid.UUID, body: SupportAccessIn, user: CurrentUserDep, request: Request
) -> SupportAccessOut:
    """Open an audited support-access session into a tenant (SPEC sections 3, 11)."""
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
    except TenantNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="tenant not found") from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    request.state.audit = AuditHint(
        action=SUPPORT_ACCESS_ACTION, object_type="tenant", object_id=str(tenant_id)
    )
    return SupportAccessOut(tenant=_tenant_out(tenant), member_count=members, reason=body.reason)


def _tenant_out(t: Tenant) -> TenantOut:
    return TenantOut(
        id=t.id, slug=t.slug, name=t.name, region=t.region, plan=t.plan, is_internal=t.is_internal
    )


# --- sources (SPEC 10.3 admin: health and runs, "run now") ------------------------------


class SourceRunOut(BaseModel):
    id: uuid.UUID
    started_at: datetime
    finished_at: datetime | None
    status: str
    fetched: int
    upserted: int
    error_count: int
    last_error: str | None


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
        started_at=run.started_at,
        finished_at=run.finished_at,
        status=run.status,
        fetched=run.fetched,
        upserted=run.upserted,
        error_count=len(errors),
        last_error=str(errors[-1].get("message")) if errors else None,
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
    out: list[SourceOut] = []
    for source in rows:
        runs = sorted(source.runs, key=lambda r: r.started_at, reverse=True)[:RECENT_RUNS]
        out.append(
            SourceOut(
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
        )
    return out


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
