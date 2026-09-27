"""Event subscribers that start scoring (M4-06).

    trigger = install_match_scoring(settings, database, bus, embeddings=..., storage=...)

`opportunity.created` / `opportunity.amended` (ingest) and `profile.changed` (the
profiles API) each schedule a scoring run. Scoring is scheduled for AFTER the publisher's
transaction commits, never inside it: the ingest subscribers run on the ingest session
while the notice is still uncommitted and a scorer opens its own tenant sessions, which
would see nothing. The hook therefore rides SQLAlchemy's `after_commit` on the publisher's
session (the same shape the knowledge-base re-index uses, M1-12 / OQ-52):

  * a Celery broker answers          -> `bidradar.score_opportunity` / `.rescore_profile`
  * eager mode or no broker answers  -> an in-process task on the running loop

Tests await `trigger.drain()` so the in-process path is deterministic.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

import structlog
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.llm import LLMClient
from app.core.config import Settings, get_settings
from app.core.db import Database, get_database
from app.jobs.score_matches import (
    build_scorer,
    enqueue_rescore_profile,
    enqueue_score_opportunity,
)
from app.services.embeddings import EmbeddingProvider
from app.services.events import (
    OPPORTUNITY_AMENDED,
    OPPORTUNITY_CREATED,
    PROFILE_CHANGED,
    Event,
    EventBus,
)
from app.services.matching.engine import MatchScorer
from app.services.storage import StorageRouter

log = structlog.get_logger(__name__)

Job = Callable[[], Coroutine[Any, Any, Any]]


class MatchTrigger:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        database: Database | None = None,
        scorer: MatchScorer | None = None,
        embeddings: EmbeddingProvider | None = None,
        storage: StorageRouter | None = None,
        llm: LLMClient | None = None,
        bus: EventBus | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.database = database or get_database()
        self.scorer = scorer or build_scorer(
            self.settings,
            self.database,
            embeddings=embeddings,
            storage=storage,
            llm=llm,
            bus=bus,
        )
        self._pending: set[asyncio.Task[Any]] = set()
        self.scheduled: list[tuple[str, str]] = []

    # -- subscription -------------------------------------------------------------------

    def subscribe(self, bus: EventBus) -> MatchTrigger:
        bus.subscribe(OPPORTUNITY_CREATED, self.on_opportunity)
        bus.subscribe(OPPORTUNITY_AMENDED, self.on_opportunity)
        bus.subscribe(PROFILE_CHANGED, self.on_profile_changed)
        return self

    async def on_opportunity(self, event_: Event) -> None:
        opportunity_id = uuid.UUID(str(event_.payload["opportunity_id"]))
        self.scheduled.append(("opportunity", str(opportunity_id)))
        self._schedule(
            event_.context.get("session"),
            lambda: enqueue_score_opportunity(opportunity_id),
            lambda: self.scorer.score_opportunity(opportunity_id),
        )

    async def on_profile_changed(self, event_: Event) -> None:
        tenant_id = uuid.UUID(str(event_.payload["tenant_id"]))
        profile_id = uuid.UUID(str(event_.payload["profile_id"]))
        self.scheduled.append(("profile", str(profile_id)))
        self._schedule(
            event_.context.get("session"),
            lambda: enqueue_rescore_profile(tenant_id, profile_id),
            lambda: self.scorer.rescore_profile(tenant_id, profile_id),
        )

    # -- scheduling ---------------------------------------------------------------------

    def _schedule(
        self, session: AsyncSession | None, enqueue: Callable[[], str | None], job: Job
    ) -> str:
        if session is None:  # the publisher already committed
            return self._start(enqueue, job)
        loop = asyncio.get_running_loop()

        def _after_commit(_sync_session: Any) -> None:
            loop.call_soon(self._start, enqueue, job)

        event.listen(session.sync_session, "after_commit", _after_commit, once=True)
        return "after_commit"

    def _start(self, enqueue: Callable[[], str | None], job: Job) -> str:
        # Celery's eager mode runs the task body inline, and that body calls asyncio.run,
        # which cannot nest inside a running loop: run the coroutine here instead.
        if not self.settings.celery_task_always_eager and enqueue() is not None:
            return "queued"
        task = asyncio.get_running_loop().create_task(_guard(job()))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)
        return "inline"

    async def drain(self, *, passes: int = 5) -> None:
        """Await every in-process scoring task (tests; never used in production)."""
        for _ in range(passes):
            await asyncio.sleep(0)
            if self._pending:
                await asyncio.gather(*list(self._pending))


async def _guard(coro: Coroutine[Any, Any, Any]) -> None:
    try:
        await coro
    except Exception as exc:  # a scoring failure must never break ingest
        log.error("matching.scoring_failed", error=str(exc)[:400])


def install_match_scoring(
    settings: Settings,
    database: Database,
    bus: EventBus,
    *,
    embeddings: EmbeddingProvider | None = None,
    storage: StorageRouter | None = None,
    llm: LLMClient | None = None,
) -> MatchTrigger:
    """Subscribe scoring to the ingest and profile events of this process."""
    return MatchTrigger(
        settings=settings,
        database=database,
        embeddings=embeddings,
        storage=storage,
        llm=llm,
        bus=bus,
    ).subscribe(bus)
