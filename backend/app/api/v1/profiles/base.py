"""POST /profiles (plan-limited), GET/PUT /profiles/{id} (SPEC 10.3)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request, status

from app.api.deps import TenantSessionDep
from app.api.v1.profiles.common import EditorDep, ReaderDep, get_profile, reject_region_foreign
from app.api.v1.profiles.schemas import ProfileCreate, ProfileOut, ProfileUpdate
from app.core.plan import Resource
from app.models import CompanyProfile, Tenant
from app.services.audit import AuditHint
from app.services.plan import PlanService
from app.services.profiles import PROFILE_COUNTERS, apply_changes

router = APIRouter(prefix="/profiles", tags=["profiles"])


@router.post("", response_model=ProfileOut, status_code=status.HTTP_201_CREATED)
async def create_profile(
    body: ProfileCreate, user: EditorDep, session: TenantSessionDep, request: Request
) -> ProfileOut:
    changes = body.changes()
    region = changes.pop("region")
    reject_region_foreign(region, set(changes))
    tenant = await session.get(Tenant, user.tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="unknown tenant")
    await PlanService(session, counters=PROFILE_COUNTERS).check(tenant, Resource.PROFILES)
    row = CompanyProfile(tenant_id=tenant.id, region=region, legal_name=changes.pop("legal_name"))
    apply_changes(row, changes)
    row.version = 1
    session.add(row)
    await session.flush()
    await session.refresh(row)
    request.state.audit = AuditHint(
        action="profile.create", object_type="company_profile", object_id=str(row.id)
    )
    return ProfileOut.from_row(row)


@router.get("/{profile_id}", response_model=ProfileOut)
async def read_profile(
    profile_id: uuid.UUID, user: ReaderDep, session: TenantSessionDep
) -> ProfileOut:
    return ProfileOut.from_row(await get_profile(session, profile_id))


@router.put("/{profile_id}", response_model=ProfileOut)
async def update_profile(
    profile_id: uuid.UUID,
    body: ProfileUpdate,
    user: EditorDep,
    session: TenantSessionDep,
    request: Request,
) -> ProfileOut:
    row = await get_profile(session, profile_id)
    changes = body.changes()
    reject_region_foreign(row.region, set(changes))
    if changes.get("legal_name", "x") is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="legal_name cannot be null"
        )
    written = apply_changes(row, changes)
    await session.flush()
    await session.refresh(row)
    request.state.audit = AuditHint(
        action="profile.update",
        object_type="company_profile",
        object_id=str(row.id),
        meta={"fields": written},
    )
    return ProfileOut.from_row(row)
