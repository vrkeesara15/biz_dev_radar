"""M4-05 integration: threshold, caching by (opportunity version, profile version),
retry-then-null, and metering under the match's tenant."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.core.config import Region, Settings
from app.core.db import Database
from app.core.opportunity import NoticeType
from app.core.plan import LLM_COST_MICROUSD, LLM_TOKENS_IN
from app.core.profile_fields import CodeScheme, PerformanceRole
from app.models import (
    AgentRun,
    AgentStep,
    CompanyProfile,
    Match,
    MatchBand,
    Opportunity,
    PastPerformance,
    ProfileCode,
    ServiceLine,
    UsageLedger,
)
from app.services.matching.loaders import load_match_profile
from app.services.matching.rationale import (
    RATIONALE_RUN_KIND,
    STATUS_BELOW_THRESHOLD,
    STATUS_CACHED,
    STATUS_FAILED,
    STATUS_OK,
    STATUS_UNAVAILABLE,
    RationaleGenerator,
    build_company_brief,
    build_notice_brief,
)
from sqlalchemy import func, select

from tests.factories import create_tenant_with_owner
from tests.llm_fake import FakeLLM

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)

RATIONALE = {
    "fit_summary": [
        "The notice asks for cloud migration, the company's primary service line.",
        "NAICS 541511 is the company's primary code.",
        "The company has delivered the same work for the Treasury.",
    ],
    "matched_capabilities": ["Cloud migration"],
    "gaps": [
        {"gap": "No FedRAMP offering", "suggested_fix": "Team with a CSP", "fix_type": "teaming"}
    ],
    "eligibility_risks": [{"risk": "Needs active SAM", "page": 3, "document": "sow.pdf"}],
    "recommended_action": "pursue",
    "confidence": 0.8,
}


async def _seed(database: Database, *, score: Decimal = Decimal("74.00")):  # type: ignore[no-untyped-def]
    async with database.owner_session() as session:
        tenant, _, _ = await create_tenant_with_owner(session)
        profile = CompanyProfile(
            tenant_id=tenant.id,
            region=Region.US,
            legal_name="Cloud Movers LLC",
            version=2,
            year_founded=2010,
            employee_count_total=120,
        )
        opp = Opportunity(
            source_id="sam_opps",
            external_id=f"m405-{uuid.uuid4().hex[:8]}",
            region=Region.US,
            country="US",
            currency="USD",
            notice_type=NoticeType.RFP,
            title="Cloud migration services",
            description_text="Migrate mainframe workloads to the cloud.",
            summary_ai="Five line summary",
            solicitation_number="47QF-26-R-0001",
            buyer_org="Department of the Treasury",
            buyer_hierarchy=["Department of the Treasury", "Internal Revenue Service"],
            naics=["541511"],
            set_aside="SBA",
            eligibility={"required_registrations": ["sam"]},
            response_due_at=NOW + timedelta(days=30),
            version=5,
        )
        session.add_all([profile, opp])
        await session.flush()
        session.add_all(
            [
                ServiceLine(
                    tenant_id=tenant.id,
                    profile_id=profile.id,
                    name="Cloud migration",
                    description="Mainframe to AWS",
                ),
                PastPerformance(
                    tenant_id=tenant.id,
                    profile_id=profile.id,
                    title="Treasury mainframe migration",
                    customer="Department of the Treasury",
                    role=PerformanceRole.PRIME,
                    scope="scope",
                ),
                ProfileCode(
                    tenant_id=tenant.id,
                    profile_id=profile.id,
                    scheme=CodeScheme.NAICS,
                    code="541511",
                    is_primary=True,
                ),
            ]
        )
        await session.flush()
        match = Match(
            tenant_id=tenant.id,
            profile_id=profile.id,
            opportunity_id=opp.id,
            opportunity_version=opp.version,
            profile_version=profile.version,
            score=score,
            band=MatchBand.HIGH.value if score >= 70 else MatchBand.LOW.value,
            breakdown={"signals": {"code_match": {"raw": 1.0, "weight": 25, "weighted": 25.0}}},
        )
        session.add(match)
        await session.flush()
        return tenant.id, profile.id, opp.id, match.id


def _generator(database: Database, llm: FakeLLM | None) -> RationaleGenerator:
    return RationaleGenerator(
        llm=llm,  # type: ignore[arg-type]
        settings=Settings(_env_file=None),  # type: ignore[call-arg]
        database=database,
    )


async def _load(session, profile_id, match_id):  # type: ignore[no-untyped-def]
    profile_row = await session.get(CompanyProfile, profile_id)
    assert profile_row is not None
    profile = await load_match_profile(session, profile_row)
    match = await session.get(Match, match_id)
    assert match is not None
    return profile, match, profile_row.legal_name


async def test_two_scorings_of_the_same_versions_make_one_llm_call(database: Database) -> None:
    tenant_id, profile_id, _opp_id, match_id = await _seed(database)
    llm = FakeLLM().queue(RATIONALE)
    generator = _generator(database, llm)
    async with database.session(tenant_id) as session:
        profile, match, name = await _load(session, profile_id, match_id)
        first = await generator.ensure(session, match, profile=profile, legal_name=name)
        assert first.status == STATUS_OK
        assert match.rationale is not None
        assert match.breakdown["rationale_status"] == STATUS_OK
    assert len(llm.calls) == 1
    # a SECOND scoring of the same (profile version, opportunity version) reuses the row
    # the unique key (profile, opportunity, both versions) IS the cache key
    async with database.session(tenant_id) as session:
        profile, match, name = await _load(session, profile_id, match_id)
        second = await generator.ensure(session, match, profile=profile, legal_name=name)
    assert second.status == STATUS_CACHED
    assert second.rationale is not None
    assert second.rationale["recommended_action"] == "pursue"
    assert len(llm.calls) == 1  # no second model call
    # only one agent run exists
    async with database.session(tenant_id) as session:
        runs = (await session.execute(select(AgentRun))).scalars().all()
        assert [r.kind for r in runs] == [RATIONALE_RUN_KIND]
        assert runs[0].status == "done"
        assert runs[0].params["opportunity_version"] == 5
        assert runs[0].params["profile_version"] == 2


async def test_a_new_opportunity_version_earns_a_fresh_call(database: Database) -> None:
    tenant_id, profile_id, opp_id, match_id = await _seed(database)
    llm = FakeLLM().queue(RATIONALE, {**RATIONALE, "recommended_action": "watch"})
    generator = _generator(database, llm)
    async with database.session(tenant_id) as session:
        profile, match, name = await _load(session, profile_id, match_id)
        await generator.ensure(session, match, profile=profile, legal_name=name)
        newer = Match(
            tenant_id=tenant_id,
            profile_id=profile_id,
            opportunity_id=opp_id,
            opportunity_version=6,
            profile_version=2,
            score=Decimal("74.00"),
            band=MatchBand.HIGH.value,
            breakdown={},
        )
        session.add(newer)
        await session.flush()
        result = await generator.ensure(session, newer, profile=profile, legal_name=name)
    assert result.status == STATUS_OK
    assert result.rationale is not None
    assert result.rationale["recommended_action"] == "watch"
    assert len(llm.calls) == 2


async def test_a_score_below_fifty_never_calls_the_model(database: Database) -> None:
    tenant_id, profile_id, _, match_id = await _seed(database, score=Decimal("49.99"))
    llm = FakeLLM()
    generator = _generator(database, llm)
    async with database.session(tenant_id) as session:
        profile, match, name = await _load(session, profile_id, match_id)
        result = await generator.ensure(session, match, profile=profile, legal_name=name)
        assert match.rationale is None
        assert match.breakdown["rationale_status"] == STATUS_BELOW_THRESHOLD
    assert result.status == STATUS_BELOW_THRESHOLD
    assert llm.calls == []
    async with database.session(tenant_id) as session:
        assert (await session.execute(select(func.count()).select_from(AgentRun))).scalar() == 0


async def test_exactly_fifty_does_call_the_model(database: Database) -> None:
    tenant_id, profile_id, _, match_id = await _seed(database, score=Decimal("50.00"))
    llm = FakeLLM().queue(RATIONALE)
    async with database.session(tenant_id) as session:
        profile, match, name = await _load(session, profile_id, match_id)
        result = await _generator(database, llm).ensure(
            session, match, profile=profile, legal_name=name
        )
    assert result.status == STATUS_OK and len(llm.calls) == 1


async def test_invalid_json_is_retried_then_the_rationale_stays_null(database: Database) -> None:
    tenant_id, profile_id, _, match_id = await _seed(database)
    llm = FakeLLM().queue({"nope": 1}, {"nope": 2}, {"nope": 3})
    async with database.session(tenant_id) as session:
        profile, match, name = await _load(session, profile_id, match_id)
        result = await _generator(database, llm).ensure(
            session, match, profile=profile, legal_name=name
        )
        assert match.rationale is None
        assert match.breakdown["rationale_status"] == STATUS_FAILED
        assert "Rationale" in match.breakdown["rationale_error"]
    assert result.status == STATUS_FAILED
    assert len(llm.calls) == 1  # one complete_json call; the retries happen inside it
    async with database.session(tenant_id) as session:
        steps = (await session.execute(select(AgentStep))).scalars().all()
        assert [s.status for s in steps] == ["failed"]
        assert steps[0].attempt == 1
        # the failed attempts are still metered under the tenant
        assert steps[0].tokens_in > 0
        metrics = {
            metric: total
            for metric, total in (
                await session.execute(
                    select(UsageLedger.metric, func.sum(UsageLedger.quantity)).group_by(
                        UsageLedger.metric
                    )
                )
            ).all()
        }
    assert metrics[LLM_TOKENS_IN] > 0
    assert metrics[LLM_COST_MICROUSD] > 0


async def test_a_recovered_second_attempt_still_produces_a_rationale(database: Database) -> None:
    """The LLM client's own retry budget absorbs one bad payload."""
    tenant_id, profile_id, _, match_id = await _seed(database)
    llm = FakeLLM().queue({"nope": 1}, RATIONALE)
    async with database.session(tenant_id) as session:
        profile, match, name = await _load(session, profile_id, match_id)
        result = await _generator(database, llm).ensure(
            session, match, profile=profile, legal_name=name
        )
    assert result.status == STATUS_OK
    assert result.rationale is not None and result.rationale["confidence"] == 0.8


