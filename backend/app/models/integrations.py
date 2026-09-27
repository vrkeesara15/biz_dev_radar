"""Tenant integrations (SPEC 7, 10.3): Slack, Teams, WhatsApp and calendar connections.

One row per (tenant, kind). `config` holds the non-secret settings (default channel,
mention targets, locale); `secret_ref` names where the webhook URL and signing secret
live — see app.services.secrets for the schemes.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class IntegrationKind(StrEnum):
    SLACK = "slack"
    TEAMS = "teams"
    WHATSAPP = "whatsapp"
    GOOGLE_CALENDAR = "google_calendar"
    MICROSOFT_CALENDAR = "microsoft_calendar"


class Integration(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "integrations"
    __table_args__ = (UniqueConstraint("tenant_id", "kind", name="uq_integrations_tenant_kind"),)

    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    # {"channel": "#bids", "mention": "@here", "ops": false, ...} - never secrets
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # "env:SLACK_WEBHOOK_ACME" | "sm://projects/p/secrets/s/versions/latest" |
    # "enc:v1:<nonce>:<ciphertext>" (app.services.secrets.resolve_secret)
    secret_ref: Mapped[str | None] = mapped_column(Text)
