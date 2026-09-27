"""Durable agent runner (SPEC 8, 10.1): agent_runs / agent_steps persisted around every
step so a crashed worker resumes from the last completed step.

    runner = AgentRunner(database, tenant_id=tenant.id, llm=llm)
    run_id = await runner.start(kind="summary", params={"opportunity_id": ...})
    result = await runner.run(run_id, [StepSpec("summarize", summarize_step)])

A step function receives a StepContext (metered LLM client, previous outputs, params,
a tenant session) and returns a Pydantic model / dict / None, stored as the step's
`output` jsonb. Every step:

- is inserted as `running` and COMMITTED before the work starts (attempt = 1 + earlier
  attempts of the same agent in this run),
- is marked `done` with tokens/cost and its output, or `failed` with the error (the run
  fails and later steps do not run),
- writes usage_ledger rows (llm_tokens_in, llm_tokens_out, llm_cost_microusd,
  ref agent_step:<id>) and calls the tracer.

`run()` skips steps whose latest row is already `done` and re-executes the rest, so a
worker that died mid-step (row left `running`) simply retries that step.

Cost guard (SPEC 8, M5-02): when a `guard` is given, it is asked BEFORE every step with
the step's projected cost; a blocking decision leaves the run `needs_approval` with
`pause_reason` set and `params["paused_at"]` naming the step, and nothing is spent.
`finish_status=RUN_PAUSED` ends a run of the implemented steps as `paused` (gates,
unimplemented steps) instead of `done`.
"""

from __future__ import annotations

import traceback
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

import structlog
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.llm import CacheBlock, InvalidOutput, LLMClient, LLMResult, Message
from app.agents.services import AgentServices
from app.agents.tracing import NoopTracer, Tracer
from app.core.cost_guard import BudgetDecision, StepEstimate
from app.core.db import Database, get_database
from app.core.llm_cost import to_microusd
from app.core.plan import LLM_COST_MICROUSD, LLM_TOKENS_IN, LLM_TOKENS_OUT, period_key
from app.models import AgentRun, AgentStep, UsageLedger
from app.models.agents import (
    RUN_DONE,
    RUN_FAILED,
    RUN_NEEDS_APPROVAL,
    RUN_PAUSED,
    RUN_QUEUED,
    RUN_RUNNING,
    STEP_DONE,
    STEP_FAILED,
    STEP_RUNNING,
)

log = structlog.get_logger(__name__)


class MeteredLLM:
    """Wraps an LLMClient and accumulates usage for the current step."""

    def __init__(self, inner: LLMClient) -> None:
        self.inner = inner
        self.results: list[LLMResult] = []

    def _track(self, result: LLMResult) -> LLMResult:
        self.results.append(result)
        return result

    async def complete_json(self, **kwargs: Any) -> LLMResult:
        try:
            return self._track(await self.inner.complete_json(**kwargs))
        except InvalidOutput as exc:
            if exc.usage is not None:  # failed attempts are still paid for
                self._track(exc.usage)
            raise

    async def complete_text(self, **kwargs: Any) -> LLMResult:
        return self._track(await self.inner.complete_text(**kwargs))

    @property
    def tokens_in(self) -> int:
        return sum(r.tokens_in for r in self.results)

    @property
    def tokens_out(self) -> int:
        return sum(r.tokens_out for r in self.results)

    @property
    def cache_read_tokens(self) -> int:
        return sum(r.cache_read_tokens for r in self.results)

    @property
    def cost_usd(self) -> Decimal:
        return sum((r.cost_usd for r in self.results), Decimal(0))

    @property
    def model(self) -> str | None:
        return self.results[-1].model if self.results else None


@dataclass(slots=True)
class StepContext:
    run: AgentRun
    step: AgentStep
    session: AsyncSession
    llm: MeteredLLM
    outputs: dict[str, Any]  # agent name -> output of earlier done steps
    params: dict[str, Any]
    tenant_id: uuid.UUID
    # storage, scanner, OCR, HTTP client factory (None for LLM-only steps such as summary)
    services: AgentServices | None = None

    def cache_block(self, text: str, ttl: str | None = None) -> CacheBlock:
        return CacheBlock(text=text, ttl=ttl)

    def message(self, role: str, content: str) -> Message:
        return {"role": role, "content": content}

    def require_services(self) -> AgentServices:
        if self.services is None:
            raise RuntimeError(f"step {self.step.agent!r} needs AgentServices on the runner")
        return self.services


@dataclass(slots=True)
class GuardContext:
    """What a step's cost estimator and the guard see before the step row exists."""

    session: AsyncSession
    run: AgentRun
    tenant_id: uuid.UUID
    outputs: dict[str, Any]
    params: dict[str, Any]
    services: AgentServices | None = None


StepFn = Callable[[StepContext], Awaitable[Any]]
Estimator = Callable[[GuardContext], Awaitable[StepEstimate | None]]


