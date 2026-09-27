"""M1-03: profile codes (NAICS validated against the 2022 table), keywords, service lines."""

from __future__ import annotations

import uuid

import httpx
import pytest
from app.core.config import Region
from app.core.db import Database
from app.core.roles import Role
from app.models import ProfileCode

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
        "/api/v1/profiles", json={"region": region, "legal_name": "Codes Co"}, headers=headers
    )
    assert r.status_code == 201, r.text
    return tid, headers, r.json()["id"]


async def test_codes_crud_with_naics_validation_and_primary(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tid, headers, pid = await _profile(api_client, database)
    url = f"/api/v1/profiles/{pid}/codes"
    r = await api_client.post(
        url, json={"scheme": "naics", "code": "541-511", "is_primary": True}, headers=headers
    )
    assert r.status_code == 201, r.text
    primary = r.json()
    assert primary["code"] == "541511" and primary["is_primary"] is True
    assert primary["title"] == "Custom Computer Programming Services"
    r = await api_client.post(url, json={"scheme": "naics", "code": "541510"}, headers=headers)
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == {
        "error": "unknown_naics",
        "code": "541510",
        "message": "541510 is not a 2022 NAICS code",
    }
    assert (
        await api_client.post(url, json={"scheme": "naics", "code": "5415"}, headers=headers)
    ).status_code == 422
    second = await api_client.post(url, json={"scheme": "naics", "code": "541512"}, headers=headers)
    assert second.status_code == 201 and second.json()["is_primary"] is False
    # duplicates are 409, not 500
    r = await api_client.post(url, json={"scheme": "naics", "code": "541511"}, headers=headers)
    assert r.status_code == 409
    # PSC / ALN formats
    assert (
        await api_client.post(url, json={"scheme": "psc", "code": "d302"}, headers=headers)
    ).json()["code"] == "D302"
    assert (
        await api_client.post(url, json={"scheme": "psc", "code": "D30"}, headers=headers)
    ).status_code == 422
    assert (
        await api_client.post(url, json={"scheme": "aln", "code": "93.778"}, headers=headers)
    ).status_code == 201
    assert (
        await api_client.post(url, json={"scheme": "aln", "code": "93778"}, headers=headers)
    ).status_code == 422
    # promoting another NAICS code demotes the previous primary within the scheme only
    r = await api_client.put(
        f"{url}/{second.json()['id']}", json={"is_primary": True}, headers=headers
    )
    assert r.status_code == 200 and r.json()["is_primary"] is True
    listed = (await api_client.get(url, headers=headers)).json()
    by_code = {c["code"]: c for c in listed}
    assert by_code["541511"]["is_primary"] is False and by_code["541512"]["is_primary"] is True
    assert [c["scheme"] for c in listed] == ["naics", "naics", "psc", "aln"]
    assert (await api_client.delete(f"{url}/{primary['id']}", headers=headers)).status_code == 204
    async with database.owner_session() as session:
        rows = (await session.execute(ProfileCode.__table__.select())).all()
    assert len(rows) == 3 and all(r.tenant_id == tid for r in rows)


async def test_code_scheme_follows_region(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    _, us_headers, us_pid = await _profile(api_client, database)
    _, in_headers, in_pid = await _profile(api_client, database, region="in")
    r = await api_client.post(
        f"/api/v1/profiles/{in_pid}/codes",
        json={"scheme": "naics", "code": "541511"},
        headers=in_headers,
    )
    assert r.status_code == 422 and r.json()["detail"]["fields"] == ["scheme=naics"]
    r = await api_client.post(
        f"/api/v1/profiles/{us_pid}/codes",
        json={"scheme": "gem", "code": "Computer Software"},
        headers=us_headers,
    )
    assert r.status_code == 422 and r.json()["detail"]["error"] == "region_mismatch"
    r = await api_client.post(
        f"/api/v1/profiles/{in_pid}/codes",
        json={"scheme": "gem", "code": "  Computer   Software "},
        headers=in_headers,
    )
    assert r.status_code == 201 and r.json()["code"] == "Computer Software"
    r = await api_client.post(
        f"/api/v1/profiles/{in_pid}/codes",
        json={"scheme": "india_category", "code": "IT Services", "is_primary": True},
        headers=in_headers,
    )
    assert r.status_code == 201


async def test_keywords_crud_and_weights(api_client: httpx.AsyncClient, database: Database) -> None:
    _, headers, pid = await _profile(api_client, database)
    url = f"/api/v1/profiles/{pid}/keywords"
    r = await api_client.post(
        url,
        json={"kind": "include", "term": "  Cloud   Migration ", "weight": 2.5},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    kw = r.json()
    assert kw["term"] == "cloud migration" and kw["weight"] == "2.5" and kw["kind"] == "include"
    r = await api_client.post(url, json={"kind": "exclude", "term": "janitorial"}, headers=headers)
    assert r.status_code == 201 and r.json()["weight"] == "1.0"
    for weight in (0, 0.05, 5.1, -1, "abc"):
        r = await api_client.post(
            url, json={"kind": "include", "term": f"w{weight}", "weight": weight}, headers=headers
        )
        assert r.status_code == 422, weight
    assert (
        await api_client.post(
            url, json={"kind": "include", "term": "edge", "weight": 0.1}, headers=headers
        )
    ).status_code == 201
    assert (
        await api_client.post(
            url, json={"kind": "include", "term": "edge2", "weight": 5}, headers=headers
        )
    ).status_code == 201
    assert (
        await api_client.post(
            url, json={"kind": "include", "term": "CLOUD MIGRATION"}, headers=headers
        )
    ).status_code == 409
    assert (
        await api_client.post(
            url, json={"kind": "exclude", "term": "cloud migration"}, headers=headers
        )
    ).status_code == 201
    assert (
        await api_client.post(url, json={"kind": "boost", "term": "x"}, headers=headers)
    ).status_code == 422
    assert (
        await api_client.post(url, json={"kind": "include", "term": "   "}, headers=headers)
    ).status_code == 422
    r = await api_client.put(f"{url}/{kw['id']}", json={"weight": "4.0"}, headers=headers)
    assert r.status_code == 200 and r.json()["weight"] == "4.0"
    assert (
        await api_client.put(f"{url}/{kw['id']}", json={"weight": 9}, headers=headers)
    ).status_code == 422
    assert (
        await api_client.put(f"{url}/{kw['id']}", json={"term": "renamed"}, headers=headers)
    ).status_code == 422
    listed = (await api_client.get(url, headers=headers)).json()
    assert [k["term"] for k in listed if k["kind"] == "include"] == [
        "edge2",
        "cloud migration",
        "edge",
    ]
    assert (await api_client.delete(f"{url}/{kw['id']}", headers=headers)).status_code == 204
    assert len((await api_client.get(url, headers=headers)).json()) == 4


async def test_service_lines_crud_and_150_word_limit(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    _, headers, pid = await _profile(api_client, database)
    url = f"/api/v1/profiles/{pid}/service-lines"
    ok = " ".join(["word"] * 150)
    r = await api_client.post(
        url,
        json={
            "name": "Cloud Migration",
            "description": ok,
            "differentiators": ["FedRAMP experience", "24x7 SOC"],
            "tools": ["AWS", "Terraform"],
            "delivery_model": "hybrid",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    line = r.json()
    assert line["tools"] == ["AWS", "Terraform"] and line["delivery_model"] == "hybrid"
    too_long = " ".join(["word"] * 151)
    r = await api_client.post(url, json={"name": "Long", "description": too_long}, headers=headers)
    assert r.status_code == 422 and "151 words" in r.text
    r = await api_client.put(f"{url}/{line['id']}", json={"description": too_long}, headers=headers)
    assert r.status_code == 422
    r = await api_client.put(
        f"{url}/{line['id']}",
        json={"description": "Shorter now.", "delivery_model": "remote"},
        headers=headers,
    )
    assert (
        r.status_code == 200
        and r.json()["description"] == "Shorter now."
        and r.json()["delivery_model"] == "remote"
    )
    assert r.json()["differentiators"] == ["FedRAMP experience", "24x7 SOC"]
    assert (
        await api_client.post(url, json={"name": "", "description": "x"}, headers=headers)
    ).status_code == 422
    assert (
        await api_client.post(
            url, json={"name": "x", "description": "y", "delivery_model": "moon"}, headers=headers
        )
    ).status_code == 422
    assert (
        await api_client.post(url, json={"name": "Second", "description": "Two"}, headers=headers)
    ).status_code == 201
    assert [s["name"] for s in (await api_client.get(url, headers=headers)).json()] == [
        "Cloud Migration",
        "Second",
    ]
    assert (await api_client.delete(f"{url}/{line['id']}", headers=headers)).status_code == 204
    assert (await api_client.get(f"{url}/{line['id']}", headers=headers)).status_code == 404


@pytest.mark.parametrize("resource", ["codes", "keywords", "service-lines"])
async def test_what_we_sell_roles_and_isolation(
    api_client: httpx.AsyncClient, database: Database, resource: str
) -> None:
    tid, owner, pid = await _profile(api_client, database)
    url = f"/api/v1/profiles/{pid}/{resource}"
    body = {
        "codes": {"scheme": "psc", "code": "D302"},
        "keywords": {"kind": "include", "term": "x"},
        "service-lines": {"name": "n", "description": "d"},
    }[resource]
    created = await api_client.post(url, json=body, headers=owner)
    assert created.status_code == 201
    for role in (Role.WRITER, Role.REVIEWER, Role.VIEWER):
        headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tid, role=role)
        assert (await api_client.get(url, headers=headers)).status_code == 200
        assert (await api_client.post(url, json=body, headers=headers)).status_code == 403
        assert (
            await api_client.delete(f"{url}/{created.json()['id']}", headers=headers)
        ).status_code == 403
    _, stranger, _ = await _profile(api_client, database)
    assert (await api_client.get(url, headers=stranger)).status_code == 404
    assert (await api_client.post(url, json=body, headers=stranger)).status_code == 404
