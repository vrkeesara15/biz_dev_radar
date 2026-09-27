"""M2-11: status job open -> closing_soon -> closed, parent roll-up, versions and events."""

from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import Region
from app.core.db import Database
from app.core.opportunity import NoticeType, OpportunityIn, OpportunityStatus
from app.jobs.status import roll_status_once, run_status_job
from app.models import Opportunity, OpportunityVersion
from app.services.events import OPPORTUNITY_AMENDED, EventBus, Recorder
from app.services.ingest import ingest
from app.services.status_job import StatusRollResult, roll_status
from freezegun import freeze_time
from sqlalchemy import select

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


def _opp(external_id: str, **overrides: Any) -> OpportunityIn:
    values: dict[str, Any] = {
        "source_id": "sam_opps",
        "external_id": external_id,
        "region": Region.US,
        "country": "US",
        "currency": "USD",
        "notice_type": NoticeType.RFP,
        "title": f"Notice {external_id}",
        "buyer_org": "Agency " + external_id,
        "posted_at": NOW - timedelta(days=10),
    }
    values.update(overrides)
    return OpportunityIn(**values)


def _bus() -> tuple[EventBus, Recorder]:
    bus, rec = EventBus(), Recorder()
    bus.subscribe("*", rec)
    return bus, rec


async def _statuses(database: Database) -> dict[str, tuple[str, int]]:
    async with database.session(None) as session:
        rows = (await session.execute(select(Opportunity))).scalars().all()
    return {r.external_id: (OpportunityStatus(r.status).value, r.version) for r in rows}


async def test_roll_status_moves_open_to_closing_soon_to_closed(database: Database) -> None:
    bus, rec = _bus()
    async with database.session(None) as session:
        for opp in (
            _opp("far", response_due_at=NOW + timedelta(days=30)),
            _opp("soon", response_due_at=NOW + timedelta(days=3)),
            _opp("past", response_due_at=NOW - timedelta(hours=1)),
            _opp("nodue"),
            _opp(
                "cancelled",
                response_due_at=NOW - timedelta(days=1),
                status=OpportunityStatus.CANCELLED,
            ),
        ):
            await ingest(session, opp, bus=bus, now=NOW - timedelta(days=9))
    assert await _statuses(database) == {
        "far": ("open", 1),
        "soon": ("open", 1),
        "past": ("open", 1),
        "nodue": ("open", 1),
        "cancelled": ("cancelled", 1),
    }
    rec.clear()

    async with database.session(None) as session:
        result = await roll_status(session, NOW, bus=bus)
    assert (result.closing_soon, result.closed, result.reopened, result.propagated) == (1, 1, 0, 0)
    assert await _statuses(database) == {
        "far": ("open", 1),
        "soon": ("closing_soon", 2),
        "past": ("closed", 2),
        "nodue": ("open", 1),
        "cancelled": ("cancelled", 1),
    }
    events = rec.named(OPPORTUNITY_AMENDED)
    assert sorted(e.payload["external_id"] for e in events) == ["past", "soon"]
    for event in events:
        assert event.payload["changes"] == ["status_changed"]
        assert event.payload["reason"] == "status_job"
        assert event.payload["version"] == 2
    async with database.session(None) as session:
        versions = (await session.execute(select(OpportunityVersion))).scalars().all()
    diffs = sorted(v.diff["status"]["new"] for v in versions)
    assert diffs == ["closed", "closing_soon"]
    assert all(v.changes == ["status_changed"] and v.version == 2 for v in versions)

    # idempotent
    async with database.session(None) as session:
        again = await roll_status(session, NOW, bus=bus)
    assert again.total == 0

    # four days later the closing_soon notice is past due
    async with database.session(None) as session:
        later = await roll_status(session, NOW + timedelta(days=4), bus=bus)
    assert later.closed == 1 and later.total == 1
    assert (await _statuses(database))["soon"] == ("closed", 3)


async def test_roll_status_uses_the_frozen_clock_by_default(database: Database) -> None:
    async with database.session(None) as session:
        await ingest(
            session,
            _opp("x", response_due_at=NOW + timedelta(days=2)),
            now=NOW - timedelta(days=20),
        )
        await ingest(
            session,
            _opp("y", response_due_at=NOW - timedelta(days=2)),
            now=NOW - timedelta(days=20),
        )
    with freeze_time("2026-09-26 12:00:00", real_asyncio=True):
        async with database.session(None) as session:
            result = await roll_status(session)
    assert (result.closing_soon, result.closed) == (1, 1)
    statuses = await _statuses(database)
    assert statuses["x"] == ("closing_soon", 2) and statuses["y"] == ("closed", 2)


