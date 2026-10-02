"""Bootstrap a managed Postgres so BidRadar can run on it (Railway, RDS, Neon, …).

Locally this work happens at `initdb` time: `infra/postgres/sql/roles.sql` creates the
non-owner `bidradar_app` role and `infra/postgres/sql/extensions.sql` installs pgvector
and pg_trgm and hands the app role its default privileges. A managed provider has no
initdb hook and gives you a database that already exists plus exactly ONE role, so the
same statements have to run at **deploy** time instead. `docker/entrypoint.sh migrate`
runs this module before `alembic upgrade head` whenever `BOOTSTRAP_DB=1` (the default).

    python -m app.jobs.bootstrap_db [--rotate-password]

Everything is idempotent: re-running it on a bootstrapped database changes nothing and
exits 0. It connects on `DATABASE_URL_OWNER` with **raw asyncpg** rather than
SQLAlchemy, because `CREATE EXTENSION` / `CREATE ROLE` / `ALTER DEFAULT PRIVILEGES` are
cluster-level DDL that has no business travelling through the ORM, and asyncpg is
already in the image.

What it deliberately does NOT do: grant on tables that already exist. The migrations
revoke UPDATE/DELETE again where rows must be append-only (`audit_log`), so a blanket
`GRANT ... ON ALL TABLES` would silently undo that. Only ALTER DEFAULT PRIVILEGES is
used, exactly as `extensions.sql` does, and it is set for `current_user` — the role the
managed provider actually gave us, which is `postgres` on Railway and `bidradar` in
compose.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from urllib.parse import quote

from app.core.config import get_settings, normalize_database_url

# The role every RLS policy depends on: not the owner, NOBYPASSRLS, so a policy can
# never be skipped by accident (CLAUDE.md, SPEC section 11).
APP_ROLE = "bidradar_app"
EXTENSIONS: tuple[str, ...] = ("vector", "pg_trgm")


class BootstrapError(RuntimeError):
    """The database cannot be bootstrapped with the inputs it was given."""


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    database: str
    owner_role: str
    app_role: str
    created_role: bool
    altered_password: bool
    extensions: tuple[str, ...]

    def as_json(self) -> str:
        return json.dumps({**asdict(self), "extensions": list(self.extensions)}, sort_keys=True)


def derive_app_url(owner_url: str, app_password: str, *, app_role: str = APP_ROLE) -> str:
    """The app-role DSN that matches an owner DSN: same host/port/database, new login.

    Railway exposes the database as one variable (`${{Postgres.DATABASE_URL}}`) holding
    the OWNER DSN. `DATABASE_URL_OWNER` is that value verbatim; `DATABASE_URL` is this
    function's output, so the two can never drift apart in host, port or database name
    — the mistake that makes an app silently talk to the wrong database.

    The scheme is normalised the same way `Settings` normalises it
    (`postgres://` → `postgresql+asyncpg://`, `?sslmode=` → `?ssl=`), and any query
    string on the owner DSN is preserved.
    """
    if not app_password:
        raise BootstrapError("APP_DB_PASSWORD is empty; cannot build the app-role DSN")
    url = normalize_database_url(owner_url)
    if "://" not in url:
        raise BootstrapError(f"not a DSN: {owner_url!r}")
    scheme, _, rest = url.partition("://")
    netloc, slash, tail = rest.partition("/")
    _, at, host = netloc.rpartition("@")
    if not at:
        host = netloc
    userinfo = f"{quote(app_role, safe='')}:{quote(app_password, safe='')}"
    return f"{scheme}://{userinfo}@{host}{slash}{tail}"


def _asyncpg_dsn(url: str) -> str:
    """asyncpg.connect() wants a libpq DSN, not SQLAlchemy's driver-qualified one."""
    dsn = normalize_database_url(url).replace("postgresql+asyncpg://", "postgresql://", 1)
    # `ssl=` is an asyncpg *keyword*, not a libpq one; put it back for the DSN parser.
    return dsn.replace("?ssl=", "?sslmode=").replace("&ssl=", "&sslmode=")


