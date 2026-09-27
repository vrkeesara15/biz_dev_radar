"""Request-scoped context (contextvars). Pure: no I/O, no framework imports."""

from __future__ import annotations

import uuid
from contextvars import ContextVar

REQUEST_ID_HEADER = "X-Request-ID"
_MAX_REQUEST_ID_LEN = 128

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def new_request_id() -> str:
    return uuid.uuid4().hex


def sanitize_request_id(value: str | None) -> str | None:
    """Accept a caller-supplied id only if it is short and printable ASCII."""
    if not value:
        return None
    value = value.strip()
    if not value or len(value) > _MAX_REQUEST_ID_LEN:
        return None
    if not all(32 < ord(ch) < 127 for ch in value):
        return None
    return value


def set_request_id(value: str | None) -> str:
    rid = sanitize_request_id(value) or new_request_id()
    _request_id.set(rid)
    return rid


def get_request_id() -> str | None:
    return _request_id.get()


def clear_request_id() -> None:
    _request_id.set(None)
