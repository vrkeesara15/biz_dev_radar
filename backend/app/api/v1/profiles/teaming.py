"""Teaming partners sub-resource (SPEC 4.4): name, UEI/PAN, capabilities, relationship."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.profiles.common import PROFILE_EDIT_ROLES
from app.api.v1.profiles.subresources import crud_router
from app.core.crypto import is_masked, mask_last4
from app.core.geo import normalize_names
from app.core.notice_types import TeamingRole
from app.core.profile_fields import normalize_pan, normalize_uei
from app.models import CompanyProfile, TeamingPartner


class TeamingPartnerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=300)]
    relationship: TeamingRole
    uei: str | None = None
    pan: str | None = None
    capabilities: list[str] = []
    website: Annotated[str | None, Field(max_length=500)] = None
    contact_email: Annotated[str | None, Field(max_length=320)] = None
    notes: Annotated[str | None, Field(max_length=2000)] = None

    @field_validator("uei")
    @classmethod
    def _uei(cls, value: str | None) -> str | None:
        return None if value is None else normalize_uei(value)

    @field_validator("pan")
    @classmethod
    def _pan(cls, value: str | None) -> str | None:
        if value is None or is_masked(value):
            return value
        return normalize_pan(value)

    @field_validator("capabilities")
    @classmethod
    def _capabilities(cls, value: list[str]) -> list[str]:
        return normalize_names(value)


class TeamingPartnerUpdate(TeamingPartnerIn):
    name: Annotated[str | None, Field(min_length=1, max_length=300)] = None  # type: ignore[assignment]
    relationship: TeamingRole | None = None  # type: ignore[assignment]
    capabilities: list[str] | None = None  # type: ignore[assignment]

    @field_validator("capabilities")
    @classmethod
    def _capabilities(cls, value: list[str] | None) -> list[str] | None:  # type: ignore[override]
        return None if value is None else normalize_names(value)


class TeamingPartnerOut(BaseModel):
    id: uuid.UUID
    profile_id: uuid.UUID
    name: str
    relationship: TeamingRole
    uei: str | None
    pan: str | None  # masked
    capabilities: list[str]
    website: str | None
    contact_email: str | None
    notes: str | None
    created_at: datetime


def partner_out(row: TeamingPartner) -> TeamingPartnerOut:
    return TeamingPartnerOut(
        id=row.id,
        profile_id=row.profile_id,
        name=row.name,
        relationship=row.relationship,
        uei=row.uei,
        pan=mask_last4(row.pan),
        capabilities=row.capabilities,
        website=row.website,
        contact_email=row.contact_email,
        notes=row.notes,
        created_at=row.created_at,
    )


async def validate_partner(
    session: AsyncSession, profile: CompanyProfile, changes: dict[str, Any], existing: Any
) -> None:
    # a masked PAN echoed back is a no-op for that field
    if is_masked(changes.get("pan")):
        del changes["pan"]
    for key in ("name", "relationship"):
        if key in changes and changes[key] is None:
            changes.pop(key)


router = crud_router(
    name="teaming-partners",
    singular="teaming_partner",
    model=TeamingPartner,
    create=TeamingPartnerIn,
    update=TeamingPartnerUpdate,
    out=TeamingPartnerOut,
    write_roles=PROFILE_EDIT_ROLES,
    validate=validate_partner,
    order_by=(TeamingPartner.name,),
    to_out=partner_out,
)
