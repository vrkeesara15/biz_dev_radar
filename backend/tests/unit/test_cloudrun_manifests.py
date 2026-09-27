"""M7-01: infra/cloudrun is generated from the adapter registry and stays in sync.

The manifests are committed so a reviewer sees the Cloud Run surface in the diff; these
tests are what make the commit trustworthy — they regenerate the tree and fail on drift,
and they assert the properties the acceptance criteria name (one job per adapter, worker
min-instances/no-throttle, a single beat, REGION as the only residency switch).
"""

from __future__ import annotations

import json
import re
import stat
from pathlib import Path
from typing import Any

import pytest
import yaml
from app.core.config import SECRET_SETTINGS
from app.jobs import generate_cloudrun as gen

PLACEHOLDER_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")


@pytest.fixture(scope="module")
def cloudrun_dir(repo_root: Path) -> Path:
    return repo_root / "infra" / "cloudrun"


def _load(path: Path) -> dict[str, Any]:
    doc = yaml.safe_load(path.read_text())
    assert isinstance(doc, dict), path
    return doc


def test_committed_tree_is_up_to_date(cloudrun_dir: Path) -> None:
    problems = gen.check(cloudrun_dir)
    assert not problems, (
        "infra/cloudrun is stale; run `uv run python -m app.jobs.generate_cloudrun` "
        f"from backend/. Offending files: {problems}"
    )


def test_one_job_per_enabled_adapter(cloudrun_dir: Path) -> None:
    adapters = gen.enabled_adapters()
    assert adapters, "the registry has no enabled adapters"
    for row in adapters:
        path = cloudrun_dir / "jobs" / f"{gen.job_name(row['source_id'])}.yaml"
        assert path.is_file(), f"no Cloud Run job for adapter {row['source_id']}"
        doc = _load(path)
        assert doc["kind"] == "Job"
        container = doc["spec"]["template"]["spec"]["template"]["spec"]["containers"][0]
        assert container["args"] == [f"job:{row['source_id']}"]
        assert container["image"] == "${BACKEND_IMAGE}"


def test_no_job_for_a_disabled_adapter(cloudrun_dir: Path) -> None:
    """Documented stubs (enabled = False) must not get a job or a Scheduler trigger."""
    from app.adapters import registry
    from app.adapters.registry import load_builtin_adapters

    load_builtin_adapters()
    disabled = [sid for sid, cls in registry.registered().items() if not registry.is_enabled(cls)]
    assert disabled, "expected at least one documented stub adapter"
    for source_id in disabled:
        path = cloudrun_dir / "jobs" / f"{gen.job_name(source_id)}.yaml"
        assert not path.exists(), f"disabled adapter {source_id} must not have a job"


def test_services_present(cloudrun_dir: Path) -> None:
    for name in ("api", "worker", "beat", "frontend"):
        doc = _load(cloudrun_dir / "services" / f"{name}.yaml")
        assert doc["apiVersion"] == "serving.knative.dev/v1"
        assert doc["kind"] == "Service"
        assert doc["metadata"]["name"] == f"bidradar-{name}"
        assert doc["spec"]["traffic"] == [{"percent": 100, "latestRevision": True}]


def test_worker_keeps_its_cpu_and_one_instance(cloudrun_dir: Path) -> None:
    ann = _load(cloudrun_dir / "services" / "worker.yaml")["spec"]["template"]["metadata"][
        "annotations"
    ]
    assert ann["run.googleapis.com/cpu-throttling"] == "false"
    assert ann["autoscaling.knative.dev/minScale"] == "1"


def test_beat_is_a_singleton(cloudrun_dir: Path) -> None:
    ann = _load(cloudrun_dir / "services" / "beat.yaml")["spec"]["template"]["metadata"][
        "annotations"
    ]
    assert ann["autoscaling.knative.dev/minScale"] == "1"
    assert ann["autoscaling.knative.dev/maxScale"] == "1", "two beats double every schedule"


def test_api_probes_healthz(cloudrun_dir: Path) -> None:
    container = _load(cloudrun_dir / "services" / "api.yaml")["spec"]["template"]["spec"][
        "containers"
    ][0]
    assert container["startupProbe"]["httpGet"]["path"] == "/healthz"
    assert container["livenessProbe"]["httpGet"]["path"] == "/healthz"


