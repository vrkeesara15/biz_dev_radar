"""M0-07: bearer JWT auth, RBAC, tenant from token, auth rate limit."""

import uuid

import httpx
from app.api.deps import ADMIN_ACCESS_ACTION
from app.core.db import Database
from app.core.roles import Role
from app.models import AuditLog, Membership, User
from sqlalchemy import func, select

from tests.auth import auth_headers, token_for
from tests.factories import create_tenant_with_owner


async def _tenant(database: Database, **overrides):  # type: ignore[no-untyped-def]
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(session, **overrides)
        return tenant.id, user.id, user.email


async def test_missing_and_invalid_tokens_are_401(api_client: httpx.AsyncClient) -> None:
    assert (await api_client.get("/api/v1/me")).status_code == 401
    r = await api_client.get("/api/v1/me", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401
    assert r.headers["WWW-Authenticate"] == "Bearer"
    r = await api_client.get("/api/v1/me", headers={"Authorization": "Basic abc"})
    assert r.status_code == 401
    wrong_secret = token_for(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        secret="other-other-other-other-other-other-oth",
    )
    r = await api_client.get("/api/v1/me", headers={"Authorization": f"Bearer {wrong_secret}"})
    assert r.status_code == 401


async def test_expired_token_is_401(api_client: httpx.AsyncClient, database: Database) -> None:
    tid, uid, _ = await _tenant(database)
    expired = token_for(user_id=uid, tenant_id=tid, expires_in=1, now=0)
    r = await api_client.get("/api/v1/me", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401
    assert r.json()["detail"] == "token expired"


async def test_me_provisions_idempotently(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tid, _, _ = await _tenant(database)
    new_user = uuid.uuid4()
    headers = auth_headers(
        user_id=new_user, tenant_id=tid, role=Role.WRITER, email="New@Example.com"
    )
    for _ in range(2):
        r = await api_client.get("/api/v1/me", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["user_id"] == str(new_user)
        assert body["tenant_id"] == str(tid)
        assert body["role"] == "writer"
        assert body["email"] == "new@example.com"
    async with database.owner_session() as session:
        users = (await session.execute(select(func.count()).select_from(User))).scalar_one()
        memberships = (
            await session.execute(
                select(func.count()).select_from(Membership).where(Membership.user_id == new_user)
            )
        ).scalar_one()
    assert users == 2  # owner from factory + provisioned user
    assert memberships == 1


async def test_unknown_tenant_in_token_is_403(api_client: httpx.AsyncClient) -> None:
    r = await api_client.get(
        "/api/v1/me", headers=auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    )
    assert r.status_code == 403


async def test_tenant_comes_from_token_not_request(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tid_a, uid_a, _ = await _tenant(database)
    tid_b, _, _ = await _tenant(database)
    headers = auth_headers(user_id=uid_a, tenant_id=tid_a)
    r = await api_client.get("/api/v1/me", params={"tenant_id": str(tid_b)}, headers=headers)
    assert r.status_code == 200 and r.json()["tenant_id"] == str(tid_a)
    headers["X-Tenant-ID"] = str(tid_b)
    r = await api_client.get("/api/v1/me", headers=headers)
    assert r.json()["tenant_id"] == str(tid_a)


async def test_require_role_admin_route(api_client: httpx.AsyncClient, database: Database) -> None:
    tid, uid, _ = await _tenant(database)
    internal, admin_uid, _ = await _tenant(database, is_internal=True, slug="internal-test")
    for role in (Role.TENANT_OWNER, Role.BID_MANAGER, Role.VIEWER):
        r = await api_client.get(
            "/api/v1/admin/tenants", headers=auth_headers(user_id=uid, tenant_id=tid, role=role)
        )
        assert r.status_code == 403, role
    r = await api_client.get(
        "/api/v1/admin/tenants",
        headers=auth_headers(user_id=admin_uid, tenant_id=internal, role=Role.PLATFORM_ADMIN),
    )
    assert r.status_code == 200, r.text
    assert {t["id"] for t in r.json()["items"]} == {str(tid), str(internal)}
    async with database.owner_session() as session:
        audit = (
            await session.execute(select(AuditLog).where(AuditLog.action == ADMIN_ACCESS_ACTION))
        ).scalar_one()
    assert audit.tenant_id == internal and audit.user_id == admin_uid
    assert audit.object_id == "/api/v1/admin/tenants"
    assert audit.ip == "203.0.113.10"
    assert audit.request_id == r.headers["X-Request-ID"]


async def test_platform_admin_does_not_inherit_tenant_roles(app) -> None:  # type: ignore[no-untyped-def]
    """require_role(tenant roles) must reject platform_admin; only admin routes list it."""
    from app.api.deps import CurrentUser, require_role
    from fastapi import HTTPException

    dep = require_role(Role.TENANT_OWNER, Role.BID_MANAGER)
    admin = CurrentUser(
        id=uuid.uuid4(), email="a", tenant_id=uuid.uuid4(), role=Role.PLATFORM_ADMIN
    )
    owner = CurrentUser(id=uuid.uuid4(), email="o", tenant_id=uuid.uuid4(), role=Role.TENANT_OWNER)
    import pytest

    with pytest.raises(HTTPException) as exc:
        await dep(admin)  # type: ignore[misc]
    assert exc.value.status_code == 403
    assert await dep(owner) is owner  # type: ignore[misc]
    with pytest.raises(ValueError):
        require_role()


async def test_auth_rate_limit_per_ip(app, database: Database) -> None:  # type: ignore[no-untyped-def]
    tid, uid, _ = await _tenant(database)
    good = auth_headers(user_id=uid, tenant_id=tid)
    bad = {"Authorization": "Bearer garbage"}
    limit = app.state.settings.auth_rate_limit_per_minute
    assert limit == 20
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("198.51.100.7", 4000)), base_url="http://t"
    ) as attacker:
        for _ in range(limit):
            assert (await attacker.get("/api/v1/me", headers=bad)).status_code == 401
        r = await attacker.get("/api/v1/me", headers=bad)
        assert r.status_code == 429
        assert r.headers["Retry-After"] == "60"
        # Even a valid token from the limited IP is refused for the rest of the window.
        assert (await attacker.get("/api/v1/me", headers=good)).status_code == 429
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("198.51.100.8", 4000)), base_url="http://t"
    ) as other:
        assert (await other.get("/api/v1/me", headers=good)).status_code == 200
    app.state.auth_limiter.reset()
