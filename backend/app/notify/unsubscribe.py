"""Per-category unsubscribe (SPEC 7, CAN-SPAM): signed links and the prefs they flip.

    url = unsubscribe_url(settings, tenant_id=..., user_id=..., category="high_fit_match")
    claims = verify_unsubscribe_token(token, settings.auth_secret)   # or raises
    categories = await apply_unsubscribe(session, claims)            # new opt-out list
    skipped = is_unsubscribed("high_fit_match", categories)

Every commercial email carries a link for its own category plus one for `all`, and the
matching `List-Unsubscribe` / `List-Unsubscribe-Post` headers (RFC 2369 / RFC 8058) so
mail clients can do it in one click without a confirmation page. The token is the same
HS256 scheme as the one-click actions (app.notify.actions): tenant + user + category,
expiring after NOTIFY_ACTION_TTL_SECONDS.

Opting out silences the EMAIL channel only: the in-app bell and Slack/Teams keep the
record, which is what "unsubscribe from this kind of email" means.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import jwt
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import ALGORITHM, AuthError
from app.core.config import Settings
from app.core.preferences import NotificationEvent
from app.models import UserNotificationPrefs

UNSUBSCRIBE_PATH = "/api/v1/notifications/unsubscribe/{token}"
TOKEN_KIND = "notify_unsubscribe"
ALL = "all"

# Emails that address one recipient but are not one of the user's preference events:
# ops alerts and the member invitation (M7-15). Their footer link must verify like any
# other, so they are categories too.
EXTRA_CATEGORIES: frozenset[str] = frozenset({"adapter_failing", "member.invited"})

CATEGORIES: frozenset[str] = frozenset(
    {ALL, *EXTRA_CATEGORIES, *(event.value for event in NotificationEvent)}
)


class UnsubscribeClaims(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    kind: str
    category: str
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    exp: int


class UnsubscribeTokenError(AuthError):
    detail = "invalid or expired unsubscribe link"


def sign_unsubscribe_token(
    settings: Settings,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    category: str,
    now: datetime | None = None,
) -> str:
    issued = int((now or datetime.now(UTC)).timestamp())
    payload: dict[str, Any] = {
        "kind": TOKEN_KIND,
        "category": category,
        "tenant_id": str(tenant_id),
        "user_id": str(user_id),
        "iat": issued,
        "exp": issued + int(settings.notify_action_ttl_seconds),
    }
    return jwt.encode(payload, settings.auth_secret, algorithm=ALGORITHM)


def verify_unsubscribe_token(
    token: str, secret: str, *, now: datetime | None = None
) -> UnsubscribeClaims:
    """Signature + expiry (against `now`) + a known category. Raises UnsubscribeTokenError."""
    if not token or not secret:
        raise UnsubscribeTokenError()
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            options={
                "require": ["exp", "kind", "category", "user_id"],
                "verify_exp": False,
                "verify_iat": False,
            },
        )
    except jwt.InvalidTokenError as exc:
        raise UnsubscribeTokenError() from exc
    if payload.get("kind") != TOKEN_KIND or payload.get("category") not in CATEGORIES:
        raise UnsubscribeTokenError()
    moment = (now or datetime.now(UTC)).timestamp()
    try:
        expired = int(payload["exp"]) <= moment
    except (TypeError, ValueError) as exc:
        raise UnsubscribeTokenError() from exc
    if expired:
        raise UnsubscribeTokenError()
    try:
        return UnsubscribeClaims.model_validate(payload)
    except ValidationError as exc:
        raise UnsubscribeTokenError() from exc


def unsubscribe_url(
    settings: Settings,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    category: str,
    now: datetime | None = None,
) -> str:
    token = sign_unsubscribe_token(
        settings, tenant_id=tenant_id, user_id=user_id, category=category, now=now
    )
    return settings.api_base_url.rstrip("/") + UNSUBSCRIBE_PATH.format(token=token)


def is_unsubscribed(category: str, categories: object) -> bool:
    """True when the event's own category or `all` is in the user's opt-out list."""
    if not isinstance(categories, list | tuple | set | frozenset):
        return False
    chosen = {str(c) for c in categories}
    return ALL in chosen or category in chosen


async def _prefs_for(session: AsyncSession, user_id: uuid.UUID) -> UserNotificationPrefs | None:
    return (
        await session.execute(
            select(UserNotificationPrefs).where(UserNotificationPrefs.user_id == user_id)
        )
    ).scalar_one_or_none()


async def load_unsubscribed(session: AsyncSession, user_id: uuid.UUID) -> tuple[str, ...]:
    """The user's opt-out categories (empty when they have no prefs row yet)."""
    row = await _prefs_for(session, user_id)
    return tuple(row.unsubscribed_categories) if row is not None else ()


async def apply_unsubscribe(
    session: AsyncSession, claims: UnsubscribeClaims, *, tenant_id: uuid.UUID | None = None
) -> tuple[str, ...]:
    """Add the token's category to the user's opt-out list (creating the prefs row when the
    user has never saved one) and return the new list. Idempotent; `all` replaces the rest."""
    row = await _prefs_for(session, claims.user_id)
    if row is None:
        row = UserNotificationPrefs(
            tenant_id=tenant_id or claims.tenant_id,
            user_id=claims.user_id,
            channels_by_event={},
            unsubscribed_categories=[],
        )
        session.add(row)
    current = list(row.unsubscribed_categories or [])
    if claims.category == ALL:
        current = [ALL]
    elif claims.category not in current and ALL not in current:
        current.append(claims.category)
    row.unsubscribed_categories = current
    await session.flush()
    return tuple(current)


async def resubscribe(session: AsyncSession, user_id: uuid.UUID, category: str) -> tuple[str, ...]:
    """Undo one opt-out (Settings > Notifications); `all` clears the whole list."""
    row = await _prefs_for(session, user_id)
    if row is None:
        return ()
    current = (
        [] if category == ALL else [c for c in row.unsubscribed_categories or [] if c != category]
    )
    row.unsubscribed_categories = current
    await session.flush()
    return tuple(current)
