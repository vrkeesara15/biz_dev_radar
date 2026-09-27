"""Codes (NAICS/PSC/ALN, GeM/India category), keywords and service lines (SPEC 4.3)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.profiles.common import PROFILE_EDIT_ROLES, region_mismatch
from app.api.v1.profiles.subresources import crud_router
from app.core.profile_fields import (
    MAX_KEYWORD_WEIGHT,
    MIN_KEYWORD_WEIGHT,
    CodeScheme,
    DeliveryModel,
    KeywordKind,
    code_scheme_allowed,
    normalize_code,
    normalize_keyword,
    validate_service_description,
)
from app.core.reference import is_valid_naics, naics_title
from app.models import CompanyProfile, ProfileCode, ProfileKeyword, ServiceLine

# --- codes ---------------------------------------------------------------------------------


class CodeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scheme: CodeScheme
    code: Annotated[str, Field(min_length=1, max_length=200)]
    is_primary: bool = False

    @model_validator(mode="after")
    def _normalize(self) -> CodeIn:
        self.code = normalize_code(self.scheme, self.code)
        return self


class CodeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_primary: bool | None = None


class CodeOut(BaseModel):
    id: uuid.UUID
    profile_id: uuid.UUID
    scheme: CodeScheme
    code: str
    title: str | None
    is_primary: bool
    created_at: datetime


async def validate_code(
    session: AsyncSession, profile: CompanyProfile, changes: dict[str, Any], existing: Any
) -> None:
    scheme = CodeScheme(changes["scheme"] if existing is None else existing.scheme)
    if existing is None:
        if not code_scheme_allowed(profile.region, scheme):
            raise region_mismatch(profile.region, [f"scheme={scheme.value}"])
        if scheme is CodeScheme.NAICS:
            if not is_valid_naics(changes["code"]):
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail={
                        "error": "unknown_naics",
                        "code": changes["code"],
                        "message": f"{changes['code']} is not a 2022 NAICS code",
                    },
                )
            changes["title"] = naics_title(changes["code"])
    if changes.get("is_primary"):
        # one primary per scheme: demote the others in the same transaction
        stmt = (
            update(ProfileCode)
            .where(ProfileCode.profile_id == profile.id, ProfileCode.scheme == scheme)
            .values(is_primary=False)
        )
        if existing is not None:
            stmt = stmt.where(ProfileCode.id != existing.id)
        await session.execute(stmt)


codes_router = crud_router(
    name="codes",
    singular="code",
    model=ProfileCode,
    create=CodeIn,
    update=CodeUpdate,
    out=CodeOut,
    write_roles=PROFILE_EDIT_ROLES,
    validate=validate_code,
    order_by=(ProfileCode.scheme, ProfileCode.is_primary.desc(), ProfileCode.code),
)

# --- keywords ------------------------------------------------------------------------------

Weight = Annotated[Decimal, Field(ge=MIN_KEYWORD_WEIGHT, le=MAX_KEYWORD_WEIGHT, decimal_places=1)]


class KeywordIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: KeywordKind
    term: Annotated[str, Field(min_length=1, max_length=100)]
    weight: Weight = Decimal("1.0")

    @field_validator("term")
    @classmethod
    def _term(cls, value: str) -> str:
        return normalize_keyword(value)


class KeywordUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weight: Weight | None = None


class KeywordOut(BaseModel):
    id: uuid.UUID
    profile_id: uuid.UUID
    kind: KeywordKind
    term: str
    weight: Decimal
    created_at: datetime


keywords_router = crud_router(
    name="keywords",
    singular="keyword",
    model=ProfileKeyword,
    create=KeywordIn,
    update=KeywordUpdate,
    out=KeywordOut,
    write_roles=PROFILE_EDIT_ROLES,
    order_by=(ProfileKeyword.kind, ProfileKeyword.weight.desc(), ProfileKeyword.term),
)

# --- service lines -------------------------------------------------------------------------

Items = list[Annotated[str, Field(min_length=1, max_length=200)]]


class ServiceLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=200)]
    description: Annotated[str, Field(min_length=1)]
    differentiators: Items = []
    tools: Items = []
    delivery_model: DeliveryModel | None = None

    @field_validator("description")
    @classmethod
    def _description(cls, value: str) -> str:
        return validate_service_description(value)


class ServiceLineUpdate(ServiceLineIn):
    name: Annotated[str, Field(min_length=1, max_length=200)] | None = None  # type: ignore[assignment]
    description: Annotated[str, Field(min_length=1)] | None = None  # type: ignore[assignment]
    differentiators: Items | None = None  # type: ignore[assignment]
    tools: Items | None = None  # type: ignore[assignment]

    @field_validator("description")
    @classmethod
    def _description(cls, value: str | None) -> str | None:  # type: ignore[override]
        return None if value is None else validate_service_description(value)


class ServiceLineOut(BaseModel):
    id: uuid.UUID
    profile_id: uuid.UUID
    name: str
    description: str
    differentiators: list[str]
    tools: list[str]
    delivery_model: DeliveryModel | None
    created_at: datetime


service_lines_router = crud_router(
    name="service-lines",
    singular="service_line",
    model=ServiceLine,
    create=ServiceLineIn,
    update=ServiceLineUpdate,
    out=ServiceLineOut,
    write_roles=PROFILE_EDIT_ROLES,
    order_by=(ServiceLine.name,),
    reindex=True,
)
