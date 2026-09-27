"""Per-user notification preferences (SPEC 4.6, 7, 10.2 user_notification_prefs)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.preferences import (
    DEFAULT_DIGEST_TIME,
    DEFAULT_MIN_SCORE_DIGEST,
    DEFAULT_MIN_SCORE_INSTANT,
)
from app.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class UserNotificationPrefs(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "user_notification_prefs"
    __table_args__ = (
        UniqueConstraint("user_id", "tenant_id", name="uq_user_notification_prefs_user_tenant"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # {event: [channel, ...]} (app.core.preferences NotificationEvent / NotificationChannel)
    channels_by_event: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    quiet_hours_start: Mapped[str | None] = mapped_column(String(5))
    quiet_hours_end: Mapped[str | None] = mapped_column(String(5))
    tz: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("'UTC'"))
    digest_time: Mapped[str] = mapped_column(
        String(5), nullable=False, server_default=DEFAULT_DIGEST_TIME
    )
    min_score_instant: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text(str(DEFAULT_MIN_SCORE_INSTANT))
    )
    min_score_digest: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text(str(DEFAULT_MIN_SCORE_DIGEST))
    )
