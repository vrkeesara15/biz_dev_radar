"""In-app bell (SPEC 7, 10.4): the notifications rows are the inbox.

The Dispatcher already wrote one `notifications` row per user per event (M4-09), so the
in-app channel has nothing to deliver — it only confirms the row exists, which keeps the
delivery log honest (one row per channel, with its own idempotency key).

    unread, total = await list_notifications(session, user_id, unread_only=True)
    await mark_read(session, notification_id, user_id, now=...)
    await mark_all_read(session, user_id, now=...)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Notification
from app.notify.core import SendResult

IN_APP = "in_app"
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class InAppChannel:
    """notify.core.Channel whose delivery is the notification row itself."""

    name = IN_APP

    async def send(self, delivery: Any, notification: Notification, recipient: Any) -> SendResult:
        # The Dispatcher flushed the row before calling any channel, so the bell item
        # already exists; the delivery row only records that the bell was the channel.
        return SendResult.sent(provider_ref=str(notification.id))


async def list_notifications(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    unread_only: bool = False,
    limit: int = DEFAULT_LIMIT,
    before: datetime | None = None,
) -> tuple[list[Notification], int]:
    """Newest first, plus the unread count for the bell badge. RLS scopes the tenant."""
    stmt = select(Notification).where(Notification.user_id == user_id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    if before is not None:
        stmt = stmt.where(Notification.created_at < before)
    stmt = stmt.order_by(Notification.created_at.desc()).limit(max(1, min(limit, MAX_LIMIT)))
    rows = list((await session.execute(stmt)).scalars().all())
    unread = (
        await session.execute(
            select(func.count())
            .select_from(Notification)
            .where(Notification.user_id == user_id, Notification.read_at.is_(None))
        )
    ).scalar_one()
    return rows, int(unread)


async def mark_read(
    session: AsyncSession,
    notification_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> Notification:
    """Idempotent: a second read keeps the first timestamp. Raises LookupError otherwise."""
    row = await session.get(Notification, notification_id)
    if row is None or row.user_id != user_id:
        raise LookupError("notification not found")
    if row.read_at is None:
        row.read_at = now or datetime.now(UTC)
        await session.flush()
    return row


async def mark_all_read(
    session: AsyncSession, user_id: uuid.UUID, *, now: datetime | None = None
) -> int:
    result = await session.execute(
        update(Notification)
        .where(Notification.user_id == user_id, Notification.read_at.is_(None))
        .values(read_at=now or datetime.now(UTC))
    )
    await session.flush()
    # execute() is typed as Result; an UPDATE always returns a CursorResult with rowcount
    return int(getattr(result, "rowcount", 0) or 0)
