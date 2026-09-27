"""Pure-ASGI middleware. Kept framework-light so it works for HTTP and websockets."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from app.core.context import REQUEST_ID_HEADER, clear_request_id, set_request_id

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

_HEADER_KEY = REQUEST_ID_HEADER.lower().encode()


class RequestIdMiddleware:
    """Attach X-Request-ID to every response and expose it via app.core.context."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        incoming = None
        for key, value in scope.get("headers", []):
            if key == _HEADER_KEY:
                incoming = value.decode("latin-1")
                break
        request_id = set_request_id(incoming)
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_header(message: Message) -> None:
            if message["type"] in ("http.response.start", "websocket.accept"):
                headers = list(message.get("headers", []))
                headers = [(k, v) for k, v in headers if k != _HEADER_KEY]
                headers.append((_HEADER_KEY, request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            clear_request_id()
