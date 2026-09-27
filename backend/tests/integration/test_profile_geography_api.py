"""M1-04: geography, buyers, value range, notice/contract types, teaming partners — round trip."""

from __future__ import annotations

import uuid

import httpx
import pytest
from app.core.config import Region
from app.core.crypto import is_encrypted
from app.core.db import Database
from app.core.notice_types import CONTRACT_TYPE_VALUES, NOTICE_TYPE_VALUES
from app.core.roles import Role
from sqlalchemy import text

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner


async def _profile(api_client: httpx.AsyncClient, database: Database, region: str = "us"):  # type: ignore[no-untyped-def]
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(
            session, region=Region(region), data_residency=Region(region)
        )
        tid, uid = tenant.id, user.id
    headers = auth_headers(user_id=uid, tenant_id=tid)
    r = await api_client.post(
        "/api/v1/profiles", json={"region": region, "legal_name": "Geo Co"}, headers=headers
    )
    assert r.status_code == 201, r.text
    return tid, headers, r.json()["id"]


WHERE = {
    "target_countries": ["us", "IN", "us"],
    "target_us_states": ["va", "md", "DC"],
    "target_in_states": ["ka", "TS"],
    "target_cities": [" Reston ", "Bengaluru", "reston"],
    "remote_ok": True,
    "target_buyers": ["Department of Veterans Affairs", "NTPC"],
    "blocked_buyers": ["Department of Defense"],
    "value_min_usd": "100000",
    "value_max_usd": "5000000.50",
    "value_min_inr": "1000000",
    "value_max_inr": "250000000",
    "notice_types_wanted": ["rfp", "rfq", "sources_sought", "rfp", "gem_bid"],
    "contract_types_preferred": ["ffp", "tm", "idiq_task_order"],
    "teaming_roles": ["prime", "sub"],
}

EXPECTED = {
    "target_countries": ["US", "IN"],
    "target_us_states": ["VA", "MD", "DC"],
    "target_in_states": ["KA", "TS"],
    "target_cities": ["Reston", "Bengaluru"],
    "remote_ok": True,
    "target_buyers": ["Department of Veterans Affairs", "NTPC"],
    "blocked_buyers": ["Department of Defense"],
    "value_min_usd": "100000.00",
    "value_max_usd": "5000000.50",
    "value_min_inr": "1000000.00",
    "value_max_inr": "250000000.00",
    "notice_types_wanted": ["rfp", "rfq", "sources_sought", "gem_bid"],
    "contract_types_preferred": ["ffp", "tm", "idiq_task_order"],
    "teaming_roles": ["prime", "sub"],
}