def test_region_is_the_only_residency_switch(cloudrun_dir: Path) -> None:
    """Same image in us-east1 and asia-south1: REGION picks the bucket and the DB."""
    for name in ("api", "worker", "beat"):
        env = {
            item["name"]: item
            for item in _load(cloudrun_dir / "services" / f"{name}.yaml")["spec"]["template"][
                "spec"
            ]["containers"][0]["env"]
        }
        assert env["REGION"]["value"] == "${REGION}"
        assert env["GCS_BUCKET_US"]["value"] == "${GCS_BUCKET_US}"
        assert env["GCS_BUCKET_IN"]["value"] == "${GCS_BUCKET_IN}"
        # DATABASE_URL is a per-environment secret, never a literal in the manifest.
        assert "secretKeyRef" in env["DATABASE_URL"]["valueFrom"]


def test_deployed_containers_scan_and_ocr(cloudrun_dir: Path) -> None:
    """SPEC 11 (ClamAV before parsing) and 10.1 (Tesseract eng+hin) are on in the cloud."""
    env = {
        item["name"]: item.get("value")
        for item in _load(cloudrun_dir / "services" / "api.yaml")["spec"]["template"]["spec"][
            "containers"
        ][0]["env"]
    }
    assert env["SCANNER_BACKEND"] == "clamav"
    assert env["OCR_BACKEND"] == "tesseract"
    assert env["OCR_LANGUAGES"] == "eng+hin"
    assert env["TRUST_PROXY_HEADERS"] == "true"


def test_every_secret_setting_is_a_secret_ref(cloudrun_dir: Path) -> None:
    container = _load(cloudrun_dir / "services" / "api.yaml")["spec"]["template"]["spec"][
        "containers"
    ][0]
    by_name = {item["name"]: item for item in container["env"]}
    for setting in SECRET_SETTINGS:
        item = by_name[setting.upper()]
        assert "value" not in item, f"{setting} must never be a literal in a manifest"
        ref = item["valueFrom"]["secretKeyRef"]
        assert ref["name"] == f"${{SECRET_PREFIX}}-{setting.replace('_', '-')}"
        assert ref["key"] == "latest"


def test_migrate_job_exists_for_the_deploy_pipeline(cloudrun_dir: Path) -> None:
    doc = _load(cloudrun_dir / "jobs" / "migrate.yaml")
    spec = doc["spec"]["template"]["spec"]["template"]["spec"]
    assert spec["containers"][0]["args"] == ["migrate"]
    assert "DATABASE_URL_OWNER" in {e["name"] for e in spec["containers"][0]["env"]}


def test_placeholders_are_all_declared(cloudrun_dir: Path) -> None:
    used: set[str] = set()
    for path in sorted(cloudrun_dir.rglob("*.yaml")):
        used |= set(PLACEHOLDER_RE.findall(path.read_text()))
    # SECRET_PREFIX appears inside secret names; everything else is a top-level value.
    undeclared = used - set(gen.PLACEHOLDERS)
    assert not undeclared, f"placeholders missing from generate_cloudrun.PLACEHOLDERS: {undeclared}"
    unused = set(gen.PLACEHOLDERS) - used
    assert not unused, f"declared but never used: {unused}"


def test_render_script_substitutes_every_placeholder(cloudrun_dir: Path) -> None:
    script = (cloudrun_dir / "render.sh").read_text()
    for name in gen.PLACEHOLDERS:
        assert name in script, f"render.sh does not substitute {name}"
    assert cloudrun_dir.joinpath("render.sh").stat().st_mode & stat.S_IXUSR, "render.sh not +x"


def test_adapter_tfvars_matches_the_registry(cloudrun_dir: Path) -> None:
    data = json.loads((cloudrun_dir / "adapters.auto.tfvars.json").read_text())
    assert data["adapter_jobs"] == gen.enabled_adapters()
    for row in data["adapter_jobs"]:
        assert len(row["schedule"].split()) == 5, "Cloud Scheduler needs a 5-field cron"
        assert row["region"] in ("us", "in")


def test_readme_documents_every_placeholder(cloudrun_dir: Path) -> None:
    readme = (cloudrun_dir / "README.md").read_text()
    for name in gen.PLACEHOLDERS:
        assert f"`{name}`" in readme or f"`{name}`" in readme.replace("` / `", "` `"), name


def test_check_detects_a_stale_tree(tmp_path: Path) -> None:
    gen.write(tmp_path)
    assert gen.check(tmp_path) == []
    (tmp_path / "services" / "api.yaml").write_text("tampered\n")
    (tmp_path / "jobs" / "ghost.yaml").write_text("orphan\n")
    problems = gen.check(tmp_path)
    assert "services/api.yaml" in problems
    assert "jobs/ghost.yaml (no longer generated)" in problems
