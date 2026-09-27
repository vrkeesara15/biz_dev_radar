"""Canonical opportunity schema (SPEC 5.3) as pure Pydantic types.

`OpportunityIn` is what every adapter's `normalize()` returns and what the ingest
pipeline upserts. It lives in core (I/O-free) so the per-source mapping functions in
`app.core.normalize` can build it and count toward core coverage; `app.adapters.base`
re-exports it as part of the adapter contract.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import Region


class NoticeType(StrEnum):
    RFI = "rfi"
    SOURCES_SOUGHT = "sources_sought"
    PRESOLICITATION = "presolicitation"
    RFP = "rfp"
    RFQ = "rfq"
    COMBINED = "combined"
    GRANT = "grant"
    FORECAST = "forecast"
    AWARD = "award"
    EOI = "eoi"
    GEM_BID = "gem_bid"
    REVERSE_AUCTION = "reverse_auction"
    CORRIGENDUM = "corrigendum"
    SPECIAL = "special"


class OpportunityStatus(StrEnum):
    OPEN = "open"
    CLOSING_SOON = "closing_soon"
    CLOSED = "closed"
    CANCELLED = "cancelled"
    AWARDED = "awarded"


class DetailStatus(StrEnum):
    """How much of the detail page we could read. 'manual' = CAPTCHA/login wall (SPEC 5.1)."""

    PENDING = "pending"
    FULL = "full"
    MANUAL = "manual"


class DocumentKind(StrEnum):
    ATTACHMENT = "attachment"
    DESCRIPTION = "description"
    AMENDMENT = "amendment"
    QA = "qa"
    OTHER = "other"


CURRENCY_BY_REGION: dict[Region, str] = {Region.US: "USD", Region.IN: "INR"}
COUNTRY_BY_REGION: dict[Region, str] = {Region.US: "US", Region.IN: "IN"}


def ensure_utc(value: datetime | None) -> datetime | None:
    """Require tz-aware datetimes and normalise them to UTC (SPEC 5.3: all stored UTC)."""
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetimes must be time-zone aware")
    return value.astimezone(UTC)


class DocumentRef(BaseModel):
    """A downloadable document attached to a notice (resourceLinks, bid PDFs, ...)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str
    file_name: str | None = None
    kind: DocumentKind = DocumentKind.ATTACHMENT
    # Known before download when the source publishes them; the parser fills the rest.
    sha256: str | None = None
    size: int | None = None
    mime_type: str | None = None


class Contact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    title: str | None = None
    email: str | None = None
    phone: str | None = None
    kind: str | None = None  # 'primary', 'secondary', 'technical' ... as published


class PlaceOfPerformance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    city: str | None = None
    state: str | None = None
    country: str | None = None
    postal_code: str | None = None  # zip or PIN
    remote: bool = False
    raw: str | None = None


class OpportunityIn(BaseModel):
    """Every SPEC 5.3 field an adapter can supply. Optional where a source lacks it.

    `id`, `summary_ai`, `embedding`, `content_hash`, `version`, `parent_opportunity_id`,
    `duplicate_of` and the awards-enrichment fields are computed by later stages and are
    not part of the adapter output.
    """

    model_config = ConfigDict(extra="forbid")

    # identity
    source_id: str = Field(min_length=1, max_length=64)
    external_id: str = Field(min_length=1, max_length=256)
    source_url: str | None = None
    region: Region
    country: str = Field(min_length=2, max_length=2)
    currency: str = Field(min_length=3, max_length=3)
    notice_type: NoticeType

    # content
    title: str = Field(min_length=1)
    description_text: str | None = None
    solicitation_number: str | None = None
    # Resolved by the pipeline into parent_opportunity_id (same source, earlier notice).
    parent_external_id: str | None = None

    # buyer
    buyer_org: str | None = None
    buyer_sub_org: str | None = None
    buyer_office: str | None = None
    buyer_hierarchy: list[str] = Field(default_factory=list)

    # classification
    naics: list[str] = Field(default_factory=list)
    psc: list[str] = Field(default_factory=list)
    aln: list[str] = Field(default_factory=list)
    india_category: list[str] = Field(default_factory=list)
    set_aside: str | None = None
    reservation: str | None = None
    place_of_performance: PlaceOfPerformance | None = None

    # money (source currency; USD normalisation happens in the pipeline)
    estimated_value_min: Decimal | None = None
    estimated_value_max: Decimal | None = None
    emd_amount: Decimal | None = None
    tender_fee: Decimal | None = None

    # dates: tz-aware, stored UTC; source_tz keeps the buyer's zone for display
    posted_at: datetime | None = None
    questions_due_at: datetime | None = None
    prebid_meeting_at: datetime | None = None
    response_due_at: datetime | None = None
    opening_at: datetime | None = None
    archive_at: datetime | None = None
    source_tz: str = "UTC"

    contacts: list[Contact] = Field(default_factory=list)
    eligibility: dict[str, Any] = Field(default_factory=dict)
    documents: list[DocumentRef] = Field(default_factory=list)

    # Only when the source states it (cancellation / award notices); else derived.
    status: OpportunityStatus | None = None
    detail_status: DetailStatus = DetailStatus.PENDING
    # Source-specific fields worth keeping for matching/display (type codes, categories).
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "posted_at",
        "questions_due_at",
        "prebid_meeting_at",
        "response_due_at",
        "opening_at",
        "archive_at",
    )
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value)

    @field_validator("naics", "psc", "aln", "india_category", "buyer_hierarchy")
    @classmethod
    def _clean_list(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for item in value:
            cleaned = item.strip()
            if cleaned and cleaned not in seen:
                seen.append(cleaned)
        return seen

    @field_validator("country", "currency")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.upper()
