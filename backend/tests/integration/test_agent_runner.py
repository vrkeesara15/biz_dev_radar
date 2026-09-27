"""M5-01: durable runs/steps, resume after a crash, usage_ledger rows, RLS."""

import uuid
from decimal import Decimal
from typing import Any

import pytest
from app.agents.llm import InvalidOutput
from app.agents.runner import AgentRunner, StepContext, StepSpec
from app.core.db import Database
from app.core.plan import LLM_COST_MICROUSD, LLM_TOKENS_IN, LLM_TOKENS_OUT
from app.models import AgentRun, AgentStep, UsageLedger
from pydantic import BaseModel
from sqlalchemy import select

from tests.factories import create_tenant_with_owner
from tests.llm_fake import FakeLLM


class Out(BaseModel):
    value: str


class Crash(BaseException):
    """Simulates the worker process dying mid-step (not an Exception: nothing catches it)."""


async def _tenant(database: Database) -> uuid.UUID:
    async with database.owner_session() as session:
        tenant, _, _ = await create_tenant_with_owner(session)
        return tenant.id


async def _rows(
    database: Database, tenant_id: uuid.UUID, run_id: uuid.UUID
) -> tuple[Any, list[Any], list[Any]]:
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
        ledger = (
            (
                await session.execute(
                    select(UsageLedger).order_by(UsageLedger.created_at, UsageLedger.metric)
                )
            )
            .scalars()
            .all()
        )
        return run, list(steps), list(ledger)


def _llm_step(text: str) -> Any:
    async def fn(ctx: StepContext) -> Out:
        result = await ctx.llm.complete_json(
            model="claude-fake", system="S", messages=[ctx.message("user", text)], schema=Out
        )
        return result.parsed  # type: ignore[no-any-return]

    return fn


async def test_run_persists_steps_outputs_usage_and_ledger(
    database: Database, fake_llm: FakeLLM
) -> None:
    tenant_id = await _tenant(database)
    fake_llm.prices = {"claude-fake": fake_llm.prices[next(iter(fake_llm.prices))]}
    fake_llm.tokens_in, fake_llm.tokens_out = 1000, 100
    fake_llm.queue({"value": "one"}, {"value": "two"})
    runner = AgentRunner(database, tenant_id=tenant_id, llm=fake_llm)
    run_id = await runner.start(kind="test", params={"opportunity_id": "abc"})

    async def uses_previous(ctx: StepContext) -> dict[str, Any]:
        assert ctx.outputs["first"] == {"value": "one"} and ctx.params == {"opportunity_id": "abc"}
        return {"joined": ctx.outputs["first"]["value"] + "+plain"}

    result = await runner.run(
        run_id,
        [
            StepSpec("first", _llm_step("a"), input_ref="opp:abc"),
            StepSpec("second", uses_previous),
            StepSpec("third", _llm_step("b")),
        ],
    )
    assert result.status == "done" and result.executed == ["first", "second", "third"]
    assert result.outputs == {
        "first": {"value": "one"},
        "second": {"joined": "one+plain"},
        "third": {"value": "two"},
    }
    run, steps, ledger = await _rows(database, tenant_id, run_id)
    assert run.status == "done" and run.started_at and run.finished_at and run.error is None
    assert run.tokens_in == 2000 and run.tokens_out == 200
    assert [(s.agent, s.attempt, s.status) for s in steps] == [
        ("first", 1, "done"),
        ("second", 1, "done"),
        ("third", 1, "done"),
    ]
    first = steps[0]
    assert (
        first.input_ref == "opp:abc"
        and first.output == {"value": "one"}
        and first.model == "claude-fake"
    )
    assert first.tokens_in == 1000 and first.tokens_out == 100 and first.cost_usd > 0
    assert steps[1].tokens_in == 0 and steps[1].cost_usd == 0 and steps[1].model is None
    assert Decimal(run.cost_usd) == first.cost_usd + steps[2].cost_usd == result.cost_usd
    # ledger: 3 rows per LLM-using step, none for the plain step
    by_ref: dict[str, dict[str, int]] = {}
    for row in ledger:
        by_ref.setdefault(row.ref, {})[row.metric] = row.quantity
    assert set(by_ref) == {f"agent_step:{first.id}", f"agent_step:{steps[2].id}"}
    assert by_ref[f"agent_step:{first.id}"] == {
        LLM_TOKENS_IN: 1000,
        LLM_TOKENS_OUT: 100,
        LLM_COST_MICROUSD: int(first.cost_usd * 1_000_000),
    }
    assert all(row.period.count("-") == 1 for row in ledger)  # monthly
    # re-running a finished run does nothing
    again = await runner.run(run_id, [StepSpec("first", _llm_step("a"))])
    assert again.status == "done" and again.skipped == ["first"] and len(fake_llm.calls) == 2