class StepGuard(Protocol):
    """Decides before a step whether it may spend (app.agents.cost_guard.LedgerCostGuard)."""

    async def check(self, ctx: GuardContext, spec: StepSpec) -> BudgetDecision | None: ...


@dataclass(frozen=True, slots=True)
class StepSpec:
    agent: str
    fn: StepFn
    input_ref: str | None = None
    # projected spend for the guard; None = the step costs nothing (no LLM)
    estimate: Estimator | None = None


@dataclass(slots=True)
class RunResult:
    run_id: uuid.UUID
    status: str
    executed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed_step: str | None = None
    error: str | None = None
    outputs: dict[str, Any] = field(default_factory=dict)
    cost_usd: Decimal = Decimal(0)
    tokens_in: int = 0
    tokens_out: int = 0
    # set when the guard stopped the run before `paused_step` (status needs_approval) or
    # when the caller asked for a paused finish (gates / unimplemented steps)
    paused_step: str | None = None
    pause_reason: str | None = None
    decision: BudgetDecision | None = None


def _jsonable(output: Any) -> Any:
    if output is None:
        return None
    if isinstance(output, BaseModel):
        return output.model_dump(mode="json")
    if isinstance(output, Mapping):
        return dict(output)
    if isinstance(output, list | tuple):
        return list(output)
    return {"value": output}


