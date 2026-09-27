"""M0-04: migrations create extensions; sessions set app.tenant_id per transaction."""

import uuid

import pytest
from app.core.db import Database, get_session
from sqlalchemy import text

from tests.db import alembic


async def _current_tenant(session) -> str | None:  # type: ignore[no-untyped-def]
    return (await session.execute(text("SELECT current_setting('app.tenant_id', true)"))).scalar()


async def test_extensions_present_after_upgrade(database: Database) -> None:
    async with database.owner_engine.connect() as conn:
        names = set((await conn.execute(text("SELECT extname FROM pg_extension"))).scalars().all())
    assert {"vector", "pg_trgm"} <= names


async def test_session_sets_tenant_on_every_transaction(database: Database) -> None:
    tid = uuid.uuid4()
    async with database.session(tid) as session:
        assert await _current_tenant(session) == str(tid)
        await session.commit()  # new transaction begins on next statement
        assert await _current_tenant(session) == str(tid)
    async with get_session(tid) as session:
        assert await _current_tenant(session) == str(tid)


async def test_session_without_tenant_has_null_setting(database: Database) -> None:
    async with database.session(None) as session:
        value = await _current_tenant(session)
    assert value in (None, "")


async def test_tenant_setting_is_transaction_local(database: Database) -> None:
    tid = uuid.uuid4()
    async with database.session(tid) as session:
        assert await _current_tenant(session) == str(tid)
    # A fresh connection from the pool must not leak the previous tenant.
    async with database.app_engine.connect() as conn:
        value = (await conn.execute(text("SELECT current_setting('app.tenant_id', true)"))).scalar()
    assert value in (None, "")


async def test_session_rolls_back_on_error(database: Database) -> None:
    with pytest.raises(RuntimeError):
        async with database.session(uuid.uuid4()) as session:
            await session.execute(text("SELECT 1"))
            raise RuntimeError("boom")


async def test_downgrade_base_leaves_no_application_tables(database: Database) -> None:
    alembic("downgrade", "base")
    try:
        async with database.owner_engine.connect() as conn:
            tables = set(
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
        assert tables <= {"alembic_version"}, tables
        async with database.owner_engine.begin() as conn:
            await conn.execute(text("DROP EXTENSION IF EXISTS vector"))
            await conn.execute(text("DROP EXTENSION IF EXISTS pg_trgm"))
    finally:
        alembic("upgrade", "head")
    async with database.owner_engine.connect() as conn:
        names = set((await conn.execute(text("SELECT extname FROM pg_extension"))).scalars().all())
    assert {"vector", "pg_trgm"} <= names
