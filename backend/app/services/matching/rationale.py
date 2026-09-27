"""SPEC 6 stage 3 wiring (M4-05): run the rationale agent, cache it, meter it.

    generator = RationaleGenerator(llm=llm, settings=settings, storage=router)
    result = await generator.ensure(session, match, profile=profile, opportunity=row)
    result.status      # ok | cached | below_threshold | failed | unavailable
    match.rationale    # the validated Rationale as jsonb, or None

Rules (SPEC 6 / M4-05 acceptance)
- Only a score >= RATIONALE_MIN_SCORE (50) calls the model; below that the row keeps
  rationale NULL and breakdown["rationale_status"] = "below_threshold".
- Cached by (opportunity version, profile version): the cache IS the matches table, whose
  unique key is exactly (profile, opportunity, opportunity_version, profile_version), so
  re-scoring the same pair twice makes ONE model call. A new version of either side is a
  different key and earns a fresh call.
- Invalid JSON is retried by the LLM client up to LLM_OUTPUT_RETRIES (2) extra times;
  when it still does not validate the step fails, rationale stays NULL and
  breakdown["rationale_status"] = "failed" with a short rationale_error (OQ-92).
- The call runs through AgentRunner under the MATCH's tenant, so agent_runs /
  agent_steps / usage_ledger carry the cost even when the attempt failed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents.llm import LLMClient
from app.agents.rationale import (
    CompanyBrief,
    DocumentPages,
    NoticeBrief,
    Rationale,
    match_rationale,
)
from app.agents.runner import AgentRunner, StepContext, StepSpec
from app.agents.tracing import Tracer, tracer_from_settings
from app.core.config import Settings, get_settings
from app.core.db import Database, get_database
from app.core.matching.types import MatchProfile
from app.models import Match, Opportunity, PastPerformance, ServiceLine
from app.services.documents import load_parsed_text
from app.services.storage import StorageRouter

log = structlog.get_logger(__name__)

RATIONALE_MIN_SCORE = Decimal(50)
RATIONALE_RUN_KIND = "match_rationale"
RATIONALE_STEP = "rationale"
STATUS_KEY = "rationale_status"
ERROR_KEY = "rationale_error"

STATUS_OK = "ok"
STATUS_CACHED = "cached"
STATUS_BELOW_THRESHOLD = "below_threshold"
STATUS_FAILED = "failed"
STATUS_UNAVAILABLE = "unavailable"

MAX_SERVICE_LINES = 12
MAX_PAST_PERFORMANCE = 12


@dataclass(frozen=True, slots=True)
class RationaleResult:
    status: str
    rationale: dict[str, Any] | None = None
    run_id: uuid.UUID | None = None
    error: str | None = None

    @property
    def called_model(self) -> bool:
        return self.status in (STATUS_OK, STATUS_FAILED)


# --- cache -------------------------------------------------------------------------------


async def cached_rationale(
    session: AsyncSession,
    *,
    profile_id: uuid.UUID,
    opportunity_id: uuid.UUID,
    opportunity_version: int,
    profile_version: int,
) -> dict[str, Any] | None:
    """A rationale already computed for exactly these two versions (RLS-scoped)."""
    return (
        await session.execute(
            select(Match.rationale)
            .where(
                Match.profile_id == profile_id,
                Match.opportunity_id == opportunity_id,
                Match.opportunity_version == opportunity_version,
                Match.profile_version == profile_version,
                Match.rationale.is_not(None),
            )
            .limit(1)
        )
    ).scalar_one_or_none()


# --- briefs ------------------------------------------------------------------------------


async def build_company_brief(
    session: AsyncSession, profile: MatchProfile, *, legal_name: str
) -> CompanyBrief:
    """The trusted half of the prompt, from the tenant's own rows."""
    profile_id = uuid.UUID(profile.id) if profile.id else None
    service_lines: list[str] = []
    past_performance: list[str] = []
    if profile_id is not None:
        rows = (
            (
                await session.execute(
                    select(ServiceLine)
                    .where(ServiceLine.profile_id == profile_id)
                    .order_by(ServiceLine.created_at, ServiceLine.id)
                    .limit(MAX_SERVICE_LINES)
                )
            )
            .scalars()
            .all()
        )
        service_lines = [f"{r.name}: {r.description}" if r.description else r.name for r in rows]
        proof = (
            (
                await session.execute(
                    select(PastPerformance)
                    .where(PastPerformance.profile_id == profile_id)
                    .order_by(PastPerformance.created_at, PastPerformance.id)
                    .limit(MAX_PAST_PERFORMANCE)
                )
            )
            .scalars()
            .all()
        )
        past_performance = [f"{r.title} for {r.customer}" for r in proof]
    return CompanyBrief(
        legal_name=legal_name,
        region=profile.region,
        service_lines=service_lines,
        codes={scheme: list(values) for scheme, values in sorted(profile.codes.items())},
        certifications=[c.kind for c in profile.certifications],
        registrations=[r.kind for r in profile.registrations],
        past_performance=past_performance,
        include_keywords=[k.term for k in profile.include_keywords],
        year_founded=profile.year_founded,
        employee_count=profile.employee_count_total,
        avg_receipts_usd=(
            None if profile.avg_receipts_usd is None else str(profile.avg_receipts_usd)
        ),
        target_geography=[
            *profile.target_us_states,
            *profile.target_in_states,
            *profile.target_cities,
        ],
    )


