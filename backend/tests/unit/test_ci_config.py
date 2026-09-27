"""M0-10: the CI workflow parses and wires the required jobs, services and gates.

Extended by M7-01 (docker-build), M7-02 (terraform-validate) and M7-03/M7-06
(deploy, preview, security-scan). Workflow YAML is the one kind of infrastructure that
never runs locally, so it gets asserted here instead.
"""

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


def test_docker_build_job_builds_both_images(repo_root: Path) -> None:
    """M7-01 acceptance: `docker build` succeeds in CI, for the backend and the frontend."""
    job = _workflow(repo_root)["jobs"]["docker-build"]
    contexts = {entry["context"] for entry in job["strategy"]["matrix"]["include"]}
    assert contexts == {"backend", "frontend"}
    build_steps = [s for s in job["steps"] if "build-push-action" in str(s.get("uses", ""))]
    assert build_steps, "docker-build must use docker/build-push-action"
    for step in build_steps:
        assert step["with"]["push"] is False, "CI builds, the deploy workflow pushes"


def test_dockerfiles_exist_for_every_build_context(repo_root: Path) -> None:
    job = _workflow(repo_root)["jobs"]["docker-build"]
    for entry in job["strategy"]["matrix"]["include"]:
        assert (repo_root / entry["context"] / "Dockerfile").is_file()


def test_terraform_validate_job_covers_every_environment(repo_root: Path) -> None:
    """M7-02 acceptance: terraform validate and fmt -check pass in CI."""
    job = _workflow(repo_root)["jobs"]["terraform-validate"]
    assert job["defaults"]["run"]["working-directory"] == "infra/terraform"
    text = _steps_text(job)
    assert "terraform fmt -check -recursive" in text
    assert 'terraform -chdir="${env_dir}" validate' in text
    # No credentials: the configuration is checked, not planned against a project.
    assert "-backend=false" in text
    assert "hashicorp/setup-terraform" in text
    envs = {p.name for p in (repo_root / "infra" / "terraform" / "envs").iterdir() if p.is_dir()}
    assert envs == {"dev", "staging-in", "prod-us", "prod-in"}
