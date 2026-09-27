"""GeM bid-PDF extraction subscriber (SPEC 5.2, 5.3, 8; M3-04).

    extractor = install_gem_extraction(settings, database, storage_router, bus)  # None w/o LLM
    extractor = GemBidExtractor(llm=fake_llm, database=database, storage=router).subscribe(bus)

When a `gem` opportunity is created and its bid PDF has already been parsed
(`documents.parsed_text_ref`), one Opus-class extraction step (`app/agents/gem_extract.py`)
reads the pages and the result is written onto the row:

    eligibility      CriteriaIn.from_dict keys (turnover, experience, EMD, MSE / Startup
                     exemptions) plus item/quantity/value/end date/consignees and the
                     per-field page citations
    estimated_value_min / _max, emd_amount      parsed with core.money.parse_inr
    response_due_at  filled from the document only when the listing gave none

Like the summary enricher it runs inside the ingest transaction on the event's session
and is metered through an AgentRunner under the INTERNAL tenant (OQ-47). An answer that
never validates raises InvalidOutput inside the step: the run is `failed`, nothing is
written and `extra.gem_extraction.status = "flagged"` records it for review (SPEC 8).
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents.gem_extract import GemBidExtraction, extract_gem_bid
from app.agents.llm import LLMClient, llm_from_settings
from app.agents.runner import AgentRunner, StepContext, StepSpec
from app.agents.tracing import Tracer, tracer_from_settings
from app.core.config import Settings, get_settings
from app.core.db import Database, get_database
from app.core.money import to_usd_or_none
from app.core.normalize.gem import SOURCE_ID as GEM_SOURCE_ID
from app.models import Opportunity
from app.services.documents import load_parsed_text
from app.services.enrichment import internal_tenant_id
from app.services.events import OPPORTUNITY_CREATED, Event, EventBus
from app.services.storage import StorageRouter

log = structlog.get_logger(__name__)

EXTRACT_RUN_KIND = "gem_extract"
EXTRACT_STEP = "extract"
EXTRA_KEY = "gem_extraction"

STATUS_OK = "ok"
STATUS_FLAGGED = "flagged"
STATUS_NO_DOCUMENT = "no_parsed_document"


class GemBidExtractor:
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
        self.extracted: list[uuid.UUID] = []

    def subscribe(self, bus: EventBus) -> GemBidExtractor:
        bus.subscribe(OPPORTUNITY_CREATED, self.on_event)
        return self

    async def _billing_tenant(self) -> uuid.UUID | None:
        if not self._tenant_checked:
            self._tenant_id = await internal_tenant_id(self.database)
            self._tenant_checked = True
        return self._tenant_id

    async def on_event(self, event: Event) -> None:
        session: AsyncSession | None = event.context.get("session")
        if session is None:
            log.warning("gem_extract.no_session", event=event.name)
            return
        if event.payload.get("source_id") not in (None, GEM_SOURCE_ID):
            return
        await self.extract(session, uuid.UUID(event.payload["opportunity_id"]))

    async def extract(
        self, session: AsyncSession, opportunity_id: uuid.UUID
    ) -> GemBidExtraction | None:
        row = (
            await session.execute(
                select(Opportunity)
                .options(selectinload(Opportunity.documents))
                .where(Opportunity.id == opportunity_id)
            )
        ).scalar_one_or_none()
        if row is None or row.source_id != GEM_SOURCE_ID:
            return None
        pages = await self._pages(row)
        if not pages:
            log.info("gem_extract.skipped", opportunity_id=str(row.id), reason=STATUS_NO_DOCUMENT)
            self._note(row, {"status": STATUS_NO_DOCUMENT})
            return None
        tenant_id = await self._billing_tenant()
        if tenant_id is None:
            log.warning("gem_extract.no_internal_tenant")
            return None
        bid_number = row.solicitation_number

        async def step(ctx: StepContext) -> GemBidExtraction:
            result = await extract_gem_bid(
                ctx.llm, settings=self.settings, pages=pages, bid_number=bid_number
            )
            parsed: GemBidExtraction = result.parsed
            return parsed

        runner = AgentRunner(
            self.database,
            tenant_id=tenant_id,
            llm=self.llm,
            tracer=self.tracer or tracer_from_settings(self.settings),
        )
        run_id = await runner.start(
            kind=EXTRACT_RUN_KIND,
            params={"opportunity_id": str(row.id), "bid_number": bid_number},
        )
        result = await runner.run(
            run_id, [StepSpec(EXTRACT_STEP, step, input_ref=f"opportunity:{row.id}")]
        )
        if result.status != "done":
            log.warning(
                "gem_extract.failed",
                opportunity_id=str(row.id),
                run_id=str(run_id),
                error=result.error,
            )
            self._note(row, {"status": STATUS_FLAGGED, "run_id": str(run_id), **_err(result.error)})
            await session.flush()
            return None
        extraction = GemBidExtraction.model_validate(result.outputs[EXTRACT_STEP])
        self._apply(row, extraction)
        self._note(
            row,
            {
                "status": STATUS_OK,
                "run_id": str(run_id),
                "pages": len(pages),
                "bid_number": bid_number,
            },
        )
        await session.flush()
        self.extracted.append(row.id)
        return extraction

    async def _pages(self, row: Opportunity) -> list[str]:
        if self.storage is None:
            return []
        storage = self.storage.for_region(row.region)
        for doc in row.documents:
            if not doc.parsed_text_ref:
                continue
            try:
                pages = await load_parsed_text(storage, doc)
            except Exception as exc:  # a missing object is not an extraction failure
                log.warning("gem_extract.parsed_text_missing", document=str(doc.id), error=str(exc))
                continue
            if any(page.strip() for page in pages):
                return pages
        return []

    def _apply(self, row: Opportunity, extraction: GemBidExtraction) -> None:
        row.eligibility = extraction.eligibility_payload(base=row.eligibility or {})
        value = extraction.estimated_value
        if value is not None:
            row.estimated_value_min = value
            row.estimated_value_max = value
            usd = to_usd_or_none(value, row.currency, self.settings.fx_rates)
            row.estimated_value_min_usd = usd
            row.estimated_value_max_usd = usd
        if extraction.emd_amount is not None:
            row.emd_amount = extraction.emd_amount
        bid_end = extraction.bid_end()
        if bid_end is not None and row.response_due_at is None:
            row.response_due_at = bid_end
        elif bid_end is not None and row.response_due_at != bid_end:
            # the listing date wins (it is the portal's own field); the difference is
            # recorded so a reviewer can see the document disagrees
            log.info(
                "gem_extract.bid_end_mismatch",
                opportunity_id=str(row.id),
                listing=row.response_due_at.isoformat() if row.response_due_at else None,
                document=bid_end.isoformat(),
            )
            row.eligibility = {**row.eligibility, "bid_end_mismatch": True}

    def _note(self, row: Opportunity, note: dict[str, Any]) -> None:
        row.extra = {**(row.extra or {}), EXTRA_KEY: note}


def _err(error: str | None) -> dict[str, Any]:
    return {"error": error[:500]} if error else {}


def install_gem_extraction(
    settings: Settings,
    database: Database,
    storage: StorageRouter,
    bus: EventBus,
    *,
    llm: LLMClient | None = None,
) -> GemBidExtractor | None:
    """Subscribe GeM bid extraction when an LLM is available (API key or injected client)."""
    client = llm or llm_from_settings(settings)
    if client is None:
        log.info("gem_extract.disabled", reason="no LLM configured")
        return None
    return GemBidExtractor(
        llm=client, database=database, storage=storage, settings=settings
    ).subscribe(bus)
