"""calendar_connections and calendar_events (SPEC 7, 9; M6-04).

A connection is one user's Google or Microsoft calendar in one tenant — a PER-USER
credential, which is why it is not an `integrations` row (those are per tenant, M4-11).
The OAuth refresh token never lands in a column: `secret_ref` names it the same way
Slack's webhook is named (app.services.secrets).

calendar_events maps one `pursuit_dates` row to the provider event it created, so a date
that moves updates the same event and a date that is deleted removes it.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin

PROVIDER_GOOGLE = "google"
PROVIDER_MICROSOFT = "microsoft"
CALENDAR_PROVIDERS: tuple[str, ...] = (PROVIDER_GOOGLE, PROVIDER_MICROSOFT)
PROVIDER_SQL_LIST = ", ".join(f"'{value}'" for value in CALENDAR_PROVIDERS)


class CalendarConnection(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "calendar_connections"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "user_id", "provider", name="uq_calendar_connections_user_provider"
        ),
        CheckConstraint(f"provider IN ({PROVIDER_SQL_LIST})", name="provider"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    # "primary" for Google, a Graph calendar id for Microsoft
    calendar_id: Mapped[str] = mapped_column(String(256), nullable=False, server_default="primary")
    # never the token itself: "env:NAME" | "sm://..." | "enc:v1:..." (app.services.secrets)
    secret_ref: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
    last_error: Mapped[str | None] = mapped_column(Text)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CalendarEvent(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "calendar_events"
    __table_args__ = (
        UniqueConstraint(
            "connection_id", "pursuit_date_id", name="uq_calendar_events_connection_date"
        ),
    )

    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("calendar_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    pursuit_date_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pursuit_dates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider_event_id: Mapped[str] = mapped_column(String(512), nullable=False)
    # RFC 5545 SEQUENCE: bumped on every push so the provider accepts the newer version
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
