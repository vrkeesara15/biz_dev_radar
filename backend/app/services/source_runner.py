"""Run one adapter end-to-end: watermark -> fetch -> normalize -> sink, with a source_runs row.

The sink is injected and defaults to the ingest pipeline (`app.services.ingest.ingest_sink`);
special sources (spend statistics, awards enrichment) pass their own. Adapters are
synchronous generators; each record is
normalised and handed to the async sink in turn. Per-record failures are recorded in
`source_runs.errors` and never abort the run; a failure of `fetch()` itself marks the
run `failing` and leaves the watermark untouched.
"""

from __future__ import annotations

import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import OpportunityIn, RawRecord, SourceAdapter
from app.core.watermark import advance_watermark, since_from_watermark
from app.services import sources as source_svc
from app.services.events import ADAPTER_FAILING, EventBus, get_event_bus

log = structlog.get_logger(__name__)

Sink = Callable[[AsyncSession, OpportunityIn, RawRecord], Awaitable[Any]]

MAX_ERRORS_KEPT = 50
# `adapter.failing` is published once the source has failed MORE than this many runs in a
# row (SPEC 5.1 nightly alerting; the ops channel subscribes in M4/M7).
FAILING_RUNS_THRESHOLD = 2


@dataclass(slots=True)
class RunResult:
    run_id: Any
    status: str
    fetched: int = 0
    upserted: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    watermark: datetime | None = None
    cursor: str | None = None
    since: datetime | None = None
    records: list[OpportunityIn] = field(default_factory=list)


def _error(stage: str, exc: BaseException, external_id: str | None = None) -> dict[str, Any]:
    return {
        "stage": stage,
        "external_id": external_id,
        "type": type(exc).__name__,
        "message": str(exc)[:500],
        "trace": "".join(traceback.format_exception_only(type(exc), exc))[-500:],
    }


async def collect_sink(session: AsyncSession, opp: OpportunityIn, raw: RawRecord) -> bool:
    """Sink that accepts every record without writing (dry runs, tests)."""
    return True


async def default_sink(session: AsyncSession, opp: OpportunityIn, raw: RawRecord) -> bool:
    from app.services.ingest import ingest_sink  # local import: ingest depends on models only

    return await ingest_sink(session, opp, raw)


async def run_source(
    session: AsyncSession,
    adapter: SourceAdapter,
    *,
    sink: Sink = default_sink,
    now: datetime | None = None,
    keep_records: bool = False,
    bus: EventBus | None = None,
) -> RunResult:
    now = now or datetime.now(UTC)
    bus = bus or get_event_bus()
    source = await source_svc.get_source(session, adapter.source_id)
    run = await source_svc.start_run(session, adapter.source_id, now=now)
    since = since_from_watermark(source.watermark_at, now=now)
    result = RunResult(
        run_id=run.id, status=source_svc.RUN_RUNNING, since=since, cursor=source.cursor
    )
    watermark = source.watermark_at
    cursor = source.cursor
    fetch_failed = False
    try:
        for raw in adapter.fetch(since, cursor):
            result.fetched += 1
            try:
                opp = adapter.normalize(raw)
                accepted = await sink(session, opp, raw)
            except Exception as exc:
                log.warning("source.record_failed", source=adapter.source_id, error=str(exc))
                if len(result.errors) < MAX_ERRORS_KEPT:
                    result.errors.append(_error("normalize", exc, raw.external_id))
                continue
            if accepted:
                result.upserted += 1
            if keep_records:
                result.records.append(opp)
            watermark = advance_watermark(watermark, opp.posted_at)
            if "cursor" in raw.meta:
                cursor = raw.meta["cursor"]
    except Exception as exc:
        fetch_failed = True
        log.error("source.fetch_failed", source=adapter.source_id, error=str(exc))
        result.errors.append(_error("fetch", exc))

    if fetch_failed:
        status = source_svc.RUN_FAILING
    elif result.errors:
        status = source_svc.RUN_DEGRADED
    else:
        status = source_svc.RUN_OK
    result.status = status
    result.watermark = watermark if not fetch_failed else source.watermark_at
    # A cursor only survives an aborted fetch: a completed run starts fresh next time
    # from the (advanced) watermark; a failed one resumes where it stopped.
    result.cursor = cursor if fetch_failed else None
    await source_svc.finish_run(
        session,
        run,
        status=status,
        fetched=result.fetched,
        upserted=result.upserted,
        errors=result.errors,
        watermark=result.watermark,
        cursor=result.cursor,
        now=datetime.now(UTC),
    )
    if status == source_svc.RUN_FAILING and source.consecutive_failures > FAILING_RUNS_THRESHOLD:
        health = adapter.health()
        await bus.publish(
            ADAPTER_FAILING,
            {
                "source_id": adapter.source_id,
                "run_id": str(run.id),
                "consecutive_failures": source.consecutive_failures,
                "health": health.status.value,
                "message": (result.errors[-1]["message"] if result.errors else health.message),
            },
        )
        log.error(
            "adapter.failing",
            source=adapter.source_id,
            consecutive_failures=source.consecutive_failures,
        )
    return result
