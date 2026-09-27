"""M1-05: past performance, personnel, registrations, vehicles, security attestations,
insurance, boilerplate, profile files, rate card."""

from __future__ import annotations

import uuid

import httpx
import pytest
from app.core.config import Region
from app.core.db import Database
from app.core.roles import Role
from app.models import PastPerformance, ProfileFile
from sqlalchemy import select

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
        "/api/v1/profiles", json={"region": region, "legal_name": "Proof Co"}, headers=headers
    )
    assert r.status_code == 201, r.text
    return tid, headers, r.json()["id"]


async def _upload(
    api_client: httpx.AsyncClient, headers: dict[str, str], name: str = "cap.pdf"
) -> str:
    r = await api_client.post(
        "/api/v1/files", files={"file": (name, PDF, "application/pdf")}, headers=headers
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


PP = {
    "title": "Enterprise Data Platform Modernization",
    "customer": "Department of Veterans Affairs",
    "customer_anonymized": True,
    "agency_type": "federal",
    "value_amount": "12500000",
    "value_currency": "USD",
    "period_start": "2022-01-01",
    "period_end": "2024-12-31",
    "role": "prime",
    "naics": "541512",
    "contract_number": "36C10B22C0001",
    "scope": "Migrated 40 legacy systems to a cloud data platform.",
    "outcomes": "Cut reporting latency by 70%; saved $3.2M per year.",
    "technologies": ["AWS", "Databricks", "aws"],
    "reference_contact": {
        "name": "Jane Doe",
        "title": "COR",
        "email": "jane@va.example.gov",
        "phone": "+1 202 555 0100",
    },
    "cpars_rating": "very_good",
    "is_public": False,
}


async def test_past_performance_every_column_and_writer_access(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tid, owner, pid = await _profile(api_client, database)
    url = f"/api/v1/profiles/{pid}/past-performance"
    writer = auth_headers(user_id=uuid.uuid4(), tenant_id=tid, role=Role.WRITER)
    r = await api_client.post(url, json=PP, headers=writer)
    assert r.status_code == 201, r.text
    pp = r.json()
    for key, value in PP.items():
        if key == "technologies":
            assert pp[key] == ["AWS", "Databricks"]
        elif key == "value_amount":
            assert pp[key] == "12500000.00"
        else:
            assert pp[key] == value, key
    assert pp["cpars_rating"] == "very_good" and pp["is_public"] is False
    r = await api_client.put(
        f"{url}/{pp['id']}", json={"is_public": True, "cpars_rating": "exceptional"}, headers=writer
    )
    assert (
        r.status_code == 200
        and r.json()["is_public"] is True
        and r.json()["cpars_rating"] == "exceptional"
    )
    assert r.json()["reference_contact"]["name"] == "Jane Doe"
    for bad in (
        {**PP, "naics": "999999"},
        {**PP, "period_start": "2025-01-01", "period_end": "2024-01-01"},
        {**PP, "value_currency": None},
        {**PP, "role": "partner"},
        {**PP, "cpars_rating": "great"},
        {**PP, "agency_type": "galactic"},
        {**PP, "reference_contact": {"linkedin": "x"}},
        {k: v for k, v in PP.items() if k != "scope"},
    ):
        assert (await api_client.post(url, json=bad, headers=owner)).status_code == 422, bad
    assert (
        await api_client.put(
            f"{url}/{pp['id']}", json={"period_start": "2030-01-01"}, headers=owner
        )
    ).status_code == 422
    assert (
        await api_client.put(f"{url}/{pp['id']}", json={"title": None}, headers=owner)
    ).status_code == 422
    for role in (Role.REVIEWER, Role.VIEWER):
        headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tid, role=role)
        assert (await api_client.get(url, headers=headers)).status_code == 200
        assert (await api_client.post(url, json=PP, headers=headers)).status_code == 403
        assert (await api_client.delete(f"{url}/{pp['id']}", headers=headers)).status_code == 403
    assert (
        await api_client.post(
            url, json={**PP, "title": "Second", "period_end": None}, headers=owner
        )
    ).status_code == 201
    listed = (await api_client.get(url, headers=owner)).json()
    assert [p["title"] for p in listed] == [PP["title"], "Second"]
    async with database.owner_session() as session:
        rows = (await session.execute(select(PastPerformance))).scalars().all()
    assert len(rows) == 2 and all(r.tenant_id == tid for r in rows)
    assert (await api_client.delete(f"{url}/{pp['id']}", headers=writer)).status_code == 204


async def test_personnel_with_resume_file(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tid, owner, pid = await _profile(api_client, database)
    writer = auth_headers(user_id=uuid.uuid4(), tenant_id=tid, role=Role.WRITER)
    resume = await _upload(api_client, writer, "resume.pdf")
    url = f"/api/v1/profiles/{pid}/personnel"
    body = {
        "name": "Priya Raman",
        "role": "Program Manager",
        "years_experience": 14,
        "clearances": ["Secret", "secret"],
        "certifications": ["PMP", "AWS Solutions Architect", "CISSP"],
        "education": "MS Computer Science, Georgia Tech",
        "resume_file_id": resume,
        "is_key_personnel": True,
    }
    r = await api_client.post(url, json=body, headers=writer)
    assert r.status_code == 201, r.text
    person = r.json()
    assert person["clearances"] == ["Secret"] and person["resume_file_id"] == resume
    assert person["certifications"] == ["PMP", "AWS Solutions Architect", "CISSP"]
    assert (
        await api_client.post(
            url, json={**body, "resume_file_id": str(uuid.uuid4())}, headers=writer
        )
    ).status_code == 422
    assert (
        await api_client.post(url, json={**body, "years_experience": 99}, headers=writer)
    ).status_code == 422
    # another tenant's file is not visible, so it cannot be attached
    _, other_owner, _ = await _profile(api_client, database)
    foreign_file = await _upload(api_client, other_owner)
    assert (
        await api_client.post(url, json={**body, "resume_file_id": foreign_file}, headers=owner)
    ).status_code == 422
    r = await api_client.put(
        f"{url}/{person['id']}",
        json={"years_experience": 15, "resume_file_id": None},
        headers=writer,
    )
    assert (
        r.status_code == 200
        and r.json()["resume_file_id"] is None
        and r.json()["years_experience"] == 15
    )
    viewer = auth_headers(user_id=uuid.uuid4(), tenant_id=tid, role=Role.VIEWER)
    assert (
        await api_client.put(f"{url}/{person['id']}", json={"role": "x"}, headers=viewer)
    ).status_code == 403


async def test_registrations_and_vehicles(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    _, us, us_pid = await _profile(api_client, database)
    _, india, in_pid = await _profile(api_client, database, region="in")
    reg_us = f"/api/v1/profiles/{us_pid}/registrations"
    reg_in = f"/api/v1/profiles/{in_pid}/registrations"
    r = await api_client.post(
        reg_us,
        json={"kind": "sam", "identifier": "ABC123DEF456", "expires_on": "2027-03-31"},
        headers=us,
    )
    assert r.status_code == 201 and r.json()["expires_on"] == "2027-03-31"
    r = await api_client.post(reg_us, json={"kind": "dsc", "holder": "x"}, headers=us)
    assert r.status_code == 422 and r.json()["detail"]["fields"] == ["kind=dsc"]
    r = await api_client.post(reg_in, json={"kind": "sam"}, headers=india)
    assert r.status_code == 422
    for kind, extra in (
        ("dsc", {"holder": "Ravi Kumar", "expires_on": "2026-11-30"}),
        ("gem", {"identifier": "GEM-SELLER-1"}),
        ("cppp", {"identifier": "CPPP-ENROL-9"}),
        ("state_portal", {"identifier": "TN-123", "portal": "tntenders.gov.in"}),
    ):
        r = await api_client.post(reg_in, json={"kind": kind, **extra}, headers=india)
        assert r.status_code == 201, (kind, r.text)
    listed = (await api_client.get(reg_in, headers=india)).json()
    assert [x["kind"] for x in listed] == ["dsc", "gem", "cppp", "state_portal"]
    assert listed[0]["holder"] == "Ravi Kumar" and listed[3]["portal"] == "tntenders.gov.in"
    # no passwords anywhere in the schema
    assert (
        await api_client.post(reg_in, json={"kind": "gem", "password": "x"}, headers=india)
    ).status_code == 422

    veh = f"/api/v1/profiles/{us_pid}/vehicles"
    r = await api_client.post(
        veh,
        json={"vehicle": "GSA MAS", "number": "47QTCA20D00XX", "expires_on": "2029-09-30"},
        headers=us,
    )
    assert r.status_code == 201, r.text
    assert (
        await api_client.post(veh, json={"vehicle": "OASIS+", "number": "OASIS-SB-1"}, headers=us)
    ).status_code == 201
    r = await api_client.put(
        f"{veh}/{r.json()['id']}", json={"expires_on": "2030-09-30"}, headers=us
    )
    assert (
        r.status_code == 200
        and r.json()["expires_on"] == "2030-09-30"
        and r.json()["vehicle"] == "GSA MAS"
    )
    assert [v["vehicle"] for v in (await api_client.get(veh, headers=us)).json()] == [
        "GSA MAS",
        "OASIS+",
    ]
    assert (await api_client.post(veh, json={"number": "x"}, headers=us)).status_code == 422


async def test_security_attestations_insurance_and_clearance_count(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    _, us, us_pid = await _profile(api_client, database)
    _, india, in_pid = await _profile(api_client, database, region="in")
    certs = f"/api/v1/profiles/{us_pid}/certifications"
    for kind, level in (
        ("fcl", "secret"),
        ("cmmc", "2"),
        ("fedramp", "moderate"),
        ("soc2", "Type II"),
        ("iso_27001", "2022"),
        ("iso_9001", None),
        ("iso_20000", None),
        ("cmmi", "3"),
    ):
        r = await api_client.post(
            certs, json={"kind": kind, "level": level, "expires_on": "2027-01-01"}, headers=us
        )
        assert r.status_code == 201, (kind, r.text)
        assert r.json()["level"] == level
    for kind in ("stqc", "cert_in"):
        assert (
            await api_client.post(
                f"/api/v1/profiles/{in_pid}/certifications", json={"kind": kind}, headers=india
            )
        ).status_code == 201
    r = await api_client.put(
        f"/api/v1/profiles/{us_pid}", json={"cleared_personnel_count": 12}, headers=us
    )
    assert r.status_code == 200 and r.json()["cleared_personnel_count"] == 12
    assert (
        await api_client.put(
            f"/api/v1/profiles/{in_pid}", json={"cleared_personnel_count": 1}, headers=india
        )
    ).status_code == 422

    ins = f"/api/v1/profiles/{us_pid}/insurance"
    r = await api_client.post(
        ins,
        json={
            "kind": "general_liability",
            "carrier": "Hartford",
            "limit_amount": "2000000",
            "limit_currency": "USD",
            "expires_on": "2026-12-31",
        },
        headers=us,
    )
    assert r.status_code == 201, r.text
    assert r.json()["limit_amount"] == "2000000.00"
    for kind in ("professional_liability", "cyber"):
        assert (
            await api_client.post(
                ins,
                json={"kind": kind, "limit_amount": "1000000", "limit_currency": "USD"},
                headers=us,
            )
        ).status_code == 201
    assert (
        await api_client.post(ins, json={"kind": "cyber", "limit_amount": "5"}, headers=us)
    ).status_code == 422
    assert (await api_client.post(ins, json={"kind": "flood"}, headers=us)).status_code == 422
    assert [i["kind"] for i in (await api_client.get(ins, headers=us)).json()] == [
        "general_liability",
        "professional_liability",
        "cyber",
    ]


async def test_boilerplate_files_and_rate_card(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tid, owner, pid = await _profile(api_client, database)
    bp = f"/api/v1/profiles/{pid}/boilerplate"
    body = "<h2>About us</h2><p>We deliver <strong>secure</strong> cloud platforms.</p>"
    r = await api_client.post(
        bp,
        json={"kind": "company_overview", "title": "Company overview", "body": body},
        headers=owner,
    )
    assert r.status_code == 201, r.text
    block = r.json()
    assert block["body"] == body and block["body_format"] == "html"
    for kind in (
        "management_approach",
        "qa_plan",
        "transition_plan",
        "security_approach",
        "diversity",
        "sustainability",
    ):
        assert (
            await api_client.post(
                bp,
                json={"kind": kind, "title": kind, "body": "x", "body_format": "markdown"},
                headers=owner,
            )
        ).status_code == 201
    assert (
        await api_client.post(
            bp, json={"kind": "company_overview", "title": "t", "body": ""}, headers=owner
        )
    ).status_code == 422
    assert (
        await api_client.post(
            bp,
            json={"kind": "company_overview", "title": "t", "body": "x", "body_format": "docx"},
            headers=owner,
        )
    ).status_code == 422
    r = await api_client.put(f"{bp}/{block['id']}", json={"body": "<p>Updated</p>"}, headers=owner)
    assert r.status_code == 200 and r.json()["body"] == "<p>Updated</p>"

    files = f"/api/v1/profiles/{pid}/files"
    cap = await _upload(api_client, owner, "capability.pdf")
    r = await api_client.post(
        files,
        json={"file_id": cap, "kind": "capability_statement", "title": "2026 Capability Statement"},
        headers=owner,
    )
    assert r.status_code == 201, r.text
    assert r.json()["file_id"] == cap and r.json()["meta"] == {}
    proposal = await _upload(api_client, owner, "proposal.pdf")
    r = await api_client.post(
        files,
        json={
            "file_id": proposal,
            "kind": "past_proposal",
            "meta": {"outcome": "won", "debrief_notes": "Strong past performance volume"},
        },
        headers=owner,
    )
    assert r.status_code == 201 and r.json()["meta"]["outcome"] == "won"
    assert (
        await api_client.post(
            files,
            json={"file_id": proposal, "kind": "past_proposal", "meta": {"outcome": "maybe"}},
            headers=owner,
        )
    ).status_code == 422
    for kind in ("brochure", "case_study", "brand", "template"):
        assert (
            await api_client.post(files, json={"file_id": cap, "kind": kind}, headers=owner)
        ).status_code == 201
    assert (
        await api_client.post(
            files, json={"file_id": str(uuid.uuid4()), "kind": "brochure"}, headers=owner
        )
    ).status_code == 422
    assert (
        await api_client.post(files, json={"file_id": cap, "kind": "logo"}, headers=owner)
    ).status_code == 422
    listed = (await api_client.get(files, headers=owner)).json()
    assert len(listed) == 6 and listed[0]["kind"] == "capability_statement"
    async with database.owner_session() as session:
        rows = (await session.execute(select(ProfileFile))).scalars().all()
    assert all(r.tenant_id == tid for r in rows)

    rates = f"/api/v1/profiles/{pid}/rate-card"
    r = await api_client.post(
        rates,
        json={
            "labor_category": "Senior Cloud Architect",
            "unit": "hour",
            "rate_amount": "185.5",
            "rate_currency": "USD",
            "min_years_experience": 10,
        },
        headers=owner,
    )
    assert r.status_code == 201, r.text
    assert r.json()["rate_amount"] == "185.50" and r.json()["unit"] == "hour"
    r = await api_client.post(
        rates,
        json={
            "labor_category": "Data Engineer",
            "unit": "month",
            "rate_amount": "450000",
            "rate_currency": "INR",
        },
        headers=owner,
    )
    assert r.status_code == 201 and r.json()["rate_currency"] == "INR"
    assert (
        await api_client.post(
            rates,
            json={
                "labor_category": "x",
                "unit": "week",
                "rate_amount": "1",
                "rate_currency": "USD",
            },
            headers=owner,
        )
    ).status_code == 422
    assert (
        await api_client.post(
            rates,
            json={
                "labor_category": "x",
                "unit": "hour",
                "rate_amount": "1",
                "rate_currency": "EUR",
            },
            headers=owner,
        )
    ).status_code == 422
    assert (
        await api_client.post(
            rates,
            json={
                "labor_category": "x",
                "unit": "hour",
                "rate_amount": "-1",
                "rate_currency": "USD",
            },
            headers=owner,
        )
    ).status_code == 422
    writer = auth_headers(user_id=uuid.uuid4(), tenant_id=tid, role=Role.WRITER)
    for url, payload in (
        (bp, {"kind": "qa_plan", "title": "t", "body": "b"}),
        (files, {"file_id": cap, "kind": "brochure"}),
        (
            rates,
            {"labor_category": "x", "unit": "hour", "rate_amount": "1", "rate_currency": "USD"},
        ),
    ):
        assert (await api_client.get(url, headers=writer)).status_code == 200
        assert (await api_client.post(url, json=payload, headers=writer)).status_code == 403


@pytest.mark.parametrize(
    "resource",
    [
        "past-performance",
        "personnel",
        "registrations",
        "vehicles",
        "insurance",
        "boilerplate",
        "files",
        "rate-card",
    ],
)
async def test_proof_resources_are_tenant_isolated(
    api_client: httpx.AsyncClient, database: Database, resource: str
) -> None:
    _, owner, pid = await _profile(api_client, database)
    _, stranger, _ = await _profile(api_client, database)
    url = f"/api/v1/profiles/{pid}/{resource}"
    assert (await api_client.get(url, headers=owner)).status_code == 200
    assert (await api_client.get(url, headers=stranger)).status_code == 404
    assert (await api_client.get(f"{url}/{uuid.uuid4()}", headers=owner)).status_code == 404
