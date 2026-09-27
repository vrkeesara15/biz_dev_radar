"""M0-11: every route, called as tenant B with tenant A's ids, must leak nothing of A.

Routes are discovered from app.openapi() at collection time, so new endpoints are
covered automatically and fail loudly until a factory exists in factories.py.
"""

from __future__ import annotations

import httpx
import pytest
from app.core.config import Settings
from app.core.db import Database
from app.main import create_app

from tests.auth import auth_headers
from tests.isolation.factories import (
    FACTORIES,
    OK_STATUSES,
    IsolationContext,
    RouteCall,
    build_context,
    is_public,
)


def discover_routes() -> list[tuple[str, str]]:
    spec = create_app(Settings(_env_file=None)).openapi()  # type: ignore[call-arg]
    return sorted(
        (method.upper(), path)
        for path, ops in spec["paths"].items()
        for method in ops
        if method.lower() in {"get", "post", "put", "patch", "delete"}
    )


ROUTES = discover_routes()
TENANT_ROUTES = [r for r in ROUTES if not is_public(*r)]
ROUTE_IDS = [f"{m} {p}" for m, p in TENANT_ROUTES]


def _url(path: str, call: RouteCall) -> str:
    url = path
    for name, value in call.path_params.items():
        url = url.replace(f"{{{name}}}", str(value))
    assert "{" not in url, f"unfilled path params in {path}: factory gave {call.path_params}"
    return url


def test_routes_discovered() -> None:
    assert ROUTES, "openapi listed no routes"
    assert TENANT_ROUTES, "expected at least one tenant-scoped route"


@pytest.mark.parametrize(("method", "path"), TENANT_ROUTES, ids=ROUTE_IDS)
def test_every_route_has_a_factory_or_is_public(method: str, path: str) -> None:
    assert (method, path) in FACTORIES, (
        f"no isolation factory for {method} {path}. Register one in "
        "backend/tests/isolation/factories.py (or add it to PUBLIC_ROUTES if it truly "
        "needs no tenant, e.g. health/docs/auth/webhooks)."
    )


@pytest.fixture()
async def ctx(database: Database) -> IsolationContext:
    return await build_context(database)


@pytest.mark.parametrize(("method", "path"), TENANT_ROUTES, ids=ROUTE_IDS)
async def test_cross_tenant_call_leaks_nothing(
    method: str, path: str, api_client: httpx.AsyncClient, ctx: IsolationContext
) -> None:
    factory = FACTORIES.get((method, path))
    if factory is None:
        pytest.fail(f"no isolation factory for {method} {path}")
    call = factory(ctx)
    url = _url(path, call)

    # Tenant B calls the route with tenant A's identifiers.
    as_b = auth_headers(
        user_id=ctx.b.owner_id, tenant_id=ctx.b.id, role=call.role, email=ctx.b.owner_email
    )
    resp = await api_client.request(method, url, json=call.json, params=call.params, headers=as_b)
    assert resp.status_code < 500, f"{method} {url} -> {resp.status_code}: {resp.text[:300]}"
    if 200 <= resp.status_code < 300:
        leaked = [name for name, value in ctx.a.ids.items() if value in resp.text]
        assert not leaked, (
            f"{method} {url} answered {resp.status_code} to tenant B with tenant A's "
            f"{leaked}: {resp.text[:500]}"
        )

    # Sanity: the same call by tenant A's owner is well-formed (so a 403/404 above is a
    # real isolation result and not a malformed probe).
    as_a = auth_headers(
        user_id=ctx.a.owner_id, tenant_id=ctx.a.id, role=call.role, email=ctx.a.owner_email
    )
    resp_a = await api_client.request(method, url, json=call.json, params=call.params, headers=as_a)
    assert resp_a.status_code in call.owner_expect, (
        f"{method} {url} as owner of A -> {resp_a.status_code}, expected one of "
        f"{sorted(call.owner_expect)}: {resp_a.text[:300]}"
    )


@pytest.mark.parametrize(("method", "path"), TENANT_ROUTES, ids=ROUTE_IDS)
async def test_tenant_routes_require_auth(
    method: str, path: str, api_client: httpx.AsyncClient, ctx: IsolationContext
) -> None:
    call = FACTORIES[(method, path)](ctx)
    resp = await api_client.request(method, _url(path, call), json=call.json, params=call.params)
    assert resp.status_code == 401, f"{method} {path} without a token -> {resp.status_code}"


async def test_public_routes_do_not_expose_tenant_data(
    api_client: httpx.AsyncClient, ctx: IsolationContext
) -> None:
    for method, path in ROUTES:
        if not is_public(method, path) or "{" in path:
            continue
        resp = await api_client.request(method, path)
        if resp.status_code in OK_STATUSES:
            leaked = [n for n, v in ctx.a.ids.items() if v in resp.text]
            assert not leaked, f"public {method} {path} leaks {leaked}"
