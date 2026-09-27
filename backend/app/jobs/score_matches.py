"""Scoring jobs (M4-06): the Celery bodies and CLI behind app.services.matching.engine.

    bidradar.score_opportunity   one new / amended notice vs every eligible profile
    bidradar.rescore_profile     one changed profile vs the open corpus
    bidradar.score_batch         everything (nightly catch-up and the load script)

    python -m app.jobs.score_matches opportunity <uuid>
    python -m app.jobs.score_matches profile <tenant uuid> <profile uuid>
    python -m app.jobs.score_matches batch [--tenant <uuid>] [--region us|in]

Each sync wrapper runs `asyncio.run` over a FRESH Database (asyncpg pools belong to the
loop that made them), exactly like the ingest and notification jobs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from typing import Any

import structlog

from app.agents.llm import LLMClient, llm_from_settings
from app.core.config import Settings, get_settings
from app.core.db import Database, get_database
from app.services.embeddings import EmbeddingProvider, embeddings_from_settings
from app.services.events import EventBus
from app.services.matching.engine import MatchScorer
from app.services.matching.rationale import RationaleGenerator
from app.services.storage import StorageRouter

log = structlog.get_logger(__name__)

SCORE_OPPORTUNITY_TASK = "bidradar.score_opportunity"
RESCORE_PROFILE_TASK = "bidradar.rescore_profile"
SCORE_BATCH_TASK = "bidradar.score_batch"


def build_scorer(
    settings: Settings,
    database: Database,
    *,
    embeddings: EmbeddingProvider | None = None,
    storage: StorageRouter | None = None,
    llm: LLMClient | None = None,
    bus: EventBus | None = None,
    with_rationale: bool = True,
) -> MatchScorer:
    """A scorer wired to this process's providers; stage 3 is skipped without an LLM."""
    client = llm if llm is not None else llm_from_settings(settings)
    rationale = (
        RationaleGenerator(llm=client, settings=settings, database=database, storage=storage)
        if with_rationale and client is not None
        else None
    )
    return MatchScorer(
        settings=settings,
        database=database,
        embeddings=embeddings or embeddings_from_settings(settings),
        rationale=rationale,
        bus=bus,
    )


async def score_opportunity_job(
    opportunity_id: uuid.UUID,
    *,
    database: Database | None = None,
    settings: Settings | None = None,
    scorer: MatchScorer | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    db = database or get_database()
    runner = scorer or build_scorer(settings, db, storage=StorageRouter(settings))
    result = await runner.score_opportunity(opportunity_id)
    log.info("matching.scored_opportunity", opportunity_id=str(opportunity_id), **result.as_dict())
    return result.as_dict()


async def rescore_profile_job(
    tenant_id: uuid.UUID,
    profile_id: uuid.UUID,
    *,
    database: Database | None = None,
    settings: Settings | None = None,
    scorer: MatchScorer | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    db = database or get_database()
    runner = scorer or build_scorer(settings, db, storage=StorageRouter(settings))
    result = await runner.rescore_profile(tenant_id, profile_id)
    log.info("matching.rescored_profile", profile_id=str(profile_id), **result.as_dict())
    return result.as_dict()


async def score_batch_job(
    *,
    tenant_id: uuid.UUID | None = None,
    region: str | None = None,
    database: Database | None = None,
    settings: Settings | None = None,
    scorer: MatchScorer | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    db = database or get_database()
    runner = scorer or build_scorer(settings, db, storage=StorageRouter(settings))
    result = await runner.score_batch(tenant_id=tenant_id, region=region)
    log.info("matching.scored_batch", **result.as_dict())
    return result.as_dict()


async def _with_fresh_database(what: str, **kwargs: Any) -> dict[str, Any]:
    settings = get_settings()
    db = Database(settings.database_url, settings.database_url_owner)
    try:
        if what == "opportunity":
            return await score_opportunity_job(database=db, settings=settings, **kwargs)
        if what == "profile":
            return await rescore_profile_job(database=db, settings=settings, **kwargs)
        return await score_batch_job(database=db, settings=settings, **kwargs)
    finally:
        await db.dispose()


def score_opportunity_sync(opportunity_id: str) -> dict[str, Any]:
    return asyncio.run(
        _with_fresh_database("opportunity", opportunity_id=uuid.UUID(opportunity_id))
    )


def rescore_profile_sync(tenant_id: str, profile_id: str) -> dict[str, Any]:
    return asyncio.run(
        _with_fresh_database(
            "profile", tenant_id=uuid.UUID(tenant_id), profile_id=uuid.UUID(profile_id)
        )
    )


def score_batch_sync(tenant_id: str | None = None, region: str | None = None) -> dict[str, Any]:
    return asyncio.run(
        _with_fresh_database(
            "batch",
            tenant_id=uuid.UUID(tenant_id) if tenant_id else None,
            region=region,
        )
    )


# --- enqueue helpers ---------------------------------------------------------------------


def _enqueue(task_name: str, args: list[str]) -> str | None:
    """Queue a Celery task; None when no broker answers (the caller runs it inline)."""
    from kombu.exceptions import OperationalError

    from app.celery_app import celery_app

    try:
        async_result = celery_app.send_task(task_name, args=args, retry=False, expires=3600)
    except (OperationalError, OSError, ConnectionError) as exc:
        log.warning("celery.broker_unreachable", task=task_name, error=str(exc)[:200])
        return None
    return str(async_result.id)


def enqueue_score_opportunity(opportunity_id: uuid.UUID) -> str | None:
    return _enqueue(SCORE_OPPORTUNITY_TASK, [str(opportunity_id)])


def enqueue_rescore_profile(tenant_id: uuid.UUID, profile_id: uuid.UUID) -> str | None:
    return _enqueue(RESCORE_PROFILE_TASK, [str(tenant_id), str(profile_id)])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="score matches (SPEC 6)")
    sub = parser.add_subparsers(dest="what", required=True)
    one = sub.add_parser("opportunity")
    one.add_argument("opportunity_id")
    prof = sub.add_parser("profile")
    prof.add_argument("tenant_id")
    prof.add_argument("profile_id")
    batch = sub.add_parser("batch")
    batch.add_argument("--tenant", default=None)
    batch.add_argument("--region", default=None, choices=["us", "in"])
    args = parser.parse_args(argv)
    if args.what == "opportunity":
        summary = score_opportunity_sync(args.opportunity_id)
    elif args.what == "profile":
        summary = rescore_profile_sync(args.tenant_id, args.profile_id)
    else:
        summary = score_batch_sync(args.tenant, args.region)
    sys.stdout.write(json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
