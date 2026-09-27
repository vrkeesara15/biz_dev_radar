"""matches (SPEC 6, 10.2): one row per (profile, opportunity, opportunity version, profile
version). Tenant-scoped (RLS). Later M4 tasks add match_feedback, saved_searches, alert_rules."""

from __future__ import annotations

import uuid
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class MatchBand(StrEnum):
    HIGH = "high"  # >= 70: instant alert
    MEDIUM = "medium"  # 50-69: digest
    LOW = "low"  # < 50: search only
    FILTERED = "filtered"  # dropped by a stage-1 hard filter (filtered_reason says which)


class Match(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "matches"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "opportunity_id",
            "opportunity_version",
            "profile_version",
            name="uq_matches_profile_opportunity_versions",
        ),
        Index("ix_matches_tenant_band_created", "tenant_id", "band", "created_at"),
    )

    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("company_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("opportunities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    opportunity_version: Mapped[int] = mapped_column(Integer, nullable=False)
    profile_version: Mapped[int] = mapped_column(Integer, nullable=False)
    # 0-100 after the set-aside cap; 0 for filtered rows
    score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    band: Mapped[str] = mapped_column(String(16), nullable=False)
    # {signals: {name: {raw, weight, weighted, note}}, filters: {...}, eligibility: [...]}
    breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    # stage-3 LLM rationale (M4-05); null until computed or when the model failed
    rationale: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    filtered_reason: Mapped[str | None] = mapped_column(Text)
    ineligible_set_aside: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
