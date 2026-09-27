"""Celery application (SPEC 10.1): Redis broker, no result backend, beat schedule built
from every enabled adapter's cron string plus the 15-minute status job.

    celery -A app.celery_app worker -l info
    celery -A app.celery_app beat -l info

Tasks are thin sync wrappers around the async job entrypoints in app/jobs (each task runs
its own event loop and a fresh Database, so worker processes never share asyncpg
connections across loops). Tests use `task.apply()` / CELERY_TASK_ALWAYS_EAGER=true;
nothing here needs a running broker at import time.
"""

from __future__ import annotations

from typing import Any

from celery import Celery
from celery.schedules import crontab

from app.adapters import registry
from app.adapters.registry import load_builtin_adapters
from app.core.config import Settings, get_settings
from app.services.status_job import SCHEDULE as STATUS_SCHEDULE

RUN_SOURCE_TASK = "bidradar.run_source"
ROLL_STATUS_TASK = "bidradar.roll_status"
INDEX_PROFILE_TASK = "bidradar.index_profile"


def cron_to_crontab(expression: str) -> crontab:
    """5-field cron ('m h dom mon dow') -> celery crontab."""
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError(f"schedule must have 5 cron fields, got {expression!r}")
    minute, hour, day_of_month, month_of_year, day_of_week = fields
    return crontab(
        minute=minute,
        hour=hour,
        day_of_month=day_of_month,
        month_of_year=month_of_year,
        day_of_week=day_of_week,
    )


def build_beat_schedule(adapters: dict[str, type[Any]] | None = None) -> dict[str, dict[str, Any]]:
    """One beat entry per ENABLED adapter (its own cron) + the status roll every 15 min."""
    if adapters is None:
        load_builtin_adapters()
        adapters = registry.registered()
    schedule: dict[str, dict[str, Any]] = {}
    for source_id, cls in adapters.items():
        if not registry.is_enabled(cls):
            continue
        schedule[f"source:{source_id}"] = {
            "task": RUN_SOURCE_TASK,
            "schedule": cron_to_crontab(cls.schedule),
            "args": (source_id,),
            "options": {"expires": 3600},
        }
    schedule["status:roll"] = {
        "task": ROLL_STATUS_TASK,
        "schedule": cron_to_crontab(STATUS_SCHEDULE),
        "options": {"expires": 900},
    }
    return schedule


def create_celery(settings: Settings | None = None) -> Celery:
    settings = settings or get_settings()
    app = Celery("bidradar", broker=settings.redis_url)
    app.conf.update(
        task_ignore_result=True,
        result_backend=None,
        task_always_eager=settings.celery_task_always_eager,
        task_eager_propagates=True,
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        task_default_queue="bidradar",
        broker_connection_retry_on_startup=True,
        broker_connection_timeout=settings.celery_broker_connect_timeout,
        timezone="UTC",
        enable_utc=True,
        beat_schedule=build_beat_schedule(),
    )
    return app


celery_app = create_celery()


@celery_app.task(name=RUN_SOURCE_TASK, bind=True, max_retries=0)  # type: ignore[untyped-decorator]
def run_source_task(self: Any, source_id: str) -> dict[str, Any]:
    from app.jobs.run_source import run_source_sync

    return run_source_sync(source_id)


@celery_app.task(name=ROLL_STATUS_TASK, bind=True, max_retries=0)  # type: ignore[untyped-decorator]
def roll_status_task(self: Any) -> dict[str, Any]:
    from app.jobs.status import run_status_job

    return run_status_job()


@celery_app.task(name=INDEX_PROFILE_TASK, bind=True, max_retries=0)  # type: ignore[untyped-decorator]
def index_profile_task(self: Any, tenant_id: str, profile_id: str) -> dict[str, Any]:
    """Re-index one profile's knowledge base (M1-12); queued by profile mutations."""
    from app.jobs.index_profile import index_profile_sync

    return index_profile_sync(tenant_id, profile_id)
