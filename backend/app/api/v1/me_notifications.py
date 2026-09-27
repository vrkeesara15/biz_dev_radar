"""In-app bell and web push subscriptions (SPEC 7, 10.4).

GET    /api/v1/me/notifications?unread=1&limit=50   the bell list + unread count
POST   /api/v1/me/notifications/{id}/read           mark one read (idempotent)
POST   /api/v1/me/notifications/read-all            clear the badge
GET    /api/v1/me/push-config                      the deployment's VAPID public key
POST   /api/v1/me/push-subscriptions                store this browser's subscription
DELETE /api/v1/me/push-subscriptions                drop it again
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import CurrentUserDep, SettingsDep, TenantSessionDep
from app.models import Notification
from app.notify.in_app import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    list_notifications,
    mark_all_read,
    mark_read,
)
from app.notify.push import Subscription, delete_push_subscription, save_push_subscription
from app.services.audit import AuditHint

router = APIRouter(prefix="/me", tags=["me"])

# payload keys that are plumbing rather than content for the bell item
_HIDDEN = frozenset({"recipient"})


class NotificationOut(BaseModel):
    id: uuid.UUID
    event_type: str
    opportunity_id: uuid.UUID | None
    pursuit_id: uuid.UUID | None
    version: int
    payload: dict[str, Any]
    created_at: datetime
    read_at: datetime | None


class NotificationPage(BaseModel):
    items: list[NotificationOut]
    unread: int


class MarkReadOut(BaseModel):
    id: uuid.UUID
    read_at: datetime


class MarkAllReadOut(BaseModel):
    marked: int


class PushKeys(BaseModel):
    model_config = ConfigDict(extra="ignore")

    p256dh: Annotated[str, Field(min_length=1, max_length=255)]
    auth: Annotated[str, Field(min_length=1, max_length=255)]


class PushSubscriptionIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    endpoint: Annotated[str, Field(min_length=1, max_length=2048)]
    keys: PushKeys


class PushConfigOut(BaseModel):
    vapid_public_key: str


class PushSubscriptionOut(BaseModel):
    id: uuid.UUID
    endpoint: str
    created: bool


class PushDeleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: Annotated[str, Field(min_length=1, max_length=2048)]


def _out(row: Notification) -> NotificationOut:
    return NotificationOut(
        id=row.id,
        event_type=row.event_type,
        opportunity_id=row.opportunity_id,
        pursuit_id=row.pursuit_id,
        version=row.version,
        payload={k: v for k, v in (row.payload or {}).items() if k not in _HIDDEN},
        created_at=row.created_at,
        read_at=row.read_at,
    )


@router.get("/notifications", response_model=NotificationPage)
async def read_notifications(
    user: CurrentUserDep,
    session: TenantSessionDep,
    unread: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    before: Annotated[datetime | None, Query()] = None,
) -> NotificationPage:
    """Newest first, with the unread count for the badge."""
    rows, unread_count = await list_notifications(
        session, user.id, unread_only=unread, limit=limit, before=before
    )
    return NotificationPage(items=[_out(row) for row in rows], unread=unread_count)


@router.post("/notifications/read-all", response_model=MarkAllReadOut)
async def read_all(
    user: CurrentUserDep, session: TenantSessionDep, request: Request
) -> MarkAllReadOut:
    marked = await mark_all_read(session, user.id)
    request.state.audit = AuditHint(
        action="notification.read_all",
        object_type="user",
        object_id=str(user.id),
        meta={"marked": marked},
    )
    return MarkAllReadOut(marked=marked)


@router.post("/notifications/{notification_id}/read", response_model=MarkReadOut)
async def read_one(
    notification_id: uuid.UUID,
    user: CurrentUserDep,
    session: TenantSessionDep,
    request: Request,
) -> MarkReadOut:
    try:
        row = await mark_read(session, notification_id, user.id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="notification not found") from exc
    request.state.audit = AuditHint(
        action="notification.read", object_type="notification", object_id=str(row.id)
    )
    if row.read_at is None:  # pragma: no cover - mark_read always sets it
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="read_at not set")
    return MarkReadOut(id=row.id, read_at=row.read_at)


@router.get("/push-config", response_model=PushConfigOut)
async def push_config(user: CurrentUserDep, settings: SettingsDep) -> PushConfigOut:
    """The VAPID public key the browser needs for `pushManager.subscribe` (RFC 8292).

    Read at runtime so one image serves every environment; 404 means this deployment has
    no key configured and the web-push toggle stays off (PROGRESS.md OQ-88).
    """
    key = settings.vapid_public_key.strip()
    if not key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="web push is not configured")
    return PushConfigOut(vapid_public_key=key)


@router.post(
    "/push-subscriptions",
    response_model=PushSubscriptionOut,
    status_code=status.HTTP_201_CREATED,
)
async def subscribe_push(
    body: PushSubscriptionIn,
    user: CurrentUserDep,
    session: TenantSessionDep,
    request: Request,
) -> PushSubscriptionOut:
    """Store (or refresh) this browser's push subscription; the endpoint is the identity."""
    row = await save_push_subscription(
        session,
        tenant_id=user.tenant_id,
        user_id=user.id,
        subscription=Subscription(body.endpoint, body.keys.p256dh, body.keys.auth),
        user_agent=request.headers.get("user-agent"),
    )
    request.state.audit = AuditHint(
        action="push_subscription.save", object_type="push_subscription", object_id=str(row.id)
    )
    return PushSubscriptionOut(id=row.id, endpoint=row.endpoint, created=True)


@router.delete("/push-subscriptions", status_code=status.HTTP_204_NO_CONTENT)
async def unsubscribe_push(
    body: PushDeleteIn,
    user: CurrentUserDep,
    session: TenantSessionDep,
    request: Request,
) -> None:
    removed = await delete_push_subscription(session, user.id, body.endpoint)
    request.state.audit = AuditHint(
        action="push_subscription.delete",
        object_type="push_subscription",
        meta={"removed": removed},
    )
    if not removed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="subscription not found")
