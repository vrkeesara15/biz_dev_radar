"""API rate limiting (SPEC 11), per tenant and per client IP.

Pure ASGI, outside the routers, so a flood is refused before it reaches a database
session. Two buckets are consulted on every request:

* **per tenant** — read from the bearer token, so one tenant cannot starve the others.
  The token is verified with the same `decode_token` the auth dependency uses; an absent
  or bad token simply means no tenant bucket, and the request is answered 401 downstream.
* **per client IP** — the only bucket an unauthenticated caller has.

Both are checked and the *stricter* rejection wins. Every response carries
`X-RateLimit-Limit`, `X-RateLimit-Remaining` and `X-RateLimit-Reset` for the bucket that
is closest to empty, so a client can pace itself before it is refused; a rejection is
`429` with `Retry-After`.

The existing failed-auth limiter in `get_current_user` is untouched: it counts *failed
credentials*, a different thing from request volume, and it stays per-instance (OQ-17).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from app.core.auth import AuthError, decode_token, parse_bearer
from app.core.ratelimit import RateLimitDecision, RateLimitPolicy, client_ip_from_headers
from app.services.ratelimit import RateLimiter

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

TOO_MANY_REQUESTS = 429
DETAIL = "rate limit exceeded"


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return str(value.decode("latin-1"))
    return None


def tenant_from_scope(scope: Scope, secret: str) -> str | None:
    """Tenant id from a valid bearer token, or None. Never raises."""
    token = parse_bearer(_header(scope, b"authorization"))
    if not token:
        return None
    try:
        return str(decode_token(token, secret).tenant_id)
    except (AuthError, ValueError):
        return None


def tightest(decisions: list[RateLimitDecision]) -> RateLimitDecision:
    """The decision a client should act on: any rejection first, else the emptiest bucket."""
    rejected = [d for d in decisions if not d.allowed]
    if rejected:
        return max(rejected, key=lambda d: d.retry_after)
    return min(decisions, key=lambda d: d.remaining)


class RateLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        limiter: RateLimiter,
        tenant_policy: RateLimitPolicy,
        ip_policy: RateLimitPolicy,
        auth_secret: str,
        exempt_paths: tuple[str, ...] = ("/healthz",),
        trust_proxy: bool = False,
    ) -> None:
        self.app = app
        self.limiter = limiter
        self.tenant_policy = tenant_policy
        self.ip_policy = ip_policy
        self.auth_secret = auth_secret
        self.exempt_paths = tuple(exempt_paths)
        self.trust_proxy = trust_proxy

    def is_exempt(self, path: str) -> bool:
        return any(path == prefix or path.startswith(f"{prefix}/") for prefix in self.exempt_paths)

    def client_ip(self, scope: Scope) -> str:
        client = scope.get("client")
        peer = client[0] if client else None
        return client_ip_from_headers(
            peer, _header(scope, b"x-forwarded-for"), trust_proxy=self.trust_proxy
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self.is_exempt(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        decisions = [await self.limiter.check(f"ip:{self.client_ip(scope)}", self.ip_policy)]
        tenant_id = tenant_from_scope(scope, self.auth_secret)
        if tenant_id:
            decisions.append(await self.limiter.check(f"tenant:{tenant_id}", self.tenant_policy))

        decision = tightest(decisions)
        if not decision.allowed:
            await self._reject(decision, send)
            return

        headers = decision.headers()

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                existing = list(message.get("headers", []))
                lowered = {name for name, _ in existing}
                for name, value in headers.items():
                    key = name.lower().encode("latin-1")
                    if key not in lowered:
                        existing.append((key, value.encode("latin-1")))
                message["headers"] = existing
            await send(message)

        await self.app(scope, receive, send_with_headers)

    async def _reject(self, decision: RateLimitDecision, send: Send) -> None:
        body = json.dumps({"detail": DETAIL, "retry_after": decision.retry_after}).encode()
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("latin-1")),
        ]
        headers += [
            (name.lower().encode("latin-1"), value.encode("latin-1"))
            for name, value in decision.headers().items()
        ]
        await send({"type": "http.response.start", "status": TOO_MANY_REQUESTS, "headers": headers})
        await send({"type": "http.response.body", "body": body})
