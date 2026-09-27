"""M0-02: compose stack definition and Postgres init scripts."""

from pathlib import Path

import yaml


def _compose(repo_root: Path) -> dict:  # type: ignore[type-arg]
    data = yaml.safe_load((repo_root / "infra" / "docker-compose.yml").read_text())
    assert isinstance(data, dict)
    return data


def test_services_images_and_healthchecks(repo_root: Path) -> None:
    services = _compose(repo_root)["services"]
    assert set(services) >= {"postgres", "redis", "minio", "mailpit"}
    assert services["postgres"]["image"] == "pgvector/pgvector:pg16"
    assert services["redis"]["image"].startswith("redis:7")
    for name in ("postgres", "redis", "minio", "mailpit"):
        assert "healthcheck" in services[name], f"{name} lacks a healthcheck"
        assert services[name]["volumes"], f"{name} lacks a volume"


def test_named_volumes_declared(repo_root: Path) -> None:
    data = _compose(repo_root)
    assert set(data["volumes"]) == {"postgres_data", "redis_data", "minio_data", "mailpit_data"}


def test_host_ports(repo_root: Path) -> None:
    services = _compose(repo_root)["services"]
    ports = {name: {p.split(":")[0] for p in svc["ports"]} for name, svc in services.items()}
    assert ports["postgres"] == {"5433"}
    assert ports["redis"] == {"6380"}
    assert ports["minio"] == {"9000", "9001"}
    assert ports["mailpit"] == {"8025", "1025"}


def test_init_scripts_create_dbs_roles_and_extensions(repo_root: Path) -> None:
    pg = repo_root / "infra" / "postgres"
    roles = (pg / "sql" / "roles.sql").read_text()
    ext = (pg / "sql" / "extensions.sql").read_text()
    assert "CREATE DATABASE bidradar_test" in roles
    assert "CREATE ROLE bidradar_app" in roles
    assert "NOBYPASSRLS" in roles
    assert "CREATE EXTENSION IF NOT EXISTS vector" in ext
    assert "CREATE EXTENSION IF NOT EXISTS pg_trgm" in ext
    init = (pg / "01-init.sh").read_text()
    assert "bidradar_test" in init
    assert (pg / "01-init.sh").stat().st_mode & 0o111, "init script must be executable"


def test_makefile_wraps_compose(repo_root: Path) -> None:
    text = (repo_root / "Makefile").read_text()
    assert "docker compose -f infra/docker-compose.yml" in text
    assert "DROP DATABASE IF EXISTS bidradar_test" in text
    assert "CREATE DATABASE bidradar_test" in text
