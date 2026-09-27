"""Audit every mutating request under /api/v1 (SPEC section 11).

Pure ASGI. After the handler has produced its response status, if
`get_current_user` resolved a user (request.state.user), one audit_log row is
written in that user's tenant with action, object, IP, request id and status.
Handlers refine the action/object by setting `request.state.audit = AuditHint(...)`.
Unauthenticated requests have no tenant and are not recorded here (they are
already rate-limited and answered 401).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from app.api.deps import CurrentUser
from app.api.v1 import API_PREFIX
from app.core.context import get_request_id
from app.core.db import get_database
from app.core.ratelimit import client_ip_from_headers
from app.logging import get_logger
from app.models import AuditLog
from app.services.audit import AuditHint

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
log = get_logger(__name__)


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return str(value.decode("latin-1"))
    return None


def route_template(path: str, path_params: dict[str, Any]) -> str:
    """Re-insert {param} placeholders so audit actions group by route, not by id."""
    template = path
    for name, value in path_params.items():
        template = template.replace(f"/{value}", f"/{{{name}}}", 1)
    return template


def default_hint(scope: Scope) -> AuditHint:
    """Action = '<method> <route template>'; object = the route's single path param."""
    path_params: dict[str, Any] = scope.get("path_params", {}) or {}
    template = route_template(str(scope.get("path", "")), path_params)
    object_id = None
    object_type = None
    if path_params:
        key, value = next(iter(path_params.items()))
        object_id = str(value)
        object_type = key[:-3] if key.endswith("_id") else key
    return AuditHint(
        action=f"{scope['method'].lower()} {template}", object_type=object_type, object_id=object_id
    )


class AuditMiddleware:
    def __init__(self, app: ASGIApp, *, prefix: str = API_PREFIX, trust_proxy: bool = False):
        self.app = app
        self.prefix = prefix
        self.trust_proxy = trust_proxy

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") not in MUTATING_METHODS
            or not str(scope.get("path", "")).startswith(self.prefix)
        ):
            await self.app(scope, receive, send)
            return

        status: dict[str, int] = {}

        async def send_capture(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_capture)
        finally:
            await self._record(scope, status.get("code"))

    async def _record(self, scope: Scope, status_code: int | None) -> None:
        state = scope.get("state") or {}
        user = state.get("user")
        if not isinstance(user, CurrentUser):
            return
        hint = state.get("audit")
        if not isinstance(hint, AuditHint):
            hint = default_hint(scope)
        client = scope.get("client")
        ip = client_ip_from_headers(
            client[0] if client else None,
            _header(scope, b"x-forwarded-for"),
            trust_proxy=self.trust_proxy,
        )
        meta: dict[str, Any] = {
            "method": scope["method"],
            "path": scope.get("path"),
            "status": status_code,
        }
        if hint.meta:
            meta.update(hint.meta)
        row = AuditLog(
            tenant_id=user.tenant_id,
            user_id=user.id,
            action=hint.action,
            object_type=hint.object_type,
            object_id=hint.object_id,
            ip=ip,
            request_id=get_request_id(),
            meta=meta,
        )
        try:
            async with get_database().session(user.tenant_id) as session:
                session.add(row)
        except Exception:  # never turn an audit failure into a 500 after the response
            log.exception("audit_write_failed", action=hint.action, tenant_id=str(user.tenant_id))
