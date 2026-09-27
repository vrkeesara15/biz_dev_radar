"""Company profiles API (SPEC 10.3): POST /profiles (plan-limited), GET/PUT /profiles/{id}.

Region gating: fields exclusive to the other region answer 422 with the offending names.
Encrypted fields are masked in every response; a masked value written back is a no-op.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.deps import TENANT_ROLES, CurrentUser, TenantSessionDep, require_role
from app.api.v1.profiles.schemas import ProfileCreate, ProfileOut, ProfileUpdate
from app.core.config import Region
from app.core.plan import Resource
from app.core.profile_fields import region_foreign_fields
from app.core.roles import Role
from app.models import CompanyProfile, Tenant
from app.services.audit import AuditHint
from app.services.plan import PlanService
from app.services.profiles import PROFILE_COUNTERS, apply_changes

router = APIRouter(prefix="/profiles", tags=["profiles"])

# SPEC section 3: tenant owner and bid manager configure the company profile.
PROFILE_EDIT_ROLES = (Role.TENANT_OWNER, Role.BID_MANAGER)
EditorDep = Annotated[CurrentUser, Depends(require_role(*PROFILE_EDIT_ROLES))]
ReaderDep = Annotated[CurrentUser, Depends(require_role(*TENANT_ROLES))]


def reject_region_foreign(region: Region, field_names: set[str]) -> None:
    foreign = region_foreign_fields(region, field_names)
    if foreign:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error": "region_mismatch",
                "region": region.value,
                "fields": foreign,
                "message": f"fields not available for region {region.value!r}: "
                + ", ".join(foreign),
            },
        )


async def get_profile(session: TenantSessionDep, profile_id: uuid.UUID) -> CompanyProfile:
    row = await session.get(CompanyProfile, profile_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="profile not found")
    return row


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
