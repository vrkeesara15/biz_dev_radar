"""Shared fixtures. DB fixtures are added in M0-04; keep unit tests DB-free."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.db import TEST_DATABASE_URL, TEST_DATABASE_URL_OWNER, fresh_schema

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"

# Tests never read a developer's .env; they use explicit env vars or defaults.
os.environ.setdefault("APP_ENV", "test")


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def backend_root() -> Path:
    return BACKEND_ROOT


@pytest.fixture(scope="session")
def settings():  # type: ignore[no-untyped-def]
    from app.core.config import Settings

    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url=TEST_DATABASE_URL,
        database_url_owner=TEST_DATABASE_URL_OWNER,
    )


# Global reference tables seeded by migrations; never truncated between tests.
SEED_TABLES = {"plan_limits"}


# --- database fixtures (real compose Postgres, database bidradar_test) -------------------


@pytest.fixture(scope="session")
def migrated_db() -> None:
    """Fresh schema per test session: downgrade to base (or hard reset), then upgrade."""
    fresh_schema()


@pytest.fixture(scope="session")
async def database(migrated_db, settings):  # type: ignore[no-untyped-def]
    """Process Database bound to the test URLs (app role + owner role)."""
    from app.core.db import Database, set_database

    db = Database(settings.database_url, settings.database_url_owner)
    set_database(db)
    yield db
    set_database(None)
    await db.dispose()


@pytest.fixture()
async def clean_db(database):  # type: ignore[no-untyped-def]
    """Truncate every application table before a test (owner role, bypasses RLS)."""
    from app.models.base import Base
    from sqlalchemy import text

    names = [t.name for t in Base.metadata.sorted_tables if t.name not in SEED_TABLES]
    if names:
        joined = ", ".join(f'"{n}"' for n in names)
        async with database.owner_engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE TABLE {joined} RESTART IDENTITY CASCADE"))
    return database


@pytest.fixture()
def app(settings):  # type: ignore[no-untyped-def]
    from app.main import create_app

    return create_app(settings)


@pytest.fixture()
async def client(app):  # type: ignore[no-untyped-def]
    import httpx

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
