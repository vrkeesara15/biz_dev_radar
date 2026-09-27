"""Awards enrichment (SPEC 5.2/5.3): link SAM.gov contract awards to opportunities by
solicitation number or NAICS + agency, fill incumbent / prior_award_value / prior_pop_end /
num_offers, flag recompete_watch for contracts ending in 6-18 months.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import OpportunityIn, RawRecord, SourceAdapter
from app.core.normalize.sam import normalized_solicitation
from app.core.normalize.sam_awards import (
    SOURCE_ID,
    Candidate,
    ContractAward,
    match_award,
    recompete_watch,
)
from app.models import AwardsEnrichment, Opportunity
from app.services.source_runner import RunResult, run_source

log = structlog.get_logger(__name__)

NORMALISE_SQL = "regexp_replace(upper({col}), '[^A-Z0-9]', '', 'g')"


@dataclass(slots=True)
class EnrichmentOutcome:
    award_id: str
    opportunity_id: Any | None
    method: str | None
    recompete: bool
    stored: bool


@dataclass(slots=True)
class AwardsRunResult:
    run: RunResult
    linked: int = 0
    recompete_only: int = 0
    skipped: int = 0
    outcomes: list[EnrichmentOutcome] = field(default_factory=list)


async def candidates_for(session: AsyncSession, award: ContractAward) -> list[Candidate]:
    """Opportunities that could be the successor of this award: same normalised
    solicitation number, or same NAICS with the awarding agency in the buyer hierarchy."""
    filters = []
    key = normalized_solicitation(award.solicitation_number)
    if key:
        filters.append(
            func.regexp_replace(func.upper(Opportunity.solicitation_number), "[^A-Z0-9]", "", "g")
            == key
        )
    if award.naics:
        filters.append(Opportunity.naics.contains([award.naics]))
    if not filters:
        return []
    rows = (
        await session.execute(
            select(
                Opportunity.id,
                Opportunity.solicitation_number,
                Opportunity.naics,
                Opportunity.buyer_org,
                Opportunity.buyer_hierarchy,
                Opportunity.posted_at,
            ).where(or_(*filters), Opportunity.source_id != SOURCE_ID)
        )
    ).all()
    return [
        Candidate(
            id=row.id,
            solicitation_number=row.solicitation_number,
            naics=row.naics or [],
            buyer_org=row.buyer_org,
            buyer_hierarchy=row.buyer_hierarchy or [],
            posted_at=row.posted_at,
        )
        for row in rows
    ]


async def enrich_from_award(
    session: AsyncSession, award: ContractAward, *, now: datetime | None = None
) -> EnrichmentOutcome:
    """Link one award; store an awards_enrichment row when it links or is a recompete
    candidate, and denormalise incumbent / value / pop end onto the opportunity."""
    now = now or datetime.now(UTC)
    watch = recompete_watch(award, now)
    matched = match_award(award, await candidates_for(session, award))
    if matched is None and not watch:
        return EnrichmentOutcome(award.award_id, None, None, False, stored=False)
    opportunity_id = matched[0].id if matched else None
    method = matched[1] if matched else "recompete_candidate"
    row = (
        await session.execute(
            select(AwardsEnrichment).where(
                AwardsEnrichment.source_id == SOURCE_ID,
                AwardsEnrichment.award_id == award.award_id,
                (
                    AwardsEnrichment.opportunity_id == opportunity_id
                    if opportunity_id is not None
                    else AwardsEnrichment.opportunity_id.is_(None)
                ),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = AwardsEnrichment(source_id=SOURCE_ID, award_id=award.award_id)
        session.add(row)
    row.opportunity_id = opportunity_id
    row.incumbent = award.vendor_name
    row.prior_award_value = award.value
    row.prior_pop_start = award.start_date
    row.prior_pop_end = award.end_date
    row.num_offers = award.num_offers
    row.agency = award.agency or award.department
    row.sub_agency = award.office
    row.naics = award.naics
    row.psc = award.psc
    row.solicitation_number = award.solicitation_number
    row.match_method = method
    row.recompete_watch = watch
    row.source_ref = award.source_url
    row.updated_at = now
    if opportunity_id is not None:
        opportunity = await session.get(Opportunity, opportunity_id)
        if opportunity is not None:
            _denormalise(opportunity, award)
    await session.flush()
    return EnrichmentOutcome(award.award_id, opportunity_id, method, watch, stored=True)


def _denormalise(opportunity: Opportunity, award: ContractAward) -> None:
    """Newest period of performance wins; never blank out an existing value."""
    if opportunity.prior_pop_end and award.end_date and award.end_date < opportunity.prior_pop_end:
        return
    if award.vendor_name:
        opportunity.incumbent = award.vendor_name
    if award.value is not None:
        opportunity.prior_award_value = award.value
    if award.end_date is not None:
        opportunity.prior_pop_end = award.end_date


async def run_awards_enrichment(
    session: AsyncSession, adapter: SourceAdapter, *, now: datetime | None = None
) -> AwardsRunResult:
    """Daily job: fetch awards through the adapter (source_runs row) and enrich."""
    now = now or datetime.now(UTC)
    outcomes: list[EnrichmentOutcome] = []

    async def sink(inner: AsyncSession, _: OpportunityIn, raw: RawRecord) -> bool:
        award = raw.meta.get("award")
        if award is None:
            return False
        outcome = await enrich_from_award(inner, award, now=now)
        outcomes.append(outcome)
        return outcome.stored

    run = await run_source(session, adapter, sink=sink, now=now)
    result = AwardsRunResult(run=run, outcomes=outcomes)
    for outcome in outcomes:
        if outcome.opportunity_id is not None:
            result.linked += 1
        elif outcome.stored:
            result.recompete_only += 1
        else:
            result.skipped += 1
    log.info(
        "awards_enrichment.done",
        linked=result.linked,
        recompete_only=result.recompete_only,
        skipped=result.skipped,
    )
    return result
