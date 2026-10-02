"""M7-01: the two Dockerfiles and the entrypoint keep the properties the spec requires.

These are cheap structural assertions, not a build — `docker build` runs in the
`docker-build` CI job (.github/workflows/ci.yml) and locally. They exist so a careless
edit (root user, dev dependencies, a missing OCR language pack) fails in seconds.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def backend_dockerfile(repo_root: Path) -> str:
    return (repo_root / "backend" / "Dockerfile").read_text()


@pytest.fixture(scope="module")
def frontend_dockerfile(repo_root: Path) -> str:
    return (repo_root / "frontend" / "Dockerfile").read_text()


@pytest.fixture(scope="module")
def entrypoint(repo_root: Path) -> str:
    return (repo_root / "backend" / "docker" / "entrypoint.sh").read_text()


def test_backend_is_multi_stage_on_python_312(backend_dockerfile: str) -> None:
    assert backend_dockerfile.count("\nFROM ") + backend_dockerfile.startswith("FROM ") >= 2
    assert "AS builder" in backend_dockerfile and "AS runtime" in backend_dockerfile
    assert "python:3.12-slim" in backend_dockerfile


def test_backend_installs_from_the_lockfile_without_dev_deps(backend_dockerfile: str) -> None:
    assert "uv sync --frozen --no-dev" in backend_dockerfile
    assert "uv sync --frozen --no-dev --no-install-project" in backend_dockerfile
    assert "--all-groups" not in backend_dockerfile, "dev dependencies must not ship"


def test_backend_carries_the_parsing_and_scanning_binaries(backend_dockerfile: str) -> None:
    # SPEC 10.1 Parsing row + SPEC 11 upload scan; OQ-10 (behind interfaces locally).
    for package in (
        "tesseract-ocr",
        "tesseract-ocr-eng",
        "tesseract-ocr-hin",
        "clamav-daemon",
        "clamav-freshclam",
        "libmagic1",
        "poppler-utils",
    ):
        assert package in backend_dockerfile, f"{package} missing from the runtime stage"


def test_backend_runs_as_a_non_root_user(backend_dockerfile: str) -> None:
    assert "USER 10001:10001" in backend_dockerfile
    assert backend_dockerfile.rstrip().splitlines()[-1].startswith("CMD ")
    user_at = backend_dockerfile.index("USER 10001:10001")
    assert "ENTRYPOINT" in backend_dockerfile[user_at:], "ENTRYPOINT must follow USER"


def test_backend_has_a_healthcheck_on_healthz(backend_dockerfile: str) -> None:
    assert "HEALTHCHECK" in backend_dockerfile
    assert "/healthz" in backend_dockerfile


def test_backend_entrypoint_supports_every_mode(entrypoint: str) -> None:
    for mode in ("api)", "worker)", "beat)", "job:*)", "migrate)", "seed)", "smoke)"):
        assert mode in entrypoint, f"entrypoint.sh has no {mode} branch"
    assert "uvicorn app.main:app" in entrypoint
    assert "celery -A app.celery_app worker" in entrypoint
    assert "celery -A app.celery_app beat" in entrypoint
    assert "app.jobs.run_source" in entrypoint
    assert "alembic upgrade head" in entrypoint
    assert "app.jobs.smoke" in entrypoint
    assert "app.seed" in entrypoint


def _run_migrations_body(entrypoint: str) -> str:
    """The body of the `run_migrations()` shell function, comments and all."""
    start = entrypoint.index("run_migrations() {")
    end = entrypoint.index("\n}\n", start)
    return entrypoint[start:end]


def _command_line(body: str, command: str) -> int:
    """Line number of the line that *runs* `command` (not one that mentions it)."""
    for number, line in enumerate(body.splitlines()):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith(command):
            return number
    raise AssertionError(f"no line runs {command!r}")


def _case_branch(entrypoint: str, mode: str) -> str:
    """One `case` arm of the entrypoint, from `  <mode>)` to its `    ;;`."""
    start = entrypoint.index(f"\n  {mode})\n")
    return entrypoint[start : entrypoint.index("\n    ;;", start)]


def test_migrate_bootstraps_a_managed_database_before_the_schema(entrypoint: str) -> None:
    """Railway prep: a managed Postgres has no initdb hook, so the extensions and the
    non-owner app role are created before alembic, and the seed after it."""
    body = _run_migrations_body(entrypoint)
    assert 'BOOTSTRAP_DB:-1}" = "1"' in body, "BOOTSTRAP_DB must default to on"
    # Match the COMMANDS, not the prose: the comments name alembic too.
    bootstrap_at = _command_line(body, "python -m app.jobs.bootstrap_db")
    alembic_at = _command_line(body, "alembic upgrade head")
    seed_at = _command_line(body, "python -m app.seed")
    assert bootstrap_at < alembic_at < seed_at, "order must be bootstrap -> migrate -> seed"


def test_only_the_schema_owner_migrates(entrypoint: str) -> None:
    """Every backend service shares one railway.json and therefore one
    preDeployCommand, so the gate is a variable: without RUN_MIGRATIONS=1 the function
    returns 0 immediately. Two concurrent `alembic upgrade head` runs would race."""
    body = _run_migrations_body(entrypoint)
    gate = body.index('RUN_MIGRATIONS:-0}" != "1"')
    ddl = body.index("python -m app.jobs.bootstrap_db")
    assert gate < ddl, "the RUN_MIGRATIONS gate must come before any DDL"
    assert "return 0" in body[gate:ddl], "the gate must return, not fall through"
    for mode in ("worker", "beat"):
        assert "run_migrations" not in _case_branch(entrypoint, mode), f"{mode} must never migrate"


def test_api_migrates_on_start_as_well_as_in_the_pre_deploy_hook(entrypoint: str) -> None:
    """Railway does not reliably run railway.json's preDeployCommand for a CLI-uploaded
    service, and a deploy that silently skips the migration fails later with
    `password authentication failed for user "bidradar_app"`. The api branch therefore
    calls the same idempotent function itself, still behind RUN_MIGRATIONS=1."""
    api = _case_branch(entrypoint, "api")
    assert "run_migrations" in api
    assert 'RUN_MIGRATIONS:-0}" = "1"' in api
    assert api.index("run_migrations") < api.index("exec uvicorn"), (
        "the schema must be current before the server accepts a request"
    )


def test_entrypoint_starts_clamd_only_when_configured(entrypoint: str) -> None:
    assert 'SCANNER_BACKEND:-noop}" = "clamav"' in entrypoint
    assert "clamd-start.sh" in entrypoint


def test_entrypoint_is_strict_and_executable(repo_root: Path, entrypoint: str) -> None:
    assert entrypoint.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in entrypoint
    for name in ("entrypoint.sh", "clamd-start.sh"):
        path = repo_root / "backend" / "docker" / name
        assert path.stat().st_mode & 0o111, f"{name} is not executable"


def test_frontend_builds_a_standalone_bundle(repo_root: Path, frontend_dockerfile: str) -> None:
    config = (repo_root / "frontend" / "next.config.ts").read_text()
    assert 'output: "standalone"' in config, "the runtime stage copies .next/standalone"
    assert "node:22-alpine" in frontend_dockerfile
    assert (
        "corepack" in frontend_dockerfile
        and "pnpm install --frozen-lockfile" in frontend_dockerfile
    )
    assert "/app/.next/standalone" in frontend_dockerfile
    assert "/app/.next/static" in frontend_dockerfile
    assert 'CMD ["node", "server.js"]' in frontend_dockerfile


def test_frontend_api_url_is_a_runtime_variable(frontend_dockerfile: str, repo_root: Path) -> None:
    """OQ-77: `NEXT_PUBLIC_*` is inlined at BUILD time, so one image could not serve two
    environments. The server-side proxy now reads plain `API_URL` at runtime; the build
    arg is only the fallback baked into the bundle."""
    assert "ARG NEXT_PUBLIC_API_URL" in frontend_dockerfile
    assert "ENV NEXT_PUBLIC_API_URL" in frontend_dockerfile
    # the old name stays as a deprecated alias so an existing pipeline keeps building
    assert "NEXT_PUBLIC_API_BASE_URL" in frontend_dockerfile
    assert "API_URL" in frontend_dockerfile.split("AS runtime", 1)[1], (
        "the runtime stage must document that API_URL wins at runtime"
    )
    client = (repo_root / "frontend" / "src" / "lib" / "api" / "client.ts").read_text()
    assert "process.env.API_URL ?? process.env.NEXT_PUBLIC_API_URL" in client


def test_frontend_runs_as_non_root_on_port_3000(frontend_dockerfile: str) -> None:
    assert "USER 10001:10001" in frontend_dockerfile
    assert "PORT=3000" in frontend_dockerfile
    assert "HOSTNAME=0.0.0.0" in frontend_dockerfile, "Next standalone binds 127.0.0.1 otherwise"


def test_dockerignores_exclude_secrets_and_caches(repo_root: Path) -> None:
    for sub, must_have in (
        ("backend", ("__pycache__/", "tests/", ".venv/", ".env")),
        ("frontend", ("node_modules", ".next", ".env")),
    ):
        text = (repo_root / sub / ".dockerignore").read_text()
        lines = {line.strip() for line in text.splitlines()}
        for entry in must_have:
            assert entry in lines, f"{sub}/.dockerignore does not exclude {entry}"
