"""Match trigger and batch scoring (M4-06, SPEC 6 / 12).

    scorer = MatchScorer(settings=settings, database=db, embeddings=provider)
    await scorer.score_opportunity(opportunity_id)      # every eligible profile, all tenants
    await scorer.rescore_profile(tenant_id, profile_id) # open notices for one profile
    await scorer.score_batch()                          # the whole open corpus (load script)

Who is scored (SPEC 6: "every active profile in its region")
  a profile is a candidate when it is_active, its region equals the notice's region and
  its completeness score is >= 40 (`core.profile_completeness.MATCHING_THRESHOLD`, the
  same `matching_enabled` flag the wizard shows). Candidates are discovered with the
  OWNER role because the set crosses tenants; everything after that runs inside one
  tenant-scoped session per tenant, so RLS still owns every read and write.

Cost control
  * stage 1 is pushed into SQL as far as it goes (region, country, notice type, not a
    duplicate, status open/closing_soon, response date still ahead or a recompete watch);
    `core.matching.hard_filters` then runs the rest (blocked buyers, exclusion keywords,
    set-aside) on what survived;
  * stage 2 is vectorised per profile: `services.matching.signals.batch_signals` costs
    three queries for a whole page of notices instead of three per pair;
  * the rows are written with one multi-row INSERT ... ON CONFLICT per page;
  * a pair dropped by stage 1 is NOT stored (OQ-94): with 200 profiles x 50k notices the
    filtered rows would be 10 M rows of "no".

Events
  a newly created high/medium row publishes `match.high` / `match.medium` with
  {tenant_id, profile_id, opportunity_id, match_id, score, band, version}; the router
  (M4-14) turns those into notifications.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import Select, and_, exists, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import Database, get_database
from app.core.matching.engine import MatchOutcome, evaluate
from app.core.matching.types import MatchProfile
from app.core.opportunity import OpportunityStatus
from app.core.profile_completeness import MATCHING_THRESHOLD
from app.models import AwardsEnrichment, CompanyProfile, Match, MatchBand, Opportunity
from app.services.embeddings import EmbeddingProvider
from app.services.events import MATCH_HIGH, MATCH_MEDIUM, EventBus, get_event_bus
from app.services.matching.loaders import load_match_profile, match_opportunity_from_row
from app.services.matching.rationale import RATIONALE_MIN_SCORE, RationaleGenerator
from app.services.matching.signals import PrecomputedSignals, batch_signals
from app.services.profiles import profile_completeness

log = structlog.get_logger(__name__)

# how many notices are scored (and written) in one page
DEFAULT_PAGE_SIZE = 500
OPEN_STATUSES = (OpportunityStatus.OPEN, OpportunityStatus.CLOSING_SOON)
BAND_EVENTS = {MatchBand.HIGH.value: MATCH_HIGH, MatchBand.MEDIUM.value: MATCH_MEDIUM}


@dataclass(frozen=True, slots=True)
class Scored:
    outcome: MatchOutcome
    version: int


@dataclass(frozen=True, slots=True)
class Candidate:
    tenant_id: uuid.UUID
    profile_id: uuid.UUID
    region: str


@dataclass(slots=True)
class ScoreRun:
    profiles: int = 0
    opportunities: int = 0
    kept: int = 0
    filtered: int = 0
    created: int = 0
    updated: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    rationales: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "profiles": self.profiles,
            "opportunities": self.opportunities,
            "kept": self.kept,
            "filtered": self.filtered,
            "created": self.created,
            "updated": self.updated,
            "high": self.high,
            "medium": self.medium,
            "low": self.low,
            "rationales": self.rationales,
            "events": len(self.events),
        }


# --- stage 1 in SQL -----------------------------------------------------------------------


def recompete_clause() -> Any:
    """SPEC 6: a recompete watch keeps a past-due or terminal notice (OQ-70)."""
    return exists().where(
        AwardsEnrichment.recompete_watch.is_(True),
        or_(
            AwardsEnrichment.opportunity_id == Opportunity.id,
            and_(
                Opportunity.solicitation_number.is_not(None),
                AwardsEnrichment.solicitation_number == Opportunity.solicitation_number,
            ),
        ),
    )


def candidate_opportunities(
    profile: MatchProfile, *, now: datetime, open_only: bool = True
) -> Select[Any]:
    """The cheap half of stage 1, as SQL the planner can use an index for."""
    stmt = select(Opportunity).where(
        Opportunity.duplicate_of.is_(None),
        Opportunity.region == profile.region,
    )
    if profile.target_countries:
        stmt = stmt.where(Opportunity.country.in_([c.upper() for c in profile.target_countries]))
    if profile.notice_types_wanted:
        stmt = stmt.where(
            Opportunity.notice_type.in_([t.lower() for t in profile.notice_types_wanted])
        )
    if open_only:
        recompete = recompete_clause()
        stmt = stmt.where(
            or_(Opportunity.status.in_(OPEN_STATUSES), recompete),
            or_(
                Opportunity.response_due_at.is_(None),
                Opportunity.response_due_at > now,
                recompete,
            ),
        )
    return stmt.order_by(Opportunity.id)


async def recompete_ids(session: AsyncSession, rows: Sequence[Opportunity]) -> set[uuid.UUID]:
    """Which of these notices an awards-enrichment row flags as a recompete to watch."""
    if not rows:
        return set()
    ids = [row.id for row in rows]
    numbers = [row.solicitation_number for row in rows if row.solicitation_number]
    clauses = [AwardsEnrichment.opportunity_id.in_(ids)]
    if numbers:
        clauses.append(AwardsEnrichment.solicitation_number.in_(numbers))
    found = (
        await session.execute(
            select(AwardsEnrichment.opportunity_id, AwardsEnrichment.solicitation_number).where(
                AwardsEnrichment.recompete_watch.is_(True), or_(*clauses)
            )
        )
    ).all()
    by_id = {row[0] for row in found if row[0] is not None}
    by_number = {row[1] for row in found if row[1]}
    return {r.id for r in rows if r.id in by_id or (r.solicitation_number or "") in by_number}


# --- candidate profiles --------------------------------------------------------------------


async def candidate_profiles(
    database: Database,
    *,
    region: str | None = None,
    tenant_id: uuid.UUID | None = None,
    profile_id: uuid.UUID | None = None,
    min_completeness: int = MATCHING_THRESHOLD,
) -> list[Candidate]:
    """Active profiles of the region whose completeness reaches the matching threshold.

    Read with the OWNER role: the set crosses tenants, which no tenant session may see.
    """
    async with database.owner_session() as session:
        stmt = select(CompanyProfile).where(CompanyProfile.is_active.is_(True))
        if region is not None:
            stmt = stmt.where(CompanyProfile.region == region)
        if tenant_id is not None:
            stmt = stmt.where(CompanyProfile.tenant_id == tenant_id)
        if profile_id is not None:
            stmt = stmt.where(CompanyProfile.id == profile_id)
        rows = (
            (await session.execute(stmt.order_by(CompanyProfile.tenant_id, CompanyProfile.id)))
            .scalars()
            .all()
        )
        out: list[Candidate] = []
        for row in rows:
            score = (await profile_completeness(session, row)).score
            if score < min_completeness:
                log.debug("matching.profile_incomplete", profile_id=str(row.id), completeness=score)
                continue
            out.append(Candidate(row.tenant_id, row.id, row.region.value))
    return out


# --- the scorer -----------------------------------------------------------------------------


class MatchScorer:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        database: Database | None = None,
        embeddings: EmbeddingProvider | None = None,
        rationale: RationaleGenerator | None = None,
        bus: EventBus | None = None,
        now: datetime | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
        min_completeness: int = MATCHING_THRESHOLD,
    ) -> None:
        self.settings = settings or get_settings()
        self.database = database or get_database()
        self.embeddings = embeddings
        self.rationale = rationale
        self.bus = bus
        self.page_size = page_size
        self.min_completeness = min_completeness
        self._now = now

    @property
    def clock(self) -> datetime:
        return self._now or datetime.now(UTC)

    # -- entry points ------------------------------------------------------------------------

    async def score_opportunity(self, opportunity_id: uuid.UUID) -> ScoreRun:
        """One new or amended notice against every eligible profile in its region."""
        run = ScoreRun()
        async with self.database.owner_session() as session:
            row = await session.get(Opportunity, opportunity_id)
            region = None if row is None else row.region.value
        if region is None:
            log.warning("matching.opportunity_missing", opportunity_id=str(opportunity_id))
            return run
        candidates = await candidate_profiles(
            self.database, region=region, min_completeness=self.min_completeness
        )
        for tenant_id, profiles in _by_tenant(candidates).items():
            async with self.database.session(tenant_id) as session:
                notice = await session.get(Opportunity, opportunity_id)
                if notice is None:  # pragma: no cover - global table, always visible
                    continue
                for candidate in profiles:
                    await self._score_profile(session, candidate, [notice], run)
        await self._publish(run)
        return run

    async def rescore_profile(self, tenant_id: uuid.UUID, profile_id: uuid.UUID) -> ScoreRun:
        """Every OPEN notice against one profile whose version just changed."""
        run = ScoreRun()
        candidates = await candidate_profiles(
            self.database,
            tenant_id=tenant_id,
            profile_id=profile_id,
            min_completeness=self.min_completeness,
        )
        if not candidates:
            log.info("matching.rescore_skipped", profile_id=str(profile_id))
            return run
        await self._score_candidates(candidates, run, open_only=True)
        await self._publish(run)
        return run

    async def score_batch(
        self,
        *,
        tenant_id: uuid.UUID | None = None,
        region: str | None = None,
        open_only: bool = True,
    ) -> ScoreRun:
        """The whole corpus against every eligible profile (nightly / load script)."""
        run = ScoreRun()
        candidates = await candidate_profiles(
            self.database,
            region=region,
            tenant_id=tenant_id,
            min_completeness=self.min_completeness,
        )
        await self._score_candidates(candidates, run, open_only=open_only)
        await self._publish(run)
        return run

    # -- internals ---------------------------------------------------------------------------

    async def _score_candidates(
        self, candidates: Sequence[Candidate], run: ScoreRun, *, open_only: bool
    ) -> None:
        for tenant_id, profiles in _by_tenant(candidates).items():
            async with self.database.session(tenant_id) as session:
                for candidate in profiles:
                    await self._score_profile(session, candidate, None, run, open_only=open_only)

    async def _score_profile(
        self,
        session: AsyncSession,
        candidate: Candidate,
        notices: Sequence[Opportunity] | None,
        run: ScoreRun,
        *,
        open_only: bool = True,
    ) -> None:
        row = await session.get(CompanyProfile, candidate.profile_id)
        if row is None:  # pragma: no cover - RLS + the candidate query already proved it
            return
        profile = await load_match_profile(session, row)
        run.profiles += 1
        if notices is not None:
            await self._score_page(session, profile, row, notices, run)
            return
        now = self.clock
        stmt = candidate_opportunities(profile, now=now, open_only=open_only)
        offset = 0
        while True:
            page = (
                (await session.execute(stmt.offset(offset).limit(self.page_size))).scalars().all()
            )
            if not page:
                return
            await self._score_page(session, profile, row, page, run)
            offset += self.page_size

    async def _score_page(
        self,
        session: AsyncSession,
        profile: MatchProfile,
        profile_row: CompanyProfile,
        page: Sequence[Opportunity],
        run: ScoreRun,
    ) -> None:
        now = self.clock
        run.opportunities += len(page)
        watched = await recompete_ids(session, page)
        signals = await batch_signals(session, profile, page, embeddings=self.embeddings)
        rows: list[dict[str, Any]] = []
        outcomes: dict[uuid.UUID, Scored] = {}
        for notice in page:
            opp = match_opportunity_from_row(notice, recompete_watch=notice.id in watched)
            outcome = evaluate(
                profile, opp, now, **signals.get(notice.id, _EMPTY_SIGNALS).as_kwargs()
            )
            if outcome.result is None:  # dropped by stage 1: nothing worth storing (OQ-94)
                run.filtered += 1
                continue
            run.kept += 1
            outcomes[notice.id] = Scored(outcome, int(notice.version or 1))
            rows.append(
                {
                    "tenant_id": profile_row.tenant_id,
                    "profile_id": profile_row.id,
                    "opportunity_id": notice.id,
                    "opportunity_version": notice.version,
                    "profile_version": profile_row.version,
                    "score": outcome.score,
                    "band": outcome.band,
                    "breakdown": outcome.breakdown,
                    "filtered_reason": None,
                    "ineligible_set_aside": outcome.ineligible_set_aside,
                }
            )
        if not rows:
            return
        created = await self._persist(session, rows, run)
        await self._after_persist(session, profile, profile_row, outcomes, created, run)

    async def _persist(
        self, session: AsyncSession, rows: list[dict[str, Any]], run: ScoreRun
    ) -> dict[uuid.UUID, uuid.UUID]:
        """One multi-row upsert; returns {opportunity_id: match_id} for the NEW rows.

        Which rows are new is decided BEFORE the write (one indexed lookup of the keys
        this page would touch) rather than from `xmax`, so the answer does not depend on
        a Postgres implementation detail. Only new rows raise an alert: re-scoring the
        same versions must never notify twice.
        """
        profile_id = rows[0]["profile_id"]
        profile_version = rows[0]["profile_version"]
        known = {
            (row[0], row[1])
            for row in (
                await session.execute(
                    select(Match.opportunity_id, Match.opportunity_version).where(
                        Match.profile_id == profile_id,
                        Match.profile_version == profile_version,
                        Match.opportunity_id.in_([r["opportunity_id"] for r in rows]),
                    )
                )
            ).all()
        }
        insert = pg_insert(Match).values(rows)
        stmt = insert.on_conflict_do_update(
            constraint="uq_matches_profile_opportunity_versions",
            set_={
                "score": insert.excluded.score,
                "band": insert.excluded.band,
                "breakdown": insert.excluded.breakdown,
                "ineligible_set_aside": insert.excluded.ineligible_set_aside,
            },
        ).returning(Match.id, Match.opportunity_id, Match.opportunity_version)
        result = (await session.execute(stmt)).all()
        await session.flush()
        created: dict[uuid.UUID, uuid.UUID] = {}
        for row in result:
            if (row[1], row[2]) not in known:
                created[row[1]] = row[0]
        run.created += len(created)
        run.updated += len(result) - len(created)
        return created

    async def _after_persist(
        self,
        session: AsyncSession,
        profile: MatchProfile,
        profile_row: CompanyProfile,
        outcomes: dict[uuid.UUID, Scored],
        created: dict[uuid.UUID, uuid.UUID],
        run: ScoreRun,
    ) -> None:
        for opportunity_id, scored in outcomes.items():
            band = scored.outcome.band
            if band == MatchBand.HIGH.value:
                run.high += 1
            elif band == MatchBand.MEDIUM.value:
                run.medium += 1
            else:
                run.low += 1
            match_id = created.get(opportunity_id)
            if match_id is None or band not in BAND_EVENTS:
                continue
            run.events.append(
                {
                    "name": BAND_EVENTS[band],
                    "payload": {
                        "tenant_id": str(profile_row.tenant_id),
                        "profile_id": str(profile_row.id),
                        "opportunity_id": str(opportunity_id),
                        "match_id": str(match_id),
                        "score": float(scored.outcome.score),
                        "band": band,
                        "version": scored.version,
                    },
                }
            )
        if self.rationale is not None:
            await self._rationales(session, profile, profile_row, outcomes, run)

    async def _rationales(
        self,
        session: AsyncSession,
        profile: MatchProfile,
        profile_row: CompanyProfile,
        outcomes: dict[uuid.UUID, Scored],
        run: ScoreRun,
    ) -> None:
        wanted = [
            oid for oid, scored in outcomes.items() if scored.outcome.score >= RATIONALE_MIN_SCORE
        ]
        if not wanted:
            return
        generator = self.rationale
        assert generator is not None
        rows = (
            (
                await session.execute(
                    select(Match).where(
                        Match.profile_id == profile_row.id,
                        Match.opportunity_id.in_(wanted),
                        Match.profile_version == profile_row.version,
                    )
                )
            )
            .scalars()
            .all()
        )
        for match in rows:
            result = await generator.ensure(
                session, match, profile=profile, legal_name=profile_row.legal_name
            )
            if result.rationale is not None:
                run.rationales += 1

    async def _publish(self, run: ScoreRun) -> None:
        bus = self.bus or get_event_bus()
        for event in run.events:
            await bus.publish(event["name"], event["payload"])


_EMPTY_SIGNALS = PrecomputedSignals()


def _by_tenant(candidates: Sequence[Candidate]) -> dict[uuid.UUID, list[Candidate]]:
    out: dict[uuid.UUID, list[Candidate]] = {}
    for candidate in candidates:
        out.setdefault(candidate.tenant_id, []).append(candidate)
    return out


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "Candidate",
    "MatchScorer",
    "ScoreRun",
    "candidate_opportunities",
    "candidate_profiles",
    "recompete_ids",
]
