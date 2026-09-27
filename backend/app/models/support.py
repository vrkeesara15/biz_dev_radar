"""support_access_grants: time-boxed platform-admin access into one tenant.

SPEC section 3 says a platform admin has "no access to tenant drafts unless support
access is granted and logged". The grant is the "granted" half (the audit_log row
written by services.audit.support_access_session is the "logged" half).

This is a GLOBAL table on purpose: a grant is platform bookkeeping *about* a tenant,
not tenant-owned data, and it is written and read only through the owner-role admin
session. It therefore carries `target_tenant_id` rather than `tenant_id`, gets no
app-role DML grant at all, and is listed in RLS_EXEMPT_TABLES in
tests/isolation/test_rls.py with the same reasoning.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class SupportAccessGrant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "support_access_grants"
    __table_args__ = (
        Index("ix_support_access_grants_target_expires", "target_tenant_id", "expires_at"),
    )

    target_tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    admin_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
