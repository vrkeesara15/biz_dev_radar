"""M4-08: saved-search and alert-rule routes, and evaluating a new match against them."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from app.core.config import Region
from app.core.db import Database
from app.core.opportunity import NoticeType
from app.core.roles import Role
from app.models import AlertRule, CompanyProfile, Opportunity, SavedSearch
from app.services.matching.alerts import (
    DEFAULT_CHANNELS,
    create_saved_search,
    evaluate_rules,
)
from sqlalchemy import select

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def _opportunity(**overrides: object) -> Opportunity:
    values: dict[str, object] = {
        "source_id": "sam_opps",
        "external_id": f"m408-{uuid.uuid4().hex[:10]}",
        "region": Region.US,
        "country": "US",
        "currency": "USD",
        "notice_type": NoticeType.RFP,
        "title": "Cloud migration services",
        "description_text": "Move mainframe workloads to a commercial cloud.",
        "naics": ["541511"],
        "buyer_org": "Department of the Treasury",
        "response_due_at": NOW + timedelta(days=10),
        "version": 1,
    }
    values.update(overrides)
    return Opportunity(**values)  # type: ignore[arg-type]


async def _seed(database: Database):  # type: ignore[no-untyped-def]
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(session)
        profile = CompanyProfile(
            tenant_id=tenant.id, region=Region.US, legal_name="Cloud Movers LLC", version=1
        )
        opp = _opportunity()
        session.add_all([profile, opp])
        await session.flush()
        return tenant.id, user.id, profile.id, opp.id


# --- routes ---------------------------------------------------------------------------------


async def test_saving_a_search_also_creates_its_alert_rule(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tenant_id, user_id, profile_id, _ = await _seed(database)
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, role=Role.BID_MANAGER)
    created = await api_client.post(
        "/api/v1/saved-searches",
        json={
            "name": "Cloud work in Virginia",
            "filters": {"q": "cloud migration", "region": "us", "naics": ["541511"]},
            "min_score": 65,
            "channels": ["email", "web_push"],
            "profile_id": str(profile_id),
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == "Cloud work in Virginia"
    assert body["filters"] == {"q": "cloud migration", "region": "us", "naics": ["541511"]}
    assert body["alert_rule_id"] is not None

    listed = await api_client.get("/api/v1/saved-searches", headers=headers)
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [body["id"]]
    assert listed.json()[0]["alert_rule_id"] == body["alert_rule_id"]

    rules = await api_client.get("/api/v1/alert-rules", headers=headers)
    assert rules.status_code == 200
    rule = rules.json()[0]
    assert rule["id"] == body["alert_rule_id"]
    assert rule["saved_search_id"] == body["id"]
    assert rule["profile_id"] == str(profile_id)
    assert rule["min_score"] == 65 and rule["mode"] == "instant" and rule["enabled"] is True
    # web_push is normalised onto the dispatcher's channel name
    assert rule["channels"] == ["email", "push"]


async def test_saved_search_min_score_falls_back_to_the_filter_then_the_spec_default(
    database: Database,
) -> None:
    tenant_id, user_id, _profile_id, _ = await _seed(database)
    async with database.session(tenant_id) as session:
        _, from_filter = await create_saved_search(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            name="From filters",
            filters={"min_score": 55},
        )
        _, default = await create_saved_search(
            session, tenant_id=tenant_id, user_id=user_id, name="Bare", filters={}
        )
        assert from_filter.min_score == 55
        assert default.min_score == 70  # SPEC 7 High threshold
        assert tuple(default.channels) == DEFAULT_CHANNELS


async def test_alert_rule_crud_and_validation(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tenant_id, user_id, profile_id, _ = await _seed(database)
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, role=Role.TENANT_OWNER)
    created = await api_client.post(
        "/api/v1/alert-rules",
        json={
            "name": "Everything high",
            "min_score": 80,
            "channels": ["in_app", "slack"],
            "mode": "digest",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    rule_id = created.json()["id"]
    assert created.json()["mode"] == "digest" and created.json()["min_score"] == 80
    assert created.json()["user_id"] == str(user_id)

    patched = await api_client.patch(
        f"/api/v1/alert-rules/{rule_id}",
        json={"enabled": False, "min_score": 90},
        headers=headers,
    )
    assert patched.status_code == 200
    assert patched.json()["enabled"] is False and patched.json()["min_score"] == 90

    # duplicate name
    again = await api_client.post(
        "/api/v1/alert-rules", json={"name": "Everything high"}, headers=headers
    )
    assert again.status_code == 409

    # unknown profile / saved search
    assert (
        await api_client.post(
            "/api/v1/alert-rules",
            json={"name": "Ghost profile", "profile_id": str(uuid.uuid4())},
            headers=headers,
        )
    ).status_code == 404
    assert (
        await api_client.post(
            "/api/v1/alert-rules",
            json={"name": "Ghost search", "saved_search_id": str(uuid.uuid4())},
            headers=headers,
        )
    ).status_code == 404
    # a body whose channels are all unusable
    assert (
        await api_client.post(
            "/api/v1/alert-rules",
            json={"name": "No channel", "channels": ["carrier-pigeon"]},
            headers=headers,
        )
    ).status_code == 422
    assert (
        await api_client.patch(
            f"/api/v1/alert-rules/{uuid.uuid4()}", json={"enabled": True}, headers=headers
        )
    ).status_code == 404
    assert profile_id is not None


async def test_a_viewer_may_save_a_search_but_not_write_a_bare_rule(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tenant_id, user_id, _, _ = await _seed(database)
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, role=Role.VIEWER)
    assert (
        await api_client.post(
            "/api/v1/saved-searches", json={"name": "Mine", "filters": {}}, headers=headers
        )
    ).status_code == 201
    assert (
        await api_client.post("/api/v1/alert-rules", json={"name": "Nope"}, headers=headers)
    ).status_code == 403


async def test_saved_searches_are_per_user_within_the_tenant(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tenant_id, user_id, _, _ = await _seed(database)
    async with database.owner_session() as session:
        from tests.factories import make_user

        other = make_user()
        session.add(other)
        await session.flush()
        other_id = other.id
    mine = auth_headers(user_id=user_id, tenant_id=tenant_id, role=Role.BID_MANAGER)
    theirs = auth_headers(user_id=other_id, tenant_id=tenant_id, role=Role.BID_MANAGER)
    assert (
        await api_client.post(
            "/api/v1/saved-searches", json={"name": "Mine", "filters": {}}, headers=mine
        )
    ).status_code == 201
    assert (await api_client.get("/api/v1/saved-searches", headers=theirs)).json() == []
    # the alert rule it created is tenant-wide, so both users see it
    assert len((await api_client.get("/api/v1/alert-rules", headers=theirs)).json()) == 1


# --- evaluation ------------------------------------------------------------------------------


async def test_a_new_match_is_evaluated_against_every_enabled_rule(
    database: Database,
) -> None:
    tenant_id, user_id, profile_id, opp_id = await _seed(database)
    async with database.owner_session() as session:
        second = CompanyProfile(
            tenant_id=tenant_id, region=Region.US, legal_name="Second profile", version=1
        )
        session.add(second)
        await session.flush()
        second_profile_id = second.id
    async with database.session(tenant_id) as session:
        await create_saved_search(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            name="Cloud only",
            filters={"q": "cloud migration", "naics": ["541511"]},
            min_score=60,
        )
        await create_saved_search(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            name="Janitorial only",
            filters={"q": "janitorial"},
            min_score=60,
        )
        # a bare rule with no saved search fires on score alone
        session.add(
            AlertRule(
                tenant_id=tenant_id,
                name="Anything high",
                min_score=70,
                channels=["slack"],
                mode="instant",
            )
        )
        # a rule pinned to another profile never fires here
        session.add(
            AlertRule(
                tenant_id=tenant_id,
                name="Other profile",
                profile_id=second_profile_id,
                min_score=0,
                channels=["email"],
            )
        )
        # a disabled rule never fires
        session.add(
            AlertRule(
                tenant_id=tenant_id,
                name="Switched off",
                min_score=0,
                channels=["email"],
                enabled=False,
            )
        )
        await session.flush()
        row = await session.get(Opportunity, opp_id)
        assert row is not None
        fired = await evaluate_rules(
            session, opportunity=row, profile_id=profile_id, score=Decimal("75"), now=NOW
        )
    names = {d.name for d in fired}
    assert names == {"Cloud only", "Anything high"}
    by_name = {d.name: d for d in fired}
    assert by_name["Anything high"].channels == ("slack",)
    assert by_name["Cloud only"].mode == "instant"
    assert by_name["Cloud only"].instant is True
    assert by_name["Cloud only"].as_dict()["min_score"] == 60


async def test_min_score_gates_a_rule(database: Database) -> None:
    tenant_id, user_id, profile_id, opp_id = await _seed(database)
    async with database.session(tenant_id) as session:
        await create_saved_search(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            name="High only",
            filters={},
            min_score=80,
        )
        await session.flush()
        row = await session.get(Opportunity, opp_id)
        assert row is not None
        assert (
            await evaluate_rules(session, opportunity=row, profile_id=profile_id, score=79, now=NOW)
            == []
        )
        fired = await evaluate_rules(
            session, opportunity=row, profile_id=profile_id, score=80, now=NOW
        )
    assert [d.name for d in fired] == ["High only"]


async def test_no_rules_means_no_decisions(database: Database) -> None:
    tenant_id, _user_id, profile_id, opp_id = await _seed(database)
    async with database.session(tenant_id) as session:
        row = await session.get(Opportunity, opp_id)
        assert row is not None
        assert (
            await evaluate_rules(session, opportunity=row, profile_id=profile_id, score=99, now=NOW)
            == []
        )


async def test_rules_never_cross_tenants(database: Database) -> None:
    tenant_a, user_a, profile_a, opp_id = await _seed(database)
    tenant_b, _user_b, profile_b, _ = await _seed(database)
    async with database.session(tenant_a) as session:
        await create_saved_search(
            session, tenant_id=tenant_a, user_id=user_a, name="A only", filters={}, min_score=0
        )
        await session.flush()
    async with database.session(tenant_b) as session:
        row = await session.get(Opportunity, opp_id)
        assert row is not None
        assert (
            await evaluate_rules(session, opportunity=row, profile_id=profile_b, score=99, now=NOW)
            == []
        )
        assert (await session.execute(select(SavedSearch))).scalars().all() == []
    async with database.session(tenant_a) as session:
        row = await session.get(Opportunity, opp_id)
        assert row is not None
        fired = await evaluate_rules(
            session, opportunity=row, profile_id=profile_a, score=99, now=NOW
        )
    assert [d.name for d in fired] == ["A only"]
