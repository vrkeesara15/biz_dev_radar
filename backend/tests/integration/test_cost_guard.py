"""M5-02: tenant monthly budget + per-pursuit cap pause a run before a step spends;
approve-budget raises the cap and resumes; GET /pursuits/{id} shows the cost meter."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from app.agents import pipeline
from app.agents.cost_guard import LedgerCostGuard, cost_snapshot
from app.agents.runner import AgentRunner, GuardContext, StepContext, StepSpec
from app.core.config import Region, Settings
from app.core.cost_guard import StepEstimate
from app.core.db import Database
from app.core.opportunity import NoticeType
from app.core.plan import LLM_COST_MICROUSD, Plan, period_key
from app.core.roles import Role
from app.models import (
    AgentRun,
    AgentStep,
    AuditLog,
    CompanyProfile,
    Opportunity,
    Pursuit,
    Tenant,
    UsageLedger,
)
from pydantic import BaseModel
from sqlalchemy import select

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner
from tests.llm_fake import FakeLLM

MODEL = "claude-fake"
# the API path builds its guard from Settings prices, so the in-process step uses a real id
OPUS = Settings(_env_file=None).llm_model_opus_class  # type: ignore[call-arg]


class Out(BaseModel):
    value: str


async def _estimate(ctx: GuardContext) -> StepEstimate:
    # 4,000 chars -> 1,000 tokens in, 100 out: exactly what FakeLLM bills per call
    return StepEstimate(model=MODEL, input_chars=4000, output_tokens=100)


async def _estimate_opus(ctx: GuardContext) -> StepEstimate:
    return StepEstimate(model=OPUS, input_chars=4000, output_tokens=100)


def _llm_step(text: str) -> Any:
    async def fn(ctx: StepContext) -> Out:
        result = await ctx.llm.complete_json(
            model=MODEL, system="S", messages=[ctx.message("user", text)], schema=Out
        )
        return result.parsed  # type: ignore[no-any-return]

    return fn


def _steps() -> list[StepSpec]:
    return [
        StepSpec("first", _llm_step("a"), estimate=_estimate),
        StepSpec("second", _llm_step("b"), estimate=_estimate),
        StepSpec("third", _llm_step("c"), estimate=_estimate),
    ]


def _price(fake_llm: FakeLLM) -> None:
    fake_llm.prices = {MODEL: fake_llm.prices[next(iter(fake_llm.prices))]}  # opus prices
    fake_llm.tokens_in, fake_llm.tokens_out = 1000, 100  # 0.0075 USD per call


async def _setup(
    database: Database, *, plan: Plan = Plan.PRO, cap: Decimal | None = None
) -> dict[str, Any]:
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(session, plan=plan)
        profile = CompanyProfile(tenant_id=tenant.id, region=Region.US, legal_name="Guard LLC")
        opp = Opportunity(
            source_id="sam_opps",
            external_id=f"guard-{uuid.uuid4().hex[:6]}",
            region=Region.US,
            country="US",
            currency="USD",
            notice_type=NoticeType.RFP,
            title="Guarded notice",
        )
        session.add_all([profile, opp])
        await session.flush()
        pursuit = Pursuit(
            tenant_id=tenant.id,
            profile_id=profile.id,
            opportunity_id=opp.id,
            created_by=user.id,
            cost_cap_usd=cap,
        )
        session.add(pursuit)
        await session.flush()
        return {
            "tenant_id": tenant.id,
            "user_id": user.id,
            "email": user.email,
            "profile_id": profile.id,
            "opportunity_id": opp.id,
            "pursuit_id": pursuit.id,
        }


async def _run_rows(database: Database, tenant_id: uuid.UUID, run_id: uuid.UUID) -> Any:
    async with database.session(tenant_id) as session:
        run = await session.get(AgentRun, run_id)
        steps = (
            (
                await session.execute(
                    select(AgentStep)
                    .where(AgentStep.run_id == run_id)
                    .order_by(AgentStep.created_at)
                )
            )
            .scalars()
            .all()
        )
        return run, list(steps)


async def test_pursuit_cap_crossed_mid_run_pauses_then_resumes_after_approval(
    database: Database, fake_llm: FakeLLM
) -> None:
    _price(fake_llm)
    ctx = await _setup(database, cap=Decimal("0.02"))  # room for two 0.0075 steps, not three
    fake_llm.queue({"value": "one"}, {"value": "two"}, {"value": "three"})
    guard = LedgerCostGuard(prices=fake_llm.prices)
    runner = AgentRunner(database, tenant_id=ctx["tenant_id"], llm=fake_llm, guard=guard)
    run_id = await runner.start(kind="pipeline", pursuit_id=ctx["pursuit_id"], params={"step": "x"})

    result = await runner.run(run_id, _steps())
    assert result.status == "needs_approval"
    assert result.executed == ["first", "second"] and result.paused_step == "third"
    assert result.pause_reason and "pursuit cap" in result.pause_reason
    assert "0.02 USD" in result.pause_reason and result.decision is not None
    assert result.decision.scope == "pursuit" and result.decision.spent == Decimal("0.015")
    assert len(fake_llm.calls) == 2  # nothing was spent on the blocked step
    run, steps = await _run_rows(database, ctx["tenant_id"], run_id)
    assert run.status == "needs_approval" and run.pause_reason == result.pause_reason
    assert run.params == {"step": "x", "paused_at": "third"} and run.finished_at is None
    assert [s.agent for s in steps] == ["first", "second"]
    assert Decimal(run.cost_usd) == Decimal("0.015")

    # snapshot as the API shows it
    async with database.session(ctx["tenant_id"]) as session:
        pursuit = await session.get(Pursuit, ctx["pursuit_id"])
        tenant = await session.get(Tenant, ctx["tenant_id"])
        assert pursuit is not None and tenant is not None
        snap = await cost_snapshot(session, pursuit, tenant)
        assert snap.pursuit_cost_usd == Decimal("0.015") and snap.pursuit_cap_usd == Decimal("0.02")
        assert snap.month_budget_usd == Decimal(50) and snap.month_spent_usd == Decimal("0.015")
        assert snap.month_remaining_usd == Decimal("49.985")
        # approval raises the cap by 1 USD
        from app.services.pursuits import approve_budget

        assert approve_budget(pursuit, tenant, Decimal(1)) == Decimal("1.02")

    again = await AgentRunner(database, tenant_id=ctx["tenant_id"], llm=fake_llm, guard=guard).run(
        run_id, _steps()
    )
    assert again.status == "done" and again.skipped == ["first", "second"]
    assert again.executed == ["third"] and again.outputs["third"] == {"value": "three"}
    run, steps = await _run_rows(database, ctx["tenant_id"], run_id)
    assert run.status == "done" and run.pause_reason is None and "paused_at" not in run.params
    assert [s.agent for s in steps] == ["first", "second", "third"]
    assert Decimal(run.cost_usd) == Decimal("0.0225")


async def test_free_plan_blocks_the_first_paid_step_but_not_free_steps(
    database: Database, fake_llm: FakeLLM
) -> None:
    _price(fake_llm)
    ctx = await _setup(database, plan=Plan.FREE)
    runner = AgentRunner(
        database,
        tenant_id=ctx["tenant_id"],
        llm=fake_llm,
        guard=LedgerCostGuard(prices=fake_llm.prices),
    )

    async def free_step(step_ctx: StepContext) -> dict[str, Any]:
        return {"collected": 1}

    run_id = await runner.start(kind="pipeline", pursuit_id=ctx["pursuit_id"])
    result = await runner.run(
        run_id,
        [StepSpec("collect", free_step), StepSpec("extract", _llm_step("a"), estimate=_estimate)],
    )
    assert result.executed == ["collect"] and result.status == "needs_approval"
    assert result.paused_step == "extract" and result.pause_reason
    assert "tenant monthly budget" in result.pause_reason and "0.00 USD" in result.pause_reason
    assert fake_llm.calls == []


async def test_month_budget_counts_earlier_ledger_spend(
    database: Database, fake_llm: FakeLLM
) -> None:
    _price(fake_llm)
    ctx = await _setup(database)  # pro: 50 USD / month
    now = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
    async with database.session(ctx["tenant_id"]) as session:
        session.add(
            UsageLedger(
                tenant_id=ctx["tenant_id"],
                metric=LLM_COST_MICROUSD,
                quantity=49_995_000,  # 49.995 USD already spent this month
                period=period_key(LLM_COST_MICROUSD, now),
                ref="earlier",
            )
        )
    guard = LedgerCostGuard(prices=fake_llm.prices, now=lambda: now)
    runner = AgentRunner(
        database, tenant_id=ctx["tenant_id"], llm=fake_llm, guard=guard, now=lambda: now
    )
    run_id = await runner.start(kind="pipeline", pursuit_id=ctx["pursuit_id"])
    result = await runner.run(run_id, _steps())
    assert result.status == "needs_approval" and result.executed == []
    assert result.decision is not None and result.decision.scope == "tenant_month"
    assert result.decision.spent == Decimal("49.995") and result.decision.limit == Decimal(50)
    # a run without a pursuit (e.g. summary_ai) is still held to the tenant budget
    solo = await runner.start(kind="summary_ai")
    assert (await runner.run(solo, _steps())).status == "needs_approval"
    # unlimited tenants (enterprise) never pause on the monthly budget
    async with database.owner_session() as session:
        tenant = await session.get(Tenant, ctx["tenant_id"])
        assert tenant is not None
        tenant.plan = Plan.ENTERPRISE
    fake_llm.queue({"value": "one"}, {"value": "two"}, {"value": "three"})
    assert (await runner.run(run_id, _steps())).status == "done"


# --- API -------------------------------------------------------------------------------


@pytest.fixture()
def fake_collect(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Register a temporary 'collect' step that spends one FakeLLM call."""
    calls = {"n": 0}

    async def collect(ctx: StepContext) -> Out:
        calls["n"] += 1
        result = await ctx.llm.complete_json(
            model=OPUS, system="S", messages=[ctx.message("user", "x")], schema=Out
        )
        return result.parsed  # type: ignore[no-any-return]

    monkeypatch.setattr(pipeline, "_loaded", True)
    monkeypatch.setattr(
        pipeline,
        "_REGISTRY",
        {"collect": pipeline.StepDef("collect", collect, estimate=_estimate_opus)},
    )
    return calls


