"""Pursuit pipeline job (SPEC 8, 10.1): run or resume one agent run.

    result = await run_agents_job(run_id, tenant_id)          # inline (API fallback, tests)
    run_agents_sync(str(run_id), str(tenant_id))              # Celery task body
    enqueue_agents(run_id, tenant_id)                         # None when no broker answers

The run's params carry `step` (collect | extract | ... | all); the pipeline registry turns
it into the implemented StepSpecs and the finish status (paused at the first unimplemented
step). The cost guard pauses the run `needs_approval` before a step that would cross the
tenant's monthly budget or the pursuit's cap; approve-budget re-enqueues the same run id.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import structlog

from app.agents.cost_guard import LedgerCostGuard
from app.agents.llm import LLMClient, llm_from_settings
from app.agents.pipeline import STEP_ALL, plan_steps
from app.agents.runner import AgentRunner, RunResult
from app.agents.services import AgentServices, services_from_settings
from app.agents.tracing import Tracer, tracer_from_settings
from app.core.config import Settings, get_settings
from app.core.db import Database, get_database
from app.models import AgentRun, Pursuit
from app.models.agents import RUN_FAILED
from app.services.pursuits import cleared_gates

log = structlog.get_logger(__name__)


class NoLLM:
    """Stand-in when ANTHROPIC_API_KEY is missing: LLM steps fail loudly, others run."""

    async def complete_json(self, **kwargs: Any) -> Any:
        raise RuntimeError("no LLM configured (ANTHROPIC_API_KEY)")

    async def complete_text(self, **kwargs: Any) -> Any:
        raise RuntimeError("no LLM configured (ANTHROPIC_API_KEY)")


def result_summary(result: RunResult) -> dict[str, Any]:
    return {
        "run_id": str(result.run_id),
        "status": result.status,
        "executed": list(result.executed),
        "skipped": list(result.skipped),
        "failed_step": result.failed_step,
        "paused_step": result.paused_step,
        "pause_reason": result.pause_reason,
        "gate": result.gate,
        "error": result.error,
        "cost_usd": str(result.cost_usd),
    }


async def run_agents_job(
    run_id: uuid.UUID,
    tenant_id: uuid.UUID,
    *,
    database: Database | None = None,
    settings: Settings | None = None,
    llm: LLMClient | None = None,
    services: AgentServices | None = None,
    tracer: Tracer | None = None,
    guard: LedgerCostGuard | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    db = database or get_database()
    async with db.session(tenant_id) as session:
        run = await session.get(AgentRun, run_id)
        if run is None:
            raise LookupError(f"agent run {run_id} not found for tenant {tenant_id}")
        step = str((run.params or {}).get("step") or STEP_ALL)
        # Human gates: the pursuit's own state says which ones a person has cleared
        # (an approved bid decision clears Gate 1), so a resumed run plans past them.
        pursuit = None if run.pursuit_id is None else await session.get(Pursuit, run.pursuit_id)
        gates = () if pursuit is None else cleared_gates(pursuit)
    try:
        specs, finish = plan_steps(step, gates_cleared=gates)
    except ValueError as exc:
        async with db.session(tenant_id) as session:
            run = await session.get(AgentRun, run_id)
            assert run is not None
            run.status, run.error = RUN_FAILED, str(exc)
        raise
    client = llm or llm_from_settings(settings) or NoLLM()
    svc = services or services_from_settings(settings)
    runner = AgentRunner(
        db,
        tenant_id=tenant_id,
        llm=client,
        tracer=tracer or tracer_from_settings(settings),
        guard=guard or LedgerCostGuard(settings),
        services=svc,
    )
    try:
        result = await runner.run(
            run_id,
            specs,
            finish_status=finish.status,
            finish_reason=finish.reason,
            finish_gate=finish.gate,
        )
    finally:
        if services is None:
            svc.close()
    summary = result_summary(result)
    log.info("agents.run_finished", **summary)
    return summary


async def _run_with_fresh_database(run_id: uuid.UUID, tenant_id: uuid.UUID) -> dict[str, Any]:
    settings = get_settings()
    db = Database(settings.database_url, settings.database_url_owner)
    try:
        return await run_agents_job(run_id, tenant_id, database=db, settings=settings)
    finally:
        await db.dispose()


def run_agents_sync(run_id: str, tenant_id: str) -> dict[str, Any]:
    return asyncio.run(_run_with_fresh_database(uuid.UUID(run_id), uuid.UUID(tenant_id)))


def enqueue_agents(run_id: uuid.UUID, tenant_id: uuid.UUID) -> str | None:
    """Queue the Celery task; None when no broker answers (caller runs inline instead)."""
    from kombu.exceptions import OperationalError

    from app.celery_app import run_agents_task

    try:
        async_result = run_agents_task.apply_async(
            args=[str(run_id), str(tenant_id)], retry=False, expires=3600
        )
    except (OperationalError, OSError, ConnectionError) as exc:
        log.warning("celery.broker_unreachable", error=str(exc)[:200])
        return None
    task_id: str = str(async_result.id)
    return task_id
