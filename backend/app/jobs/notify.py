"""Notification beat jobs (SPEC 7, M4-13).

    bidradar.send_digests     every 15 min: send the daily digest / Monday roll-up to
                              every user whose digest_time just passed in THEIR zone
    bidradar.flush_scheduled  every 5 min: send deliveries whose quiet-hours deferral
                              (notification_deliveries.scheduled_for) has expired

Both run once per tick over every tenant. `python -m app.jobs.notify digests|flush` runs
one of them by hand.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import Database, get_database
from app.models import User, UserNotificationPrefs
from app.notify.core import Dispatcher, NotificationEvent, Recipient
from app.notify.digest import DIGEST_EVENT, collect_digest, mark_rolled_up
from app.notify.registry import build_dispatcher
from app.notify.scheduling import (
    SchedulePrefs,
    digest_window,
    is_digest_due,
    is_weekly_due,
    localize,
    parse_hhmm,
    zone,
)
from app.notify.unsubscribe import load_unsubscribed

log = structlog.get_logger(__name__)

_ONE_DAY = timedelta(days=1)
DIGEST_SCHEDULE = "*/15 * * * *"
FLUSH_SCHEDULE = "*/5 * * * *"


@dataclass(frozen=True, slots=True)
class DigestRun:
    considered: int = 0
    daily: int = 0
    weekly: int = 0
    empty: int = 0
    rolled_up: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "considered": self.considered,
            "daily": self.daily,
            "weekly": self.weekly,
            "empty": self.empty,
            "rolled_up": self.rolled_up,
        }


async def _tenants_with_prefs(database: Database) -> list[uuid.UUID]:
    async with database.owner_session() as session:
        rows = (
            (await session.execute(select(UserNotificationPrefs.tenant_id).distinct()))
            .scalars()
            .all()
        )
    return list(rows)


def _dedupe_key(prefs: SchedulePrefs, now: datetime, *, weekly: bool) -> str:
    """One digest per user per period, whatever the beat does (SPEC 7 exactly once)."""
    tz = zone(prefs.tz)
    day = now.astimezone(tz).date()
    # a digest_time just before local midnight can tick over into the next local day;
    # name the period by the local day the digest instant itself belongs to
    due = localize(day, parse_hhmm(prefs.digest_time), tz)
    if due > now:
        day = (due - _ONE_DAY).astimezone(tz).date()
    return f"{'weekly' if weekly else 'daily'}:{day.isoformat()}"


async def send_digests_for_tenant(
    session: AsyncSession,
    dispatcher: Dispatcher,
    tenant_id: uuid.UUID,
    *,
    now: datetime,
) -> DigestRun:
    """Send the daily digest and the Monday roll-up to whoever is due in this tenant."""
    rows = (
        await session.execute(
            select(UserNotificationPrefs, User)
            .join(User, User.id == UserNotificationPrefs.user_id)
            .order_by(UserNotificationPrefs.created_at)
        )
    ).all()
    considered = daily = weekly_count = empty = rolled = 0
    for row, user in rows:
        prefs = SchedulePrefs.from_row(row)
        for weekly in (False, True):
            due = is_weekly_due(prefs, now) if weekly else is_digest_due(prefs, now)
            if not due:
                continue
            considered += 1
            since, until = digest_window(prefs, now, weekly=weekly)
            plan = await collect_digest(
                session, row.user_id, tenant_id, since=since, until=until, weekly=weekly
            )
            if plan.empty:
                empty += 1
                continue
            event = NotificationEvent(
                event_type=DIGEST_EVENT,
                tenant_id=tenant_id,
                payload=plan.payload(),
                occurred_at=now,
                dedupe_key=_dedupe_key(prefs, now, weekly=weekly),
            )
            recipient = Recipient(
                user_id=row.user_id,
                channels=("email",),
                email=user.email,
                name=user.name,
                tz=prefs.tz,
                unsubscribed=await load_unsubscribed(session, row.user_id),
            )
            result = await dispatcher.dispatch(session, event, [recipient])
            if result.notifications:
                rolled += await mark_rolled_up(session, plan)
                if weekly:
                    weekly_count += 1
                else:
                    daily += 1
    return DigestRun(considered, daily, weekly_count, empty, rolled)


async def send_digests_once(
    *,
    database: Database | None = None,
    settings: Settings | None = None,
    dispatcher: Dispatcher | None = None,
    now: datetime | None = None,
) -> DigestRun:
    db = database or get_database()
    conf = settings or get_settings()
    sender = dispatcher or build_dispatcher(conf, db)
    moment = now or datetime.now(UTC)
    totals = DigestRun()
    for tenant_id in await _tenants_with_prefs(db):
        async with db.session(tenant_id) as session:
            run = await send_digests_for_tenant(session, sender, tenant_id, now=moment)
        totals = DigestRun(
            totals.considered + run.considered,
            totals.daily + run.daily,
            totals.weekly + run.weekly,
            totals.empty + run.empty,
            totals.rolled_up + run.rolled_up,
        )
    log.info("notify.digests", **totals.as_dict())
    return totals


async def flush_scheduled_once(
    *,
    database: Database | None = None,
    settings: Settings | None = None,
    dispatcher: Dispatcher | None = None,
    now: datetime | None = None,
    limit: int = 500,
) -> dict[str, int]:
    """Send every queued delivery whose quiet-hours deferral has expired, per tenant."""
    db = database or get_database()
    conf = settings or get_settings()
    sender = dispatcher or build_dispatcher(conf, db)
    moment = now or datetime.now(UTC)
    sent = failed = skipped = 0
    for tenant_id in await _tenants_with_prefs(db):
        async with db.session(tenant_id) as session:
            for delivery in await sender.flush_due(session, now=moment, limit=limit):
                if delivery.status == "sent":
                    sent += 1
                elif delivery.status == "failed":
                    failed += 1
                elif delivery.status == "skipped":
                    skipped += 1
    counts = {"sent": sent, "failed": failed, "skipped": skipped}
    log.info("notify.flush_scheduled", **counts)
    return counts


def run_send_digests(now: datetime | None = None) -> dict[str, Any]:
    """Synchronous wrapper for Celery / CLI."""
    return asyncio.run(send_digests_once(now=now)).as_dict()


def run_flush_scheduled(now: datetime | None = None) -> dict[str, Any]:
    return dict(asyncio.run(flush_scheduled_once(now=now)))


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    which = args[0] if args else "digests"
    counts = run_flush_scheduled() if which == "flush" else run_send_digests()
    sys.stdout.write(json.dumps(counts) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
