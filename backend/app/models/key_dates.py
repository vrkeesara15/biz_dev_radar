"""pursuit_dates (SPEC 9, 10.2): the key dates every pursuit is tracked against.

Auto rows are derived from the notice by `app.core.key_dates` and re-derived when an
amendment moves the deadline; a row a human edited becomes `source = 'user'` and is never
shifted again. The reminder ladder (M6-03) and the calendar feed (M6-04) hang off these
rows, so `at` is always stored in UTC with the buyer's zone alongside for display.
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
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.key_dates import KIND_CUSTOM, KINDS, SOURCE_AUTO, SOURCES
from app.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin

KIND_SQL_LIST = ", ".join(f"'{kind}'" for kind in KINDS)
SOURCE_SQL_LIST = ", ".join(f"'{source}'" for source in SOURCES)
# one auto row per kind per pursuit; `custom` rows are free-form and may repeat
AUTO_KIND_PREDICATE = f"kind <> '{KIND_CUSTOM}'::text"


class PursuitDate(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "pursuit_dates"
    __table_args__ = (
        CheckConstraint(f"kind IN ({KIND_SQL_LIST})", name="kind"),
        CheckConstraint(f"source IN ({SOURCE_SQL_LIST})", name="source"),
        Index(
            "uq_pursuit_dates_pursuit_kind",
            "pursuit_id",
            "kind",
            unique=True,
            postgresql_where=text(AUTO_KIND_PREDICATE),
        ),
        Index("ix_pursuit_dates_at", "at"),
    )

    pursuit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pursuits.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # app.core.key_dates.KINDS
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # the buyer's zone at the time the row was written (SPEC 9 dual time zone rendering)
    buyer_tz: Mapped[str] = mapped_column(String(64), nullable=False, server_default="UTC")
    # auto = derived from the notice and re-derived on an amendment; user = never shifted
    source: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default=text(f"'{SOURCE_AUTO}'")
    )
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    # "5 days before the deadline", or the note left when an amendment shifted the row
    note: Mapped[str | None] = mapped_column(Text)
    # RFC 5545 SEQUENCE (M6-04): bumped on every change so calendars accept the newer
    # version of the event this row renders as.
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )
