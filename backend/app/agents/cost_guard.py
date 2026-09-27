"""Ledger-backed cost guard for AgentRunner (SPEC 8, M5-02).

    runner = AgentRunner(db, tenant_id=..., llm=llm, guard=LedgerCostGuard(settings))

Before each step the guard reads what the tenant spent this month (usage_ledger
llm_cost_microusd), the tenant's plan budget (plan_limits agent_budget_usd_month; None =
unlimited, internal tenants unlimited), what the pursuit's runs have spent so far
(agent_runs.cost_usd) and the pursuit's cap (pursuits.cost_cap_usd or the tenant default),
projects the step's cost through `StepSpec.estimate` and returns the first blocking
decision. The pure arithmetic lives in app.core.cost_guard.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.runner import GuardContext, StepSpec
from app.core.config import Settings, get_settings
from app.core.cost_guard import (
    BudgetDecision,
    check_budget,
    check_pursuit_cap,
    first_block,
    project_cost,
)
from app.core.llm_cost import MICRO, ModelPrice, parse_prices
from app.core.plan import LLM_COST_MICROUSD, Resource
from app.models import AgentRun, Pursuit, Tenant
from app.services.plan import PlanService


@dataclass(frozen=True, slots=True)
class CostSnapshot:
    """What GET /pursuits/{id} shows: spend so far against the two limits."""

    pursuit_cost_usd: Decimal
    pursuit_cap_usd: Decimal
    month_spent_usd: Decimal
    month_budget_usd: Decimal | None  # None = unlimited

    @property
    def month_remaining_usd(self) -> Decimal | None:
        if self.month_budget_usd is None:
            return None
        return max(self.month_budget_usd - self.month_spent_usd, Decimal(0))


async def month_spent_usd(
    session: AsyncSession, tenant_id: uuid.UUID, now: Callable[[], datetime]
) -> Decimal:
    micro = await PlanService(session, now=now).usage(tenant_id, LLM_COST_MICROUSD)
    return Decimal(micro) / MICRO


async def month_budget_usd(session: AsyncSession, tenant: Tenant) -> Decimal | None:
    limit = await PlanService(session).limit_for(tenant, Resource.AGENT_BUDGET_USD_MONTH)
    return None if limit is None else Decimal(limit)


async def pursuit_spent_usd(session: AsyncSession, pursuit_id: uuid.UUID) -> Decimal:
    total = (
        await session.execute(
            select(func.coalesce(func.sum(AgentRun.cost_usd), 0)).where(
                AgentRun.pursuit_id == pursuit_id
            )
        )
    ).scalar_one()
    return Decimal(total)


def effective_cap(pursuit: Pursuit, tenant: Tenant) -> Decimal:
    if pursuit.cost_cap_usd is not None:
        return Decimal(pursuit.cost_cap_usd)
    return Decimal(tenant.pursuit_cost_cap_usd)


async def cost_snapshot(
    session: AsyncSession,
    pursuit: Pursuit,
    tenant: Tenant,
    *,
    now: Callable[[], datetime] | None = None,
) -> CostSnapshot:
    clock = now or (lambda: datetime.now(UTC))
    return CostSnapshot(
        pursuit_cost_usd=await pursuit_spent_usd(session, pursuit.id),
        pursuit_cap_usd=effective_cap(pursuit, tenant),
        month_spent_usd=await month_spent_usd(session, tenant.id, clock),
        month_budget_usd=await month_budget_usd(session, tenant),
    )


class LedgerCostGuard:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        prices: Mapping[str, ModelPrice] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.prices = dict(prices) if prices is not None else parse_prices(self.settings.llm_prices)
        self._now = now or (lambda: datetime.now(UTC))

    async def projected(self, ctx: GuardContext, spec: StepSpec) -> Decimal:
        if spec.estimate is None:
            return Decimal(0)
        estimate = await spec.estimate(ctx)
        if estimate is None:
            return Decimal(0)
        return project_cost(estimate, self.prices)

    async def check(self, ctx: GuardContext, spec: StepSpec) -> BudgetDecision | None:
        tenant = await ctx.session.get(Tenant, ctx.tenant_id)
        if tenant is None:  # RLS hid the tenant: never spend blind
            raise LookupError(f"tenant {ctx.tenant_id} not visible to the cost guard")
        projected = await self.projected(ctx, spec)
        decisions = [
            check_budget(
                await month_spent_usd(ctx.session, tenant.id, self._now),
                await month_budget_usd(ctx.session, tenant),
                projected,
            )
        ]
        if ctx.run.pursuit_id is not None:
            pursuit = await ctx.session.get(Pursuit, ctx.run.pursuit_id)
            if pursuit is not None:
                decisions.append(
                    check_pursuit_cap(
                        await pursuit_spent_usd(ctx.session, pursuit.id),
                        effective_cap(pursuit, tenant),
                        projected,
                    )
                )
        return first_block(decisions)
