"""Pydantic schemas for company profiles (SPEC 4.1). Writes accept plaintext for encrypted
fields and ignore echoed masks; reads always mask (app.core.crypto.mask_last4)."""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.config import Region
from app.core.crypto import is_masked, mask_last4
from app.core.eligibility import SizeStatus, size_status_by_naics
from app.core.finance import CURRENCIES, FiscalYearRevenue, average_turnover
from app.core.geo import (
    normalize_countries,
    normalize_india_states,
    normalize_names,
    normalize_us_states,
)
from app.core.notice_types import ContractType, NoticeType, TeamingRole
from app.core.preferences import (
    validate_bid_no_bid_weights,
    validate_output_languages,
    validate_scoring_weights,
)
from app.core.profile_fields import (
    ENCRYPTED_FIELDS,
    NORMALIZERS,
    AddressKind,
    LegalStructure,
    LocalSupplierClass,
    MseOwnership,
    SamStatus,
    UdyamCategory,
)
from app.core.roles import Role
from app.models import CompanyProfile

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_COUNTRY = re.compile(r"^[A-Z]{2}$")

Str = Annotated[str, Field(min_length=1, max_length=300)]
Currency = Annotated[str, Field(pattern="^(" + "|".join(CURRENCIES) + ")$")]
Amount = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)]


class Address(BaseModel):
    kind: AddressKind
    line1: Annotated[str, Field(min_length=1, max_length=300)]
    line2: Annotated[str | None, Field(max_length=300)] = None
    city: Annotated[str, Field(min_length=1, max_length=120)]
    state: Annotated[str | None, Field(max_length=120)] = None
    postal_code: Annotated[str | None, Field(max_length=20)] = None
    country: Annotated[str, Field(min_length=2, max_length=2)]

    @field_validator("country")
    @classmethod
    def _country(cls, value: str) -> str:
        value = value.upper()
        if not _COUNTRY.match(value):
            raise ValueError("country must be an ISO 3166-1 alpha-2 code")
        return value


class RevenueEntry(BaseModel):
    fiscal_year: Annotated[int, Field(ge=1990, le=2100)]
    amount: Amount
    currency: Currency


class AverageTurnoverOut(BaseModel):
    amount: Decimal
    currency: str
    fiscal_years: list[int]


class SizeStatusOut(BaseModel):
    status: SizeStatus
    basis: str | None
    threshold: str | None
    measured: str | None
    reason: str


