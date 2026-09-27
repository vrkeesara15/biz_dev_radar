"""matches (SPEC 6, 10.2): one row per (profile, opportunity, opportunity version, profile
version), plus the learning loop it feeds - match_feedback (thumbs) and
keyword_suggestions (the weekly re-tune proposals) - and the alerting it drives:
saved_searches and alert_rules. All tenant-scoped (RLS)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
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


class Thumb(StrEnum):
    UP = "up"
    DOWN = "down"


class SuggestionStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class MatchFeedback(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """SPEC 6 learning loop: thumbs up/down and "not relevant because..." on an alert.

    One row per (match, user): a second opinion from the same person replaces the first.
    """

    __tablename__ = "match_feedback"
    __table_args__ = (UniqueConstraint("match_id", "user_id", name="uq_match_feedback_match_user"),)

    match_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("matches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    thumb: Mapped[str] = mapped_column(String(8), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)


class KeywordSuggestion(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """A weekly re-tune proposal (SPEC 6: never silently applied).

    One live row per (profile, kind, term); the weekly job refreshes a `pending` row and
    leaves an already decided one alone, so a rejected term is not proposed again.
    """

    __tablename__ = "keyword_suggestions"
    __table_args__ = (
        UniqueConstraint(
            "profile_id", "kind", "term", name="uq_keyword_suggestions_profile_kind_term"
        ),
        Index("ix_keyword_suggestions_tenant_status", "tenant_id", "status"),
    )

    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("company_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    term: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # include | exclude
    # signed change to profile_keywords.weight on approve (include), or 0 for exclude
    delta_weight: Mapped[Decimal] = mapped_column(
        Numeric(3, 1), nullable=False, server_default="0.0"
    )
    # {support, positives, negatives, rate, baseline, lift}
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


class AlertMode(StrEnum):
    INSTANT = "instant"
    DIGEST = "digest"


class SavedSearch(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """SPEC 6: "users can save any filter set; each saved search is also an alert rule"."""

    __tablename__ = "saved_searches"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "name", name="uq_saved_searches_user_name"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # the GET /opportunities query string as jsonb (core.matching.saved_search.SearchFilters)
    filters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")


class AlertRule(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """Which new matches reach which channels (SPEC 6 / 7).

    `saved_search_id` is NULL for a rule that is not backed by a saved search, and
    `profile_id` is NULL for a rule that applies to every profile of the tenant.
    """

    __tablename__ = "alert_rules"
    __table_args__ = (
        Index("ix_alert_rules_tenant_enabled", "tenant_id", "enabled"),
        UniqueConstraint("tenant_id", "name", name="uq_alert_rules_tenant_name"),
    )

    saved_search_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("saved_searches.id", ondelete="CASCADE"), index=True
    )
    profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("company_profiles.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    min_score: Mapped[int] = mapped_column(Integer, nullable=False, server_default="70")
    # app.notify.registry.CHANNEL_NAMES values (in_app, email, slack, teams, push)
    channels: Mapped[list[str]] = mapped_column(
        ARRAY(Text()), nullable=False, server_default=text("'{}'::text[]")
    )
    mode: Mapped[str] = mapped_column(String(16), nullable=False, server_default="instant")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
