"""Weekly keyword re-tune (M4-07, SPEC 6 learning loop).

    bidradar.retune_keywords    Mondays: turn 90 days of thumbs into pending
                                keyword_suggestions for each profile that got feedback

    python -m app.jobs.retune_keywords

Suggestions are never applied here; an owner or bid manager approves them through
PUT /api/v1/profiles/{id}/keyword-suggestions/{id}.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.db import Database, get_database
from app.models import Match, MatchFeedback
from app.services.matching.learning import FEEDBACK_WINDOW_DAYS, retune_profile

log = structlog.get_logger(__name__)

# Mondays at 06:15 UTC, after the weekly notification roll-up has gone out.
RETUNE_SCHEDULE = "15 6 * * 1"


@dataclass(frozen=True, slots=True)
class RetuneRun:
    tenants: int = 0
    profiles: int = 0
    suggestions: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "tenants": self.tenants,
            "profiles": self.profiles,
            "suggestions": self.suggestions,
        }


async def _profiles_with_feedback(
    database: Database, since: datetime
) -> dict[uuid.UUID, list[uuid.UUID]]:
    """{tenant: [profile, ...]} for every profile with a thumb in the window.

    Read with the OWNER role because the set crosses tenants; the re-tune itself then
    runs inside each tenant's own session.
    """
    async with database.owner_session() as session:
        rows = (
            await session.execute(
                select(Match.tenant_id, Match.profile_id)
                .join(MatchFeedback, MatchFeedback.match_id == Match.id)
                .where(MatchFeedback.created_at >= since)
                .distinct()
            )
        ).all()
    out: dict[uuid.UUID, list[uuid.UUID]] = {}
    for tenant_id, profile_id in rows:
        out.setdefault(tenant_id, []).append(profile_id)
    return out


async def retune_keywords_job(
    *,
    database: Database | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
    window_days: int = FEEDBACK_WINDOW_DAYS,
) -> dict[str, Any]:
    _ = settings or get_settings()
    db = database or get_database()
    moment = now or datetime.now(UTC)
    since = moment - timedelta(days=window_days)
    by_tenant = await _profiles_with_feedback(db, since)
    tenants = profiles = suggestions = 0
    for tenant_id, profile_ids in by_tenant.items():
        tenants += 1
        async with db.session(tenant_id) as session:
            for profile_id in profile_ids:
                profiles += 1
                written = await retune_profile(
                    session, tenant_id, profile_id, now=moment, window_days=window_days
                )
                suggestions += len(written)
    run = RetuneRun(tenants=tenants, profiles=profiles, suggestions=suggestions)
    log.info("matching.retune_finished", **run.as_dict())
    return run.as_dict()


async def _with_fresh_database() -> dict[str, Any]:
    settings = get_settings()
    db = Database(settings.database_url, settings.database_url_owner)
    try:
        return await retune_keywords_job(database=db, settings=settings)
    finally:
        await db.dispose()


def retune_keywords_sync() -> dict[str, Any]:
    return asyncio.run(_with_fresh_database())


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI entry point
    _ = argv
    sys.stdout.write(json.dumps(retune_keywords_sync(), default=str) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
