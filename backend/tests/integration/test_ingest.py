"""M2-09: ingest pipeline: upsert, content_hash, versions, amendment events."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx
from app.adapters.http import MemoryArchiver, PoliteClient
from app.adapters.sam_opps import SEARCH_URL, SamOpportunitiesAdapter
from app.core.config import Settings
from app.core.db import Database
from app.core.normalize.sam import link_amendments, normalize_sam_notice
from app.core.opportunity import DocumentRef, NoticeType, OpportunityIn, OpportunityStatus
from app.core.politeness import PolicyTable
from app.models import Opportunity, OpportunityDocument, OpportunityVersion
from app.services import sources as source_svc
from app.services.events import OPPORTUNITY_AMENDED, OPPORTUNITY_CREATED, EventBus, Recorder
from app.services.ingest import ingest
from app.services.source_runner import run_source
from sqlalchemy import select

FIXTURES = Path(__file__).resolve().parents[1] / "adapters" / "fixtures" / "sam_opps"
PAGE1 = json.loads((FIXTURES / "page1.json").read_text())
PAGE2 = json.loads((FIXTURES / "page2.json").read_text())
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


@pytest.fixture()
def bus() -> tuple[EventBus, Recorder]:
    bus = EventBus()
    recorder = Recorder()
    bus.subscribe("*", recorder)
    return bus, recorder


def _record(index: int, **overrides: object) -> dict:  # type: ignore[type-arg]
    record = json.loads(json.dumps(PAGE1["opportunitiesData"][index]))
    record.update(overrides)
    return record


async def test_ingest_twice_with_moved_deadline_creates_one_version_and_one_event(
    database: Database, bus: tuple[EventBus, Recorder]
) -> None:
    event_bus, recorder = bus
    first = normalize_sam_notice(_record(0))
    async with database.session(None) as session:
        created = await ingest(
            session, first, raw_ref="raw/sam_opps/2026/09/26/n/1", bus=event_bus, now=NOW
        )
        assert created.created and not created.changed and created.version == 1
        opp_id = created.opportunity.id
    # exact same content again: nothing happens
    async with database.session(None) as session:
        same = await ingest(
            session,
            first,
            raw_ref="raw/sam_opps/2026/09/26/n/2",
            bus=event_bus,
            now=NOW + timedelta(hours=1),
        )
        assert same.unchanged and same.version == 1
    # the deadline moves by a week
    moved = normalize_sam_notice(_record(0, responseDeadLine="2026-10-27T14:00:00-04:00"))
    async with database.session(None) as session:
        amended = await ingest(
            session,
            moved,
            raw_ref="raw/sam_opps/2026/09/27/n/3",
            bus=event_bus,
            now=NOW + timedelta(days=1),
        )
    assert amended.changed and amended.version == 2
    assert amended.changes == ["deadline_moved"]
    assert amended.diff == {
        "response_due_at": {"old": "2026-10-20T18:00:00+00:00", "new": "2026-10-27T18:00:00+00:00"}
    }
    async with database.session(None) as session:
        row = await session.get(Opportunity, opp_id)
        assert row is not None
        assert row.version == 2
        assert row.response_due_at == datetime(2026, 10, 27, 18, 0, tzinfo=UTC)
        assert row.raw_ref == "raw/sam_opps/2026/09/27/n/3"
        assert row.last_seen_at == NOW + timedelta(days=1)
        assert row.content_hash == amended.content_hash and len(row.content_hash) == 64
        versions = (await session.execute(select(OpportunityVersion))).scalars().all()
        assert len(versions) == 1
        assert versions[0].version == 2 and versions[0].changes == ["deadline_moved"]
        assert versions[0].diff["response_due_at"]["new"] == "2026-10-27T18:00:00+00:00"
        assert versions[0].content_hash == row.content_hash
    assert [e.name for e in recorder.events] == [OPPORTUNITY_CREATED, OPPORTUNITY_AMENDED]
    amended_events = recorder.named(OPPORTUNITY_AMENDED)
    assert len(amended_events) == 1
    payload = amended_events[0].payload
    assert payload["opportunity_id"] == str(opp_id) and payload["version"] == 2
    assert payload["changes"] == ["deadline_moved"] and "response_due_at" in payload["diff"]
    assert payload["source_id"] == "sam_opps" and payload["external_id"] == first.external_id


async def test_upsert_stores_fields_documents_usd_and_status(
    database: Database, bus: tuple[EventBus, Recorder]
) -> None:
    event_bus, recorder = bus
    opp = OpportunityIn(
        source_id="gem",
        external_id="GEM/2026/B/1",
        region="in",
        country="IN",
        currency="INR",
        notice_type=NoticeType.GEM_BID,
        title="Supply of laptops",
        buyer_org="Ministry of Testing",
        estimated_value_max=Decimal("1250000"),
        emd_amount=Decimal("25000"),
        response_due_at=datetime(2026, 10, 15, 12, 0, tzinfo=UTC),
        source_tz="Asia/Kolkata",
        contacts=[{"name": "Officer", "email": "o@example.in"}],
        place_of_performance={"city": "Hyderabad", "state": "Telangana", "country": "IN"},
        documents=[DocumentRef(url="https://bid.example/1.pdf", file_name="bid.pdf", size=10)],
    )
    settings = Settings(_env_file=None, fx_rates='{"USD": 1.0, "INR": 0.012}')  # type: ignore[call-arg]
    async with database.session(None) as session:
        result = await ingest(
            session, opp, raw_ref="raw/gem/1", bus=event_bus, settings=settings, now=NOW
        )
        opp_id = result.opportunity.id
    async with database.session(None) as session:
        row = (
            await session.execute(select(Opportunity).where(Opportunity.id == opp_id))
        ).scalar_one()
        assert row.status is OpportunityStatus.OPEN and row.version == 1
        assert row.estimated_value_max_usd == Decimal("15000.00")
        assert row.currency == "INR" and row.emd_amount == Decimal("25000")
        assert row.place_of_performance["city"] == "Hyderabad"
        assert row.contacts[0]["email"] == "o@example.in"
        assert row.detail_status == "pending" and row.raw_ref == "raw/gem/1"
        docs = (await session.execute(select(OpportunityDocument))).scalars().all()
        assert len(docs) == 1 and docs[0].file_name == "bid.pdf" and docs[0].status == "pending"
        assert docs[0].size == 10 and docs[0].opportunity_id == opp_id
    # new attachment + cancellation on the next fetch
    changed = opp.model_copy(
        update={
            "status": OpportunityStatus.CANCELLED,
            "documents": [*opp.documents, DocumentRef(url="https://bid.example/corrigendum.pdf")],
        }
    )
    async with database.session(None) as session:
        result = await ingest(session, changed, bus=event_bus, settings=settings, now=NOW)
    assert result.version == 2 and set(result.changes) == {"new_attachment", "cancelled"}
    async with database.session(None) as session:
        row = (
            await session.execute(select(Opportunity).where(Opportunity.id == opp_id))
        ).scalar_one()
        assert row.status is OpportunityStatus.CANCELLED
        assert len((await session.execute(select(OpportunityDocument))).scalars().all()) == 2
        version = (await session.execute(select(OpportunityVersion))).scalars().one()
        assert version.diff["status"] == {"old": "open", "new": "cancelled"}
        assert len(version.diff["documents"]["new"]) == 2
    assert len(recorder.named(OPPORTUNITY_AMENDED)) == 1


async def test_amendments_link_to_the_earliest_notice(
    database: Database, bus: tuple[EventBus, Recorder]
) -> None:
    event_bus, _ = bus
    batch = link_amendments(normalize_sam_notice(r) for r in PAGE1["opportunitiesData"])
    async with database.session(None) as session:
        results = [await ingest(session, opp, bus=event_bus, now=NOW) for opp in batch]
    by_ext = {r.opportunity.external_id: r.opportunity for r in results}
    parent = by_ext["3f9c1a2b7e8d4c5a9b0e1f2a3b4c5d6e"]
    amendment = by_ext["4a0d2b3c8f9e5d6b0c1f2a3b4c5d6e7f"]
    assert amendment.parent_opportunity_id == parent.id
    assert parent.parent_opportunity_id is None
    # a later notice with the same number arriving alone (no batch hint) also links by number
    later = normalize_sam_notice(
        _record(
            1,
            noticeId="9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f",
            postedDate="2026-09-28",
            type="Solicitation",
        )
    )
    async with database.session(None) as session:
        result = await ingest(session, later, bus=event_bus, now=NOW)
        assert result.opportunity.parent_opportunity_id == parent.id


@respx.mock
async def test_run_source_with_sam_adapter_ingests_end_to_end(
    database: Database, bus: tuple[EventBus, Recorder]
) -> None:
    respx.get("https://api.sam.gov/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(SEARCH_URL, params={"offset": "0"}).mock(return_value=httpx.Response(200, json=PAGE1))
    respx.get(SEARCH_URL, params={"offset": "5"}).mock(return_value=httpx.Response(200, json=PAGE2))
    settings = Settings(_env_file=None, sam_api_key="k", sam_daily_quota=100)  # type: ignore[call-arg]
    client = PoliteClient(
        settings=settings,
        archiver=MemoryArchiver(),
        clock=lambda: NOW.timestamp(),
        sleep=lambda s: None,
        policies=PolicyTable(default_rate=1e6, quotas={"api.sam.gov": 100}),
        now=lambda: NOW,
    )
    adapter = SamOpportunitiesAdapter(
        client=client, settings=settings, now=lambda: NOW, page_size=5
    )
    async with database.session(None) as session:
        await source_svc.sync_sources(session)
        first = await run_source(session, adapter, now=NOW)
    assert first.status == "ok" and first.fetched == 6 and first.upserted == 6
    async with database.session(None) as session:
        rows = (await session.execute(select(Opportunity))).scalars().all()
        assert len(rows) == 6
        assert all(r.raw_ref and r.raw_ref.startswith("raw/sam_opps/2026/09/26/") for r in rows)
        assert {r.notice_type.value for r in rows} >= {"presolicitation", "rfp", "award"}
        assert len((await session.execute(select(OpportunityDocument))).scalars().all()) == 7
    # second run: nothing changed -> zero upserts, no versions
    async with database.session(None) as session:
        second = await run_source(session, adapter, now=NOW)
    assert second.status == "ok" and second.fetched == 6 and second.upserted == 0
    async with database.session(None) as session:
        assert (await session.execute(select(OpportunityVersion))).scalars().all() == []
