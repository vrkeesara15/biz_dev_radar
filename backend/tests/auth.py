"""Token helpers for tests."""

from __future__ import annotations

import uuid

from app.core.auth import encode_token
from app.core.roles import Role

TEST_SECRET = "dev-only-change-me-0123456789abcdef0123456789abcdef"


def token_for(
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    role: Role | str = Role.TENANT_OWNER,
    email: str | None = None,
    secret: str = TEST_SECRET,
    expires_in: int = 3600,
    now: float | None = None,
) -> str:
    return encode_token(
        user_id=user_id,
        email=email or f"{user_id.hex[:8]}@example.com",
        tenant_id=tenant_id,
        role=role,
        secret=secret,
        expires_in=expires_in,
        now=now,
    )


def auth_headers(**kwargs) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {"Authorization": f"Bearer {token_for(**kwargs)}"}
