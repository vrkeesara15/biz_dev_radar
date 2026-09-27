"""Audit log writes and the audited platform-admin bypass session (SPEC section 11)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import get_request_id
from app.core.db import Database, get_database
from app.models import AuditLog, Tenant

SUPPORT_ACCESS_ACTION = "support_access"


class TenantNotFoundError(LookupError):
    """Raised when support access targets a tenant id that does not exist."""


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
        if await audit_session.get(Tenant, tenant_id) is None:
            raise TenantNotFoundError(str(tenant_id))
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


@dataclass(frozen=True, slots=True)
class AuditHint:
    """Handlers may set `request.state.audit = AuditHint(...)` to name the action/object
    the middleware records for a mutating request."""

    action: str
    object_type: str | None = None
    object_id: str | None = None
    meta: dict[str, Any] | None = None


async def audit(
    session: AsyncSession,
    action: str,
    obj: Any,
    *,
    user_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
    ip: str | None = None,
    meta: dict[str, Any] | None = None,
) -> AuditLog:
    """Log a read or service-level action against a model row.

    `obj` is a mapped instance (object_type = its table name, object_id = its id) or a
    (object_type, object_id) tuple. tenant_id defaults to obj.tenant_id, then to the
    session's bound tenant. Use it for draft/export reads that must be logged.
    """
    if isinstance(obj, tuple):
        object_type, object_id = obj
    else:
        object_type = getattr(obj, "__tablename__", type(obj).__name__.lower())
        object_id = getattr(obj, "id", None)
    resolved_tenant = tenant_id or getattr(obj, "tenant_id", None) or session.info.get("tenant_id")
    if resolved_tenant is None:
        raise ValueError("audit() needs a tenant: pass tenant_id or use a tenant-bound session")
    return await write_audit(
        session,
        tenant_id=resolved_tenant,
        user_id=user_id,
        action=action,
        object_type=object_type,
        object_id=object_id,
        ip=ip,
        meta=meta,
    )