async def test_where_and_how_big_round_trip(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    _, headers, pid = await _profile(api_client, database)
    before = (await api_client.get(f"/api/v1/profiles/{pid}", headers=headers)).json()
    assert before["remote_ok"] is False and before["notice_types_wanted"] == []
    assert before["value_min_usd"] is None and before["target_us_states"] == []
    r = await api_client.put(f"/api/v1/profiles/{pid}", json=WHERE, headers=headers)
    assert r.status_code == 200, r.text
    written = r.json()
    read_back = (await api_client.get(f"/api/v1/profiles/{pid}", headers=headers)).json()
    for key, value in EXPECTED.items():
        assert written[key] == value, key
        assert read_back[key] == value, key
    # writing the response back is idempotent (round trip equality, masked fields excepted)
    again = await api_client.put(
        f"/api/v1/profiles/{pid}", json={k: read_back[k] for k in EXPECTED}, headers=headers
    )
    assert again.status_code == 200
    assert {k: again.json()[k] for k in EXPECTED} == EXPECTED


async def test_notice_and_contract_types_match_spec(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    _, headers, pid = await _profile(api_client, database)
    r = await api_client.put(
        f"/api/v1/profiles/{pid}",
        json={
            "notice_types_wanted": list(NOTICE_TYPE_VALUES),
            "contract_types_preferred": list(CONTRACT_TYPE_VALUES),
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["notice_types_wanted"] == list(NOTICE_TYPE_VALUES)
    assert r.json()["contract_types_preferred"] == list(CONTRACT_TYPE_VALUES)
    for bad in ("tender", "RFP", "solicitation"):
        r = await api_client.put(
            f"/api/v1/profiles/{pid}", json={"notice_types_wanted": [bad]}, headers=headers
        )
        assert r.status_code == 422, bad
    assert (
        await api_client.put(
            f"/api/v1/profiles/{pid}", json={"contract_types_preferred": ["cpff"]}, headers=headers
        )
    ).status_code == 422
    assert (
        await api_client.put(
            f"/api/v1/profiles/{pid}", json={"teaming_roles": ["mentor"]}, headers=headers
        )
    ).status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"target_us_states": ["Virginia"]},
        {"target_us_states": ["ZZ"]},
        {"target_in_states": ["XX"]},
        {"target_countries": ["USA"]},
        {"value_min_usd": "10", "value_max_usd": "5"},
        {"value_min_inr": "10", "value_max_inr": "5"},
        {"value_min_usd": "-1"},
        {"value_max_inr": "1.234"},
        {"target_cities": ["x" * 201]},
    ],
)
async def test_geography_validation(
    api_client: httpx.AsyncClient, database: Database, body: dict
) -> None:
    _, headers, pid = await _profile(api_client, database)
    assert (
        await api_client.put(f"/api/v1/profiles/{pid}", json=body, headers=headers)
    ).status_code == 422


async def test_value_range_bounds_can_be_set_separately(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    _, headers, pid = await _profile(api_client, database)
    assert (
        await api_client.put(
            f"/api/v1/profiles/{pid}", json={"value_max_usd": "100"}, headers=headers
        )
    ).status_code == 200
    assert (
        await api_client.put(
            f"/api/v1/profiles/{pid}", json={"value_min_usd": "50"}, headers=headers
        )
    ).status_code == 200
    r = await api_client.put(
        f"/api/v1/profiles/{pid}",
        json={"value_min_usd": None, "value_max_usd": None},
        headers=headers,
    )
    assert r.status_code == 200 and r.json()["value_min_usd"] is None


async def test_teaming_partners_crud_with_masked_pan(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tid, headers, pid = await _profile(api_client, database)
    url = f"/api/v1/profiles/{pid}/teaming-partners"
    r = await api_client.post(
        url,
        json={
            "name": "Beta Systems Pvt Ltd",
            "relationship": "sub",
            "uei": "beta12345678",
            "pan": "abcde1234f",
            "capabilities": ["Data engineering", " data engineering", "GIS"],
            "website": "https://beta.example.com",
            "contact_email": "bd@beta.example.com",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    partner = r.json()
    assert partner["uei"] == "BETA12345678" and partner["pan"] == "•••••234F"
    assert (
        partner["capabilities"] == ["Data engineering", "GIS"] and partner["relationship"] == "sub"
    )
    assert "ABCDE1234F" not in r.text
    async with database.owner_session() as session:
        raw = (
            await session.execute(
                text("SELECT pan, uei FROM teaming_partners WHERE id = :id"),
                {"id": uuid.UUID(partner["id"])},
            )
        ).one()
    assert is_encrypted(raw.pan) and raw.uei == "BETA12345678"
    # masked PAN echoed back is a no-op; a new PAN replaces; relationship changes
    r = await api_client.put(
        f"{url}/{partner['id']}", json={"pan": "•••••234F", "relationship": "jv"}, headers=headers
    )
    assert (
        r.status_code == 200 and r.json()["pan"] == "•••••234F" and r.json()["relationship"] == "jv"
    )
    async with database.owner_session() as session:
        after = (
            await session.execute(
                text("SELECT pan FROM teaming_partners WHERE id = :id"),
                {"id": uuid.UUID(partner["id"])},
            )
        ).scalar_one()
    assert after == raw.pan
    r = await api_client.put(f"{url}/{partner['id']}", json={"pan": "ZZZZZ9999Z"}, headers=headers)
    assert r.status_code == 200 and r.json()["pan"] == "•••••999Z"
    for bad in (
        {"name": "x", "relationship": "partner"},
        {"name": "x", "relationship": "sub", "uei": "SHORT"},
        {"name": "x", "relationship": "sub", "pan": "BAD"},
        {"relationship": "sub"},
    ):
        assert (await api_client.post(url, json=bad, headers=headers)).status_code == 422, bad
    for rel in ("prime", "sub", "jv"):
        assert (
            await api_client.post(
                url, json={"name": f"P-{rel}", "relationship": rel}, headers=headers
            )
        ).status_code == 201
    listed = (await api_client.get(url, headers=headers)).json()
    assert [p["name"] for p in listed] == ["Beta Systems Pvt Ltd", "P-jv", "P-prime", "P-sub"]
    assert (await api_client.delete(f"{url}/{partner['id']}", headers=headers)).status_code == 204
    writer = auth_headers(user_id=uuid.uuid4(), tenant_id=tid, role=Role.WRITER)
    assert (await api_client.get(url, headers=writer)).status_code == 200
    assert (
        await api_client.post(url, json={"name": "x", "relationship": "sub"}, headers=writer)
    ).status_code == 403
    _, stranger, _ = await _profile(api_client, database)
    assert (await api_client.get(url, headers=stranger)).status_code == 404
