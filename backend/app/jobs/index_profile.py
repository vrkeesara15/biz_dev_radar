"""Knowledge-base indexing job (M1-12): re-index one profile after its sources changed.

    await index_profile_job(tenant_id, profile_id)            # from async code
    index_profile_sync(tenant_id, profile_id)                 # Celery task body (own loop)
    await schedule_reindex(session, tenant_id, profile_id)    # API hook (profile mutations)

The API hook: with CELERY_TASK_ALWAYS_EAGER (tests, single-process dev) the profile is
re-indexed inline inside the request's own session, so chunks commit atomically with the
mutation; otherwise the `bidradar.index_profile` Celery task is enqueued from an
`after_commit` listener on that session (never before the rows are visible to a worker),
falling back to an in-process task when no broker answers.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import structlog
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import Database, get_database
from app.services.embeddings import EmbeddingProvider, embeddings_available, get_embeddings
from app.services.knowledge_base import index_profile
from app.services.storage import StorageRouter, get_storage_router

log = structlog.get_logger(__name__)

INDEX_PROFILE_TASK = "bidradar.index_profile"


async def index_profile_job(
    tenant_id: uuid.UUID,
    profile_id: uuid.UUID,
    *,
    database: Database | None = None,
    embeddings: EmbeddingProvider | None = None,
    storage: StorageRouter | None = None,
    force: bool = False,
) -> dict[str, Any]:
    provider = embeddings or get_embeddings()
    if not embeddings_available(provider):
        log.warning("kb.index_skipped", profile_id=str(profile_id), reason="no embedding key")
        return {"profile_id": str(profile_id), "skipped": "embedding provider not configured"}
    db = database or get_database()
    async with db.session(tenant_id) as session:
        result = await index_profile(
            session,
            profile_id,
            embeddings=provider,
            storage=storage or get_storage_router(),
            force=force,
        )
    return result.as_dict()


async def _run_with_fresh_database(tenant_id: uuid.UUID, profile_id: uuid.UUID) -> dict[str, Any]:
    settings = get_settings()
    db = Database(settings.database_url, settings.database_url_owner)
    try:
        return await index_profile_job(
            tenant_id, profile_id, database=db, storage=StorageRouter(settings)
        )
    finally:
        await db.dispose()


def index_profile_sync(tenant_id: str, profile_id: str) -> dict[str, Any]:
    return asyncio.run(_run_with_fresh_database(uuid.UUID(tenant_id), uuid.UUID(profile_id)))


def enqueue_index_profile(tenant_id: uuid.UUID, profile_id: uuid.UUID) -> str | None:
    """Queue the Celery task; None when no broker answers (caller runs inline instead)."""
    from kombu.exceptions import OperationalError

    from app.celery_app import index_profile_task

    try:
        async_result = index_profile_task.apply_async(
            args=[str(tenant_id), str(profile_id)], retry=False, expires=3600
        )
    except (OperationalError, OSError, ConnectionError) as exc:
        log.warning("celery.broker_unreachable", error=str(exc)[:200])
        return None
    return str(async_result.id)


# fire-and-forget fallbacks keep a strong reference until they finish (RUF006)
_pending: set[asyncio.Task[Any]] = set()


def _spawn(coro: Any) -> None:
    task = asyncio.get_running_loop().create_task(coro)
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def schedule_reindex(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    profile_id: uuid.UUID,
    *,
    settings: Settings | None = None,
    embeddings: EmbeddingProvider | None = None,
    storage: StorageRouter | None = None,
) -> str:
    """Re-index after a knowledge-base source changed. Returns 'inline' (eager: indexed in
    `session` now), 'queued' (Celery task enqueued after commit) or 'skipped'."""
    settings = settings or get_settings()
    provider = embeddings or get_embeddings()
    if not embeddings_available(provider):
        log.warning("kb.index_skipped", profile_id=str(profile_id), reason="no embedding key")
        return "skipped"
    if settings.celery_task_always_eager:
        await index_profile(
            session, profile_id, embeddings=provider, storage=storage or get_storage_router()
        )
        return "inline"
    loop = asyncio.get_running_loop()

    def _after_commit(_sync_session: Any) -> None:
        if enqueue_index_profile(tenant_id, profile_id) is None:
            loop.call_soon(
                _spawn,
                index_profile_job(tenant_id, profile_id, embeddings=provider, storage=storage),
            )

    event.listen(session.sync_session, "after_commit", _after_commit, once=True)
    return "queued"
