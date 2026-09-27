"""compliance_items and pursuit_artifacts (SPEC 8 agent 3, 10.2).

compliance_items is the matrix: one row per requirement, the proposal section it belongs
to, its owner and its status. pursuit_artifacts is the generic versioned JSON store for
everything an agent produces that is not a row of its own -- format rules, the submission
checklist, the packet, the bid/no-bid scorecard, the outline, the pricing template
reference. Re-running an agent appends a new version; nothing is overwritten.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class ComplianceItem(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "compliance_items"
    __table_args__ = (
        UniqueConstraint("pursuit_id", "requirement_id", name="uq_compliance_items_requirement"),
    )

    pursuit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pursuits.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    requirement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("requirements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    section: Mapped[str] = mapped_column(String(64), nullable=False)
    # volume | reason the section was chosen: volume | type | keyword | default | model
    reason: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'type'"))
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    # open | drafted | reviewed | done | na
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'open'"))
    notes: Mapped[str | None] = mapped_column(Text)


class PursuitArtifact(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "pursuit_artifacts"
    __table_args__ = (
        UniqueConstraint("pursuit_id", "kind", "version", name="uq_pursuit_artifacts_version"),
    )

    pursuit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pursuits.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # format_rules | checklist | packet | scorecard | outline | pricing_template
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_by: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'agent'")
    )
