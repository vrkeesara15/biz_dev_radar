"""M0-10: the CI workflow parses and wires the required jobs, services and gates."""

from pathlib import Path
from typing import Any

import yaml


def _workflow(repo_root: Path) -> dict[str, Any]:
    data = yaml.safe_load((repo_root / ".github" / "workflows" / "ci.yml").read_text())
    assert isinstance(data, dict)
    return data


def _steps_text(job: dict[str, Any]) -> str:
    return "\n".join(str(step.get("run", "")) + str(step.get("uses", "")) for step in job["steps"])


def test_triggers(repo_root: Path) -> None:
    wf = _workflow(repo_root)
    triggers = wf.get("on") or wf.get(True)  # PyYAML 1.1 reads bare `on:` as True
    assert triggers is not None
    assert "push" in triggers and "pull_request" in triggers


def test_jobs_present(repo_root: Path) -> None:
    jobs = _workflow(repo_root)["jobs"]
    assert {"backend-lint", "backend-test", "backend-isolation", "frontend-lint-build"} <= set(jobs)


def test_backend_test_services_and_gates(repo_root: Path) -> None:
    job = _workflow(repo_root)["jobs"]["backend-test"]
    services = job["services"]
    assert services["postgres"]["image"] == "pgvector/pgvector:pg16"
    assert services["redis"]["image"].startswith("redis:7")
    text = _steps_text(job)
    assert "01-init.sh" in text, "CI must create roles/dbs with the same init SQL as compose"
    assert "make test" in text, "coverage gate (--cov-fail-under=85) lives in make test"
    assert job["env"]["TEST_DATABASE_URL"].endswith("/bidradar_test")
    assert "bidradar_app" in job["env"]["TEST_DATABASE_URL"]


def test_backend_lint_runs_make_lint(repo_root: Path) -> None:
    job = _workflow(repo_root)["jobs"]["backend-lint"]
    assert "make lint" in _steps_text(job)


def test_isolation_is_its_own_job(repo_root: Path) -> None:
    job = _workflow(repo_root)["jobs"]["backend-isolation"]
    assert job["services"]["postgres"]["image"] == "pgvector/pgvector:pg16"
    assert "make isolation" in _steps_text(job)


def test_frontend_job(repo_root: Path) -> None:
    job = _workflow(repo_root)["jobs"]["frontend-lint-build"]
    assert "hashFiles('frontend/package.json')" in job["if"]
    assert job["defaults"]["run"]["working-directory"] == "frontend"
    text = _steps_text(job)
    for cmd in ("pnpm install --frozen-lockfile", "pnpm lint", "pnpm typecheck", "pnpm build"):
        assert cmd in text


def test_makefile_test_target_is_the_gate(repo_root: Path) -> None:
    text = (repo_root / "Makefile").read_text()
    assert "--cov-fail-under=85" in text and "alembic upgrade head" in text