async def test_pursuit_api_cost_meter_and_approve_budget_resume(
    app: Any,
    api_client: httpx.AsyncClient,
    database: Database,
    fake_llm: FakeLLM,
    fake_collect: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_llm.tokens_in, fake_llm.tokens_out = 1000, 100  # 0.0075 USD per opus-class call
    app.state.llm = fake_llm
    ctx = await _setup(database, cap=Decimal("0.00"))  # an explicit zero cap: no paid step
    owner = auth_headers(user_id=ctx["user_id"], tenant_id=ctx["tenant_id"], email=ctx["email"])
    manager = auth_headers(user_id=uuid.uuid4(), tenant_id=ctx["tenant_id"], role=Role.BID_MANAGER)
    writer = auth_headers(user_id=uuid.uuid4(), tenant_id=ctx["tenant_id"], role=Role.WRITER)
    viewer = auth_headers(user_id=uuid.uuid4(), tenant_id=ctx["tenant_id"], role=Role.VIEWER)

    # create is idempotent per (profile, opportunity)
    body = {"profile_id": str(ctx["profile_id"]), "opportunity_id": str(ctx["opportunity_id"])}
    resp = await api_client.post("/api/v1/pursuits", json=body, headers=owner)
    assert resp.status_code == 200 and resp.json()["id"] == str(ctx["pursuit_id"])
    other = await api_client.post(
        "/api/v1/pursuits", json={**body, "profile_id": str(uuid.uuid4())}, headers=owner
    )
    assert other.status_code == 404
    assert (await api_client.post("/api/v1/pursuits", json=body, headers=writer)).status_code == 403

    resp = await api_client.get(f"/api/v1/pursuits/{ctx['pursuit_id']}", headers=viewer)
    assert resp.status_code == 200
    data = resp.json()
    assert data["cost_so_far_usd"] == "0" and data["cost_cap_usd"] == "0.00"
    assert data["budget_month_limit_usd"] == "50" and data["budget_month_remaining_usd"] == "50"
    assert data["run"] is None and data["stage"] == "identified"

    # a run the guard paused before spending
    runner = AgentRunner(
        database, tenant_id=ctx["tenant_id"], llm=fake_llm, guard=LedgerCostGuard()
    )
    specs, finish = pipeline.plan_steps("collect")
    run_id = await runner.start(
        kind="pipeline", pursuit_id=ctx["pursuit_id"], params={"step": "collect"}
    )
    paused = await runner.run(run_id, specs, finish_status=finish.status)
    assert paused.status == "needs_approval" and fake_collect["n"] == 0
    resp = await api_client.get(f"/api/v1/pursuits/{ctx['pursuit_id']}", headers=viewer)
    run = resp.json()["run"]
    assert run["id"] == str(run_id) and run["status"] == "needs_approval"
    assert run["paused_at"] == "collect" and "pursuit cap" in run["pause_reason"]
    assert run["step"] == "collect"

    # writers may not approve; bid managers may. No broker in tests: force the inline path.
    approve = {"additional_usd": "5", "reason": "worth it"}
    url = f"/api/v1/pursuits/{ctx['pursuit_id']}/agents/approve-budget"
    assert (await api_client.post(url, json=approve, headers=writer)).status_code == 403
    monkeypatch.setattr("app.jobs.run_agents.enqueue_agents", lambda run_id, tenant_id: None)
    fake_llm.queue({"value": "collected"})
    resp = await api_client.post(url, json=approve, headers=manager)
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["previous_cap_usd"] == "0.00" and out["new_cap_usd"] == "5.00"
    assert out["resumed_run_id"] == str(run_id) and out["mode"] == "inline"
    assert out["result"]["status"] == "done" and out["result"]["executed"] == ["collect"]
    assert fake_collect["n"] == 1
    meter = out["pursuit"]
    assert meter["cost_cap_usd"] == "5.00" and Decimal(meter["cost_so_far_usd"]) == Decimal(
        "0.0075"
    )
    assert meter["run"]["status"] == "done" and meter["run"]["pause_reason"] is None
    assert Decimal(meter["budget_month_remaining_usd"]) == Decimal("49.9925")

    # the approval is recorded (who, how much, which run)
    async with database.session(ctx["tenant_id"]) as session:
        rows = (
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "pursuit.budget_approved")
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1 and rows[0].object_id == str(ctx["pursuit_id"])
    assert rows[0].meta["additional_usd"] == "5" and rows[0].meta["run_id"] == str(run_id)
    assert rows[0].meta["new_cap_usd"] == "5.00"

    # approving again with nothing to resume just raises the cap
    resp = await api_client.post(url, json={"additional_usd": "1"}, headers=owner)
    assert resp.status_code == 200 and resp.json()["mode"] == "none"
    assert resp.json()["new_cap_usd"] == "6.00" and resp.json()["resumed_run_id"] is None
    # tenant B never sees the pursuit
    async with database.owner_session() as session:
        tenant_b, user_b, _ = await create_tenant_with_owner(session)
    other_headers = auth_headers(user_id=user_b.id, tenant_id=tenant_b.id)
    assert (
        await api_client.get(f"/api/v1/pursuits/{ctx['pursuit_id']}", headers=other_headers)
    ).status_code == 404
    assert (await api_client.post(url, json=approve, headers=other_headers)).status_code == 404
