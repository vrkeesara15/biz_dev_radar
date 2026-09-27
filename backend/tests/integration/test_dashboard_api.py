"""M6-08: GET /api/v1/dashboard over seeded pursuits — SQL aggregates under RLS."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
from app.core.config import Region, Settings
from app.core.db import Database
from app.core.opportunity import NoticeType
from app.core.roles import Role
from app.models import CompanyProfile, Opportunity, Pursuit
from app.services.dashboard import FEEDBACK_TABLE, dashboard, feedback_precision
from sqlalchemy import text

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
SETTINGS = Settings(_env_file=None)  # type: ignore[call-arg]


async def _tenant(database: Database) -> dict[str, Any]:
    async with database.owner_session() as session:
        tenant, owner, _ = await create_tenant_with_owner(session)
        profile = CompanyProfile(tenant_id=tenant.id, region=Region.US, legal_name="KPI LLC")
        session.add(profile)
        await session.flush()
        return {"tenant_id": tenant.id, "owner_id": owner.id, "profile_id": profile.id}


async def _pursuit(
    database: Database,
    ctx: dict[str, Any],
    *,
    stage: str,
    value_usd: Decimal | None = None,
    due: datetime | None = None,
    title: str = "A notice",
) -> uuid.UUID:
    async with database.owner_session(ctx["tenant_id"]) as session:
        opportunity = Opportunity(
            source_id="sam_opps",
            external_id=f"kpi-{uuid.uuid4().hex[:8]}",
            region=Region.US,
            country="US",
            currency="USD",
            notice_type=NoticeType.RFP,
            title=title,
            source_tz="America/New_York",
            response_due_at=due,
            estimated_value_max_usd=value_usd,
        )
        session.add(opportunity)
        await session.flush()
        pursuit = Pursuit(
            tenant_id=ctx["tenant_id"],
            profile_id=ctx["profile_id"],
            opportunity_id=opportunity.id,
            owner_user_id=ctx["owner_id"],
            stage=stage,
            decision="bid",
        )
        session.add(pursuit)
        await session.flush()
        return pursuit.id


def _headers(ctx: dict[str, Any], role: Role = Role.TENANT_OWNER) -> dict[str, str]:
    return auth_headers(user_id=ctx["owner_id"], tenant_id=ctx["tenant_id"], role=role)


async def _seed(database: Database, ctx: dict[str, Any]) -> None:
    await _pursuit(
        database,
        ctx,
        stage="qualifying",
        value_usd=Decimal("120000.00"),
        due=NOW + timedelta(days=3),
        title="Closing this week",
    )
    await _pursuit(
        database,
        ctx,
        stage="drafting",
        value_usd=Decimal("500000.00"),
        due=NOW + timedelta(days=30),
        title="Closing next month",
    )
    await _pursuit(database, ctx, stage="drafting", value_usd=None, due=NOW + timedelta(days=6))
    await _pursuit(database, ctx, stage="submitted", value_usd=Decimal("900000.00"))
    await _pursuit(database, ctx, stage="awarded", value_usd=Decimal("300000.00"))
    await _pursuit(database, ctx, stage="awarded", value_usd=Decimal("100000.00"))
    await _pursuit(database, ctx, stage="lost", value_usd=Decimal("200000.00"))
    await _pursuit(database, ctx, stage="cancelled", value_usd=Decimal("50000.00"))


async def test_the_kpis_aggregate_the_seeded_pipeline(
    database: Database, clean_db: Database
) -> None:
    ctx = await _tenant(database)
    await _seed(database, ctx)
    async with database.session(ctx["tenant_id"]) as session:
        kpis = await dashboard(session, SETTINGS, now=NOW)

    assert kpis.open_by_stage == {
        "identified": 0,
        "qualifying": 1,
        "bid_decision": 0,
        "drafting": 2,
        "in_review": 0,
        "final_approval": 0,
    }
    # only the open stages count towards the pipeline; submitted / awarded / lost do not
    assert kpis.pipeline_value_by_stage["qualifying"]["USD"] == Decimal("120000.00")
    assert kpis.pipeline_value_by_stage["drafting"]["USD"] == Decimal("500000.00")
    assert kpis.pipeline_value_total["USD"] == Decimal("620000.00")
    # the INR column is the same figure at the configured rate, so the two always agree
    rate = Decimal(str(SETTINGS.fx_rates["INR"]))
    assert kpis.pipeline_value_total["INR"] == (Decimal("620000.00") / rate).quantize(
        Decimal("0.01")
    )

    # SPEC 9: awarded / (awarded + lost)
    assert (kpis.awarded, kpis.lost) == (2, 1)
    assert kpis.win_rate == 0.6667
    # "packages submitted" includes the ones that went on to be won or lost
    assert kpis.submitted == 4
    assert kpis.hours_saved_per_package == 20
    assert kpis.hours_saved_total == 80
    assert "estimate" in kpis.hours_saved_basis

    assert [item.title for item in kpis.due_next_7_days] == [
        "Closing this week",
        "A notice",
    ]
    assert kpis.due_next_7_days[0].stage == "qualifying"
    assert kpis.due_next_7_days[0].owner_user_id == str(ctx["owner_id"])


async def test_an_empty_tenant_reports_no_rates_rather_than_zeros(
    database: Database, clean_db: Database
) -> None:
    ctx = await _tenant(database)
    async with database.session(ctx["tenant_id"]) as session:
        kpis = await dashboard(session, SETTINGS, now=NOW)
    assert set(kpis.open_by_stage.values()) == {0}
    assert kpis.due_next_7_days == []
    assert kpis.pipeline_value_by_stage == {}
    assert kpis.pipeline_value_total == {"USD": Decimal("0.00"), "INR": Decimal("0.00")}
    assert kpis.win_rate is None
    assert kpis.hours_saved_total == 0
    assert kpis.alert_precision is None


async def test_alert_precision_is_null_until_match_feedback_exists(
    database: Database, clean_db: Database
) -> None:
    ctx = await _tenant(database)
    async with database.session(ctx["tenant_id"]) as session:
        assert await feedback_precision(session) == (None, 0)

    # stand the table up the way the matching branch will, and the KPI lights up
    async with database.owner_session(ctx["tenant_id"]) as session:
        await session.execute(
            text(f"CREATE TABLE {FEEDBACK_TABLE} (id serial primary key, verdict text)")
        )
        await session.execute(
            text(
                f"INSERT INTO {FEEDBACK_TABLE} (verdict) VALUES "
                "('useful'), ('useful'), ('useful'), ('not_relevant')"
            )
        )
    try:
        async with database.owner_session(ctx["tenant_id"]) as session:
            precision, rated = await feedback_precision(session)
        assert (precision, rated) == (0.75, 4)
    finally:
        async with database.owner_session(ctx["tenant_id"]) as session:
            await session.execute(text(f"DROP TABLE {FEEDBACK_TABLE}"))


async def test_an_unexpected_feedback_shape_answers_null(
    database: Database, clean_db: Database
) -> None:
    ctx = await _tenant(database)
    async with database.owner_session(ctx["tenant_id"]) as session:
        await session.execute(
            text(f"CREATE TABLE {FEEDBACK_TABLE} (id serial primary key, rating int)")
        )
    try:
        async with database.owner_session(ctx["tenant_id"]) as session:
            assert await feedback_precision(session) == (None, 0)
    finally:
        async with database.owner_session(ctx["tenant_id"]) as session:
            await session.execute(text(f"DROP TABLE {FEEDBACK_TABLE}"))


async def test_the_route_renders_both_time_zones_and_reads_for_every_role(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _tenant(database)
    await _pursuit(
        database,
        ctx,
        stage="drafting",
        value_usd=Decimal("250000.00"),
        due=datetime.now(UTC) + timedelta(days=2),
        title="Due very soon",
    )
    response = await api_client.get("/api/v1/dashboard", headers=_headers(ctx))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["open_by_stage"]["drafting"] == 1
    assert body["pipeline_value_by_stage"]["drafting"]["USD"] == "250000.00"
    assert body["pipeline_value_by_stage"]["drafting"]["INR"] != "0.00"
    assert body["win_rate"] is None
    assert body["avg_hours_saved_per_package"] == 20
    assert "estimate" in body["hours_saved_basis"]
    assert body["alert_precision"] is None
    item = body["due_next_7_days"][0]
    assert item["title"] == "Due very soon"
    assert item["due_at"]["buyer_tz"] == "America/New_York"
    assert "EDT" in item["due_at"]["display"] or "EST" in item["due_at"]["display"]
    assert item["countdown"]

    for role in (Role.BID_MANAGER, Role.WRITER, Role.REVIEWER, Role.VIEWER):
        assert (
            await api_client.get("/api/v1/dashboard", headers=_headers(ctx, role))
        ).status_code == 200


async def test_the_dashboard_is_tenant_scoped(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    a = await _tenant(database)
    b = await _tenant(database)
    await _seed(database, a)
    body = (await api_client.get("/api/v1/dashboard", headers=_headers(b))).json()
    assert set(body["open_by_stage"].values()) == {0}
    assert body["due_next_7_days"] == []
    assert body["pipeline_value_total"]["USD"] == "0.00"
    assert body["submitted"] == 0
