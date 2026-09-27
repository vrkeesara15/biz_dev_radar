"""M2-10: cross-source key and fuzzy trigram dedupe inside the ingest pipeline."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.core.config import Region
from app.core.db import Database
from app.core.opportunity import Contact, DocumentRef, NoticeType, OpportunityIn
from app.models import Opportunity, OpportunityVersion
from app.services.dedupe import find_duplicates
from app.services.events import EventBus, Recorder
from app.services.ingest import ingest
from sqlalchemy import select

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
DUE = datetime(2026, 10, 20, 18, 0, tzinfo=UTC)


def _opp(source_id: str, external_id: str, **overrides: Any) -> OpportunityIn:
    values: dict[str, Any] = {
        "source_id": source_id,
        "external_id": external_id,
        "source_url": f"https://{source_id}.example.test/{external_id}",
        "region": Region.US,
        "country": "US",
        "currency": "USD",
        "notice_type": NoticeType.RFP,
        "title": "Cloud Migration Services for Field Offices",
        "buyer_org": "Internal Revenue Service",
        "posted_at": NOW - timedelta(days=1),
        "response_due_at": DUE,
    }
    values.update(overrides)
    return OpportunityIn(**values)


async def _ingest(database: Database, opp: OpportunityIn, bus: EventBus | None = None) -> Any:
    async with database.session(None) as session:
        result = await ingest(
            session, opp, raw_ref=f"raw/{opp.source_id}/{opp.external_id}", bus=bus
        )
        return result


async def _row(database: Database, opp_id: Any) -> Opportunity:
    async with database.session(None) as session:
        row = await session.get(Opportunity, opp_id)
        assert row is not None
        await session.refresh(row, ["documents"])
        return row


async def test_cross_source_key_merges_into_the_richer_record(database: Database) -> None:
    thin = await _ingest(database, _opp("sam_opps", "n-1", solicitation_number="47PF0018R0023"))
    rich = await _ingest(
        database,
        _opp(
            "mirror_portal",
            "m-9",
            solicitation_number="RFP No. 47PF-0018-R0023",
            buyer_org="Internal Revenue Service, Inc.",
            description_text="Migrate 40 field offices to GovCloud.",
            naics=["541512"],
            set_aside="SBA",
            estimated_value_max=Decimal("2500000"),
            contacts=[Contact(name="Jane Officer", email="jane@example.gov")],
            documents=[
                DocumentRef(url="https://mirror/doc/1.pdf", file_name="sow.pdf"),
                DocumentRef(url="https://mirror/doc/2.pdf", file_name="qa.pdf"),
            ],
        ),
    )
    assert rich.merged and rich.merged[0].winner_id == rich.opportunity.id
    assert rich.merged[0].loser_id == thin.opportunity.id

    loser = await _row(database, thin.opportunity.id)
    winner = await _row(database, rich.opportunity.id)
    assert loser.duplicate_of == winner.id
    assert winner.duplicate_of is None
    assert loser.reference_norm == winner.reference_norm == "47PF0018R0023"
    assert loser.buyer_norm == winner.buyer_norm == "internal revenue service"
    # both source links survive on the winner
    assert winner.extra["also_from"] == [
        {
            "source_id": "sam_opps",
            "external_id": "n-1",
            "source_url": "https://sam_opps.example.test/n-1",
        }
    ]


async def test_existing_record_wins_when_it_is_richer(database: Database) -> None:
    first = await _ingest(
        database,
        _opp(
            "sam_opps",
            "n-2",
            solicitation_number="W912DY-26-R-0001",
            description_text="Full description",
            naics=["541330"],
            documents=[DocumentRef(url="https://sam/doc/1.pdf")],
        ),
    )
    second = await _ingest(
        database, _opp("mirror_portal", "m-2", solicitation_number="Solicitation W912DY26R0001")
    )
    assert second.merged[0].winner_id == first.opportunity.id
    assert second.merged[0].loser_id == second.opportunity.id
    loser = await _row(database, second.opportunity.id)
    winner = await _row(database, first.opportunity.id)
    assert loser.duplicate_of == winner.id
    assert winner.extra["also_from"][0]["source_id"] == "mirror_portal"


async def test_same_source_same_reference_is_an_amendment_not_a_duplicate(
    database: Database,
) -> None:
    first = await _ingest(database, _opp("sam_opps", "n-3", solicitation_number="ABC-1"))
    second = await _ingest(
        database,
        _opp(
            "sam_opps", "n-3-a", solicitation_number="ABC-1", posted_at=NOW, title="ABC-1 Amend 1"
        ),
    )
    assert second.merged == []
    row = await _row(database, second.opportunity.id)
    assert row.duplicate_of is None
    assert row.parent_opportunity_id == first.opportunity.id


async def test_fuzzy_title_same_buyer_due_within_a_day_merges(database: Database) -> None:
    base = await _ingest(
        database,
        _opp("cppp", "c-1", title="Supply of Desktop Computers to District Offices"),
    )
    mirror = await _ingest(
        database,
        _opp(
            "gem",
            "g-1",
            title="Supply of Desktop Computers to District Offices.",
            buyer_org="INTERNAL REVENUE SERVICE.",
            response_due_at=DUE + timedelta(hours=20),
            description_text="richer",
            documents=[DocumentRef(url="https://gem/bid.pdf")],
        ),
    )
    assert len(mirror.merged) == 1
    assert mirror.merged[0].method == "fuzzy_title"
    loser = await _row(database, base.opportunity.id)
    assert loser.duplicate_of == mirror.opportunity.id


async def test_fuzzy_controls_do_not_merge(database: Database) -> None:
    await _ingest(database, _opp("cppp", "c-2", title="Annual Maintenance of Lifts"))
    # due date 3 days apart
    far = await _ingest(
        database,
        _opp(
            "gem",
            "g-2",
            title="Annual Maintenance of Lifts",
            response_due_at=DUE + timedelta(days=3),
        ),
    )
    assert far.merged == []
    # different buyer
    other = await _ingest(
        database,
        _opp("gem", "g-3", title="Annual Maintenance of Lifts", buyer_org="Ministry of Rail"),
    )
    assert other.merged == []
    # title too different
    diff = await _ingest(
        database, _opp("gem", "g-4", title="Annual Maintenance of Lifts and Escalators Block C")
    )
    assert diff.merged == []
    async with database.session(None) as session:
        dups = (
            (
                await session.execute(
                    select(Opportunity).where(Opportunity.duplicate_of.is_not(None))
                )
            )
            .scalars()
            .all()
        )
    assert dups == []


async def test_also_from_survives_re_ingest_and_creates_no_diff(database: Database) -> None:
    bus = EventBus()
    rec = Recorder()
    bus.subscribe("*", rec)
    await _ingest(database, _opp("sam_opps", "n-5", solicitation_number="X-5"), bus)
    rich = await _ingest(
        database,
        _opp("mirror_portal", "m-5", solicitation_number="X-5", description_text="rich"),
        bus,
    )
    winner_id = rich.opportunity.id
    # the winner's source publishes an amendment (deadline moved)
    moved = await _ingest(
        database,
        _opp(
            "mirror_portal",
            "m-5",
            solicitation_number="X-5",
            description_text="rich",
            response_due_at=DUE + timedelta(days=7),
        ),
        bus,
    )
    assert moved.changed and moved.merged == []
    row = await _row(database, winner_id)
    assert row.extra["also_from"][0]["external_id"] == "n-5"
    async with database.session(None) as session:
        versions = (
            (
                await session.execute(
                    select(OpportunityVersion).where(OpportunityVersion.opportunity_id == winner_id)
                )
            )
            .scalars()
            .all()
        )
    assert len(versions) == 1
    assert set(versions[0].diff) == {"response_due_at"}
    # the loser is not merged twice
    again = await _ingest(database, _opp("sam_opps", "n-5", solicitation_number="X-5"), bus)
    assert again.unchanged and again.merged == []
    row = await _row(database, winner_id)
    assert len(row.extra["also_from"]) == 1


async def test_find_duplicates_lists_candidates_with_method(database: Database) -> None:
    a = await _ingest(database, _opp("sam_opps", "n-6", solicitation_number="K-6"))
    async with database.session(None) as session:
        row = await session.get(Opportunity, a.opportunity.id)
        assert row is not None
        probe = Opportunity(
            source_id="mirror_portal",
            external_id="m-6",
            title=row.title,
            buyer_norm=row.buyer_norm,
            reference_norm="K6",
            response_due_at=row.response_due_at,
        )
        found = await find_duplicates(session, probe)
    assert [(c.id, c.method) for c in found] == [(a.opportunity.id, "cross_source_key")]
