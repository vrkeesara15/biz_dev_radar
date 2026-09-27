"""M1-01: company_profiles table, encrypted+masked fields, identifier validation, region gating."""

from __future__ import annotations

import uuid

import httpx
import pytest
from app.core.config import Region
from app.core.crypto import is_encrypted
from app.core.db import Database
from app.core.plan import Plan
from app.core.roles import Role
from app.models import AuditLog, CompanyProfile
from app.models.types import get_field_cipher
from sqlalchemy import select, text

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner

US_BODY = {
    "region": "us",
    "legal_name": "Alpha Federal LLC",
    "dba_names": ["Alpha Fed"],
    "addresses": [
        {
            "kind": "hq",
            "line1": "1 Main St",
            "city": "Reston",
            "state": "VA",
            "postal_code": "20190",
            "country": "us",
        }
    ],
    "website": "alphafed.example.com",
    "phone": "+1 703 555 0100",
    "bid_inbox_email": "Bids@AlphaFed.example.com",
    "year_founded": 2009,
    "legal_structure": "llc",
    "uei": "abc123def456",
    "cage_code": "1ab23",
    "sam_status": "active",
    "sam_expires_on": "2027-03-31",
    "ein": "123456789",
    "bank_name": "First Bank",
    "bank_account_number": "000123456789",
    "bank_routing_code": "021000021",
}

IN_BODY = {
    "region": "in",
    "legal_name": "Alpha Infotech Pvt Ltd",
    "legal_structure": "pvt_ltd",
    "pan": "abcde1234f",
    "gstin": "27abcde1234f1z5",
    "tan": "blra12345a",
    "cin_llpin": "U72900KA2010PTC012345",
    "udyam_number": "UDYAM-KA-03-0012345",
    "udyam_category": "small",
    "dpiit_number": "DIPP12345",
    "gem_seller_id": "GEM-SELLER-1",
    "local_supplier_class": "class_1",
    "local_content_pct": "62.50",
}


async def _tenant(database: Database, **overrides):  # type: ignore[no-untyped-def]
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(session, **overrides)
        return tenant.id, user.id


