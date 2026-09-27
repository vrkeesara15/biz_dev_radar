"""M2-08: awards enrichment links awards to opportunities and flags recompetes."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import respx
from app.adapters.http import MemoryArchiver, PoliteClient
from app.adapters.sam_awards import SamAwardsAdapter
from app.core.config import Region, Settings
from app.core.db import Database
from app.core.normalize.sam_awards import award_from_record
from app.core.opportunity import NoticeType
from app.core.politeness import PolicyTable
from app.models import AwardsEnrichment, Opportunity, SourceRun
from app.services import sources as source_svc
from app.services.awards import enrich_from_award, run_awards_enrichment
from sqlalchemy import select

FIXTURE = (
    Path(__file__).resolve().parents[1] / "adapters" / "fixtures" / "sam_awards" / "page1.json"
)
PAGE1 = json.loads(FIXTURE.read_text())
ROWS = PAGE1["awardsData"]
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
URL = "https://api.sam.gov/contract-awards/v1/search"


def _opp(**overrides: object) -> Opportunity:
    values: dict[str, object] = {
        "source_id": "sam_opps",
        "region": Region.US,
        "country": "US",
        "currency": "USD",
        "notice_type": NoticeType.RFP,
        "title": "notice",
        "source_tz": "America/New_York",
    }
    values.update(overrides)
    return Opportunity(**values)


async def _seed(database: Database) -> dict[str, Opportunity]:
    async with database.session(None) as session:
        army = _opp(
            external_id="army-1",
            solicitation_number="W911NF-26-R-0007",
            naics=["541512"],
            buyer_org="DEPT OF DEFENSE",
            buyer_hierarchy=["DEPT OF DEFENSE", "DEPT OF THE ARMY", "AMC"],
            posted_at=datetime(2026, 9, 20, tzinfo=UTC),
        )
        army_amend = _opp(
            external_id="army-2",
            solicitation_number="w911nf 26 r 0007",
            naics=["541512"],
            buyer_org="DEPT OF DEFENSE",
            buyer_hierarchy=["DEPT OF DEFENSE"],
            posted_at=datetime(2026, 9, 24, tzinfo=UTC),
        )
        gsa = _opp(
            external_id="gsa-1",
            solicitation_number="47QFCA26Q0042",
            naics=["541519"],
            buyer_org="GENERAL SERVICES ADMINISTRATION",
            buyer_hierarchy=["GENERAL SERVICES ADMINISTRATION", "FEDERAL ACQUISITION SERVICE"],
            posted_at=datetime(2026, 9, 22, tzinfo=UTC),
            prior_pop_end=date(2030, 1, 1),
            incumbent="Existing Inc.",
        )
        decoy = _opp(
            external_id="doe-1",
            solicitation_number="DE-1",
            naics=["541519"],
            buyer_org="DEPT OF ENERGY",
            buyer_hierarchy=["DEPT OF ENERGY"],
        )
        session.add_all([army, army_amend, gsa, decoy])
        await session.flush()
        return {"army": army, "army_amend": army_amend, "gsa": gsa, "decoy": decoy}


async def test_enrich_matches_by_solicitation_number_and_flags_recompete(
    database: Database,
) -> None:
    opps = await _seed(database)
    award = award_from_record(ROWS[0])
    async with database.session(None) as session:
        outcome = await enrich_from_award(session, award, now=NOW)
    assert outcome.opportunity_id == opps["army_amend"].id  # latest posting wins
    assert outcome.method == "solicitation_number" and outcome.recompete is True
    async with database.session(None) as session:
        row = (await session.execute(select(AwardsEnrichment))).scalars().one()
        assert row.opportunity_id == opps["army_amend"].id
        assert row.incumbent == "Northwind Federal Systems LLC"
        assert row.prior_award_value == Decimal("24900000.00")
        assert row.prior_pop_end == date(2027, 6, 30) and row.num_offers == 5
        assert row.recompete_watch is True and row.match_method == "solicitation_number"
        assert row.source_ref == "https://sam.gov/awards/W911NF20C0007/view"
        opp = await session.get(Opportunity, opps["army_amend"].id)
        assert opp is not None
        assert opp.incumbent == "Northwind Federal Systems LLC"
        assert opp.prior_award_value == Decimal("24900000.00")
        assert opp.prior_pop_end == date(2027, 6, 30)
    # idempotent
    async with database.session(None) as session:
        await enrich_from_award(session, award, now=NOW)
    async with database.session(None) as session:
        assert len((await session.execute(select(AwardsEnrichment))).scalars().all()) == 1


async def test_enrich_matches_by_naics_and_agency_without_recompete(database: Database) -> None:
    opps = await _seed(database)
    award = award_from_record(ROWS[1])  # NAICS 541519, GSA / FAS, ends 2029
    async with database.session(None) as session:
        outcome = await enrich_from_award(session, award, now=NOW)
    assert outcome.opportunity_id == opps["gsa"].id and outcome.method == "naics_agency"
    assert outcome.recompete is False
    async with database.session(None) as session:
        row = (await session.execute(select(AwardsEnrichment))).scalars().one()
        assert row.recompete_watch is False and row.num_offers == 3
        gsa = await session.get(Opportunity, opps["gsa"].id)
        assert gsa is not None
        # an older period of performance never overwrites a newer denormalised value
        assert gsa.incumbent == "Existing Inc." and gsa.prior_pop_end == date(2030, 1, 1)
        decoy = await session.get(Opportunity, opps["decoy"].id)
        assert decoy is not None and decoy.incumbent is None


async def test_no_match_cases(database: Database) -> None:
    await _seed(database)
    async with database.session(None) as session:
        candidate = await enrich_from_award(session, award_from_record(ROWS[2]), now=NOW)
        dropped = await enrich_from_award(session, award_from_record(ROWS[3]), now=NOW)
    assert candidate.opportunity_id is None and candidate.stored is True
    assert candidate.method == "recompete_candidate" and candidate.recompete is True
    assert dropped.stored is False and dropped.opportunity_id is None
    async with database.session(None) as session:
        rows = (await session.execute(select(AwardsEnrichment))).scalars().all()
        assert [r.award_id for r in rows] == ["140D0420C0011-0"]
        assert rows[0].opportunity_id is None and rows[0].recompete_watch is True


@respx.mock
async def test_daily_job_runs_adapter_and_enriches(database: Database) -> None:
    opps = await _seed(database)
    respx.get("https://api.sam.gov/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(URL).mock(return_value=httpx.Response(200, json=PAGE1))
    settings = Settings(_env_file=None, sam_api_key="k")  # type: ignore[call-arg]
    client = PoliteClient(
        settings=settings,
        archiver=MemoryArchiver(),
        clock=lambda: NOW.timestamp(),
        sleep=lambda s: None,
        policies=PolicyTable(default_rate=1e6),
        now=lambda: NOW,
    )
    adapter = SamAwardsAdapter(
        client=client,
        settings=settings,
        now=lambda: NOW,
        naics_codes=["541512", "541519", "236220", "541611"],
    )
    async with database.session(None) as session:
        await source_svc.sync_sources(session)
        result = await run_awards_enrichment(session, adapter, now=NOW)
    assert result.run.status == "ok" and result.run.fetched == 4
    assert (result.linked, result.recompete_only, result.skipped) == (2, 1, 1)
    async with database.session(None) as session:
        rows = (await session.execute(select(AwardsEnrichment))).scalars().all()
        assert len(rows) == 3
        linked = {r.opportunity_id for r in rows if r.opportunity_id is not None}
        assert linked == {opps["army_amend"].id, opps["gsa"].id}
        run = (await session.execute(select(SourceRun))).scalars().one()
        assert run.source_id == "sam_awards" and run.upserted == 3
