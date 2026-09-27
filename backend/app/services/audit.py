"""Audit log writes and the audited platform-admin bypass session (SPEC section 11)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import get_request_id
from app.core.db import Database, get_database
from app.models import AuditLog

SUPPORT_ACCESS_ACTION = "support_access"


def build_audit_row(
    *,
    tenant_id: uuid.UUID,
    action: str,
    user_id: uuid.UUID | None = None,
    object_type: str | None = None,
    object_id: str | uuid.UUID | None = None,
    ip: str | None = None,
    meta: dict[str, Any] | None = None,
) -> AuditLog:
    return AuditLog(
        tenant_id=tenant_id,
        user_id=user_id,
        action=action,
        object_type=object_type,
        object_id=None if object_id is None else str(object_id),
        ip=ip,
        request_id=get_request_id(),
        meta=meta or {},
    )


async def write_audit(session: AsyncSession, **kwargs: Any) -> AuditLog:
    """Add an audit row to an open session (committed with the caller's transaction)."""
    row = build_audit_row(**kwargs)
    session.add(row)
    await session.flush()
    return row


@asynccontextmanager
async def support_access_session(
    *,
    tenant_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    reason: str,
    ip: str | None = None,
    database: Database | None = None,
) -> AsyncIterator[AsyncSession]:
    """Owner-role session that bypasses RLS for platform-admin support access.

    The audit row is committed in its own transaction BEFORE the bypass session opens,
    so it survives even if the admin's work later fails or rolls back.
    """
    if not reason.strip():
        raise ValueError("support access requires a reason")
    db = database or get_database()
    async with db.owner_session(tenant_id) as audit_session:
        await write_audit(
            audit_session,
            tenant_id=tenant_id,
            user_id=actor_user_id,
            action=SUPPORT_ACCESS_ACTION,
            object_type="tenant",
            object_id=tenant_id,
            ip=ip,
            meta={"reason": reason},
        )
    async with db.owner_session(tenant_id) as session:
        yield session
