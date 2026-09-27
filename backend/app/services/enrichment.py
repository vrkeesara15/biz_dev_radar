"""Opportunity enrichment subscribers (M2-13): summary_ai on create / amend.

    enricher = install_enrichment(settings, database, storage_router, bus)   # None w/o LLM
    enricher = SummaryEnricher(llm=fake_llm, database=database, storage=router).subscribe(bus)

The subscriber runs inside the ingest transaction (event.context["session"]) so it sees
the uncommitted row and its summary commits together with the notice. The LLM call is
metered through an AgentRunner under the INTERNAL tenant (opportunities are global, so
their enrichment cost is the platform's: OQ-47). Cached per (opportunity_id, version):
`summary_version == version` means no call.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents.llm import LLMClient, llm_from_settings
from app.agents.runner import AgentRunner, StepContext, StepSpec
from app.agents.summarize import DocumentExcerpt, FiveLineSummary, summarize_opportunity
from app.agents.tracing import Tracer, tracer_from_settings
from app.core.config import Settings, get_settings
from app.core.db import Database, get_database
from app.models import Opportunity, Tenant
from app.services.documents import load_parsed_text
from app.services.events import OPPORTUNITY_AMENDED, OPPORTUNITY_CREATED, Event, EventBus
from app.services.storage import StorageRouter

log = structlog.get_logger(__name__)

INTERNAL_TENANT_SLUG = "internal"
SUMMARY_RUN_KIND = "summary_ai"
SUMMARY_STEP = "summarize"


async def internal_tenant_id(database: Database) -> uuid.UUID | None:
    """The platform tenant that carries global enrichment cost (owner role: RLS hides
    tenants from a session without tenant context)."""
    async with database.owner_session() as session:
        row = (
            await session.execute(select(Tenant.id).where(Tenant.slug == INTERNAL_TENANT_SLUG))
        ).scalar_one_or_none()
    return row


class SummaryEnricher:
    def __init__(
        self,
        *,
        llm: LLMClient,
        database: Database | None = None,
        storage: StorageRouter | None = None,
        settings: Settings | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        self.llm = llm
        self.database = database or get_database()
        self.storage = storage
        self.settings = settings or get_settings()
        self.tracer = tracer
        self._tenant_id: uuid.UUID | None = None
        self._tenant_checked = False
        self.summarised: list[tuple[uuid.UUID, int]] = []

    def subscribe(self, bus: EventBus) -> SummaryEnricher:
        bus.subscribe(OPPORTUNITY_CREATED, self.on_event)
        bus.subscribe(OPPORTUNITY_AMENDED, self.on_event)
        return self

    async def _billing_tenant(self) -> uuid.UUID | None:
        if not self._tenant_checked:
            self._tenant_id = await internal_tenant_id(self.database)
            self._tenant_checked = True
            if self._tenant_id is None:
                log.warning("enrichment.no_internal_tenant", slug=INTERNAL_TENANT_SLUG)
        return self._tenant_id

    async def on_event(self, event: Event) -> None:
        session: AsyncSession | None = event.context.get("session")
        if session is None:
            log.warning("enrichment.no_session", event=event.name)
            return
        opportunity_id = uuid.UUID(event.payload["opportunity_id"])
        await self.summarize(session, opportunity_id)

    async def summarize(self, session: AsyncSession, opportunity_id: uuid.UUID) -> str | None:
        """Generate (or reuse) summary_ai for the row; returns the summary text."""
        row = (
            await session.execute(
                select(Opportunity)
                .options(selectinload(Opportunity.documents))
                .where(Opportunity.id == opportunity_id)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        if row.summary_ai and row.summary_version == row.version:
            return row.summary_ai  # cached for this version
        tenant_id = await self._billing_tenant()
        if tenant_id is None:
            return None
        documents = await self._excerpts(row)
        version = row.version

        async def step(ctx: StepContext) -> FiveLineSummary:
            result = await summarize_opportunity(
                ctx.llm,
                settings=self.settings,
                title=row.title,
                description=row.description_text,
                documents=documents,
                buyer=" / ".join(row.buyer_hierarchy) or row.buyer_org,
                facts=_facts(row),
            )
            parsed: FiveLineSummary = result.parsed
            return parsed

        runner = AgentRunner(
            self.database,
            tenant_id=tenant_id,
            llm=self.llm,
            tracer=self.tracer or tracer_from_settings(self.settings),
        )
        run_id = await runner.start(
            kind=SUMMARY_RUN_KIND,
            params={"opportunity_id": str(row.id), "version": version},
        )
        result = await runner.run(
            run_id, [StepSpec(SUMMARY_STEP, step, input_ref=f"opportunity:{row.id}@{version}")]
        )
        if result.status != "done":
            log.warning("enrichment.summary_failed", opportunity_id=str(row.id), error=result.error)
            return None
        lines = result.outputs[SUMMARY_STEP]["lines"]
        row.summary_ai = "\n".join(lines)
        row.summary_version = version
        await session.flush()
        self.summarised.append((row.id, version))
        return row.summary_ai

    async def _excerpts(self, row: Opportunity) -> list[DocumentExcerpt]:
        if self.storage is None:
            return []
        storage = self.storage.for_region(row.region)
        excerpts: list[DocumentExcerpt] = []
        for doc in row.documents:
            if not doc.parsed_text_ref:
                continue
            try:
                pages = await load_parsed_text(storage, doc)
            except Exception as exc:  # a missing object must not block the summary
                log.warning(
                    "enrichment.parsed_text_missing", document_id=str(doc.id), error=str(exc)
                )
                continue
            excerpts.append(DocumentExcerpt(name=doc.file_name or doc.url, pages=pages))
        return excerpts


def _facts(row: Opportunity) -> dict[str, Any]:
    return {
        "notice_type": row.notice_type.value,
        "solicitation_number": row.solicitation_number,
        "naics": ", ".join(row.naics) or None,
        "set_aside": row.set_aside,
        "reservation": row.reservation,
        "estimated_value_min": row.estimated_value_min,
        "estimated_value_max": row.estimated_value_max,
        "currency": row.currency,
        "emd_amount": row.emd_amount,
        "tender_fee": row.tender_fee,
        "questions_due_at": row.questions_due_at,
        "prebid_meeting_at": row.prebid_meeting_at,
        "response_due_at": row.response_due_at,
        "place_of_performance": row.place_of_performance,
        "eligibility": row.eligibility or None,
    }


def install_enrichment(
    settings: Settings,
    database: Database,
    storage: StorageRouter,
    bus: EventBus,
    *,
    llm: LLMClient | None = None,
) -> SummaryEnricher | None:
    """Subscribe summary enrichment when an LLM is available (API key or injected client)."""
    client = llm or llm_from_settings(settings)
    if client is None:
        log.info("enrichment.disabled", reason="no LLM configured")
        return None
    return SummaryEnricher(
        llm=client, database=database, storage=storage, settings=settings
    ).subscribe(bus)
