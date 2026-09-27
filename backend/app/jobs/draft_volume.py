"""Celery body for one volume drafter (SPEC 8 agent 6).

`app.agents.drafters` fans the volumes out as a group of `bidradar.draft_volume` tasks
when AGENT_FANOUT=celery; each task drafts its volume with its own database connection
and its own LLM client, and returns the VolumeOut payload the step aggregates.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from app.agents.drafters import draft_volume
from app.agents.llm import llm_from_settings
from app.core.config import Settings, get_settings
from app.core.db import Database
from app.jobs.run_agents import NoLLM


async def draft_volume_job(
    tenant_id: uuid.UUID,
    pursuit_id: uuid.UUID,
    volume: str,
    *,
    database: Database | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    db = database or Database(settings.database_url, settings.database_url_owner)
    try:
        out = await draft_volume(
            db,
            tenant_id,
            pursuit_id,
            volume,
            llm=llm_from_settings(settings) or NoLLM(),
            settings=settings,
        )
    finally:
        if database is None:
            await db.dispose()
    return out.model_dump(mode="json")


def draft_volume_sync(tenant_id: str, pursuit_id: str, volume: str) -> dict[str, Any]:
    return asyncio.run(draft_volume_job(uuid.UUID(tenant_id), uuid.UUID(pursuit_id), volume))
