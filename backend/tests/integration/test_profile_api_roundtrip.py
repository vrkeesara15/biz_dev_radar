"""M1-09: full profile round trip through the API and the role matrix over every route."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from app.core.config import Region
from app.core.db import Database
from app.core.profile_fields import ENCRYPTED_FIELDS
from app.core.roles import Role

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner

PDF = b"%PDF-1.4\n%%EOF\n"

US_PROFILE: dict[str, Any] = {
    "region": "us",
    "legal_name": "Alpha Federal LLC",
    "dba_names": ["Alpha Fed"],
    "addresses": [
        {
            "kind": "registered",
            "line1": "1 Main St",
            "line2": None,
            "city": "Reston",
            "state": "VA",
            "postal_code": "20190",
            "country": "US",
        },
        {
            "kind": "branch",
            "line1": "9 Elm St",
            "line2": "Suite 4",
            "city": "Austin",
            "state": "TX",
            "postal_code": "78701",
            "country": "US",
        },
    ],
    "website": "https://alphafed.example.com",
    "phone": "+1 703 555 0100",
    "bid_inbox_email": "bids@alphafed.example.com",
    "year_founded": 2009,
    "legal_structure": "llc",
    "is_active": True,
    "uei": "ABC123DEF456",
    "cage_code": "1AB23",
    "sam_status": "active",
    "sam_expires_on": "2027-03-31",
    "ein": "12-3456789",
    "employee_count_total": 160,
    "employees_by_country": {"US": 40, "IN": 120},
    "annual_revenue": [
        {"fiscal_year": 2023, "amount": "1000000.00", "currency": "USD"},
        {"fiscal_year": 2024, "amount": "1300000.00", "currency": "USD"},
        {"fiscal_year": 2025, "amount": "1600000.00", "currency": "USD"},
    ],
    "audited_fiscal_years": [2023, 2024, 2025],
    "bonding_capacity_amount": "5000000.00",
    "bonding_capacity_currency": "USD",
    "target_countries": ["US"],
    "target_us_states": ["VA", "MD", "DC"],
    "target_cities": ["Reston", "Austin"],
    "remote_ok": True,
    "target_buyers": ["Department of Veterans Affairs"],
    "blocked_buyers": ["Department of Defense"],
    "value_min_usd": "100000.00",
    "value_max_usd": "5000000.00",
    "notice_types_wanted": ["rfp", "rfq", "sources_sought"],
    "contract_types_preferred": ["ffp", "tm"],
    "teaming_roles": ["prime", "sub"],
    "cleared_personnel_count": 12,
    "scoring_weights": {
        "code_match": 30,
        "semantic_similarity": 20,
        "keyword_match": 10,
        "eligibility": 15,
        "value_fit": 5,
        "geography": 5,
        "buyer_affinity": 5,
        "past_performance_relevance": 10,
    },
    "bid_no_bid_weights": {
        "fit": 30,
        "eligibility": 20,
        "capacity": 15,
        "competition": 15,
        "value": 5,
        "win_probability": 15,
    },
    "required_approver_roles": ["tenant_owner", "bid_manager"],
    "output_languages": ["en"],
    "bank_name": "First Bank",
    "bank_account_number": "000123456789",
    "bank_routing_code": "021000021",
}

IN_PROFILE: dict[str, Any] = {
    "region": "in",
    "legal_name": "Alpha Infotech Pvt Ltd",
    "legal_structure": "pvt_ltd",
    "pan": "ABCDE1234F",
    "gstin": "27ABCDE1234F1Z5",
    "tan": "BLRA12345A",
    "cin_llpin": "U72900KA2010PTC012345",
    "udyam_number": "UDYAM-KA-03-0012345",
    "udyam_category": "small",
    "dpiit_number": "DIPP12345",
    "gem_seller_id": "GEM-SELLER-1",
    "local_supplier_class": "class_1",
    "local_content_pct": "62.50",
    "net_worth_amount": "25000000.00",
    "net_worth_currency": "INR",
    "solvency_certificate_available": True,
    "mse_ownership": "women",
    "annual_revenue": [
        {"fiscal_year": 2024, "amount": "50000000.00", "currency": "INR"},
        {"fiscal_year": 2025, "amount": "70000000.00", "currency": "INR"},
    ],
    "target_in_states": ["KA", "TS"],
    "value_min_inr": "1000000.00",
    "value_max_inr": "250000000.00",
    "notice_types_wanted": ["gem_bid", "eoi", "reverse_auction"],
    "contract_types_preferred": ["rate_contract"],
    "output_languages": ["en", "hi"],
}

MASKED = set(ENCRYPTED_FIELDS)


async def _tenant(api_client: httpx.AsyncClient, database: Database, region: str):  # type: ignore[no-untyped-def]
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(
            session, region=Region(region), data_residency=Region(region)
        )
        return tenant.id, auth_headers(user_id=user.id, tenant_id=tenant.id)


@pytest.mark.parametrize("body", [US_PROFILE, IN_PROFILE], ids=["us", "in"])
async def test_full_profile_round_trip(
    api_client: httpx.AsyncClient, database: Database, body: dict[str, Any]
) -> None:
    _, headers = await _tenant(api_client, database, body["region"])
    created = await api_client.post("/api/v1/profiles", json=body, headers=headers)
    assert created.status_code == 201, created.text
    pid = created.json()["id"]
    read = await api_client.get(f"/api/v1/profiles/{pid}", headers=headers)
    assert read.status_code == 200
    out = read.json()
    assert out == created.json()
    for key, value in body.items():
        if key in MASKED:
            assert out[key] == "•" * 5 + value[-4:], key
            assert value not in read.text
        else:
            assert out[key] == value, key
    # writing the response back (masked fields included) changes nothing
    writable = {k: v for k, v in out.items() if k in body and k != "region"}
    again = await api_client.put(f"/api/v1/profiles/{pid}", json=writable, headers=headers)
    assert again.status_code == 200, again.text
    assert {k: again.json()[k] for k in body if k != "region"} == {
        k: out[k] for k in body if k != "region"
    }
    assert again.json()["version"] == out["version"]  # masked echo + identical values = no-op
    listed = await api_client.get("/api/v1/profiles", headers=headers)
    assert listed.status_code == 200 and [p["id"] for p in listed.json()] == [pid]


async def test_sub_resources_round_trip_and_list(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    _, headers = await _tenant(api_client, database, "us")
    pid = (await api_client.post("/api/v1/profiles", json=US_PROFILE, headers=headers)).json()["id"]
    base = f"/api/v1/profiles/{pid}"
    upload = await api_client.post(
        "/api/v1/files", files={"file": ("cap.pdf", PDF, "application/pdf")}, headers=headers
    )
    file_id = upload.json()["id"]
    payloads: dict[str, dict[str, Any]] = {
        "codes": {"scheme": "naics", "code": "541511", "is_primary": True},
        "keywords": {"kind": "include", "term": "cloud migration", "weight": "2.5"},
        "service-lines": {
            "name": "Cloud",
            "description": "Cloud migration",
            "differentiators": ["FedRAMP"],
            "tools": ["AWS"],
            "delivery_model": "hybrid",
        },
        "past-performance": {
            "title": "PP",
            "customer": "VA",
            "role": "prime",
            "scope": "s",
            "cpars_rating": "exceptional",
            "is_public": True,
        },
        "personnel": {
            "name": "Priya",
            "role": "PM",
            "years_experience": 10,
            "clearances": ["Secret"],
            "certifications": ["PMP"],
            "resume_file_id": file_id,
            "is_key_personnel": True,
        },
        "certifications": {"kind": "8a", "cert_number": "8A-1", "expires_on": "2030-01-01"},
        "registrations": {"kind": "sam", "identifier": "ABC123DEF456", "expires_on": "2027-03-31"},
        "vehicles": {"vehicle": "GSA MAS", "number": "47Q", "expires_on": "2029-09-30"},
        "insurance": {
            "kind": "cyber",
            "carrier": "Hartford",
            "limit_amount": "1000000.00",
            "limit_currency": "USD",
        },
        "boilerplate": {
            "kind": "company_overview",
            "title": "Overview",
            "body": "<p>We</p>",
            "body_format": "html",
        },
        "files": {
            "file_id": file_id,
            "kind": "capability_statement",
            "title": "Cap",
            "meta": {"year": 2026},
        },
        "teaming-partners": {
            "name": "Beta",
            "relationship": "sub",
            "uei": "BETA12345678",
            "capabilities": ["GIS"],
        },
        "rate-card": {
            "labor_category": "PM",
            "unit": "hour",
            "rate_amount": "150.00",
            "rate_currency": "USD",
        },
    }
    for name, payload in payloads.items():
        created = await api_client.post(f"{base}/{name}", json=payload, headers=headers)
        assert created.status_code == 201, (name, created.text)
        item = created.json()
        for key, value in payload.items():
            assert item[key] == value, (name, key)
        assert item["profile_id"] == pid
        read = await api_client.get(f"{base}/{name}/{item['id']}", headers=headers)
        assert read.status_code == 200 and read.json() == item, name
        listed = await api_client.get(f"{base}/{name}", headers=headers)
        assert listed.status_code == 200 and listed.json() == [item], name
    profile = (await api_client.get(base, headers=headers)).json()
    assert profile["version"] == 1 + len(payloads)
    assert profile["size_status_by_naics"]["541511"]["status"] == "small"
    assert profile["completeness"]["matching_enabled"]


def _profile_routes(app) -> list[tuple[str, str]]:  # type: ignore[no-untyped-def]
    spec = app.openapi()
    return sorted(
        (m.upper(), path)
        for path, ops in spec["paths"].items()
        for m in ops
        if path.startswith("/api/v1/profiles") and m.upper() in {"GET", "POST", "PUT", "DELETE"}
    )


WRITER_RESOURCES = ("past-performance", "personnel")


async def test_role_matrix_over_every_profile_route(
    api_client: httpx.AsyncClient,
    database: Database,
    app,  # type: ignore[no-untyped-def]
) -> None:
    """Viewer/reviewer read everything and get 403 on every write; writer additionally
    writes past performance and personnel; bid_manager and owner write everything."""
    tid, owner = await _tenant(api_client, database, "us")
    pid = (
        await api_client.post(
            "/api/v1/profiles", json={"region": "us", "legal_name": "RBAC Co"}, headers=owner
        )
    ).json()["id"]
    routes = _profile_routes(app)
    assert len(routes) >= 60
    seen_resources = set()
    for method, path in routes:
        if method == "GET":
            continue
        concrete = path.replace("{profile_id}", pid).replace("{item_id}", str(uuid.uuid4()))
        parts = path.split("/")  # ['', 'api', 'v1', 'profiles', '{profile_id}', <resource>, ...]
        resource = parts[5] if len(parts) > 5 else "profile"
        seen_resources.add(resource)
        for role in (Role.VIEWER, Role.REVIEWER):
            r = await api_client.request(
                method,
                concrete,
                json={},
                headers=auth_headers(user_id=uuid.uuid4(), tenant_id=tid, role=role),
            )
            assert r.status_code == 403, (role, method, path, r.status_code)
        writer = await api_client.request(
            method,
            concrete,
            json={},
            headers=auth_headers(user_id=uuid.uuid4(), tenant_id=tid, role=Role.WRITER),
        )
        if resource in WRITER_RESOURCES:
            assert writer.status_code != 403, ("writer must reach", method, path)
        else:
            assert writer.status_code == 403, (
                "writer must not write",
                method,
                path,
                writer.status_code,
            )
        for role in (Role.BID_MANAGER, Role.TENANT_OWNER):
            r = await api_client.request(
                method,
                concrete,
                json={},
                headers=auth_headers(user_id=uuid.uuid4(), tenant_id=tid, role=role),
            )
            assert r.status_code != 403, (role, method, path)
        admin = await api_client.request(
            method,
            concrete,
            json={},
            headers=auth_headers(user_id=uuid.uuid4(), tenant_id=tid, role=Role.PLATFORM_ADMIN),
        )
        assert admin.status_code == 403, ("platform_admin", method, path)
    assert {
        "profile",
        "codes",
        "keywords",
        "service-lines",
        "past-performance",
        "personnel",
        "certifications",
        "registrations",
        "files",
        "boilerplate",
        "teaming-partners",
        "vehicles",
        "insurance",
        "rate-card",
    } <= seen_resources
    for method, path in routes:
        if method != "GET":
            continue
        concrete = path.replace("{profile_id}", pid).replace("{item_id}", str(uuid.uuid4()))
        for role in (Role.VIEWER, Role.REVIEWER, Role.WRITER, Role.BID_MANAGER, Role.TENANT_OWNER):
            r = await api_client.get(
                concrete, headers=auth_headers(user_id=uuid.uuid4(), tenant_id=tid, role=role)
            )
            assert r.status_code in (200, 404), (role, path, r.status_code)
        assert (
            await api_client.get(
                concrete,
                headers=auth_headers(user_id=uuid.uuid4(), tenant_id=tid, role=Role.PLATFORM_ADMIN),
            )
        ).status_code == 403
