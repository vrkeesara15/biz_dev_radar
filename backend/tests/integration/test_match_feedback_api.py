"""M4-07: POST /opportunities/{id}/feedback, the weekly re-tune job and approving a
suggestion (SPEC 6 learning loop)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from app.core.config import Region
from app.core.db import Database
from app.core.opportunity import NoticeType
from app.core.profile_fields import KeywordKind
from app.core.roles import Role
from app.jobs.retune_keywords import retune_keywords_job
from app.models import (
    AuditLog,
    CompanyProfile,
    KeywordSuggestion,
    Match,
    MatchFeedback,
    Opportunity,
    ProfileKeyword,
)
from app.services.matching.learning import retune_profile
from sqlalchemy import select

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def _opportunity(title: str, **overrides: object) -> Opportunity:
    values: dict[str, object] = {
        "source_id": "sam_opps",
        "external_id": f"m407-{uuid.uuid4().hex[:10]}",
        "region": Region.US,
        "country": "US",
        "currency": "USD",
        "notice_type": NoticeType.RFP,
        "title": title,
        "response_due_at": NOW + timedelta(days=30),
        "version": 1,
    }
    values.update(overrides)
    return Opportunity(**values)  # type: ignore[arg-type]


async def _seed(database: Database, titles: list[str]):  # type: ignore[no-untyped-def]
    """A tenant with one profile and one scored match per title."""
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(session)
        profile = CompanyProfile(
            tenant_id=tenant.id, region=Region.US, legal_name="Cloud Movers LLC", version=1
        )
        session.add(profile)
        await session.flush()
        matches: list[tuple[uuid.UUID, uuid.UUID]] = []
        for title in titles:
            opp = _opportunity(title)
            session.add(opp)
            await session.flush()
            match = Match(
                tenant_id=tenant.id,
                profile_id=profile.id,
                opportunity_id=opp.id,
                opportunity_version=1,
                profile_version=1,
                score=Decimal("72.00"),
                band="high",
                breakdown={},
            )
            session.add(match)
            await session.flush()
            matches.append((opp.id, match.id))
        return tenant.id, user.id, profile.id, matches


# --- the feedback route ---------------------------------------------------------------------


async def test_feedback_is_recorded_once_per_user_and_audited(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tenant_id, user_id, profile_id, matches = await _seed(database, ["Cloud migration services"])
    opp_id, match_id = matches[0]
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, role=Role.BID_MANAGER)
    first = await api_client.post(
        f"/api/v1/opportunities/{opp_id}/feedback",
        json={"thumb": "down", "reason": "wrong size band"},
        headers=headers,
    )
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["thumb"] == "down" and body["reason"] == "wrong size band"
    assert body["match_id"] == str(match_id) and body["profile_id"] == str(profile_id)
    # a changed mind replaces the row instead of adding a second one
    second = await api_client.post(
        f"/api/v1/opportunities/{opp_id}/feedback",
        json={"thumb": "up"},
        headers=headers,
    )
    assert second.status_code == 201, second.text
    assert second.json()["id"] == body["id"]
    assert second.json()["thumb"] == "up" and second.json()["reason"] is None
    async with database.session(tenant_id) as session:
        rows = (await session.execute(select(MatchFeedback))).scalars().all()
        assert len(rows) == 1 and rows[0].thumb == "up"
        actions = (await session.execute(select(AuditLog.action))).scalars().all()
    assert actions.count("opportunity.feedback") == 2


async def test_feedback_without_a_match_is_404(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(session)
        opp = _opportunity("Unscored notice")
        session.add(opp)
        await session.flush()
        tenant_id, user_id, opp_id = tenant.id, user.id, opp.id
    response = await api_client.post(
        f"/api/v1/opportunities/{opp_id}/feedback",
        json={"thumb": "up"},
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id, role=Role.VIEWER),
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "no match for this opportunity and profile"


async def test_feedback_validates_the_thumb(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tenant_id, user_id, _, matches = await _seed(database, ["Cloud migration services"])
    response = await api_client.post(
        f"/api/v1/opportunities/{matches[0][0]}/feedback",
        json={"thumb": "sideways"},
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
    )
    assert response.status_code == 422


# --- the weekly re-tune -----------------------------------------------------------------------


async def _thumb(database: Database, tenant_id, user_id, match_id, thumb: str) -> None:  # type: ignore[no-untyped-def]
    async with database.session(tenant_id) as session:
        session.add(
            MatchFeedback(tenant_id=tenant_id, match_id=match_id, user_id=user_id, thumb=thumb)
        )
        await session.flush()


async def test_retune_writes_pending_suggestions_from_the_thumbs(database: Database) -> None:
    titles = [
        "Cloud migration for the Treasury",
        "Cloud migration of mainframe workloads",
        "Cloud migration and modernisation",
        "Janitorial custodial duties",
        "Janitorial supplies and cleaning",
        "Janitorial cleaning of offices",
    ]
    tenant_id, user_id, profile_id, matches = await _seed(database, titles)
    for index, (_opp, match_id) in enumerate(matches):
        await _thumb(database, tenant_id, user_id, match_id, "up" if index < 3 else "down")
    async with database.session(tenant_id) as session:
        written = await retune_profile(session, tenant_id, profile_id, now=NOW)
    assert written
    async with database.session(tenant_id) as session:
        rows = (await session.execute(select(KeywordSuggestion))).scalars().all()
    by_term = {r.term: r for r in rows}
    assert "cloud migration" in by_term and by_term["cloud migration"].kind == "include"
    assert by_term["cloud migration"].status == "pending"
    assert Decimal(by_term["cloud migration"].delta_weight) > 0
    assert by_term["cloud migration"].evidence["support"] == 3
    assert by_term["cloud migration"].evidence["lift"] == 0.5
    assert "janitorial" in by_term and by_term["janitorial"].kind == "exclude"
    assert Decimal(by_term["janitorial"].delta_weight) < 0
    assert all(r.tenant_id == tenant_id and r.profile_id == profile_id for r in rows)


async def test_retune_is_idempotent_and_withdraws_stale_suggestions(
    database: Database,
) -> None:
    titles = ["Cloud migration one", "Cloud migration two", "Cloud migration three", "Lawn one"]
    tenant_id, user_id, profile_id, matches = await _seed(database, titles)
    for index, (_opp, match_id) in enumerate(matches):
        await _thumb(database, tenant_id, user_id, match_id, "up" if index < 3 else "down")
    async with database.session(tenant_id) as session:
        await retune_profile(session, tenant_id, profile_id, now=NOW)
    async with database.session(tenant_id) as session:
        first = (await session.execute(select(KeywordSuggestion.id))).scalars().all()
        await retune_profile(session, tenant_id, profile_id, now=NOW)
    async with database.session(tenant_id) as session:
        second = (await session.execute(select(KeywordSuggestion.id))).scalars().all()
    assert sorted(map(str, first)) == sorted(map(str, second))  # updated in place
    # feedback older than the window no longer supports anything
    async with database.session(tenant_id) as session:
        await retune_profile(session, tenant_id, profile_id, now=NOW + timedelta(days=200))
        assert (await session.execute(select(KeywordSuggestion))).scalars().all() == []


async def test_a_decided_suggestion_is_never_proposed_again(database: Database) -> None:
    titles = ["Cloud migration one", "Cloud migration two", "Cloud migration three", "Lawn one"]
    tenant_id, user_id, profile_id, matches = await _seed(database, titles)
    for index, (_opp, match_id) in enumerate(matches):
        await _thumb(database, tenant_id, user_id, match_id, "up" if index < 3 else "down")
    async with database.session(tenant_id) as session:
        written = await retune_profile(session, tenant_id, profile_id, now=NOW)
        rejected = written[0]
        rejected.status = "rejected"
        rejected_term, rejected_kind = rejected.term, rejected.kind
        await session.flush()
    async with database.session(tenant_id) as session:
        again = await retune_profile(session, tenant_id, profile_id, now=NOW)
    assert (rejected_term, rejected_kind) not in {(r.term, r.kind) for r in again}
    async with database.session(tenant_id) as session:
        rows = (await session.execute(select(KeywordSuggestion))).scalars().all()
    still_there = [r for r in rows if r.term == rejected_term and r.kind == rejected_kind]
    assert len(still_there) == 1 and still_there[0].status == "rejected"


async def test_the_weekly_job_covers_every_tenant_with_feedback(database: Database) -> None:
    titles = ["Cloud migration one", "Cloud migration two", "Cloud migration three", "Lawn one"]
    tenant_id, user_id, _profile_id, matches = await _seed(database, titles)
    for index, (_opp, match_id) in enumerate(matches):
        await _thumb(database, tenant_id, user_id, match_id, "up" if index < 3 else "down")
    # a second tenant with matches but no thumbs is skipped
    await _seed(database, ["Cloud migration elsewhere"])
    summary = await retune_keywords_job(database=database, now=NOW)
    assert summary["tenants"] == 1 and summary["profiles"] == 1
    assert summary["suggestions"] >= 1


# --- approving -----------------------------------------------------------------------------


async def test_approving_a_suggestion_writes_the_keyword(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tenant_id, user_id, profile_id, _ = await _seed(database, ["Cloud migration services"])
    async with database.session(tenant_id) as session:
        include = KeywordSuggestion(
            tenant_id=tenant_id,
            profile_id=profile_id,
            term="cloud migration",
            kind="include",
            delta_weight=Decimal("0.8"),
            evidence={"support": 4, "lift": 0.4},
        )
        exclude = KeywordSuggestion(
            tenant_id=tenant_id,
            profile_id=profile_id,
            term="janitorial",
            kind="exclude",
            delta_weight=Decimal("-1.0"),
            evidence={"support": 3, "lift": -0.5},
        )
        session.add_all([include, exclude])
        await session.flush()
        include_id, exclude_id = include.id, exclude.id
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, role=Role.TENANT_OWNER)
    listed = await api_client.get(
        f"/api/v1/profiles/{profile_id}/keyword-suggestions?status=pending", headers=headers
    )
    assert listed.status_code == 200, listed.text
    by_term = {row["term"]: row for row in listed.json()}
    assert set(by_term) == {"cloud migration", "janitorial"}
    assert by_term["cloud migration"]["evidence"]["support"] == 4
    assert by_term["janitorial"]["kind"] == "exclude"

    approved = await api_client.put(
        f"/api/v1/profiles/{profile_id}/keyword-suggestions/{include_id}",
        json={"status": "approved"},
        headers=headers,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert approved.json()["decided_at"] is not None
    rejected = await api_client.put(
        f"/api/v1/profiles/{profile_id}/keyword-suggestions/{exclude_id}",
        json={"status": "rejected"},
        headers=headers,
    )
    assert rejected.status_code == 200
    async with database.session(tenant_id) as session:
        keywords = (await session.execute(select(ProfileKeyword))).scalars().all()
    assert len(keywords) == 1  # only the approved one was applied
    assert keywords[0].term == "cloud migration"
    assert keywords[0].kind == KeywordKind.INCLUDE
    assert keywords[0].weight == Decimal("1.8")  # 1.0 + delta_weight
    # deciding twice is a conflict
    twice = await api_client.put(
        f"/api/v1/profiles/{profile_id}/keyword-suggestions/{include_id}",
        json={"status": "approved"},
        headers=headers,
    )
    assert twice.status_code == 409


async def test_approving_an_existing_include_nudges_its_weight(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tenant_id, user_id, profile_id, _ = await _seed(database, ["Cloud migration services"])
    async with database.session(tenant_id) as session:
        session.add(
            ProfileKeyword(
                tenant_id=tenant_id,
                profile_id=profile_id,
                kind=KeywordKind.INCLUDE,
                term="cloud migration",
                weight=Decimal("2.0"),
            )
        )
        suggestion = KeywordSuggestion(
            tenant_id=tenant_id,
            profile_id=profile_id,
            term="cloud migration",
            kind="include",
            delta_weight=Decimal("-0.5"),
            evidence={},
        )
        session.add(suggestion)
        await session.flush()
        suggestion_id = suggestion.id
    response = await api_client.put(
        f"/api/v1/profiles/{profile_id}/keyword-suggestions/{suggestion_id}",
        json={"status": "approved"},
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id, role=Role.TENANT_OWNER),
    )
    assert response.status_code == 200, response.text
    async with database.session(tenant_id) as session:
        row = (await session.execute(select(ProfileKeyword))).scalar_one()
    assert row.weight == Decimal("1.5")


async def test_a_viewer_may_not_decide(api_client: httpx.AsyncClient, database: Database) -> None:
    tenant_id, user_id, profile_id, _ = await _seed(database, ["Cloud migration services"])
    async with database.session(tenant_id) as session:
        suggestion = KeywordSuggestion(
            tenant_id=tenant_id, profile_id=profile_id, term="cloud", kind="include", evidence={}
        )
        session.add(suggestion)
        await session.flush()
        suggestion_id = suggestion.id
    response = await api_client.put(
        f"/api/v1/profiles/{profile_id}/keyword-suggestions/{suggestion_id}",
        json={"status": "approved"},
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id, role=Role.VIEWER),
    )
    assert response.status_code == 403


async def test_unknown_status_filter_is_422(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tenant_id, user_id, profile_id, _ = await _seed(database, ["Cloud migration services"])
    response = await api_client.get(
        f"/api/v1/profiles/{profile_id}/keyword-suggestions?status=maybe",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
    )
    assert response.status_code == 422
