"""M5-11: the grounding validator runs on every draft version (agent or user) and the
pursuit exposes the unsupported-claim count."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
from app.core.citations import profile_token
from app.core.config import Region
from app.core.db import Database
from app.core.opportunity import NoticeType
from app.core.profile_fields import PerformanceRole
from app.models import CompanyProfile, DraftVersion, Opportunity, PastPerformance, Pursuit
from app.services.drafts import grounding_inputs, save_version, summarise
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
        session.add(past)
        await session.flush()
        pursuit = Pursuit(
            tenant_id=tenant.id,
            profile_id=profile.id,
            opportunity_id=opp.id,
            created_by=user.id,
        )
        session.add(pursuit)
        await session.flush()
        return {
            "tenant_id": tenant.id,
            "user_id": user.id,
            "email": user.email,
            "profile_id": profile.id,
            "pursuit_id": pursuit.id,
            "past_performance": past.id,
        }


async def test_grounding_inputs_cover_the_profiles_records(database: Database) -> None:
    ctx = await _setup(database)
    async with database.session(ctx["tenant_id"]) as session:
        inputs = await grounding_inputs(session, ctx["pursuit_id"])
    assert profile_token("past_performance", ctx["past_performance"]) in inputs.tokens
    assert "Alpha Federal LLC" in inputs.facts and "US Treasury" in inputs.facts
    async with database.session(ctx["tenant_id"]) as session:
        assert (await grounding_inputs(session, uuid.uuid4())).tokens == frozenset()


async def test_every_version_is_validated_whoever_wrote_it(database: Database) -> None:
    ctx = await _setup(database)
    token = profile_token("past_performance", ctx["past_performance"])
    async with database.session(ctx["tenant_id"]) as session:
        _draft, agent_version = await save_version(
            session,
            ctx["tenant_id"],
            ctx["pursuit_id"],
            "technical-approach",
            title="Technical Approach",
            body_html=(
                f"<h2>Technical Approach</h2><p>We migrated 400 workloads for the US "
                f"Treasury [{token}].</p>"
            ),
            citations=[{"token": token, "quote": "Migrated 400 workloads"}],
            author="agent",
        )
        assert agent_version.flags["unsupported_count"] == 0
        assert agent_version.flags["supported_count"] == 1
        assert agent_version.flags["flags"] == []

        # a writer's edit is validated the same way -- and this one invents a fact
        _draft, user_version = await save_version(
            session,
            ctx["tenant_id"],
            ctx["pursuit_id"],
            "technical-approach",
            title="Technical Approach",
            body_html=(
                "<h2>Technical Approach</h2><p>We migrated 400 workloads for the US "
                "Treasury.</p><p>We are CMMI Level 5 appraised and employ 250 engineers.</p>"
            ),
            author="user",
            author_user_id=ctx["user_id"],
        )
        assert user_version.version == 2 and user_version.author == "user"
        assert user_version.flags["unsupported_count"] == 2
        reasons = {flag["reason"] for flag in user_version.flags["flags"]}
        assert {"numbers", "certification", "past_performance"} <= reasons
        assert user_version.flags["supported_count"] == 0


async def test_the_pursuit_exposes_the_unsupported_claim_count(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database)
    token = profile_token("past_performance", ctx["past_performance"])
    async with database.session(ctx["tenant_id"]) as session:
        await save_version(
            session,
            ctx["tenant_id"],
            ctx["pursuit_id"],
            "technical-approach",
            title="Technical Approach",
            body_html="<p>We employ 250 engineers and hold ISO 27001.</p>",
            needs_input=[{"placeholder": "[NEEDS INPUT: head count]", "question": "How many?"}],
            author="agent",
        )
        await save_version(
            session,
            ctx["tenant_id"],
            ctx["pursuit_id"],
            "past-performance",
            title="Past Performance",
            body_html=f"<p>We migrated 400 workloads for the US Treasury [{token}].</p>",
            citations=[{"token": token, "quote": "Migrated"}],
            author="agent",
        )
        summary = await summarise(session, ctx["pursuit_id"])
    assert summary.count == 2 and summary.flagged_sections == 1
    assert summary.unsupported_claims_count == 1 and summary.needs_input_count == 1

    owner = auth_headers(user_id=ctx["user_id"], tenant_id=ctx["tenant_id"], email=ctx["email"])
    resp = await api_client.get(f"/api/v1/pursuits/{ctx['pursuit_id']}", headers=owner)
    assert resp.status_code == 200, resp.text
    drafts = resp.json()["drafts"]
    assert drafts["count"] == 2 and drafts["unsupported_claims_count"] == 1
    assert drafts["needs_input_count"] == 1 and drafts["flagged_sections"] == 1
    assert drafts["approved"] == 0 and drafts["in_review"] == 0


async def test_only_the_current_version_counts(database: Database) -> None:
    ctx = await _setup(database)
    token = profile_token("past_performance", ctx["past_performance"])
    async with database.session(ctx["tenant_id"]) as session:
        await save_version(
            session,
            ctx["tenant_id"],
            ctx["pursuit_id"],
            "technical-approach",
            title="Technical Approach",
            body_html="<p>We employ 250 engineers and hold ISO 27001.</p>",
            author="agent",
        )
        assert (await summarise(session, ctx["pursuit_id"])).unsupported_claims_count == 1
        await save_version(
            session,
            ctx["tenant_id"],
            ctx["pursuit_id"],
            "technical-approach",
            title="Technical Approach",
            body_html=f"<p>We migrated 400 workloads for the US Treasury [{token}].</p>",
            citations=[{"token": token, "quote": "Migrated"}],
            author="user",
            author_user_id=ctx["user_id"],
        )
        summary = await summarise(session, ctx["pursuit_id"])
        assert summary.count == 1 and summary.unsupported_claims_count == 0
        rows = (
            (await session.execute(select(DraftVersion).order_by(DraftVersion.version)))
            .scalars()
            .all()
        )
        assert [r.flags["unsupported_count"] for r in rows] == [1, 0]  # history keeps both
