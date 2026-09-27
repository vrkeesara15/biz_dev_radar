"""Server-side plan limit enforcement (SPEC section 3).

    svc = PlanService(session)              # tenant-bound app-role session
    await svc.check(tenant, Resource.PROFILES)          # raises PlanLimitExceeded
    await svc.consume(tenant, Resource.AGENT_DRAFTS_PER_MONTH, ref=str(run_id))

Limits come from plan_limits (falling back to app.core.plan.PLAN_DEFAULTS); usage
comes from usage_ledger for the resource's period, unless a counter is registered
for the resource (e.g. M1 counts company_profiles rows directly).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.plan import (
    LimitCheck,
    Plan,
    PlanLimitExceeded,
    Resource,
    check_limit,
    default_limit,
    effective_limit,
    period_key,
)
from app.models import PlanLimit, Tenant, UsageLedger

UsageCounter = Callable[[AsyncSession, uuid.UUID], Awaitable[int]]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class PlanService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        counters: Mapping[str, UsageCounter] | None = None,
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.session = session
        self.counters = dict(counters or {})
        self.now = now

    async def limit_for(self, tenant: Tenant, resource: Resource | str) -> int | None:
        """Effective limit for the tenant (None = unlimited). Internal tenants: unlimited."""
        resource_name = str(Resource(resource).value) if resource in Resource else str(resource)
        row = (
            await self.session.execute(
                select(PlanLimit.limit_value).where(
                    PlanLimit.plan == Plan(tenant.plan), PlanLimit.resource == resource_name
                )
            )
        ).one_or_none()
        if row is None:
            return effective_limit(
                tenant.plan,
                resource_name,
                is_internal=tenant.is_internal,
                configured=default_limit(tenant.plan, resource_name),
            )
        return effective_limit(
            tenant.plan, resource_name, is_internal=tenant.is_internal, configured=row[0]
        )

    async def usage(self, tenant_id: uuid.UUID, resource: Resource | str) -> int:
        resource_name = str(resource)
        counter = self.counters.get(resource_name)
        if counter is not None:
            return int(await counter(self.session, tenant_id))
        total = (
            await self.session.execute(
                select(func.coalesce(func.sum(UsageLedger.quantity), 0)).where(
                    UsageLedger.tenant_id == tenant_id,
                    UsageLedger.metric == resource_name,
                    UsageLedger.period == period_key(resource_name, self.now()),
                )
            )
        ).scalar_one()
        return int(total)

    async def check(
        self, tenant: Tenant, resource: Resource | str, requested: int = 1
    ) -> LimitCheck:
        """Raise PlanLimitExceeded if `requested` more units would exceed the limit."""
        resource_name = str(resource)
        limit = await self.limit_for(tenant, resource_name)
        used = 0 if limit is None else await self.usage(tenant.id, resource_name)
        result = check_limit(resource_name, limit, used, requested)
        if not result.allowed:
            raise PlanLimitExceeded(str(tenant.plan), result)
        return result

    async def record(
        self,
        tenant_id: uuid.UUID,
        resource: Resource | str,
        quantity: int = 1,
        *,
        ref: str | None = None,
    ) -> UsageLedger:
        """Append a usage_ledger row for the resource's current period."""
        resource_name = str(resource)
        row = UsageLedger(
            tenant_id=tenant_id,
            metric=resource_name,
            quantity=quantity,
            period=period_key(resource_name, self.now()),
            ref=ref,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def consume(
        self,
        tenant: Tenant,
        resource: Resource | str,
        requested: int = 1,
        *,
        ref: str | None = None,
    ) -> UsageLedger:
        """check() then record(); the caller's transaction makes the pair atomic."""
        await self.check(tenant, resource, requested)
        return await self.record(tenant.id, resource, requested, ref=ref)
