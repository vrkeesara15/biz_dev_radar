"""Web push (SPEC 7): VAPID-signed messages to the browser subscriptions we hold.

    channel = PushChannel(settings, subscriptions=push_resolver(database))

A browser subscription is {endpoint, keys.p256dh, keys.auth}; it is stored per user in
`push_subscriptions` (tenant-scoped) by POST /api/v1/me/push-subscriptions. Delivery goes
through pywebpush, which does the RFC 8291 payload encryption and the RFC 8292 VAPID
signature from VAPID_PRIVATE_KEY / VAPID_SUBJECT.

A push endpoint that answers 404 or 410 is permanently gone (the browser dropped the
subscription); the sender reports it as `gone` and the caller prunes the row. Anything
else is a normal failure, so the Dispatcher retries and then falls back to email.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models import Notification, PushSubscription
from app.notify.core import SendResult

log = structlog.get_logger(__name__)

PUSH = "push"
GONE_STATUSES = frozenset({404, 410})
MAX_PAYLOAD_BYTES = 3800  # browsers guarantee 4 KB after encryption


@dataclass(frozen=True, slots=True)
class Subscription:
    endpoint: str
    p256dh: str
    auth: str
    id: uuid.UUID | None = None

    def as_info(self) -> dict[str, Any]:
        return {"endpoint": self.endpoint, "keys": {"p256dh": self.p256dh, "auth": self.auth}}


@dataclass(frozen=True, slots=True)
class PushResult:
    ok: bool
    gone: bool = False
    error: str | None = None


class PushSender(Protocol):
    async def send(self, subscription: Subscription, payload: str) -> PushResult: ...


# (tenant_id, user_id) -> that user's live subscriptions; the tenant is needed for RLS.
SubscriptionResolver = Callable[[uuid.UUID, uuid.UUID], Awaitable[Sequence[Subscription]]]
GoneHandler = Callable[[uuid.UUID, Subscription], Awaitable[None]]


class WebPushSender:
    """pywebpush in a worker thread (it is a blocking requests-based client)."""

    def __init__(self, settings: Settings, *, timeout: float = 10.0) -> None:
        self.private_key = settings.vapid_private_key
        self.subject = settings.vapid_subject
        self.timeout = timeout

    def _send_sync(self, subscription: Subscription, payload: str) -> PushResult:
        from pywebpush import WebPushException, webpush

        try:
            webpush(
                subscription_info=subscription.as_info(),
                data=payload,
                vapid_private_key=self.private_key,
                vapid_claims={"sub": self.subject},
                timeout=self.timeout,
            )
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            return PushResult(False, gone=status in GONE_STATUSES, error=f"push: {exc}")
        except Exception as exc:  # a transport error is a normal, retryable failure
            return PushResult(False, error=f"push: {type(exc).__name__}: {exc}")
        return PushResult(True)

    async def send(self, subscription: Subscription, payload: str) -> PushResult:
        if not self.private_key:
            return PushResult(False, error="VAPID_PRIVATE_KEY is not configured")
        return await asyncio.to_thread(self._send_sync, subscription, payload)


def build_payload(notification: Notification) -> str:
    """What the service worker receives: title, body, the deep link and the bell id."""
    payload = dict(notification.payload or {})
    title = str(payload.get("title") or notification.event_type.replace("_", " ").title())
    parts = [str(payload.get("buyer") or payload.get("buyer_org") or "")]
    if payload.get("score") is not None:
        parts.append(f"fit {payload['score']}")
    body = " · ".join(p for p in parts if p) or notification.event_type.replace("_", " ")
    message = {
        "title": title,
        "body": body,
        "tag": str(notification.id),
        "event_type": notification.event_type,
        "url": payload.get("deep_link"),
        "notification_id": str(notification.id),
    }
    encoded = json.dumps(message, separators=(",", ":"))
    if len(encoded.encode()) > MAX_PAYLOAD_BYTES:  # pragma: no cover - titles are short
        message["title"] = title[:120]
        message["body"] = body[:200]
        encoded = json.dumps(message, separators=(",", ":"))
    return encoded


class PushChannel:
    """notify.core.Channel fanning one notification out to every live subscription."""

    name = PUSH

    def __init__(
        self,
        settings: Settings,
        *,
        subscriptions: SubscriptionResolver | None = None,
        sender: PushSender | None = None,
        on_gone: GoneHandler | None = None,
    ) -> None:
        self.settings = settings
        self.subscriptions = subscriptions
        self.sender = sender or WebPushSender(settings)
        self.on_gone = on_gone

    async def send(self, delivery: Any, notification: Notification, recipient: Any) -> SendResult:
        if self.subscriptions is None:
            return SendResult.skip("no push subscription resolver")
        subs = list(await self.subscriptions(notification.tenant_id, recipient.user_id))
        if not subs:
            return SendResult.skip("user has no push subscriptions")
        payload = build_payload(notification)
        sent = 0
        errors: list[str] = []
        for subscription in subs:
            result = await self.sender.send(subscription, payload)
            if result.ok:
                sent += 1
                continue
            if result.gone:
                log.info("notify.push.gone", endpoint=subscription.endpoint[:60])
                if self.on_gone is not None:
                    await self.on_gone(notification.tenant_id, subscription)
                continue
            errors.append(result.error or "push failed")
        if sent:
            return SendResult.sent(provider_ref=f"{sent}/{len(subs)}")
        if errors:
            return SendResult.failed("; ".join(errors)[:500])
        return SendResult.skip("every push subscription is gone")


# --- storage ------------------------------------------------------------------------------------


async def load_push_subscriptions(session: AsyncSession, user_id: uuid.UUID) -> list[Subscription]:
    rows = (
        (
            await session.execute(
                select(PushSubscription)
                .where(PushSubscription.user_id == user_id)
                .order_by(PushSubscription.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [Subscription(r.endpoint, r.p256dh, r.auth, r.id) for r in rows]


async def save_push_subscription(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    subscription: Subscription,
    user_agent: str | None = None,
    now: datetime | None = None,
) -> PushSubscription:
    """Upsert by endpoint: the browser re-sends the same endpoint after every refresh."""
    row = (
        await session.execute(
            select(PushSubscription).where(PushSubscription.endpoint == subscription.endpoint)
        )
    ).scalar_one_or_none()
    if row is None:
        row = PushSubscription(
            tenant_id=tenant_id,
            user_id=user_id,
            endpoint=subscription.endpoint,
            p256dh=subscription.p256dh,
            auth=subscription.auth,
            user_agent=(user_agent or "")[:512] or None,
        )
        session.add(row)
    else:
        row.user_id = user_id
        row.p256dh = subscription.p256dh
        row.auth = subscription.auth
        row.user_agent = (user_agent or "")[:512] or None
    row.last_seen_at = now or datetime.now(UTC)
    await session.flush()
    return row


async def delete_push_subscription(
    session: AsyncSession, user_id: uuid.UUID, endpoint: str
) -> bool:
    row = (
        await session.execute(
            select(PushSubscription).where(
                PushSubscription.user_id == user_id, PushSubscription.endpoint == endpoint
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    await session.delete(row)
    await session.flush()
    return True
