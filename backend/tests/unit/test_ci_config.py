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


# ------------------------------------------------------------------ M7-03 deploy


def _yaml(repo_root: Path, name: str) -> dict[str, Any]:
    data = yaml.safe_load((repo_root / ".github" / "workflows" / name).read_text())
    assert isinstance(data, dict)
    return data


def _triggers(workflow: dict[str, Any]) -> Any:
    return workflow.get("on") or workflow.get(True)  # PyYAML 1.1 reads bare `on:` as True


def test_deploy_runs_on_main_and_on_version_tags(repo_root: Path) -> None:
    triggers = _triggers(_yaml(repo_root, "deploy.yml"))
    assert triggers["push"]["branches"] == ["main"]
    assert triggers["push"]["tags"] == ["v*"]


def test_deploy_uses_workload_identity_and_never_a_json_key(repo_root: Path) -> None:
    text = (repo_root / ".github" / "workflows" / "deploy.yml").read_text()
    action = (repo_root / ".github" / "actions" / "deploy-cloud-run" / "action.yml").read_text()
    for source in (text, action):
        assert "google-github-actions/auth@v2" in source
        assert "workload_identity_provider" in source
        assert "credentials_json" not in source, "a JSON key must never appear (SPEC 11)"
    assert _yaml(repo_root, "deploy.yml")["permissions"]["id-token"] == "write"


def test_merge_to_main_builds_then_deploys_dev(repo_root: Path) -> None:
    jobs = _yaml(repo_root, "deploy.yml")["jobs"]
    assert "refs/heads/main" in jobs["build"]["if"]
    assert jobs["deploy-dev"]["needs"] == "build"
    assert jobs["deploy-dev"]["environment"]["name"] == "dev"


def test_a_tag_promotes_the_same_digest_and_never_rebuilds(repo_root: Path) -> None:
    """SPEC 12: production runs the same image staging-in ran."""
    jobs = _yaml(repo_root, "deploy.yml")["jobs"]
    resolve = jobs["resolve"]
    assert "refs/tags/v" in resolve["if"]
    resolve_text = _steps_text(resolve)
    assert "build-push-action" not in resolve_text, "a promotion must not rebuild the image"
    assert "gcloud artifacts docker images describe" in resolve_text

    for name in ("deploy-staging-in", "deploy-prod"):
        job = jobs[name]
        images = [
            step["with"]["backend_image"]
            for step in job["steps"]
            if isinstance(step.get("with"), dict) and "backend_image" in step["with"]
        ]
        assert images, name
        for value in images:
            assert "needs.resolve.outputs.backend_image" in value, name

    # The composite action refuses anything that is not pinned by digest.
    action = (repo_root / ".github" / "actions" / "deploy-cloud-run" / "action.yml").read_text()
    assert "*@sha256:*" in action


def test_promotion_order_is_staging_then_both_productions(repo_root: Path) -> None:
    jobs = _yaml(repo_root, "deploy.yml")["jobs"]
    assert jobs["deploy-staging-in"]["environment"]["name"] == "staging-in"
    prod = jobs["deploy-prod"]
    assert "deploy-staging-in" in prod["needs"]
    environments = {entry["environment"] for entry in prod["strategy"]["matrix"]["include"]}
    assert environments == {"prod-us", "prod-in"}
    regions = {
        entry["environment"]: entry["region"] for entry in prod["strategy"]["matrix"]["include"]
    }
    assert regions == {"prod-us": "us-east1", "prod-in": "asia-south1"}
    # Each production gets its own protected environment, so each needs its own approval.
    assert prod["environment"]["name"] == "${{ matrix.environment }}"


