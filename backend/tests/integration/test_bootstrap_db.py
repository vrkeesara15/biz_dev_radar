"""Railway prep: `app.jobs.bootstrap_db` against a real Postgres.

The target is a throwaway database created and dropped inside the test with the owner
connection, so nothing here touches `bidradar_test`. The app role is throwaway too:
`bidradar_app` is cluster-wide and already exists in compose, which would make
`created_role` meaningless.

What is asserted is what a managed provider does NOT give us for free: the two
extensions, a login role that is explicitly not a superuser and explicitly NOT
`BYPASSRLS`, CONNECT on the database, USAGE on `public`, and the two
`ALTER DEFAULT PRIVILEGES` entries without which every table alembic creates would be
invisible to the app role. Then the whole thing is run a second time.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator

import asyncpg
import pytest
from app.jobs.bootstrap_db import EXTENSIONS, BootstrapError, bootstrap, derive_app_url

from tests.db import TEST_DATABASE_URL_OWNER

#: `postgresql+asyncpg://` is SQLAlchemy's spelling; asyncpg.connect wants libpq's.
OWNER_DSN = TEST_DATABASE_URL_OWNER.replace("postgresql+asyncpg://", "postgresql://")
APP_PASSWORD = "throwaway-bootstrap-pw"


def _with_database(dsn: str, database: str) -> str:
    head, _, _tail = dsn.rpartition("/")
    return f"{head}/{database}"


@pytest.fixture
async def throwaway() -> AsyncIterator[tuple[str, str, str]]:
    """(owner dsn for a brand-new database, its name, a brand-new role name)."""
    suffix = secrets.token_hex(4)
    database = f"bidradar_bootstrap_{suffix}"
    role = f"bootstrap_app_{suffix}"
    admin = await asyncpg.connect(dsn=_with_database(OWNER_DSN, "postgres"))
    try:
        await admin.execute(f'CREATE DATABASE "{database}"')
    except asyncpg.InsufficientPrivilegeError as exc:  # pragma: no cover - CI owner is super
        await admin.close()
        pytest.skip(f"owner role may not CREATE DATABASE: {exc}")
    try:
        yield _with_database(OWNER_DSN, database), database, role
    finally:
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1",
            database,
        )
        await admin.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.close()


async def _default_acls(conn: asyncpg.Connection, owner: str) -> dict[str, str]:
    rows = await conn.fetch(
        """
        SELECT d.defaclobjtype AS objtype, array_to_string(d.defaclacl, ',') AS acl
        FROM pg_default_acl d
        JOIN pg_namespace n ON n.oid = d.defaclnamespace
        JOIN pg_roles r ON r.oid = d.defaclrole
        WHERE n.nspname = 'public' AND r.rolname = $1
        """,
        owner,
    )

    # `defaclobjtype` is a Postgres "char", which asyncpg hands back as bytes.
    def _key(value: bytes | str) -> str:
        return value.decode() if isinstance(value, bytes) else value

    return {_key(row["objtype"]): row["acl"] for row in rows}


async def test_bootstrap_creates_extensions_role_and_grants(
    throwaway: tuple[str, str, str],
) -> None:
    owner_dsn, database, role = throwaway

    result = await bootstrap(owner_dsn, APP_PASSWORD, app_role=role)

    assert result.database == database
    assert result.created_role is True
    assert result.altered_password is False
    assert result.extensions == EXTENSIONS

    conn = await asyncpg.connect(dsn=owner_dsn)
    try:
        installed = {row["extname"] for row in await conn.fetch("SELECT extname FROM pg_extension")}
        assert {"vector", "pg_trgm"} <= installed

        # The role: able to log in, and unable to do anything that would defeat RLS.
        attrs = await conn.fetchrow(
            "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls "
            "FROM pg_roles WHERE rolname = $1",
            role,
        )
        assert attrs is not None
        assert attrs["rolcanlogin"] is True
        assert attrs["rolsuper"] is False
        assert attrs["rolcreatedb"] is False
        assert attrs["rolcreaterole"] is False
        assert attrs["rolbypassrls"] is False, "the app role must never bypass RLS"

        assert await conn.fetchval(
            "SELECT has_database_privilege($1, $2, 'CONNECT')", role, database
        )
        assert await conn.fetchval("SELECT has_schema_privilege($1, 'public', 'USAGE')", role)

        acls = await _default_acls(conn, result.owner_role)
        assert f"{role}=arwd/" in acls.get("r", ""), "tables: SELECT/INSERT/UPDATE/DELETE"
        assert f"{role}=rU/" in acls.get("S", ""), "sequences: USAGE, SELECT"
    finally:
        await conn.close()


async def test_default_privileges_reach_a_table_the_owner_creates_afterwards(
    throwaway: tuple[str, str, str],
) -> None:
    """The point of ALTER DEFAULT PRIVILEGES: alembic runs AFTER the bootstrap, and the
    app role must see what it creates without anyone granting table by table."""
    owner_dsn, _database, role = throwaway
    await bootstrap(owner_dsn, APP_PASSWORD, app_role=role)

    owner_conn = await asyncpg.connect(dsn=owner_dsn)
    try:
        await owner_conn.execute("CREATE TABLE after_bootstrap (id serial PRIMARY KEY, n int)")
    finally:
        await owner_conn.close()

    app_dsn = derive_app_url(owner_dsn, APP_PASSWORD, app_role=role).replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    app_conn = await asyncpg.connect(dsn=app_dsn)
    try:
        await app_conn.execute("INSERT INTO after_bootstrap (n) VALUES (1)")
        assert await app_conn.fetchval("SELECT count(*) FROM after_bootstrap") == 1
    finally:
        await app_conn.close()


async def test_bootstrap_is_idempotent(throwaway: tuple[str, str, str]) -> None:
    owner_dsn, _database, role = throwaway
    first = await bootstrap(owner_dsn, APP_PASSWORD, app_role=role)
    second = await bootstrap(owner_dsn, APP_PASSWORD, app_role=role)

    assert first.created_role is True
    assert second.created_role is False
    assert second.altered_password is False
    assert (second.database, second.owner_role) == (first.database, first.owner_role)

    conn = await asyncpg.connect(dsn=owner_dsn)
    try:
        # Re-running must not duplicate the default-privilege entries.
        rows = await conn.fetchval(
            """
            SELECT count(*) FROM pg_default_acl d
            JOIN pg_namespace n ON n.oid = d.defaclnamespace
            JOIN pg_roles r ON r.oid = d.defaclrole
            WHERE n.nspname = 'public' AND r.rolname = $1
            """,
            first.owner_role,
        )
        assert rows == 2
    finally:
        await conn.close()


async def test_rotate_password_is_opt_in(throwaway: tuple[str, str, str]) -> None:
    owner_dsn, _database, role = throwaway
    await bootstrap(owner_dsn, APP_PASSWORD, app_role=role)

    unchanged = await bootstrap(owner_dsn, "a-different-password", app_role=role)
    assert unchanged.altered_password is False

    rotated = await bootstrap(
        owner_dsn, "a-different-password", app_role=role, rotate_password=True
    )
    assert rotated.altered_password is True

    app_dsn = derive_app_url(owner_dsn, "a-different-password", app_role=role).replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    conn = await asyncpg.connect(dsn=app_dsn)
    await conn.close()


async def test_missing_password_for_a_missing_role_is_a_clear_error(
    throwaway: tuple[str, str, str],
) -> None:
    owner_dsn, _database, role = throwaway
    with pytest.raises(BootstrapError, match="APP_DB_PASSWORD"):
        await bootstrap(owner_dsn, "", app_role=role)
