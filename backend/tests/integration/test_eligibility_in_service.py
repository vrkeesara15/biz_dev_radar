"""M3-08: services.profiles.load_eligibility_snapshot feeds core.eligibility_in from stored rows."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import httpx
from app.core.config import Region
from app.core.db import Database
from app.core.eligibility_in import (
    MSE_EXEMPTION,
    CertificationIn,
    CriteriaIn,
    RegistrationIn,
    Status,
    evaluate_in,
)
from app.core.finance import FiscalYearRevenue
from app.models import CompanyProfile
from app.services.profiles import load_eligibility_snapshot

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner


async def test_snapshot_from_stored_profile_and_children(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(
            session, region=Region.IN, data_residency=Region.IN
        )
        tid, uid = tenant.id, user.id
    headers = auth_headers(user_id=uid, tenant_id=tid)
    r = await api_client.post(
        "/api/v1/profiles",
        json={
            "region": "in",
            "legal_name": "Udyam Systems Pvt Ltd",
            "year_founded": 2015,
            "udyam_number": "UDYAM-TN-01-0001234",
            "udyam_category": "micro",
            "dpiit_number": "DIPP12345",
            "gem_seller_id": "GEM-SELLER-9",
            "annual_revenue": [
                {"fiscal_year": 2023, "amount": "10000000", "currency": "INR"},
                {"fiscal_year": 2024, "amount": "20000000", "currency": "INR"},
                {"fiscal_year": 2025, "amount": "30000000", "currency": "INR"},
            ],
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    for payload in (
        {"kind": "iso_9001", "expires_on": "2027-01-01"},
        {"kind": "cmmi", "level": "3"},
    ):
        r = await api_client.post(
            f"/api/v1/profiles/{pid}/certifications", json=payload, headers=headers
        )
        assert r.status_code == 201, r.text
    for payload in (
        {"kind": "dsc", "holder": "Ravi Kumar", "identifier": "DSC-1", "expires_on": "2026-01-01"},
        {"kind": "gem", "identifier": "GEM-SELLER-9"},
    ):
        r = await api_client.post(
            f"/api/v1/profiles/{pid}/registrations", json=payload, headers=headers
        )
        assert r.status_code == 201, r.text

    async with database.session(tid) as session:
        row = await session.get(CompanyProfile, uuid.UUID(pid))
        assert row is not None
        snapshot = await load_eligibility_snapshot(session, row)

    assert snapshot.revenue == (
        FiscalYearRevenue(2023, Decimal("10000000"), "INR"),
        FiscalYearRevenue(2024, Decimal("20000000"), "INR"),
        FiscalYearRevenue(2025, Decimal("30000000"), "INR"),
    )
    assert (snapshot.year_founded, snapshot.udyam_number, snapshot.udyam_category) == (
        2015,
        "UDYAM-TN-01-0001234",
        "micro",
    )
    assert snapshot.dpiit_number == "DIPP12345" and snapshot.gem_seller_id == "GEM-SELLER-9"
    assert set(snapshot.certifications) == {
        CertificationIn("iso_9001", date(2027, 1, 1)),
        CertificationIn("cmmi", None),
    }
    assert snapshot.registrations == (
        RegistrationIn("dsc", "DSC-1", date(2026, 1, 1)),
        RegistrationIn("gem", "GEM-SELLER-9", None),
    )

    out = evaluate_in(
        snapshot,
        CriteriaIn(
            min_avg_turnover_inr=Decimal("50000000"),
            required_certifications=("ISO 9001",),
            emd_amount_inr=Decimal("100000"),
            allows_mse_exemption=True,
            requires_gem_registration=True,
            requires_dsc=True,
        ),
        date(2026, 9, 27),
    )
    assert out.status is Status.FAIL and out.blocking == ("dsc",)
    assert out.exemptions == (MSE_EXEMPTION,)
    assert [r.status.value for r in out.results] == ["pass", "pass", "pass", "pass", "fail"]


async def test_snapshot_of_an_empty_profile(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(
            session, region=Region.IN, data_residency=Region.IN
        )
        tid, uid = tenant.id, user.id
    headers = auth_headers(user_id=uid, tenant_id=tid)
    r = await api_client.post(
        "/api/v1/profiles", json={"region": "in", "legal_name": "Bare Co"}, headers=headers
    )
    assert r.status_code == 201, r.text
    async with database.session(tid) as session:
        row = await session.get(CompanyProfile, uuid.UUID(r.json()["id"]))
        assert row is not None
        snapshot = await load_eligibility_snapshot(session, row)
    assert snapshot.revenue == () and snapshot.certifications == ()
    assert snapshot.registrations == () and snapshot.udyam_category is None
    out = evaluate_in(snapshot, CriteriaIn(min_avg_turnover_inr=Decimal(1), requires_dsc=True))
    assert out.status is Status.UNKNOWN and out.score == Decimal("0.5")
