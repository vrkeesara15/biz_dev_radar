"""requirements (SPEC 8 agent 2, 10.2): one row per extracted requirement of a pursuit,
always with its document + page citation (the validator rejects anything without one).

Re-running the extractor replaces the pursuit's rows (compliance_items cascade); every
extraction attempt's full output is also kept on its agent_steps row.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin

REQUIREMENT_TYPES: tuple[str, ...] = (
    "shall",
    "must",
    "should",
    "eligibility",
    "format",
    "submission",
    "evaluation",
)


class Requirement(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "requirements"
    __table_args__ = (UniqueConstraint("pursuit_id", "req_id", name="uq_requirements_req_id"),)

    pursuit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pursuits.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    req_id: Mapped[str] = mapped_column(String(16), nullable=False)  # R-001 ...
    text: Mapped[str] = mapped_column(Text, nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("opportunity_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    page: Mapped[int] = mapped_column(Integer, nullable=False)
    # shall | must | should | eligibility | format | submission | evaluation
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    volume: Mapped[str | None] = mapped_column(Text)
    # verbatim excerpt of the page the requirement was taken from (the clickable citation)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
