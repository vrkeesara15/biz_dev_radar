"""M0-01: directory layout matches SPEC 13.1 and tooling files exist."""

from pathlib import Path

import pytest

REQUIRED_DIRS = [
    "backend/app/api",
    "backend/app/core",
    "backend/app/models",
    "backend/app/services",
    "backend/app/agents",
    "backend/app/adapters",
    "backend/app/notify",
    "backend/migrations",
    "backend/tests/unit",
    "backend/tests/adapters/fixtures",
    "backend/tests/integration",
    "backend/tests/isolation",
    "backend/tests/evals",
    "frontend",
    "infra/terraform",
    "infra/cloudrun",
    "evals/golden/us",
    "evals/golden/in",
    ".github/workflows",
]


@pytest.mark.parametrize("rel", REQUIRED_DIRS)
def test_required_dir_exists(repo_root: Path, rel: str) -> None:
    assert (repo_root / rel).is_dir(), f"missing directory {rel}"


def test_uv_files(backend_root: Path) -> None:
    assert (backend_root / "pyproject.toml").is_file()
    assert (backend_root / "uv.lock").is_file(), "uv.lock must be committed"


def test_makefile_targets(repo_root: Path) -> None:
    text = (repo_root / "Makefile").read_text()
    for target in ("lint", "format", "test", "eval", "up", "down", "db-reset", "seed", "migrate"):
        assert f"\n{target}:" in text, f"Makefile missing target {target}"
    assert "ruff format --check" in text
    assert "ruff check" in text
    assert "mypy" in text
    assert "--cov=app/core" in text
    assert "--cov-fail-under=85" in text
    assert "tests/evals" in text


def test_gitignore(repo_root: Path) -> None:
    lines = {ln.strip() for ln in (repo_root / ".gitignore").read_text().splitlines()}
    for needed in (".env", ".venv/", "node_modules/", "logs/*.jsonl", "__pycache__/"):
        assert needed in lines, f".gitignore missing {needed}"
