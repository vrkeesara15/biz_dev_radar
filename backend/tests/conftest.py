"""Shared fixtures. DB fixtures are added in M0-04; keep unit tests DB-free."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

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

    return Settings(_env_file=None)  # type: ignore[call-arg]


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