def notice_facts(row: Opportunity) -> dict[str, Any]:
    return {
        key: value
        for key, value in (
            ("solicitation_number", row.solicitation_number),
            ("naics", ", ".join(row.naics) or None),
            ("psc", ", ".join(row.psc) or None),
            ("set_aside", row.set_aside),
            ("reservation", row.reservation),
            ("estimated_value_min", row.estimated_value_min),
            ("estimated_value_max", row.estimated_value_max),
            ("currency", row.currency),
            ("emd_amount", row.emd_amount),
            ("response_due_at", row.response_due_at),
            ("place_of_performance", row.place_of_performance),
            ("incumbent", row.incumbent),
        )
        if value not in (None, "", [], {})
    }


async def build_notice_brief(
    row: Opportunity, *, storage: StorageRouter | None = None
) -> NoticeBrief:
    """The untrusted half: the notice and the first pages of its parsed documents."""
    documents: list[DocumentPages] = []
    if storage is not None:
        region_storage = storage.for_region(row.region)
        for doc in row.documents:
            if not doc.parsed_text_ref:
                continue
            try:
                pages = await load_parsed_text(region_storage, doc)
            except Exception as exc:  # a missing object must never block the rationale
                log.warning(
                    "rationale.parsed_text_missing", document_id=str(doc.id), error=str(exc)
                )
                continue
            documents.append(DocumentPages(name=doc.file_name or doc.url, pages=pages))
    return NoticeBrief(
        title=row.title,
        notice_type=row.notice_type.value,
        buyer=" / ".join(row.buyer_hierarchy) or row.buyer_org,
        summary=row.summary_ai,
        description=row.description_text,
        facts=notice_facts(row),
        eligibility=dict(row.eligibility or {}),
        documents=documents,
    )


# --- generator ---------------------------------------------------------------------------


