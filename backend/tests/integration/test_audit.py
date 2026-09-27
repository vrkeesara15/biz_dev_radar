"""M0-08: audit middleware, audit() helper, append-only audit rows."""

import uuid

import httpx
import pytest
from app.core.db import Database
from app.core.roles import Role
from app.models import AuditLog, UsageLedger
from app.services.audit import SUPPORT_ACCESS_ACTION, audit
from sqlalchemy import select, text
from sqlalchemy.exc import ProgrammingError

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner
from tests.isolation.factories import is_public


async def _tenant(database: Database, **overrides):  # type: ignore[no-untyped-def]
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(session, **overrides)
        return tenant.id, user.id


async def _audit_rows(database: Database, action: str | None = None) -> list[AuditLog]:
    async with database.owner_session() as session:
        stmt = select(AuditLog).order_by(AuditLog.at)
        if action:
            stmt = stmt.where(AuditLog.action == action)
        return list((await session.execute(stmt)).scalars().all())


async def test_mutating_request_writes_audit_row(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tid, uid = await _tenant(database)
    headers = auth_headers(user_id=uid, tenant_id=tid, role=Role.WRITER)
    r = await api_client.patch(
        "/api/v1/me", json={"name": "Ada", "tz": "Asia/Kolkata", "locale": "hi-IN"}, headers=headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["tz"] == "Asia/Kolkata"
    rows = await _audit_rows(database)
    assert len(rows) == 1
    row = rows[0]
    assert row.action == "me.update"
    assert row.tenant_id == tid and row.user_id == uid
    assert row.object_type == "user" and row.object_id == str(uid)
    assert row.ip == "203.0.113.10"
    assert row.request_id == r.headers["X-Request-ID"]
    assert row.meta["status"] == 200 and row.meta["method"] == "PATCH"
    assert row.meta["fields"] == ["locale", "name", "tz"]


async def test_reads_are_not_audited_by_middleware(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tid, uid = await _tenant(database)
    r = await api_client.get("/api/v1/me", headers=auth_headers(user_id=uid, tenant_id=tid))
    assert r.status_code == 200
    assert await _audit_rows(database) == []


async def test_unauthenticated_mutation_is_not_audited(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    r = await api_client.patch("/api/v1/me", json={"name": "x"})
    assert r.status_code == 401
    assert await _audit_rows(database) == []


async def test_failed_mutation_by_authenticated_user_is_audited(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tid, uid = await _tenant(database)
    r = await api_client.patch(
        "/api/v1/me", json={"tz": "Mars/Olympus"}, headers=auth_headers(user_id=uid, tenant_id=tid)
    )
    assert r.status_code == 422
    rows = await _audit_rows(database)
    assert len(rows) == 1
    assert rows[0].action == "patch /api/v1/me" and rows[0].meta["status"] == 422


async def test_every_mutating_route_under_api_v1_is_audited(
    app,
    api_client: httpx.AsyncClient,
    database: Database,  # type: ignore[no-untyped-def]
) -> None:
    """Drive every POST/PUT/PATCH/DELETE route with an empty body as an authenticated
    user; each must leave exactly one audit row whatever its status code.

    Provider webhooks (PUBLIC_ROUTES) are the one exception: they carry no user, so the
    middleware has no tenant to write into. Their effect is audited inside the service
    instead (action billing.webhook, see test_billing_api.py). The exempt set is asserted
    below so a new route cannot slip out of the audit trail unnoticed."""
    tid, uid = await _tenant(database, is_internal=True)
    headers = auth_headers(user_id=uid, tenant_id=tid, role=Role.PLATFORM_ADMIN)
    spec = app.openapi()
    all_mutating = [
        (method.upper(), path)
        for path, ops in spec["paths"].items()
        for method in ops
        if method.upper() in {"POST", "PUT", "PATCH", "DELETE"} and path.startswith("/api/v1/")
    ]
    mutating = [route for route in all_mutating if not is_public(*route)]
    exempt = sorted(route for route in all_mutating if is_public(*route))
    assert exempt == [
        ("POST", "/api/v1/webhooks/razorpay"),
        ("POST", "/api/v1/webhooks/stripe"),
    ], f"unexpected route exempted from the audit middleware: {exempt}"
    assert mutating, "expected mutating routes"
    for method, path in mutating:
        concrete = path.replace("{tenant_id}", str(tid)).replace("{id}", str(uuid.uuid4()))
        before = len(await _audit_rows(database))
        r = await api_client.request(method, concrete, json={}, headers=headers)
        assert r.status_code != 401, (method, path)
        after = await _audit_rows(database)
        assert len(after) == before + 1, f"{method} {path} -> {r.status_code} left no audit row"
        assert after[-1].meta["status"] == r.status_code


async def test_support_access_route(api_client: httpx.AsyncClient, database: Database) -> None:
    internal, admin = await _tenant(database, is_internal=True)
    target, _ = await _tenant(database)
    admin_headers = auth_headers(user_id=admin, tenant_id=internal, role=Role.PLATFORM_ADMIN)
    r = await api_client.post(
        f"/api/v1/admin/tenants/{target}/support-access",
        json={"reason": "ticket #7"},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["tenant"]["id"] == str(target) and r.json()["member_count"] == 1
    rows = await _audit_rows(database, SUPPORT_ACCESS_ACTION)
    # one from the service (in the target tenant) + one from the middleware (admin tenant)
    assert {row.tenant_id for row in rows} == {target, internal}
    assert all(row.user_id == admin and row.object_id == str(target) for row in rows)
    # non-admins are refused and no support_access row is written for them
    owner_headers = auth_headers(user_id=uuid.uuid4(), tenant_id=target, role=Role.TENANT_OWNER)
    r = await api_client.post(
        f"/api/v1/admin/tenants/{internal}/support-access",
        json={"reason": "nope"},
        headers=owner_headers,
    )
    assert r.status_code == 403
    assert len(await _audit_rows(database, SUPPORT_ACCESS_ACTION)) == 2
    r = await api_client.post(
        f"/api/v1/admin/tenants/{uuid.uuid4()}/support-access",
        json={"reason": "ghost tenant"},
        headers=admin_headers,
    )
    assert r.status_code == 404


async def test_audit_helper_from_services(database: Database) -> None:
    tid, uid = await _tenant(database)
    async with database.session(tid) as session:
        ledger = UsageLedger(tenant_id=tid, metric="profiles", quantity=1, period="lifetime")
        session.add(ledger)
        await session.flush()
        row = await audit(session, "draft.read", ledger, user_id=uid, meta={"section": "x"})
        assert row.object_type == "usage_ledger" and row.object_id == str(ledger.id)
        row2 = await audit(session, "export.read", ("export", "exp-1"))
        assert row2.tenant_id == tid and row2.user_id is None
    rows = await _audit_rows(database)
    assert [r.action for r in rows] == ["draft.read", "export.read"]
    async with database.owner_session() as session:  # no tenant bound, no tenant on obj
        with pytest.raises(ValueError):
            await audit(session, "x", ("thing", "1"))


async def test_audit_rows_are_append_only_for_app_role(database: Database) -> None:
    tid, uid = await _tenant(database)
    async with database.owner_session() as session:
        session.add(AuditLog(tenant_id=tid, user_id=uid, action="seed"))
    async with database.session(tid) as session:
        assert (await session.execute(select(AuditLog))).scalars().one().action == "seed"
        with pytest.raises(ProgrammingError, match="permission denied"):
            await session.execute(text("UPDATE audit_log SET action = 'tampered'"))
        await session.rollback()
    async with database.session(tid) as session:
        with pytest.raises(ProgrammingError, match="permission denied"):
            await session.execute(text("DELETE FROM audit_log"))
        await session.rollback()
    async with database.session(tid) as session:
        with pytest.raises(ProgrammingError, match="permission denied"):
            await session.execute(text("UPDATE plan_limits SET limit_value = 999"))
        await session.rollback()
    async with database.owner_session() as session:
        grants = set(
            (
                await session.execute(
                    text(
                        "SELECT privilege_type FROM information_schema.role_table_grants "
                        "WHERE table_name = 'audit_log' AND grantee = 'bidradar_app'"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert grants == {"SELECT", "INSERT"}