class AgentRunner:
    def __init__(
        self,
        database: Database | None = None,
        *,
        tenant_id: uuid.UUID,
        llm: LLMClient,
        tracer: Tracer | None = None,
        now: Callable[[], datetime] | None = None,
        guard: StepGuard | None = None,
        services: AgentServices | None = None,
    ) -> None:
        self.database = database or get_database()
        self.tenant_id = tenant_id
        self.llm = llm
        self.tracer = tracer or NoopTracer()
        self._now = now or (lambda: datetime.now(UTC))
        self.guard = guard
        self.services = services

    # -- lifecycle ------------------------------------------------------------------
    async def start(
        self,
        *,
        kind: str,
        pursuit_id: uuid.UUID | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> uuid.UUID:
        async with self.database.session(self.tenant_id) as session:
            run = AgentRun(
                tenant_id=self.tenant_id,
                kind=kind,
                pursuit_id=pursuit_id,
                status=RUN_QUEUED,
                params=dict(params or {}),
            )
            session.add(run)
            await session.flush()
            return run.id

    async def run(
        self,
        run_id: uuid.UUID,
        steps: Sequence[StepSpec],
        *,
        finish_status: str = RUN_DONE,
        finish_reason: str | None = None,
    ) -> RunResult:
        """Execute the not-yet-done steps in order. `finish_status` (done or paused) is the
        status once every given step is done; `finish_reason` explains a paused finish."""
        if finish_status not in (RUN_DONE, RUN_PAUSED):
            raise ValueError("finish_status must be done or paused")
        async with self.database.session(self.tenant_id) as session:
            run = await session.get(AgentRun, run_id)
            if run is None:
                raise LookupError(f"agent run {run_id} not found for tenant {self.tenant_id}")
            if run.status == RUN_DONE:
                return RunResult(run_id, RUN_DONE, skipped=[s.agent for s in steps])
            run.status = RUN_RUNNING
            run.started_at = run.started_at or self._now()
            run.error = None
            run.pause_reason = None
            done_outputs = await self._done_outputs(session, run_id)
            attempts = await self._attempts(session, run_id)
            params = {k: v for k, v in (run.params or {}).items() if k != "paused_at"}
        result = RunResult(run_id, RUN_RUNNING, outputs=dict(done_outputs))

        for spec in steps:
            if spec.agent in done_outputs:
                result.skipped.append(spec.agent)
                continue
            block = await self._guard_check(spec, run_id, result, params)
            if block is not None:
                result.paused_step = spec.agent
                result.pause_reason = block.reason
                result.decision = block
                log.warning(
                    "agent.needs_approval",
                    run_id=str(run_id),
                    agent=spec.agent,
                    reason=block.reason,
                )
                break
            ok = await self._execute(spec, run_id, result, attempts.get(spec.agent, 0) + 1, params)
            result.executed.append(spec.agent)
            if not ok:
                break

        async with self.database.session(self.tenant_id) as session:
            run = await session.get(AgentRun, run_id)
            assert run is not None
            if result.failed_step is not None:
                run.status = RUN_FAILED
                run.error = result.error
                run.finished_at = self._now()
            elif result.paused_step is not None:
                run.status = RUN_NEEDS_APPROVAL
                run.pause_reason = result.pause_reason
                run.params = {**params, "paused_at": result.paused_step}
            elif finish_status == RUN_PAUSED:
                run.status = RUN_PAUSED
                run.pause_reason = finish_reason
                run.params = params
                result.pause_reason = finish_reason
            else:
                run.status = RUN_DONE
                run.params = params
                run.finished_at = self._now()
            result.status = run.status
            result.cost_usd = Decimal(run.cost_usd)
            result.tokens_in, result.tokens_out = run.tokens_in, run.tokens_out
        self.tracer.flush()
        return result

    async def _guard_check(
        self, spec: StepSpec, run_id: uuid.UUID, result: RunResult, params: dict[str, Any]
    ) -> BudgetDecision | None:
        if self.guard is None:
            return None
        async with self.database.session(self.tenant_id) as session:
            run = await session.get(AgentRun, run_id)
            assert run is not None
            ctx = GuardContext(
                session=session,
                run=run,
                tenant_id=self.tenant_id,
                outputs=dict(result.outputs),
                params=params,
                services=self.services,
            )
            return await self.guard.check(ctx, spec)

    # -- internals ----------------------------------------------------------------
    async def _done_outputs(self, session: AsyncSession, run_id: uuid.UUID) -> dict[str, Any]:
        rows = (
            await session.execute(
                select(AgentStep)
                .where(AgentStep.run_id == run_id, AgentStep.status == STEP_DONE)
                .order_by(AgentStep.created_at.asc())
            )
        ).scalars()
        return {row.agent: row.output for row in rows}

    async def _attempts(self, session: AsyncSession, run_id: uuid.UUID) -> dict[str, int]:
        rows = (
            await session.execute(
                select(AgentStep.agent, AgentStep.attempt).where(AgentStep.run_id == run_id)
            )
        ).all()
        out: dict[str, int] = {}
        for agent, attempt in rows:
            out[agent] = max(out.get(agent, 0), attempt)
        return out

    async def _execute(
        self,
        spec: StepSpec,
        run_id: uuid.UUID,
        result: RunResult,
        attempt: int,
        params: dict[str, Any],
    ) -> bool:
        async with self.database.session(self.tenant_id) as session:
            run = await session.get(AgentRun, run_id)
            assert run is not None
            step = AgentStep(
                tenant_id=self.tenant_id,
                run_id=run_id,
                agent=spec.agent,
                attempt=attempt,
                input_ref=spec.input_ref,
                status=STEP_RUNNING,
                started_at=self._now(),
            )
            session.add(step)
            await session.commit()  # durable before any LLM call (resume point)
            self.tracer.step_started(
                run_id=str(run_id), step_id=str(step.id), agent=spec.agent, attempt=attempt
            )
            metered = MeteredLLM(self.llm)
            ctx = StepContext(
                run=run,
                step=step,
                session=session,
                llm=metered,
                outputs=dict(result.outputs),
                params=params,
                tenant_id=self.tenant_id,
                services=self.services,
            )
            try:
                output = _jsonable(await spec.fn(ctx))
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"[:2000]
                log.error("agent.step_failed", run_id=str(run_id), agent=spec.agent, error=error)
                log.debug("agent.step_trace", trace=traceback.format_exc())
                await session.rollback()  # discard the step's partial writes
                await session.refresh(step)
                await session.refresh(run)
                self._finish_step(step, run, metered, STEP_FAILED, error=error)
                await self._ledger(session, step, metered)
                await session.commit()
                result.failed_step, result.error = spec.agent, error
                return False
            step.output = output
            self._finish_step(step, run, metered, STEP_DONE)
            await self._ledger(session, step, metered)
            await session.commit()
            result.outputs[spec.agent] = output
            return True

    def _finish_step(
        self,
        step: AgentStep,
        run: AgentRun,
        metered: MeteredLLM,
        status: str,
        *,
        error: str | None = None,
    ) -> None:
        step.status = status
        step.error = error
        step.finished_at = self._now()
        step.model = metered.model
        step.tokens_in = metered.tokens_in
        step.tokens_out = metered.tokens_out
        step.cache_read_tokens = metered.cache_read_tokens
        step.cost_usd = metered.cost_usd
        run.tokens_in += metered.tokens_in
        run.tokens_out += metered.tokens_out
        run.cost_usd = Decimal(run.cost_usd) + metered.cost_usd
        self.tracer.step_finished(
            run_id=str(run.id),
            step_id=str(step.id),
            agent=step.agent,
            status=status,
            model=step.model,
            tokens_in=step.tokens_in,
            tokens_out=step.tokens_out,
            cost_usd=float(step.cost_usd),
            error=error,
        )

    async def _ledger(self, session: AsyncSession, step: AgentStep, metered: MeteredLLM) -> None:
        if not metered.results:
            return
        now = self._now()
        ref = f"agent_step:{step.id}"
        for metric, quantity in (
            (LLM_TOKENS_IN, metered.tokens_in),
            (LLM_TOKENS_OUT, metered.tokens_out),
            (LLM_COST_MICROUSD, to_microusd(metered.cost_usd)),
        ):
            session.add(
                UsageLedger(
                    tenant_id=self.tenant_id,
                    metric=metric,
                    quantity=quantity,
                    period=period_key(metric, now),
                    ref=ref,
                )
            )
        await session.flush()
