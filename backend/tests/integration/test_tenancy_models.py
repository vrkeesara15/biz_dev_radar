"""M0-05: tenancy tables, constraints and seeded plan limits."""

import uuid

import pytest
from app.core.db import Database
from app.core.plan import plan_limit_rows
from app.core.roles import Role
from app.models import AuditLog, Membership, PlanLimit, UsageLedger
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.factories import create_tenant_with_owner, make_tenant, make_user


async def test_plan_limits_seeded(database: Database) -> None:
    async with database.owner_session() as session:
        rows = (await session.execute(select(PlanLimit))).scalars().all()
    got = {(r.plan.value, r.resource): r.limit_value for r in rows}
    expected = {(r["plan"], r["resource"]): r["limit_value"] for r in plan_limit_rows()}
    assert got == expected
    assert got[("free", "profiles")] == 1
    assert got[("pro", "agent_drafts_per_month")] == 10
    assert got[("enterprise", "profiles")] is None


async def test_membership_unique_per_user_and_tenant(database: Database) -> None:
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(session)
        session.add(Membership(tenant_id=tenant.id, user_id=user.id, role=Role.VIEWER))
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


async def test_role_enum_rejects_unknown_values(database: Database) -> None:
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(session)
        await session.commit()
        with pytest.raises(DBAPIError):
            await session.execute(
                text(
                    "INSERT INTO memberships (tenant_id, user_id, role) "
                    "VALUES (:t, :u, 'superuser')"
                ),
                {"t": tenant.id, "u": user.id},
            )
        await session.rollback()


async def test_tenant_slug_and_user_email_unique(database: Database) -> None:
    async with database.owner_session() as session:
        session.add(make_tenant(slug="dup"))
        await session.flush()
        session.add(make_tenant(slug="dup"))
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()
    async with database.owner_session() as session:
        session.add(make_user(email="dup@example.com"))
        await session.flush()
        session.add(make_user(email="dup@example.com"))
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


async def test_tenant_defaults_and_enums(database: Database) -> None:
    async with database.owner_session() as session:
        tenant = make_tenant()
        del tenant.plan, tenant.is_internal
        session.add(tenant)
        await session.flush()
        await session.refresh(tenant)
        assert tenant.plan.value == "free"
        assert tenant.is_internal is False
        assert tenant.region.value == "us"
        assert tenant.created_at is not None
        with pytest.raises(DBAPIError):
            await session.execute(
                text(
                    "INSERT INTO tenants (name, slug, region, data_residency) "
                    "VALUES ('x', 'x', 'eu', 'eu')"
                )
            )
        await session.rollback()


async def test_usage_ledger_and_audit_log_rows(database: Database) -> None:
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(session)
        session.add(
            UsageLedger(
                tenant_id=tenant.id, metric="agent_drafts_per_month", quantity=2, period="2026-09"
            )
        )
        session.add(
            AuditLog(
                tenant_id=tenant.id,
                user_id=user.id,
                action="draft.read",
                object_type="draft",
                object_id=str(uuid.uuid4()),
                ip="127.0.0.1",
                request_id="req-1",
                meta={"section": "technical"},
            )
        )
        await session.commit()
        ledger = (await session.execute(select(UsageLedger))).scalar_one()
        assert ledger.period == "2026-09" and ledger.ref is None
        audit = (await session.execute(select(AuditLog))).scalar_one()
        assert audit.meta == {"section": "technical"}
        assert audit.at is not None


async def test_tenant_id_required_on_tenant_tables(database: Database) -> None:
    async with database.owner_session() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    "INSERT INTO usage_ledger (metric, quantity, period) "
                    "VALUES ('m', 1, 'lifetime')"
                )
            )
        await session.rollback()
