"""reminders (SPEC 9, 10.2): one row per rung of the ladder on one key date.

The row IS the idempotency record: `sent_at` is stamped inside the same transaction as
the dispatch, so a beat tick that overlaps the previous one cannot send twice, and
`skipped_reason` records a rung that came due after the pursuit was submitted, passed or
cancelled. A date that moves has its unsent rungs regenerated (M6-02 recalculation).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.reminders import LABELS
from app.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin

LABEL_SQL_LIST = ", ".join(f"'{label}'" for label in LABELS)


class Reminder(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "reminders"
    __table_args__ = (
        UniqueConstraint(
            "pursuit_date_id", "offset_label", "due_at", name="uq_reminders_date_label_due"
        ),
        CheckConstraint(f"offset_label IN ({LABEL_SQL_LIST})", name="offset_label"),
        # the beat's only query: unsent rungs that have come due
        Index("ix_reminders_pending", "sent_at", "due_at"),
    )

    pursuit_date_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pursuit_dates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # app.core.reminders.LABELS: 7d | 3d | 24h | 4h | 1h | overdue
    offset_label: Mapped[str] = mapped_column(String(8), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # why this rung was never sent (the pursuit closed before it came due)
    skipped_reason: Mapped[str | None] = mapped_column(Text)
    # the notification the dispatch created, so the delivery log links back to the rung
    delivery_notification_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("notifications.id", ondelete="SET NULL")
    )
    # 0 owner | 1 + bid managers | 2 + tenant owner (app.core.reminders)
    escalation_level: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
