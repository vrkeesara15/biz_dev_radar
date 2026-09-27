"""M1-08: GET /profiles/{id} carries completeness computed from the stored profile."""

from __future__ import annotations

import httpx
from app.core.config import Region
from app.core.db import Database
from app.core.profile_completeness import SECTION_WEIGHTS

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner

PDF = b"%PDF-1.4\n%%EOF\n"


async def _profile(api_client: httpx.AsyncClient, database: Database, region: str = "us"):  # type: ignore[no-untyped-def]
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(
            session, region=Region(region), data_residency=Region(region)
        )
        tid, uid = tenant.id, user.id
    headers = auth_headers(user_id=uid, tenant_id=tid)
    r = await api_client.post(
        "/api/v1/profiles", json={"region": region, "legal_name": "Complete Co"}, headers=headers
    )
    assert r.status_code == 201, r.text
    return headers, r.json()


async def test_completeness_grows_with_the_profile(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    headers, created = await _profile(api_client, database)
    pid = created["id"]
    url = f"/api/v1/profiles/{pid}"
    c0 = created["completeness"]
    assert set(c0["sections"]) == set(SECTION_WEIGHTS)
    assert c0["sections"]["identity"]["weight"] == 15 and c0["sections"]["identity"]["score"] == 3
    assert 0 < c0["score"] < 40 and not c0["matching_enabled"] and not c0["drafting_enabled"]
    assert "identity.uei" not in c0["missing"] and "registrations.uei" in c0["missing"]
    assert "registrations.pan" not in c0["missing"]  # IN-only item absent for a US profile

    # identity + registrations + finance + geography + preferences
    r = await api_client.put(
        url,
        json={
            "addresses": [{"kind": "hq", "line1": "1 Main", "city": "Reston", "country": "US"}],
            "website": "https://complete.example.com",
            "phone": "+1 703 555 0100",
            "bid_inbox_email": "bids@complete.example.com",
            "year_founded": 2010,
            "legal_structure": "llc",
            "uei": "ABC123DEF456",
            "cage_code": "1AB23",
            "sam_status": "active",
            "sam_expires_on": "2027-01-01",
            "employee_count_total": 80,
            "annual_revenue": [
                {"fiscal_year": y, "amount": "5000000", "currency": "USD"}
                for y in (2023, 2024, 2025)
            ],
            "bonding_capacity_amount": "1000000",
            "bonding_capacity_currency": "USD",
            "audited_fiscal_years": [2025],
            "target_us_states": ["VA"],
            "value_min_usd": "100000",
            "value_max_usd": "5000000",
            "notice_types_wanted": ["rfp"],
            "target_buyers": ["VA"],
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
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    c1 = r.json()["completeness"]
    for section in ("identity", "registrations", "size_finance", "where_how_big"):
        assert c1["sections"][section]["score"] == SECTION_WEIGHTS[section], section
    assert c1["matching_enabled"] and not c1["drafting_enabled"]
    assert c1["score"] >= 40

    # what we sell + proof
    for code in ("541511", "541512", "541519"):
        assert (
            await api_client.post(
                f"{url}/codes",
                json={"scheme": "naics", "code": code, "is_primary": code == "541511"},
                headers=headers,
            )
        ).status_code == 201
    for term in ("cloud", "data", "devsecops", "zero trust", "migration"):
        assert (
            await api_client.post(
                f"{url}/keywords", json={"kind": "include", "term": term}, headers=headers
            )
        ).status_code == 201
    assert (
        await api_client.post(
            f"{url}/keywords", json={"kind": "exclude", "term": "janitorial"}, headers=headers
        )
    ).status_code == 201
    for name in ("Cloud", "Data", "Security"):
        assert (
            await api_client.post(
                f"{url}/service-lines", json={"name": name, "description": "d"}, headers=headers
            )
        ).status_code == 201
    cap = await api_client.post(
        "/api/v1/files", files={"file": ("cap.pdf", PDF, "application/pdf")}, headers=headers
    )
    assert (
        await api_client.post(
            f"{url}/files",
            json={"file_id": cap.json()["id"], "kind": "capability_statement"},
            headers=headers,
        )
    ).status_code == 201
    for i in range(2):
        assert (
            await api_client.post(
                f"{url}/past-performance",
                json={"title": f"PP {i}", "customer": "VA", "role": "prime", "scope": "s"},
                headers=headers,
            )
        ).status_code == 201
    c2 = (await api_client.get(url, headers=headers)).json()["completeness"]
    assert c2["sections"]["what_we_sell"]["score"] == 20
    assert c2["score"] >= 70 and not c2["drafting_enabled"], "drafting needs 3 past performances"
    assert c2["missing"] and all(m.startswith(("proof.", "preferences.")) for m in c2["missing"])

    assert (
        await api_client.post(
            f"{url}/past-performance",
            json={"title": "PP 3", "customer": "VA", "role": "sub", "scope": "s"},
            headers=headers,
        )
    ).status_code == 201
    c3 = (await api_client.get(url, headers=headers)).json()["completeness"]
    assert c3["drafting_enabled"] and c3["matching_enabled"] and c3["score"] > c2["score"]

    # fill the rest -> 100
    for i in range(2):
        assert (
            await api_client.post(
                f"{url}/past-performance",
                json={"title": f"PP {i + 4}", "customer": "VA", "role": "sub", "scope": "s"},
                headers=headers,
            )
        ).status_code == 201
        assert (
            await api_client.post(
                f"{url}/personnel", json={"name": f"P{i}", "role": "PM"}, headers=headers
            )
        ).status_code == 201
    for kind in ("company_overview", "qa_plan", "transition_plan"):
        assert (
            await api_client.post(
                f"{url}/boilerplate",
                json={"kind": kind, "title": kind, "body": "b"},
                headers=headers,
            )
        ).status_code == 201
    assert (
        await api_client.post(f"{url}/certifications", json={"kind": "8a"}, headers=headers)
    ).status_code == 201
    assert (
        await api_client.post(
            f"{url}/rate-card",
            json={
                "labor_category": "PM",
                "unit": "hour",
                "rate_amount": "150",
                "rate_currency": "USD",
            },
            headers=headers,
        )
    ).status_code == 201
    assert (
        await api_client.get("/api/v1/me/notification-prefs", headers=headers)
    ).status_code == 200
    c4 = (await api_client.get(url, headers=headers)).json()["completeness"]
    assert c4 == {
        "score": 100,
        "matching_enabled": True,
        "drafting_enabled": True,
        "missing": [],
        "sections": {
            name: {"score": w, "weight": w, "missing": []} for name, w in SECTION_WEIGHTS.items()
        },
    }


async def test_indian_profile_uses_indian_items(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    headers, created = await _profile(api_client, database, region="in")
    missing = created["completeness"]["missing"]
    assert "registrations.pan" in missing and "registrations.gstin" in missing
    assert "registrations.uei" not in missing and "size_finance.net_worth" in missing
    assert "what_we_sell.primary_india_category" in missing
    r = await api_client.put(
        f"/api/v1/profiles/{created['id']}",
        json={
            "pan": "ABCDE1234F",
            "gstin": "27ABCDE1234F1Z5",
            "cin_llpin": "U7",
            "udyam_number": "UDYAM-1",
            "gem_seller_id": "GEM-1",
        },
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["completeness"]["sections"]["registrations"]["score"] == 10
