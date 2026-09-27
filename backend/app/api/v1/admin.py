"""Platform-admin routes (SPEC 10.3). Every request here goes through the audited
owner-role session; platform admins never see tenant data without support access."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import AdminSessionDep, CurrentUserDep, client_ip, get_app_settings
from app.core.config import Region
from app.core.plan import Plan
from app.core.roles import Role
from app.models import Membership, Tenant
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
