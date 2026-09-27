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
