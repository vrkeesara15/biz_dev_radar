"""Notification one-click actions and unsubscribe (SPEC 7).

    GET  /api/v1/notifications/actions/{token}        Pursue / Watch / Pass / Assign
    GET  /api/v1/notifications/unsubscribe/{token}    CAN-SPAM link in the email footer
    POST /api/v1/notifications/unsubscribe/{token}    RFC 8058 List-Unsubscribe=One-Click


The link is clicked from an email / Slack / Teams message, so there is no bearer token: the
signed action token (app.notify.actions) authenticates the click and scopes the tenant, user
and notification. The action intent is recorded on the notification (M6 turns it into the
pursuit transition) and the caller is redirected to the opportunity page.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel

from app.api.deps import SettingsDep, client_ip
from app.core.db import get_database
from app.notify.actions import (
    ActionTokenError,
    NotificationAction,
    deep_link,
    record_action,
    verify_action_token,
)
from app.notify.unsubscribe import (
    UnsubscribeTokenError,
    apply_unsubscribe,
    verify_unsubscribe_token,
)
from app.services.audit import write_audit

router = APIRouter(prefix="/notifications", tags=["notifications"])

ACTION_AUDIT = "notification.action"
UNSUBSCRIBE_AUDIT = "notification.unsubscribe"


class ActionOut(BaseModel):
    action: NotificationAction
    notification_id: uuid.UUID
    opportunity_id: uuid.UUID | None
    pursuit_id: uuid.UUID | None
    recorded: bool
    redirect: str


@router.get("/actions/{token}", response_model=ActionOut, status_code=status.HTTP_202_ACCEPTED)
async def take_action(
    token: str,
    request: Request,
    settings: SettingsDep,
    reason: Annotated[str | None, Query(max_length=500)] = None,
    assignee: Annotated[str | None, Query(max_length=320)] = None,
) -> ActionOut:
    """Record a Pursue / Watch / Pass (with reason) / Assign click and answer with the
    redirect target (202: the pursuit itself is created by the M6 endpoints)."""
    try:
        claims = verify_action_token(token, settings.auth_secret)
    except ActionTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=exc.detail) from exc
    async with get_database().session(claims.tenant_id) as session:
        try:
            row = await record_action(session, claims, reason=reason, assignee=assignee)
        except LookupError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="notification not found") from exc
        await write_audit(
            session,
            tenant_id=claims.tenant_id,
            user_id=claims.user_id,
            action=ACTION_AUDIT,
            object_type="notification",
            object_id=row.id,
            ip=client_ip(request, settings),
            meta={"action": claims.action.value, "reason": reason, "assignee": assignee},
        )
    return ActionOut(
        action=claims.action,
        notification_id=claims.notification_id,
        opportunity_id=claims.opportunity_id,
        pursuit_id=claims.pursuit_id,
        recorded=True,
        redirect=deep_link(settings, claims.opportunity_id, claims.pursuit_id),
    )


class UnsubscribeOut(BaseModel):
    category: str
    unsubscribed_categories: list[str]
    redirect: str


async def _unsubscribe(token: str, request: Request, settings: SettingsDep) -> UnsubscribeOut:
    try:
        claims = verify_unsubscribe_token(token, settings.auth_secret)
    except UnsubscribeTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=exc.detail) from exc
    async with get_database().session(claims.tenant_id) as session:
        categories = await apply_unsubscribe(session, claims)
        await write_audit(
            session,
            tenant_id=claims.tenant_id,
            user_id=claims.user_id,
            action=UNSUBSCRIBE_AUDIT,
            object_type="user_notification_prefs",
            object_id=claims.user_id,
            ip=client_ip(request, settings),
            meta={"category": claims.category},
        )
    return UnsubscribeOut(
        category=claims.category,
        unsubscribed_categories=list(categories),
        redirect=settings.app_base_url.rstrip("/") + "/app/settings/notifications",
    )


@router.get("/unsubscribe/{token}", response_model=UnsubscribeOut)
async def unsubscribe(token: str, request: Request, settings: SettingsDep) -> UnsubscribeOut:
    """CAN-SPAM per-category opt-out clicked from the email footer."""
    return await _unsubscribe(token, request, settings)


@router.post("/unsubscribe/{token}", response_model=UnsubscribeOut)
async def unsubscribe_one_click(
    token: str, request: Request, settings: SettingsDep
) -> UnsubscribeOut:
    """RFC 8058 one-click target named by the List-Unsubscribe-Post header."""
    return await _unsubscribe(token, request, settings)
