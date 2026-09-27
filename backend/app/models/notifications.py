"""notifications + notification_deliveries (SPEC 7, 10.2). Tenant-scoped (RLS).

A notification is one event for one user (the in-app bell item); each delivery is one
channel attempt log with its own idempotency key (SPEC 7: exactly once per channel).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class DeliveryChannel(StrEnum):
    IN_APP = "in_app"
    EMAIL = "email"
    SLACK = "slack"
    TEAMS = "teams"
    WHATSAPP = "whatsapp"
    PUSH = "push"


class DeliveryStatus(StrEnum):
    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"
    OPENED = "opened"
    SKIPPED = "skipped"


class Notification(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_unread", "tenant_id", "user_id", "read_at", "created_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # app.core.preferences.NotificationEvent values plus ops events (adapter_failing, ...)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("opportunities.id", ondelete="SET NULL"), index=True
    )
    # pursuits arrive with M6; kept as a plain uuid until then
    pursuit_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    # "<user>:<event_type>:<opportunity or pursuit id>:<version>" (SPEC 7)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    # {title, buyer, score, band, deep_link, actions{pursue,watch,pass,assign}, actions_taken[]}
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotificationDelivery(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        Index("ix_notification_deliveries_status_scheduled", "status", "scheduled_for"),
    )

    notification_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("notifications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=DeliveryStatus.QUEUED.value
    )
    # "<notification key>:<channel>" (+ ":fallback:<failed channel>" for email fallbacks)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text)
    # quiet hours / digest (M4-13): queued until this instant; NULL = send now
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # set on the email delivery created after another channel failed 3x (SPEC 7)
    fallback_of_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("notification_deliveries.id", ondelete="SET NULL")
    )
    fallback_reason: Mapped[str | None] = mapped_column(Text)
    # provider message id (SES/SendGrid/Slack ts) for opened/bounce webhooks
    provider_ref: Mapped[str | None] = mapped_column(String(256))
