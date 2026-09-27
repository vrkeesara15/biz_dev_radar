"""M0-06: row-level security on every tenant table."""

import uuid

import pytest
from app.core.db import Database
from app.core.roles import Role
from app.models import AuditLog, Membership, Tenant, UsageLedger, User
from app.services.audit import SUPPORT_ACCESS_ACTION, support_access_session
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from tests.factories import create_tenant_with_owner, make_user

# Tables that legitimately hold no tenant rows. Everything else in `public` must have RLS.
RLS_EXEMPT_TABLES = {
    "alembic_version",
    "plan_limits",
    # M2 ingestion tables are global: public notices shared by every tenant (SPEC 5.3),
    # adapter bookkeeping and spend statistics. No tenant_id, no policy, app-role DML grants.
    "sources",
    "source_runs",
    "opportunities",
    "opportunity_versions",
    "opportunity_documents",
    "document_chunks",
    "awards_enrichment",
}


async def _two_tenants(database: Database) -> tuple[Tenant, User, Tenant, User]:
    async with database.owner_session() as session:
        a, ua, _ = await create_tenant_with_owner(session)
        b, ub, _ = await create_tenant_with_owner(session)
        session.add_all(
            [
                UsageLedger(tenant_id=a.id, metric="profiles", quantity=1, period="lifetime"),
                UsageLedger(tenant_id=b.id, metric="profiles", quantity=1, period="lifetime"),
                AuditLog(tenant_id=a.id, user_id=ua.id, action="test"),
            ]
        )
        await session.commit()
        return a, ua, b, ub


async def test_rows_of_tenant_a_invisible_to_tenant_b(database: Database) -> None:
    a, ua, b, ub = await _two_tenants(database)
    async with database.session(b.id) as session:
        ledger = (await session.execute(select(UsageLedger))).scalars().all()
        assert {row.tenant_id for row in ledger} == {b.id}
        assert (await session.execute(select(AuditLog))).scalars().all() == []
        members = (await session.execute(select(Membership))).scalars().all()
        assert {m.tenant_id for m in members} == {b.id}
        tenants = (await session.execute(select(Tenant))).scalars().all()
        assert [t.id for t in tenants] == [b.id]
        users = (await session.execute(select(User))).scalars().all()
        assert [u.id for u in users] == [ub.id]
    async with database.session(a.id) as session:
        assert (await session.execute(select(AuditLog))).scalars().one().tenant_id == a.id
        assert [u.id for u in (await session.execute(select(User))).scalars()] == [ua.id]


async def test_no_tenant_context_sees_nothing(database: Database) -> None:
    await _two_tenants(database)
    async with database.session(None) as session:
        for model in (Tenant, User, Membership, UsageLedger, AuditLog):
            assert (await session.execute(select(model))).scalars().all() == []


async def test_with_check_blocks_writing_into_another_tenant(database: Database) -> None:
    a, _, b, _ = await _two_tenants(database)
    async with database.session(a.id) as session:
        session.add(UsageLedger(tenant_id=b.id, metric="profiles", quantity=1, period="lifetime"))
        with pytest.raises(DBAPIError):
            await session.flush()
        await session.rollback()
    async with database.session(a.id) as session:
        session.add(UsageLedger(tenant_id=a.id, metric="profiles", quantity=1, period="lifetime"))
        await session.flush()
    # Updates cannot move a row across tenants either.
    async with database.session(a.id) as session:
        with pytest.raises(DBAPIError):
            await session.execute(
                text("UPDATE usage_ledger SET tenant_id = :b WHERE tenant_id = :a"),
                {"a": a.id, "b": b.id},
            )
        await session.rollback()