async def test_create_us_profile_masks_and_encrypts(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tid, uid = await _tenant(database)
    headers = auth_headers(user_id=uid, tenant_id=tid, role=Role.TENANT_OWNER)
    r = await api_client.post("/api/v1/profiles", json=US_BODY, headers=headers)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["tenant_id"] == str(tid) and body["region"] == "us"
    assert body["uei"] == "ABC123DEF456" and body["cage_code"] == "1AB23"
    assert body["sam_expires_on"] == "2027-03-31" and body["sam_status"] == "active"
    assert body["website"] == "https://alphafed.example.com"
    assert body["bid_inbox_email"] == "bids@alphafed.example.com"
    assert body["addresses"][0]["country"] == "US" and body["addresses"][0]["kind"] == "hq"
    # masked to the last 4 characters
    assert body["ein"] == "•••••6789"
    assert body["bank_account_number"] == "•••••6789"
    assert body["bank_routing_code"] == "•••••0021"
    assert body["pan"] is None and body["version"] == 1
    for secret in ("12-3456789", "123456789", "000123456789", "021000021"):
        assert secret not in r.text

    pid = uuid.UUID(body["id"])
    async with database.owner_session() as session:
        raw = (
            await session.execute(
                text(
                    "SELECT ein, bank_account_number, bank_routing_code, pan "
                    "FROM company_profiles WHERE id = :id"
                ),
                {"id": pid},
            )
        ).one()
        row = await session.get(CompanyProfile, pid)
    assert is_encrypted(raw.ein) and is_encrypted(raw.bank_account_number)
    assert raw.pan is None
    assert "3456789" not in raw.ein
    assert get_field_cipher().decrypt(raw.ein) == "12-3456789"
    assert row is not None and row.ein == "12-3456789"  # ORM sees plaintext
    assert row.sam_expires_on.isoformat() == "2027-03-31"
    assert row.dba_names == ["Alpha Fed"]

    async with database.owner_session() as session:
        audits = (
            (await session.execute(select(AuditLog).where(AuditLog.action == "profile.create")))
            .scalars()
            .all()
        )
    assert len(audits) == 1 and audits[0].object_id == str(pid)
    assert "3456789" not in str(audits[0].meta)


async def test_create_in_profile(api_client: httpx.AsyncClient, database: Database) -> None:
    tid, uid = await _tenant(database, region=Region.IN, data_residency=Region.IN)
    headers = auth_headers(user_id=uid, tenant_id=tid, role=Role.BID_MANAGER)
    r = await api_client.post("/api/v1/profiles", json=IN_BODY, headers=headers)
    assert r.status_code == 201, r.text
    body = r.json()
    assert (
        body["pan"] == "•••••234F" and body["gstin"] == "•••••F1Z5" and body["tan"] == "•••••345A"
    )
    assert body["cin_llpin"] == "U72900KA2010PTC012345"
    assert body["udyam_category"] == "small" and body["local_supplier_class"] == "class_1"
    assert body["local_content_pct"] == "62.50"
    assert body["uei"] is None and body["ein"] is None
    async with database.owner_session() as session:
        raw = (
            await session.execute(
                text("SELECT pan, gstin, tan FROM company_profiles WHERE id = :id"),
                {"id": uuid.UUID(body["id"])},
            )
        ).one()
    assert all(is_encrypted(v) for v in raw)
    assert get_field_cipher().decrypt(raw.gstin) == "27ABCDE1234F1Z5"


@pytest.mark.parametrize(
    ("region", "body", "foreign"),
    [
        ("us", {**US_BODY, "pan": "ABCDE1234F", "gstin": "27ABCDE1234F1Z5"}, ["gstin", "pan"]),
        ("in", {**IN_BODY, "uei": "ABC123DEF456", "ein": "12-3456789"}, ["ein", "uei"]),
        (
            "in",
            {**IN_BODY, "sam_expires_on": "2027-01-01", "cage_code": "1AB23"},
            ["cage_code", "sam_expires_on"],
        ),
        ("us", {**US_BODY, "udyam_category": "micro"}, ["udyam_category"]),
    ],
)
async def test_region_foreign_fields_are_422(
    api_client: httpx.AsyncClient, database: Database, region: str, body: dict, foreign: list
) -> None:
    tid, uid = await _tenant(database, region=Region(region), data_residency=Region(region))
    headers = auth_headers(user_id=uid, tenant_id=tid)
    r = await api_client.post("/api/v1/profiles", json=body, headers=headers)
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "region_mismatch" and detail["region"] == region
    assert detail["fields"] == foreign
    for name in foreign:
        assert name in detail["message"]
    async with database.owner_session() as session:
        assert (await session.execute(select(CompanyProfile))).scalars().all() == []


async def test_region_gating_on_update_uses_stored_region(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tid, uid = await _tenant(database)
    headers = auth_headers(user_id=uid, tenant_id=tid)
    pid = (await api_client.post("/api/v1/profiles", json=US_BODY, headers=headers)).json()["id"]
    r = await api_client.put(f"/api/v1/profiles/{pid}", json={"tan": "BLRA12345A"}, headers=headers)
    assert r.status_code == 422 and r.json()["detail"]["fields"] == ["tan"]
    # explicit nulls for foreign fields are also refused (they are not part of this region)
    r = await api_client.put(f"/api/v1/profiles/{pid}", json={"pan": None}, headers=headers)
    assert r.status_code == 422
    # region itself is not updatable
    r = await api_client.put(f"/api/v1/profiles/{pid}", json={"region": "in"}, headers=headers)
    assert r.status_code == 422


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("uei", "ABC123DEF45"),
        ("uei", "ABC123DEF4567"),
        ("uei", "ABC123DEF45!"),
        ("cage_code", "1AB2"),
        ("cage_code", "1AB234"),
        ("ein", "12-345678"),
        ("sam_expires_on", "2027-13-01"),
        ("sam_expires_on", "soon"),
        ("year_founded", 1700),
        ("legal_structure", "sole"),
        ("bid_inbox_email", "not-an-email"),
        ("addresses", [{"kind": "hq", "line1": "x", "city": "y", "country": "USA"}]),
        ("addresses", [{"kind": "office", "line1": "x", "city": "y", "country": "US"}]),
        ("unknown_field", "x"),
    ],
)
async def test_identifier_and_field_validation(
    api_client: httpx.AsyncClient, database: Database, field: str, value: object
) -> None:
    tid, uid = await _tenant(database)
    headers = auth_headers(user_id=uid, tenant_id=tid)
    r = await api_client.post("/api/v1/profiles", json={**US_BODY, field: value}, headers=headers)
    assert r.status_code == 422, r.text
    assert field in r.text or field == "unknown_field"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("pan", "ABCD1234F", "AAAAA9999A"),
        ("gstin", "27ABCDE1234F1Y5", "GSTIN"),
        ("tan", "BLR12345A", "AAAA99999A"),
    ],
)
async def test_indian_identifier_validation(
    api_client: httpx.AsyncClient, database: Database, field: str, value: str, message: str
) -> None:
    tid, uid = await _tenant(database, region=Region.IN, data_residency=Region.IN)
    headers = auth_headers(user_id=uid, tenant_id=tid)
    r = await api_client.post("/api/v1/profiles", json={**IN_BODY, field: value}, headers=headers)
    assert r.status_code == 422 and message in r.text


