"""Web push subscriptions (SPEC 7). One row per browser, tenant-scoped (RLS)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class PushSubscription(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "push_subscriptions"
    # A person can belong to several tenants and subscribe from the same browser in each,
    # so the endpoint is unique per tenant, not globally.
    __table_args__ = (
        UniqueConstraint("tenant_id", "endpoint", name="uq_push_subscriptions_tenant_endpoint"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # the push service URL the browser handed us (FCM, Mozilla, WNS, ...)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    # RFC 8291 keys from PushSubscription.getKey(); base64url, not secrets of ours
    p256dh: Mapped[str] = mapped_column(String(255), nullable=False)
    auth: Mapped[str] = mapped_column(String(255), nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String(512))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