def test_migrations_run_as_a_job_before_the_traffic_shift(repo_root: Path) -> None:
    action = yaml.safe_load(
        (repo_root / ".github" / "actions" / "deploy-cloud-run" / "action.yml").read_text()
    )
    names = [str(step.get("name", step.get("id", ""))) for step in action["runs"]["steps"]]
    blob = "\n".join(str(step.get("run", "")) for step in action["runs"]["steps"])
    assert "gcloud run jobs execute" in blob and "--wait" in blob
    migrate_at = next(i for i, name in enumerate(names) if "migration" in name.lower())
    traffic_at = next(i for i, name in enumerate(names) if "traffic" in name.lower())
    assert migrate_at < traffic_at, "the schema must be migrated before traffic moves"
    assert "-migrate" in blob, "the migrate Cloud Run job is what runs alembic"


def test_preview_deploys_a_tagged_zero_traffic_revision(repo_root: Path) -> None:
    workflow = _yaml(repo_root, "preview.yml")
    triggers = _triggers(workflow)
    assert set(triggers["pull_request"]["types"]) == {
        "opened",
        "synchronize",
        "reopened",
        "closed",
    }
    deploy = workflow["jobs"]["deploy"]
    assert "closed" in deploy["if"] and "head.repo.full_name == github.repository" in deploy["if"]
    text = _steps_text(deploy)
    assert '--tag "${TAG}"' in text and "--no-traffic" in text
    assert "pr-${{ github.event.pull_request.number }}" in (
        (repo_root / ".github" / "workflows" / "preview.yml").read_text()
    )
    assert "github-script" in text, "the PR gets a comment with the URL"


def test_preview_is_torn_down_when_the_pr_closes(repo_root: Path) -> None:
    teardown = _yaml(repo_root, "preview.yml")["jobs"]["teardown"]
    assert teardown["if"] == "github.event.action == 'closed'"
    text = _steps_text(teardown)
    assert "--remove-tags" in text


def test_deploy_runbook_exists(repo_root: Path) -> None:
    runbook = repo_root / "docs" / "runbooks" / "deploy.md"
    assert runbook.is_file()
    text = runbook.read_text()
    for topic in ("rollback", "preview", "migration"):
        assert topic in text.lower(), f"the runbook does not cover {topic}"


# --------------------------------------------------------------------------- load (M7-10)


def test_load_workflow_is_nightly_and_manual(repo_root: Path) -> None:
    """SPEC 12's load target runs unattended, so a regression is caught by a red night."""
    triggers = _triggers(_yaml(repo_root, "load.yml"))
    assert "workflow_dispatch" in triggers
    assert triggers["schedule"] == [{"cron": "30 3 * * *"}]


def test_load_workflow_runs_the_smoke_against_its_own_database(repo_root: Path) -> None:
    job = _yaml(repo_root, "load.yml")["jobs"]["load-smoke"]
    assert job["services"]["postgres"]["image"] == "pgvector/pgvector:pg16"
    assert job["services"]["redis"]["image"].startswith("redis:7")
    text = _steps_text(job)
    assert "make load-smoke" in text
    assert "01-init.sh" in text and "CREATE DATABASE bidradar_load" in text
    assert job["env"]["LOAD_DATABASE_URL"].endswith("/bidradar_load")
    assert job["env"]["LOAD_DATABASE_URL_OWNER"].endswith("/bidradar_load")
    upload = [s for s in job["steps"] if "upload-artifact" in str(s.get("uses", ""))]
    assert upload and upload[0]["with"]["path"] == "load-report/"


def test_make_load_smoke_runs_all_three_scripts(repo_root: Path) -> None:
    text = (repo_root / "Makefile").read_text()
    assert "load-smoke:" in text and "load-db:" in text and "load-full:" in text
    for script in ("seed.py", "score.py", "search.py"):
        assert f"$(LOAD_SCRIPTS)/{script}" in text
    assert "bidradar_load" in text, "a load run must never share bidradar_test"
    assert "--reset" in text and "--report" in text
    # the load scripts are linted with the backend's ruff configuration
    assert "ruff check --config pyproject.toml $(LOAD_SCRIPTS)" in text
