"""Agent fan-out (SPEC 8 agent 6: "one drafter per volume runs in parallel").

Two ways to run the per-volume drafters, chosen by `AGENT_FANOUT`:

- inline (default, and always under CELERY_TASK_ALWAYS_EAGER): the volumes run as
  concurrent coroutines in this process, bounded by AGENT_FANOUT_CONCURRENCY, sharing
  the step's metered LLM client so their tokens land on the agent_steps row,
- celery: each volume is a `bidradar.draft_volume` task in one Celery group, so a big
  package spreads over the worker pool. The group is awaited in a thread; each task
  meters its own usage_ledger rows (OQ-104).

`fan_out` keeps the input order, so the caller can zip results back onto its jobs.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import structlog

log = structlog.get_logger(__name__)

FANOUT_INLINE = "inline"
FANOUT_CELERY = "celery"
FANOUT_MODES: tuple[str, ...] = (FANOUT_INLINE, FANOUT_CELERY)


async def fan_out[T, R](
    items: Sequence[T], worker: Callable[[T], Awaitable[R]], *, concurrency: int = 3
) -> list[R]:
    """Run `worker` over every item concurrently (at most `concurrency` at a time).

    Results come back in the order of `items`. The first exception propagates once the
    other coroutines have finished, so a failing volume fails the step.
    """
    if not items:
        return []
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def guarded(item: T) -> R:
        async with semaphore:
            return await worker(item)

    return list(await asyncio.gather(*(guarded(item) for item in items)))


def enqueue_group(args_list: Sequence[Sequence[Any]]) -> Any | None:
    """Queue one `bidradar.draft_volume` task per volume as a Celery group.

    Returns the GroupResult, or None when no broker answers (the caller runs inline).
    """
    from kombu.exceptions import OperationalError

    from app.celery_app import draft_volume_task

    try:
        from celery import group

        result = group(draft_volume_task.s(*args) for args in args_list).apply_async(
            retry=False, expires=3600
        )
    except (OperationalError, OSError, ConnectionError) as exc:
        log.warning("celery.broker_unreachable", error=str(exc)[:200])
        return None
    return result


async def await_group(
    result: Any,
    *,
    timeout: float = 900.0,  # noqa: ASYNC109 - Celery's own result timeout, not asyncio's
) -> list[Any]:
    """Wait for a Celery group's results without blocking the event loop."""
    return list(await asyncio.to_thread(result.get, timeout=timeout, disable_sync_subtasks=False))
