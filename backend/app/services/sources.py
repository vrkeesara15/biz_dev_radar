"""sources / source_runs bookkeeping: registry sync, watermarks, run records."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import registry
from app.core.config import Region
from app.core.db import Database, get_database
from app.models import Source, SourceRun

log = structlog.get_logger(__name__)

RUN_RUNNING = "running"
RUN_OK = "ok"
RUN_DEGRADED = "degraded"
RUN_FAILING = "failing"


async def sync_sources(session: AsyncSession) -> list[str]:
    """Ensure one `sources` row per registered adapter. Returns the ids created.

    Existing rows keep their operator-set `enabled` flag and watermark; only the
    descriptive columns (region, schedule) follow the code.
    """
    wanted = registry.registered()
    if not wanted:
        return []
    existing = set(
        (await session.execute(select(Source.source_id).where(Source.source_id.in_(wanted))))
        .scalars()
        .all()
    )
    for source_id, cls in wanted.items():
        stmt = insert(Source).values(
            source_id=source_id,
            region=Region(cls.region),
            schedule=cls.schedule,
            enabled=registry.is_enabled(cls),
        )
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=[Source.source_id],
                set_={"region": stmt.excluded.region, "schedule": stmt.excluded.schedule},
            )
        )
    await session.flush()
    return [sid for sid in wanted if sid not in existing]


async def sync_sources_on_startup(database: Database | None = None) -> None:
    """Best-effort startup hook: a database outage must not stop the API from booting."""
    db = database or get_database()
    try:
        async with db.session(None) as session:
            created = await sync_sources(session)
        if created:
            log.info("sources.synced", created=created)
    except Exception as exc:
        log.warning("sources.sync_failed", error=str(exc))


async def get_source(session: AsyncSession, source_id: str) -> Source:
    source = await session.get(Source, source_id)
    if source is None:
        raise LookupError(f"unknown source {source_id!r}; run sync_sources()")
    return source


async def start_run(
    session: AsyncSession, source_id: str, *, now: datetime | None = None
) -> SourceRun:
    run = SourceRun(source_id=source_id, started_at=now or datetime.now(UTC), status=RUN_RUNNING)
    session.add(run)
    await session.flush()
    return run


async def finish_run(
    session: AsyncSession,
    run: SourceRun,
    *,
    status: str,
    fetched: int,
    upserted: int,
    errors: list[dict[str, Any]],
    watermark: datetime | None,
    cursor: str | None,
    now: datetime | None = None,
) -> SourceRun:
    """Close a run and roll its outcome up onto the `sources` row."""
    now = now or datetime.now(UTC)
    run.finished_at = now
    run.status = status
    run.fetched = fetched
    run.upserted = upserted
    run.errors = errors
    run.watermark = watermark
    run.cursor = cursor
    source = await get_source(session, run.source_id)
    source.last_run_at = now
    source.last_status = status
    if status == RUN_FAILING:
        source.consecutive_failures += 1
    else:
        source.consecutive_failures = 0
        if watermark is not None:
            source.watermark_at = watermark
        source.cursor = cursor
    source.health_status = status if status != RUN_OK else "ok"
    source.health_message = errors[-1].get("message") if errors else None
    await session.flush()
    return run


async def recent_runs(session: AsyncSession, source_id: str, limit: int = 10) -> list[SourceRun]:
    rows = await session.execute(
        select(SourceRun)
        .where(SourceRun.source_id == source_id)
        .order_by(SourceRun.started_at.desc())
        .limit(limit)
    )
    return list(rows.scalars().all())


async def get_run(session: AsyncSession, run_id: uuid.UUID) -> SourceRun | None:
    return await session.get(SourceRun, run_id)
