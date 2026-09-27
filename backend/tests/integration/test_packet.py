"""M5-14: GET /api/v1/pursuits/{id}/packet returns what to upload where, the portal link,
the signatures / DSC steps and the deadline in both zones -- and makes no outbound call
and no submission (SPEC 1: a human always submits on the portal)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from app.core.config import Region
from app.core.db import Database
from app.core.opportunity import NoticeType
from app.core.packet import NEVER_SUBMITS, SAM_LOGIN_NOTE
from app.core.roles import Role
from app.models import User

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner
from tests.integration.test_matrix import US_REQUIREMENTS, _run, _setup
from tests.llm_fake import FakeLLM

DUE = datetime(2026, 10, 30, 18, 0, tzinfo=UTC)
ANSWER: dict[str, Any] = {
    "assignments": [
        {"req_id": "R-004", "section": "Management Plan"},
        {"req_id": "R-005", "section": "Technical Approach"},
    ]
}
IN_REQUIREMENTS: list[tuple[str, str, str, str | None]] = [
    (
        "R-001",
        "Bidders must furnish EMD of INR 2,50,000 or a bank guarantee valid for 45 days.",
        "eligibility",
        None,
    ),
    ("R-002", "Average annual turnover of INR 5 crore is required.", "eligibility", None),
    (
        "R-003",
        "Both covers must be digitally signed with a Class 3 DSC on eprocure.gov.in.",
        "submission",
        None,
    ),
    ("R-004", "Upload each cover as a single PDF.", "format", None),
]


async def _set_deadline_and_tz(database: Database, ctx: dict[str, Any], *, tz: str) -> None:
    from app.models import Opportunity

    async with database.owner_session() as session:
        opportunity = await session.get(Opportunity, ctx["opportunity_id"])
        assert opportunity is not None
        opportunity.response_due_at = DUE
        opportunity.source_tz = tz
        opportunity.solicitation_number = "2032H5-26-SS-0042"


async def _set_user_tz(database: Database, user_id: uuid.UUID, tz: str) -> None:
    async with database.owner_session() as session:
        row = await session.get(User, user_id)
        assert row is not None
        row.tz = tz


async def test_us_packet_lists_uploads_signatures_and_the_sam_login_note(
    api_client: httpx.AsyncClient, database: Database, fake_llm: FakeLLM
) -> None:
    ctx = await _setup(database, requirements=US_REQUIREMENTS)
    await _set_deadline_and_tz(database, ctx, tz="America/New_York")
    await _set_user_tz(database, ctx["user_id"], "Asia/Kolkata")
    owner = auth_headers(user_id=ctx["user_id"], tenant_id=ctx["tenant_id"], email=ctx["email"])
    url = f"/api/v1/pursuits/{ctx['pursuit_id']}/packet"

    # a packet exists before the matrix agent runs: the region's steps, no rules yet
    early = await api_client.get(url, headers=owner)
    assert early.status_code == 200
    body = early.json()["packet"]
    assert [s["label"] for s in body["upload_steps"]] == ["Capability statement / response"]
    assert body["sam_login_note"] == SAM_LOGIN_NOTE and body["dsc_steps"] == []
    assert body["deadline"]["utc"] == "2026-10-30T18:00:00Z"
    assert early.json()["checklist"] == [] and early.json()["checklist_version"] is None

    fake_llm.queue(ANSWER)
    await _run(database, ctx, fake_llm)

    resp = await api_client.get(url, headers=owner)
    assert resp.status_code == 200
    data = resp.json()
    packet = data["packet"]
    assert data["pursuit_id"] == str(ctx["pursuit_id"])
    assert data["checklist_version"] == 1 and data["generated_at"] is not None
    assert {i["key"] for i in data["checklist"]} >= {"sam_registration", "reps_certs"}

    steps = packet["upload_steps"]
    assert steps[0]["label"] == "Capability statement / response"
    assert steps[0]["destination"] == "Email to market.research@irs.example.gov"
    assert steps[0]["file_name"] == "CompanyName_IRS_SS_0042.pdf"
    assert steps[0]["formats"] == ["PDF"] and "10-page limit" in steps[0]["note"]
    assert [s["order"] for s in steps] == list(range(1, len(steps) + 1))
    assert any(s["label"].startswith("Capability statement covering") for s in steps)

    assert packet["portal"] == "SAM.gov" and packet["portal_url"] == "https://portal.test/notice"
    assert packet["submission_method"] == "email"
    assert packet["email"] == "market.research@irs.example.gov"
    assert packet["sam_login_note"] == SAM_LOGIN_NOTE and packet["dsc_steps"] == []
    assert [s["key"] for s in packet["signatures"]] == ["offer_form", "reps_certs"]
    assert packet["page_limit"] == 10

    deadline = packet["deadline"]
    assert deadline["buyer_tz"] == "America/New_York" and "EDT" in deadline["buyer_display"]
    assert deadline["user_tz"] == "Asia/Kolkata" and "IST" in deadline["user_display"]
    assert "=" in deadline["display"]
    assert packet["disclaimer"] == NEVER_SUBMITS

    # another tenant cannot read it
    async with database.owner_session() as session:
        tenant_b, user_b, _ = await create_tenant_with_owner(session)
    other = auth_headers(user_id=user_b.id, tenant_id=tenant_b.id)
    assert (await api_client.get(url, headers=other)).status_code == 404


async def test_india_packet_has_emd_bg_and_dsc_steps(
    api_client: httpx.AsyncClient, database: Database, fake_llm: FakeLLM
) -> None:
    ctx = await _setup(
        database,
        region=Region.IN,
        notice_type=NoticeType.GEM_BID,
        source_id="gem_bids",
        currency="INR",
        emd=Decimal("250000"),
        requirements=IN_REQUIREMENTS,
    )
    await _set_deadline_and_tz(database, ctx, tz="Asia/Kolkata")
    viewer = auth_headers(user_id=uuid.uuid4(), tenant_id=ctx["tenant_id"], role=Role.VIEWER)
    await _run(database, ctx, fake_llm)
    assert fake_llm.calls == []

    resp = await api_client.get(f"/api/v1/pursuits/{ctx['pursuit_id']}/packet", headers=viewer)
    assert resp.status_code == 200
    data = resp.json()
    packet = data["packet"]
    labels = [s["label"] for s in packet["upload_steps"]]
    assert labels[:2] == ["Technical bid cover", "Financial bid cover / BoQ"]
    assert all("Class 3 DSC" in s["note"] for s in packet["upload_steps"][:2])
    assert any("turnover" in label.lower() for label in labels)
    assert any("affidavit" in label.lower() for label in labels)
    assert any("bank guarantee" in label.lower() for label in labels)

    assert len(packet["dsc_steps"]) == 5
    assert "Class 3 signing DSC token" in packet["dsc_steps"][0]
    assert packet["sam_login_note"] is None
    assert packet["signatures"][0]["key"] == "dsc_covers"
    # EMD and the tender fee stay on the checklist as payments, never as uploads
    checklist = {i["key"]: i for i in data["checklist"]}
    assert checklist["emd"]["required"] and "₹2,50,000" in checklist["emd"]["note"]
    assert not any("Earnest money" in label for label in labels)
    assert packet["deadline"]["buyer_tz"] == "Asia/Kolkata"
    assert packet["disclaimer"] == NEVER_SUBMITS


async def test_packet_makes_no_outbound_call_and_exposes_no_submit_route(
    app: Any, api_client: httpx.AsyncClient, database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SPEC 1: auto-submission is out of scope. Reading the packet must not touch the
    network, and no route anywhere offers to submit a bid."""
    calls: list[str] = []

    def _forbid(*args: Any, **kwargs: Any) -> None:
        calls.append(str(args))
        raise AssertionError("the packet route must not make an outbound HTTP call")

    # the real network transports only (the test client speaks ASGI, and asyncpg keeps its
    # own socket): anything that would leave the process is fatal here
    for target in (
        "httpx.HTTPTransport.handle_request",
        "httpx.AsyncHTTPTransport.handle_async_request",
        "app.adapters.http.PoliteClient.get",
    ):
        monkeypatch.setattr(target, _forbid, raising=True)

    ctx = await _setup(database, requirements=US_REQUIREMENTS)
    owner = auth_headers(user_id=ctx["user_id"], tenant_id=ctx["tenant_id"], email=ctx["email"])
    resp = await api_client.get(f"/api/v1/pursuits/{ctx['pursuit_id']}/packet", headers=owner)
    assert resp.status_code == 200 and calls == []

    body = resp.json()
    rendered = str(body).lower()
    assert "submit on your behalf" not in rendered
    assert body["packet"]["disclaimer"] == NEVER_SUBMITS

    paths = set(app.openapi()["paths"])
    assert not [p for p in paths if "submit" in p.lower()]
    for path, methods in app.openapi()["paths"].items():
        for method, spec in methods.items():
            operation = f"{method} {path} {spec.get('operationId', '')}".lower()
            assert "submit" not in operation, operation
