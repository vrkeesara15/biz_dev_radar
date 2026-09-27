"""Reminder beat job (SPEC 9, M6-03).

    bidradar.send_reminders   every 5 min: add the overdue rungs that have come due and
                              send every unsent rung whose moment has passed

Idempotent by construction: each rung's `sent_at` is written in the same transaction as
its dispatch, so two overlapping ticks cannot send it twice. `python -m app.jobs.reminders`
runs one pass by hand.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.db import Database, get_database
from app.models import Pursuit
from app.notify.core import Dispatcher
from app.notify.registry import build_dispatcher
from app.services.reminders import SCHEDULE, ReminderRun, send_due

log = structlog.get_logger(__name__)

__all__ = ["SCHEDULE", "main", "run_send_reminders", "send_reminders_once"]


async def _tenants_with_pursuits(database: Database) -> list[uuid.UUID]:
    async with database.owner_session() as session:
        rows = (await session.execute(select(Pursuit.tenant_id).distinct())).scalars().all()
    return [uuid.UUID(str(row)) for row in rows]


async def send_reminders_once(
    *,
    database: Database | None = None,
    settings: Settings | None = None,
    dispatcher: Dispatcher | None = None,
    now: datetime | None = None,
) -> ReminderRun:
    db = database or get_database()
    conf = settings or get_settings()
    sender = dispatcher or build_dispatcher(conf, db)
    moment = now or datetime.now(UTC)
    totals = ReminderRun()
    for tenant_id in await _tenants_with_pursuits(db):
        async with db.session(tenant_id) as session:
            run = await send_due(session, conf, sender, tenant_id, now=moment)
        totals = ReminderRun(
            totals.considered + run.considered,
            totals.sent + run.sent,
            totals.skipped + run.skipped,
            totals.created + run.created,
        )
    log.info("reminders.tick", **totals.as_dict())
    return totals


def run_send_reminders(now: datetime | None = None) -> dict[str, Any]:
    """Synchronous wrapper for Celery / CLI (its own loop and Database per task)."""
    return dict(asyncio.run(_with_fresh_database(now)).as_dict())


async def _with_fresh_database(now: datetime | None) -> ReminderRun:
    settings = get_settings()
    db = Database(settings.database_url, settings.database_url_owner)
    try:
        return await send_reminders_once(database=db, settings=settings, now=now)
    finally:
        await db.dispose()


def main(argv: list[str] | None = None) -> int:
    counts = run_send_reminders()
    sys.stdout.write(json.dumps(counts) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
