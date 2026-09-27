"""Test database helpers shared by conftest modules (no pytest imports here)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]

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
