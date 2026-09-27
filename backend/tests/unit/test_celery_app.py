"""M2-16: Celery app, beat schedule from adapter cron strings, eager task wiring."""

from typing import Any

import pytest
from app.adapters.registry import load_builtin_adapters, registered
from app.celery_app import (
    FLUSH_SCHEDULED_TASK,
    ROLL_STATUS_TASK,
    RUN_SOURCE_TASK,
    SEND_DIGESTS_TASK,
    SEND_REMINDERS_TASK,
    build_beat_schedule,
    celery_app,
    create_celery,
    cron_to_crontab,
    roll_status_task,
    run_source_task,
)
from app.core.config import Settings
from celery.schedules import crontab

from tests.adapters.fixture_adapter import FixtureAdapter


class Disabled(FixtureAdapter):
    source_id = "disabled_stub"
    schedule = "0 0 1 1 *"
    enabled = False


class Weekly(FixtureAdapter):
    source_id = "weekly"
    schedule = "0 3 * * 1"


def test_cron_to_crontab() -> None:
    assert cron_to_crontab("*/30 * * * *") == crontab(minute="*/30")
    assert cron_to_crontab("0 3 * * 1") == crontab(minute="0", hour="3", day_of_week="1")
    assert cron_to_crontab("0 */2 * * *") == crontab(minute="0", hour="*/2")
    with pytest.raises(ValueError):
        cron_to_crontab("every 30 minutes")


def test_beat_schedule_from_registry_skips_disabled_and_adds_status() -> None:
    schedule = build_beat_schedule({"weekly": Weekly, "disabled_stub": Disabled})
    assert set(schedule) == {
        "source:weekly",
        "status:roll",
        # M4-13: the notification beat
        "notify:digests",
        "notify:flush",
        # M6-03: the deadline reminder ladder
        "pursuits:reminders",
    }
    weekly = schedule["source:weekly"]
    assert weekly["task"] == RUN_SOURCE_TASK and weekly["args"] == ("weekly",)
    assert weekly["schedule"] == crontab(minute="0", hour="3", day_of_week="1")
    assert schedule["status:roll"]["task"] == ROLL_STATUS_TASK
    assert schedule["status:roll"]["schedule"] == crontab(minute="*/15")
    assert schedule["notify:digests"]["task"] == SEND_DIGESTS_TASK
    assert schedule["notify:digests"]["schedule"] == crontab(minute="*/15")
    assert schedule["notify:flush"]["task"] == FLUSH_SCHEDULED_TASK
    assert schedule["notify:flush"]["schedule"] == crontab(minute="*/5")
    assert schedule["pursuits:reminders"]["task"] == SEND_REMINDERS_TASK
    assert schedule["pursuits:reminders"]["schedule"] == crontab(minute="*/5")


def test_default_schedule_covers_every_enabled_builtin_adapter() -> None:
    load_builtin_adapters()
    schedule = celery_app.conf.beat_schedule
    for source_id, cls in registered().items():
        key = f"source:{source_id}"
        if getattr(cls, "enabled", True):
            assert key in schedule, key
            assert schedule[key]["schedule"] == cron_to_crontab(cls.schedule)
        else:
            assert key not in schedule
    assert "status:roll" in schedule


def test_celery_config_redis_broker_no_result_backend() -> None:
    settings = Settings(
        _env_file=None, redis_url="redis://example:6379/3", celery_task_always_eager=True
    )  # type: ignore[call-arg]
    app = create_celery(settings)
    assert app.conf.broker_url == "redis://example:6379/3"
    assert app.conf.result_backend is None and app.conf.task_ignore_result is True
    assert app.conf.task_always_eager is True and app.conf.timezone == "UTC"
    assert celery_app.conf.task_always_eager is False  # never eager by default


def test_tasks_run_eagerly_over_the_job_entrypoints(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []
    monkeypatch.setattr(
        "app.jobs.run_source.run_source_sync",
        lambda source_id, mode="worker": (
            calls.append(("run", source_id)) or {"source_id": source_id, "status": "ok"}
        ),
    )
    monkeypatch.setattr(
        "app.jobs.status.run_status_job", lambda: calls.append(("status",)) or {"total": 0}
    )
    result = run_source_task.apply(args=["sam_opps"])
    assert result.successful() and result.result == {"source_id": "sam_opps", "status": "ok"}
    status = roll_status_task.apply()
    assert status.successful() and status.result == {"total": 0}
    assert calls == [("run", "sam_opps"), ("status",)]
    assert (
        run_source_task.name == "bidradar.run_source"
        and roll_status_task.name == "bidradar.roll_status"
    )
