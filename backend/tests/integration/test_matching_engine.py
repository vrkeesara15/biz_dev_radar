"""M4-06: match trigger, batch scoring, events and the per-tenant scoping of both."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
import structlog
from app.core.config import Region, Settings
from app.core.db import Database
from app.core.opportunity import NoticeType, OpportunityStatus
from app.core.profile_fields import CodeScheme, KeywordKind, PerformanceRole
from app.core.roles import Role
from app.models import (
    AwardsEnrichment,
    CompanyProfile,
    Match,
    Opportunity,
    PastPerformance,
    ProfileCode,
    ProfileKeyword,
    ServiceLine,
)
from app.services.embeddings import FakeEmbeddings
from app.services.events import MATCH_HIGH, MATCH_MEDIUM, PROFILE_CHANGED, EventBus, Recorder
from app.services.knowledge_base import index_profile
from app.services.matching.engine import (
    MatchScorer,
    candidate_opportunities,
    candidate_profiles,
)
from app.services.matching.loaders import load_match_profile
from app.services.matching.triggers import MatchTrigger
from sqlalchemy import func, select

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner

log = structlog.get_logger(__name__)

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)

CLOUD_BODY = (
    "The contractor shall migrate legacy mainframe workloads to a commercial cloud with "
    "devops automation and kubernetes container orchestration."
)


def _settings() -> Settings:
    return Settings(_env_file=None, celery_task_always_eager=True)  # type: ignore[call-arg]


def _opportunity(title: str, body: str, **overrides: object) -> Opportunity:
    values: dict[str, object] = {
        "source_id": "sam_opps",
        "external_id": f"m406-{uuid.uuid4().hex[:10]}",
        "region": Region.US,
        "country": "US",
        "currency": "USD",
        "notice_type": NoticeType.RFP,
        "status": OpportunityStatus.OPEN,
        "title": title,
        "description_text": body,
        "naics": ["541511"],
        "response_due_at": NOW + timedelta(days=30),
        "version": 1,
    }
    values.update(overrides)
    return Opportunity(**values)  # type: ignore[arg-type]


async def _complete_profile(session, tenant_id, **overrides):  # type: ignore[no-untyped-def]
    """A profile whose completeness clears the matching threshold (>= 40)."""
    values: dict[str, object] = {
        "region": Region.US,
        "legal_name": "Cloud Movers LLC",
        "version": 1,
        "uei": f"UEI{uuid.uuid4().hex[:9].upper()}",
        "ein": "12-3456789",
        "website": "https://example.test",
        "year_founded": 2010,
        "employee_count_total": 120,
        "target_countries": ["US"],
        "target_us_states": ["VA"],
        "remote_ok": True,
        "notice_types_wanted": ["rfp", "combined"],
        "annual_revenue": [
            {"fiscal_year": 2024, "amount": "10000000.00", "currency": "USD"},
            {"fiscal_year": 2025, "amount": "12000000.00", "currency": "USD"},
        ],
        "value_min_usd": Decimal("50000"),
        "value_max_usd": Decimal("5000000"),
    }
    values.update(overrides)
    profile = CompanyProfile(tenant_id=tenant_id, **values)
    session.add(profile)
    await session.flush()
    session.add_all(
        [
            ProfileCode(
                tenant_id=tenant_id,
                profile_id=profile.id,
                scheme=CodeScheme.NAICS,
                code="541511",
                is_primary=True,
            ),
            ProfileKeyword(
                tenant_id=tenant_id,
                profile_id=profile.id,
                kind=KeywordKind.INCLUDE,
                term="cloud migration",
            ),
            ServiceLine(
                tenant_id=tenant_id,
                profile_id=profile.id,
                name="Cloud migration",
                description="We migrate mainframe workloads to commercial cloud platforms.",
            ),
            PastPerformance(
                tenant_id=tenant_id,
                profile_id=profile.id,
                title="Treasury mainframe migration",
                customer="Department of the Treasury",
                role=PerformanceRole.PRIME,
                scope="Migrated mainframe workloads to a cloud platform.",
            ),
        ]
    )
    await session.flush()
    return profile


async def _seed(database: Database, embeddings: FakeEmbeddings):  # type: ignore[no-untyped-def]
    async with database.owner_session() as session:
        tenant, _, _ = await create_tenant_with_owner(session)
        profile = await _complete_profile(session, tenant.id)
        hit = _opportunity("Cloud migration services", CLOUD_BODY)
        miss = _opportunity(
            "Grounds maintenance and landscaping",
            "Mow lawns, trim hedges and remove snow at the campus.",
            naics=["561730"],
        )
        session.add_all([hit, miss])
        await session.flush()
        ids = (tenant.id, profile.id, hit.id, miss.id)
    async with database.session(ids[0]) as session:
        await index_profile(session, ids[1], embeddings=embeddings)
    return ids


def _scorer(database: Database, fake_embeddings: FakeEmbeddings, bus: EventBus) -> MatchScorer:
    return MatchScorer(
        settings=_settings(),
        database=database,
        embeddings=fake_embeddings,
        rationale=None,  # stage 3 has its own tests (M4-05)
        bus=bus,
        now=NOW,
    )


# --- candidate selection -------------------------------------------------------------------


async def test_only_active_complete_profiles_of_the_region_are_candidates(
    database: Database,
) -> None:
    async with database.owner_session() as session:
        tenant, _, _ = await create_tenant_with_owner(session)
        good = await _complete_profile(session, tenant.id)
        inactive = await _complete_profile(session, tenant.id, is_active=False)
        other_region = await _complete_profile(
            session,
            tenant.id,
            region=Region.IN,
            uei=None,
            ein=None,
            target_countries=["IN"],
        )
        thin = CompanyProfile(tenant_id=tenant.id, region=Region.US, legal_name="Thin Co")
        session.add(thin)
        await session.flush()
        ids = (tenant.id, good.id, inactive.id, other_region.id, thin.id)
    candidates = await candidate_profiles(database, region="us")
    found = {c.profile_id for c in candidates}
    assert ids[1] in found
    assert ids[2] not in found  # is_active = false
    assert ids[3] not in found  # IN region
    assert ids[4] not in found  # completeness below 40


async def test_candidate_opportunity_sql_applies_the_cheap_filters(
    database: Database, fake_embeddings: FakeEmbeddings
) -> None:
    tenant_id, profile_id, hit_id, _ = await _seed(database, fake_embeddings)
    async with database.owner_session() as session:
        rows = [
            _opportunity("Closed notice", "x", status=OpportunityStatus.CLOSED),
            _opportunity("Past due", "x", response_due_at=NOW - timedelta(days=1)),
            _opportunity("Wrong region", "x", region=Region.IN, country="IN"),
            _opportunity("Wrong country", "x", country="CA"),
            _opportunity("Unwanted type", "x", notice_type=NoticeType.AWARD),
            _opportunity("No deadline", "x", response_due_at=None),
        ]
        session.add_all(rows)
        await session.flush()
        no_deadline_id = rows[-1].id
    async with database.session(tenant_id) as session:
        row = await session.get(CompanyProfile, profile_id)
        assert row is not None
        profile = await load_match_profile(session, row)
        found = (await session.execute(candidate_opportunities(profile, now=NOW))).scalars().all()
    titles = {r.title for r in found}
    assert "Cloud migration services" in titles
    assert "No deadline" in titles  # an unknown deadline is not "past" (OQ-70)
    for dropped in ("Closed notice", "Past due", "Wrong region", "Wrong country", "Unwanted type"):
        assert dropped not in titles
    assert no_deadline_id in {r.id for r in found} and hit_id in {r.id for r in found}


async def test_a_recompete_watch_keeps_a_past_due_notice(
    database: Database, fake_embeddings: FakeEmbeddings
) -> None:
    tenant_id, profile_id, _, _ = await _seed(database, fake_embeddings)
    async with database.owner_session() as session:
        stale = _opportunity(
            "Recompete of cloud migration",
            CLOUD_BODY,
            response_due_at=NOW - timedelta(days=5),
            status=OpportunityStatus.CLOSED,
            solicitation_number="RC-0001",
        )
        session.add(stale)
        await session.flush()
        session.add(
            AwardsEnrichment(
                opportunity_id=None,
                source_id="usaspending",
                award_id="AW-9",
                solicitation_number="RC-0001",
                recompete_watch=True,
                match_method="recompete_candidate",
            )
        )
        await session.flush()
    async with database.session(tenant_id) as session:
        row = await session.get(CompanyProfile, profile_id)
        assert row is not None
        profile = await load_match_profile(session, row)
        found = (await session.execute(candidate_opportunities(profile, now=NOW))).scalars().all()
    assert "Recompete of cloud migration" in {r.title for r in found}


# --- scoring ---------------------------------------------------------------------------------


async def test_scoring_one_opportunity_writes_a_match_and_emits_an_event(
    database: Database, fake_embeddings: FakeEmbeddings
) -> None:
    tenant_id, profile_id, hit_id, miss_id = await _seed(database, fake_embeddings)
    bus, recorder = EventBus(), Recorder()
    bus.subscribe("*", recorder)
    run = await _scorer(database, fake_embeddings, bus).score_opportunity(hit_id)
    assert run.profiles == 1 and run.opportunities == 1 and run.kept == 1
    assert run.created == 1 and run.updated == 0
    async with database.session(tenant_id) as session:
        match = (await session.execute(select(Match))).scalar_one()
        assert match.profile_id == profile_id and match.opportunity_id == hit_id
        assert match.opportunity_version == 1 and match.profile_version == 1
        assert match.band in {"high", "medium", "low"}
        assert match.breakdown["signals"]["code_match"]["raw"] == 1.0
        assert match.rationale is None  # no rationale generator on this scorer
    names = [e.name for e in recorder.events]
    if match.band == "high":
        assert names == [MATCH_HIGH]
    elif match.band == "medium":
        assert names == [MATCH_MEDIUM]
    if names:
        payload = recorder.events[0].payload
        assert payload["tenant_id"] == str(tenant_id)
        assert payload["profile_id"] == str(profile_id)
        assert payload["opportunity_id"] == str(hit_id)
        assert payload["version"] == 1
        assert payload["band"] == match.band
        assert payload["score"] == float(match.score)
        assert uuid.UUID(payload["match_id"]) == match.id
    assert miss_id is not None


async def test_rescoring_the_same_versions_updates_and_emits_nothing_new(
    database: Database, fake_embeddings: FakeEmbeddings
) -> None:
    tenant_id, _, hit_id, _ = await _seed(database, fake_embeddings)
    bus, recorder = EventBus(), Recorder()
    bus.subscribe("*", recorder)
    scorer = _scorer(database, fake_embeddings, bus)
    first = await scorer.score_opportunity(hit_id)
    first_events = len(recorder.events)
    second = await scorer.score_opportunity(hit_id)
    assert first.created == 1 and second.created == 0 and second.updated == 1
    assert len(recorder.events) == first_events  # no duplicate alert
    async with database.session(tenant_id) as session:
        assert (await session.execute(select(func.count()).select_from(Match))).scalar() == 1


async def test_a_filtered_pair_is_not_stored(
    database: Database, fake_embeddings: FakeEmbeddings
) -> None:
    tenant_id, profile_id, _, _ = await _seed(database, fake_embeddings)
    async with database.owner_session() as session:
        profile = await session.get(CompanyProfile, profile_id)
        assert profile is not None
        profile.blocked_buyers = ["Department of Energy"]
        blocked = _opportunity(
            "Cloud migration services", CLOUD_BODY, buyer_org="Department of Energy"
        )
        session.add(blocked)
        await session.flush()
        blocked_id = blocked.id
    run = await _scorer(database, fake_embeddings, EventBus()).score_opportunity(blocked_id)
    assert run.filtered == 1 and run.kept == 0 and run.created == 0
    async with database.session(tenant_id) as session:
        assert (await session.execute(select(Match))).scalars().all() == []


async def test_matches_never_cross_tenants(
    database: Database, fake_embeddings: FakeEmbeddings
) -> None:
    tenant_a, _, hit_id, _ = await _seed(database, fake_embeddings)
    async with database.owner_session() as session:
        tenant_b, _, _ = await create_tenant_with_owner(session)
        await _complete_profile(session, tenant_b.id, legal_name="Rival Inc")
        tenant_b_id = tenant_b.id
    async with database.session(tenant_b_id) as session:
        await index_profile(
            session,
            (await session.execute(select(CompanyProfile.id))).scalar_one(),
            embeddings=fake_embeddings,
        )
    run = await _scorer(database, fake_embeddings, EventBus()).score_opportunity(hit_id)
    assert run.profiles == 2  # both tenants have an eligible profile
    async with database.session(tenant_a) as session:
        mine = (await session.execute(select(Match))).scalars().all()
        assert len(mine) == 1 and mine[0].tenant_id == tenant_a
    async with database.session(tenant_b_id) as session:
        theirs = (await session.execute(select(Match))).scalars().all()
        assert len(theirs) == 1 and theirs[0].tenant_id == tenant_b_id


async def test_rescore_profile_covers_the_open_corpus_only(
    database: Database, fake_embeddings: FakeEmbeddings
) -> None:
    tenant_id, profile_id, hit_id, miss_id = await _seed(database, fake_embeddings)
    async with database.owner_session() as session:
        closed = _opportunity("Closed cloud migration", CLOUD_BODY, status=OpportunityStatus.CLOSED)
        session.add(closed)
        await session.flush()
        closed_id = closed.id
    run = await _scorer(database, fake_embeddings, EventBus()).rescore_profile(
        tenant_id, profile_id
    )
    assert run.profiles == 1
    async with database.session(tenant_id) as session:
        scored = {m.opportunity_id for m in (await session.execute(select(Match))).scalars()}
    assert hit_id in scored
    assert closed_id not in scored
    # the landscaping notice survives stage 1 (its notice type is wanted) but scores low
    assert miss_id in scored or run.filtered >= 1


async def test_score_batch_scores_every_tenant(
    database: Database, fake_embeddings: FakeEmbeddings
) -> None:
    tenant_id, _, hit_id, _ = await _seed(database, fake_embeddings)
    run = await _scorer(database, fake_embeddings, EventBus()).score_batch()
    assert run.profiles == 1 and run.opportunities >= 2
    async with database.session(tenant_id) as session:
        rows = (await session.execute(select(Match))).scalars().all()
    assert {r.opportunity_id for r in rows} >= {hit_id}


async def test_incomplete_profile_is_never_scored(
    database: Database, fake_embeddings: FakeEmbeddings
) -> None:
    async with database.owner_session() as session:
        tenant, _, _ = await create_tenant_with_owner(session)
        thin = CompanyProfile(tenant_id=tenant.id, region=Region.US, legal_name="Thin Co")
        opp = _opportunity("Cloud migration services", CLOUD_BODY)
        session.add_all([thin, opp])
        await session.flush()
        tenant_id, opp_id = tenant.id, opp.id
    run = await _scorer(database, fake_embeddings, EventBus()).score_opportunity(opp_id)
    assert run.profiles == 0 and run.created == 0
    async with database.session(tenant_id) as session:
        assert (await session.execute(select(Match))).scalars().all() == []


# --- triggers --------------------------------------------------------------------------------


async def test_the_trigger_scores_after_the_publishing_transaction_commits(
    database: Database, fake_embeddings: FakeEmbeddings
) -> None:
    from app.services.events import OPPORTUNITY_CREATED

    tenant_id, _, _, _ = await _seed(database, fake_embeddings)
    bus = EventBus()
    trigger = MatchTrigger(
        settings=_settings(),
        database=database,
        scorer=_scorer(database, fake_embeddings, bus),
        bus=bus,
    ).subscribe(bus)
    async with database.owner_session() as session:
        fresh = _opportunity("Cloud migration services for the IRS", CLOUD_BODY)
        session.add(fresh)
        await session.flush()
        fresh_id = fresh.id
        await bus.publish(
            OPPORTUNITY_CREATED,
            {"opportunity_id": str(fresh_id)},
            context={"session": session},
        )
        # nothing has run yet: the notice is not committed
        async with database.session(tenant_id) as probe:
            assert (await probe.execute(select(Match))).scalars().all() == []
    await trigger.drain()
    assert trigger.scheduled == [("opportunity", str(fresh_id))]
    async with database.session(tenant_id) as session:
        rows = (await session.execute(select(Match))).scalars().all()
    assert [r.opportunity_id for r in rows] == [fresh_id]


async def test_profile_changed_reschedules_a_rescore(
    database: Database, fake_embeddings: FakeEmbeddings
) -> None:
    tenant_id, profile_id, hit_id, _ = await _seed(database, fake_embeddings)
    bus = EventBus()
    trigger = MatchTrigger(
        settings=_settings(),
        database=database,
        scorer=_scorer(database, fake_embeddings, bus),
        bus=bus,
    ).subscribe(bus)
    await bus.publish(
        PROFILE_CHANGED,
        {"tenant_id": str(tenant_id), "profile_id": str(profile_id), "version": 1},
    )
    await trigger.drain()
    assert trigger.scheduled == [("profile", str(profile_id))]
    async with database.session(tenant_id) as session:
        rows = (await session.execute(select(Match))).scalars().all()
    assert hit_id in {r.opportunity_id for r in rows}


async def test_a_scoring_failure_never_escapes_the_trigger(
    database: Database, fake_embeddings: FakeEmbeddings
) -> None:
    class Exploding:
        async def score_opportunity(self, opportunity_id: uuid.UUID) -> None:
            raise RuntimeError("boom")

    bus = EventBus()
    trigger = MatchTrigger(
        settings=_settings(),
        database=database,
        scorer=Exploding(),  # type: ignore[arg-type]
        bus=bus,
    ).subscribe(bus)
    from app.services.events import OPPORTUNITY_AMENDED

    await bus.publish(OPPORTUNITY_AMENDED, {"opportunity_id": str(uuid.uuid4())})
    await trigger.drain()  # no exception


# --- API wiring ------------------------------------------------------------------------------


async def test_putting_a_profile_publishes_profile_changed(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    from app.services.events import get_event_bus, set_event_bus

    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(session)
        tenant_id, user_id = tenant.id, user.id
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, role=Role.TENANT_OWNER)
    bus, recorder = EventBus(), Recorder()
    bus.subscribe(PROFILE_CHANGED, recorder)
    previous = get_event_bus()
    set_event_bus(bus)
    try:
        created = await api_client.post(
            "/api/v1/profiles",
            json={"region": "us", "legal_name": "Event Co"},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        profile_id = created.json()["id"]
        renamed = await api_client.put(
            f"/api/v1/profiles/{profile_id}",
            json={"legal_name": "Renamed Co"},
            headers=headers,
        )
        assert renamed.status_code == 200, renamed.text
        # an identical re-PUT changes nothing, so no event
        again = await api_client.put(
            f"/api/v1/profiles/{profile_id}",
            json={"legal_name": "Renamed Co"},
            headers=headers,
        )
        assert again.status_code == 200
        # a child write bumps the version and publishes too
        code = await api_client.post(
            f"/api/v1/profiles/{profile_id}/codes",
            json={"scheme": "naics", "code": "541511", "is_primary": True},
            headers=headers,
        )
        assert code.status_code == 201, code.text
    finally:
        set_event_bus(previous)
    events = recorder.named(PROFILE_CHANGED)
    assert len(events) == 2
    assert events[0].payload["profile_id"] == profile_id
    assert events[0].payload["fields"] == ["legal_name"]
    assert events[1].payload["fields"] == ["codes"]
    assert events[1].payload["version"] > events[0].payload["version"]


# --- load shape -------------------------------------------------------------------------------


@pytest.mark.load
async def test_two_thousand_notices_by_twenty_profiles_under_a_minute(
    database: Database, fake_embeddings: FakeEmbeddings
) -> None:
    """SPEC 12 asks for 50k x 200 < 10 min in the load script (M7-10). This is the same
    shape at 1/25 of the width and 1/10 of the depth, so the local budget is 60 s.

    Measured on the dev compose Postgres: 40,000 pairs in ~22 s, i.e. ~1,800 pairs/s in
    ONE process with every pair surviving stage 1 and being written. 50k x 200 is 10 M
    pairs, so a single process needs ~90 min and the SPEC target only falls out when the
    batch is sharded: profiles are embarrassingly parallel, so M7-10's load script must
    fan `bidradar.score_batch` out per profile (or per tenant) across workers, and a real
    corpus prunes most pairs in `candidate_opportunities` before they are ever scored
    (OQ-95)."""
    notices = 2_000
    profiles = 20
    async with database.owner_session() as session:
        tenant, _, _ = await create_tenant_with_owner(session)
        profile_ids = []
        for index in range(profiles):
            row = await _complete_profile(session, tenant.id, legal_name=f"Bidder {index}")
            profile_ids.append(row.id)
        for index in range(notices):
            row = _opportunity(
                f"Cloud migration services lot {index}",
                f"{CLOUD_BODY} Lot {index}.",
            )
            row.embedding = fake_embeddings.vector(f"cloud migration lot {index}")
            session.add(row)
            if index % 500 == 499:
                await session.flush()
        await session.flush()
        tenant_id = tenant.id
    async with database.session(tenant_id) as session:
        for profile_id in profile_ids:
            await index_profile(session, profile_id, embeddings=fake_embeddings)
    started = time.monotonic()
    run = await _scorer(database, fake_embeddings, EventBus()).score_batch(tenant_id=tenant_id)
    elapsed = time.monotonic() - started
    log.info("matching.load_shape", pairs=run.opportunities, seconds=round(elapsed, 2))
    assert run.profiles == profiles
    assert run.opportunities == notices * profiles
    assert run.created == notices * profiles
    assert elapsed < 60, f"scored {run.opportunities} pairs in {elapsed:.1f}s"
