"""M3-04: GeM bid-PDF extraction on opportunity.created, written onto the row."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.core.config import Region, Settings
from app.core.db import Database
from app.core.eligibility_in import CriteriaIn
from app.core.opportunity import DocumentRef, NoticeType, OpportunityIn
from app.core.plan import LLM_COST_MICROUSD, LLM_TOKENS_IN
from app.models import AgentRun, AgentStep, Opportunity, OpportunityDocument, UsageLedger
from app.services.documents import parse_and_store
from app.services.events import EventBus
from app.services.gem_extraction import (
    EXTRA_KEY,
    STATUS_FLAGGED,
    STATUS_NO_DOCUMENT,
    STATUS_OK,
    GemBidExtractor,
    install_gem_extraction,
)
from app.services.ingest import ingest
from app.services.storage import LocalStorage, StorageRouter
from sqlalchemy import select

from tests.factories import make_tenant
from tests.llm_fake import FakeLLM

GOLDEN = Path(__file__).resolve().parents[3] / "evals" / "golden" / "in" / "gem"
BID = "GEM-2026-B-1234567"
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
SETTINGS = Settings(_env_file=None)  # type: ignore[call-arg]
ANSWER: dict[str, Any] = json.loads((GOLDEN / f"{BID}.llm.json").read_text())
EXPECTED: dict[str, Any] = json.loads((GOLDEN / f"{BID}.expected.json").read_text())


def _opp(**overrides: Any) -> OpportunityIn:
    values: dict[str, Any] = {
        "source_id": "gem",
        "external_id": "GEM/2026/B/1234567",
        "source_url": "https://bidplus.gem.gov.in/showbidDocument/7891234",
        "region": Region.IN,
        "country": "IN",
        "currency": "INR",
        "notice_type": NoticeType.GEM_BID,
        "title": "Desktop Computers x 120 (GEM/2026/B/1234567)",
        "solicitation_number": "GEM/2026/B/1234567",
        "buyer_org": "Ministry of Railways",
        "eligibility": {"requires_gem_registration": True},
        "documents": [
            DocumentRef(
                url="https://bidplus.gem.gov.in/showbidDocument/7891234",
                file_name=f"{BID}.pdf",
                mime_type="application/pdf",
            )
        ],
    }
    values.update(overrides)
    return OpportunityIn(**values)


async def _internal_tenant(database: Database) -> uuid.UUID:
    async with database.owner_session() as session:
        tenant = make_tenant(slug="internal", is_internal=True)
        session.add(tenant)
        await session.flush()
        return tenant.id


def _setup(
    database: Database, fake_llm: FakeLLM, tmp_path: Path
) -> tuple[EventBus, GemBidExtractor, LocalStorage]:
    storage = LocalStorage(tmp_path, "bidradar-in", signing_secret="s")
    router = StorageRouter(SETTINGS, overrides={Region.IN: storage})
    bus = EventBus()
    extractor = GemBidExtractor(
        llm=fake_llm, database=database, storage=router, settings=SETTINGS
    ).subscribe(bus)
    return bus, extractor, storage


async def _ingest_with_parsed_pdf(
    database: Database, bus: EventBus, storage: LocalStorage, **overrides: Any
) -> uuid.UUID:
    """Ingest the bid, store the parsed bid PDF, then replay the created event."""
    async with database.session(None) as session:
        created = await ingest(session, _opp(**overrides), bus=EventBus(), now=NOW)
        opp_id = created.opportunity.id
        doc = (
            await session.execute(
                select(OpportunityDocument).where(OpportunityDocument.opportunity_id == opp_id)
            )
        ).scalar_one()
        await parse_and_store(session, doc, (GOLDEN / f"{BID}.pdf").read_bytes(), storage=storage)
    return opp_id


async def _row(database: Database, opp_id: uuid.UUID) -> Opportunity:
    async with database.session(None) as session:
        row = await session.get(Opportunity, opp_id)
        assert row is not None
        return row


async def test_created_gem_bid_is_extracted_onto_the_row(
    database: Database, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    internal = await _internal_tenant(database)
    bus, extractor, storage = _setup(database, fake_llm, tmp_path)
    fake_llm.queue(ANSWER)
    opp_id = await _ingest_with_parsed_pdf(database, bus, storage)
    async with database.session(None) as session:
        assert await extractor.extract(session, opp_id) is not None

    row = await _row(database, opp_id)
    assert row.eligibility["min_avg_turnover_inr"] == EXPECTED["min_avg_turnover_inr"]
    assert row.eligibility["emd_amount_inr"] == EXPECTED["emd_amount_inr"]
    assert row.eligibility["min_experience_years"] == EXPECTED["min_experience_years"]
    assert row.eligibility["allows_mse_exemption"] is True
    assert row.eligibility["allows_startup_exemption"] is False
    assert row.eligibility["requires_gem_registration"] is True  # the adapter's flag survives
    assert row.eligibility["consignee_locations"] == EXPECTED["consignee_locations"]
    assert row.eligibility["citations"]["emd_amount_inr"] == 2
    assert row.eligibility["bid_end_at"] == EXPECTED["bid_end_at"]
    # the parsed money lands on the canonical columns (core.money.parse_inr)
    assert row.estimated_value_min == row.estimated_value_max == Decimal("12000000")
    assert row.estimated_value_max_usd is not None and row.estimated_value_max_usd > 0
    assert row.emd_amount == Decimal("240000")
    assert row.response_due_at == datetime.fromisoformat(EXPECTED["bid_end_at"])
    assert row.extra[EXTRA_KEY]["status"] == STATUS_OK
    assert row.extra[EXTRA_KEY]["pages"] == 3
    # the whole payload still loads into the India eligibility rules
    criteria = CriteriaIn.from_dict(row.eligibility)
    assert criteria.min_avg_turnover_inr == Decimal("4500000")
    assert criteria.requires_gem_registration is True

    # metered under the internal tenant
    async with database.session(internal) as session:
        run = (await session.execute(select(AgentRun))).scalar_one()
        assert run.kind == "gem_extract" and run.status == "done"
        step = (await session.execute(select(AgentStep))).scalar_one()
        assert step.agent == "extract" and step.model == SETTINGS.llm_model_opus_class
        ledger = (await session.execute(select(UsageLedger))).scalars().all()
        assert {r.metric for r in ledger} >= {LLM_TOKENS_IN, LLM_COST_MICROUSD}
    assert extractor.extracted == [opp_id]


async def test_the_created_event_triggers_extraction_for_gem_only(
    database: Database, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    await _internal_tenant(database)
    bus, extractor, _storage = _setup(database, fake_llm, tmp_path)
    async with database.session(None) as session:  # a US notice on the same bus
        await ingest(
            session,
            OpportunityIn(
                source_id="sam_opps",
                external_id="n-1",
                region=Region.US,
                country="US",
                currency="USD",
                notice_type=NoticeType.RFP,
                title="Cloud Migration",
            ),
            bus=bus,
            now=NOW,
        )
    assert fake_llm.calls == [] and extractor.extracted == []

    fake_llm.queue(ANSWER)
    async with database.session(None) as session:  # gem, but the PDF is not parsed yet
        created = await ingest(session, _opp(), bus=bus, now=NOW)
    row = await _row(database, created.opportunity.id)
    assert fake_llm.calls == []
    assert row.extra[EXTRA_KEY]["status"] == STATUS_NO_DOCUMENT
    assert row.eligibility == {"requires_gem_registration": True}


async def test_an_answer_that_never_validates_flags_the_row_and_writes_nothing(
    database: Database, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    internal = await _internal_tenant(database)
    bus, extractor, storage = _setup(database, fake_llm, tmp_path)
    uncited = {**ANSWER, "citations": {}}
    fake_llm.queue(uncited, uncited, uncited)
    opp_id = await _ingest_with_parsed_pdf(database, bus, storage)
    async with database.session(None) as session:
        assert await extractor.extract(session, opp_id) is None

    row = await _row(database, opp_id)
    assert row.extra[EXTRA_KEY]["status"] == STATUS_FLAGGED
    assert "InvalidOutput" in row.extra[EXTRA_KEY]["error"]
    assert row.eligibility == {"requires_gem_registration": True}
    assert row.emd_amount is None and row.estimated_value_max is None
    async with database.session(internal) as session:
        run = (await session.execute(select(AgentRun))).scalar_one()
        assert run.status == "failed" and "InvalidOutput" in (run.error or "")
        step = (await session.execute(select(AgentStep))).scalar_one()
        assert step.status == "failed" and step.tokens_in > 0  # failed attempts are paid for


async def test_one_bad_answer_is_retried_and_the_second_is_kept(
    database: Database, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    await _internal_tenant(database)
    bus, extractor, storage = _setup(database, fake_llm, tmp_path)
    fake_llm.queue({**ANSWER, "citations": {}}, ANSWER)
    opp_id = await _ingest_with_parsed_pdf(database, bus, storage)
    async with database.session(None) as session:
        extraction = await extractor.extract(session, opp_id)
    assert extraction is not None and extraction.quantity == 120
    row = await _row(database, opp_id)
    assert row.extra[EXTRA_KEY]["status"] == STATUS_OK
    assert row.emd_amount == Decimal("240000")


async def test_a_listing_deadline_is_not_overwritten_by_the_document(
    database: Database, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    await _internal_tenant(database)
    bus, extractor, storage = _setup(database, fake_llm, tmp_path)
    fake_llm.queue(ANSWER)
    listed = datetime(2026, 10, 17, 14, 53, tzinfo=UTC) + timedelta(days=2)
    opp_id = await _ingest_with_parsed_pdf(database, bus, storage, response_due_at=listed)
    async with database.session(None) as session:
        await extractor.extract(session, opp_id)
    row = await _row(database, opp_id)
    assert row.response_due_at == listed, "the portal's own field wins"
    assert row.eligibility["bid_end_mismatch"] is True
    assert row.eligibility["bid_end_at"] == EXPECTED["bid_end_at"]


async def test_install_requires_an_llm(
    database: Database, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    router = StorageRouter(SETTINGS)
    assert install_gem_extraction(SETTINGS, database, router, EventBus()) is None
    installed = install_gem_extraction(SETTINGS, database, router, EventBus(), llm=fake_llm)
    assert isinstance(installed, GemBidExtractor)
