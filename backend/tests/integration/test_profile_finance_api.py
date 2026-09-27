"""M1-02: size/finance fields, average turnover, socio-economic certifications, IN MSE enum."""

from __future__ import annotations

import uuid

import httpx
import pytest
from app.core.config import Region
from app.core.db import Database
from app.core.roles import Role
from app.models import Certification, CompanyProfile
from sqlalchemy import select

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner

REVENUE_USD = [
    {"fiscal_year": 2023, "amount": "1000000.00", "currency": "USD"},
    {"fiscal_year": 2025, "amount": "1600000.00", "currency": "USD"},
    {"fiscal_year": 2024, "amount": "1300000.00", "currency": "USD"},
    {"fiscal_year": 2022, "amount": "900000.00", "currency": "USD"},
]


async def _profile(  # type: ignore[no-untyped-def]
    api_client: httpx.AsyncClient, database: Database, region: str = "us", **tenant_overrides
):
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(
            session, region=Region(region), data_residency=Region(region), **tenant_overrides
        )
        tid, uid = tenant.id, user.id
    headers = auth_headers(user_id=uid, tenant_id=tid)
    r = await api_client.post(
        "/api/v1/profiles", json={"region": region, "legal_name": "Finance Co"}, headers=headers
    )
    assert r.status_code == 201, r.text
    return tid, uid, headers, r.json()["id"]


