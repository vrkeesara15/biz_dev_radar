"""Notification core (SPEC 7): events, recipients, channels, idempotency, retries, fallback.

    dispatcher = Dispatcher({"email": EmailChannel(...), "slack": SlackChannel(...)}, settings)
    result = await dispatcher.dispatch(session, event, [Recipient(user_id=..., email=...,
                                                                   channels=("in_app", "email"))])
    result.notifications      # rows created (one per NEW recipient; duplicates are no-ops)
    result.deliveries         # every delivery row touched, with its final status
    await delivery_log(session, notification_id=...)

Rules
- Idempotency key = "<user>:<event_type>:<opportunity or pursuit id>:<version>" on the
  notification; each delivery adds ":<channel>". An event already delivered to a user is a
  no-op (SPEC 7: exactly once).
- Every payload carries the deep link and signed one-click actions (Pursue / Watch / Pass /
  Assign) from app.notify.actions.
- A channel send is retried NOTIFY_MAX_ATTEMPTS times with NOTIFY_BACKOFF_SECONDS between
  attempts (clock and sleep are injectable); when it still fails the delivery is `failed` and
  an email fallback delivery is created and sent (unless the failing channel was email, the
  recipient has no address, or email is already one of their channels). A channel that is
  not configured is `skipped`; so is a channel that declines on purpose (SendResult.skip:
  an unsubscribed category, a recipient with no address).
- A delivery whose `scheduled_for` lies in the future (quiet hours / digest, M4-13) stays
  `queued`; `flush_due` sends what is due.

Channels implement `send(delivery, notification, recipient) -> SendResult` and never raise
for provider errors (a raised exception counts as a failed attempt too).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models import DeliveryStatus, Notification, NotificationDelivery
from app.notify.actions import action_links, deep_link
from app.notify.unsubscribe import load_unsubscribed

log = structlog.get_logger(__name__)

Clock = Callable[[], datetime]
Sleep = Callable[[float], Awaitable[None]]

EMAIL = "email"


@dataclass(frozen=True, slots=True)
class NotificationEvent:
    """One thing that happened, addressed to a tenant (the router picks the recipients)."""

    event_type: str  # app.core.preferences.NotificationEvent value or an ops event
    tenant_id: uuid.UUID
    opportunity_id: uuid.UUID | None = None
    pursuit_id: uuid.UUID | None = None
    version: int = 1
    payload: dict[str, Any] = field(default_factory=dict)
    # when the event was produced (scoring time for matches): latency is measured from here
    occurred_at: datetime | None = None
    # the notice's deadline; quiet hours are ignored when it is < 72 h away (M4-13)
    response_due_at: datetime | None = None
    # stands in for the object id when the event is not about one row: a digest uses
    # "daily:2026-10-11" so one user gets one digest per period however often beat ticks
    dedupe_key: str | None = None

    @property
    def object_id(self) -> uuid.UUID | None:
        return self.opportunity_id or self.pursuit_id

    def idempotency_key(self, user_id: uuid.UUID) -> str:
        marker = self.dedupe_key or (str(self.object_id) if self.object_id else "-")
        return f"{user_id}:{self.event_type}:{marker}:{self.version}"


@dataclass(frozen=True, slots=True)
class Recipient:
    user_id: uuid.UUID
    channels: tuple[str, ...]
    email: str | None = None
    name: str | None = None
    tz: str = "UTC"
    locale: str = "en"
    # per-user delivery instant (quiet hours / digest); None = now
    scheduled_for: datetime | None = None
    # CAN-SPAM opt-outs (app.notify.unsubscribe): the email channel skips these categories
    unsubscribed: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SendResult:
    ok: bool
    error: str | None = None
    provider_ref: str | None = None
    # the channel declined on purpose (unsubscribed, no address): no retry, no fallback
    skipped: bool = False

    @classmethod
    def sent(cls, provider_ref: str | None = None) -> SendResult:
        return cls(True, None, provider_ref)

    @classmethod
    def failed(cls, error: str) -> SendResult:
        return cls(False, error, None)

    @classmethod
    def skip(cls, reason: str) -> SendResult:
        return cls(False, reason, None, True)


class Channel(Protocol):
    name: str

    async def send(
        self, delivery: NotificationDelivery, notification: Notification, recipient: Recipient
    ) -> SendResult: ...


@dataclass
class DispatchResult:
    notifications: list[Notification] = field(default_factory=list)
    deliveries: list[NotificationDelivery] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)  # idempotency keys skipped

    @property
    def sent(self) -> list[NotificationDelivery]:
        return [d for d in self.deliveries if d.status == DeliveryStatus.SENT.value]

    @property
    def failed(self) -> list[NotificationDelivery]:
        return [d for d in self.deliveries if d.status == DeliveryStatus.FAILED.value]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Dispatcher:
    def __init__(
        self,
        channels: Mapping[str, Channel],
        settings: Settings | None = None,
        *,
        clock: Clock = _utcnow,
        sleep: Sleep = asyncio.sleep,
        max_attempts: int | None = None,
        backoff_seconds: Sequence[float] | None = None,
    ) -> None:
        self.channels = dict(channels)
        self.settings = settings or get_settings()
        self.clock = clock
        self.sleep = sleep
        self.max_attempts = max(1, max_attempts or self.settings.notify_max_attempts)
        self.backoff = list(
            backoff_seconds if backoff_seconds is not None else self.settings.notify_backoff_seconds
        )
        # recipient lookup for deferred deliveries (flush_due) keyed by notification id
        self._recipients: dict[uuid.UUID, Recipient] = {}

    # --- public --------------------------------------------------------------------------------

    async def dispatch(
        self, session: AsyncSession, event: NotificationEvent, recipients: Iterable[Recipient]
    ) -> DispatchResult:
        result = DispatchResult()
        for recipient in recipients:
            key = event.idempotency_key(recipient.user_id)
            existing = (
                await session.execute(
                    select(Notification.id).where(Notification.idempotency_key == key)
                )
            ).scalar_one_or_none()
            if existing is not None:
                result.duplicates.append(key)
                log.info("notify.duplicate", key=key)
                continue
            notification = await self._create_notification(session, event, recipient, key)
            result.notifications.append(notification)
            self._recipients[notification.id] = recipient
            for channel in dict.fromkeys(recipient.channels):
                delivery = await self._create_delivery(
                    session, notification, channel, f"{key}:{channel}", recipient.scheduled_for
                )
                result.deliveries.append(delivery)
                if channel not in self.channels:
                    delivery.status = DeliveryStatus.SKIPPED.value
                    delivery.last_error = f"channel {channel!r} not configured"
                    continue
                if delivery.scheduled_for is not None and delivery.scheduled_for > self.clock():
                    continue  # stays queued until flush_due (quiet hours / digest)
                fallback = await self.deliver(session, delivery, notification, recipient)
                if fallback is not None:
                    result.deliveries.append(fallback)
        await session.flush()
        return result

    async def deliver(
        self,
        session: AsyncSession,
        delivery: NotificationDelivery,
        notification: Notification,
        recipient: Recipient,
    ) -> NotificationDelivery | None:
        """Send one queued delivery with retries; returns the email fallback delivery when
        the channel gave up and a fallback was possible."""
        channel = self.channels[delivery.channel]
        error = await self._attempt(channel, delivery, notification, recipient)
        if error is None:
            return None  # sent, or the channel skipped it on purpose
        delivery.status = DeliveryStatus.FAILED.value
        delivery.last_error = error
        log.warning(
            "notify.delivery_failed",
            channel=delivery.channel,
            attempts=delivery.attempts,
            error=error,
        )
        return await self._fallback(session, delivery, notification, recipient, error)

    async def flush_due(
        self, session: AsyncSession, *, now: datetime | None = None, limit: int = 500
    ) -> list[NotificationDelivery]:
        """Send every queued delivery whose scheduled_for has passed (beat task in M4-13)."""
        moment = now or self.clock()
        rows = (
            (
                await session.execute(
                    select(NotificationDelivery)
                    .where(
                        NotificationDelivery.status == DeliveryStatus.QUEUED.value,
                        NotificationDelivery.scheduled_for.is_not(None),
                        NotificationDelivery.scheduled_for <= moment,
                    )
                    .order_by(NotificationDelivery.scheduled_for)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        touched: list[NotificationDelivery] = []
        for delivery in rows:
            notification = await session.get(Notification, delivery.notification_id)
            if notification is None:  # pragma: no cover - FK cascade removes deliveries
                continue
            recipient = self._recipients.get(notification.id) or await recipient_for(
                session, notification, (delivery.channel,)
            )
            touched.append(delivery)
            if delivery.channel not in self.channels:
                delivery.status = DeliveryStatus.SKIPPED.value
                delivery.last_error = f"channel {delivery.channel!r} not configured"
                continue
            fallback = await self.deliver(session, delivery, notification, recipient)
            if fallback is not None:
                touched.append(fallback)
        await session.flush()
        return touched

    # --- internals -----------------------------------------------------------------------------

    async def _create_notification(
        self,
        session: AsyncSession,
        event: NotificationEvent,
        recipient: Recipient,
        key: str,
    ) -> Notification:
        now = self.clock()
        notification_id = uuid.uuid4()
        payload: dict[str, Any] = {
            **event.payload,
            "deep_link": deep_link(self.settings, event.opportunity_id, event.pursuit_id),
            "actions": action_links(
                self.settings,
                tenant_id=event.tenant_id,
                user_id=recipient.user_id,
                notification_id=notification_id,
                opportunity_id=event.opportunity_id,
                pursuit_id=event.pursuit_id,
                now=now,
            ),
            "actions_taken": [],
            "recipient": {"email": recipient.email, "name": recipient.name, "tz": recipient.tz},
        }
        if event.occurred_at is not None:
            payload["occurred_at"] = event.occurred_at.isoformat()
        if event.response_due_at is not None:
            payload["response_due_at"] = event.response_due_at.isoformat()
        row = Notification(
            id=notification_id,
            tenant_id=event.tenant_id,
            user_id=recipient.user_id,
            event_type=event.event_type,
            opportunity_id=event.opportunity_id,
            pursuit_id=event.pursuit_id,
            version=event.version,
            idempotency_key=key,
            payload=payload,
            created_at=now,
        )
        session.add(row)
        await session.flush()
        return row

    async def _create_delivery(
        self,
        session: AsyncSession,
        notification: Notification,
        channel: str,
        key: str,
        scheduled_for: datetime | None,
        *,
        fallback_of: NotificationDelivery | None = None,
        fallback_reason: str | None = None,
    ) -> NotificationDelivery:
        row = NotificationDelivery(
            tenant_id=notification.tenant_id,
            notification_id=notification.id,
            channel=channel,
            status=DeliveryStatus.QUEUED.value,
            idempotency_key=key,
            attempts=0,
            scheduled_for=scheduled_for,
            fallback_of_id=None if fallback_of is None else fallback_of.id,
            fallback_reason=fallback_reason,
            created_at=self.clock(),
        )
        session.add(row)
        await session.flush()
        return row

    async def _attempt(
        self,
        channel: Channel,
        delivery: NotificationDelivery,
        notification: Notification,
        recipient: Recipient,
    ) -> str | None:
        """Up to max_attempts sends with backoff; None on success, else the last error."""
        error = "no attempt made"
        for attempt in range(1, self.max_attempts + 1):
            delivery.attempts = attempt
            try:
                outcome = await channel.send(delivery, notification, recipient)
            except Exception as exc:  # a misbehaving provider is a failed attempt
                outcome = SendResult.failed(f"{type(exc).__name__}: {exc}")
            if outcome.ok:
                delivery.status = DeliveryStatus.SENT.value
                delivery.sent_at = self.clock()
                delivery.last_error = None
                delivery.provider_ref = outcome.provider_ref
                return None
            if outcome.skipped:  # a deliberate decline is not a failure: no retry, no fallback
                delivery.status = DeliveryStatus.SKIPPED.value
                delivery.last_error = outcome.error or "skipped"
                return None
            error = outcome.error or "send failed"
            delivery.last_error = error
            if attempt < self.max_attempts:
                pause = self.backoff[min(attempt - 1, len(self.backoff) - 1)] if self.backoff else 0
                if pause > 0:
                    await self.sleep(pause)
        return error

    async def _fallback(
        self,
        session: AsyncSession,
        failed: NotificationDelivery,
        notification: Notification,
        recipient: Recipient,
        error: str,
    ) -> NotificationDelivery | None:
        # no fallback when the failing channel IS email, email is not configured, the user
        # has no address, or email is already one of the recipient's own channels (that
        # delivery covers them; a second mail would be a duplicate)
        if (
            failed.channel == EMAIL
            or EMAIL not in self.channels
            or not recipient.email
            or EMAIL in recipient.channels
        ):
            return None
        key = f"{notification.idempotency_key}:{EMAIL}:fallback:{failed.channel}"
        exists = (
            await session.execute(
                select(NotificationDelivery.id).where(NotificationDelivery.idempotency_key == key)
            )
        ).scalar_one_or_none()
        if exists is not None:
            return None
        fallback = await self._create_delivery(
            session,
            notification,
            EMAIL,
            key,
            None,
            fallback_of=failed,
            fallback_reason=f"{failed.channel} failed after {failed.attempts} attempts: {error}",
        )
        final = await self._attempt(self.channels[EMAIL], fallback, notification, recipient)
        if final is not None:
            fallback.status = DeliveryStatus.FAILED.value
            fallback.last_error = final
        return fallback


# --- helpers --------------------------------------------------------------------------------


async def recipient_for(
    session: AsyncSession, notification: Notification, channels: tuple[str, ...]
) -> Recipient:
    """Rebuild a Recipient from the stored payload (deferred deliveries in another process)."""
    stored = notification.payload.get("recipient") or {}
    unsubscribed = await load_unsubscribed(session, notification.user_id)
    return Recipient(
        user_id=notification.user_id,
        channels=channels,
        email=stored.get("email"),
        name=stored.get("name"),
        tz=stored.get("tz") or "UTC",
        unsubscribed=unsubscribed,
    )


async def delivery_log(
    session: AsyncSession,
    *,
    notification_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    channel: str | None = None,
    status: str | None = None,
    since: datetime | None = None,
    limit: int = 200,
) -> list[NotificationDelivery]:
    """Delivery log (sent/failed/opened per channel), newest first, RLS-scoped."""
    stmt = select(NotificationDelivery).join(
        Notification, Notification.id == NotificationDelivery.notification_id
    )
    if notification_id is not None:
        stmt = stmt.where(NotificationDelivery.notification_id == notification_id)
    if user_id is not None:
        stmt = stmt.where(Notification.user_id == user_id)
    if channel is not None:
        stmt = stmt.where(NotificationDelivery.channel == channel)
    if status is not None:
        stmt = stmt.where(NotificationDelivery.status == status)
    if since is not None:
        stmt = stmt.where(NotificationDelivery.created_at >= since)
    stmt = stmt.order_by(NotificationDelivery.created_at.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


def latency_seconds(notification: Notification, delivery: NotificationDelivery) -> float | None:
    """Seconds from the event (scoring) to the send; None until sent."""
    if delivery.sent_at is None:
        return None
    raw = notification.payload.get("occurred_at")
    start = datetime.fromisoformat(raw) if raw else notification.created_at
    return (delivery.sent_at - start).total_seconds()