async def test_app_role_cannot_bypass_rls(database: Database) -> None:
    await _two_tenants(database)
    async with database.app_engine.connect() as conn:
        role = (await conn.execute(text("SELECT current_user"))).scalar()
        bypass = (
            await conn.execute(
                text("SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user")
            )
        ).scalar()
        assert bypass is False, f"{role} must not bypass RLS"
        owners = (
            (
                await conn.execute(
                    text(
                        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                        "AND tableowner = current_user"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert owners == [], f"app role must not own tables: {owners}"
        # Turning row security off as a non-owner makes RLS-protected queries error out.
        await conn.execute(text("SET row_security = off"))
        with pytest.raises(ProgrammingError):
            await conn.execute(text("SELECT count(*) FROM usage_ledger"))


async def test_every_tenant_table_has_forced_rls_and_policy(database: Database) -> None:
    async with database.owner_engine.connect() as conn:
        tenant_tables = set(
            (
                await conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND column_name = 'tenant_id'"
                    )
                )
            )
            .scalars()
            .all()
        )
        all_tables = set(
            (
                await conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
                    )
                )
            )
            .scalars()
            .all()
        )
        rls = {
            row[0]: (row[1], row[2])
            for row in await conn.execute(
                text(
                    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relkind = 'r'"
                )
            )
        }
        policies = set(
            (
                await conn.execute(
                    text("SELECT DISTINCT tablename FROM pg_policies WHERE schemaname = 'public'")
                )
            )
            .scalars()
            .all()
        )
    assert tenant_tables, "expected tenant-scoped tables"
    required = (tenant_tables | all_tables) - RLS_EXEMPT_TABLES
    problems = [
        f"{t}: rls={rls.get(t, (False, False))[0]} force={rls.get(t, (False, False))[1]} "
        f"policy={t in policies}"
        for t in sorted(required)
        if not (rls.get(t) == (True, True) and t in policies)
    ]
    assert not problems, "tables without forced RLS + policy:\n" + "\n".join(problems)


async def test_users_are_provisioned_by_owner_role_only(database: Database) -> None:
    """A not-yet-member user is never visible to the app role, so it cannot insert one
    (INSERT ... RETURNING is checked against the SELECT policy). Provisioning is owner work."""
    a, _, _, _ = await _two_tenants(database)
    async with database.session(a.id) as session:
        session.add(make_user())
        with pytest.raises(DBAPIError):
            await session.flush()
        await session.rollback()
    async with database.owner_session() as session:
        user = make_user()
        session.add(user)
        await session.flush()
        session.add(Membership(tenant_id=a.id, user_id=user.id, role=Role.WRITER))
        user_id = user.id
    async with database.session(a.id) as session:
        assert (await session.get(User, user_id)) is not None
        assert (
            await session.execute(select(Membership).where(Membership.user_id == user_id))
        ).scalar_one().role is Role.WRITER


async def test_support_access_bypass_writes_audit_row(database: Database) -> None:
    a, ua, b, _ = await _two_tenants(database)
    async with support_access_session(
        tenant_id=a.id, actor_user_id=ua.id, reason="ticket #42", ip="10.0.0.1", database=database
    ) as session:
        rows = (await session.execute(select(UsageLedger))).scalars().all()
        assert {r.tenant_id for r in rows} == {a.id, b.id}  # owner engine sees everything
    async with database.owner_session() as session:
        audit = (
            await session.execute(select(AuditLog).where(AuditLog.action == SUPPORT_ACCESS_ACTION))
        ).scalar_one()
    assert audit.tenant_id == a.id
    assert audit.user_id == ua.id
    assert audit.object_id == str(a.id)
    assert audit.meta == {"reason": "ticket #42"}
    assert audit.ip == "10.0.0.1"
    with pytest.raises(ValueError):
        async with support_access_session(
            tenant_id=a.id, actor_user_id=ua.id, reason="  ", database=database
        ):
            pass


async def test_support_access_audit_survives_failed_work(database: Database) -> None:
    a, ua, _, _ = await _two_tenants(database)
    with pytest.raises(RuntimeError):
        async with support_access_session(
            tenant_id=a.id, actor_user_id=ua.id, reason="oops", database=database
        ) as session:
            await session.execute(text("SELECT 1"))
            raise RuntimeError("admin work failed")
    async with database.owner_session() as session:
        count = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == SUPPORT_ACCESS_ACTION, AuditLog.tenant_id == a.id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(count) == 1
    assert uuid.UUID(count[0].object_id or "") == a.id