async def test_ingest_derives_status_and_reopens_when_deadline_moves(database: Database) -> None:
    bus, _rec = _bus()
    async with database.session(None) as session:
        created = await ingest(
            session, _opp("r", response_due_at=NOW - timedelta(days=1)), bus=bus, now=NOW
        )
        assert created.opportunity.status is OpportunityStatus.CLOSED
    async with database.session(None) as session:
        amended = await ingest(
            session, _opp("r", response_due_at=NOW + timedelta(days=30)), bus=bus, now=NOW
        )
    assert amended.changed and amended.opportunity.status is OpportunityStatus.OPEN
    assert set(amended.changes) == {"deadline_moved", "status_changed"}
    assert amended.diff["status"] == {"old": "closed", "new": "open"}


async def test_cancellation_and_award_notices_update_the_parent(database: Database) -> None:
    bus, rec = _bus()
    async with database.session(None) as session:
        parent = await ingest(
            session,
            _opp("P-1", solicitation_number="SOL-1", response_due_at=NOW + timedelta(days=20)),
            bus=bus,
            now=NOW,
        )
        child = await ingest(
            session,
            _opp(
                "P-1-cancel",
                solicitation_number="SOL-1",
                notice_type=NoticeType.CORRIGENDUM,
                posted_at=NOW,
                status=OpportunityStatus.CANCELLED,
            ),
            bus=bus,
            now=NOW,
        )
        assert child.opportunity.parent_opportunity_id == parent.opportunity.id
    statuses = await _statuses(database)
    assert statuses["P-1"] == ("cancelled", 2)
    assert statuses["P-1-cancel"] == ("cancelled", 1)
    parent_events = [e for e in rec.named(OPPORTUNITY_AMENDED) if e.payload["external_id"] == "P-1"]
    assert len(parent_events) == 1
    assert parent_events[0].payload["changes"] == ["cancelled"]
    assert parent_events[0].payload["reason"] == "child_notice"
    assert parent_events[0].payload["diff"] == {"status": {"old": "open", "new": "cancelled"}}

    # award notice on another solicitation
    async with database.session(None) as session:
        await ingest(session, _opp("P-2", solicitation_number="SOL-2"), bus=bus, now=NOW)
        await ingest(
            session,
            _opp(
                "P-2-award",
                solicitation_number="SOL-2",
                notice_type=NoticeType.AWARD,
                posted_at=NOW,
                status=OpportunityStatus.AWARDED,
            ),
            bus=bus,
            now=NOW,
        )
    assert (await _statuses(database))["P-2"] == ("awarded", 2)
    # the job has nothing left to do (and never touches terminal statuses)
    async with database.session(None) as session:
        assert (await roll_status(session, NOW + timedelta(days=60), bus=bus)).total == 0


async def test_roll_status_propagates_terminal_children_out_of_order(database: Database) -> None:
    bus, rec = _bus()
    async with database.session(None) as session:
        parent = Opportunity(
            source_id="cppp",
            external_id="T-1",
            region=Region.US,
            country="US",
            currency="USD",
            notice_type=NoticeType.RFP,
            title="Tender T-1",
            response_due_at=NOW + timedelta(days=30),
        )
        session.add(parent)
        await session.flush()
        child = Opportunity(
            source_id="cppp",
            external_id="T-1-corr",
            region=Region.US,
            country="US",
            currency="USD",
            notice_type=NoticeType.CORRIGENDUM,
            title="Tender T-1 cancelled",
            status=OpportunityStatus.CANCELLED,
            parent_opportunity_id=parent.id,
        )
        session.add(child)
    async with database.session(None) as session:
        result = await roll_status(session, NOW, bus=bus)
    assert result.propagated == 1 and result.total == 1
    assert (await _statuses(database))["T-1"] == ("cancelled", 2)
    assert rec.named(OPPORTUNITY_AMENDED)[0].payload["changes"] == ["cancelled"]


async def test_job_entrypoint_runs_against_the_process_database(database: Database) -> None:
    async with database.session(None) as session:
        await ingest(
            session,
            _opp("j", response_due_at=NOW + timedelta(days=1)),
            now=NOW - timedelta(days=20),
        )
    result = await roll_status_once(database, NOW)
    assert isinstance(result, StatusRollResult) and result.closing_soon == 1


def test_run_status_job_returns_counts(monkeypatch: Any) -> None:
    async def fake(database: Any = None, now: Any = None) -> StatusRollResult:
        return StatusRollResult(closing_soon=2, closed=1)

    monkeypatch.setattr("app.jobs.status.roll_status_once", fake)
    assert run_status_job() == {
        "closing_soon": 2,
        "closed": 1,
        "reopened": 0,
        "propagated": 0,
        "total": 3,
    }
