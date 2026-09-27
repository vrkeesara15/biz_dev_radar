"""Tenant export / erasure jobs (SPEC 11, M7-07).

    await tenant_export_job(tenant_id, request_id)      # from async code
    tenant_export_sync(tenant_id, request_id)           # Celery task body (own loop + DB)
    schedule_tenant_job(...)                            # API hook: eager inline or queued

Both jobs are long and destructive, so the API only ever records a data_requests row and
hands the work off: with CELERY_TASK_ALWAYS_EAGER (tests, single-process dev) the job runs
inline before the response, otherwise `bidradar.tenant_export` / `bidradar.tenant_delete`
are enqueued and the request stays `in_progress` until the worker finishes it.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import structlog

from app.core.config import Settings, get_settings
from app.core.db import Database, get_database
from app.core.privacy import DataRequestStatus
from app.models import DataRequest
from app.services.privacy import complete_request, delete_tenant, export_tenant
from app.services.storage import StorageRouter, get_storage_router

log = structlog.get_logger(__name__)

TENANT_EXPORT_TASK = "bidradar.tenant_export"
TENANT_DELETE_TASK = "bidradar.tenant_delete"


async def tenant_export_job(
    tenant_id: uuid.UUID,
    request_id: uuid.UUID,
    *,
    database: Database | None = None,
    storage_router: StorageRouter | None = None,
) -> dict[str, Any]:
    db = database or get_database()
    async with db.session(tenant_id) as session:
        request = await session.get(DataRequest, request_id)
        if request is None:
            log.warning("privacy.export_request_missing", request_id=str(request_id))
            return {"request_id": str(request_id), "skipped": "request not found"}
        return await export_tenant(
            session,
            tenant_id=tenant_id,
            request=request,
            storage_router=storage_router or get_storage_router(),
        )


async def tenant_delete_job(
    tenant_id: uuid.UUID,
    request_id: uuid.UUID,
    *,
    database: Database | None = None,
    storage_router: StorageRouter | None = None,
) -> dict[str, Any]:
    """Erase the tenant. The data_requests row goes with everything else, so its outcome
    is recorded in the retained audit_log row rather than on the request."""
    db = database or get_database()
    requested_by: uuid.UUID | None = None
    async with db.session(tenant_id) as session:
        request = await session.get(DataRequest, request_id)
        if request is None:
            log.warning("privacy.delete_request_missing", request_id=str(request_id))
            return {"request_id": str(request_id), "skipped": "request not found"}
        requested_by = request.user_id
        await complete_request(session, request, status=DataRequestStatus.IN_PROGRESS)
    async with db.owner_session() as owner:
        result = await delete_tenant(
            owner,
            tenant_id=tenant_id,
            storage_router=storage_router or get_storage_router(),
            requested_by=requested_by,
            request_id=request_id,
        )
    return {"request_id": str(request_id), **result}


async def _with_fresh_database(
    job: Any, tenant_id: uuid.UUID, request_id: uuid.UUID
) -> dict[str, Any]:
    settings = get_settings()
    db = Database(settings.database_url, settings.database_url_owner)
    try:
        return await job(  # type: ignore[no-any-return]
            tenant_id, request_id, database=db, storage_router=StorageRouter(settings)
        )
    finally:
        await db.dispose()


def tenant_export_sync(tenant_id: str, request_id: str) -> dict[str, Any]:
    return asyncio.run(
        _with_fresh_database(tenant_export_job, uuid.UUID(tenant_id), uuid.UUID(request_id))
    )


def tenant_delete_sync(tenant_id: str, request_id: str) -> dict[str, Any]:
    return asyncio.run(
        _with_fresh_database(tenant_delete_job, uuid.UUID(tenant_id), uuid.UUID(request_id))
    )


def enqueue_tenant_job(task_name: str, tenant_id: uuid.UUID, request_id: uuid.UUID) -> str | None:
    """Queue the Celery task; None when no broker answers (caller runs inline instead)."""
    from kombu.exceptions import OperationalError

    from app.celery_app import tenant_delete_task, tenant_export_task

    task = tenant_export_task if task_name == TENANT_EXPORT_TASK else tenant_delete_task
    try:
        async_result = task.apply_async(
            args=[str(tenant_id), str(request_id)], retry=False, expires=86400
        )
    except (OperationalError, OSError, ConnectionError) as exc:
        log.warning("celery.broker_unreachable", error=str(exc)[:200])
        return None
    return str(async_result.id)


async def schedule_tenant_job(
    task_name: str,
    tenant_id: uuid.UUID,
    request_id: uuid.UUID,
    *,
    settings: Settings | None = None,
    storage_router: StorageRouter | None = None,
) -> str:
    """Run the job inline (eager settings) or enqueue it. Returns 'inline' or 'queued'."""
    settings = settings or get_settings()
    job = tenant_export_job if task_name == TENANT_EXPORT_TASK else tenant_delete_job
    if settings.celery_task_always_eager:
        await job(tenant_id, request_id, storage_router=storage_router)
        return "inline"
    if enqueue_tenant_job(task_name, tenant_id, request_id) is None:
        await job(tenant_id, request_id, storage_router=storage_router)
        return "inline"
    return "queued"
