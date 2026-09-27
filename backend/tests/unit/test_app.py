"""M0-03: health, OpenAPI layout, request id middleware."""

import re

import httpx
from app import __version__
from app.core.context import get_request_id, sanitize_request_id, set_request_id


async def test_healthz(client: httpx.AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "version": __version__}


async def test_openapi_lists_business_routes_under_api_v1(client: httpx.AsyncClient) -> None:
    spec = (await client.get("/openapi.json")).json()
    paths = set(spec["paths"])
    business = {p for p in paths if p != "/healthz"}
    assert business, "expected at least one business route"
    assert all(p.startswith("/api/v1/") for p in business), sorted(business)
    assert "/api/v1/system/info" in paths


async def test_system_info(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/v1/system/info")
    assert resp.status_code == 200
    body = resp.json()
    assert body["region"] in ("us", "in")
    assert body["version"] == __version__


async def test_request_id_generated(client: httpx.AsyncClient) -> None:
    resp = await client.get("/healthz")
    rid = resp.headers.get("X-Request-ID")
    assert rid and re.fullmatch(r"[0-9a-f]{32}", rid)
    other = (await client.get("/healthz")).headers["X-Request-ID"]
    assert other != rid


async def test_request_id_echoed_and_present_on_errors(client: httpx.AsyncClient) -> None:
    resp = await client.get("/healthz", headers={"X-Request-ID": "trace-abc-123"})
    assert resp.headers["X-Request-ID"] == "trace-abc-123"
    resp = await client.get("/api/v1/does-not-exist", headers={"X-Request-ID": "trace-404"})
    assert resp.status_code == 404
    assert resp.headers["X-Request-ID"] == "trace-404"
    resp = await client.get("/healthz", headers={"X-Request-ID": "bad id with spaces"})
    assert resp.headers["X-Request-ID"] != "bad id with spaces"


def test_sanitize_request_id() -> None:
    assert sanitize_request_id(None) is None
    assert sanitize_request_id("   ") is None
    assert sanitize_request_id("x" * 129) is None
    assert sanitize_request_id("ok-1") == "ok-1"
    assert sanitize_request_id("nö") is None
    rid = set_request_id(None)
    assert get_request_id() == rid
