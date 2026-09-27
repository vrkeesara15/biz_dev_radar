"""Auth.js-compatible HS256 JWT claims. Pure logic (no I/O).

The front end (Auth.js) mints an HS256 JWT with the shared AUTH_SECRET carrying
sub (user id), email, tenant_id, role, exp. The API never trusts a tenant id from
anywhere else.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import jwt
from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.roles import Role

ALGORITHM = "HS256"
REQUIRED_CLAIMS = ("sub", "email", "tenant_id", "role", "exp")
DEFAULT_TTL_SECONDS = 3600
CLOCK_LEEWAY_SECONDS = 10


class AuthError(Exception):
    """Base class; maps to HTTP 401."""

    detail = "authentication failed"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or self.detail)
        self.detail = detail or self.detail


class InvalidTokenError(AuthError):
    detail = "invalid token"


class ExpiredTokenError(AuthError):
    detail = "token expired"


class AuthClaims(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    sub: uuid.UUID
    email: str
    tenant_id: uuid.UUID
    role: Role
    exp: int
    iat: int | None = None


def encode_token(
    *,
    user_id: uuid.UUID,
    email: str,
    tenant_id: uuid.UUID,
    role: Role | str,
    secret: str,
    expires_in: int = DEFAULT_TTL_SECONDS,
    now: float | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    """Mint a token the API accepts (used by tests and local tooling; Auth.js does this in prod)."""
    issued = int(now if now is not None else time.time())
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "email": email,
        "tenant_id": str(tenant_id),
        "role": Role(role).value,
        "iat": issued,
        "exp": issued + int(expires_in),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def decode_token(token: str, secret: str) -> AuthClaims:
    """Verify signature + expiry and validate the claim shape. Raises AuthError subclasses."""
    if not token or not secret:
        raise InvalidTokenError()
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            leeway=CLOCK_LEEWAY_SECONDS,
            options={"require": list(REQUIRED_CLAIMS)},
        )
    except jwt.ExpiredSignatureError as exc:
        raise ExpiredTokenError() from exc
    except jwt.InvalidTokenError as exc:
        raise InvalidTokenError() from exc
    try:
        return AuthClaims.model_validate(payload)
    except ValidationError as exc:
        raise InvalidTokenError("malformed claims") from exc


def parse_bearer(header_value: str | None) -> str | None:
    """Extract the token from an Authorization header; None if absent or not Bearer."""
    if not header_value:
        return None
    scheme, _, token = header_value.strip().partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()
