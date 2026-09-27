"""M0-12: PlanService enforces plan_limits against usage with period rollover."""

import uuid
from datetime import UTC, datetime

import httpx
import pytest
from app.core.db import Database
from app.core.plan import Plan, PlanLimitExceeded, Resource
from app.models import PlanLimit, Tenant
from app.services.plan import PlanService
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import create_tenant_with_owner

SEP = datetime(2026, 9, 15, tzinfo=UTC)
OCT = datetime(2026, 10, 1, 0, 0, 1, tzinfo=UTC)


async def _tenant(database: Database, **overrides) -> uuid.UUID:  # type: ignore[no-untyped-def]
    async with database.owner_session() as session:
        tenant, _, _ = await create_tenant_with_owner(session, **overrides)
        return tenant.id


async def _svc(
    session: AsyncSession, tid: uuid.UUID, now: datetime = SEP
) -> tuple[PlanService, Tenant]:
    tenant = await session.get(Tenant, tid)
    assert tenant is not None
    return PlanService(session, now=lambda: now), tenant


async def test_profiles_limit_free_plan(database: Database) -> None:
    tid = await _tenant(database, plan=Plan.FREE)
    async with database.session(tid) as session:
        svc, tenant = await _svc(session, tid)
        result = await svc.check(tenant, Resource.PROFILES)
        assert result.allowed and result.remaining == 1
        await svc.consume(tenant, Resource.PROFILES, ref="profile-1")
        with pytest.raises(PlanLimitExceeded) as exc:
            await svc.check(tenant, Resource.PROFILES)
        assert exc.value.resource == "profiles" and exc.value.limit == 1 and exc.value.used == 1
        assert exc.value.plan == "free"
        with pytest.raises(PlanLimitExceeded):
            await svc.consume(tenant, Resource.PROFILES)


async def test_source_regions(database: Database) -> None:
    free = await _tenant(database, plan=Plan.FREE)
    pro = await _tenant(database, plan=Plan.PRO)
    async with database.session(free) as session:
        svc, tenant = await _svc(session, free)
        await svc.consume(tenant, Resource.SOURCE_REGIONS, ref="us")
        with pytest.raises(PlanLimitExceeded, match="source_regions"):
            await svc.check(tenant, Resource.SOURCE_REGIONS)
    async with database.session(pro) as session:
        svc, tenant = await _svc(session, pro)
        assert (await svc.check(tenant, Resource.SOURCE_REGIONS, requested=100)).limit is None
        assert (await svc.check(tenant, Resource.INSTANT_ALERTS)).limit == 1


async def test_agent_drafts_per_month_rolls_over(database: Database) -> None:
    tid = await _tenant(database, plan=Plan.PRO)
    async with database.session(tid) as session:
        svc, tenant = await _svc(session, tid, now=SEP)
        for i in range(10):
            await svc.consume(tenant, Resource.AGENT_DRAFTS_PER_MONTH, ref=f"draft-{i}")
        assert await svc.usage(tid, Resource.AGENT_DRAFTS_PER_MONTH) == 10
        with pytest.raises(PlanLimitExceeded) as exc:
            await svc.check(tenant, Resource.AGENT_DRAFTS_PER_MONTH)
        assert exc.value.limit == 10 and exc.value.used == 10
        # requested > remaining is refused even when some headroom exists
        svc_sep_partial = PlanService(session, now=lambda: SEP)
        with pytest.raises(PlanLimitExceeded):
            await svc_sep_partial.check(tenant, Resource.AGENT_DRAFTS_PER_MONTH, requested=1)
        # New month: the ledger for October is empty, the limit resets.
        svc_oct = PlanService(session, now=lambda: OCT)
        assert await svc_oct.usage(tid, Resource.AGENT_DRAFTS_PER_MONTH) == 0
        result = await svc_oct.check(tenant, Resource.AGENT_DRAFTS_PER_MONTH, requested=10)
        assert result.allowed and result.remaining == 10
        row = await svc_oct.record(tid, Resource.AGENT_DRAFTS_PER_MONTH, ref="draft-oct")
        assert row.period == "2026-10"
        assert await svc.usage(tid, Resource.AGENT_DRAFTS_PER_MONTH) == 10  # September untouched


