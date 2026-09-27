"""One-click notification actions (SPEC 7): signed URLs for Pursue / Watch / Pass / Assign.

    links = action_links(settings, tenant_id=..., user_id=..., notification_id=...,
                         opportunity_id=..., now=...)
    # {"pursue": "https://api/.../notifications/actions/<token>", "watch": ..., ...}
    claims = verify_action_token(token, settings.auth_secret)      # ActionClaims or raises
    await record_action(session, claims, reason=..., now=...)      # appends payload.actions_taken

Tokens are HS256 JWTs signed with AUTH_SECRET (the same secret Auth.js shares), scoped to one
tenant, user, notification and action, and expire after NOTIFY_ACTION_TTL_SECONDS. Slack and
Teams buttons carry the same tokens, so every channel funnels into `record_action`. The
pursuit endpoints themselves arrive with M6: until then the intent is stored on the
notification (payload.actions_taken) and the caller is redirected to the opportunity page.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import jwt
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.auth import ALGORITHM, AuthError
from app.core.config import Settings
from app.models import Notification

ACTION_PATH = "/api/v1/notifications/actions/{token}"
TOKEN_KIND = "notify_action"


class NotificationAction(StrEnum):
    PURSUE = "pursue"
    WATCH = "watch"
    PASS = "pass"
    ASSIGN = "assign"


ACTIONS: tuple[NotificationAction, ...] = tuple(NotificationAction)


class ActionClaims(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    kind: str
    action: NotificationAction
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    notification_id: uuid.UUID
    opportunity_id: uuid.UUID | None = None
    pursuit_id: uuid.UUID | None = None
    exp: int


class ActionTokenError(AuthError):
    detail = "invalid or expired action link"


def sign_action_token(
    settings: Settings,
    *,
    action: NotificationAction | str,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    notification_id: uuid.UUID,
    opportunity_id: uuid.UUID | None = None,
    pursuit_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> str:
    issued = int((now or datetime.now(UTC)).timestamp())
    payload: dict[str, Any] = {
        "kind": TOKEN_KIND,
        "action": NotificationAction(action).value,
        "tenant_id": str(tenant_id),
        "user_id": str(user_id),
        "notification_id": str(notification_id),
        "opportunity_id": None if opportunity_id is None else str(opportunity_id),
        "pursuit_id": None if pursuit_id is None else str(pursuit_id),
        "iat": issued,
        "exp": issued + int(settings.notify_action_ttl_seconds),
    }
    return jwt.encode(payload, settings.auth_secret, algorithm=ALGORITHM)


def verify_action_token(token: str, secret: str, *, now: datetime | None = None) -> ActionClaims:
    """Signature + expiry (against `now`, default wall clock) + shape. Raises
    ActionTokenError (an AuthError) otherwise."""
    if not token or not secret:
        raise ActionTokenError()
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            # expiry is checked below against `now` (injectable); iat is informational
            options={
                "require": ["exp", "kind", "action", "notification_id"],
                "verify_exp": False,
                "verify_iat": False,
            },
        )
    except jwt.InvalidTokenError as exc:
        raise ActionTokenError() from exc
    if payload.get("kind") != TOKEN_KIND:
        raise ActionTokenError()
    moment = (now or datetime.now(UTC)).timestamp()
    try:
        expired = int(payload["exp"]) <= moment
    except (TypeError, ValueError) as exc:
        raise ActionTokenError() from exc
    if expired:
        raise ActionTokenError()
    try:
        return ActionClaims.model_validate(payload)
    except ValidationError as exc:
        raise ActionTokenError() from exc


def action_url(settings: Settings, token: str) -> str:
    return settings.api_base_url.rstrip("/") + ACTION_PATH.format(token=token)


def deep_link(
    settings: Settings, opportunity_id: uuid.UUID | None, pursuit_id: uuid.UUID | None
) -> str:
    base = settings.app_base_url.rstrip("/")
    if pursuit_id is not None:
        return f"{base}/app/pursuits/{pursuit_id}"
    if opportunity_id is not None:
        return f"{base}/app/opportunities/{opportunity_id}"
    return f"{base}/app"


def action_links(
    settings: Settings,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    notification_id: uuid.UUID,
    opportunity_id: uuid.UUID | None,
    pursuit_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> dict[str, str]:
    """Signed URL per one-click action (SPEC 7); Pass takes `?reason=` on the click."""
    return {
        action.value: action_url(
            settings,
            sign_action_token(
                settings,
                action=action,
                tenant_id=tenant_id,
                user_id=user_id,
                notification_id=notification_id,
                opportunity_id=opportunity_id,
                pursuit_id=pursuit_id,
                now=now,
            ),
        )
        for action in ACTIONS
    }


async def record_action(
    session: AsyncSession,
    claims: ActionClaims,
    *,
    reason: str | None = None,
    assignee: str | None = None,
    source: str = "link",
    now: datetime | None = None,
) -> Notification:
    """Append the action intent to notifications.payload.actions_taken (idempotent per
    action+source: clicking the same button twice records once) and return the row.
    Raises LookupError when the notification is not visible in the token's tenant."""
    row = await session.get(Notification, claims.notification_id)
    if row is None or row.user_id != claims.user_id:
        raise LookupError("notification not found")
    taken: list[dict[str, Any]] = list(row.payload.get("actions_taken") or [])
    entry: dict[str, Any] = {
        "action": claims.action.value,
        "at": (now or datetime.now(UTC)).isoformat(),
        "source": source,
    }
    if reason:
        entry["reason"] = reason.strip()[:500]
    if assignee:
        entry["assignee"] = assignee.strip()[:320]
    if not any(t.get("action") == entry["action"] and t.get("source") == source for t in taken):
        taken.append(entry)
        row.payload = {**row.payload, "actions_taken": taken}
        flag_modified(row, "payload")
        await session.flush()
    return row
