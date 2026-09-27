"""Proof for drafting (SPEC 4.5): past performance, personnel, registrations, vehicles,
insurance, boilerplate, profile files, rate card. All tenant-scoped profile children."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, Enum, ForeignKey, Integer, Numeric, String, Text, false, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.profile_fields import (
    AgencyType,
    BoilerplateKind,
    CparsRating,
    InsuranceKind,
    PerformanceRole,
    ProfileFileKind,
    RateUnit,
    RegistrationKind,
)
from app.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.profile_items import ProfileChildMixin
from app.models.tenancy import _values

PerformanceRoleEnum = Enum(PerformanceRole, name="performance_role", values_callable=_values)
AgencyTypeEnum = Enum(AgencyType, name="agency_type", values_callable=_values)
CparsRatingEnum = Enum(CparsRating, name="cpars_rating", values_callable=_values)
RegistrationKindEnum = Enum(RegistrationKind, name="registration_kind", values_callable=_values)
InsuranceKindEnum = Enum(InsuranceKind, name="insurance_kind", values_callable=_values)
BoilerplateKindEnum = Enum(BoilerplateKind, name="boilerplate_kind", values_callable=_values)
ProfileFileKindEnum = Enum(ProfileFileKind, name="profile_file_kind", values_callable=_values)
RateUnitEnum = Enum(RateUnit, name="rate_unit", values_callable=_values)


def _text_list() -> Any:
    return mapped_column(ARRAY(Text), nullable=False, server_default=text("'{}'::text[]"))


def _file_fk() -> Any:
    return mapped_column(UUID(as_uuid=True), ForeignKey("files.id", ondelete="SET NULL"))


class PastPerformance(UUIDPrimaryKeyMixin, TenantMixin, ProfileChildMixin, TimestampMixin, Base):
    __tablename__ = "past_performance"

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    customer: Mapped[str] = mapped_column(String(300), nullable=False)
    # when true the customer is described generically in drafts ("a federal civilian agency")
    customer_anonymized: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false()
    )
    agency_type: Mapped[AgencyType | None] = mapped_column(AgencyTypeEnum)
    value_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    value_currency: Mapped[str | None] = mapped_column(String(3))
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    role: Mapped[PerformanceRole] = mapped_column(PerformanceRoleEnum, nullable=False)
    naics: Mapped[str | None] = mapped_column(String(6))
    contract_number: Mapped[str | None] = mapped_column(String(100))
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    outcomes: Mapped[str | None] = mapped_column(Text)
    technologies: Mapped[list[str]] = _text_list()
    # {"name":..., "title":..., "email":..., "phone":...}
    reference_contact: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    cpars_rating: Mapped[CparsRating | None] = mapped_column(CparsRatingEnum)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())


class Personnel(UUIDPrimaryKeyMixin, TenantMixin, ProfileChildMixin, TimestampMixin, Base):
    __tablename__ = "personnel"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(200), nullable=False)
    years_experience: Mapped[int | None] = mapped_column(Integer)
    clearances: Mapped[list[str]] = _text_list()
    certifications: Mapped[list[str]] = _text_list()
    education: Mapped[str | None] = mapped_column(Text)
    resume_file_id: Mapped[uuid.UUID | None] = _file_fk()
    is_key_personnel: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())


class Registration(UUIDPrimaryKeyMixin, TenantMixin, ProfileChildMixin, TimestampMixin, Base):
    """Portal enrolments and signing credentials metadata (never passwords or keys)."""

    __tablename__ = "registrations"

    kind: Mapped[RegistrationKind] = mapped_column(RegistrationKindEnum, nullable=False)
    identifier: Mapped[str | None] = mapped_column(String(200))
    holder: Mapped[str | None] = mapped_column(String(200))
    portal: Mapped[str | None] = mapped_column(String(200))
    expires_on: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)


class Vehicle(UUIDPrimaryKeyMixin, TenantMixin, ProfileChildMixin, TimestampMixin, Base):
    """GSA Schedule / MAS, GWACs, IDIQs, BPAs held (SPEC 4.1)."""

    __tablename__ = "vehicles"

    vehicle: Mapped[str] = mapped_column(String(200), nullable=False)
    number: Mapped[str | None] = mapped_column(String(100))
    expires_on: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)


class Insurance(UUIDPrimaryKeyMixin, TenantMixin, ProfileChildMixin, TimestampMixin, Base):
    __tablename__ = "insurance"

    kind: Mapped[InsuranceKind] = mapped_column(InsuranceKindEnum, nullable=False)
    carrier: Mapped[str | None] = mapped_column(String(200))
    policy_number: Mapped[str | None] = mapped_column(String(100))
    limit_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    limit_currency: Mapped[str | None] = mapped_column(String(3))
    expires_on: Mapped[date | None] = mapped_column(Date)
    file_id: Mapped[uuid.UUID | None] = _file_fk()


class BoilerplateBlock(UUIDPrimaryKeyMixin, TenantMixin, ProfileChildMixin, TimestampMixin, Base):
    __tablename__ = "boilerplate_blocks"

    kind: Mapped[BoilerplateKind] = mapped_column(BoilerplateKindEnum, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    # rich text (TipTap HTML); body_format keeps the door open for markdown
    body: Mapped[str] = mapped_column(Text, nullable=False)
    body_format: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default=text("'html'")
    )


class ProfileFile(UUIDPrimaryKeyMixin, TenantMixin, ProfileChildMixin, TimestampMixin, Base):
    """Capability statements, brochures, case studies, past proposals, brand assets and
    proposal templates: a files row plus profile-level metadata."""

    __tablename__ = "profile_files"

    file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("files.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[ProfileFileKind] = mapped_column(ProfileFileKindEnum, nullable=False)
    title: Mapped[str | None] = mapped_column(String(300))
    # past_proposal: {"outcome": "won|lost", "debrief_notes": ...}; brand: {"colors": [...]}
    meta: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class RateCardEntry(UUIDPrimaryKeyMixin, TenantMixin, ProfileChildMixin, TimestampMixin, Base):
    """Labor categories with rates (US hourly) or man-month rates (IN)."""

    __tablename__ = "rate_card_entries"

    labor_category: Mapped[str] = mapped_column(String(200), nullable=False)
    unit: Mapped[RateUnit] = mapped_column(RateUnitEnum, nullable=False)
    rate_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    rate_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    min_years_experience: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
