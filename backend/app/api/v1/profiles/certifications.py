"""Certifications sub-resource: socio-economic certs (SPEC 4.2) and security/compliance
attestations (SPEC 4.5). Kinds exclusive to the other region are refused with 422."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.profiles.common import PROFILE_EDIT_ROLES, region_mismatch
from app.api.v1.profiles.subresources import crud_router
from app.core.profile_fields import CertificationKind, certification_allowed
from app.models import Certification, CompanyProfile, File


class CertificationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: CertificationKind
    cert_number: Annotated[str | None, Field(max_length=100)] = None
    issued_by: Annotated[str | None, Field(max_length=200)] = None
    level: Annotated[str | None, Field(max_length=32)] = None
    issued_on: date | None = None
    expires_on: date | None = None
    file_id: uuid.UUID | None = None
    notes: Annotated[str | None, Field(max_length=2000)] = None


class CertificationUpdate(CertificationIn):
    kind: CertificationKind | None = None  # type: ignore[assignment]


class CertificationOut(BaseModel):
    id: uuid.UUID
    profile_id: uuid.UUID
    kind: CertificationKind
    cert_number: str | None
    issued_by: str | None
    level: str | None
    issued_on: date | None
    expires_on: date | None
    file_id: uuid.UUID | None
    notes: str | None
    created_at: datetime


async def validate_certification(
    session: AsyncSession, profile: CompanyProfile, changes: dict[str, Any], existing: Any
) -> None:
    kind = changes.get("kind", getattr(existing, "kind", None))
    if kind is not None and not certification_allowed(profile.region, kind):
        raise region_mismatch(profile.region, [f"kind={CertificationKind(kind).value}"])
    issued, expires = changes.get("issued_on"), changes.get("expires_on")
    if existing is not None:
        issued = changes.get("issued_on", existing.issued_on)
        expires = changes.get("expires_on", existing.expires_on)
    if issued and expires and expires < issued:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="expires_on must not precede issued_on"
        )
    await check_file_visible(session, changes.get("file_id"))


async def check_file_visible(session: AsyncSession, file_id: uuid.UUID | None) -> None:
    """A referenced file must exist in this tenant (RLS hides other tenants' rows)."""
    if file_id is not None and await session.get(File, file_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="file_id not found")


router = crud_router(
    name="certifications",
    singular="certification",
    model=Certification,
    create=CertificationIn,
    update=CertificationUpdate,
    out=CertificationOut,
    write_roles=PROFILE_EDIT_ROLES,
    validate=validate_certification,
)