async def test_usage_is_recorded_under_the_match_tenant(database: Database) -> None:
    tenant_id, profile_id, _, match_id = await _seed(database)
    async with database.owner_session() as session:
        other, _, _ = await create_tenant_with_owner(session)
        other_id = other.id
    llm = FakeLLM().queue(RATIONALE)
    async with database.session(tenant_id) as session:
        profile, match, name = await _load(session, profile_id, match_id)
        await _generator(database, llm).ensure(session, match, profile=profile, legal_name=name)
    async with database.session(tenant_id) as session:
        mine = (await session.execute(select(UsageLedger))).scalars().all()
        assert {r.metric for r in mine} >= {LLM_TOKENS_IN, LLM_COST_MICROUSD}
        assert all(r.tenant_id == tenant_id for r in mine)
    async with database.session(other_id) as session:
        assert (await session.execute(select(UsageLedger))).scalars().all() == []
        assert (await session.execute(select(AgentRun))).scalars().all() == []


async def test_without_an_llm_the_status_is_unavailable(database: Database) -> None:
    tenant_id, profile_id, _, match_id = await _seed(database)
    async with database.session(tenant_id) as session:
        profile, match, name = await _load(session, profile_id, match_id)
        result = await _generator(database, None).ensure(
            session, match, profile=profile, legal_name=name
        )
        assert match.rationale is None
        assert match.breakdown["rationale_status"] == STATUS_UNAVAILABLE
    assert result.status == STATUS_UNAVAILABLE


