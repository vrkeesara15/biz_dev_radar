"""M5-17: a human's edit of a draft is diffed against the version it started from and
kept as drafting feedback -- per tenant, never shared across tenants."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
from app.core.config import Region
from app.core.db import Database
from app.core.opportunity import NoticeType
from app.core.roles import Role
from app.models import AuditLog, CompanyProfile, DraftFeedback, Opportunity, Pursuit
from app.services import draft_feedback as feedback_svc
from app.services.drafts import save_version
from sqlalchemy import select

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner

AGENT_BODY = (
    "<h2>Technical Approach</h2>"
    "<p>We will migrate the workloads using our standard runbook.</p>"
    "<p>Our team is world-class.</p>"
)
EDITED_BODY = (
    "<h2>Technical Approach</h2>"
    "<p>We will migrate 400 workloads using the runbook we ran at the Treasury.</p>"
)


async def _tenant(database: Database, legal_name: str) -> dict[str, Any]:
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(session)
        profile = CompanyProfile(tenant_id=tenant.id, region=Region.US, legal_name=legal_name)
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
        return {
            "tenant_id": tenant.id,
            "user_id": user.id,
            "email": user.email,
            "pursuit_id": pursuit.id,
        }


async def _agent_draft(
    database: Database, ctx: dict[str, Any], section_id: str = "technical-approach"
) -> None:
    async with database.session(ctx["tenant_id"]) as session:
        await save_version(
            session,
            ctx["tenant_id"],
            ctx["pursuit_id"],
            section_id,
            title="Technical Approach",
            volume="Volume I - Technical",
            body_html=AGENT_BODY,
            author="agent",
            model="claude-sonnet-5",
        )


async def _feedback(database: Database, tenant_id: uuid.UUID) -> list[DraftFeedback]:
    async with database.owner_session() as session:
        return list(
            (
                await session.execute(
                    select(DraftFeedback)
                    .where(DraftFeedback.tenant_id == tenant_id)
                    .order_by(DraftFeedback.created_at)
                )
            )
            .scalars()
            .all()
        )


def _headers(ctx: dict[str, Any], role: Role | None = None) -> dict[str, str]:
    if role is None:
        return auth_headers(user_id=ctx["user_id"], tenant_id=ctx["tenant_id"], email=ctx["email"])
    return auth_headers(user_id=uuid.uuid4(), tenant_id=ctx["tenant_id"], role=role)


# --- the hook in save_version -----------------------------------------------------------


async def test_a_writers_edit_of_an_agent_draft_is_diffed_and_stored(
    database: Database,
) -> None:
    ctx = await _tenant(database, "Alpha Federal LLC")
    await _agent_draft(database, ctx)
    async with database.session(ctx["tenant_id"]) as session:
        _draft, version = await save_version(
            session,
            ctx["tenant_id"],
            ctx["pursuit_id"],
            "technical-approach",
            title="Technical Approach",
            body_html=EDITED_BODY,
            author="user",
            author_user_id=ctx["user_id"],
        )
    rows = await _feedback(database, ctx["tenant_id"])
    assert len(rows) == 1
    row = rows[0]
    assert row.section_id == "technical-approach" and row.from_author == "agent"
    assert row.to_version_id == version.id and row.from_version_id is not None
    assert row.edited_by == ctx["user_id"] and row.pursuit_id == ctx["pursuit_id"]
    assert row.stats == {"added": 1, "removed": 2, "changed": True}
    assert "--- v1 (agent)" in row.diff_text and "+++ v2 (user)" in row.diff_text
    assert "-Our team is world-class." in row.diff_text
    assert "+We will migrate 400 workloads" in row.diff_text


async def test_an_agent_version_is_never_feedback(database: Database) -> None:
    """Only a human's edit is feedback -- the red team's auto-revision is not."""
    ctx = await _tenant(database, "Alpha Federal LLC")
    await _agent_draft(database, ctx)
    async with database.session(ctx["tenant_id"]) as session:
        await save_version(
            session,
            ctx["tenant_id"],
            ctx["pursuit_id"],
            "technical-approach",
            title="Technical Approach",
            body_html=EDITED_BODY,
            author="agent",
        )
    assert await _feedback(database, ctx["tenant_id"]) == []


async def test_a_brand_new_section_a_human_wrote_has_nothing_to_diff(
    database: Database,
) -> None:
    ctx = await _tenant(database, "Alpha Federal LLC")
    async with database.session(ctx["tenant_id"]) as session:
        await save_version(
            session,
            ctx["tenant_id"],
            ctx["pursuit_id"],
            "cover-letter",
            title="Cover Letter",
            body_html="<p>Dear contracting officer.</p>",
            author="user",
            author_user_id=ctx["user_id"],
        )
    assert await _feedback(database, ctx["tenant_id"]) == []


async def test_a_save_that_changes_nothing_records_no_feedback(database: Database) -> None:
    ctx = await _tenant(database, "Alpha Federal LLC")
    await _agent_draft(database, ctx)
    async with database.session(ctx["tenant_id"]) as session:
        await save_version(
            session,
            ctx["tenant_id"],
            ctx["pursuit_id"],
            "technical-approach",
            title="Technical Approach",
            body_html=AGENT_BODY,
            author="user",
            author_user_id=ctx["user_id"],
        )
    assert await _feedback(database, ctx["tenant_id"]) == []


async def test_a_second_edit_diffs_against_the_first_edit(database: Database) -> None:
    ctx = await _tenant(database, "Alpha Federal LLC")
    await _agent_draft(database, ctx)
    async with database.session(ctx["tenant_id"]) as session:
        for body in (EDITED_BODY, EDITED_BODY + "<p>We start on day one.</p>"):
            await save_version(
                session,
                ctx["tenant_id"],
                ctx["pursuit_id"],
                "technical-approach",
                title="Technical Approach",
                body_html=body,
                author="user",
                author_user_id=ctx["user_id"],
            )
    rows = await _feedback(database, ctx["tenant_id"])
    assert [r.from_author for r in rows] == ["agent", "user"]
    assert rows[1].stats == {"added": 1, "removed": 0, "changed": True}
    assert "+We start on day one." in rows[1].diff_text


# --- reading it back ----------------------------------------------------------------------


async def test_recent_examples_returns_agent_to_human_pairs_for_the_section(
    database: Database,
) -> None:
    ctx = await _tenant(database, "Alpha Federal LLC")
    await _agent_draft(database, ctx)
    async with database.session(ctx["tenant_id"]) as session:
        await save_version(
            session,
            ctx["tenant_id"],
            ctx["pursuit_id"],
            "technical-approach",
            title="Technical Approach",
            body_html=EDITED_BODY,
            author="user",
            author_user_id=ctx["user_id"],
        )
        # a second, human-on-human edit: not a lesson about the agent
        await save_version(
            session,
            ctx["tenant_id"],
            ctx["pursuit_id"],
            "technical-approach",
            title="Technical Approach",
            body_html=EDITED_BODY + "<p>Transition starts on day one.</p>",
            author="user",
            author_user_id=ctx["user_id"],
        )
        examples = await feedback_svc.recent_examples(
            session, ctx["tenant_id"], "technical-approach", k=5
        )
        kinds = await feedback_svc.section_kinds(session, ctx["tenant_id"])
    assert len(examples) == 1
    assert examples[0].section_id == "technical-approach"
    assert "world-class" in examples[0].before
    assert "400 workloads" in examples[0].after and "world-class" not in examples[0].after
    assert examples[0].as_dict()["diff_text"].startswith("--- v1 (agent)")
    assert kinds == ["technical-approach"]


async def test_recent_examples_never_crosses_a_tenant_boundary(database: Database) -> None:
    alpha = await _tenant(database, "Alpha Federal LLC")
    beta = await _tenant(database, "Beta Systems Pvt Ltd")
    for ctx, edit in ((alpha, EDITED_BODY), (beta, "<p>Beta's secret winning wording.</p>")):
        await _agent_draft(database, ctx)
        async with database.session(ctx["tenant_id"]) as session:
            await save_version(
                session,
                ctx["tenant_id"],
                ctx["pursuit_id"],
                "technical-approach",
                title="Technical Approach",
                body_html=edit,
                author="user",
                author_user_id=ctx["user_id"],
            )
    async with database.session(alpha["tenant_id"]) as session:
        examples = await feedback_svc.recent_examples(
            session, alpha["tenant_id"], "technical-approach", k=10
        )
        # even asked for the other tenant's id, RLS leaves nothing to read
        foreign = await feedback_svc.recent_examples(
            session, beta["tenant_id"], "technical-approach", k=10
        )
    assert len(examples) == 1
    assert "secret winning wording" not in examples[0].after
    assert foreign == []


async def test_recent_examples_caps_k(database: Database) -> None:
    ctx = await _tenant(database, "Alpha Federal LLC")
    await _agent_draft(database, ctx)
    async with database.session(ctx["tenant_id"]) as session:
        await save_version(
            session,
            ctx["tenant_id"],
            ctx["pursuit_id"],
            "technical-approach",
            title="Technical Approach",
            body_html=EDITED_BODY,
            author="user",
            author_user_id=ctx["user_id"],
        )
        assert len(await feedback_svc.recent_examples(session, ctx["tenant_id"], "x", k=0)) == 0
        assert (
            len(await feedback_svc.recent_examples(session, ctx["tenant_id"], "nope", k=9999)) == 0
        )
        assert (
            len(
                await feedback_svc.recent_examples(
                    session, ctx["tenant_id"], "technical-approach", k=9999
                )
            )
            == 1
        )


# --- the route ------------------------------------------------------------------------------


async def test_the_feedback_route_is_scoped_audited_and_role_checked(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _tenant(database, "Alpha Federal LLC")
    await _agent_draft(database, ctx)
    base = f"/api/v1/pursuits/{ctx['pursuit_id']}/drafts/technical-approach"

    empty = await api_client.get(f"{base}/feedback", headers=_headers(ctx))
    assert empty.status_code == 200 and empty.json()["count"] == 0

    saved = await api_client.put(
        base,
        json={"body_html": EDITED_BODY, "base_version": 1},
        headers=_headers(ctx, Role.WRITER),
    )
    assert saved.status_code == 200, saved.text

    listed = await api_client.get(f"{base}/feedback", headers=_headers(ctx))
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["count"] == 1 and body["section_id"] == "technical-approach"
    item = body["items"][0]
    assert item["from_author"] == "agent" and item["stats"]["changed"] is True
    assert "-Our team is world-class." in item["diff_text"]
    assert item["edited_by"] is not None

    # a viewer never sees draft text; an unknown section is 404
    assert (
        await api_client.get(f"{base}/feedback", headers=_headers(ctx, Role.VIEWER))
    ).status_code == 403
    missing = await api_client.get(
        f"/api/v1/pursuits/{ctx['pursuit_id']}/drafts/nope/feedback", headers=_headers(ctx)
    )
    assert missing.status_code == 404

    async with database.owner_session() as session:
        reads = (
            await session.execute(select(AuditLog).where(AuditLog.action == "draft.feedback_read"))
        ).scalars()
        rows = list(reads)
    assert len(rows) == 2 and rows[-1].meta["count"] == 1


async def test_another_tenant_cannot_read_the_feedback(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _tenant(database, "Alpha Federal LLC")
    await _agent_draft(database, ctx)
    async with database.owner_session() as session:
        other, other_user, _ = await create_tenant_with_owner(session)
    foreign = auth_headers(user_id=other_user.id, tenant_id=other.id, email=other_user.email)
    answer = await api_client.get(
        f"/api/v1/pursuits/{ctx['pursuit_id']}/drafts/technical-approach/feedback",
        headers=foreign,
    )
    assert answer.status_code == 404