async def bootstrap(
    owner_url: str,
    app_password: str,
    *,
    app_role: str = APP_ROLE,
    rotate_password: bool = False,
) -> BootstrapResult:
    """Create the extensions, the app role and its grants. Idempotent."""
    import asyncpg

    conn = await asyncpg.connect(dsn=_asyncpg_dsn(owner_url))
    try:
        database: str = await conn.fetchval("SELECT current_database()")
        owner: str = await conn.fetchval("SELECT current_user")

        for extension in EXTENSIONS:
            name = await conn.fetchval("SELECT quote_ident($1)", extension)
            await conn.execute(f"CREATE EXTENSION IF NOT EXISTS {name}")

        role_ident: str = await conn.fetchval("SELECT quote_ident($1)", app_role)
        exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", app_role)
        created_role = False
        altered_password = False
        if not exists:
            if not app_password:
                raise BootstrapError(
                    f"role {app_role} does not exist and APP_DB_PASSWORD is empty; "
                    "set APP_DB_PASSWORD to the password DATABASE_URL carries"
                )
            literal: str = await conn.fetchval("SELECT quote_literal($1)", app_password)
            await conn.execute(
                f"CREATE ROLE {role_ident} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                f"NOBYPASSRLS PASSWORD {literal}"
            )
            created_role = True
        elif rotate_password and app_password:
            # Postgres hashes the password, so "has it changed?" is unanswerable; the
            # flag exists because setting it unconditionally would rotate the secret on
            # every deploy and lock out any connection pool still holding the old one.
            literal = await conn.fetchval("SELECT quote_literal($1)", app_password)
            await conn.execute(f"ALTER ROLE {role_ident} WITH PASSWORD {literal}")
            altered_password = True

        db_ident: str = await conn.fetchval("SELECT quote_ident($1)", database)
        owner_ident: str = await conn.fetchval("SELECT quote_ident($1)", owner)
        await conn.execute(f"GRANT CONNECT ON DATABASE {db_ident} TO {role_ident}")
        await conn.execute(f"GRANT USAGE ON SCHEMA public TO {role_ident}")
        await conn.execute(
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_ident} IN SCHEMA public "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role_ident}"
        )
        await conn.execute(
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_ident} IN SCHEMA public "
            f"GRANT USAGE, SELECT ON SEQUENCES TO {role_ident}"
        )
        return BootstrapResult(
            database=database,
            owner_role=owner,
            app_role=app_role,
            created_role=created_role,
            altered_password=altered_password,
            extensions=EXTENSIONS,
        )
    finally:
        await conn.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.jobs.bootstrap_db",
        description=(
            "Idempotently install pgvector + pg_trgm and create the non-owner "
            "bidradar_app role with its grants on DATABASE_URL_OWNER."
        ),
    )
    parser.add_argument(
        "--rotate-password",
        action="store_true",
        help=(
            "ALTER the app role's password to APP_DB_PASSWORD even when the role "
            "already exists. Off by default: a rotation on every deploy would break "
            "any pool still holding the previous password."
        ),
    )
    args = parser.parse_args(argv)
    settings = get_settings()
    result = asyncio.run(
        bootstrap(
            settings.database_url_owner,
            settings.app_db_password,
            rotate_password=args.rotate_password,
        )
    )
    payload = json.loads(result.as_json())
    # The commonest managed-Postgres misconfiguration is a DATABASE_URL pointing at a
    # different host or database than DATABASE_URL_OWNER. Say so without printing either
    # DSN, which would put a password in the deploy log.
    if settings.app_db_password:
        payload["database_url_matches_owner"] = settings.database_url == derive_app_url(
            settings.database_url_owner, settings.app_db_password
        )
    sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
