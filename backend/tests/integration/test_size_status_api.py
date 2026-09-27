"""M1-07: GET /profiles/{id} exposes size_status_by_naics computed from finance + codes."""

from __future__ import annotations

import httpx
from app.core.db import Database

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner


async def _profile(api_client: httpx.AsyncClient, database: Database, region: str = "us"):  # type: ignore[no-untyped-def]
    from app.core.config import Region

    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(
            session, region=Region(region), data_residency=Region(region)
        )
        tid, uid = tenant.id, user.id
    headers = auth_headers(user_id=uid, tenant_id=tid)
    r = await api_client.post(
        "/api/v1/profiles", json={"region": region, "legal_name": "Size Co"}, headers=headers
    )
    assert r.status_code == 201, r.text
    return headers, r.json()["id"]


async def test_size_status_by_naics(api_client: httpx.AsyncClient, database: Database) -> None:
    headers, pid = await _profile(api_client, database)
    url = f"/api/v1/profiles/{pid}"
    assert (await api_client.get(url, headers=headers)).json()["size_status_by_naics"] == {}
    codes = f"{url}/codes"
    for code, primary in (("541511", True), ("336411", False), ("541330", False)):
        r = await api_client.post(
            codes, json={"scheme": "naics", "code": code, "is_primary": primary}, headers=headers
        )
        assert r.status_code == 201, r.text
    # no finance data yet -> unknown, with the threshold still reported
    status = (await api_client.get(url, headers=headers)).json()["size_status_by_naics"]
    assert list(status) == ["541511", "336411", "541330"]  # primary first
    assert status["541511"] == {
        "status": "unknown",
        "basis": "receipts",
        "threshold": "34000000",
        "measured": None,
        "reason": "average receipts unknown",
    }
    assert status["336411"]["status"] == "unknown" and status["336411"]["basis"] == "employees"

    r = await api_client.put(
        url,
        json={
            "employee_count_total": 1200,
            "annual_revenue": [
                {"fiscal_year": 2023, "amount": "20000000", "currency": "USD"},
                {"fiscal_year": 2024, "amount": "30000000", "currency": "USD"},
                {"fiscal_year": 2025, "amount": "40000000", "currency": "USD"},
            ],
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    status = r.json()["size_status_by_naics"]
    assert status["541511"]["status"] == "small"  # avg 30M <= 34M
    assert status["541511"]["measured"] == "30000000.00"
    assert status["541330"]["status"] == "other_than_small"  # 25.5M cap
    assert status["336411"]["status"] == "small"  # 1200 <= 1500 employees
    assert status["336411"]["measured"] == "1200"
    r = await api_client.put(url, json={"employee_count_total": 1600}, headers=headers)
    assert r.json()["size_status_by_naics"]["336411"]["status"] == "other_than_small"
    read = (await api_client.get(url, headers=headers)).json()["size_status_by_naics"]
    assert read == r.json()["size_status_by_naics"]


async def test_inr_turnover_does_not_feed_sba_receipts(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    headers, pid = await _profile(api_client, database)
    url = f"/api/v1/profiles/{pid}"
    # a US profile that happens to report INR revenue: receipts-based codes stay unknown
    assert (
        await api_client.post(
            f"{url}/codes", json={"scheme": "naics", "code": "541511"}, headers=headers
        )
    ).status_code == 201
    assert (
        await api_client.post(
            f"{url}/codes", json={"scheme": "naics", "code": "336411"}, headers=headers
        )
    ).status_code == 201
    r = await api_client.put(
        url,
        json={
            "employee_count_total": 50,
            "annual_revenue": [{"fiscal_year": 2025, "amount": "10", "currency": "INR"}],
        },
        headers=headers,
    )
    assert r.status_code == 200
    status = r.json()["size_status_by_naics"]
    assert status["541511"]["status"] == "unknown"
    assert status["336411"]["status"] == "small"
