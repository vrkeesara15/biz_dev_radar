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
def settings(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    from app.core.config import Settings

    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url=TEST_DATABASE_URL,
        database_url_owner=TEST_DATABASE_URL_OWNER,
        # local object storage under the session tmp dir (never the repo's .storage)
        local_storage_root=str(tmp_path_factory.mktemp("storage")),
        # deterministic embeddings and inline (eager) background jobs; no network
        embedding_provider="fake",
        celery_task_always_eager=True,
        # In-process mail: a route that sends (POST /tenant/members/invite) must not open
        # an SMTP connection from a test. The Mailpit test builds its own SMTPProvider.
        email_provider="memory",
        # The API rate limiter is off for the shared app fixture: every test would
        # otherwise share one bucket keyed on the test client's IP and start failing at
        # request 121. tests/integration/test_rate_limit_api.py builds its own app with
        # the limiter on and an in-process bucket.
        rate_limit_enabled=False,
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
def fake_embeddings():  # type: ignore[no-untyped-def]
    """Deterministic EmbeddingProvider (app/services/embeddings.FakeEmbeddings), installed
    process-wide for the test so jobs and the app share it; never touches the network."""
    from app.services.embeddings import FakeEmbeddings, set_embeddings

    provider = FakeEmbeddings()
    set_embeddings(provider)
    yield provider
    set_embeddings(None)


@pytest.fixture()
def app(settings, fake_embeddings):  # type: ignore[no-untyped-def]
    from app.main import create_app

    application = create_app(settings)
    application.state.embeddings = fake_embeddings
    return application


@pytest.fixture()
async def client(app):  # type: ignore[no-untyped-def]
    import httpx

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


@pytest.fixture()
async def api_client(app, clean_db):  # type: ignore[no-untyped-def]
    """HTTP client over the app with a clean, migrated database behind it."""
    import httpx

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("203.0.113.10", 51000)),
        base_url="http://test",
    ) as ac:
        yield ac


@pytest.fixture()
def fake_llm():  # type: ignore[no-untyped-def]
    """Queued-response LLM double (tests/llm_fake.py); never touches the network."""
    from tests.llm_fake import FakeLLM

    return FakeLLM()
