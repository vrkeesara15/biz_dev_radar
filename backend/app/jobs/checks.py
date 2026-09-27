"""Recurring-check beat jobs (SPEC 9, 4.1; M6-06).

    bidradar.expiry_checks   daily: 60/30/7-day renewal reminders to the tenant owner and
                             the SAM / DSC bid block
    bidradar.stale_pursuits  daily: a pursuit nobody has touched for five days nudges its
                             owner

`python -m app.jobs.checks expiry|stale` runs one of them by hand.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import UTC, date, datetime
from typing import Any

import structlog
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.db import Database, get_database
from app.models import CompanyProfile, Pursuit
from app.notify.core import Dispatcher
from app.notify.registry import build_dispatcher
from app.services.checks import (
    EXPIRY_SCHEDULE,
    STALE_SCHEDULE,
    CheckRun,
    expiry_checks_for_tenant,
    stale_pursuits_for_tenant,
)

log = structlog.get_logger(__name__)

__all__ = [
    "EXPIRY_SCHEDULE",
    "STALE_SCHEDULE",
    "expiry_checks_once",
    "main",
    "run_expiry_checks",
    "run_stale_pursuits",
    "stale_pursuits_once",
]


def _merge(a: CheckRun, b: CheckRun) -> CheckRun:
    return CheckRun(a.scanned + b.scanned, a.notified + b.notified, a.blocked + b.blocked)


async def _tenants(database: Database, column: Any) -> list[uuid.UUID]:
    async with database.owner_session() as session:
        result = await session.execute(select(column).distinct())
        rows: list[Any] = list(result.scalars().all())
    return [uuid.UUID(str(row)) for row in rows]


async def expiry_checks_once(
    *,
    database: Database | None = None,
    settings: Settings | None = None,
    dispatcher: Dispatcher | None = None,
    today: date | None = None,
) -> CheckRun:
    db = database or get_database()
    conf = settings or get_settings()
    sender = dispatcher or build_dispatcher(conf, db)
    day = today or datetime.now(UTC).date()
    totals = CheckRun()
    for tenant_id in await _tenants(db, CompanyProfile.tenant_id):
        async with db.session(tenant_id) as session:
            run = await expiry_checks_for_tenant(session, conf, sender, tenant_id, today=day)
        totals = _merge(totals, run)
    log.info("checks.expiry", **totals.as_dict())
    return totals


async def stale_pursuits_once(
    *,
    database: Database | None = None,
    settings: Settings | None = None,
    dispatcher: Dispatcher | None = None,
    now: datetime | None = None,
) -> CheckRun:
    db = database or get_database()
    conf = settings or get_settings()
    sender = dispatcher or build_dispatcher(conf, db)
    moment = now or datetime.now(UTC)
    totals = CheckRun()
    for tenant_id in await _tenants(db, Pursuit.tenant_id):
        async with db.session(tenant_id) as session:
            run = await stale_pursuits_for_tenant(session, conf, sender, tenant_id, now=moment)
        totals = _merge(totals, run)
    log.info("checks.stale_pursuits", **totals.as_dict())
    return totals


async def _with_fresh_database(which: str) -> CheckRun:
    settings = get_settings()
    db = Database(settings.database_url, settings.database_url_owner)
    try:
        if which == "stale":
            return await stale_pursuits_once(database=db, settings=settings)
        return await expiry_checks_once(database=db, settings=settings)
    finally:
        await db.dispose()


def run_expiry_checks() -> dict[str, int]:
    return dict(asyncio.run(_with_fresh_database("expiry")).as_dict())


def run_stale_pursuits() -> dict[str, int]:
    return dict(asyncio.run(_with_fresh_database("stale")).as_dict())


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    which = args[0] if args else "expiry"
    counts = run_stale_pursuits() if which == "stale" else run_expiry_checks()
    sys.stdout.write(json.dumps(counts) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
