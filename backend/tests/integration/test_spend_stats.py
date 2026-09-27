"""M2-07: weekly spend statistics job writes agency_spend_stats and recompete candidates."""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import respx
from app.adapters.http import MemoryArchiver, PoliteClient
from app.adapters.usaspending import SEARCH_URL, UsaSpendingAdapter
from app.core.config import Settings
from app.core.db import Database
from app.core.normalize.usaspending import award_from_row, fiscal_year, is_recompete_candidate
from app.core.politeness import PolicyTable
from app.models import AgencySpendStat, AwardsEnrichment, SourceRun
from app.services import sources as source_svc
from app.services.spend import run_spend_stats
from sqlalchemy import select, text

FIXTURES = Path(__file__).resolve().parents[1] / "adapters" / "fixtures" / "usaspending"
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
PAGE1 = json.loads((FIXTURES / "spending_by_award_page1.json").read_text())
PAGE2 = json.loads((FIXTURES / "spending_by_award_page2.json").read_text())
ROWS = [*PAGE1["results"], *PAGE2["results"]]


def _adapter() -> UsaSpendingAdapter:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    client = PoliteClient(
        settings=settings,
        archiver=MemoryArchiver(),
        clock=lambda: NOW.timestamp(),
        sleep=lambda s: None,
        policies=PolicyTable(default_rate=1e6),
        now=lambda: NOW,
    )
    return UsaSpendingAdapter(client=client, settings=settings, now=lambda: NOW, page_size=6)


def _mock() -> None:
    respx.get("https://api.usaspending.gov/robots.txt").mock(return_value=httpx.Response(404))

    def responder(request: httpx.Request) -> httpx.Response:
        page = json.loads(request.content)["page"]
        return httpx.Response(200, json=PAGE1 if page == 1 else PAGE2)

    respx.post(SEARCH_URL).mock(side_effect=responder)


@respx.mock
async def test_spend_stats_job_aggregates_and_flags_recompetes(database: Database) -> None:
    _mock()
    async with database.session(None) as session:
        await source_svc.sync_sources(session)
        result = await run_spend_stats(session, _adapter(), now=NOW)
    assert result.run.status == "ok"
    assert result.fiscal_years == [2024, 2025, 2026]
    assert result.awards_seen == len(ROWS)
    awards = [award_from_row(r) for r in ROWS]
    in_window = [a for a in awards if fiscal_year(a.start_date or a.end_date) in {2024, 2025, 2026}]  # type: ignore[arg-type]
    assert result.stats_written > 0
    recompetes = [a for a in awards if is_recompete_candidate(a.end_date, NOW)]
    assert result.recompete_written == len(recompetes) > 0

    async with database.session(None) as session:
        stats = (await session.execute(select(AgencySpendStat))).scalars().all()
        assert len(stats) == result.stats_written
        assert sum(s.award_count for s in stats) == len(in_window)
        assert sum(s.obligations for s in stats) == sum(a.amount for a in in_window)
        assert {s.fiscal_year for s in stats} <= {2024, 2025, 2026}
        sample = stats[0]
        assert sample.agency and isinstance(sample.obligations, Decimal)
        rows = (await session.execute(select(AwardsEnrichment))).scalars().all()
        assert len(rows) == len(recompetes)
        row = next(r for r in rows if r.award_id == recompetes[0].award_id)
        assert row.opportunity_id is None
        assert row.recompete_watch is True
        assert row.match_method == "recompete_candidate"
        assert row.incumbent == recompetes[0].recipient
        assert row.prior_award_value == recompetes[0].amount
        assert row.prior_pop_end == recompetes[0].end_date
        assert row.prior_pop_end is not None
        assert timedelta(days=182) <= row.prior_pop_end - NOW.date() <= timedelta(days=548)
        assert row.source_ref is not None and row.source_ref.startswith(
            "https://www.usaspending.gov/award/"
        )
        run = (await session.execute(select(SourceRun))).scalars().one()
        assert run.source_id == "usaspending" and run.fetched == len(ROWS)

    # Rerun replaces the statistics and updates (not duplicates) the recompete rows.
    async with database.session(None) as session:
        again = await run_spend_stats(session, _adapter(), now=NOW)
    assert again.stats_written == result.stats_written
    async with database.session(None) as session:
        assert (
            len((await session.execute(select(AgencySpendStat))).scalars().all())
            == result.stats_written
        )
        assert len((await session.execute(select(AwardsEnrichment))).scalars().all()) == len(
            recompetes
        )


async def test_agency_spend_stats_is_global(database: Database) -> None:
    async with database.owner_engine.connect() as conn:
        cols = set(
            (
                await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'agency_spend_stats'"
                    )
                )
            )
            .scalars()
            .all()
        )
        rls = (
            await conn.execute(
                text("SELECT relrowsecurity FROM pg_class WHERE relname = 'agency_spend_stats'")
            )
        ).scalar()
    assert "tenant_id" not in cols and rls is False
    assert {
        "agency",
        "sub_agency",
        "naics",
        "psc",
        "fiscal_year",
        "obligations",
        "award_count",
    } <= cols
    async with database.session(None) as session:
        session.add(
            AgencySpendStat(
                agency="Department of Testing",
                naics="541512",
                psc="DA01",
                fiscal_year=2026,
                obligations=Decimal("10.50"),
                award_count=1,
                computed_at=NOW,
            )
        )
        await session.flush()
    async with database.session(None) as session:
        stat = (await session.execute(select(AgencySpendStat))).scalars().one()
        assert stat.sub_agency == "" and stat.source_id == "usaspending"
        assert stat.computed_at.date() == date(2026, 9, 26)
