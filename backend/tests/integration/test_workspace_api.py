"""M5-16: the pursuit workspace API -- drafts with optimistic versioning, section
approval, comments and the agents/run step names."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from app.core.citations import profile_token
from app.core.config import Region
from app.core.db import Database
from app.core.opportunity import NoticeType
from app.core.profile_fields import PerformanceRole
from app.core.roles import Role
from app.models import (
    AuditLog,
    Comment,
    CompanyProfile,
    ComplianceItem,
    Draft,
    Opportunity,
    OpportunityDocument,
    PastPerformance,
    Pursuit,
    Requirement,
)
from app.services.drafts import save_version
from sqlalchemy import select

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner


async def _setup(database: Database) -> dict[str, Any]:
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(session)
        profile = CompanyProfile(
            tenant_id=tenant.id, region=Region.US, legal_name="Alpha Federal LLC"
        )
        opp = Opportunity(
            source_id="sam_opps",
            external_id=f"ext-{uuid.uuid4().hex[:8]}",
            region=Region.US,
            country="US",
            currency="USD",
            notice_type=NoticeType.RFP,
            title="Cloud migration services",
        )
        session.add_all([profile, opp])
        await session.flush()
        past = PastPerformance(
            tenant_id=tenant.id,
            profile_id=profile.id,
            title="Treasury cloud migration",
            customer="US Treasury",
            role=PerformanceRole.PRIME,
            scope="Migrated 400 workloads",
        )
        doc = OpportunityDocument(
            opportunity_id=opp.id, url="https://x.test/rfp.pdf", file_name="rfp.pdf"
        )
        session.add_all([past, doc])
        await session.flush()
        pursuit = Pursuit(
            tenant_id=tenant.id,
            profile_id=profile.id,
            opportunity_id=opp.id,
            created_by=user.id,
            decision="bid",
            stage="drafting",
        )
        session.add(pursuit)
        await session.flush()
        req = Requirement(
            tenant_id=tenant.id,
            pursuit_id=pursuit.id,
            req_id="R-001",
            text="The contractor shall migrate 400 workloads.",
            document_id=doc.id,
            page=1,
            type="shall",
            quote="shall migrate 400 workloads",
        )
        session.add(req)
        await session.flush()
        item = ComplianceItem(
            tenant_id=tenant.id,
            pursuit_id=pursuit.id,
            requirement_id=req.id,
            section="Technical Approach",
            reason="keyword",
        )
        session.add(item)
        await session.flush()
        ctx = {
            "tenant_id": tenant.id,
            "user_id": user.id,
            "email": user.email,
            "profile_id": profile.id,
            "pursuit_id": pursuit.id,
            "past_performance": past.id,
            "compliance_item": item.id,
        }
    token = profile_token("past_performance", ctx["past_performance"])
    async with database.session(ctx["tenant_id"]) as session:
        draft, _version = await save_version(
            session,
            ctx["tenant_id"],
            ctx["pursuit_id"],
            "technical-approach",
            title="Technical Approach",
            volume="Volume I - Technical",
            body_html=f"<h2>Technical Approach</h2><p>We migrated 400 workloads [{token}].</p>",
            citations=[{"token": token, "quote": "Migrated 400 workloads"}],
            author="agent",
            model="claude-sonnet-5",
        )
        await save_version(
            session,
            ctx["tenant_id"],
            ctx["pursuit_id"],
            "past-performance",
            title="Past Performance",
            volume="Volume II - Past Performance",
            body_html="<p>We employ 250 engineers and hold ISO 27001.</p>",
            needs_input=[{"placeholder": "[NEEDS INPUT: head count]", "question": "How many?"}],
            author="agent",
        )
        ctx["draft_id"] = draft.id
    return ctx


def _headers(ctx: dict[str, Any], role: Role | None = None) -> dict[str, str]:
    if role is None:
        return auth_headers(user_id=ctx["user_id"], tenant_id=ctx["tenant_id"], email=ctx["email"])
    return auth_headers(user_id=uuid.uuid4(), tenant_id=ctx["tenant_id"], role=role)


async def _audit_actions(database: Database, action: str) -> list[AuditLog]:
    async with database.owner_session() as session:
        return list(
            (await session.execute(select(AuditLog).where(AuditLog.action == action)))
            .scalars()
            .all()
        )


async def test_list_and_read_drafts_with_their_grounding_counts(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database)
    base = f"/api/v1/pursuits/{ctx['pursuit_id']}/drafts"

    listed = await api_client.get(base, headers=_headers(ctx))
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["count"] == 2 and body["unsupported_claims_count"] == 1
    assert body["needs_input_count"] == 1 and body["flagged_sections"] == 1
    sections = {item["section_id"]: item for item in body["items"]}
    assert sections["technical-approach"]["unsupported_claims"] == 0
    assert sections["technical-approach"]["citations"] == 1
    assert sections["technical-approach"]["volume"] == "Volume I - Technical"
    assert sections["past-performance"]["unsupported_claims"] == 1
    assert sections["past-performance"]["needs_input"] == 1
    assert all(item["status"] == "draft" and item["version"] == 1 for item in body["items"])

    one = await api_client.get(f"{base}/technical-approach", headers=_headers(ctx))
    assert one.status_code == 200, one.text
    section = one.json()
    assert section["title"] == "Technical Approach" and section["versions"] == [1]
    assert section["current"]["author"] == "agent" and section["current"]["version"] == 1
    assert "<h2>Technical Approach</h2>" in section["current"]["body_html"]
    assert section["current"]["citations"][0]["token"] == profile_token(
        "past_performance", ctx["past_performance"]
    )
    assert section["current"]["flags"]["unsupported_count"] == 0
    assert section["current"]["model"] == "claude-sonnet-5"

    missing = await api_client.get(f"{base}/nope", headers=_headers(ctx))
    assert missing.status_code == 404

    # SPEC 11: draft reads are audited
    reads = await _audit_actions(database, "draft.read")
    assert len(reads) == 1 and reads[0].meta["section_id"] == "technical-approach"
    assert reads[0].meta["version"] == 1 and reads[0].user_id == ctx["user_id"]
    assert len(await _audit_actions(database, "draft.list")) == 1


async def test_draft_roles(api_client: httpx.AsyncClient, database: Database) -> None:
    ctx = await _setup(database)
    base = f"/api/v1/pursuits/{ctx['pursuit_id']}/drafts"
    viewer = _headers(ctx, Role.VIEWER)
    reviewer = _headers(ctx, Role.REVIEWER)

    # a viewer sees dashboards, never draft text (SPEC 3)
    assert (await api_client.get(base, headers=viewer)).status_code == 403
    assert (await api_client.get(f"{base}/technical-approach", headers=viewer)).status_code == 403
    assert (await api_client.get(f"{base}/technical-approach", headers=reviewer)).status_code == 200

    # a reviewer may comment and approve, never edit
    edit = {"body_html": "<p>reviewer edit</p>", "base_version": 1}
    assert (
        await api_client.put(f"{base}/technical-approach", json=edit, headers=reviewer)
    ).status_code == 403
    assert (
        await api_client.put(
            f"{base}/technical-approach", json=edit, headers=_headers(ctx, Role.WRITER)
        )
    ).status_code == 200


async def test_put_uses_optimistic_versioning_and_revalidates_grounding(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database)
    base = f"/api/v1/pursuits/{ctx['pursuit_id']}/drafts"
    writer_id = uuid.uuid4()
    writer = auth_headers(user_id=writer_id, tenant_id=ctx["tenant_id"], role=Role.WRITER)

    stale = await api_client.put(
        f"{base}/technical-approach",
        json={"body_html": "<p>late edit</p>", "base_version": 0},
        headers=writer,
    )
    assert stale.status_code == 409 and "version 1" in stale.json()["detail"]

    saved = await api_client.put(
        f"{base}/technical-approach",
        json={
            "body_html": (
                '<p onclick="steal()">We are CMMI Level 5 appraised.</p><script>alert(1)</script>'
            ),
            "base_version": 1,
        },
        headers=writer,
    )
    assert saved.status_code == 200, saved.text
    current = saved.json()["current"]
    assert current["version"] == 2 and current["author"] == "user"
    assert current["author_user_id"] == str(writer_id)
    assert "onclick" not in current["body_html"] and "<script" not in current["body_html"]
    # the writer's own claim is re-checked by the grounding validator (M5-11)
    assert current["flags"]["unsupported_count"] == 1
    assert {f["reason"] for f in current["flags"]["flags"]} >= {"certification"}
    assert saved.json()["versions"] == [1, 2]

    # markdown is accepted and rendered
    md = await api_client.put(
        f"{base}/technical-approach",
        json={"body_markdown": "## Approach\n\n- one\n- two", "base_version": 2},
        headers=writer,
    )
    assert md.status_code == 200
    assert "<h2>Approach</h2>" in md.json()["current"]["body_html"]
    assert "<li>one</li>" in md.json()["current"]["body_html"]

    # exactly one body, and a brand-new section needs a title
    both = await api_client.put(
        f"{base}/technical-approach",
        json={"body_html": "<p>x</p>", "body_markdown": "x", "base_version": 3},
        headers=writer,
    )
    assert both.status_code == 422
    untitled = await api_client.put(
        f"{base}/cover-letter", json={"body_html": "<p>x</p>", "base_version": 0}, headers=writer
    )
    assert untitled.status_code == 422
    created = await api_client.put(
        f"{base}/cover-letter",
        json={"body_html": "<p>Dear Contracting Officer</p>", "base_version": 0, "title": "Cover"},
        headers=writer,
    )
    assert created.status_code == 200 and created.json()["current"]["version"] == 1

    rows = await _audit_actions(database, "draft.saved")
    assert [row.meta["version"] for row in rows] == [2, 3, 1]
    assert rows[0].meta["unsupported_claims"] == 1


async def test_approve_a_section(api_client: httpx.AsyncClient, database: Database) -> None:
    ctx = await _setup(database)
    base = f"/api/v1/pursuits/{ctx['pursuit_id']}/drafts"
    reviewer_id = uuid.uuid4()
    reviewer = auth_headers(user_id=reviewer_id, tenant_id=ctx["tenant_id"], role=Role.REVIEWER)

    denied = await api_client.post(
        f"{base}/technical-approach/approve", headers=_headers(ctx, Role.WRITER)
    )
    assert denied.status_code == 403

    approved = await api_client.post(f"{base}/technical-approach/approve", headers=reviewer)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert approved.json()["approved_by"] == str(reviewer_id)
    assert approved.json()["approved_at"] is not None

    listed = (await api_client.get(base, headers=_headers(ctx))).json()
    assert listed["approved"] == 1
    assert (await api_client.post(f"{base}/nope/approve", headers=reviewer)).status_code == 404
    assert len(await _audit_actions(database, "draft.approved")) == 1


async def test_comments_on_drafts_matrix_rows_and_the_scorecard(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database)
    base = f"/api/v1/pursuits/{ctx['pursuit_id']}/comments"
    reviewer = _headers(ctx, Role.REVIEWER)

    assert (
        await api_client.post(
            base,
            json={"target_type": "draft_section", "target_id": str(ctx["draft_id"]), "body": "hi"},
            headers=_headers(ctx, Role.VIEWER),
        )
    ).status_code == 403

    created = await api_client.post(
        base,
        json={
            "target_type": "draft_section",
            "target_id": str(ctx["draft_id"]),
            "body": "Cite the Treasury contract here.",
        },
        headers=reviewer,
    )
    assert created.status_code == 201, created.text
    comment_id = created.json()["id"]
    assert created.json()["resolved_at"] is None

    on_matrix = await api_client.post(
        base,
        json={
            "target_type": "compliance_item",
            "target_id": str(ctx["compliance_item"]),
            "body": "Who owns R-001?",
        },
        headers=_headers(ctx),
    )
    assert on_matrix.status_code == 201

    bad_target = await api_client.post(
        base,
        json={"target_type": "draft_section", "target_id": str(uuid.uuid4()), "body": "ghost"},
        headers=reviewer,
    )
    assert bad_target.status_code == 404
    bad_type = await api_client.post(
        base,
        json={"target_type": "invoice", "target_id": str(ctx["draft_id"]), "body": "x"},
        headers=reviewer,
    )
    assert bad_type.status_code == 422

    listed = await api_client.get(base, headers=reviewer)
    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 2 and listed.json()["open_count"] == 2
    only_draft = await api_client.get(
        base, params={"target_type": "draft_section"}, headers=reviewer
    )
    assert [c["id"] for c in only_draft.json()["items"]] == [comment_id]
    assert (
        await api_client.get(base, params={"target_type": "nope"}, headers=reviewer)
    ).status_code == 422

    # an open comment shows on the section it belongs to
    section = await api_client.get(
        f"/api/v1/pursuits/{ctx['pursuit_id']}/drafts/technical-approach", headers=reviewer
    )
    assert section.json()["comments"] == 1

    resolved = await api_client.post(f"{base}/{comment_id}/resolve", headers=reviewer)
    assert resolved.status_code == 200 and resolved.json()["resolved_at"] is not None
    first = resolved.json()["resolved_at"]
    again = await api_client.post(f"{base}/{comment_id}/resolve", headers=reviewer)
    assert again.status_code == 200 and again.json()["resolved_at"] == first  # idempotent
    assert (
        await api_client.post(f"{base}/{uuid.uuid4()}/resolve", headers=reviewer)
    ).status_code == 404

    open_only = await api_client.get(base, params={"resolved": False}, headers=reviewer)
    assert len(open_only.json()["items"]) == 1
    done = await api_client.get(base, params={"resolved": True}, headers=reviewer)
    assert [c["id"] for c in done.json()["items"]] == [comment_id]
    assert len(await _audit_actions(database, "comment.created")) == 2
    assert len(await _audit_actions(database, "comment.resolved")) == 2

    async with database.session(ctx["tenant_id"]) as session:
        rows = (await session.execute(select(Comment))).scalars().all()
        assert {row.target_type for row in rows} == {"draft_section", "compliance_item"}


async def test_agents_run_accepts_the_new_steps(
    api_client: httpx.AsyncClient, database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = await _setup(database)
    url = f"/api/v1/pursuits/{ctx['pursuit_id']}/agents/run"

    async def _dispatch(*_args: Any, **_kwargs: Any) -> tuple[str, str | None, None]:
        return "queued", "task-1", None

    monkeypatch.setattr("app.api.v1.pursuits.dispatch_run", _dispatch)
    for step in ("bid_no_bid", "outline", "draft"):
        resp = await api_client.post(
            url, json={"step": step}, headers=_headers(ctx, Role.BID_MANAGER)
        )
        assert resp.status_code == 202, resp.text
        assert resp.json()["step"] == step and resp.json()["steps"] == [step]
    # the pursuit has an approved bid decision, so "all" plans past Gate 1
    everything = await api_client.post(
        url, json={"step": "all"}, headers=_headers(ctx, Role.BID_MANAGER)
    )
    assert everything.json()["steps"][:5] == [
        "collect",
        "extract",
        "matrix",
        "bid_no_bid",
        "outline",
    ]
    bad = await api_client.post(url, json={"step": "nope"}, headers=_headers(ctx))
    assert bad.status_code == 422


async def test_another_tenant_sees_no_drafts_or_comments(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database)
    async with database.owner_session() as session:
        tenant_b, user_b, _ = await create_tenant_with_owner(session)
        headers = auth_headers(user_id=user_b.id, tenant_id=tenant_b.id, email=user_b.email)
    base = f"/api/v1/pursuits/{ctx['pursuit_id']}"
    assert (await api_client.get(f"{base}/drafts", headers=headers)).status_code == 404
    assert (
        await api_client.get(f"{base}/drafts/technical-approach", headers=headers)
    ).status_code == 404
    assert (
        await api_client.put(
            f"{base}/drafts/technical-approach",
            json={"body_html": "<p>x</p>", "base_version": 1},
            headers=headers,
        )
    ).status_code == 404
    assert (await api_client.get(f"{base}/comments", headers=headers)).status_code == 404
    async with database.session(ctx["tenant_id"]) as session:
        assert len((await session.execute(select(Draft))).scalars().all()) == 2