async def test_update_masked_value_is_a_noop_and_plaintext_rewrites(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tid, uid = await _tenant(database)
    headers = auth_headers(user_id=uid, tenant_id=tid)
    created = (await api_client.post("/api/v1/profiles", json=US_BODY, headers=headers)).json()
    pid = created["id"]
    async with database.owner_session() as session:
        before = (
            await session.execute(
                text("SELECT ein FROM company_profiles WHERE id = :id"), {"id": uuid.UUID(pid)}
            )
        ).scalar_one()

    # echo the masked EIN back together with a real change
    r = await api_client.put(
        f"/api/v1/profiles/{pid}",
        json={
            "ein": created["ein"],
            "bank_account_number": "•••••6789",
            "phone": "+1 703 555 0199",
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["ein"] == "•••••6789" and r.json()["phone"] == "+1 703 555 0199"
    assert r.json()["version"] == 2
    async with database.owner_session() as session:
        after = (
            await session.execute(
                text("SELECT ein FROM company_profiles WHERE id = :id"), {"id": uuid.UUID(pid)}
            )
        ).scalar_one()
        audit = (
            (await session.execute(select(AuditLog).where(AuditLog.action == "profile.update")))
            .scalars()
            .one()
        )
    assert after == before, "masked value must not re-encrypt or change the stored EIN"
    assert audit.meta["fields"] == ["phone"]

    # a masked-only body changes nothing (version stays)
    r = await api_client.put(f"/api/v1/profiles/{pid}", json={"ein": "•••••6789"}, headers=headers)
    assert r.status_code == 200 and r.json()["version"] == 2

    # real plaintext replaces the secret; null clears it
    r = await api_client.put(f"/api/v1/profiles/{pid}", json={"ein": "98-7654321"}, headers=headers)
    assert r.status_code == 200 and r.json()["ein"] == "•••••4321" and r.json()["version"] == 3
    r = await api_client.put(f"/api/v1/profiles/{pid}", json={"ein": None}, headers=headers)
    assert r.status_code == 200 and r.json()["ein"] is None
    r = await api_client.put(f"/api/v1/profiles/{pid}", json={"legal_name": None}, headers=headers)
    assert r.status_code == 422


async def test_read_roles_and_write_roles(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tid, uid = await _tenant(database)
    owner = auth_headers(user_id=uid, tenant_id=tid)
    pid = (await api_client.post("/api/v1/profiles", json=US_BODY, headers=owner)).json()["id"]
    for role in (Role.VIEWER, Role.REVIEWER, Role.WRITER):
        headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tid, role=role)
        assert (await api_client.get(f"/api/v1/profiles/{pid}", headers=headers)).status_code == 200
        r = await api_client.put(f"/api/v1/profiles/{pid}", json={"phone": "x"}, headers=headers)
        assert r.status_code == 403, role
        r = await api_client.post("/api/v1/profiles", json=US_BODY, headers=headers)
        assert r.status_code == 403, role
    admin = auth_headers(user_id=uuid.uuid4(), tenant_id=tid, role=Role.PLATFORM_ADMIN)
    assert (await api_client.get(f"/api/v1/profiles/{pid}", headers=admin)).status_code == 403
    other_tid, other_uid = await _tenant(database)
    stranger = auth_headers(user_id=other_uid, tenant_id=other_tid)
    assert (await api_client.get(f"/api/v1/profiles/{pid}", headers=stranger)).status_code == 404
    r = await api_client.put(f"/api/v1/profiles/{pid}", json={"phone": "x"}, headers=stranger)
    assert r.status_code == 404
    assert (
        await api_client.get(f"/api/v1/profiles/{uuid.uuid4()}", headers=owner)
    ).status_code == 404


async def test_profile_creation_is_plan_limited(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tid, uid = await _tenant(database, plan=Plan.FREE)
    headers = auth_headers(user_id=uid, tenant_id=tid)
    assert (
        await api_client.post("/api/v1/profiles", json=US_BODY, headers=headers)
    ).status_code == 201
    r = await api_client.post("/api/v1/profiles", json=US_BODY, headers=headers)
    assert r.status_code == 402, r.text
    detail = r.json()["detail"]
    assert detail == {
        "error": "plan_limit_exceeded",
        "limit": "profiles",
        "limit_value": 1,
        "used": 1,
        "requested": 1,
        "plan": "free",
    }
    pro_tid, pro_uid = await _tenant(database, plan=Plan.PRO)
    pro = auth_headers(user_id=pro_uid, tenant_id=pro_tid)
    for _ in range(3):
        assert (
            await api_client.post("/api/v1/profiles", json=US_BODY, headers=pro)
        ).status_code == 201
    assert (await api_client.post("/api/v1/profiles", json=US_BODY, headers=pro)).status_code == 402
    internal_tid, internal_uid = await _tenant(database, plan=Plan.FREE, is_internal=True)
    internal = auth_headers(user_id=internal_uid, tenant_id=internal_tid)
    for _ in range(3):
        assert (
            await api_client.post("/api/v1/profiles", json=US_BODY, headers=internal)
        ).status_code == 201
