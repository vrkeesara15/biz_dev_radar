"""POST /profiles (plan-limited), GET/PUT /profiles/{id} (SPEC 10.3)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from app.api.deps import TenantSessionDep
from app.api.v1.profiles.common import EditorDep, ReaderDep, get_profile, reject_region_foreign
from app.api.v1.profiles.schemas import ProfileCreate, ProfileOut, ProfileUpdate, ProfileWrite
from app.core.config import Region
from app.core.plan import Resource
from app.models import CompanyProfile, Tenant
from app.services.audit import AuditHint
from app.services.plan import PlanService
from app.services.profiles import (
    PROFILE_COUNTERS,
    apply_changes,
    naics_codes_for,
    profile_completeness,
    publish_profile_changed,
)

router = APIRouter(prefix="/profiles", tags=["profiles"])

# Columns with defaults that a PUT may replace but never null out.
NOT_NULLABLE = (
    "legal_name",
    "scoring_weights",
    "bid_no_bid_weights",
    "required_approver_roles",
    "output_languages",
    "dba_names",
    "addresses",
    "employees_by_country",
    "annual_revenue",
    "audited_fiscal_years",
    "solvency_certificate_available",
    "remote_ok",
    "is_active",
)


async def _out(session: TenantSessionDep, row: CompanyProfile) -> ProfileOut:
    return ProfileOut.from_row(
        row,
        naics_codes=await naics_codes_for(session, row.id),
        completeness=await profile_completeness(session, row),
    )


def _region_checks(body: ProfileWrite, region: Region) -> None:
    errors = body.region_errors(region)
    if errors:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": "region_mismatch", "region": region.value, "fields": errors},
        )


@router.post("", response_model=ProfileOut, status_code=status.HTTP_201_CREATED)
async def create_profile(
    body: ProfileCreate, user: EditorDep, session: TenantSessionDep, request: Request
) -> ProfileOut:
    _region_checks(body, body.region)
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
    return await _out(session, row)


@router.get("", response_model=list[ProfileOut])
async def list_profiles(user: ReaderDep, session: TenantSessionDep) -> list[ProfileOut]:
    """Every profile of the caller's tenant (RLS-scoped), oldest first."""
    rows = (
        (
            await session.execute(
                select(CompanyProfile).order_by(CompanyProfile.created_at, CompanyProfile.id)
            )
        )
        .scalars()
        .all()
    )
    return [await _out(session, row) for row in rows]


@router.get("/{profile_id}", response_model=ProfileOut)
async def read_profile(
    profile_id: uuid.UUID, user: ReaderDep, session: TenantSessionDep
) -> ProfileOut:
    return await _out(session, await get_profile(session, profile_id))


@router.put("/{profile_id}", response_model=ProfileOut)
async def update_profile(
    profile_id: uuid.UUID,
    body: ProfileUpdate,
    user: EditorDep,
    session: TenantSessionDep,
    request: Request,
) -> ProfileOut:
    row = await get_profile(session, profile_id)
    _region_checks(body, row.region)
    changes = body.changes()
    reject_region_foreign(row.region, set(changes))
    for name in NOT_NULLABLE:
        if changes.get(name, "x") is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"{name} cannot be null"
            )
    written = apply_changes(row, changes)
    await session.flush()
    await session.refresh(row)
    if written:  # the version bumped: M4-06 re-scores the open corpus after this commit
        await publish_profile_changed(session, row, fields=written)
    request.state.audit = AuditHint(
        action="profile.update",
        object_type="company_profile",
        object_id=str(row.id),
        meta={"fields": written},
    )
    return await _out(session, row)
