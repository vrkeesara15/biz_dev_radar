"""The reminder ladder in the database (SPEC 9, M6-03).

    await generate_for_date(session, date, now=now)      # on create / after a date moves
    run = await send_due(session, settings, dispatcher, tenant_id, now=now)

One `reminders` row per rung. The row is the idempotency record: `sent_at` is stamped in
the SAME transaction as the dispatch, so two overlapping beat ticks cannot send a rung
twice, and the dispatcher's own idempotency key is a second belt.

Rules
- rungs already in the past when a date is created are never generated: a notice opened
  two days before its deadline does not get "7 days to go";
- a date that MOVES has its unsent rungs deleted and re-derived from the new instant,
  while rungs already sent stay as history;
- overdue rungs (every 4 h) are added lazily by the beat, capped per pass;
- a rung that comes due after the pursuit left the open stages (submitted / awarded /
  lost / cancelled / no_bid) is recorded `skipped_reason` and never sent — SPEC 9's
  "until someone marks it submitted, passed or cancelled";
- escalation follows `core.reminders.escalation_level` against the date's acknowledgement:
  level 1 adds the tenant's bid managers, level 2 adds the tenant owner.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import pursuit_stages as stages
from app.core.config import Settings
from app.core.display_time import countdown, render_tz
from app.core.preferences import DEFAULT_CHANNELS_BY_EVENT, NotificationEvent
from app.core.reminders import (
    ESCALATION_MANAGER,
    ESCALATION_TENANT_OWNER,
    LABEL_OVERDUE,
    describe,
    escalation_level,
    ladder,
    overdue_slots,
)
from app.core.roles import Role
from app.models import (
    Membership,
    Opportunity,
    Pursuit,
    PursuitDate,
    Reminder,
    User,
    UserNotificationPrefs,
)
from app.notify.core import Dispatcher, Recipient
from app.notify.core import NotificationEvent as DispatchEvent
from app.notify.scheduling import SchedulePrefs, deliver_at
from app.notify.unsubscribe import load_unsubscribed

log = structlog.get_logger(__name__)

REMINDER_EVENT = NotificationEvent.DEADLINE_REMINDER.value
SCHEDULE = "*/5 * * * *"  # SPEC 9: the beat runs every five minutes
CLOSED_REASON = "the pursuit was {stage} before this reminder came due"
# SPEC 7's deadline-reminder row: email + Slack + WhatsApp (IN) + in-app. The calendar
# already holds the event (M6-04), so it is not a notification channel here.
DEFAULT_CHANNELS: tuple[str, ...] = ("in_app", "email", "slack", "whatsapp")


@dataclass
class ReminderRun:
    considered: int = 0
    sent: int = 0
    skipped: int = 0
    created: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "considered": self.considered,
            "sent": self.sent,
            "skipped": self.skipped,
            "created": self.created,
        }


# --- generating the rungs ---------------------------------------------------------------------


async def existing_rungs(session: AsyncSession, date_id: uuid.UUID) -> Sequence[Reminder]:
    return (
        (
            await session.execute(
                select(Reminder)
                .where(Reminder.pursuit_date_id == date_id)
                .order_by(Reminder.due_at)
            )
        )
        .scalars()
        .all()
    )


async def generate_for_date(
    session: AsyncSession, date: PursuitDate, *, now: datetime | None = None
) -> list[Reminder]:
    """Create the missing pre-deadline rungs for one key date; returns the new rows."""
    moment = now or datetime.now(UTC)
    have = {(r.offset_label, r.due_at) for r in await existing_rungs(session, date.id)}
    created: list[Reminder] = []
    for label, due_at in ladder(date.at, moment):
        if (label, due_at) in have:
            continue
        row = Reminder(
            tenant_id=date.tenant_id,
            pursuit_date_id=date.id,
            offset_label=label,
            due_at=due_at,
            escalation_level=escalation_level(label, acknowledged=date.acknowledged_at is not None),
        )
        session.add(row)
        created.append(row)
    if created:
        await session.flush()
    return created


async def regenerate_for_date(
    session: AsyncSession, date: PursuitDate, *, now: datetime | None = None
) -> list[Reminder]:
    """After a date moved: drop the unsent rungs and re-derive them from the new instant.

    Rungs already sent are history and stay; a rung the new schedule still wants is
    re-created at its new moment.
    """
    for row in await existing_rungs(session, date.id):
        if row.sent_at is None and row.skipped_reason is None:
            await session.delete(row)
    await session.flush()
    return await generate_for_date(session, date, now=now)


async def add_overdue_rungs(
    session: AsyncSession, date: PursuitDate, *, now: datetime
) -> list[Reminder]:
    """Every 4 h past the deadline, up to now, continuing from the last one recorded."""
    rows = await existing_rungs(session, date.id)
    last = max(
        (r.due_at for r in rows if r.offset_label == LABEL_OVERDUE),
        default=None,
    )
    created: list[Reminder] = []
    for label, due_at in overdue_slots(date.at, now, after=last):
        row = Reminder(
            tenant_id=date.tenant_id,
            pursuit_date_id=date.id,
            offset_label=label,
            due_at=due_at,
            escalation_level=escalation_level(label, acknowledged=date.acknowledged_at is not None),
        )
        session.add(row)
        created.append(row)
    if created:
        await session.flush()
    return created


# --- sending --------------------------------------------------------------------------------


async def open_dates(session: AsyncSession) -> Sequence[tuple[PursuitDate, Pursuit]]:
    """Key dates whose pursuit is still open (SPEC 9: until submitted / passed / cancelled)."""
    rows = (
        await session.execute(
            select(PursuitDate, Pursuit)
            .join(Pursuit, Pursuit.id == PursuitDate.pursuit_id)
            .where(Pursuit.stage.in_(list(stages.OPEN_STAGES)))
        )
    ).all()
    return [(d, p) for d, p in rows]


async def due_reminders(
    session: AsyncSession, *, now: datetime, limit: int = 500
) -> Sequence[Reminder]:
    return (
        (
            await session.execute(
                select(Reminder)
                .where(
                    Reminder.sent_at.is_(None),
                    Reminder.skipped_reason.is_(None),
                    Reminder.due_at <= now,
                )
                .order_by(Reminder.due_at)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )


async def escalation_recipients(
    session: AsyncSession, pursuit: Pursuit, level: int
) -> list[uuid.UUID]:
    """Who this rung reaches: the owner, plus bid managers at level 1, plus the tenant
    owner at level 2 (SPEC 9)."""
    out: list[uuid.UUID] = []
    if pursuit.owner_user_id is not None:
        out.append(pursuit.owner_user_id)
    wanted: list[Role] = []
    if level >= ESCALATION_MANAGER:
        wanted.append(Role.BID_MANAGER)
    if level >= ESCALATION_TENANT_OWNER:
        wanted.append(Role.TENANT_OWNER)
    if wanted:
        rows = (
            (await session.execute(select(Membership.user_id).where(Membership.role.in_(wanted))))
            .scalars()
            .all()
        )
        for user_id in rows:
            if user_id not in out:
                out.append(uuid.UUID(str(user_id)))
    return out


def channels_for(prefs: UserNotificationPrefs | None) -> tuple[str, ...]:
    """The user's channels for this event, falling back to SPEC 7's row."""
    if prefs is None:
        return DEFAULT_CHANNELS
    configured = (prefs.channels_by_event or {}).get(REMINDER_EVENT)
    if isinstance(configured, list) and configured:
        return tuple(str(name) for name in configured)
    if DEFAULT_CHANNELS_BY_EVENT.get(REMINDER_EVENT):
        return DEFAULT_CHANNELS
    return DEFAULT_CHANNELS  # pragma: no cover - the map always has the event


async def build_recipients(
    session: AsyncSession, pursuit: Pursuit, level: int, *, due_at: datetime, now: datetime
) -> list[Recipient]:
    """Everyone this rung reaches, each with their own channels and quiet-hours instant.

    SPEC 7's quiet-hours exception does the right thing by itself here: a deadline less
    than 72 h away is urgent, so the late rungs of the ladder are never deferred.
    """
    recipients: list[Recipient] = []
    for user_id in await escalation_recipients(session, pursuit, level):
        user = await session.get(User, user_id)
        if user is None:
            continue
        prefs = (
            await session.execute(
                select(UserNotificationPrefs).where(UserNotificationPrefs.user_id == user_id)
            )
        ).scalar_one_or_none()
        plan = deliver_at(
            SchedulePrefs.from_row(prefs) if prefs is not None else SchedulePrefs(tz=user.tz),
            now=now,
            response_due_at=due_at,
        )
        recipients.append(
            Recipient(
                user_id=user_id,
                channels=channels_for(prefs),
                email=user.email,
                name=user.name,
                tz=(prefs.tz if prefs is not None else None) or user.tz,
                scheduled_for=plan.send_at if plan.deferred else None,
                unsubscribed=await load_unsubscribed(session, user_id),
            )
        )
    return recipients


def reminder_payload(
    date: PursuitDate,
    pursuit: Pursuit,
    opportunity: Opportunity,
    reminder: Reminder,
    *,
    now: datetime,
) -> dict[str, Any]:
    return {
        "title": opportunity.title,
        "buyer": opportunity.buyer_org,
        "buyer_tz": date.buyer_tz,
        "key_date": date.label,
        "key_date_kind": date.kind,
        "due_at": date.at.isoformat(),
        "offset_label": reminder.offset_label,
        "offset_display": describe(reminder.offset_label),
        "escalation_level": reminder.escalation_level,
        "acknowledged": date.acknowledged_at is not None,
        "countdown": countdown(now, date.at),
        "due_display": render_tz(date.at, date.buyer_tz, None, with_year=True),
        "pursuit_stage": pursuit.stage,
        "response_due_at": (
            None if opportunity.response_due_at is None else opportunity.response_due_at.isoformat()
        ),
    }


async def send_due(
    session: AsyncSession,
    settings: Settings,
    dispatcher: Dispatcher,
    tenant_id: uuid.UUID,
    *,
    now: datetime | None = None,
    limit: int = 500,
) -> ReminderRun:
    """One beat tick for one tenant: add overdue rungs, then send whatever is due."""
    moment = now or datetime.now(UTC)
    run = ReminderRun()

    for open_date, _pursuit in await open_dates(session):
        run.created += len(await add_overdue_rungs(session, open_date, now=moment))
        if not await existing_rungs(session, open_date.id):
            run.created += len(await generate_for_date(session, open_date, now=moment))

    for reminder in await due_reminders(session, now=moment, limit=limit):
        run.considered += 1
        date = await session.get(PursuitDate, reminder.pursuit_date_id)
        pursuit = None if date is None else await session.get(Pursuit, date.pursuit_id)
        if date is None or pursuit is None:  # pragma: no cover - the FKs cascade
            continue
        if not stages.is_open(pursuit.stage):
            reminder.skipped_reason = CLOSED_REASON.format(stage=pursuit.stage)
            run.skipped += 1
            continue
        opportunity = await session.get(Opportunity, pursuit.opportunity_id)
        if opportunity is None:  # pragma: no cover - the FK cascades
            continue
        # acknowledging the date after the rung was created drops the escalation (SPEC 9)
        reminder.escalation_level = escalation_level(
            reminder.offset_label, acknowledged=date.acknowledged_at is not None
        )
        recipients = await build_recipients(
            session, pursuit, reminder.escalation_level, due_at=date.at, now=moment
        )
        if not recipients:
            reminder.skipped_reason = "the pursuit has nobody to remind"
            run.skipped += 1
            continue
        event = DispatchEvent(
            event_type=REMINDER_EVENT,
            tenant_id=tenant_id,
            opportunity_id=opportunity.id,
            pursuit_id=pursuit.id,
            payload=reminder_payload(date, pursuit, opportunity, reminder, now=moment),
            occurred_at=reminder.due_at,
            response_due_at=date.at,
            dedupe_key=f"{date.id}:{reminder.offset_label}:{int(reminder.due_at.timestamp())}",
        )
        result = await dispatcher.dispatch(session, event, recipients)
        # stamped in the same transaction as the dispatch: a second tick finds it handled
        reminder.sent_at = moment
        if result.notifications:
            reminder.delivery_notification_id = result.notifications[0].id
        run.sent += 1
    await session.flush()
    return run
