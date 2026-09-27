"""Async engine and tenant-scoped sessions.

Two engines:
  * app engine  -> role bidradar_app (non-owner, NOBYPASSRLS). Every transaction
    runs `set_config('app.tenant_id', <tenant>, true)` (= SET LOCAL) so RLS
    policies see the request tenant. No tenant -> setting is NULL -> RLS hides all.
  * owner engine -> migrations and the explicit platform-admin bypass; the bypass
    helper lives in app.services.audit and always writes an audit_log row.

The tenant id is bound to the Session via `session.info["tenant_id"]` and applied
by an `after_begin` listener, so it survives commit() within the same session.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session

TENANT_SETTING = "app.tenant_id"
_SET_TENANT_SQL = text("SELECT set_config(:name, :value, true)")
_LISTENER_FLAG = "bidradar_tenant_listener"


def _apply_tenant(session: Session, transaction: Any, connection: Any) -> None:
    tenant_id = session.info.get("tenant_id")
    if tenant_id is None:
        return
    connection.execute(_SET_TENANT_SQL, {"name": TENANT_SETTING, "value": str(tenant_id)})


def _install_listener(sync_session_class: type[Session]) -> None:
    if getattr(sync_session_class, _LISTENER_FLAG, False):
        return
    event.listen(sync_session_class, "after_begin", _apply_tenant)
    setattr(sync_session_class, _LISTENER_FLAG, True)


class TenantSession(Session):
    """Sync session class used under AsyncSession; carries the tenant listener."""


_install_listener(TenantSession)


class Database:
    """Holds the two engines and session factories. One instance per process."""

    def __init__(self, app_url: str, owner_url: str, *, echo: bool = False, **engine_kwargs: Any):
        self.app_engine: AsyncEngine = create_async_engine(app_url, echo=echo, **engine_kwargs)
        self.owner_engine: AsyncEngine = create_async_engine(owner_url, echo=echo, **engine_kwargs)
        self._app_sessions = async_sessionmaker(
            self.app_engine, expire_on_commit=False, sync_session_class=TenantSession
        )
        self._owner_sessions = async_sessionmaker(
            self.owner_engine, expire_on_commit=False, sync_session_class=TenantSession
        )

    @asynccontextmanager
    async def session(self, tenant_id: uuid.UUID | None) -> AsyncIterator[AsyncSession]:
        """Tenant-scoped session on the app role. Commits on success, rolls back on error."""
        async with self._app_sessions() as session:
            session.info["tenant_id"] = tenant_id
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    @asynccontextmanager
    async def owner_session(
        self, tenant_id: uuid.UUID | None = None
    ) -> AsyncIterator[AsyncSession]:
        """Owner-role session (bypasses RLS). Only for migrations, seeds and audited admin use."""
        async with self._owner_sessions() as session:
            session.info["tenant_id"] = tenant_id
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    async def dispose(self) -> None:
        await self.app_engine.dispose()
        await self.owner_engine.dispose()


_database: Database | None = None


def get_database() -> Database:
    """Process-wide Database built from Settings (lazy so imports stay side-effect free)."""
    global _database
    if _database is None:
        from app.core.config import get_settings

        s = get_settings()
        _database = Database(s.database_url, s.database_url_owner)
    return _database


def set_database(database: Database | None) -> None:
    """Override the process-wide Database (tests, workers)."""
    global _database
    _database = database


@asynccontextmanager
async def get_session(tenant_id: uuid.UUID | None) -> AsyncIterator[AsyncSession]:
    """Convenience wrapper: tenant-scoped app-role session from the process Database."""
    async with get_database().session(tenant_id) as session:
        yield session