async def test_free_plan_has_no_drafts(database: Database) -> None:
    tid = await _tenant(database, plan=Plan.FREE)
    async with database.session(tid) as session:
        svc, tenant = await _svc(session, tid)
        with pytest.raises(PlanLimitExceeded) as exc:
            await svc.check(tenant, Resource.AGENT_DRAFTS_PER_MONTH)
        assert exc.value.limit == 0 and exc.value.used == 0


async def test_internal_tenant_always_passes(database: Database) -> None:
    tid = await _tenant(database, plan=Plan.FREE, is_internal=True)
    async with database.session(tid) as session:
        svc, tenant = await _svc(session, tid)
        for _ in range(5):
            await svc.consume(tenant, Resource.PROFILES)
        for resource in Resource:
            result = await svc.check(tenant, resource, requested=1_000)
            assert result.allowed and result.limit is None
        assert await svc.limit_for(tenant, "made_up_resource") is None


async def test_limits_are_read_from_plan_limits_table(database: Database) -> None:
    tid = await _tenant(database, plan=Plan.PRO)
    async with database.owner_session() as session:
        await session.execute(
            update(PlanLimit)
            .where(PlanLimit.plan == Plan.PRO, PlanLimit.resource == "profiles")
            .values(limit_value=5)
        )
    try:
        async with database.session(tid) as session:
            svc, tenant = await _svc(session, tid)
            assert await svc.limit_for(tenant, Resource.PROFILES) == 5
            for _ in range(5):
                await svc.consume(tenant, Resource.PROFILES)
            with pytest.raises(PlanLimitExceeded) as exc:
                await svc.check(tenant, Resource.PROFILES)
            assert exc.value.limit == 5
            # Unknown resources have no row and no default: denied.
            with pytest.raises(PlanLimitExceeded) as exc:
                await svc.check(tenant, "made_up_resource")
            assert exc.value.limit == 0
    finally:
        async with database.owner_session() as session:
            await session.execute(
                update(PlanLimit)
                .where(PlanLimit.plan == Plan.PRO, PlanLimit.resource == "profiles")
                .values(limit_value=3)
            )


async def test_usage_counter_override(database: Database) -> None:
    tid = await _tenant(database, plan=Plan.PRO)

    async def three(_: AsyncSession, __: uuid.UUID) -> int:
        return 3

    async with database.session(tid) as session:
        tenant = await session.get(Tenant, tid)
        assert tenant is not None
        svc = PlanService(session, counters={"profiles": three})
        assert await svc.usage(tid, Resource.PROFILES) == 3
        with pytest.raises(PlanLimitExceeded):
            await svc.check(tenant, Resource.PROFILES)


async def test_usage_is_tenant_scoped_by_rls(database: Database) -> None:
    a = await _tenant(database, plan=Plan.FREE)
    b = await _tenant(database, plan=Plan.FREE)
    async with database.session(a) as session:
        svc, tenant = await _svc(session, a)
        await svc.consume(tenant, Resource.PROFILES)
    async with database.session(b) as session:
        svc, tenant = await _svc(session, b)
        assert await svc.usage(b, Resource.PROFILES) == 0
        assert (await svc.check(tenant, Resource.PROFILES)).allowed
    async with database.owner_session() as session:
        rows = (await session.execute(select(PlanLimit))).scalars().all()
        assert len(rows) == len(Plan) * len(Resource) == 15


async def test_plan_limit_maps_to_http_402(app, api_client: httpx.AsyncClient) -> None:  # type: ignore[no-untyped-def]
    from app.core.plan import check_limit

    async def _boom() -> None:
        raise PlanLimitExceeded("free", check_limit("profiles", 1, used=1))

    app.add_api_route("/api/v1/_plan_probe", _boom, methods=["GET"])
    resp = await api_client.get("/api/v1/_plan_probe")
    assert resp.status_code == 402
    body = resp.json()["detail"]
    assert body["error"] == "plan_limit_exceeded" and body["limit"] == "profiles"
    assert body["limit_value"] == 1 and body["plan"] == "free"