class RationaleGenerator:
    def __init__(
        self,
        *,
        llm: LLMClient | None,
        settings: Settings | None = None,
        database: Database | None = None,
        storage: StorageRouter | None = None,
        tracer: Tracer | None = None,
        min_score: Decimal = RATIONALE_MIN_SCORE,
    ) -> None:
        self.llm = llm
        self.settings = settings or get_settings()
        self.database = database or get_database()
        self.storage = storage
        self.tracer = tracer
        self.min_score = min_score

    async def ensure(
        self,
        session: AsyncSession,
        match: Match,
        *,
        profile: MatchProfile,
        opportunity: Opportunity | None = None,
        legal_name: str = "",
    ) -> RationaleResult:
        """Fill `match.rationale` (and the breakdown status) for one scored match."""
        if Decimal(match.score) < self.min_score:
            return self._record(match, RationaleResult(STATUS_BELOW_THRESHOLD))
        cached = await cached_rationale(
            session,
            profile_id=match.profile_id,
            opportunity_id=match.opportunity_id,
            opportunity_version=match.opportunity_version,
            profile_version=match.profile_version,
        )
        if cached is not None:
            return self._record(match, RationaleResult(STATUS_CACHED, cached))
        if self.llm is None:
            log.info("rationale.disabled", reason="no LLM configured")
            return self._record(match, RationaleResult(STATUS_UNAVAILABLE))
        row = opportunity or await self._load_opportunity(session, match.opportunity_id)
        if row is None:  # pragma: no cover - the FK makes this unreachable in practice
            return self._record(match, RationaleResult(STATUS_UNAVAILABLE))
        company = await build_company_brief(
            session, profile, legal_name=legal_name or "(unnamed company)"
        )
        notice = await build_notice_brief(row, storage=self.storage)
        result = await self._run(match, notice=notice, company=company)
        return self._record(match, result)

    async def _load_opportunity(
        self, session: AsyncSession, opportunity_id: uuid.UUID
    ) -> Opportunity | None:
        return (
            await session.execute(
                select(Opportunity)
                .options(selectinload(Opportunity.documents))
                .where(Opportunity.id == opportunity_id)
            )
        ).scalar_one_or_none()

    async def _run(
        self, match: Match, *, notice: NoticeBrief, company: CompanyBrief
    ) -> RationaleResult:
        score, band, breakdown = float(match.score), match.band, dict(match.breakdown or {})
        llm = self.llm
        assert llm is not None  # checked by the caller

        async def step(ctx: StepContext) -> Rationale:
            outcome = await match_rationale(
                ctx.llm,
                settings=self.settings,
                notice=notice,
                company=company,
                score=score,
                band=band,
                breakdown=breakdown,
            )
            parsed: Rationale = outcome.parsed
            return parsed

        runner = AgentRunner(
            self.database,
            tenant_id=match.tenant_id,
            llm=llm,
            tracer=self.tracer or tracer_from_settings(self.settings),
        )
        run_id = await runner.start(
            kind=RATIONALE_RUN_KIND,
            params={
                "match_id": str(match.id),
                "profile_id": str(match.profile_id),
                "opportunity_id": str(match.opportunity_id),
                "opportunity_version": match.opportunity_version,
                "profile_version": match.profile_version,
            },
        )
        input_ref = (
            f"match:{match.profile_id}@{match.profile_version}"
            f":{match.opportunity_id}@{match.opportunity_version}"
        )
        outcome = await runner.run(run_id, [StepSpec(RATIONALE_STEP, step, input_ref=input_ref)])
        if outcome.status != "done":
            log.warning(
                "rationale.failed",
                match_id=str(match.id),
                run_id=str(run_id),
                error=outcome.error,
            )
            return RationaleResult(STATUS_FAILED, None, run_id, (outcome.error or "")[:300] or None)
        return RationaleResult(STATUS_OK, outcome.outputs[RATIONALE_STEP], run_id)

    @staticmethod
    def _record(match: Match, result: RationaleResult) -> RationaleResult:
        breakdown = dict(match.breakdown or {})
        breakdown[STATUS_KEY] = result.status
        if result.error:
            breakdown[ERROR_KEY] = result.error
        else:
            breakdown.pop(ERROR_KEY, None)
        match.breakdown = breakdown
        if result.rationale is not None:
            match.rationale = result.rationale
        return result


def rationale_generator_from_settings(
    settings: Settings | None = None,
    *,
    database: Database | None = None,
    storage: StorageRouter | None = None,
    llm: LLMClient | None = None,
) -> RationaleGenerator:
    """A generator that is a no-op (status `unavailable`) when no LLM is configured."""
    from app.agents.llm import llm_from_settings

    settings = settings or get_settings()
    return RationaleGenerator(
        llm=llm or llm_from_settings(settings),
        settings=settings,
        database=database,
        storage=storage,
    )


__all__ = [
    "RATIONALE_MIN_SCORE",
    "RATIONALE_RUN_KIND",
    "STATUS_BELOW_THRESHOLD",
    "STATUS_CACHED",
    "STATUS_FAILED",
    "STATUS_OK",
    "STATUS_UNAVAILABLE",
    "RationaleGenerator",
    "RationaleResult",
    "build_company_brief",
    "build_notice_brief",
    "cached_rationale",
    "rationale_generator_from_settings",
]
