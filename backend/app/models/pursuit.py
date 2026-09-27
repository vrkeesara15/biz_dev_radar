"""pursuits (SPEC 9, 10.2): a tenant's decision to chase one opportunity with one profile.

Minimal shape for the agent pipeline (M5): M6-01 adds the stage rules, key dates, tasks
and the pursue/watch/pass routes. Agent runs, requirements, compliance items and
artifacts hang off `pursuit_id`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin

STAGE_IDENTIFIED = "identified"


class Pursuit(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "pursuits"
    __table_args__ = (
        UniqueConstraint("profile_id", "opportunity_id", name="uq_pursuits_profile_opportunity"),
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
    # identified | qualifying | bid_decision | drafting | in_review | final_approval |
    # submitted | awarded | lost | cancelled | no_bid (rules arrive with M6-01)
    stage: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'identified'")
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    decision: Mapped[str | None] = mapped_column(String(16))  # bid | no_bid
    internal_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    # Per-pursuit LLM spend cap in USD (SPEC 8); NULL = the tenant's default cap. Raised by
    # POST /pursuits/{id}/agents/approve-budget (recorded in audit_log).
    cost_cap_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
