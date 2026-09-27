"""Platform-admin routes (SPEC 10.3). Every request here goes through the audited
owner-role session; platform admins never see tenant drafts without support access."""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import AdminSessionDep
from app.core.config import Region
from app.core.plan import Plan
from app.models import Tenant

router = APIRouter(prefix="/admin", tags=["admin"])


class TenantOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    region: Region
    plan: Plan
    is_internal: bool


@router.get("/tenants", response_model=list[TenantOut])
async def list_tenants(session: AdminSessionDep) -> list[TenantOut]:
    rows = (await session.execute(select(Tenant).order_by(Tenant.slug))).scalars().all()
    return [
        TenantOut(
            id=t.id,
            slug=t.slug,
            name=t.name,
            region=t.region,
            plan=t.plan,
            is_internal=t.is_internal,
        )
        for t in rows
    ]
