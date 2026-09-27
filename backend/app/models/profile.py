"""company_profiles (SPEC 4.1 identity and registrations; later tasks add 4.2-4.6 columns).

Region-specific columns are nullable and gated by the API (app.core.profile_fields);
EIN/PAN/GSTIN/TAN and bank details use EncryptedString (SPEC 11)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Integer,
    Numeric,
    String,
    Text,
    false,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import Region
from app.core.profile_fields import (
    LegalStructure,
    LocalSupplierClass,
    MseOwnership,
    SamStatus,
    UdyamCategory,
)
from app.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.tenancy import RegionEnum, _values
from app.models.types import EncryptedString

LegalStructureEnum = Enum(LegalStructure, name="legal_structure", values_callable=_values)
SamStatusEnum = Enum(SamStatus, name="sam_status", values_callable=_values)
UdyamCategoryEnum = Enum(UdyamCategory, name="udyam_category", values_callable=_values)
LocalSupplierClassEnum = Enum(
    LocalSupplierClass, name="local_supplier_class", values_callable=_values
)
MseOwnershipEnum = Enum(MseOwnership, name="mse_ownership", values_callable=_values)


class CompanyProfile(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "company_profiles"
    __table_args__ = (
        CheckConstraint("year_founded BETWEEN 1800 AND 2100", name="year_founded_range"),
    )

    region: Mapped[Region] = mapped_column(RegionEnum, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
    # bumped on every write; matches/rationales cache on (opportunity version, profile version)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # --- identity (both regions)
    legal_name: Mapped[str] = mapped_column(String(300), nullable=False)
    dba_names: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    # [{kind: registered|hq|branch, line1, line2, city, state, postal_code, country}]
    addresses: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    website: Mapped[str | None] = mapped_column(String(500))
    phone: Mapped[str | None] = mapped_column(String(40))
    bid_inbox_email: Mapped[str | None] = mapped_column(String(320))
    year_founded: Mapped[int | None] = mapped_column(Integer)
    legal_structure: Mapped[LegalStructure | None] = mapped_column(LegalStructureEnum)

    # --- US registrations
    uei: Mapped[str | None] = mapped_column(String(12))
    cage_code: Mapped[str | None] = mapped_column(String(5))
    sam_status: Mapped[SamStatus | None] = mapped_column(SamStatusEnum)
    sam_expires_on: Mapped[date | None] = mapped_column(Date)
    ein: Mapped[str | None] = mapped_column(EncryptedString)

    # --- IN registrations
    pan: Mapped[str | None] = mapped_column(EncryptedString)
    gstin: Mapped[str | None] = mapped_column(EncryptedString)
    tan: Mapped[str | None] = mapped_column(EncryptedString)
    cin_llpin: Mapped[str | None] = mapped_column(String(32))
    udyam_number: Mapped[str | None] = mapped_column(String(32))
    udyam_category: Mapped[UdyamCategory | None] = mapped_column(UdyamCategoryEnum)
    dpiit_number: Mapped[str | None] = mapped_column(String(32))
    gem_seller_id: Mapped[str | None] = mapped_column(String(64))
    local_supplier_class: Mapped[LocalSupplierClass | None] = mapped_column(LocalSupplierClassEnum)
    local_content_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))

    # --- size and finances (SPEC 4.2)
    employee_count_total: Mapped[int | None] = mapped_column(Integer)
    # {"US": 40, "IN": 120}
    employees_by_country: Mapped[dict[str, int]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # [{"fiscal_year": 2025, "amount": "1234567.00", "currency": "USD"}, ...]
    annual_revenue: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    net_worth_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    net_worth_currency: Mapped[str | None] = mapped_column(String(3))
    solvency_certificate_available: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false()
    )
    audited_fiscal_years: Mapped[list[int]] = mapped_column(
        ARRAY(Integer), nullable=False, server_default=text("'{}'::integer[]")
    )
    bonding_capacity_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    bonding_capacity_currency: Mapped[str | None] = mapped_column(String(3))
    mse_ownership: Mapped[MseOwnership | None] = mapped_column(MseOwnershipEnum)

    # --- bank details (both regions, encrypted; SPEC 11)
    bank_name: Mapped[str | None] = mapped_column(String(200))
    bank_account_number: Mapped[str | None] = mapped_column(EncryptedString)
    bank_routing_code: Mapped[str | None] = mapped_column(EncryptedString)
