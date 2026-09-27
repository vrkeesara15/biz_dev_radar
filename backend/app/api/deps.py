"""FastAPI dependencies: current user (JWT), RBAC, tenant-scoped and admin sessions."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthError, decode_token, parse_bearer
from app.core.config import Settings
from app.core.context import set_principal
from app.core.db import get_database
from app.core.ratelimit import FixedWindowLimiter, client_ip_from_headers
from app.core.roles import Role
from app.notify.core import Dispatcher
from app.services.audit import write_audit
from app.services.scanner import Scanner
from app.services.storage import StorageRouter

ADMIN_ACCESS_ACTION = "admin_access"
# Every tenant-scoped role (platform_admin excluded: it has no tenant data access).
TENANT_ROLES: tuple[Role, ...] = tuple(r for r in Role if r is not Role.PLATFORM_ADMIN)


@dataclass(frozen=True, slots=True)
class CurrentUser:
    id: uuid.UUID
    email: str
    tenant_id: uuid.UUID
    role: Role

    @property
    def is_platform_admin(self) -> bool:
        return self.role is Role.PLATFORM_ADMIN


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_auth_limiter(request: Request) -> FixedWindowLimiter:
    limiter: FixedWindowLimiter = request.app.state.auth_limiter
    return limiter


def get_storage_router(request: Request) -> StorageRouter:
    router: StorageRouter = request.app.state.storage_router
    return router


def get_scanner(request: Request) -> Scanner:
    scanner: Scanner = request.app.state.scanner
    return scanner


def get_dispatcher(request: Request) -> Dispatcher:
    """Notification Dispatcher for routes that send mail (M7-15 member invitations).

    Built on first use from the app's settings and cached on app.state so the channels
    (and their per-tenant resolvers) are made once; tests install their own recorder by
    setting `app.state.dispatcher` before the request.
    """
    dispatcher: Dispatcher | None = getattr(request.app.state, "dispatcher", None)
    if dispatcher is None:
        from app.notify.registry import build_dispatcher

        dispatcher = build_dispatcher(request.app.state.settings)
        request.app.state.dispatcher = dispatcher
    return dispatcher


def client_ip(request: Request, settings: Settings) -> str:
    peer = request.client.host if request.client else None
    return client_ip_from_headers(
        peer, request.headers.get("x-forwarded-for"), trust_proxy=settings.trust_proxy_headers
    )


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    request: Request,
    settings: Annotated[Settings, Depends(get_app_settings)],
    limiter: Annotated[FixedWindowLimiter, Depends(get_auth_limiter)],
) -> CurrentUser:
    """Verify the bearer JWT. Tenant and role come from the token only.

    Failed attempts are counted per client IP; more than the configured number per
    minute answers 429 for the rest of the window.
    """
    ip = client_ip(request, settings)
    if limiter.is_limited(ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many failed authentication attempts",
            headers={"Retry-After": str(int(limiter.window_seconds))},
        )
    token = parse_bearer(request.headers.get("authorization"))
    try:
        if token is None:
            raise AuthError("missing bearer token")
        claims = decode_token(token, settings.auth_secret)
    except AuthError as exc:
        if limiter.hit(ip) > limiter.limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="too many failed authentication attempts",
                headers={"Retry-After": str(int(limiter.window_seconds))},
            ) from exc
        raise _unauthorized(exc.detail) from exc
    user = CurrentUser(
        id=claims.sub, email=claims.email, tenant_id=claims.tenant_id, role=claims.role
    )
    request.state.user = user  # read by AuditMiddleware after the handler ran
    # Bind the principal so logs, OTel spans and Sentry events of this request carry it
    # without every call site passing it along (M7-05).
    set_principal(user.tenant_id, user.id)
    return user


def require_role(*roles: Role) -> Callable[..., Awaitable[CurrentUser]]:
    """Allow only the listed roles. platform_admin passes only where it is listed
    (i.e. admin routes); it never implicitly inherits tenant permissions."""
    if not roles:
        raise ValueError("require_role needs at least one role")
    allowed = frozenset(roles)

    async def _dependency(
        user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> CurrentUser:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"role {user.role.value} may not access this resource",
            )
        return user

    return _dependency


async def get_tenant_session(
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> AsyncIterator[AsyncSession]:
    """App-role session scoped (via RLS) to the token's tenant."""
    async with get_database().session(user.tenant_id) as session:
        yield session


async def get_admin_session(
    request: Request,
    settings: Annotated[Settings, Depends(get_app_settings)],
    user: Annotated[CurrentUser, Depends(require_role(Role.PLATFORM_ADMIN))],
) -> AsyncIterator[AsyncSession]:
    """Owner-role session for platform-admin routes. Every use is audited in the admin's
    own tenant (action admin_access, object = route path)."""
    db = get_database()
    async with db.owner_session(user.tenant_id) as audit_session:
        await write_audit(
            audit_session,
            tenant_id=user.tenant_id,
            user_id=user.id,
            action=ADMIN_ACCESS_ACTION,
            object_type="route",
            object_id=request.url.path,
            ip=client_ip(request, settings),
            meta={"method": request.method},
        )
    async with db.owner_session(user.tenant_id) as session:
        yield session


async def get_admin_write_session(
    user: Annotated[CurrentUser, Depends(require_role(Role.PLATFORM_ADMIN))],
) -> AsyncIterator[AsyncSession]:
    """Owner-role session for MUTATING platform-admin routes.

    Unlike get_admin_session it writes no admin_access row: the audit middleware already
    records exactly one row per mutating request (and tests/integration/test_audit.py
    asserts exactly one), so a second row here would double-count every admin write.
    """
    async with get_database().owner_session(user.tenant_id) as session:
        yield session


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]
DispatcherDep = Annotated[Dispatcher, Depends(get_dispatcher)]
StorageRouterDep = Annotated[StorageRouter, Depends(get_storage_router)]
ScannerDep = Annotated[Scanner, Depends(get_scanner)]
TenantSessionDep = Annotated[AsyncSession, Depends(get_tenant_session)]
AdminSessionDep = Annotated[AsyncSession, Depends(get_admin_session)]
AdminWriteSessionDep = Annotated[AsyncSession, Depends(get_admin_write_session)]
