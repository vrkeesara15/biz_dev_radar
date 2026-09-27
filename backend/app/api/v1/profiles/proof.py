"""Proof for drafting (SPEC 4.5): past performance, personnel, registrations, vehicles,
insurance, boilerplate, profile files, rate card. Writers may edit past performance and
personnel; everything else needs bid_manager/tenant_owner."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.profiles.certifications import check_file_visible
from app.api.v1.profiles.common import EVIDENCE_EDIT_ROLES, PROFILE_EDIT_ROLES, region_mismatch
from app.api.v1.profiles.subresources import crud_router
from app.core.finance import CURRENCIES
from app.core.geo import normalize_names
from app.core.profile_fields import (
    AgencyType,
    BoilerplateKind,
    CparsRating,
    InsuranceKind,
    PerformanceRole,
    ProfileFileKind,
    RateUnit,
    RegistrationKind,
    normalize_code,
    registration_allowed,
)
from app.core.reference import is_valid_naics
from app.models import (
    BoilerplateBlock,
    CompanyProfile,
    Insurance,
    PastPerformance,
    Personnel,
    ProfileFile,
    RateCardEntry,
    Registration,
    Vehicle,
)

Currency = Annotated[str, Field(pattern="^(" + "|".join(CURRENCIES) + ")$")]
Amount = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)]
Short = Annotated[str, Field(min_length=1, max_length=200)]
Title = Annotated[str, Field(min_length=1, max_length=300)]
Names = list[Annotated[str, Field(min_length=1, max_length=200)]]


def _forbid() -> ConfigDict:
    return ConfigDict(extra="forbid")


def _dates_ordered(start: date | None, end: date | None, first: str, second: str) -> None:
    if start and end and end < start:
        raise ValueError(f"{second} must not precede {first}")


async def _no_nulls(changes: dict[str, Any], *required: str) -> None:
    for key in required:
        if key in changes and changes[key] is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"{key} cannot be null"
            )


# --- past performance ----------------------------------------------------------------------


class ReferenceContact(BaseModel):
    model_config = _forbid()

    name: Short | None = None
    title: Short | None = None
    email: Annotated[str | None, Field(max_length=320)] = None
    phone: Annotated[str | None, Field(max_length=40)] = None


class PastPerformanceIn(BaseModel):
    model_config = _forbid()

    title: Title
    customer: Title
    customer_anonymized: bool = False
    agency_type: AgencyType | None = None
    value_amount: Amount | None = None
    value_currency: Currency | None = None
    period_start: date | None = None
    period_end: date | None = None
    role: PerformanceRole
    naics: str | None = None
    contract_number: Annotated[str | None, Field(max_length=100)] = None
    scope: Annotated[str, Field(min_length=1)]
    outcomes: str | None = None
    technologies: Names = []
    reference_contact: ReferenceContact = ReferenceContact()
    cpars_rating: CparsRating | None = None
    is_public: bool = False

    @field_validator("naics")
    @classmethod
    def _naics(cls, value: str | None) -> str | None:
        if value is None:
            return None
        code = normalize_code("naics", value)
        if not is_valid_naics(code):
            raise ValueError(f"{code} is not a 2022 NAICS code")
        return code

    @field_validator("technologies")
    @classmethod
    def _tech(cls, value: list[str]) -> list[str]:
        return normalize_names(value)

    @model_validator(mode="after")
    def _period(self) -> PastPerformanceIn:
        _dates_ordered(self.period_start, self.period_end, "period_start", "period_end")
        if (self.value_amount is None) != (self.value_currency is None):
            raise ValueError("value_amount and value_currency go together")
        return self


class PastPerformanceUpdate(PastPerformanceIn):
    title: Title | None = None  # type: ignore[assignment]
    customer: Title | None = None  # type: ignore[assignment]
    role: PerformanceRole | None = None  # type: ignore[assignment]
    scope: Annotated[str, Field(min_length=1)] | None = None  # type: ignore[assignment]
    technologies: Names | None = None  # type: ignore[assignment]
    reference_contact: ReferenceContact | None = None  # type: ignore[assignment]
    customer_anonymized: bool | None = None  # type: ignore[assignment]
    is_public: bool | None = None  # type: ignore[assignment]

    @field_validator("technologies")
    @classmethod
    def _tech(cls, value: list[str] | None) -> list[str] | None:  # type: ignore[override]
        return None if value is None else normalize_names(value)

    @model_validator(mode="after")
    def _period(self) -> PastPerformanceUpdate:
        _dates_ordered(self.period_start, self.period_end, "period_start", "period_end")
        return self


class PastPerformanceOut(BaseModel):
    id: uuid.UUID
    profile_id: uuid.UUID
    title: str
    customer: str
    customer_anonymized: bool
    agency_type: AgencyType | None
    value_amount: Decimal | None
    value_currency: str | None
    period_start: date | None
    period_end: date | None
    role: PerformanceRole
    naics: str | None
    contract_number: str | None
    scope: str
    outcomes: str | None
    technologies: list[str]
    reference_contact: dict[str, Any]
    cpars_rating: CparsRating | None
    is_public: bool
    created_at: datetime


async def validate_past_performance(
    session: AsyncSession, profile: CompanyProfile, changes: dict[str, Any], existing: Any
) -> None:
    await _no_nulls(changes, "title", "customer", "role", "scope", "technologies", "is_public")
    if existing is not None:
        start = changes.get("period_start", existing.period_start)
        end = changes.get("period_end", existing.period_end)
        if start and end and end < start:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="period_end must not precede period_start",
            )
    if changes.get("reference_contact") is None and "reference_contact" in changes:
        changes["reference_contact"] = {}


past_performance_router = crud_router(
    name="past-performance",
    singular="past_performance",
    model=PastPerformance,
    create=PastPerformanceIn,
    update=PastPerformanceUpdate,
    out=PastPerformanceOut,
    write_roles=EVIDENCE_EDIT_ROLES,
    validate=validate_past_performance,
    order_by=(PastPerformance.period_end.desc().nulls_last(), PastPerformance.title),
    reindex=True,
)

# --- personnel -----------------------------------------------------------------------------


class PersonnelIn(BaseModel):
    model_config = _forbid()

    name: Short
    role: Short
    years_experience: Annotated[int | None, Field(ge=0, le=70)] = None
    clearances: Names = []
    certifications: Names = []
    education: Annotated[str | None, Field(max_length=2000)] = None
    resume_file_id: uuid.UUID | None = None
    is_key_personnel: bool = False

    @field_validator("clearances", "certifications")
    @classmethod
    def _lists(cls, value: list[str]) -> list[str]:
        return normalize_names(value)


class PersonnelUpdate(PersonnelIn):
    name: Short | None = None  # type: ignore[assignment]
    role: Short | None = None  # type: ignore[assignment]
    clearances: Names | None = None  # type: ignore[assignment]
    certifications: Names | None = None  # type: ignore[assignment]
    is_key_personnel: bool | None = None  # type: ignore[assignment]

    @field_validator("clearances", "certifications")
    @classmethod
    def _lists(cls, value: list[str] | None) -> list[str] | None:  # type: ignore[override]
        return None if value is None else normalize_names(value)


class PersonnelOut(BaseModel):
    id: uuid.UUID
    profile_id: uuid.UUID
    name: str
    role: str
    years_experience: int | None
    clearances: list[str]
    certifications: list[str]
    education: str | None
    resume_file_id: uuid.UUID | None
    is_key_personnel: bool
    created_at: datetime


async def validate_personnel(
    session: AsyncSession, profile: CompanyProfile, changes: dict[str, Any], existing: Any
) -> None:
    await _no_nulls(changes, "name", "role", "clearances", "certifications", "is_key_personnel")
    await check_file_visible(session, changes.get("resume_file_id"))


personnel_router = crud_router(
    name="personnel",
    singular="personnel",
    model=Personnel,
    create=PersonnelIn,
    update=PersonnelUpdate,
    out=PersonnelOut,
    write_roles=EVIDENCE_EDIT_ROLES,
    validate=validate_personnel,
    order_by=(Personnel.is_key_personnel.desc(), Personnel.name),
)

# --- registrations -------------------------------------------------------------------------


class RegistrationIn(BaseModel):
    model_config = _forbid()

    kind: RegistrationKind
    identifier: Short | None = None
    holder: Short | None = None
    portal: Short | None = None
    expires_on: date | None = None
    notes: Annotated[str | None, Field(max_length=2000)] = None


class RegistrationUpdate(RegistrationIn):
    kind: RegistrationKind | None = None  # type: ignore[assignment]


class RegistrationOut(BaseModel):
    id: uuid.UUID
    profile_id: uuid.UUID
    kind: RegistrationKind
    identifier: str | None
    holder: str | None
    portal: str | None
    expires_on: date | None
    notes: str | None
    created_at: datetime


async def validate_registration(
    session: AsyncSession, profile: CompanyProfile, changes: dict[str, Any], existing: Any
) -> None:
    await _no_nulls(changes, "kind")
    kind = changes.get("kind", getattr(existing, "kind", None))
    if kind is not None and not registration_allowed(profile.region, kind):
        raise region_mismatch(profile.region, [f"kind={RegistrationKind(kind).value}"])


registrations_router = crud_router(
    name="registrations",
    singular="registration",
    model=Registration,
    create=RegistrationIn,
    update=RegistrationUpdate,
    out=RegistrationOut,
    write_roles=PROFILE_EDIT_ROLES,
    validate=validate_registration,
    order_by=(Registration.kind, Registration.expires_on),
)

# --- vehicles ------------------------------------------------------------------------------


class VehicleIn(BaseModel):
    model_config = _forbid()

    vehicle: Short
    number: Annotated[str | None, Field(max_length=100)] = None
    expires_on: date | None = None
    notes: Annotated[str | None, Field(max_length=2000)] = None


class VehicleUpdate(VehicleIn):
    vehicle: Short | None = None  # type: ignore[assignment]


class VehicleOut(BaseModel):
    id: uuid.UUID
    profile_id: uuid.UUID
    vehicle: str
    number: str | None
    expires_on: date | None
    notes: str | None
    created_at: datetime


async def validate_vehicle(
    session: AsyncSession, profile: CompanyProfile, changes: dict[str, Any], existing: Any
) -> None:
    await _no_nulls(changes, "vehicle")


vehicles_router = crud_router(
    name="vehicles",
    singular="vehicle",
    model=Vehicle,
    create=VehicleIn,
    update=VehicleUpdate,
    out=VehicleOut,
    write_roles=PROFILE_EDIT_ROLES,
    validate=validate_vehicle,
    order_by=(Vehicle.vehicle,),
)

# --- insurance -----------------------------------------------------------------------------


class InsuranceIn(BaseModel):
    model_config = _forbid()

    kind: InsuranceKind
    carrier: Short | None = None
    policy_number: Annotated[str | None, Field(max_length=100)] = None
    limit_amount: Amount | None = None
    limit_currency: Currency | None = None
    expires_on: date | None = None
    file_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _limit(self) -> InsuranceIn:
        if (self.limit_amount is None) != (self.limit_currency is None):
            raise ValueError("limit_amount and limit_currency go together")
        return self


class InsuranceUpdate(InsuranceIn):
    kind: InsuranceKind | None = None  # type: ignore[assignment]

    @model_validator(mode="after")
    def _limit(self) -> InsuranceUpdate:
        return self


class InsuranceOut(BaseModel):
    id: uuid.UUID
    profile_id: uuid.UUID
    kind: InsuranceKind
    carrier: str | None
    policy_number: str | None
    limit_amount: Decimal | None
    limit_currency: str | None
    expires_on: date | None
    file_id: uuid.UUID | None
    created_at: datetime


async def validate_insurance(
    session: AsyncSession, profile: CompanyProfile, changes: dict[str, Any], existing: Any
) -> None:
    await _no_nulls(changes, "kind")
    await check_file_visible(session, changes.get("file_id"))


insurance_router = crud_router(
    name="insurance",
    singular="insurance",
    model=Insurance,
    create=InsuranceIn,
    update=InsuranceUpdate,
    out=InsuranceOut,
    write_roles=PROFILE_EDIT_ROLES,
    validate=validate_insurance,
    order_by=(Insurance.kind, Insurance.expires_on),
)

# --- boilerplate ---------------------------------------------------------------------------


class BoilerplateIn(BaseModel):
    model_config = _forbid()

    kind: BoilerplateKind
    title: Title
    body: Annotated[str, Field(min_length=1, max_length=200_000)]
    body_format: Annotated[str, Field(pattern="^(html|markdown)$")] = "html"


class BoilerplateUpdate(BoilerplateIn):
    kind: BoilerplateKind | None = None  # type: ignore[assignment]
    title: Title | None = None  # type: ignore[assignment]
    body: Annotated[str, Field(min_length=1, max_length=200_000)] | None = None  # type: ignore[assignment]
    body_format: Annotated[str, Field(pattern="^(html|markdown)$")] | None = None  # type: ignore[assignment]


class BoilerplateOut(BaseModel):
    id: uuid.UUID
    profile_id: uuid.UUID
    kind: BoilerplateKind
    title: str
    body: str
    body_format: str
    created_at: datetime


async def validate_boilerplate(
    session: AsyncSession, profile: CompanyProfile, changes: dict[str, Any], existing: Any
) -> None:
    await _no_nulls(changes, "kind", "title", "body", "body_format")


boilerplate_router = crud_router(
    name="boilerplate",
    singular="boilerplate_block",
    model=BoilerplateBlock,
    create=BoilerplateIn,
    update=BoilerplateUpdate,
    out=BoilerplateOut,
    write_roles=PROFILE_EDIT_ROLES,
    validate=validate_boilerplate,
    order_by=(BoilerplateBlock.kind, BoilerplateBlock.title),
    reindex=True,
)

# --- profile files -------------------------------------------------------------------------


class ProfileFileIn(BaseModel):
    model_config = _forbid()

    file_id: uuid.UUID
    kind: ProfileFileKind
    title: Title | None = None
    meta: dict[str, Any] = {}

    @model_validator(mode="after")
    def _meta(self) -> ProfileFileIn:
        if self.kind is ProfileFileKind.PAST_PROPOSAL:
            outcome = self.meta.get("outcome")
            if outcome not in (None, "won", "lost", "no_decision"):
                raise ValueError("past_proposal meta.outcome must be won, lost or no_decision")
        return self


class ProfileFileUpdate(BaseModel):
    model_config = _forbid()

    kind: ProfileFileKind | None = None
    title: Title | None = None
    meta: dict[str, Any] | None = None


class ProfileFileOut(BaseModel):
    id: uuid.UUID
    profile_id: uuid.UUID
    file_id: uuid.UUID
    kind: ProfileFileKind
    title: str | None
    meta: dict[str, Any]
    created_at: datetime


async def validate_profile_file(
    session: AsyncSession, profile: CompanyProfile, changes: dict[str, Any], existing: Any
) -> None:
    await _no_nulls(changes, "kind", "meta")
    if existing is None:
        await check_file_visible(session, changes["file_id"])


profile_files_router = crud_router(
    name="files",
    singular="profile_file",
    model=ProfileFile,
    create=ProfileFileIn,
    update=ProfileFileUpdate,
    out=ProfileFileOut,
    write_roles=PROFILE_EDIT_ROLES,
    validate=validate_profile_file,
    order_by=(ProfileFile.kind, ProfileFile.created_at),
    reindex=True,
)

# --- rate card -----------------------------------------------------------------------------


class RateCardIn(BaseModel):
    model_config = _forbid()

    labor_category: Short
    unit: RateUnit
    rate_amount: Amount
    rate_currency: Currency
    min_years_experience: Annotated[int | None, Field(ge=0, le=70)] = None
    notes: Annotated[str | None, Field(max_length=2000)] = None


class RateCardUpdate(RateCardIn):
    labor_category: Short | None = None  # type: ignore[assignment]
    unit: RateUnit | None = None  # type: ignore[assignment]
    rate_amount: Amount | None = None  # type: ignore[assignment]
    rate_currency: Currency | None = None  # type: ignore[assignment]


class RateCardOut(BaseModel):
    id: uuid.UUID
    profile_id: uuid.UUID
    labor_category: str
    unit: RateUnit
    rate_amount: Decimal
    rate_currency: str
    min_years_experience: int | None
    notes: str | None
    created_at: datetime


async def validate_rate_card(
    session: AsyncSession, profile: CompanyProfile, changes: dict[str, Any], existing: Any
) -> None:
    await _no_nulls(changes, "labor_category", "unit", "rate_amount", "rate_currency")


rate_card_router = crud_router(
    name="rate-card",
    singular="rate_card_entry",
    model=RateCardEntry,
    create=RateCardIn,
    update=RateCardUpdate,
    out=RateCardOut,
    write_roles=PROFILE_EDIT_ROLES,
    validate=validate_rate_card,
    order_by=(RateCardEntry.labor_category,),
)

ROUTERS = (
    past_performance_router,
    personnel_router,
    registrations_router,
    vehicles_router,
    insurance_router,
    boilerplate_router,
    profile_files_router,
    rate_card_router,
)
