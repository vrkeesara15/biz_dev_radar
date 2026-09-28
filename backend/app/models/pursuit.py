"""pursuits (SPEC 9, 10.2): a tenant's decision to chase one opportunity with one profile.

M5-02 created the minimal shape the agent pipeline hangs off; M5-06/M5-10/M5-13 added the
Gate 1 and Gate 2 stamps and the "package final" switch; M6-01 added the stage rules
(`app.core.pursuit_stages`), the watch flag and the pass reason. Agent runs, requirements,
compliance items, artifacts, drafts, key dates, tasks, comments, exports and reminders all
hang off `pursuit_id`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.pursuit_stages import DECISIONS, DEFAULT_STAGE, STAGES
from app.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin

__all__ = ["DECISIONS", "STAGES", "STAGE_IDENTIFIED", "STAGE_SQL_LIST", "Pursuit"]

STAGE_IDENTIFIED = DEFAULT_STAGE
# "'identified', 'qualifying', ..." for the CHECK constraint, in SPEC 9 order
STAGE_SQL_LIST = ", ".join(f"'{stage}'" for stage in STAGES)


class Pursuit(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "pursuits"
    __table_args__ = (
        UniqueConstraint("profile_id", "opportunity_id", name="uq_pursuits_profile_opportunity"),
        # a CHECK rather than a Postgres enum: SPEC 9 stages are product vocabulary and a
        # future stage should be one migration, not an enum rewrite under load.
        CheckConstraint(f"stage IN ({STAGE_SQL_LIST})", name="stage"),
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
    # submitted | awarded | lost | cancelled | no_bid (rules: app.core.pursuit_stages)
    stage: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text(f"'{DEFAULT_STAGE}'"), index=True
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    decision: Mapped[str | None] = mapped_column(String(16))  # bid | no_bid
    # Gate 1 (SPEC 8, 9): who recorded the decision, when, and why
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)
    # Gate 2 (SPEC 8): who approved the whole draft package for export, and when
    package_approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    package_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # SPEC 11: exports carry the "DRAFT - internal" footer until a human marks the
    # package final (POST /pursuits/{id}/mark-final, after Gate 2)
    package_final: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    package_final_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    package_final_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    internal_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # "Watch" (SPEC 7 one-click action): tracked for amendments, but no agent work
    watch: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    # why the tenant passed (POST /opportunities/{id}/pass); feeds match feedback later
    pass_reason: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # SPEC 9 "stale pursuits": the last time anyone (or any agent) touched this pursuit.
    # Bumped by app.services.pursuits.touch from every stage move, task, comment, date and
    # agent run (OQ-132).
    activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    # SPEC 9: an amendment on a notice whose pursuit is past drafting forces a re-check
    matrix_recheck_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false()
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    # Per-pursuit LLM spend cap in USD (SPEC 8); NULL = the tenant's default cap. Raised by
    # POST /pursuits/{id}/agents/approve-budget (recorded in audit_log).
    cost_cap_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