async def test_the_prompt_carries_the_profile_and_the_notice(database: Database) -> None:
    tenant_id, profile_id, opp_id, match_id = await _seed(database)
    llm = FakeLLM().queue(RATIONALE)
    async with database.session(tenant_id) as session:
        profile, match, name = await _load(session, profile_id, match_id)
        await _generator(database, llm).ensure(session, match, profile=profile, legal_name=name)
        opp_row = await session.get(Opportunity, opp_id)
        assert opp_row is not None
        brief = await build_notice_brief(opp_row)
        company = await build_company_brief(session, profile, legal_name=name)
    bundle = llm.calls[0].cache_blocks[0].text
    assert "Cloud Movers LLC" in bundle
    assert "Cloud migration: Mainframe to AWS" in bundle
    assert "Treasury mainframe migration for Department of the Treasury" in bundle
    assert '<untrusted source="title">' in bundle and "Cloud migration services" in bundle
    assert "solicitation_number: 47QF-26-R-0001" in bundle
    assert "required_registrations" in bundle
    assert brief.buyer == "Department of the Treasury / Internal Revenue Service"
    assert brief.summary == "Five line summary"
    assert brief.documents == []  # no storage router, so no document excerpts
    assert company.codes == {"naics": ["541511"]}
    assert company.year_founded == 2010 and company.employee_count == 120


async def test_rationale_cache_never_crosses_tenants(database: Database) -> None:
    tenant_id, profile_id, opp_id, match_id = await _seed(database)
    llm = FakeLLM().queue(RATIONALE, RATIONALE)
    async with database.session(tenant_id) as session:
        profile, match, name = await _load(session, profile_id, match_id)
        await _generator(database, llm).ensure(session, match, profile=profile, legal_name=name)
    # a second tenant with its OWN profile for the same notice must not read A's rationale
    async with database.owner_session() as session:
        other, _, _ = await create_tenant_with_owner(session)
        other_profile = CompanyProfile(
            tenant_id=other.id, region=Region.US, legal_name="Rival Inc", version=2
        )
        session.add(other_profile)
        await session.flush()
        other_match = Match(
            tenant_id=other.id,
            profile_id=other_profile.id,
            opportunity_id=opp_id,
            opportunity_version=5,
            profile_version=2,
            score=Decimal("74.00"),
            band=MatchBand.HIGH.value,
            breakdown={},
        )
        session.add(other_match)
        await session.flush()
        other_id, other_match_id, other_profile_id = other.id, other_match.id, other_profile.id
    async with database.session(other_id) as session:
        profile, match, name = await _load(session, other_profile_id, other_match_id)
        result = await _generator(database, llm).ensure(
            session, match, profile=profile, legal_name=name
        )
    assert result.status == STATUS_OK  # a fresh call, not tenant A's cached answer
    assert len(llm.calls) == 2
