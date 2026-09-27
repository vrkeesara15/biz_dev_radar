"""Canonical opportunities and related global tables (SPEC 5.3, 5.4, 10.2).

These tables hold PUBLIC notices shared by every tenant: no tenant_id, no RLS. Tenant
state about an opportunity (matches, pursuits, notes) lives in tenant tables that
reference `opportunities.id`.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import Region
from app.core.opportunity import NoticeType, OpportunityStatus
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.tenancy import RegionEnum, _values

EMBEDDING_DIM = 1024

NoticeTypeEnum = Enum(NoticeType, name="notice_type", values_callable=_values)
OpportunityStatusEnum = Enum(OpportunityStatus, name="opportunity_status", values_callable=_values)

# Full-text expression shared by the model index and the migration. Written exactly as
# Postgres reflects it, so the migration-drift test sees identical text on both sides.
FTS_EXPR = (
    "to_tsvector('english'::regconfig, (COALESCE(title, ''::text) || ' '::text) || "
    "COALESCE(description_text, ''::text))"
)


def _updated_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Opportunity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "opportunities"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_opportunities_source_external"),
        Index(
            "ix_opportunities_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
        Index("ix_opportunities_fts", text(FTS_EXPR), postgresql_using="gin"),
        Index("ix_opportunities_naics", "naics", postgresql_using="gin"),
        Index("ix_opportunities_region_status_due", "region", "status", "response_due_at"),
    )

    # identity (SPEC 5.3)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(256), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    region: Mapped[Region] = mapped_column(RegionEnum, nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    notice_type: Mapped[NoticeType] = mapped_column(NoticeTypeEnum, nullable=False, index=True)

    # content
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description_text: Mapped[str | None] = mapped_column(Text)
    summary_ai: Mapped[str | None] = mapped_column(Text)
    solicitation_number: Mapped[str | None] = mapped_column(String(128), index=True)
    parent_opportunity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("opportunities.id", ondelete="SET NULL"), index=True
    )

    # buyer
    buyer_org: Mapped[str | None] = mapped_column(Text)
    buyer_sub_org: Mapped[str | None] = mapped_column(Text)
    buyer_office: Mapped[str | None] = mapped_column(Text)
    buyer_hierarchy: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )

    # classification
    naics: Mapped[list[str]] = mapped_column(ARRAY(String(16)), nullable=False, server_default="{}")
    psc: Mapped[list[str]] = mapped_column(ARRAY(String(16)), nullable=False, server_default="{}")
    aln: Mapped[list[str]] = mapped_column(ARRAY(String(16)), nullable=False, server_default="{}")
    india_category: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), nullable=False, server_default="{}"
    )
    set_aside: Mapped[str | None] = mapped_column(String(32))
    reservation: Mapped[str | None] = mapped_column(String(64))
    place_of_performance: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # money: source currency plus USD-normalised copies
    estimated_value_min: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    estimated_value_max: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    estimated_value_min_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    estimated_value_max_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    emd_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    tender_fee: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))

    # dates (UTC) + the buyer's zone for display
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    questions_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    prebid_meeting_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    opening_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archive_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_tz: Mapped[str] = mapped_column(String(64), nullable=False, server_default="UTC")

    contacts: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    eligibility: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")

    # awards enrichment (denormalised from awards_enrichment for search/display)
    incumbent: Mapped[str | None] = mapped_column(Text)
    prior_award_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    prior_pop_end: Mapped[date | None] = mapped_column(Date)

    status: Mapped[OpportunityStatus] = mapped_column(
        OpportunityStatusEnum, nullable=False, server_default="open", index=True
    )
    # pending | full | manual (CAPTCHA/login wall: store the id + portal URL, SPEC 5.1)
    detail_status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")

    # change detection and dedupe (SPEC 5.4)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    duplicate_of: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("opportunities.id", ondelete="SET NULL"), index=True
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    raw_ref: Mapped[str | None] = mapped_column(Text)
    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = _updated_at()

    versions: Mapped[list[OpportunityVersion]] = relationship(
        back_populates="opportunity",
        cascade="all, delete-orphan",
        order_by="OpportunityVersion.version",
    )
    documents: Mapped[list[OpportunityDocument]] = relationship(
        back_populates="opportunity", cascade="all, delete-orphan"
    )
    awards: Mapped[list[AwardsEnrichment]] = relationship(
        back_populates="opportunity", cascade="all, delete-orphan"
    )


class OpportunityVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One row per detected change: field-level diff {field: {old, new}} + change kinds."""

    __tablename__ = "opportunity_versions"
    __table_args__ = (
        UniqueConstraint("opportunity_id", "version", name="uq_opportunity_versions_version"),
    )

    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("opportunities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    diff: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    # deadline_moved | new_attachment | qa_posted | cancelled | awarded | status | other
    changes: Mapped[list[str]] = mapped_column(
        ARRAY(String(32)), nullable=False, server_default="{}"
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    opportunity: Mapped[Opportunity] = relationship(back_populates="versions")


class OpportunityDocument(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "opportunity_documents"
    __table_args__ = (
        UniqueConstraint("opportunity_id", "url", name="uq_opportunity_documents_url"),
    )

    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("opportunities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_name: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, server_default="attachment")
    mime_type: Mapped[str | None] = mapped_column(String(128))
    hash: Mapped[str | None] = mapped_column(String(64))  # sha256 of the bytes
    size: Mapped[int | None] = mapped_column(BigInteger)
    pages: Mapped[int | None] = mapped_column(Integer)
    # pending | downloaded | parsed | failed | skipped
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    parsed_text_ref: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = _updated_at()

    opportunity: Mapped[Opportunity] = relationship(back_populates="documents")
    chunks: Mapped[list[DocumentChunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentChunk(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_document_chunks_index"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("opportunity_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))

    document: Mapped[OpportunityDocument] = relationship(back_populates="chunks")


class AwardsEnrichment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Prior awards linked to an opportunity (SAM awards, USAspending). opportunity_id is
    NULL for recompete candidates that have no live notice yet (M2-07)."""

    __tablename__ = "awards_enrichment"
    __table_args__ = (Index("ix_awards_enrichment_source_award", "source_id", "award_id"),)

    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("opportunities.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    award_id: Mapped[str] = mapped_column(String(128), nullable=False)
    incumbent: Mapped[str | None] = mapped_column(Text)
    prior_award_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    prior_pop_start: Mapped[date | None] = mapped_column(Date)
    prior_pop_end: Mapped[date | None] = mapped_column(Date, index=True)
    num_offers: Mapped[int | None] = mapped_column(Integer)
    agency: Mapped[str | None] = mapped_column(Text)
    sub_agency: Mapped[str | None] = mapped_column(Text)
    naics: Mapped[str | None] = mapped_column(String(16), index=True)
    psc: Mapped[str | None] = mapped_column(String(16))
    solicitation_number: Mapped[str | None] = mapped_column(String(128))
    # solicitation_number | naics_agency | recompete_candidate
    match_method: Mapped[str | None] = mapped_column(String(32))
    recompete_watch: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    source_ref: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = _updated_at()

    opportunity: Mapped[Opportunity | None] = relationship(back_populates="awards")
