"""Pydantic schemas for company profiles (SPEC 4.1). Writes accept plaintext for encrypted
fields and ignore echoed masks; reads always mask (app.core.crypto.mask_last4)."""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import Region
from app.core.crypto import is_masked, mask_last4
from app.core.profile_fields import (
    ENCRYPTED_FIELDS,
    NORMALIZERS,
    AddressKind,
    LegalStructure,
    LocalSupplierClass,
    SamStatus,
    UdyamCategory,
)
from app.models import CompanyProfile

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_COUNTRY = re.compile(r"^[A-Z]{2}$")

Str = Annotated[str, Field(min_length=1, max_length=300)]


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


class ProfileWrite(BaseModel):
    """Every writable 4.1 field, all optional so PUT can be partial."""

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
    # bank (encrypted)
    bank_name: Annotated[str | None, Field(max_length=200)] = None
    bank_account_number: Annotated[str | None, Field(max_length=64)] = None
    bank_routing_code: Annotated[str | None, Field(max_length=32)] = None

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

    def changes(self) -> dict[str, Any]:
        """Fields the client actually sent, minus echoed masks of encrypted fields."""
        data = self.model_dump(exclude_unset=True, mode="python")
        for name in ENCRYPTED_FIELDS:
            if name in data and is_masked(data[name]):
                del data[name]
        if "addresses" in data and data["addresses"] is not None:
            data["addresses"] = [
                a.model_dump(mode="json") if isinstance(a, Address) else a
                for a in data["addresses"]
            ]
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

    @classmethod
    def from_row(cls, row: CompanyProfile) -> ProfileOut:
        values = {name: getattr(row, name) for name in cls.model_fields}
        for name in ENCRYPTED_FIELDS:
            values[name] = mask_last4(values[name])
        return cls.model_validate(values)
