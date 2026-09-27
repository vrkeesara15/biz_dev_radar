"""M7-02: the Terraform tree parses and says what SPEC 11 and SPEC 12 require it to say.

`terraform validate` and `terraform fmt -check` run in CI (the `terraform-validate` job)
and catch HCL errors. These tests catch the things validate cannot see: that the four
environments are the four the spec names, in the right regions; that backups and PITR are
not quietly weakened; that production keeps deletion protection; and that the two lists
Terraform shares with the Python code — SECRET_SETTINGS and the adapter registry — have
not drifted.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import hcl2
import pytest
from app.core.config import SECRET_SETTINGS

# SPEC 12's table: four environments, two regions, two residencies.
ENVIRONMENTS: dict[str, tuple[str, str]] = {
    "dev": ("us-east1", "us"),
    "staging-in": ("asia-south1", "in"),
    "prod-us": ("us-east1", "us"),
    "prod-in": ("asia-south1", "in"),
}
PRODUCTION = ("staging-in", "prod-us", "prod-in")

MODULES = (
    "network",
    "cloud_sql",
    "memorystore",
    "gcs",
    "secrets",
    "iam",
    "artifact_registry",
    "cloud_run_service",
    "cloud_run_job",
    "scheduler",
    "monitoring",
    "environment",
)


def _unquote(value: Any) -> Any:
    """python-hcl2 keeps the source quotes on literal strings."""
    if isinstance(value, str) and len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    if isinstance(value, list):
        return [_unquote(item) for item in value]
    return value


def _load(path: Path) -> dict[str, Any]:
    with path.open() as handle:
        data: dict[str, Any] = hcl2.load(handle)
    return data


def _blocks(doc: dict[str, Any], kind: str) -> dict[str, Any]:
    """`{name: body}` for a singly-labelled block type (variable, output, module)."""
    out: dict[str, Any] = {}
    for entry in doc.get(kind, []):
        for name, body in entry.items():
            out[_unquote(name)] = body
    return out


def _resource(doc: dict[str, Any], resource_type: str, name: str = "this") -> dict[str, Any]:
    """One `resource "<type>" "<name>"` body. python-hcl2 keeps the quotes on both labels."""
    for entry in doc.get("resource", []):
        for kind, bodies in entry.items():
            if _unquote(kind) != resource_type:
                continue
            for label, body in bodies.items():
                if _unquote(label) == name:
                    assert isinstance(body, dict)
                    return body
    raise AssertionError(f'resource "{resource_type}" "{name}" not found')


@pytest.fixture(scope="module")
def tf_root(repo_root: Path) -> Path:
    return repo_root / "infra" / "terraform"


# ------------------------------------------------------------------ shape


def test_every_module_exists_and_parses(tf_root: Path) -> None:
    for name in MODULES:
        main = tf_root / "modules" / name / "main.tf"
        assert main.is_file(), f"module {name} has no main.tf"
        assert _load(main), f"module {name}/main.tf parsed empty"
        assert (tf_root / "modules" / name / "variables.tf").is_file()
        assert (tf_root / "modules" / name / "outputs.tf").is_file()


def test_every_hcl_file_parses(tf_root: Path) -> None:
    files = sorted(tf_root.rglob("*.tf"))
    assert len(files) >= 30, "expected the full module tree"
    for path in files:
        if ".terraform" in path.parts:
            continue
        _load(path)


def test_the_four_environments_are_the_spec_ones(tf_root: Path) -> None:
    dirs = {p.name for p in (tf_root / "envs").iterdir() if p.is_dir()}
    assert dirs == set(ENVIRONMENTS)


@pytest.mark.parametrize("env", sorted(ENVIRONMENTS))
def test_environment_region_and_residency(tf_root: Path, env: str) -> None:
    region, residency = ENVIRONMENTS[env]
    module = _blocks(_load(tf_root / "envs" / env / "main.tf"), "module")["bidradar"]
    assert _unquote(module["environment"]) == env
    assert _unquote(module["residency"]) == residency
    assert _unquote(module["source"]) == "../../modules/environment"

    tfvars = tf_root / "envs" / env / "terraform.tfvars"
    values = {k: _unquote(v) for k, v in _load(tfvars).items()}
    assert values["region"] == region, f"{env} must deploy to {region} (SPEC 12)"


@pytest.mark.parametrize("env", sorted(ENVIRONMENTS))
def test_environment_uses_a_gcs_backend_with_a_bucket(tf_root: Path, env: str) -> None:
    versions = _load(tf_root / "envs" / env / "versions.tf")
    backends = versions["terraform"][0]["backend"]
    declared = {_unquote(key) for block in backends for key in block}
    assert declared == {"gcs"}, "state lives in GCS, in the environment's own region"
    # A backend block cannot read variables, so the bucket is a committed partial config.
    hcl = (tf_root / "envs" / env / "backend.hcl").read_text()
    assert "bucket" in hcl and env in hcl
    values = {k: _unquote(v) for k, v in _load(tf_root / "envs" / env / "terraform.tfvars").items()}
    assert values["state_bucket"] in hcl


# ------------------------------------------------------------------ SPEC 11 backups


def test_cloud_sql_is_postgres_16_with_private_ip(tf_root: Path) -> None:
    doc = _load(tf_root / "modules" / "cloud_sql" / "main.tf")
    instance = _resource(doc, "google_sql_database_instance")
    assert _unquote(instance["database_version"]) == "POSTGRES_16"
    settings = instance["settings"][0]
    ip = settings["ip_configuration"][0]
    assert ip["ipv4_enabled"] is False, "the database must have no public address"


def test_backups_are_daily_with_seven_day_pitr(tf_root: Path) -> None:
    doc = _load(tf_root / "modules" / "cloud_sql" / "main.tf")
    instance = _resource(doc, "google_sql_database_instance")
    backup = instance["settings"][0]["backup_configuration"][0]
    assert backup["enabled"] is True
    assert backup["point_in_time_recovery_enabled"] is True
    assert "transaction_log_retention_days" in backup

    variables = _blocks(_load(tf_root / "modules" / "cloud_sql" / "variables.tf"), "variable")
    retention = variables["transaction_log_retention_days"]
    assert retention["default"] == 7, "SPEC 11 requires a 7-day PITR window"
    # And it cannot be lowered by a tfvars file without the plan failing.
    assert retention["validation"][0]["condition"].endswith(">= 7}")


def test_production_environments_protect_the_database_and_the_bucket(tf_root: Path) -> None:
    for env in PRODUCTION:
        module = _blocks(_load(tf_root / "envs" / env / "main.tf"), "module")["bidradar"]
        assert module["sql_deletion_protection"] is True, env
        assert module["bucket_force_destroy"] is False, env
    dev = _blocks(_load(tf_root / "envs" / "dev" / "main.tf"), "module")["bidradar"]
    assert dev["sql_deletion_protection"] is False, "dev is disposable on purpose"


def test_both_production_regions_run_highly_available_postgres(tf_root: Path) -> None:
    for env in ("prod-us", "prod-in"):
        module = _blocks(_load(tf_root / "envs" / env / "main.tf"), "module")["bidradar"]
        assert _unquote(module["sql_availability_type"]) == "REGIONAL", env


# ------------------------------------------------------------------ SPEC 11 CMEK / files


def test_buckets_use_cmek_and_are_never_public(tf_root: Path) -> None:
    doc = _load(tf_root / "modules" / "gcs" / "main.tf")
    bucket = _resource(doc, "google_storage_bucket")
    assert bucket["uniform_bucket_level_access"] is True
    assert _unquote(bucket["public_access_prevention"]) == "enforced"
    assert "default_kms_key_name" in bucket["encryption"][0]

    keyring = _resource(doc, "google_kms_key_ring")
    # The key lives where the bucket lives: an Indian bucket gets an Indian key.
    assert "var.location" in keyring["location"]


def test_bucket_lifecycle_keeps_raw_payloads_and_expires_exports(tf_root: Path) -> None:
    variables = _blocks(_load(tf_root / "modules" / "gcs" / "variables.tf"), "variable")
    assert variables["raw_retention_days"]["default"] == 400
    assert variables["export_retention_days"]["default"] == 30
    text = (tf_root / "modules" / "gcs" / "main.tf").read_text()
    assert '"raw/"' in text and '"exports/"' in text


# ------------------------------------------------------------------ shared lists


def test_secret_settings_match_the_python_list(tf_root: Path) -> None:
    variables = _blocks(_load(tf_root / "modules" / "environment" / "variables.tf"), "variable")
    default = [_unquote(name) for name in variables["secret_settings"]["default"]]
    assert default == list(SECRET_SETTINGS), (
        "modules/environment/variables.tf secret_settings has drifted from "
        "app.core.config.SECRET_SETTINGS"
    )


def test_no_secret_value_is_committed_in_terraform(tf_root: Path) -> None:
    """Terraform creates the secret containers; the values come from elsewhere."""
    text = "\n".join(
        path.read_text() for path in sorted(tf_root.rglob("*.tf")) if ".terraform" not in path.parts
    )
    for marker in ("sk-ant-", "sk_live_", "rzp_live_", "AKIA", "BEGIN PRIVATE KEY"):
        assert marker not in text
    # The only secret versions Terraform writes are the DSNs it generated itself.
    secrets_main = (tf_root / "modules" / "secrets" / "main.tf").read_text()
    assert "secret_data = var.managed_values[each.value]" in secrets_main


def test_scheduler_is_driven_by_the_generated_adapter_list(tf_root: Path, repo_root: Path) -> None:
    text = (tf_root / "modules" / "environment" / "main.tf").read_text()
    assert "adapters.auto.tfvars.json" in text, "the cron list must come from the registry"
    generated = json.loads(
        (repo_root / "infra" / "cloudrun" / "adapters.auto.tfvars.json").read_text()
    )
    assert generated["adapter_jobs"], "no adapters would be scheduled"


def test_environments_schedule_only_their_own_regions_adapters(tf_root: Path) -> None:
    expected = {
        "dev": ["us", "in"],
        "staging-in": ["in"],
        "prod-us": ["us"],
        "prod-in": ["in"],
    }
    for env, regions in expected.items():
        module = _blocks(_load(tf_root / "envs" / env / "main.tf"), "module")["bidradar"]
        assert [_unquote(r) for r in module["scheduled_adapter_regions"]] == regions, env


# ------------------------------------------------------------------ SPEC 12 residency


def test_residency_outputs_document_bucket_and_database_locations(tf_root: Path) -> None:
    outputs = _blocks(_load(tf_root / "modules" / "environment" / "outputs.tf"), "output")
    residency = outputs["residency"]["value"]
    for field in ("bucket_location", "cmek_location", "database_region", "backup_location"):
        assert field in residency, f"the residency output must expose {field}"
    assert "residency_ok" in outputs
    assert "backups" in outputs

    for env in ENVIRONMENTS:
        env_outputs = _blocks(_load(tf_root / "envs" / env / "outputs.tf"), "output")
        assert {"residency", "residency_ok", "backups"} <= set(env_outputs), env


def test_a_region_residency_mismatch_fails_the_plan(tf_root: Path) -> None:
    text = (tf_root / "modules" / "environment" / "main.tf").read_text()
    assert 'check "residency_matches_region"' in text
    variables = _blocks(_load(tf_root / "modules" / "environment" / "variables.tf"), "variable")
    assert "validation" in variables["region"], "only the two spec regions are allowed"
    assert "validation" in variables["environment"]


def test_secrets_replicate_inside_the_environment_region(tf_root: Path) -> None:
    text = (tf_root / "modules" / "secrets" / "main.tf").read_text()
    assert "user_managed" in text, "automatic replication would copy Indian secrets abroad"
    assert "location = var.replication_location" in text