async def test_resume_after_crash_skips_completed_steps(
    database: Database, fake_llm: FakeLLM
) -> None:
    tenant_id = await _tenant(database)
    fake_llm.prices = {"claude-fake": next(iter(fake_llm.prices.values()))}
    fake_llm.queue({"value": "one"}, {"value": "two"}, {"value": "three"})
    runner = AgentRunner(database, tenant_id=tenant_id, llm=fake_llm)
    run_id = await runner.start(kind="pipeline")
    calls = {"first": 0, "second": 0, "third": 0}
    crash_once = {"armed": True}

    def counting(name: str, inner: Any) -> Any:
        async def fn(ctx: StepContext) -> Any:
            calls[name] += 1
            if name == "second" and crash_once["armed"]:
                crash_once["armed"] = False
                raise Crash()
            return await inner(ctx)

        return fn

    steps = [
        StepSpec("first", counting("first", _llm_step("a"))),
        StepSpec("second", counting("second", _llm_step("b"))),
        StepSpec("third", counting("third", _llm_step("c"))),
    ]
    with pytest.raises(Crash):
        await runner.run(run_id, steps)
    run, rows, _ = await _rows(database, tenant_id, run_id)
    assert run.status == "running"  # the worker died: nothing closed the run
    assert [(s.agent, s.status) for s in rows] == [("first", "done"), ("second", "running")]

    # a new worker picks the run up
    result = await AgentRunner(database, tenant_id=tenant_id, llm=fake_llm).run(run_id, steps)
    assert result.status == "done"
    assert result.skipped == ["first"] and result.executed == ["second", "third"]
    assert calls == {"first": 1, "second": 2, "third": 1}
    run, rows, _ledger = await _rows(database, tenant_id, run_id)
    assert [(s.agent, s.attempt, s.status) for s in rows] == [
        ("first", 1, "done"),
        ("second", 1, "running"),  # the orphaned attempt keeps its history
        ("second", 2, "done"),
        ("third", 1, "done"),
    ]
    assert result.outputs == {
        "first": {"value": "one"},
        "second": {"value": "two"},
        "third": {"value": "three"},
    }
    assert run.tokens_in == 3000  # only the three successful LLM calls were made


async def test_failed_step_marks_step_and_run_failed_and_stops(
    database: Database, fake_llm: FakeLLM
) -> None:
    tenant_id = await _tenant(database)
    fake_llm.prices = {"claude-fake": next(iter(fake_llm.prices.values()))}
    fake_llm.queue({"value": "one"}, {"bad": 1}, {"bad": 2}, {"bad": 3})
    runner = AgentRunner(database, tenant_id=tenant_id, llm=fake_llm)
    run_id = await runner.start(kind="pipeline")
    third_ran = {"yes": False}

    async def third(ctx: StepContext) -> None:
        third_ran["yes"] = True

    result = await runner.run(
        run_id,
        [
            StepSpec("first", _llm_step("a")),
            StepSpec("extract", _llm_step("b")),
            StepSpec("third", third),
        ],
    )
    assert result.status == "failed" and result.failed_step == "extract"
    assert (
        result.error and result.error.startswith("InvalidOutput") and "3 attempts" in result.error
    )
    assert not third_ran["yes"]
    run, rows, ledger = await _rows(database, tenant_id, run_id)
    assert run.status == "failed" and run.error == result.error and run.finished_at
    assert [(s.agent, s.status) for s in rows] == [("first", "done"), ("extract", "failed")]
    failed = rows[1]
    assert failed.error == result.error and failed.output is None
    # the failed attempts are still paid for and metered
    assert failed.tokens_in == 3000 and failed.cost_usd > 0
    assert {r.ref for r in ledger} == {f"agent_step:{rows[0].id}", f"agent_step:{failed.id}"}
    with pytest.raises(InvalidOutput):
        await fake_llm.queue({"bad": 1}, {"bad": 1}, {"bad": 1}).complete_json(
            model="claude-fake", system="S", messages=[], schema=Out
        )


async def test_runs_and_steps_are_tenant_isolated(database: Database, fake_llm: FakeLLM) -> None:
    tenant_a, tenant_b = await _tenant(database), await _tenant(database)
    fake_llm.prices = {"claude-fake": next(iter(fake_llm.prices.values()))}
    fake_llm.queue({"value": "a"})
    runner = AgentRunner(database, tenant_id=tenant_a, llm=fake_llm)
    run_id = await runner.start(kind="k")
    await runner.run(run_id, [StepSpec("first", _llm_step("x"))])
    async with database.session(tenant_b) as session:
        assert await session.get(AgentRun, run_id) is None
        assert (await session.execute(select(AgentStep))).scalars().all() == []
        assert (await session.execute(select(UsageLedger))).scalars().all() == []
    with pytest.raises(LookupError):
        await AgentRunner(database, tenant_id=tenant_b, llm=fake_llm).run(run_id, [])
    async with database.session(tenant_a) as session:
        assert len((await session.execute(select(AgentStep))).scalars().all()) == 1
