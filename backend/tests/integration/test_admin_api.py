"""M7-08 admin console: tenants, run history, usage/LLM cost, health, support access.

Acceptance (tasks.json M7-08):
  * GET /api/v1/admin/tenants, /admin/sources, /admin/sources/{id}/runs, /admin/usage
    are platform_admin only.
  * Support access to a tenant requires an explicit grant and writes support_access
    audit rows.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.core.admin import DEFAULT_SUPPORT_MINUTES, MAX_SUPPORT_MINUTES
from app.core.config import Region
from app.core.db import Database
from app.core.plan import LLM_COST_MICROUSD, LLM_TOKENS_IN, LLM_TOKENS_OUT, Plan
from app.core.roles import Role
from app.models import (
    AgentRun,
    AuditLog,
    BillingCustomer,
    CompanyProfile,
    Membership,
    Source,
    SourceRun,
    SupportAccessGrant,
    Tenant,
    UsageLedger,
)
from app.services.audit import SUPPORT_ACCESS_ACTION
from sqlalchemy import select

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner, make_user

PERIOD = "2026-04"
PERIOD_AT = datetime(2026, 4, 12, 10, 0, tzinfo=UTC)
OTHER_PERIOD = "2026-03"


class Ctx:
    """Two ordinary tenants plus the internal tenant that the platform admin belongs to."""

    def __init__(self) -> None:
        self.admin_tenant: uuid.UUID
        self.admin_user: uuid.UUID
        self.alpha: uuid.UUID
        self.beta: uuid.UUID
        self.alpha_owner: uuid.UUID

    @property
    def admin_headers(self) -> dict[str, str]:
        return auth_headers(
            user_id=self.admin_user, tenant_id=self.admin_tenant, role=Role.PLATFORM_ADMIN
        )

    @property
    def owner_headers(self) -> dict[str, str]:
        return auth_headers(user_id=self.alpha_owner, tenant_id=self.alpha, role=Role.TENANT_OWNER)


@pytest.fixture()
async def ctx(database: Database, clean_db: object) -> Ctx:
    out = Ctx()
    async with database.owner_session() as session:
        internal, admin, _ = await create_tenant_with_owner(
            session, slug="internal", name="BidRadar", is_internal=True, plan=Plan.ENTERPRISE
        )
        alpha, alpha_owner, _ = await create_tenant_with_owner(
            session, slug="alpha-corp", name="Alpha Corporation", plan=Plan.FREE
        )
        beta, _, _ = await create_tenant_with_owner(
            session,
            slug="beta-llc",
            name="Beta LLC",
            plan=Plan.PRO,
            region=Region.IN,
            data_residency=Region.IN,
        )
        # a second member and a profile for the counts
        extra = make_user()
        session.add(extra)
        await session.flush()
        session.add(Membership(tenant_id=alpha.id, user_id=extra.id, role=Role.WRITER))
        session.add(
            CompanyProfile(tenant_id=alpha.id, region=Region.US, legal_name="Alpha Corporation")
        )
        session.add(
            BillingCustomer(
                tenant_id=alpha.id,
                provider="stripe",
                customer_id="cus_alpha",
                subscription_id="sub_alpha",
                status="active",
                plan=Plan.FREE,
            )
        )
        # LLM usage for April (alpha, beta) and March (alpha only, must not leak in)
        session.add_all(
            [
                UsageLedger(
                    tenant_id=alpha.id, metric=LLM_TOKENS_IN, quantity=1_000, period=PERIOD
                ),
                UsageLedger(tenant_id=alpha.id, metric=LLM_TOKENS_IN, quantity=500, period=PERIOD),
                UsageLedger(tenant_id=alpha.id, metric=LLM_TOKENS_OUT, quantity=250, period=PERIOD),
                UsageLedger(
                    tenant_id=alpha.id,
                    metric=LLM_COST_MICROUSD,
                    quantity=2_500_000,
                    period=PERIOD,
                ),
                UsageLedger(tenant_id=beta.id, metric=LLM_TOKENS_IN, quantity=10, period=PERIOD),
                UsageLedger(
                    tenant_id=beta.id, metric=LLM_COST_MICROUSD, quantity=1_000, period=PERIOD
                ),
                UsageLedger(
                    tenant_id=alpha.id,
                    metric=LLM_COST_MICROUSD,
                    quantity=999_000_000,
                    period=OTHER_PERIOD,
                ),
            ]
        )
        session.add_all(
            [
                AgentRun(
                    tenant_id=alpha.id, kind="summary_ai", status="done", created_at=PERIOD_AT
                ),
                AgentRun(tenant_id=alpha.id, kind="draft", status="done", created_at=PERIOD_AT),
                AgentRun(
                    tenant_id=alpha.id,
                    kind="draft",
                    status="done",
                    created_at=datetime(2026, 3, 1, tzinfo=UTC),
                ),
            ]
        )
        await session.flush()
        out.admin_tenant, out.admin_user = internal.id, admin.id
        out.alpha, out.alpha_owner, out.beta = alpha.id, alpha_owner.id, beta.id
    return out


async def _seed_source_runs(database: Database, count: int) -> None:
    async with database.owner_session() as session:
        session.add(
            Source(source_id="probe_source", region=Region.US, schedule="0 6 * * *", enabled=True)
        )
        await session.flush()
        for i in range(count):
            session.add(
                SourceRun(
                    source_id="probe_source",
                    started_at=datetime(2026, 4, 1, tzinfo=UTC) + timedelta(hours=i),
                    finished_at=datetime(2026, 4, 1, tzinfo=UTC) + timedelta(hours=i, minutes=3),
                    status="failing" if i % 2 else "ok",
                    fetched=i * 10,
                    upserted=i,
                    errors=[{"message": f"boom {i}"}] if i % 2 else [],
                )
            )


# --- RBAC -----------------------------------------------------------------------------

ADMIN_GETS = [
    "/api/v1/admin/tenants",
    "/api/v1/admin/usage",
    "/api/v1/admin/health",
    "/api/v1/admin/sources/sam_opps/runs",
]


@pytest.mark.parametrize("path", ADMIN_GETS)
@pytest.mark.parametrize(
    "role", [Role.TENANT_OWNER, Role.BID_MANAGER, Role.WRITER, Role.REVIEWER, Role.VIEWER]
)
async def test_admin_routes_are_platform_admin_only(
    api_client: httpx.AsyncClient, ctx: Ctx, path: str, role: Role
) -> None:
    headers = auth_headers(user_id=ctx.alpha_owner, tenant_id=ctx.alpha, role=role)
    assert (await api_client.get(path, headers=headers)).status_code == 403
    assert (await api_client.get(path)).status_code == 401


async def test_tenant_detail_and_patch_are_platform_admin_only(
    api_client: httpx.AsyncClient, ctx: Ctx
) -> None:
    path = f"/api/v1/admin/tenants/{ctx.alpha}"
    assert (await api_client.get(path, headers=ctx.owner_headers)).status_code == 403
    r = await api_client.patch(path, json={"plan": "pro"}, headers=ctx.owner_headers)
    assert r.status_code == 403
    assert (await api_client.get(f"{path}/audit-log", headers=ctx.owner_headers)).status_code == 403


# --- GET /admin/tenants ----------------------------------------------------------------


async def test_list_tenants_has_counts_plan_and_pagination(
    api_client: httpx.AsyncClient, ctx: Ctx
) -> None:
    r = await api_client.get("/api/v1/admin/tenants", headers=ctx.admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 3 and body["page"] == 1 and body["pages"] == 1
    by_slug = {t["slug"]: t for t in body["items"]}
    assert [t["slug"] for t in body["items"]] == sorted(by_slug)
    alpha = by_slug["alpha-corp"]
    assert alpha["plan"] == "free" and alpha["region"] == "us" and alpha["is_internal"] is False
    assert alpha["member_count"] == 2 and alpha["profile_count"] == 1
    assert alpha["created_at"] and alpha["deleted_at"] is None
    assert by_slug["internal"]["is_internal"] is True
    assert by_slug["beta-llc"]["region"] == "in" and by_slug["beta-llc"]["member_count"] == 1

    # q filters on name or slug, case-insensitively
    r = await api_client.get(
        "/api/v1/admin/tenants", params={"q": "ALPHA"}, headers=ctx.admin_headers
    )
    assert [t["slug"] for t in r.json()["items"]] == ["alpha-corp"]
    r = await api_client.get(
        "/api/v1/admin/tenants", params={"q": "llc"}, headers=ctx.admin_headers
    )
    assert [t["slug"] for t in r.json()["items"]] == ["beta-llc"]
    r = await api_client.get(
        "/api/v1/admin/tenants", params={"q": "nothing"}, headers=ctx.admin_headers
    )
    assert r.json()["items"] == [] and r.json()["total"] == 0

    # pagination
    r = await api_client.get(
        "/api/v1/admin/tenants", params={"page": 2, "page_size": 2}, headers=ctx.admin_headers
    )
    body = r.json()
    assert body["total"] == 3 and body["pages"] == 2 and len(body["items"]) == 1


async def test_deleted_tenants_are_hidden_unless_asked_for(
    api_client: httpx.AsyncClient, database: Database, ctx: Ctx
) -> None:
    async with database.owner_session() as session:
        tenant = await session.get(Tenant, ctx.beta)
        assert tenant is not None
        tenant.deleted_at = datetime.now(UTC)
    r = await api_client.get("/api/v1/admin/tenants", headers=ctx.admin_headers)
    assert "beta-llc" not in {t["slug"] for t in r.json()["items"]}
    r = await api_client.get(
        "/api/v1/admin/tenants", params={"include_deleted": True}, headers=ctx.admin_headers
    )
    beta = next(t for t in r.json()["items"] if t["slug"] == "beta-llc")
    assert beta["deleted_at"] is not None


# --- GET/PATCH /admin/tenants/{id} ------------------------------------------------------


async def test_tenant_detail_carries_limits_usage_and_billing(
    api_client: httpx.AsyncClient, ctx: Ctx
) -> None:
    r = await api_client.get(
        f"/api/v1/admin/tenants/{ctx.alpha}",
        params={"period": PERIOD},
        headers=ctx.admin_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tenant"]["slug"] == "alpha-corp" and body["period"] == PERIOD
    assert body["plan_limits"]["profiles"] == 1  # free plan
    assert body["usage"]["tokens_in"] == 1_500 and body["usage"]["tokens_out"] == 250
    assert body["usage"]["cost_microusd"] == 2_500_000
    assert body["usage"]["cost_usd"] == pytest.approx(2.5)
    assert body["usage"]["agent_runs"] == 2
    assert body["billing"] == {
        "provider": "stripe",
        "status": "active",
        "plan": "free",
        "current_period_end": None,
        "has_subscription": True,
    }
    assert body["support_access"] is None
    # a tenant without a billing customer simply has none
    r = await api_client.get(f"/api/v1/admin/tenants/{ctx.beta}", headers=ctx.admin_headers)
    assert r.json()["billing"] is None
    # unknown tenant
    r = await api_client.get(f"/api/v1/admin/tenants/{uuid.uuid4()}", headers=ctx.admin_headers)
    assert r.status_code == 404


async def test_patch_tenant_changes_plan_and_writes_audit_rows(
    api_client: httpx.AsyncClient, database: Database, ctx: Ctx
) -> None:
    r = await api_client.patch(
        f"/api/v1/admin/tenants/{ctx.alpha}",
        json={"plan": "enterprise", "is_internal": True},
        headers=ctx.admin_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["plan"] == "enterprise" and r.json()["is_internal"] is True
    async with database.owner_session() as session:
        rows = (
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "admin.tenant.update")
                )
            )
            .scalars()
            .all()
        )
    # one in the tenant's own trail, one in the admin's (audit middleware)
    assert {row.tenant_id for row in rows} == {ctx.alpha, ctx.admin_tenant}
    tenant_row = next(row for row in rows if row.tenant_id == ctx.alpha)
    assert tenant_row.user_id == ctx.admin_user
    assert tenant_row.meta["changes"]["plan"] == {"from": "free", "to": "enterprise"}

    # a no-op PATCH changes nothing and writes no row into the tenant's trail
    r = await api_client.patch(
        f"/api/v1/admin/tenants/{ctx.alpha}", json={}, headers=ctx.admin_headers
    )
    assert r.status_code == 200 and r.json()["plan"] == "enterprise"
    async with database.owner_session() as session:
        rows = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "admin.tenant.update", AuditLog.tenant_id == ctx.alpha
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1

    r = await api_client.patch(
        f"/api/v1/admin/tenants/{uuid.uuid4()}", json={"plan": "pro"}, headers=ctx.admin_headers
    )
    assert r.status_code == 404


# --- support access --------------------------------------------------------------------


async def test_support_access_creates_a_time_boxed_grant_and_audit_rows(
    api_client: httpx.AsyncClient, database: Database, ctx: Ctx
) -> None:
    before = datetime.now(UTC)
    r = await api_client.post(
        f"/api/v1/admin/tenants/{ctx.alpha}/support-access",
        json={"reason": "ticket #918 cannot export", "minutes": 15},
        headers=ctx.admin_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tenant"]["id"] == str(ctx.alpha) and body["member_count"] == 2
    grant = body["grant"]
    assert grant["reason"] == "ticket #918 cannot export"
    expires = datetime.fromisoformat(grant["expires_at"])
    assert timedelta(minutes=14) < expires - before < timedelta(minutes=16)

    async with database.owner_session() as session:
        grants = (await session.execute(select(SupportAccessGrant))).scalars().all()
        audits = (
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == SUPPORT_ACCESS_ACTION)
                )
            )
            .scalars()
            .all()
        )
    assert len(grants) == 1
    assert grants[0].target_tenant_id == ctx.alpha and grants[0].admin_user_id == ctx.admin_user
    assert grants[0].revoked_at is None
    # the service row lands in the tenant's trail, the middleware row in the admin's
    assert {a.tenant_id for a in audits} == {ctx.alpha, ctx.admin_tenant}
    tenant_audit = next(a for a in audits if a.tenant_id == ctx.alpha)
    assert tenant_audit.meta["reason"] == "ticket #918 cannot export"


async def test_support_access_defaults_and_validation(
    api_client: httpx.AsyncClient, database: Database, ctx: Ctx
) -> None:
    r = await api_client.post(
        f"/api/v1/admin/tenants/{ctx.alpha}/support-access",
        json={"reason": "default window"},
        headers=ctx.admin_headers,
    )
    assert r.status_code == 200
    granted = datetime.fromisoformat(r.json()["grant"]["granted_at"])
    expires = datetime.fromisoformat(r.json()["grant"]["expires_at"])
    assert expires - granted == timedelta(minutes=DEFAULT_SUPPORT_MINUTES)

    # a reason is mandatory and the window is capped
    for payload in ({}, {"reason": "x"}, {"reason": "fine", "minutes": MAX_SUPPORT_MINUTES + 1}):
        r = await api_client.post(
            f"/api/v1/admin/tenants/{ctx.alpha}/support-access",
            json=payload,
            headers=ctx.admin_headers,
        )
        assert r.status_code == 422, payload
    async with database.owner_session() as session:
        assert len((await session.execute(select(SupportAccessGrant))).scalars().all()) == 1


async def test_tenant_audit_log_needs_an_active_grant(
    api_client: httpx.AsyncClient, database: Database, ctx: Ctx
) -> None:
    async with database.owner_session() as session:
        session.add(AuditLog(tenant_id=ctx.alpha, user_id=ctx.alpha_owner, action="draft.read"))

    path = f"/api/v1/admin/tenants/{ctx.alpha}/audit-log"
    r = await api_client.get(path, headers=ctx.admin_headers)
    assert r.status_code == 403
    assert "support access" in r.json()["detail"]

    await api_client.post(
        f"/api/v1/admin/tenants/{ctx.alpha}/support-access",
        json={"reason": "reading the trail for ticket #918"},
        headers=ctx.admin_headers,
    )
    r = await api_client.get(path, headers=ctx.admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] >= 1
    assert "draft.read" in {row["action"] for row in body["items"]}
    assert body["grant"]["tenant_id"] == str(ctx.alpha)

    # another admin's grant does not help, and an expired one stops working
    other_admin = auth_headers(
        user_id=uuid.uuid4(), tenant_id=ctx.admin_tenant, role=Role.PLATFORM_ADMIN
    )
    assert (await api_client.get(path, headers=other_admin)).status_code == 403
    async with database.owner_session() as session:
        grant = (await session.execute(select(SupportAccessGrant))).scalars().one()
        grant.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    assert (await api_client.get(path, headers=ctx.admin_headers)).status_code == 403


async def test_revoked_grant_stops_working(
    api_client: httpx.AsyncClient, database: Database, ctx: Ctx
) -> None:
    await api_client.post(
        f"/api/v1/admin/tenants/{ctx.alpha}/support-access",
        json={"reason": "revoke me"},
        headers=ctx.admin_headers,
    )
    path = f"/api/v1/admin/tenants/{ctx.alpha}/audit-log"
    assert (await api_client.get(path, headers=ctx.admin_headers)).status_code == 200
    async with database.owner_session() as session:
        grant = (await session.execute(select(SupportAccessGrant))).scalars().one()
        grant.revoked_at = datetime.now(UTC)
    assert (await api_client.get(path, headers=ctx.admin_headers)).status_code == 403


# --- GET /admin/usage -------------------------------------------------------------------


async def test_usage_reports_tokens_cost_and_runs_per_tenant(
    api_client: httpx.AsyncClient, ctx: Ctx
) -> None:
    r = await api_client.get(
        "/api/v1/admin/usage", params={"period": PERIOD}, headers=ctx.admin_headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["period"] == PERIOD
    rows = {row["slug"]: row for row in body["items"]}
    assert set(rows) == {"internal", "alpha-corp", "beta-llc"}
    assert rows["alpha-corp"]["tokens_in"] == 1_500
    assert rows["alpha-corp"]["tokens_out"] == 250
    assert rows["alpha-corp"]["cost_microusd"] == 2_500_000
    assert rows["alpha-corp"]["cost_usd"] == pytest.approx(2.5)
    assert rows["alpha-corp"]["agent_runs"] == 2
    assert rows["beta-llc"]["cost_microusd"] == 1_000
    assert rows["internal"]["cost_microusd"] == 0 and rows["internal"]["agent_runs"] == 0
    # most expensive first, so the chart reads top-down
    assert [row["slug"] for row in body["items"]] == ["alpha-corp", "beta-llc", "internal"]
    assert body["total_cost_microusd"] == 2_501_000
    assert body["total_tokens_in"] == 1_510 and body["total_tokens_out"] == 250
    assert body["total_agent_runs"] == 2
    # notifications are not counted yet (the table arrives with M4)
    assert rows["alpha-corp"]["notifications"] is None


async def test_usage_period_switches_and_validates(api_client: httpx.AsyncClient, ctx: Ctx) -> None:
    r = await api_client.get(
        "/api/v1/admin/usage", params={"period": OTHER_PERIOD}, headers=ctx.admin_headers
    )
    rows = {row["slug"]: row for row in r.json()["items"]}
    assert rows["alpha-corp"]["cost_microusd"] == 999_000_000
    assert rows["alpha-corp"]["agent_runs"] == 1
    assert rows["alpha-corp"]["tokens_in"] == 0

    # no period means the current month
    r = await api_client.get("/api/v1/admin/usage", headers=ctx.admin_headers)
    assert r.json()["period"] == datetime.now(UTC).strftime("%Y-%m")
    assert r.json()["total_cost_microusd"] == 0

    r = await api_client.get(
        "/api/v1/admin/usage", params={"period": "2026-13"}, headers=ctx.admin_headers
    )
    assert r.status_code == 422


# --- GET /admin/sources/{id}/runs --------------------------------------------------------


async def test_source_run_history_is_paginated_newest_first(
    api_client: httpx.AsyncClient, database: Database, ctx: Ctx
) -> None:
    await _seed_source_runs(database, 7)
    r = await api_client.get(
        "/api/v1/admin/sources/probe_source/runs",
        params={"page_size": 3},
        headers=ctx.admin_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source_id"] == "probe_source"
    assert body["total"] == 7 and body["pages"] == 3 and len(body["items"]) == 3
    started = [row["started_at"] for row in body["items"]]
    assert started == sorted(started, reverse=True)
    first = body["items"][0]
    assert first["fetched"] == 60 and first["upserted"] == 6 and first["status"] == "ok"
    failing = next(row for row in body["items"] if row["status"] == "failing")
    assert failing["error_count"] == 1 and failing["last_error"].startswith("boom")

    r = await api_client.get(
        "/api/v1/admin/sources/probe_source/runs",
        params={"page": 3, "page_size": 3},
        headers=ctx.admin_headers,
    )
    assert len(r.json()["items"]) == 1

    r = await api_client.get("/api/v1/admin/sources/no_such_source/runs", headers=ctx.admin_headers)
    assert r.status_code == 404


# --- GET /admin/health --------------------------------------------------------------------


async def test_health_reports_db_broker_storage_and_adapters(
    api_client: httpx.AsyncClient, database: Database, ctx: Ctx
) -> None:
    async with database.owner_session() as session:
        session.add(
            Source(
                source_id="broken_source",
                region=Region.US,
                schedule="0 6 * * *",
                health_status="failing",
                health_message="layout changed",
                consecutive_failures=4,
            )
        )
    r = await api_client.get("/api/v1/admin/health", headers=ctx.admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    checks = {c["name"]: c for c in body["checks"]}
    assert set(checks) == {"database", "broker", "storage", "adapters"}
    assert checks["database"]["status"] == "ok"
    assert checks["broker"]["status"] in {"ok", "failing"}
    assert checks["storage"]["meta"]["backend"] in {"local", "s3", "gcs"}
    assert checks["adapters"]["status"] == "failing"
    assert "broken_source" in (checks["adapters"]["detail"] or "")
    assert body["status"] in {"failing", "degraded", "unconfigured"}
    broken = next(a for a in body["adapters"] if a["source_id"] == "broken_source")
    assert broken["consecutive_failures"] == 4 and broken["health_message"] == "layout changed"
