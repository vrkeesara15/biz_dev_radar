"""M6-01: pursue / watch / pass, stage moves with their 409 rules and the board listing."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from app.core.config import Region
from app.core.db import Database
from app.core.opportunity import NoticeType
from app.core.plan import Plan
from app.core.pursuit_stages import DECISION_BID, GATE_1_REASON
from app.core.roles import Role
from app.models import AgentRun, AuditLog, CompanyProfile, Membership, Opportunity, Pursuit, User
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner, make_user

DUE = datetime(2026, 10, 14, 18, 0, tzinfo=UTC)


async def _notice(session: Any, **overrides: Any) -> Opportunity:
    values: dict[str, Any] = {
        "source_id": "sam_opps",
        "external_id": f"m6-{uuid.uuid4().hex[:8]}",
        "region": Region.US,
        "country": "US",
        "currency": "USD",
        "notice_type": NoticeType.RFP,
        "title": "Helpdesk support services",
        "buyer_org": "Internal Revenue Service",
        "source_tz": "America/New_York",
        "response_due_at": DUE,
        "estimated_value_max_usd": Decimal("2500000.00"),
    }
    values.update(overrides)
    row = Opportunity(**values)
    session.add(row)
    await session.flush()
    return row


async def _setup(database: Database, *, plan: Plan = Plan.PRO, profiles: int = 1) -> dict[str, Any]:
    async with database.owner_session() as session:
        tenant, owner, _ = await create_tenant_with_owner(session, plan=plan)
        made = []
        for n in range(profiles):
            profile = CompanyProfile(
                tenant_id=tenant.id, region=Region.US, legal_name=f"Acme {n} LLC"
            )
            session.add(profile)
            made.append(profile)
        writer = make_user()
        session.add(writer)
        await session.flush()
        session.add(Membership(tenant_id=tenant.id, user_id=writer.id, role=Role.WRITER))
        opportunity = await _notice(session)
        await session.flush()
        return {
            "tenant_id": tenant.id,
            "owner_id": owner.id,
            "owner_email": owner.email,
            "writer_id": writer.id,
            "profile_id": made[0].id,
            "profile_ids": [p.id for p in made],
            "opportunity_id": opportunity.id,
        }


def _headers(ctx: dict[str, Any], role: Role = Role.TENANT_OWNER) -> dict[str, str]:
    user_id = ctx["writer_id"] if role is Role.WRITER else ctx["owner_id"]
    return auth_headers(user_id=user_id, tenant_id=ctx["tenant_id"], role=role)


async def _pursuit(database: Database, pursuit_id: uuid.UUID, tenant_id: uuid.UUID) -> Pursuit:
    async with database.session(tenant_id) as session:
        row = await session.get(Pursuit, pursuit_id)
        assert row is not None
        return row


async def _set(database: Database, pursuit_id: uuid.UUID, tenant_id: uuid.UUID, **values: Any):  # type: ignore[no-untyped-def]
    async with database.owner_session(tenant_id) as session:
        row = await session.get(Pursuit, pursuit_id)
        assert row is not None
        for key, value in values.items():
            setattr(row, key, value)


async def test_pursue_creates_the_pursuit_with_the_internal_deadline(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database)
    response = await api_client.post(
        f"/api/v1/opportunities/{ctx['opportunity_id']}/pursue",
        json={"run_agents": False},
        headers=_headers(ctx),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["created"] is True
    assert body["agents"] is None
    pursuit = body["pursuit"]
    assert pursuit["stage"] == "identified"
    assert pursuit["owner_user_id"] == str(ctx["owner_id"])
    # SPEC 9: internal deadline is 48 hours before the buyer's
    assert pursuit["internal_due_at"] == (DUE - timedelta(hours=48)).isoformat().replace(
        "+00:00", "Z"
    ) or datetime.fromisoformat(pursuit["internal_due_at"]) == DUE - timedelta(hours=48)

    # idempotent: the second click reuses the same card with 200
    again = await api_client.post(
        f"/api/v1/opportunities/{ctx['opportunity_id']}/pursue",
        json={"run_agents": False},
        headers=_headers(ctx),
    )
    assert again.status_code == 200
    assert again.json()["created"] is False
    assert again.json()["pursuit"]["id"] == pursuit["id"]

    async with database.session(ctx["tenant_id"]) as session:
        rows = (await session.execute(select(Pursuit))).scalars().all()
        assert len(rows) == 1
        audits = (
            (await session.execute(select(AuditLog).where(AuditLog.action == "pursuit.pursued")))
            .scalars()
            .all()
        )
        assert len(audits) == 2


async def test_pursue_enqueues_the_pipeline_and_the_free_plan_does_not(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database)
    response = await api_client.post(
        f"/api/v1/opportunities/{ctx['opportunity_id']}/pursue", json={}, headers=_headers(ctx)
    )
    assert response.status_code == 201, response.text
    agents = response.json()["agents"]
    # tests run Celery eager, so the run row is created and left queued (OQ-82)
    assert agents["run_id"] is not None
    assert agents["reason"] == "celery eager: run left queued"
    async with database.session(ctx["tenant_id"]) as session:
        run = await session.get(AgentRun, uuid.UUID(agents["run_id"]))
        assert run is not None
        assert run.kind == "pipeline"
        assert run.params["step"] == "all"
        assert run.status == "queued"

    free = await _setup(database, plan=Plan.FREE)
    response = await api_client.post(
        f"/api/v1/opportunities/{free['opportunity_id']}/pursue", json={}, headers=_headers(free)
    )
    assert response.status_code == 201
    agents = response.json()["agents"]
    assert agents == {
        "enqueued": False,
        "run_id": None,
        "task_id": None,
        "reason": "the free plan has no agent budget",
    }
    async with database.session(free["tenant_id"]) as session:
        assert (await session.execute(select(AgentRun))).scalars().all() == []


async def test_watch_tracks_without_starting_any_agent(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database)
    response = await api_client.post(
        f"/api/v1/opportunities/{ctx['opportunity_id']}/watch",
        json={},
        headers=_headers(ctx, Role.WRITER),
    )
    assert response.status_code == 201, response.text
    assert response.json()["agents"] is None
    pursuit_id = uuid.UUID(response.json()["pursuit"]["id"])
    row = await _pursuit(database, pursuit_id, ctx["tenant_id"])
    assert row.watch is True
    assert row.stage == "identified"
    async with database.session(ctx["tenant_id"]) as session:
        assert (await session.execute(select(AgentRun))).scalars().all() == []

    # pursuing afterwards clears the watch flag
    await api_client.post(
        f"/api/v1/opportunities/{ctx['opportunity_id']}/pursue",
        json={"run_agents": False},
        headers=_headers(ctx),
    )
    assert (await _pursuit(database, pursuit_id, ctx["tenant_id"])).watch is False


async def test_pass_cancels_with_a_reason_and_no_bid_needs_a_decision_stage(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database)
    response = await api_client.post(
        f"/api/v1/opportunities/{ctx['opportunity_id']}/pass",
        json={"reason": "no past performance in this domain"},
        headers=_headers(ctx),
    )
    assert response.status_code == 201, response.text
    pursuit = response.json()["pursuit"]
    assert pursuit["stage"] == "cancelled"
    pursuit_id = uuid.UUID(pursuit["id"])
    assert (
        await _pursuit(database, pursuit_id, ctx["tenant_id"])
    ).pass_reason == "no past performance in this domain"

    # a formal no-bid is only available from qualifying / bid_decision
    await _set(database, pursuit_id, ctx["tenant_id"], stage="qualifying", pass_reason=None)
    response = await api_client.post(
        f"/api/v1/opportunities/{ctx['opportunity_id']}/pass",
        json={"reason": "margin too thin", "no_bid": True},
        headers=_headers(ctx),
    )
    assert response.status_code == 200
    assert response.json()["pursuit"]["stage"] == "no_bid"

    # from drafting it is a cancellation, not a no-bid
    await _set(database, pursuit_id, ctx["tenant_id"], stage="drafting", decision=DECISION_BID)
    response = await api_client.post(
        f"/api/v1/opportunities/{ctx['opportunity_id']}/pass",
        json={"reason": "buyer cancelled", "no_bid": True},
        headers=_headers(ctx),
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error"] == "stage_transition"
    assert detail["from"] == "drafting"
    assert detail["to"] == "no_bid"
    assert "qualifying or bid_decision" in detail["reason"]

    # an empty reason is refused
    assert (
        await api_client.post(
            f"/api/v1/opportunities/{ctx['opportunity_id']}/pass",
            json={"reason": ""},
            headers=_headers(ctx),
        )
    ).status_code == 422


async def test_pursuing_a_passed_notice_reopens_it(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database)
    passed = await api_client.post(
        f"/api/v1/opportunities/{ctx['opportunity_id']}/pass",
        json={"reason": "wrong NAICS"},
        headers=_headers(ctx),
    )
    pursuit_id = uuid.UUID(passed.json()["pursuit"]["id"])
    reopened = await api_client.post(
        f"/api/v1/opportunities/{ctx['opportunity_id']}/pursue",
        json={"run_agents": False},
        headers=_headers(ctx),
    )
    assert reopened.status_code == 200
    assert reopened.json()["pursuit"]["stage"] == "qualifying"
    assert (await _pursuit(database, pursuit_id, ctx["tenant_id"])).pass_reason is None


async def test_a_tenant_with_several_profiles_must_name_one(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database, profiles=2)
    response = await api_client.post(
        f"/api/v1/opportunities/{ctx['opportunity_id']}/pursue",
        json={"run_agents": False},
        headers=_headers(ctx),
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error"] == "profile_required"
    assert sorted(detail["profile_ids"]) == sorted(str(p) for p in ctx["profile_ids"])

    ok = await api_client.post(
        f"/api/v1/opportunities/{ctx['opportunity_id']}/pursue",
        json={"run_agents": False, "profile_id": str(ctx["profile_ids"][1])},
        headers=_headers(ctx),
    )
    assert ok.status_code == 201
    assert ok.json()["pursuit"]["profile_id"] == str(ctx["profile_ids"][1])


async def test_unknown_opportunity_and_profile_are_404(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database)
    missing = await api_client.post(
        f"/api/v1/opportunities/{uuid.uuid4()}/pursue",
        json={"run_agents": False},
        headers=_headers(ctx),
    )
    assert missing.status_code == 404
    other = await api_client.post(
        f"/api/v1/opportunities/{ctx['opportunity_id']}/pursue",
        json={"run_agents": False, "profile_id": str(uuid.uuid4())},
        headers=_headers(ctx),
    )
    assert other.status_code == 404


async def test_board_actions_are_role_gated(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database)
    writer = _headers(ctx, Role.WRITER)
    assert (
        await api_client.post(
            f"/api/v1/opportunities/{ctx['opportunity_id']}/pursue",
            json={"run_agents": False},
            headers=writer,
        )
    ).status_code == 403
    assert (
        await api_client.post(
            f"/api/v1/opportunities/{ctx['opportunity_id']}/pass",
            json={"reason": "nope"},
            headers=writer,
        )
    ).status_code == 403
    # a writer may watch
    assert (
        await api_client.post(
            f"/api/v1/opportunities/{ctx['opportunity_id']}/watch", json={}, headers=writer
        )
    ).status_code == 201
    viewer = auth_headers(user_id=ctx["writer_id"], tenant_id=ctx["tenant_id"], role=Role.VIEWER)
    assert (
        await api_client.post(
            f"/api/v1/opportunities/{ctx['opportunity_id']}/watch", json={}, headers=viewer
        )
    ).status_code == 403


async def test_patch_walks_the_ladder_and_gate_1_answers_409(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database)
    created = await api_client.post(
        f"/api/v1/opportunities/{ctx['opportunity_id']}/pursue",
        json={"run_agents": False},
        headers=_headers(ctx),
    )
    pursuit_id = created.json()["pursuit"]["id"]
    url = f"/api/v1/pursuits/{pursuit_id}"

    for target in ("qualifying", "bid_decision"):
        response = await api_client.patch(url, json={"stage": target}, headers=_headers(ctx))
        assert response.status_code == 200, response.text
        assert response.json()["stage"] == target

    blocked = await api_client.patch(url, json={"stage": "drafting"}, headers=_headers(ctx))
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == {
        "error": "stage_transition",
        "from": "bid_decision",
        "to": "drafting",
        "reason": GATE_1_REASON,
    }

    # Gate 1 approved (the decision endpoint itself belongs to the bid/no-bid agent)
    await _set(
        database,
        uuid.UUID(pursuit_id),
        ctx["tenant_id"],
        decision=DECISION_BID,
        decided_by=ctx["owner_id"],
        decided_at=datetime.now(UTC),
    )
    allowed = await api_client.patch(url, json={"stage": "drafting"}, headers=_headers(ctx))
    assert allowed.status_code == 200
    assert allowed.json()["stage"] == "drafting"

    skipped = await api_client.patch(url, json={"stage": "submitted"}, headers=_headers(ctx))
    assert skipped.status_code == 409
    assert skipped.json()["detail"]["reason"] == "cannot skip in_review"

    for target in ("in_review", "final_approval", "submitted"):
        assert (
            await api_client.patch(url, json={"stage": target}, headers=_headers(ctx))
        ).status_code == 200
    row = await _pursuit(database, uuid.UUID(pursuit_id), ctx["tenant_id"])
    assert row.submitted_at is not None

    won = await api_client.patch(url, json={"stage": "awarded"}, headers=_headers(ctx))
    assert won.status_code == 200
    assert won.json()["stage"] == "awarded"


async def test_patch_rejects_an_unknown_stage_and_a_writers_backwards_drag(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database)
    created = await api_client.post(
        f"/api/v1/opportunities/{ctx['opportunity_id']}/pursue",
        json={"run_agents": False},
        headers=_headers(ctx),
    )
    pursuit_id = created.json()["pursuit"]["id"]
    url = f"/api/v1/pursuits/{pursuit_id}"
    assert (
        await api_client.patch(url, json={"stage": "shortlisted"}, headers=_headers(ctx))
    ).status_code == 422

    await api_client.patch(url, json={"stage": "qualifying"}, headers=_headers(ctx))
    back = await api_client.patch(
        url, json={"stage": "identified"}, headers=_headers(ctx, Role.WRITER)
    )
    assert back.status_code == 409
    assert back.json()["detail"]["reason"] == "only a bid manager may move a pursuit backwards"
    assert (
        await api_client.patch(url, json={"stage": "identified"}, headers=_headers(ctx))
    ).status_code == 200


async def test_patch_assigns_an_owner_and_sets_the_internal_deadline(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database)
    created = await api_client.post(
        f"/api/v1/opportunities/{ctx['opportunity_id']}/pursue",
        json={"run_agents": False},
        headers=_headers(ctx),
    )
    url = f"/api/v1/pursuits/{created.json()['pursuit']['id']}"
    moved = DUE - timedelta(days=3)
    response = await api_client.patch(
        url,
        json={"owner_user_id": str(ctx["writer_id"]), "internal_due_at": moved.isoformat()},
        headers=_headers(ctx),
    )
    assert response.status_code == 200, response.text
    assert response.json()["owner_user_id"] == str(ctx["writer_id"])
    assert datetime.fromisoformat(response.json()["internal_due_at"]) == moved

    # a writer may move their own card but may not reassign it
    refused = await api_client.patch(
        url, json={"owner_user_id": str(ctx["owner_id"])}, headers=_headers(ctx, Role.WRITER)
    )
    assert refused.status_code == 403
    assert (
        await api_client.patch(
            url, json={"owner_user_id": str(uuid.uuid4())}, headers=_headers(ctx)
        )
    ).status_code == 404


async def test_list_filters_and_groups_the_board(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database)
    async with database.owner_session(ctx["tenant_id"]) as session:
        second = await _notice(
            session,
            title="Cloud migration",
            region=Region.IN,
            currency="INR",
            source_tz="Asia/Kolkata",
            response_due_at=DUE + timedelta(days=20),
            estimated_value_max_usd=Decimal("50000.00"),
        )
        third = await _notice(
            session, title="Training services", response_due_at=None, estimated_value_max_usd=None
        )
        other_user = make_user()
        session.add(other_user)
        await session.flush()
        session.add(
            Membership(tenant_id=ctx["tenant_id"], user_id=other_user.id, role=Role.BID_MANAGER)
        )
        for opportunity in (second, third):
            session.add(
                Pursuit(
                    tenant_id=ctx["tenant_id"],
                    profile_id=ctx["profile_id"],
                    opportunity_id=opportunity.id,
                    owner_user_id=other_user.id,
                    stage="qualifying",
                )
            )
        await session.flush()
        other_id = other_user.id

    await api_client.post(
        f"/api/v1/opportunities/{ctx['opportunity_id']}/pursue",
        json={"run_agents": False},
        headers=_headers(ctx),
    )

    page = (await api_client.get("/api/v1/pursuits", headers=_headers(ctx))).json()
    assert page["total"] == 3
    assert page["by_stage"] == {"identified": 1, "qualifying": 2}
    # soonest deadline first, undated last
    assert [item["title"] for item in page["items"]] == [
        "Helpdesk support services",
        "Cloud migration",
        "Training services",
    ]
    first = page["items"][0]
    assert first["response_due_at"]["buyer_tz"] == "America/New_York"
    assert "EDT" in first["response_due_at"]["display"]
    assert first["internal_due_at"] is not None
    assert first["buyer_org"] == "Internal Revenue Service"

    filtered = (
        await api_client.get(
            "/api/v1/pursuits", params={"owner": str(other_id)}, headers=_headers(ctx)
        )
    ).json()
    assert filtered["total"] == 2
    assert filtered["by_stage"] == {"qualifying": 2}

    by_stage = (
        await api_client.get(
            "/api/v1/pursuits", params={"stage": "identified"}, headers=_headers(ctx)
        )
    ).json()
    assert by_stage["total"] == 1

    by_region = (
        await api_client.get("/api/v1/pursuits", params={"region": "in"}, headers=_headers(ctx))
    ).json()
    assert [item["title"] for item in by_region["items"]] == ["Cloud migration"]

    by_value = (
        await api_client.get(
            "/api/v1/pursuits", params={"min_value": "100000"}, headers=_headers(ctx)
        )
    ).json()
    assert [item["title"] for item in by_value["items"]] == ["Helpdesk support services"]

    by_due = (
        await api_client.get(
            "/api/v1/pursuits",
            params={"due_before": (DUE + timedelta(days=1)).isoformat()},
            headers=_headers(ctx),
        )
    ).json()
    assert [item["title"] for item in by_due["items"]] == ["Helpdesk support services"]

    bad = await api_client.get(
        "/api/v1/pursuits", params={"stage": "shortlisted"}, headers=_headers(ctx)
    )
    assert bad.status_code == 422


async def test_list_and_patch_are_tenant_scoped(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    a = await _setup(database)
    b = await _setup(database)
    created = await api_client.post(
        f"/api/v1/opportunities/{a['opportunity_id']}/pursue",
        json={"run_agents": False},
        headers=_headers(a),
    )
    pursuit_id = created.json()["pursuit"]["id"]
    assert (await api_client.get("/api/v1/pursuits", headers=_headers(b))).json()["total"] == 0
    foreign = await api_client.patch(
        f"/api/v1/pursuits/{pursuit_id}", json={"stage": "qualifying"}, headers=_headers(b)
    )
    assert foreign.status_code == 404


async def test_the_stage_check_constraint_guards_the_table(
    database: Database,
) -> None:
    ctx = await _setup(database)
    async with database.owner_session(ctx["tenant_id"]) as session:
        session.add(
            Pursuit(
                tenant_id=ctx["tenant_id"],
                profile_id=ctx["profile_id"],
                opportunity_id=ctx["opportunity_id"],
                stage="shortlisted",
            )
        )
        with pytest.raises(IntegrityError, match="ck_pursuits_stage"):
            await session.flush()
        await session.rollback()


async def test_the_board_reads_for_every_tenant_role(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database)
    await api_client.post(
        f"/api/v1/opportunities/{ctx['opportunity_id']}/pursue",
        json={"run_agents": False},
        headers=_headers(ctx),
    )
    async with database.session(ctx["tenant_id"]) as session:
        assert (await session.get(User, ctx["owner_id"])) is not None
    for role in (Role.BID_MANAGER, Role.WRITER, Role.REVIEWER, Role.VIEWER):
        headers = auth_headers(user_id=ctx["writer_id"], tenant_id=ctx["tenant_id"], role=role)
        response = await api_client.get("/api/v1/pursuits", headers=headers)
        assert response.status_code == 200, role
        assert response.json()["total"] == 1
