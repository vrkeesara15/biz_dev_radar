"""Consent records and data-principal requests (SPEC 11, M7-07). Both tenant-scoped."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class Consent(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """One row per (user, notice kind, version) acceptance; never updated in place.

    Re-accepting the same version is a no-op (the unique constraint); a new notice
    version produces a new row, so the trail shows what was shown and when.
    """

    __tablename__ = "consents"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "user_id", "kind", "version", name="uq_consents_tenant_user_kind_version"
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # app.core.privacy.ConsentKind: dpdp | privacy_policy | terms
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ip: Mapped[str | None] = mapped_column(String(64))


class DataRequest(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """A data-principal or tenant-owner request with its statutory answer-by date."""

    __tablename__ = "data_requests"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    # app.core.privacy.DataRequestKind
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # app.core.privacy.DataRequestStatus
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'received'")
    )
    # free-form request payload and result summary (export counts, deletion counts, note)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    sla_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_file_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("files.id", ondelete="SET NULL")
    )