class ProfileWrite(BaseModel):
    """Every writable 4.1/4.2 field, all optional so PUT can be partial."""

    model_config = ConfigDict(extra="forbid")

    legal_name: Str | None = None
    dba_names: list[Annotated[str, Field(min_length=1, max_length=300)]] | None = None
    addresses: list[Address] | None = None
    website: Annotated[str | None, Field(max_length=500)] = None
    phone: Annotated[str | None, Field(max_length=40)] = None
    bid_inbox_email: Annotated[str | None, Field(max_length=320)] = None
    year_founded: Annotated[int | None, Field(ge=1800, le=2100)] = None
    legal_structure: LegalStructure | None = None
    is_active: bool | None = None
    # US
    uei: str | None = None
    cage_code: str | None = None
    sam_status: SamStatus | None = None
    sam_expires_on: date | None = None
    ein: str | None = None
    # IN
    pan: str | None = None
    gstin: str | None = None
    tan: str | None = None
    cin_llpin: Annotated[str | None, Field(max_length=32)] = None
    udyam_number: Annotated[str | None, Field(max_length=32)] = None
    udyam_category: UdyamCategory | None = None
    dpiit_number: Annotated[str | None, Field(max_length=32)] = None
    gem_seller_id: Annotated[str | None, Field(max_length=64)] = None
    local_supplier_class: LocalSupplierClass | None = None
    local_content_pct: Annotated[Decimal | None, Field(ge=0, le=100, decimal_places=2)] = None
    # 4.2 size and finances
    employee_count_total: Annotated[int | None, Field(ge=0)] = None
    employees_by_country: dict[str, Annotated[int, Field(ge=0)]] | None = None
    annual_revenue: list[RevenueEntry] | None = None
    net_worth_amount: Amount | None = None
    net_worth_currency: Currency | None = None
    solvency_certificate_available: bool | None = None
    audited_fiscal_years: list[Annotated[int, Field(ge=1990, le=2100)]] | None = None
    bonding_capacity_amount: Amount | None = None
    bonding_capacity_currency: Currency | None = None
    mse_ownership: MseOwnership | None = None
    # 4.4 where and how big
    target_countries: list[str] | None = None
    target_us_states: list[str] | None = None
    target_in_states: list[str] | None = None
    target_cities: list[str] | None = None
    remote_ok: bool | None = None
    target_buyers: list[str] | None = None
    blocked_buyers: list[str] | None = None
    value_min_usd: Amount | None = None
    value_max_usd: Amount | None = None
    value_min_inr: Amount | None = None
    value_max_inr: Amount | None = None
    notice_types_wanted: list[NoticeType] | None = None
    contract_types_preferred: list[ContractType] | None = None
    teaming_roles: list[TeamingRole] | None = None
    # 4.5 proof: personnel clearances count (US); the rest are sub-resources
    cleared_personnel_count: Annotated[int | None, Field(ge=0)] = None
    # 4.6 preferences
    scoring_weights: dict[str, int] | None = None
    bid_no_bid_weights: dict[str, int] | None = None
    required_approver_roles: list[Role] | None = None
    output_languages: list[str] | None = None  # region-checked in validate_for_region()
    # bank (encrypted)
    bank_name: Annotated[str | None, Field(max_length=200)] = None
    bank_account_number: Annotated[str | None, Field(max_length=64)] = None
    bank_routing_code: Annotated[str | None, Field(max_length=32)] = None

    @field_validator("scoring_weights")
    @classmethod
    def _scoring(cls, value: dict[str, int] | None) -> dict[str, int] | None:
        return None if value is None else validate_scoring_weights(value)

    @field_validator("bid_no_bid_weights")
    @classmethod
    def _bid_no_bid(cls, value: dict[str, int] | None) -> dict[str, int] | None:
        return None if value is None else validate_bid_no_bid_weights(value)

    @field_validator("required_approver_roles")
    @classmethod
    def _approvers(cls, value: list[Role] | None) -> list[Role] | None:
        if value is None:
            return None
        roles = list(dict.fromkeys(value))
        if Role.PLATFORM_ADMIN in roles:
            raise ValueError("platform_admin cannot be a required approver")
        if any(r in (Role.VIEWER,) for r in roles):
            raise ValueError("viewers cannot approve")
        return roles

    @field_validator("target_countries")
    @classmethod
    def _target_countries(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else normalize_countries(value)

    @field_validator("target_us_states")
    @classmethod
    def _us_states(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else normalize_us_states(value)

    @field_validator("target_in_states")
    @classmethod
    def _in_states(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else normalize_india_states(value)

    @field_validator("target_cities", "target_buyers", "blocked_buyers")
    @classmethod
    def _names(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else normalize_names(value)

    @field_validator("notice_types_wanted", "contract_types_preferred", "teaming_roles")
    @classmethod
    def _dedupe_enums(cls, value: list[Any] | None) -> list[Any] | None:
        if value is None:
            return None
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def _value_ranges(self) -> ProfileWrite:
        for currency in ("usd", "inr"):
            low = getattr(self, f"value_min_{currency}")
            high = getattr(self, f"value_max_{currency}")
            if low is not None and high is not None and low > high:
                raise ValueError(f"value_min_{currency} must not exceed value_max_{currency}")
        return self

    @field_validator("employees_by_country")
    @classmethod
    def _countries(cls, value: dict[str, int] | None) -> dict[str, int] | None:
        if value is None:
            return None
        out: dict[str, int] = {}
        for code, count in value.items():
            key = code.strip().upper()
            if not _COUNTRY.match(key):
                raise ValueError(f"country {code!r} must be an ISO 3166-1 alpha-2 code")
            out[key] = count
        return out

    @field_validator("annual_revenue")
    @classmethod
    def _revenue(cls, value: list[RevenueEntry] | None) -> list[RevenueEntry] | None:
        if value is None:
            return None
        years = [e.fiscal_year for e in value]
        if len(set(years)) != len(years):
            raise ValueError("annual_revenue has duplicate fiscal years")
        if len({e.currency for e in value}) > 1:
            raise ValueError("annual_revenue entries must share one currency")
        return sorted(value, key=lambda e: e.fiscal_year)

    @field_validator("audited_fiscal_years")
    @classmethod
    def _audited(cls, value: list[int] | None) -> list[int] | None:
        return None if value is None else sorted(set(value))

    @field_validator("bid_inbox_email")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower()
        if not _EMAIL.match(value):
            raise ValueError("bid_inbox_email must be an email address")
        return value

    @field_validator("website")
    @classmethod
    def _website(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value.startswith(("http://", "https://")):
            value = "https://" + value
        return value

    @field_validator("uei", "cage_code", "ein", "pan", "gstin", "tan")
    @classmethod
    def _identifiers(cls, value: str | None, info: Any) -> str | None:
        if value is None or is_masked(value):
            return value
        return NORMALIZERS[info.field_name](value)

    def region_errors(self, region: Region) -> list[str]:
        """Validation that needs the profile's region (output languages)."""
        errors: list[str] = []
        if self.output_languages is not None:
            try:
                self.output_languages = validate_output_languages(region, self.output_languages)
            except ValueError as exc:
                errors.append(str(exc))
        return errors

    def changes(self) -> dict[str, Any]:
        """Fields the client actually sent, minus echoed masks of encrypted fields."""
        data = self.model_dump(exclude_unset=True, mode="python")
        for name in ENCRYPTED_FIELDS:
            if name in data and is_masked(data[name]):
                del data[name]
        # jsonb columns take JSON-native values (Decimal amounts become strings); enum lists
        # are stored as text[]
        as_json = self.model_dump(exclude_unset=True, mode="json")
        for name in (
            "addresses",
            "annual_revenue",
            "employees_by_country",
            "notice_types_wanted",
            "contract_types_preferred",
            "teaming_roles",
            "required_approver_roles",
            "scoring_weights",
            "bid_no_bid_weights",
        ):
            if data.get(name) is not None:
                data[name] = as_json[name]
        return data


class ProfileCreate(ProfileWrite):
    region: Region
    legal_name: Str


class ProfileUpdate(ProfileWrite):
    pass


class ProfileOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    region: Region
    is_active: bool
    version: int
    created_at: datetime
    updated_at: datetime
    legal_name: str
    dba_names: list[str]
    addresses: list[Address]
    website: str | None
    phone: str | None
    bid_inbox_email: str | None
    year_founded: int | None
    legal_structure: LegalStructure | None
    uei: str | None
    cage_code: str | None
    sam_status: SamStatus | None
    sam_expires_on: date | None
    ein: str | None  # masked
    pan: str | None  # masked
    gstin: str | None  # masked
    tan: str | None  # masked
    cin_llpin: str | None
    udyam_number: str | None
    udyam_category: UdyamCategory | None
    dpiit_number: str | None
    gem_seller_id: str | None
    local_supplier_class: LocalSupplierClass | None
    local_content_pct: Decimal | None
    bank_name: str | None
    bank_account_number: str | None  # masked
    bank_routing_code: str | None  # masked
    # 4.2
    employee_count_total: int | None
    employees_by_country: dict[str, int]
    annual_revenue: list[RevenueEntry]
    average_turnover: AverageTurnoverOut | None  # computed by core.finance
    net_worth_amount: Decimal | None
    net_worth_currency: str | None
    solvency_certificate_available: bool
    audited_fiscal_years: list[int]
    bonding_capacity_amount: Decimal | None
    bonding_capacity_currency: str | None
    mse_ownership: MseOwnership | None
    # 4.4
    target_countries: list[str]
    target_us_states: list[str]
    target_in_states: list[str]
    target_cities: list[str]
    remote_ok: bool
    target_buyers: list[str]
    blocked_buyers: list[str]
    value_min_usd: Decimal | None
    value_max_usd: Decimal | None
    value_min_inr: Decimal | None
    value_max_inr: Decimal | None
    notice_types_wanted: list[NoticeType]
    contract_types_preferred: list[ContractType]
    teaming_roles: list[TeamingRole]
    # 4.5
    cleared_personnel_count: int | None
    # 4.6
    scoring_weights: dict[str, int]
    bid_no_bid_weights: dict[str, int]
    required_approver_roles: list[Role]
    output_languages: list[str]
    # computed (M1-07): SBA status per NAICS code from USD average receipts / head count
    size_status_by_naics: dict[str, SizeStatusOut]

    @classmethod
    def from_row(cls, row: CompanyProfile, *, naics_codes: list[str] | None = None) -> ProfileOut:
        values = {name: getattr(row, name, None) for name in cls.model_fields}
        for name in ENCRYPTED_FIELDS:
            values[name] = mask_last4(values[name])
        avg = compute_average_turnover(row.annual_revenue)
        values["average_turnover"] = avg
        values["size_status_by_naics"] = compute_size_status(
            naics_codes or [], avg, row.employee_count_total
        )
        return cls.model_validate(values)


def compute_size_status(
    naics_codes: list[str], avg: AverageTurnoverOut | None, employees: int | None
) -> dict[str, SizeStatusOut]:
    """SBA receipts are USD: an INR turnover average cannot be compared, so it counts as
    unknown receipts (employee-based codes still resolve)."""
    receipts = avg.amount if avg is not None and avg.currency == "USD" else None
    return {
        code: SizeStatusOut(
            status=det.status,
            basis=det.basis,
            threshold=None if det.threshold is None else str(det.threshold),
            measured=None if det.measured is None else str(det.measured),
            reason=det.reason,
        )
        for code, det in size_status_by_naics(naics_codes, receipts, employees).items()
    }


def compute_average_turnover(entries: list[dict[str, Any]]) -> AverageTurnoverOut | None:
    avg = average_turnover(
        FiscalYearRevenue(int(e["fiscal_year"]), Decimal(str(e["amount"])), str(e["currency"]))
        for e in entries
    )
    if avg is None:
        return None
    return AverageTurnoverOut(
        amount=avg.amount, currency=avg.currency, fiscal_years=list(avg.fiscal_years)
    )
