"""Status job entrypoint (M2-11). Celery beat calls `run_status_job` every 15 minutes
(wired in M2-16); `python -m app.jobs.status` runs it once.

    from app.jobs.status import run_status_job
    counts = run_status_job()           # {"closing_soon": 3, "closed": 1, ...}
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from typing import Any

from app.core.db import Database, get_database
from app.services.status_job import SCHEDULE, StatusRollResult, roll_status

__all__ = ["SCHEDULE", "roll_status_once", "run_status_job"]


async def roll_status_once(
    database: Database | None = None, now: datetime | None = None
) -> StatusRollResult:
    db = database or get_database()
    async with db.session(None) as session:
        return await roll_status(session, now)


def _as_dict(result: StatusRollResult) -> dict[str, Any]:
    return {
        "closing_soon": result.closing_soon,
        "closed": result.closed,
        "reopened": result.reopened,
        "propagated": result.propagated,
        "total": result.total,
    }


def run_status_job(now: datetime | None = None) -> dict[str, Any]:
    """Synchronous wrapper for Celery / CLI."""
    return _as_dict(asyncio.run(roll_status_once(now=now)))


def main(argv: list[str] | None = None) -> int:
    counts = run_status_job()
    sys.stdout.write(json.dumps(counts) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
