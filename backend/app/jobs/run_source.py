"""Per-source job entrypoint (SPEC 10.1: one Cloud Run job per adapter).

    python -m app.jobs.run_source sam_opps          # prints the source_runs summary as JSON
    result = await run_source_job("grants_gov")     # from async code (admin "run now")
    run_source_sync("usaspending")                  # Celery task body (own loop + Database)

Steps: sync the `sources` rows from the registry (OQ-31), build the adapter, dispatch:
usaspending -> spend statistics job, sam_awards -> awards enrichment job, everything else
-> run_source with the ingest sink (summary enrichment subscribed when an LLM is
configured). Every path writes a source_runs row; `adapter.failing` is published by
run_source after more than two consecutive failing runs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Callable
from datetime import datetime
from typing import Any

import structlog

from app.adapters import registry
from app.adapters.base import SourceAdapter
from app.adapters.registry import AdapterNotFoundError, load_builtin_adapters
from app.core.config import Settings, get_settings
from app.core.db import Database, get_database
from app.services import sources as source_svc
from app.services.awards import run_awards_enrichment
from app.services.enrichment import install_enrichment
from app.services.events import EventBus, get_event_bus
from app.services.gem_extraction import install_gem_extraction
from app.services.matching.triggers import install_match_scoring
from app.services.opportunity_embeddings import install_opportunity_embeddings
from app.services.source_runner import RunResult, run_source
from app.services.spend import run_spend_stats
from app.services.storage import StorageRouter

log = structlog.get_logger(__name__)

SPEND_SOURCE = "usaspending"
AWARDS_SOURCE = "sam_awards"

AdapterFactory = Callable[[str], SourceAdapter]


def default_adapter_factory(source_id: str) -> SourceAdapter:
    load_builtin_adapters()
    cls = registry.get_adapter_class(source_id)
    if not registry.is_enabled(cls):
        raise RuntimeError(f"source {source_id!r} is disabled (documented stub)")
    return registry.create_adapter(source_id)


def _summary(source_id: str, result: RunResult, *, mode: str) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "run_id": str(result.run_id),
        "status": result.status,
        "fetched": result.fetched,
        "upserted": result.upserted,
        "errors": len(result.errors),
        "last_error": result.errors[-1]["message"] if result.errors else None,
        "watermark": result.watermark.isoformat() if result.watermark else None,
        "mode": mode,
    }


async def run_source_job(
    source_id: str,
    *,
    database: Database | None = None,
    adapter: SourceAdapter | None = None,
    adapter_factory: AdapterFactory | None = None,
    settings: Settings | None = None,
    bus: EventBus | None = None,
    now: datetime | None = None,
    mode: str = "inline",
) -> dict[str, Any]:
    settings = settings or get_settings()
    db = database or get_database()
    bus = bus or get_event_bus()
    factory = adapter_factory or default_adapter_factory  # resolved per call (tests patch it)
    adapter = adapter or factory(source_id)
    async with db.session(None) as session:
        await source_svc.sync_sources(session)
    run_result: RunResult
    async with db.session(None) as session:
        if adapter.source_id == SPEND_SOURCE:
            run_result = (await run_spend_stats(session, adapter, now=now)).run
        elif adapter.source_id == AWARDS_SOURCE:
            run_result = (await run_awards_enrichment(session, adapter, now=now)).run
        else:
            run_result = await run_source(session, adapter, now=now, bus=bus)
    summary = _summary(adapter.source_id, run_result, mode=mode)
    log.info("source.run_finished", **summary)
    return summary


async def _run_with_fresh_database(source_id: str, mode: str) -> dict[str, Any]:
    """A Database per invocation: worker tasks each run their own event loop."""
    settings = get_settings()
    db = Database(settings.database_url, settings.database_url_owner)
    try:
        # the ingest events need their subscribers in this process too (gem extraction,
        # then summary_ai so the summary sees the extracted eligibility)
        bus = EventBus()
        storage = StorageRouter(settings)
        install_gem_extraction(settings, db, storage, bus)
        install_enrichment(settings, db, storage, bus)
        install_opportunity_embeddings(settings, bus)
        # M4-06: the worker scores what it ingests (the run is queued or, without a
        # broker, executed in this process once the ingest transaction commits)
        trigger = install_match_scoring(settings, db, bus, storage=storage)
        result = await run_source_job(source_id, database=db, settings=settings, bus=bus, mode=mode)
        await trigger.drain()
        return result
    finally:
        await db.dispose()


def run_source_sync(source_id: str, *, mode: str = "worker") -> dict[str, Any]:
    return asyncio.run(_run_with_fresh_database(source_id, mode))


def enqueue_run(source_id: str) -> str | None:
    """Queue the Celery task; None when no broker answers (caller runs inline instead)."""
    from kombu.exceptions import OperationalError

    from app.celery_app import run_source_task

    try:
        async_result = run_source_task.apply_async(args=[source_id], retry=False, expires=3600)
    except (OperationalError, OSError, ConnectionError) as exc:
        log.warning("celery.broker_unreachable", error=str(exc)[:200])
        return None
    task_id: str = str(async_result.id)
    return task_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="run one source adapter end-to-end")
    parser.add_argument("source_id")
    args = parser.parse_args(argv)
    try:
        summary = run_source_sync(args.source_id, mode="cli")
    except (AdapterNotFoundError, RuntimeError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    sys.stdout.write(json.dumps(summary, default=str) + "\n")
    return 0 if summary["status"] != "failing" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
