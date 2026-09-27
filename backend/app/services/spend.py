"""Weekly USAspending job: agency x NAICS x PSC obligations for the last 3 fiscal years into
agency_spend_stats, plus recompete candidates (period of performance ending in 6-18 months)
into awards_enrichment (opportunity_id NULL, recompete_watch = true).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import OpportunityIn, RawRecord, SourceAdapter
from app.core.normalize.usaspending import (
    SOURCE_ID,
    AwardRecord,
    aggregate_spend,
    is_recompete_candidate,
    last_fiscal_years,
)
from app.models import AgencySpendStat, AwardsEnrichment
from app.services.source_runner import RunResult, run_source

log = structlog.get_logger(__name__)

RECOMPETE_METHOD = "recompete_candidate"


@dataclass(slots=True)
class SpendStatsResult:
    run: RunResult
    fiscal_years: list[int]
    stats_written: int = 0
    recompete_written: int = 0
    awards_seen: int = 0
    messages: list[str] = field(default_factory=list)


async def run_spend_stats(
    session: AsyncSession,
    adapter: SourceAdapter,
    *,
    now: datetime | None = None,
    fiscal_years: int = 3,
) -> SpendStatsResult:
    """Fetch awards through the adapter (writing a source_runs row), then rebuild the
    statistics rows for the covered fiscal years and upsert recompete candidates."""
    now = now or datetime.now(UTC)
    awards: list[AwardRecord] = []
    messages: set[str] = set()

    async def collect(_: AsyncSession, __: OpportunityIn, raw: RawRecord) -> bool:
        award = raw.meta.get("award")
        if award is None:
            return False
        awards.append(award)
        for message in raw.meta.get("messages") or []:
            messages.add(str(message))
        return True

    run = await run_source(session, adapter, sink=collect, now=now)
    years = last_fiscal_years(now, fiscal_years)
    result = SpendStatsResult(
        run=run, fiscal_years=years, awards_seen=len(awards), messages=sorted(messages)
    )
    if run.status == "failing" and not awards:
        return result

    buckets = aggregate_spend(awards, years)
    await session.execute(
        delete(AgencySpendStat).where(
            AgencySpendStat.source_id == SOURCE_ID, AgencySpendStat.fiscal_year.in_(years)
        )
    )
    if buckets:
        rows: list[dict[str, Any]] = [
            {
                "agency": agency,
                "sub_agency": sub_agency,
                "naics": naics,
                "psc": psc,
                "fiscal_year": fy,
                "obligations": bucket.obligations,
                "award_count": bucket.award_count,
                "source_id": SOURCE_ID,
                "computed_at": now,
            }
            for (agency, sub_agency, naics, psc, fy), bucket in buckets.items()
        ]
        await session.execute(insert(AgencySpendStat), rows)
    result.stats_written = len(buckets)

    for award in awards:
        if not is_recompete_candidate(award.end_date, now):
            continue
        await upsert_recompete_candidate(session, award, now=now)
        result.recompete_written += 1
    await session.flush()
    log.info(
        "spend_stats.done",
        awards=len(awards),
        stats=result.stats_written,
        recompete=result.recompete_written,
    )
    return result


async def upsert_recompete_candidate(
    session: AsyncSession, award: AwardRecord, *, now: datetime
) -> AwardsEnrichment:
    """One awards_enrichment row per (usaspending, award_id) with no opportunity yet."""
    existing = (
        await session.execute(
            select(AwardsEnrichment).where(
                AwardsEnrichment.source_id == SOURCE_ID,
                AwardsEnrichment.award_id == award.award_id,
                AwardsEnrichment.opportunity_id.is_(None),
            )
        )
    ).scalar_one_or_none()
    row = existing or AwardsEnrichment(source_id=SOURCE_ID, award_id=award.award_id)
    row.incumbent = award.recipient
    row.prior_award_value = award.amount
    row.prior_pop_start = award.start_date
    row.prior_pop_end = award.end_date
    row.agency = award.agency or None
    row.sub_agency = award.sub_agency or None
    row.naics = award.naics or None
    row.psc = award.psc or None
    row.match_method = RECOMPETE_METHOD
    row.recompete_watch = True
    row.source_ref = award.url
    row.updated_at = now
    if existing is None:
        session.add(row)
    await session.flush()
    return row