async def test_size_and_finance_fields_round_trip_with_average_turnover(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    _, _, headers, pid = await _profile(api_client, database)
    created = (await api_client.get(f"/api/v1/profiles/{pid}", headers=headers)).json()
    assert created["average_turnover"] is None
    assert created["employees_by_country"] == {} and created["annual_revenue"] == []
    assert (
        created["audited_fiscal_years"] == [] and created["solvency_certificate_available"] is False
    )
    body = {
        "employee_count_total": 160,
        "employees_by_country": {"us": 40, "IN": 120},
        "annual_revenue": REVENUE_USD,
        "audited_fiscal_years": [2025, 2023, 2024, 2024],
        "bonding_capacity_amount": "5000000",
        "bonding_capacity_currency": "USD",
    }
    r = await api_client.put(f"/api/v1/profiles/{pid}", json=body, headers=headers)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["employee_count_total"] == 160
    assert out["employees_by_country"] == {"US": 40, "IN": 120}
    assert [e["fiscal_year"] for e in out["annual_revenue"]] == [2022, 2023, 2024, 2025]
    assert out["audited_fiscal_years"] == [2023, 2024, 2025]
    assert (
        out["bonding_capacity_amount"] == "5000000.00" and out["bonding_capacity_currency"] == "USD"
    )
    # average of the LAST 3 fiscal years, currency preserved (core.finance)
    assert out["average_turnover"] == {
        "amount": "1300000.00",
        "currency": "USD",
        "fiscal_years": [2023, 2024, 2025],
    }
    async with database.owner_session() as session:
        row = await session.get(CompanyProfile, uuid.UUID(pid))
    assert row is not None and row.annual_revenue[0]["currency"] == "USD"
    assert row.employees_by_country == {"US": 40, "IN": 120}


async def test_indian_finance_fields_and_inr_average(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    _, _, headers, pid = await _profile(api_client, database, region="in")
    body = {
        "annual_revenue": [
            {"fiscal_year": 2024, "amount": "50000000", "currency": "INR"},
            {"fiscal_year": 2025, "amount": "70000000", "currency": "INR"},
        ],
        "net_worth_amount": "25000000.00",
        "net_worth_currency": "INR",
        "solvency_certificate_available": True,
        "mse_ownership": "women",
        "udyam_number": "UDYAM-KA-03-0012345",
        "udyam_category": "micro",
        "dpiit_number": "DIPP99999",
    }
    r = await api_client.put(f"/api/v1/profiles/{pid}", json=body, headers=headers)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["average_turnover"] == {
        "amount": "60000000.00",
        "currency": "INR",
        "fiscal_years": [2024, 2025],
    }
    assert out["net_worth_amount"] == "25000000.00" and out["net_worth_currency"] == "INR"
    assert out["solvency_certificate_available"] is True and out["mse_ownership"] == "women"
    assert out["udyam_category"] == "micro" and out["dpiit_number"] == "DIPP99999"
    for value in ("sc_st", "sc_st_women", "none"):
        r = await api_client.put(
            f"/api/v1/profiles/{pid}", json={"mse_ownership": value}, headers=headers
        )
        assert r.status_code == 200 and r.json()["mse_ownership"] == value
    r = await api_client.put(
        f"/api/v1/profiles/{pid}", json={"mse_ownership": "obc"}, headers=headers
    )
    assert r.status_code == 422
    r = await api_client.put(
        f"/api/v1/profiles/{pid}", json={"udyam_category": "large"}, headers=headers
    )
    assert r.status_code == 422


async def test_indian_only_finance_fields_are_rejected_for_us(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    _, _, headers, pid = await _profile(api_client, database)
    body = {
        "net_worth_amount": "1",
        "net_worth_currency": "USD",
        "solvency_certificate_available": True,
        "mse_ownership": "women",
    }
    r = await api_client.put(f"/api/v1/profiles/{pid}", json=body, headers=headers)
    assert r.status_code == 422
    assert r.json()["detail"]["fields"] == [
        "mse_ownership",
        "net_worth_amount",
        "net_worth_currency",
        "solvency_certificate_available",
    ]


@pytest.mark.parametrize(
    "body",
    [
        {"annual_revenue": [{"fiscal_year": 2025, "amount": "1", "currency": "EUR"}]},
        {"annual_revenue": [{"fiscal_year": 2025, "amount": "-1", "currency": "USD"}]},
        {
            "annual_revenue": [
                {"fiscal_year": 2025, "amount": "1", "currency": "USD"},
                {"fiscal_year": 2025, "amount": "2", "currency": "USD"},
            ]
        },
        {
            "annual_revenue": [
                {"fiscal_year": 2025, "amount": "1", "currency": "USD"},
                {"fiscal_year": 2024, "amount": "2", "currency": "INR"},
            ]
        },
        {"annual_revenue": [{"fiscal_year": 1980, "amount": "1", "currency": "USD"}]},
        {"annual_revenue": [{"fiscal_year": 2025, "amount": "1.234", "currency": "USD"}]},
        {"employees_by_country": {"USA": 1}},
        {"employees_by_country": {"US": -1}},
        {"employee_count_total": -5},
        {"bonding_capacity_currency": "GBP"},
        {"audited_fiscal_years": [1900]},
    ],
)
async def test_finance_validation(
    api_client: httpx.AsyncClient, database: Database, body: dict
) -> None:
    _, _, headers, pid = await _profile(api_client, database)
    r = await api_client.put(f"/api/v1/profiles/{pid}", json=body, headers=headers)
    assert r.status_code == 422, r.text


async def test_certifications_crud(api_client: httpx.AsyncClient, database: Database) -> None:
    tid, _, headers, pid = await _profile(api_client, database)
    url = f"/api/v1/profiles/{pid}/certifications"
    assert (await api_client.get(url, headers=headers)).json() == []
    r = await api_client.post(
        url,
        json={
            "kind": "8a",
            "cert_number": "8A-2024-001",
            "issued_on": "2024-01-15",
            "expires_on": "2033-01-14",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    cert = r.json()
    assert cert["kind"] == "8a" and cert["cert_number"] == "8A-2024-001"
    assert cert["expires_on"] == "2033-01-14" and cert["profile_id"] == pid
    for kind in ("hubzone", "wosb", "edwosb", "sdvosb", "vosb", "sdb"):
        r = await api_client.post(
            url, json={"kind": kind, "expires_on": "2027-06-30"}, headers=headers
        )
        assert r.status_code == 201, (kind, r.text)
    listed = (await api_client.get(url, headers=headers)).json()
    assert [c["kind"] for c in listed] == [
        "8a",
        "hubzone",
        "wosb",
        "edwosb",
        "sdvosb",
        "vosb",
        "sdb",
    ]

    r = await api_client.put(
        f"{url}/{cert['id']}", json={"expires_on": "2034-01-14"}, headers=headers
    )
    assert r.status_code == 200 and r.json()["expires_on"] == "2034-01-14"
    assert r.json()["cert_number"] == "8A-2024-001"
    r = await api_client.get(f"{url}/{cert['id']}", headers=headers)
    assert r.status_code == 200 and r.json()["expires_on"] == "2034-01-14"
    # the profile version moves with its children
    assert (await api_client.get(f"/api/v1/profiles/{pid}", headers=headers)).json()["version"] == 9

    r = await api_client.delete(f"{url}/{cert['id']}", headers=headers)
    assert r.status_code == 204
    assert (await api_client.get(f"{url}/{cert['id']}", headers=headers)).status_code == 404
    assert len((await api_client.get(url, headers=headers)).json()) == 6
    async with database.owner_session() as session:
        rows = (await session.execute(select(Certification))).scalars().all()
    assert len(rows) == 6 and all(
        r.tenant_id == tid and r.profile_id == uuid.UUID(pid) for r in rows
    )


async def test_certification_validation_and_region(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    _, _, us_headers, us_pid = await _profile(api_client, database)
    _, _, in_headers, in_pid = await _profile(api_client, database, region="in")
    us_url = f"/api/v1/profiles/{us_pid}/certifications"
    in_url = f"/api/v1/profiles/{in_pid}/certifications"
    r = await api_client.post(in_url, json={"kind": "8a"}, headers=in_headers)
    assert r.status_code == 422 and r.json()["detail"]["fields"] == ["kind=8a"]
    r = await api_client.post(us_url, json={"kind": "stqc"}, headers=us_headers)
    assert r.status_code == 422 and r.json()["detail"]["region"] == "us"
    assert (
        await api_client.post(in_url, json={"kind": "cert_in"}, headers=in_headers)
    ).status_code == 201
    assert (
        await api_client.post(in_url, json={"kind": "iso_27001"}, headers=in_headers)
    ).status_code == 201
    r = await api_client.post(us_url, json={"kind": "sdb"}, headers=us_headers)
    assert r.status_code == 201
    r = await api_client.put(
        f"{us_url}/{r.json()['id']}", json={"kind": "stqc"}, headers=us_headers
    )
    assert r.status_code == 422
    assert (
        await api_client.post(us_url, json={"kind": "minority"}, headers=us_headers)
    ).status_code == 422
    assert (
        await api_client.post(us_url, json={"kind": "8a", "bogus": 1}, headers=us_headers)
    ).status_code == 422
    r = await api_client.post(
        us_url,
        json={"kind": "8a", "issued_on": "2025-01-01", "expires_on": "2024-01-01"},
        headers=us_headers,
    )
    assert r.status_code == 422
    r = await api_client.post(
        us_url, json={"kind": "8a", "file_id": str(uuid.uuid4())}, headers=us_headers
    )
    assert r.status_code == 422 and "file_id" in r.text


async def test_certifications_roles_and_isolation(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tid, _, owner, pid = await _profile(api_client, database)
    url = f"/api/v1/profiles/{pid}/certifications"
    cert = (await api_client.post(url, json={"kind": "wosb"}, headers=owner)).json()
    for role in (Role.WRITER, Role.REVIEWER, Role.VIEWER):
        headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tid, role=role)
        assert (await api_client.get(url, headers=headers)).status_code == 200
        assert (
            await api_client.post(url, json={"kind": "sdb"}, headers=headers)
        ).status_code == 403
        assert (
            await api_client.put(f"{url}/{cert['id']}", json={}, headers=headers)
        ).status_code == 403
        assert (await api_client.delete(f"{url}/{cert['id']}", headers=headers)).status_code == 403
    manager = auth_headers(user_id=uuid.uuid4(), tenant_id=tid, role=Role.BID_MANAGER)
    assert (await api_client.post(url, json={"kind": "sdb"}, headers=manager)).status_code == 201
    _, _, stranger, other_pid = await _profile(api_client, database)
    assert (await api_client.get(url, headers=stranger)).status_code == 404
    assert (await api_client.get(f"{url}/{cert['id']}", headers=stranger)).status_code == 404
    # an item id from another profile of the same tenant is not reachable through this profile
    other_url = f"/api/v1/profiles/{other_pid}/certifications"
    r = await api_client.get(f"{other_url}/{cert['id']}", headers=stranger)
    assert r.status_code == 404
