"""Queries behind the platform-admin console (SPEC 10.3 admin routes, 10.4 screen 9).

Every function here takes the owner-role admin session (app.api.deps.get_admin_session),
which bypasses RLS: that is the whole point of the console, and every use of it is
already written to audit_log by the dependency. Nothing here reads tenant *content*;
the only tenant-scoped read is the audit log, which needs an explicit support-access
grant (app.api.v1.admin.require_support_access).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin import (
    grant_is_active,
    period_bounds,
    support_access_expiry,
    usd_from_microusd,
    worst_health,
)
from app.core.config import Region, Settings, StorageBackend
from app.core.plan import LLM_COST_MICROUSD, LLM_TOKENS_IN, LLM_TOKENS_OUT, Plan, default_limit
from app.core.plan import Resource as PlanResource
from app.models import (
    AgentRun,
    AuditLog,
    BillingCustomer,
    CompanyProfile,
    Membership,
    PlanLimit,
    Source,
    SourceRun,
    SupportAccessGrant,
    Tenant,
    UsageLedger,
)
from app.services.storage import bucket_for_region

USAGE_METRICS = (LLM_TOKENS_IN, LLM_TOKENS_OUT, LLM_COST_MICROUSD)


# --- tenants --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TenantRow:
    tenant: Tenant
    member_count: int
    profile_count: int


def _tenant_filters(q: str | None, include_deleted: bool) -> list[ColumnElement[bool]]:
    criteria: list[ColumnElement[bool]] = []
    if not include_deleted:
        criteria.append(Tenant.deleted_at.is_(None))
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        criteria.append(or_(func.lower(Tenant.name).like(like), func.lower(Tenant.slug).like(like)))
    return criteria


async def list_tenants(
    session: AsyncSession,
    *,
    q: str | None = None,
    page: int = 1,
    page_size: int = 25,
    include_deleted: bool = False,
) -> tuple[list[TenantRow], int]:
    """One page of tenants with their member and profile counts."""
    criteria = _tenant_filters(q, include_deleted)
    total = (
        await session.execute(select(func.count()).select_from(Tenant).where(*criteria))
    ).scalar_one()
    members = (
        select(Membership.tenant_id, func.count().label("n"))
        .group_by(Membership.tenant_id)
        .subquery()
    )
    profiles = (
        select(CompanyProfile.tenant_id, func.count().label("n"))
        .group_by(CompanyProfile.tenant_id)
        .subquery()
    )
    stmt = (
        select(
            Tenant,
            func.coalesce(members.c.n, 0),
            func.coalesce(profiles.c.n, 0),
        )
        .outerjoin(members, members.c.tenant_id == Tenant.id)
        .outerjoin(profiles, profiles.c.tenant_id == Tenant.id)
        .where(*criteria)
        .order_by(Tenant.slug)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await session.execute(stmt)).all()
    return [TenantRow(tenant=r[0], member_count=r[1], profile_count=r[2]) for r in rows], total


async def get_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> TenantRow | None:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        return None
    members = (
        await session.execute(
            select(func.count()).select_from(Membership).where(Membership.tenant_id == tenant_id)
        )
    ).scalar_one()
    profiles = (
        await session.execute(
            select(func.count())
            .select_from(CompanyProfile)
            .where(CompanyProfile.tenant_id == tenant_id)
        )
    ).scalar_one()
    return TenantRow(tenant=tenant, member_count=members, profile_count=profiles)


async def plan_limits_for(session: AsyncSession, plan: Plan) -> dict[str, int | None]:
    """Effective plan_limits rows for a plan, falling back to the static defaults."""
    rows = (await session.execute(select(PlanLimit).where(PlanLimit.plan == plan))).scalars().all()
    limits = {row.resource: row.limit_value for row in rows}
    for resource in PlanResource:
        limits.setdefault(resource.value, default_limit(plan, resource))
    return limits


async def billing_status(session: AsyncSession, tenant_id: uuid.UUID) -> dict[str, Any] | None:
    """Provider subscription state for a tenant; never any customer-identifying id."""
    customer = (
        await session.execute(select(BillingCustomer).where(BillingCustomer.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if customer is None:
        return None
    return {
        "provider": customer.provider,
        "status": customer.status,
        "plan": customer.plan.value if customer.plan else None,
        "current_period_end": customer.current_period_end,
        "has_subscription": customer.subscription_id is not None,
    }


# --- usage and LLM cost ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UsageRow:
    tenant_id: uuid.UUID
    slug: str
    name: str
    plan: Plan
    region: Region
    tokens_in: int = 0
    tokens_out: int = 0
    cost_microusd: int = 0
    agent_runs: int = 0
    # SPEC 10.4 screen 9 also wants notifications sent per tenant; the notifications
    # table arrives with M4 (notify milestone). Nothing to count until then.
    notifications: int | None = None

    @property
    def cost_usd(self) -> Decimal:
        return usd_from_microusd(self.cost_microusd)


async def usage_by_tenant(session: AsyncSession, period: str) -> list[UsageRow]:
    """Tokens, LLM cost and agent-run counts per tenant for one 'YYYY-MM' period."""
    ledger = (
        select(
            UsageLedger.tenant_id,
            UsageLedger.metric,
            func.sum(UsageLedger.quantity).label("total"),
        )
        .where(UsageLedger.period == period, UsageLedger.metric.in_(USAGE_METRICS))
        .group_by(UsageLedger.tenant_id, UsageLedger.metric)
    )
    totals: dict[uuid.UUID, dict[str, int]] = {}
    for tenant_id, metric, total in (await session.execute(ledger)).all():
        totals.setdefault(tenant_id, {})[metric] = int(total or 0)

    start, end = period_bounds(period)
    runs_stmt = (
        select(AgentRun.tenant_id, func.count())
        .where(AgentRun.created_at >= start, AgentRun.created_at < end)
        .group_by(AgentRun.tenant_id)
    )
    runs = {tid: int(n) for tid, n in (await session.execute(runs_stmt)).all()}

    tenants = (await session.execute(select(Tenant).order_by(Tenant.slug))).scalars().all()
    rows = [
        UsageRow(
            tenant_id=t.id,
            slug=t.slug,
            name=t.name,
            plan=t.plan,
            region=t.region,
            tokens_in=totals.get(t.id, {}).get(LLM_TOKENS_IN, 0),
            tokens_out=totals.get(t.id, {}).get(LLM_TOKENS_OUT, 0),
            cost_microusd=totals.get(t.id, {}).get(LLM_COST_MICROUSD, 0),
            agent_runs=runs.get(t.id, 0),
        )
        for t in tenants
    ]
    # Most expensive first: the console's cost chart reads top-down.
    rows.sort(key=lambda r: (-r.cost_microusd, -r.agent_runs, r.slug))
    return rows


async def tenant_usage(session: AsyncSession, tenant_id: uuid.UUID, period: str) -> UsageRow | None:
    for row in await usage_by_tenant(session, period):
        if row.tenant_id == tenant_id:
            return row
    return None


# --- source runs ----------------------------------------------------------------------


async def source_runs(
    session: AsyncSession, source_id: str, *, page: int = 1, page_size: int = 25
) -> tuple[list[SourceRun], int]:
    total = (
        await session.execute(
            select(func.count()).select_from(SourceRun).where(SourceRun.source_id == source_id)
        )
    ).scalar_one()
    rows = (
        (
            await session.execute(
                select(SourceRun)
                .where(SourceRun.source_id == source_id)
                .order_by(SourceRun.started_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return list(rows), total


# --- support access -------------------------------------------------------------------


async def create_support_grant(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    admin_user_id: uuid.UUID,
    reason: str,
    minutes: int | None,
    now: datetime | None = None,
) -> SupportAccessGrant:
    granted_at = now or datetime.now(UTC)
    grant = SupportAccessGrant(
        target_tenant_id=tenant_id,
        admin_user_id=admin_user_id,
        reason=reason.strip(),
        granted_at=granted_at,
        expires_at=support_access_expiry(granted_at, minutes),
    )
    session.add(grant)
    await session.flush()
    return grant


async def active_support_grant(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    admin_user_id: uuid.UUID,
    now: datetime | None = None,
) -> SupportAccessGrant | None:
    """The admin's newest unexpired, unrevoked grant into `tenant_id`, if any."""
    moment = now or datetime.now(UTC)
    grant = (
        await session.execute(
            select(SupportAccessGrant)
            .where(
                and_(
                    SupportAccessGrant.target_tenant_id == tenant_id,
                    SupportAccessGrant.admin_user_id == admin_user_id,
                    SupportAccessGrant.revoked_at.is_(None),
                    SupportAccessGrant.expires_at > moment,
                )
            )
            .order_by(SupportAccessGrant.expires_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if grant is None:
        return None
    return grant if grant_is_active(grant.expires_at, grant.revoked_at, moment) else None


async def tenant_audit_log(
    session: AsyncSession, tenant_id: uuid.UUID, *, page: int = 1, page_size: int = 50
) -> tuple[list[AuditLog], int]:
    total = (
        await session.execute(
            select(func.count()).select_from(AuditLog).where(AuditLog.tenant_id == tenant_id)
        )
    ).scalar_one()
    rows = (
        (
            await session.execute(
                select(AuditLog)
                .where(AuditLog.tenant_id == tenant_id)
                .order_by(AuditLog.at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return list(rows), total


# --- system health --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    status: str  # ok | degraded | failing | unconfigured
    detail: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


async def check_database(session: AsyncSession) -> Check:
    try:
        await session.execute(select(1))
    except Exception as exc:  # pragma: no cover - only on a broken database
        return Check(name="database", status="failing", detail=str(exc)[:200])
    return Check(name="database", status="ok")


def check_broker(settings: Settings) -> Check:
    """Can the Celery broker be reached? Never raises; a broker outage is 'failing'."""
    try:
        from kombu import Connection

        with Connection(
            settings.redis_url, connect_timeout=settings.celery_broker_connect_timeout
        ) as connection:
            connection.ensure_connection(
                max_retries=0, timeout=settings.celery_broker_connect_timeout
            )
    except Exception as exc:
        return Check(name="broker", status="failing", detail=str(exc)[:200])
    return Check(name="broker", status="ok")


def check_storage(settings: Settings) -> Check:
    buckets = {r.value: bucket_for_region(settings, r) for r in Region}
    backend = settings.storage_backend
    missing = [region for region, bucket in buckets.items() if not bucket]
    if missing:
        return Check(
            name="storage",
            status="unconfigured",
            detail=f"no bucket configured for {', '.join(missing)}",
            meta={"backend": backend.value, "buckets": buckets},
        )
    status = "degraded" if backend is StorageBackend.LOCAL else "ok"
    detail = "local filesystem backend (development only)" if status == "degraded" else None
    return Check(
        name="storage", status=status, detail=detail, meta={"backend": backend.value, **buckets}
    )


async def adapter_health(session: AsyncSession) -> tuple[Check, list[Source]]:
    sources = (await session.execute(select(Source).order_by(Source.source_id))).scalars().all()
    rollup = worst_health(s.health_status for s in sources)
    failing = [s.source_id for s in sources if s.health_status == "failing"]
    degraded = [s.source_id for s in sources if s.health_status == "degraded"]
    status = {"failing": "failing", "degraded": "degraded"}.get(rollup, "ok")
    detail = None
    if failing:
        detail = f"failing: {', '.join(failing)}"
    elif degraded:
        detail = f"degraded: {', '.join(degraded)}"
    return (
        Check(
            name="adapters",
            status=status,
            detail=detail,
            meta={
                "total": len(sources),
                "failing": len(failing),
                "degraded": len(degraded),
                "health": rollup,
            },
        ),
        list(sources),
    )
