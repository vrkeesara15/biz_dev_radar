"""Test database helpers shared by conftest modules (no pytest imports here)."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
EXTENSIONS_SQL = REPO_ROOT / "infra" / "postgres" / "sql" / "extensions.sql"

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://bidradar_app:bidradar_app@localhost:5433/bidradar_test",
)
TEST_DATABASE_URL_OWNER = os.environ.get(
    "TEST_DATABASE_URL_OWNER",
    "postgresql+asyncpg://bidradar:bidradar@localhost:5433/bidradar_test",
)


def alembic(*args: str) -> None:
    """Run alembic in a subprocess against the TEST database (owner role)."""
    env = {**os.environ, "DATABASE_URL_OWNER": TEST_DATABASE_URL_OWNER}
    subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def _sql_statements(path: Path) -> list[str]:
    body = "\n".join(
        line for line in path.read_text().splitlines() if not line.strip().startswith("--")
    )
    return [stmt.strip() for stmt in body.split(";") if stmt.strip()]


def reset_test_schema() -> None:
    """Drop and recreate the public schema of the TEST database (owner role).

    Used when `alembic downgrade base` cannot run because the milestone migration was
    edited in place after the database was stamped (CLAUDE.md: one migration per
    milestone, edited until the milestone closes).
    """
    import asyncpg

    dsn = TEST_DATABASE_URL_OWNER.replace("postgresql+asyncpg://", "postgresql://")
    statements = [
        "DROP SCHEMA public CASCADE",
        "CREATE SCHEMA public",
        *_sql_statements(EXTENSIONS_SQL),
    ]

    async def _run() -> None:
        conn = await asyncpg.connect(dsn=dsn)
        try:
            for stmt in statements:
                await conn.execute(stmt)
        finally:
            await conn.close()

    asyncio.run(_run())


def fresh_schema() -> None:
    """Fresh schema for the test session: downgrade base (or hard reset), then upgrade head."""
    try:
        alembic("downgrade", "base")
    except subprocess.CalledProcessError:
        reset_test_schema()
    alembic("upgrade", "head")
