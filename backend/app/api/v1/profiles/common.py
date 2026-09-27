"""Shared dependencies and helpers for the profile routers."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status

from app.api.deps import TENANT_ROLES, CurrentUser, TenantSessionDep, require_role
from app.core.config import Region
from app.core.profile_fields import region_foreign_fields
from app.core.roles import Role
from app.models import CompanyProfile

# SPEC section 3: tenant owner and bid manager configure the company profile; writers may
# upload past performance and resumes (evidence sub-resources).
PROFILE_EDIT_ROLES = (Role.TENANT_OWNER, Role.BID_MANAGER)
EVIDENCE_EDIT_ROLES = (*PROFILE_EDIT_ROLES, Role.WRITER)

EditorDep = Annotated[CurrentUser, Depends(require_role(*PROFILE_EDIT_ROLES))]
EvidenceEditorDep = Annotated[CurrentUser, Depends(require_role(*EVIDENCE_EDIT_ROLES))]
ReaderDep = Annotated[CurrentUser, Depends(require_role(*TENANT_ROLES))]


def region_mismatch(region: Region, fields: list[str]) -> HTTPException:
    return HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={
            "error": "region_mismatch",
            "region": region.value,
            "fields": fields,
            "message": f"fields not available for region {region.value!r}: " + ", ".join(fields),
        },
    )


def reject_region_foreign(region: Region, field_names: set[str]) -> None:
    foreign = region_foreign_fields(region, field_names)
    if foreign:
        raise region_mismatch(region, foreign)


async def get_profile(session: TenantSessionDep, profile_id: uuid.UUID) -> CompanyProfile:
    row = await session.get(CompanyProfile, profile_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="profile not found")
    return row


def bump_version(profile: CompanyProfile) -> None:
    profile.version = (profile.version or 1) + 1
