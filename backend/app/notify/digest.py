"""Daily digest and weekly roll-up (SPEC 7). One email instead of many.

    plan = await collect_digest(session, user_id, since=..., until=...)
    plan.items          # what goes in the email, newest first
    plan.rolled_up      # the deliveries folded into it (marked skipped, never sent twice)

The DAILY digest collects two things for one user and then retires the first:

  * every notification whose email delivery is still `queued` with a scheduled_for in the
    past (quiet hours deferred it, or the router queued it as digest-only), and
  * medium-band matches recorded in the window that were never delivered instantly.

Folding a queued delivery into the digest marks it `skipped` with a reason, so the
Dispatcher's flush never sends it as its own email afterwards.

The WEEKLY roll-up is a summary, not a delivery mechanism: it lists everything from the
past seven days whether or not it already went out, and retires nothing.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DeliveryStatus, Notification, NotificationDelivery
from app.notify.core import EMAIL

log = structlog.get_logger(__name__)

DIGEST_EVENT = "digest"
MEDIUM_BAND = "medium"
ROLLED_UP_REASON = "rolled into the digest"
MAX_ITEMS = 50
# events that are never worth a digest line of their own
_SKIP_EVENTS = frozenset({DIGEST_EVENT})


@dataclass(frozen=True, slots=True)
class DigestPlan:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    since: datetime
    until: datetime
    weekly: bool = False
    items: list[dict[str, Any]] = field(default_factory=list)
    rolled_up: list[NotificationDelivery] = field(default_factory=list)
    notifications: list[Notification] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.items

    def payload(self) -> dict[str, Any]:
        return {
            "digest_period": "weekly" if self.weekly else "daily",
            "items": self.items,
            "window": {"from": self.since.isoformat(), "to": self.until.isoformat()},
        }


def digest_item(notification: Notification) -> dict[str, Any]:
    """One line of the digest, in the shape app.notify.render's `items` loop expects."""
    payload = dict(notification.payload or {})
    return {
        "title": payload.get("title") or notification.event_type.replace("_", " ").title(),
        "buyer": payload.get("buyer") or payload.get("buyer_org"),
        "score": payload.get("score"),
        "band": payload.get("band"),
        "link": payload.get("deep_link"),
        "event_type": notification.event_type,
        "response_due_at": payload.get("response_due_at"),
        "buyer_tz": payload.get("buyer_tz"),
        "value_amount": payload.get("value_amount"),
        "value_currency": payload.get("value_currency"),
        "notification_id": str(notification.id),
    }


async def collect_digest(
    session: AsyncSession,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    *,
    since: datetime,
    until: datetime,
    weekly: bool = False,
    limit: int = MAX_ITEMS,
) -> DigestPlan:
    """Everything that should go into this user's digest (see the module docstring)."""
    if weekly:
        return await _collect_weekly(
            session, user_id, tenant_id, since=since, until=until, limit=limit
        )
    queued = (
        await session.execute(
            select(NotificationDelivery, Notification)
            .join(Notification, Notification.id == NotificationDelivery.notification_id)
            .where(
                Notification.user_id == user_id,
                NotificationDelivery.channel == EMAIL,
                NotificationDelivery.status == DeliveryStatus.QUEUED.value,
                NotificationDelivery.scheduled_for.is_not(None),
                NotificationDelivery.scheduled_for <= until,
            )
            .order_by(Notification.created_at.desc(), Notification.id.desc())
            .limit(limit)
        )
    ).all()
    seen: set[uuid.UUID] = set()
    items: list[dict[str, Any]] = []
    rolled: list[NotificationDelivery] = []
    notifications: list[Notification] = []
    for delivery, notification in queued:
        if notification.event_type in _SKIP_EVENTS or notification.id in seen:
            continue
        seen.add(notification.id)
        items.append(digest_item(notification))
        rolled.append(delivery)
        notifications.append(notification)

    if len(items) < limit:
        medium = (
            (
                await session.execute(
                    select(Notification)
                    .where(
                        Notification.user_id == user_id,
                        Notification.created_at > since,
                        Notification.created_at <= until,
                        Notification.payload["band"].astext == MEDIUM_BAND,
                    )
                    .order_by(Notification.created_at.desc(), Notification.id.desc())
                    .limit(limit - len(items))
                )
            )
            .scalars()
            .all()
        )
        for notification in medium:
            if notification.id in seen or notification.event_type in _SKIP_EVENTS:
                continue
            seen.add(notification.id)
            items.append(digest_item(notification))
            notifications.append(notification)

    return DigestPlan(
        user_id=user_id,
        tenant_id=tenant_id,
        since=since,
        until=until,
        weekly=weekly,
        items=items,
        rolled_up=rolled,
        notifications=notifications,
    )


async def _collect_weekly(
    session: AsyncSession,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    *,
    since: datetime,
    until: datetime,
    limit: int,
) -> DigestPlan:
    """Monday roll-up: everything from the week, delivered or not, and nothing retired."""
    rows = (
        (
            await session.execute(
                select(Notification)
                .where(
                    Notification.user_id == user_id,
                    Notification.event_type.not_in(_SKIP_EVENTS),
                    Notification.created_at > since,
                    Notification.created_at <= until,
                )
                .order_by(Notification.created_at.desc(), Notification.id.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return DigestPlan(
        user_id=user_id,
        tenant_id=tenant_id,
        since=since,
        until=until,
        weekly=True,
        items=[digest_item(row) for row in rows],
        notifications=list(rows),
    )


async def mark_rolled_up(session: AsyncSession, plan: DigestPlan) -> int:
    """Retire the queued deliveries the digest now covers so they are never sent twice."""
    for delivery in plan.rolled_up:
        delivery.status = DeliveryStatus.SKIPPED.value
        delivery.last_error = ROLLED_UP_REASON
    await session.flush()
    return len(plan.rolled_up)
