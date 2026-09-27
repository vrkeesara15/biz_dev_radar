"""M4-01: matches table (migration, uniqueness, RLS) and the ORM -> core.matching loaders."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from app.core.config import Region
from app.core.db import Database
from app.core.matching.filters import hard_filters
from app.core.opportunity import NoticeType
from app.core.profile_fields import (
    CertificationKind,
    CodeScheme,
    KeywordKind,
    PerformanceRole,
    RegistrationKind,
    UdyamCategory,
)
from app.models import (
    AwardsEnrichment,
    Certification,
    CompanyProfile,
    Match,
    MatchBand,
    Opportunity,
    PastPerformance,
    ProfileCode,
    ProfileKeyword,
    Registration,
)
from app.services.matching.loaders import (
    is_recompete_watch,
    load_match_opportunity,
    load_match_profile,
    match_opportunity_from_row,
    usd_receipts,
)
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from tests.factories import create_tenant_with_owner

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


async def _seed(database: Database):  # type: ignore[no-untyped-def]
    async with database.owner_session() as session:
        tenant, _, _ = await create_tenant_with_owner(session)
        profile = CompanyProfile(
            tenant_id=tenant.id,
            region=Region.US,
            legal_name="Match Co",
            version=3,
            target_countries=["US"],
            blocked_buyers=["Department of Energy"],
            notice_types_wanted=["rfp", "combined"],
            remote_ok=True,
            target_buyers=["Internal Revenue Service"],
            annual_revenue=[
                {"fiscal_year": 2023, "amount": "10000000.00", "currency": "USD"},
                {"fiscal_year": 2024, "amount": "20000000.00", "currency": "USD"},
                {"fiscal_year": 2025, "amount": "30000000.00", "currency": "USD"},
            ],
            employee_count_total=120,
            scoring_weights={
                "code_match": 30,
                "semantic_similarity": 20,
                "keyword_match": 10,
                "eligibility": 15,
                "value_fit": 5,
                "geography": 5,
                "buyer_affinity": 5,
                "past_performance_relevance": 10,
            },
        )
        opp = Opportunity(
            source_id="sam_opps",
            external_id=f"m4-{uuid.uuid4().hex[:8]}",
            region=Region.US,
            country="US",
            currency="USD",
            notice_type=NoticeType.RFP,
            title="Cloud migration services",
            description_text="Move workloads to the cloud",
            solicitation_number="47QF-26-R-0001",
            buyer_org="Department of the Treasury",
            buyer_sub_org="Internal Revenue Service",
            buyer_hierarchy=["Department of the Treasury", "Internal Revenue Service"],
            naics=["541511"],
            psc=["D302"],
            set_aside="SBA",
            place_of_performance={"state": "VA", "country": "US", "remote": False},
            estimated_value_min=Decimal("100000"),
            estimated_value_max=Decimal("500000"),
            estimated_value_min_usd=Decimal("100000"),
            estimated_value_max_usd=Decimal("500000"),
            response_due_at=NOW + timedelta(days=30),
            version=2,
        )
        session.add_all([profile, opp])
        await session.flush()
        session.add_all(
            [
                ProfileCode(
                    tenant_id=tenant.id,
                    profile_id=profile.id,
                    scheme=CodeScheme.NAICS,
                    code="541512",
                    is_primary=False,
                ),
                ProfileCode(
                    tenant_id=tenant.id,
                    profile_id=profile.id,
                    scheme=CodeScheme.NAICS,
                    code="541511",
                    is_primary=True,
                ),
                ProfileCode(
                    tenant_id=tenant.id, profile_id=profile.id, scheme=CodeScheme.PSC, code="D302"
                ),
                ProfileKeyword(
                    tenant_id=tenant.id,
                    profile_id=profile.id,
                    kind=KeywordKind.INCLUDE,
                    term="cloud migration",
                    weight=Decimal("2.5"),
                ),
                ProfileKeyword(
                    tenant_id=tenant.id,
                    profile_id=profile.id,
                    kind=KeywordKind.EXCLUDE,
                    term="janitorial",
                ),
                Certification(
                    tenant_id=tenant.id,
                    profile_id=profile.id,
                    kind=CertificationKind.WOSB,
                    expires_on=date(2027, 1, 1),
                ),
                PastPerformance(
                    tenant_id=tenant.id,
                    profile_id=profile.id,
                    title="IRS modernization",
                    customer="Internal Revenue Service",
                    role=PerformanceRole.PRIME,
                    scope="scope",
                ),
                PastPerformance(
                    tenant_id=tenant.id,
                    profile_id=profile.id,
                    title="IRS modernization 2",
                    customer="Internal Revenue Service",
                    role=PerformanceRole.SUB,
                    scope="scope",
                ),
            ]
        )
        await session.flush()
        return tenant.id, profile.id, opp.id


async def test_matches_table_unique_per_versions_and_rls(database: Database) -> None:
    tenant_id, profile_id, opp_id = await _seed(database)
    async with database.owner_session() as session:
        other, _, _ = await create_tenant_with_owner(session)
        other_id = other.id
    row = {
        "tenant_id": tenant_id,
        "profile_id": profile_id,
        "opportunity_id": opp_id,
        "opportunity_version": 2,
        "profile_version": 3,
        "score": Decimal("72.50"),
        "band": MatchBand.HIGH.value,
        "breakdown": {"signals": {"code_match": {"raw": 1.0, "weight": 25, "weighted": 25.0}}},
    }
    async with database.session(tenant_id) as session:
        session.add(Match(**row))
        await session.flush()
        # a new opportunity version is a new row; the same versions are not
        session.add(Match(**{**row, "opportunity_version": 3}))
        await session.flush()
    with pytest.raises(IntegrityError, match="uq_matches_profile_opportunity_versions"):
        async with database.session(tenant_id) as session:
            session.add(Match(**row))
            await session.flush()
    async with database.session(tenant_id) as session:
        rows = (await session.execute(select(Match).order_by(Match.opportunity_version))).scalars()
        rows = list(rows)
        assert [r.opportunity_version for r in rows] == [2, 3]
        assert rows[0].score == Decimal("72.50") and rows[0].band == "high"
        assert rows[0].breakdown["signals"]["code_match"]["weight"] == 25
        assert rows[0].rationale is None and rows[0].filtered_reason is None
        assert rows[0].ineligible_set_aside is False
        assert rows[0].created_at.tzinfo is not None
    async with database.session(other_id) as session:
        assert (await session.execute(select(Match))).scalars().all() == []
    # the other tenant cannot write rows into A either (WITH CHECK)
    with pytest.raises(Exception, match=r"row-level security|violates"):
        async with database.session(other_id) as session:
            session.add(Match(**{**row, "opportunity_version": 4}))
            await session.flush()
    async with database.owner_engine.connect() as conn:
        forced = (
            await conn.execute(
                text("SELECT relforcerowsecurity FROM pg_class WHERE relname = 'matches'")
            )
        ).scalar()
        assert forced is True


async def test_filtered_row_shape(database: Database) -> None:
    tenant_id, profile_id, opp_id = await _seed(database)
    async with database.session(tenant_id) as session:
        session.add(
            Match(
                tenant_id=tenant_id,
                profile_id=profile_id,
                opportunity_id=opp_id,
                opportunity_version=2,
                profile_version=3,
                score=Decimal("0"),
                band=MatchBand.FILTERED.value,
                filtered_reason="blocked_buyer",
            )
        )
        await session.flush()
    async with database.session(tenant_id) as session:
        row = (await session.execute(select(Match))).scalar_one()
        assert row.band == "filtered" and row.filtered_reason == "blocked_buyer"
        assert row.breakdown == {}


async def test_loaders_build_pure_inputs_and_filters_run_on_them(database: Database) -> None:
    tenant_id, profile_id, opp_id = await _seed(database)
    async with database.session(tenant_id) as session:
        profile_row = await session.get(CompanyProfile, profile_id)
        assert profile_row is not None
        profile = await load_match_profile(session, profile_row)
    assert profile.region == "us" and profile.id == str(profile_id) and profile.version == 3
    assert profile.codes == {"naics": ("541511", "541512"), "psc": ("D302",)}  # primary first
    assert [(k.term, k.weight) for k in profile.include_keywords] == [
        ("cloud migration", Decimal("2.5"))
    ]
    assert profile.exclude_keywords == ("janitorial",)
    assert profile.avg_receipts_usd == Decimal("20000000.00")
    assert profile.employee_count_total == 120
    assert [(c.kind, c.expires_on) for c in profile.certifications] == [("wosb", date(2027, 1, 1))]
    assert profile.past_customers == ("Internal Revenue Service",)  # de-duplicated
    assert profile.target_buyers == ("Internal Revenue Service",)
    assert profile.blocked_buyers == ("Department of Energy",)
    assert profile.remote_ok is True
    assert profile.scoring_weights["code_match"] == 30
    assert profile.eligibility_in is None  # US profile

    async with database.session(None) as session:
        opp_row = await session.get(Opportunity, opp_id)
        assert opp_row is not None
        opp = match_opportunity_from_row(opp_row)
        assert await is_recompete_watch(session, opp_row) is False
        loaded = await load_match_opportunity(session, opp_row)
    assert opp == loaded
    assert opp.id == str(opp_id) and opp.version == 2
    assert opp.summary == "Move workloads to the cloud"  # description when no summary_ai
    assert opp.buyers == ("Department of the Treasury", "Internal Revenue Service")
    assert opp.naics == ("541511",) and opp.psc == ("D302",)
    assert opp.set_aside == "SBA" and opp.status == "open"
    assert opp.place_of_performance == {"state": "VA", "country": "US", "remote": False}
    assert opp.estimated_value_max_usd == Decimal("500000.00")
    assert opp.recompete_watch is False

    result = hard_filters(profile, opp, NOW)
    assert result.keep is True and result.cap is None  # $20M avg is small for 541511


async def test_recompete_watch_from_awards_enrichment_and_summary_preference(
    database: Database,
) -> None:
    tenant_id, profile_id, opp_id = await _seed(database)
    async with database.owner_session() as session:
        opp_row = await session.get(Opportunity, opp_id)
        assert opp_row is not None
        opp_row.summary_ai = "Five-line summary"
        opp_row.response_due_at = NOW - timedelta(days=1)
        session.add(
            AwardsEnrichment(
                opportunity_id=None,
                source_id="usaspending",
                award_id="AW-1",
                solicitation_number="47QF-26-R-0001",
                recompete_watch=True,
                match_method="recompete_candidate",
            )
        )
        await session.flush()
    async with database.session(None) as session:
        opp_row = await session.get(Opportunity, opp_id)
        assert opp_row is not None
        opp = await load_match_opportunity(session, opp_row)
    assert opp.summary == "Five-line summary"
    assert opp.recompete_watch is True
    async with database.session(tenant_id) as session:
        profile_row = await session.get(CompanyProfile, profile_id)
        assert profile_row is not None
        profile = await load_match_profile(session, profile_row)
    # past due, but on recompete watch: kept (SPEC 6 stage 1)
    assert hard_filters(profile, opp, NOW).keep is True


async def test_in_profile_loader_carries_eligibility_snapshot(database: Database) -> None:
    async with database.owner_session() as session:
        tenant, _, _ = await create_tenant_with_owner(
            session, region=Region.IN, data_residency=Region.IN
        )
        profile = CompanyProfile(
            tenant_id=tenant.id,
            region=Region.IN,
            legal_name="Bharat Tech Pvt Ltd",
            udyam_number="UDYAM-TS-01-0001234",
            udyam_category=UdyamCategory.SMALL,
            dpiit_number="DIPP1234",
            gem_seller_id="GEM-1",
            year_founded=2015,
            annual_revenue=[
                {"fiscal_year": 2024, "amount": "15000000.00", "currency": "INR"},
                {"fiscal_year": 2025, "amount": "25000000.00", "currency": "INR"},
            ],
        )
        session.add(profile)
        await session.flush()
        session.add(
            Registration(
                tenant_id=tenant.id,
                profile_id=profile.id,
                kind=RegistrationKind.DSC,
                identifier="DSC-1",
                expires_on=date(2027, 6, 30),
            )
        )
        await session.flush()
        tenant_id, profile_id = tenant.id, profile.id
    async with database.session(tenant_id) as session:
        row = await session.get(CompanyProfile, profile_id)
        assert row is not None
        loaded = await load_match_profile(session, row)
    assert loaded.region == "in" and loaded.is_mse is True
    assert loaded.udyam_category == "small" and loaded.dpiit_number == "DIPP1234"
    assert loaded.avg_receipts_usd is None  # INR turnover never feeds SBA receipts
    assert loaded.eligibility_in is not None
    assert loaded.eligibility_in.year_founded == 2015
    assert [r.kind for r in loaded.eligibility_in.registrations] == ["dsc"]
    assert len(loaded.eligibility_in.revenue) == 2


def test_usd_receipts_rules() -> None:
    assert usd_receipts(None) is None
    assert usd_receipts([]) is None
    assert usd_receipts([{"fiscal_year": 2025, "amount": "5", "currency": "INR"}]) is None
    mixed = [
        {"fiscal_year": 2024, "amount": "5", "currency": "INR"},
        {"fiscal_year": 2025, "amount": "5", "currency": "USD"},
    ]
    assert usd_receipts(mixed) is None
    assert usd_receipts([{"fiscal_year": 2025, "amount": "5.00", "currency": "USD"}]) == Decimal(
        "5.00"
    )


async def test_loaded_profile_feeds_the_eligibility_signal(database: Database) -> None:
    """M4-04: registrations / SAM status reach the US path through the loader."""
    from app.core.matching.eligibility_signal import eligibility_signal
    from app.core.profile_fields import SamStatus

    tenant_id, profile_id, opp_id = await _seed(database)
    async with database.owner_session() as session:
        profile_row = await session.get(CompanyProfile, profile_id)
        assert profile_row is not None
        profile_row.sam_status = SamStatus.ACTIVE
        profile_row.sam_expires_on = date(2027, 3, 1)
        profile_row.year_founded = 2010
        session.add(
            Registration(
                tenant_id=tenant_id,
                profile_id=profile_id,
                kind=RegistrationKind.STATE_PORTAL,
                identifier="eVA",
                expires_on=date(2027, 1, 1),
            )
        )
        opp_row = await session.get(Opportunity, opp_id)
        assert opp_row is not None
        opp_row.eligibility = {
            "min_experience_years": 5,
            "required_certifications": ["WOSB"],
            "required_registrations": ["sam", "state_portal"],
        }
        await session.flush()
    async with database.session(tenant_id) as session:
        row = await session.get(CompanyProfile, profile_id)
        assert row is not None
        profile = await load_match_profile(session, row)
    async with database.session(None) as session:
        opp_row = await session.get(Opportunity, opp_id)
        assert opp_row is not None
        opp = match_opportunity_from_row(opp_row)
    assert profile.year_founded == 2010 and profile.sam_status == "active"
    assert [(r.kind, r.identifier) for r in profile.registrations] == [("state_portal", "eVA")]
    signal = eligibility_signal(profile, opp, NOW.date())
    statuses = {c["name"]: c["status"] for c in signal.detail["criteria"]}
    assert statuses == {
        "size_status": "pass",  # SBA set-aside, $20M avg receipts under the 541511 cap
        "experience": "pass",
        "certification:wosb": "pass",
        "registration:sam": "pass",
        "registration:state_portal": "pass",
    }
    assert signal.raw == Decimal("1")
