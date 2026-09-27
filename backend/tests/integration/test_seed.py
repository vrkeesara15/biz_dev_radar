"""M0-09: internal tenant + platform admin seed is idempotent."""

import pytest
from app.core.config import Settings
from app.core.db import Database
from app.core.plan import Plan, Resource, effective_limit
from app.core.roles import Role
from app.models import Membership, Tenant, User
from app.seed import INTERNAL_SLUG, run, seed
from sqlalchemy import func, select


async def _counts(database: Database) -> tuple[int, int, int]:
    async with database.owner_session() as session:
        t = (await session.execute(select(func.count()).select_from(Tenant))).scalar_one()
        u = (await session.execute(select(func.count()).select_from(User))).scalar_one()
        m = (await session.execute(select(func.count()).select_from(Membership))).scalar_one()
        return t, u, m


async def test_seed_creates_internal_tenant_and_admin(database: Database) -> None:
    result = await seed(database, "Admin@Example.com")
    assert result.created_tenant and result.created_user and result.created_membership
    async with database.owner_session() as session:
        tenant = (
            await session.execute(select(Tenant).where(Tenant.slug == INTERNAL_SLUG))
        ).scalar_one()
        assert tenant.is_internal is True
        assert tenant.plan is Plan.ENTERPRISE
        assert tenant.region.value == "us" and tenant.data_residency.value == "us"
        user = (
            await session.execute(select(User).where(User.email == "admin@example.com"))
        ).scalar_one()
        membership = (
            await session.execute(
                select(Membership).where(
                    Membership.user_id == user.id, Membership.tenant_id == tenant.id
                )
            )
        ).scalar_one()
        assert membership.role is Role.PLATFORM_ADMIN
    # Internal tenants bypass plan limits regardless of configured values.
    for resource in Resource:
        assert effective_limit(tenant.plan, resource, is_internal=tenant.is_internal) is None
    assert effective_limit(Plan.FREE, Resource.PROFILES, is_internal=True, configured=1) is None


async def test_seed_is_idempotent(database: Database) -> None:
    first = await seed(database, "admin@example.com")
    before = await _counts(database)
    second = await seed(database, "admin@example.com")
    assert (second.created_tenant, second.created_user, second.created_membership) == (
        False,
        False,
        False,
    )
    assert second.tenant_id == first.tenant_id and second.user_id == first.user_id
    assert await _counts(database) == before == (1, 1, 1)
    assert "exists" in second.summary() and "created" in first.summary()


async def test_seed_repairs_internal_flags(database: Database) -> None:
    await seed(database, "admin@example.com")
    async with database.owner_session() as session:
        tenant = (
            await session.execute(select(Tenant).where(Tenant.slug == INTERNAL_SLUG))
        ).scalar_one()
        tenant.is_internal = False
        tenant.plan = Plan.FREE
    await seed(database, "admin@example.com")
    async with database.owner_session() as session:
        tenant = (
            await session.execute(select(Tenant).where(Tenant.slug == INTERNAL_SLUG))
        ).scalar_one()
        assert tenant.is_internal is True and tenant.plan is Plan.ENTERPRISE


async def test_seed_rejects_bad_email(database: Database) -> None:
    with pytest.raises(ValueError):
        await seed(database, "not-an-email")


async def test_run_and_main_use_settings(
    database: Database, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    custom = settings.model_copy(update={"seed_admin_email": "ops@example.com"})
    result = await run(custom, database)
    assert result.created_user
    async with database.owner_session() as session:
        assert (
            await session.execute(select(User).where(User.email == "ops@example.com"))
        ).scalar_one_or_none() is not None
