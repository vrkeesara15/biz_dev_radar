"""Generate the Cloud Run manifests in `infra/cloudrun/` from the adapter registry.

    python -m app.jobs.generate_cloudrun            # write infra/cloudrun/**
    python -m app.jobs.generate_cloudrun --check    # fail if the tree is stale

This is a BUILD-TIME tool (it imports PyYAML, a dev dependency) — nothing in the deployed
image runs it. The generated tree is committed so a reviewer sees the Cloud Run surface in
the diff, and `tests/unit/test_cloudrun_manifests.py` regenerates it and fails on drift.

What is generated (SPEC 10.1: "Cloud Scheduler -> Cloud Run jobs, one per adapter"):

  services/{api,worker,beat,frontend}.yaml  Knative serving Services
  jobs/<source_id>.yaml                     one Job per ENABLED adapter, args ["job:<id>"]
  jobs/migrate.yaml                         alembic upgrade head, run before a traffic shift
  jobs/smoke.yaml                           the nightly live smoke, runnable in-region
  adapters.auto.tfvars.json                 {source_id, schedule, region} for Terraform

Every environment-specific value is a `${PLACEHOLDER}` (see PLACEHOLDERS). Terraform is the
authoritative deployer; `gcloud run services replace` users render these with
`infra/cloudrun/render.sh`, which substitutes the same names.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from app.adapters import registry
from app.adapters.registry import load_builtin_adapters
from app.core.config import SECRET_SETTINGS

SERVING_API = "serving.knative.dev/v1"
JOB_API = "run.googleapis.com/v1"
NAME_PREFIX = "bidradar"

# Placeholders the deploy pipeline (or render.sh) substitutes. Keep in sync with
# infra/cloudrun/README.md; the manifest test asserts no other ${...} appears.
PLACEHOLDERS: tuple[str, ...] = (
    "APP_ENV",
    "BACKEND_IMAGE",
    "CLOUD_SQL_INSTANCE",
    "CORS_ORIGINS",
    "FRONTEND_IMAGE",
    "GCP_REGION",
    "GCS_BUCKET_IN",
    "GCS_BUCKET_US",
    "NEXT_PUBLIC_API_BASE_URL",
    "REGION",
    "SECRET_PREFIX",
    "SERVICE_ACCOUNT",
    "VPC_CONNECTOR",
)

# Non-secret settings every backend container needs. REGION is the residency switch: the
# SAME image runs in us-east1 and asia-south1 and picks its bucket (GCS_BUCKET_US /
# GCS_BUCKET_IN) and DSN through app/core/config.py from these values alone.
BACKEND_ENV: tuple[tuple[str, str], ...] = (
    ("APP_ENV", "${APP_ENV}"),
    ("REGION", "${REGION}"),
    ("STORAGE_BACKEND", "gcs"),
    ("GCS_BUCKET_US", "${GCS_BUCKET_US}"),
    ("GCS_BUCKET_IN", "${GCS_BUCKET_IN}"),
    ("SCANNER_BACKEND", "clamav"),
    ("CLAMAV_HOST", "127.0.0.1"),
    ("OCR_BACKEND", "tesseract"),
    ("OCR_LANGUAGES", "eng+hin"),
    ("TRUST_PROXY_HEADERS", "true"),
    ("CORS_ORIGINS", "${CORS_ORIGINS}"),
    ("LOG_LEVEL", "INFO"),
)


def secret_env() -> list[dict[str, Any]]:
    """One env var per credential, sourced from Secret Manager (never from the image)."""
    return [
        {
            "name": name.upper(),
            "valueFrom": {
                "secretKeyRef": {
                    "name": f"${{SECRET_PREFIX}}-{name.replace('_', '-')}",
                    "key": "latest",
                }
            },
        }
        for name in SECRET_SETTINGS
    ]


def backend_env() -> list[dict[str, Any]]:
    return [{"name": name, "value": value} for name, value in BACKEND_ENV] + secret_env()


def _service(
    name: str,
    *,
    image: str,
    args: list[str] | None,
    env: list[dict[str, Any]],
    cpu: str,
    memory: str,
    min_scale: int,
    max_scale: int,
    concurrency: int,
    port: int = 8080,
    cpu_always_on: bool = False,
    sql: bool = True,
    probe: bool = True,
) -> dict[str, Any]:
    annotations: dict[str, str] = {
        "autoscaling.knative.dev/minScale": str(min_scale),
        "autoscaling.knative.dev/maxScale": str(max_scale),
        "run.googleapis.com/execution-environment": "gen2",
        "run.googleapis.com/vpc-access-connector": "${VPC_CONNECTOR}",
        "run.googleapis.com/vpc-access-egress": "private-ranges-only",
    }
    if sql:
        annotations["run.googleapis.com/cloudsql-instances"] = "${CLOUD_SQL_INSTANCE}"
    if cpu_always_on:
        # Celery keeps working between requests; throttled CPU would stall the worker.
        annotations["run.googleapis.com/cpu-throttling"] = "false"
    else:
        annotations["run.googleapis.com/startup-cpu-boost"] = "true"

    container: dict[str, Any] = {
        "image": image,
        "ports": [{"name": "http1", "containerPort": port}],
        "env": env,
        "resources": {"limits": {"cpu": cpu, "memory": memory}},
    }
    if args is not None:
        container["args"] = args
    if probe:
        container["startupProbe"] = {
            "httpGet": {"path": "/healthz", "port": port},
            "initialDelaySeconds": 5,
            "periodSeconds": 5,
            "failureThreshold": 30,
            "timeoutSeconds": 5,
        }
        container["livenessProbe"] = {
            "httpGet": {"path": "/healthz", "port": port},
            "periodSeconds": 30,
            "timeoutSeconds": 5,
            "failureThreshold": 3,
        }
    return {
        "apiVersion": SERVING_API,
        "kind": "Service",
        "metadata": {
            "name": f"{NAME_PREFIX}-{name}",
            "labels": {"cloud.googleapis.com/location": "${GCP_REGION}", "app": NAME_PREFIX},
            "annotations": {
                "run.googleapis.com/launch-stage": "GA",
                "run.googleapis.com/ingress": "all" if name in {"api", "frontend"} else "internal",
            },
        },
        "spec": {
            "template": {
                "metadata": {"annotations": annotations},
                "spec": {
                    "serviceAccountName": "${SERVICE_ACCOUNT}",
                    "containerConcurrency": concurrency,
                    "timeoutSeconds": 300,
                    "containers": [container],
                },
            },
            "traffic": [{"percent": 100, "latestRevision": True}],
        },
    }


def _job(
    name: str,
    *,
    args: list[str],
    timeout_seconds: int,
    memory: str = "2Gi",
    cpu: str = "1",
    max_retries: int = 1,
) -> dict[str, Any]:
    return {
        "apiVersion": JOB_API,
        "kind": "Job",
        "metadata": {
            "name": f"{NAME_PREFIX}-job-{name}",
            "labels": {"cloud.googleapis.com/location": "${GCP_REGION}", "app": NAME_PREFIX},
            "annotations": {"run.googleapis.com/launch-stage": "GA"},
        },
        "spec": {
            "template": {
                "spec": {
                    "parallelism": 1,
                    "taskCount": 1,
                    "template": {
                        "spec": {
                            "serviceAccountName": "${SERVICE_ACCOUNT}",
                            "maxRetries": max_retries,
                            "timeoutSeconds": timeout_seconds,
                            "containers": [
                                {
                                    "image": "${BACKEND_IMAGE}",
                                    "args": args,
                                    "env": backend_env(),
                                    "resources": {"limits": {"cpu": cpu, "memory": memory}},
                                }
                            ],
                        },
                        "metadata": {
                            "annotations": {
                                "run.googleapis.com/cloudsql-instances": "${CLOUD_SQL_INSTANCE}",
                                "run.googleapis.com/vpc-access-connector": "${VPC_CONNECTOR}",
                                "run.googleapis.com/vpc-access-egress": "private-ranges-only",
                            }
                        },
                    },
                }
            }
        },
    }


def enabled_adapters() -> list[dict[str, str]]:
    """[{source_id, schedule, region}] for every enabled adapter, sorted by source_id."""
    load_builtin_adapters()
    rows = [
        {"source_id": source_id, "schedule": cls.schedule, "region": cls.region}
        for source_id, cls in registry.registered().items()
        if registry.is_enabled(cls)
    ]
    return sorted(rows, key=lambda row: row["source_id"])


def job_name(source_id: str) -> str:
    """Cloud Run names are RFC1035: lowercase letters, digits and hyphens."""
    return source_id.replace("_", "-")


def build_manifests() -> dict[str, dict[str, Any]]:
    """Relative path under infra/cloudrun -> manifest dict."""
    manifests: dict[str, dict[str, Any]] = {
        "services/api.yaml": _service(
            "api",
            image="${BACKEND_IMAGE}",
            args=["api"],
            env=backend_env(),
            cpu="2",
            memory="2Gi",
            min_scale=1,
            max_scale=20,
            concurrency=80,
        ),
        "services/worker.yaml": _service(
            "worker",
            image="${BACKEND_IMAGE}",
            args=["worker"],
            env=backend_env(),
            cpu="2",
            memory="4Gi",
            min_scale=1,
            max_scale=5,
            concurrency=1,
            cpu_always_on=True,
        ),
        "services/beat.yaml": _service(
            "beat",
            image="${BACKEND_IMAGE}",
            args=["beat"],
            env=backend_env(),
            cpu="1",
            memory="1Gi",
            # Exactly one beat: two schedulers would double every periodic task.
            min_scale=1,
            max_scale=1,
            concurrency=1,
            cpu_always_on=True,
        ),
        "services/frontend.yaml": _service(
            "frontend",
            image="${FRONTEND_IMAGE}",
            args=None,
            env=[
                {"name": "NODE_ENV", "value": "production"},
                {"name": "PORT", "value": "3000"},
                # Auth.js v5 rejects the request host unless it is told a proxy
                # terminates TLS; Cloud Run always does (X-Forwarded-Host).
                {"name": "AUTH_TRUST_HOST", "value": "true"},
                {"name": "NEXT_PUBLIC_API_BASE_URL", "value": "${NEXT_PUBLIC_API_BASE_URL}"},
                {
                    "name": "AUTH_SECRET",
                    "valueFrom": {
                        "secretKeyRef": {"name": "${SECRET_PREFIX}-auth-secret", "key": "latest"}
                    },
                },
            ],
            cpu="1",
            memory="1Gi",
            min_scale=1,
            max_scale=10,
            concurrency=80,
            port=3000,
            sql=False,
            probe=False,
        ),
        # Ran by deploy.yml before the traffic shift (M7-03); owner role, no HTTP.
        "jobs/migrate.yaml": _job("migrate", args=["migrate"], timeout_seconds=900, memory="1Gi"),
        "jobs/smoke.yaml": _job("smoke", args=["smoke"], timeout_seconds=1800, memory="1Gi"),
    }
    for row in enabled_adapters():
        name = job_name(row["source_id"])
        manifests[f"jobs/{name}.yaml"] = _job(
            name, args=[f"job:{row['source_id']}"], timeout_seconds=3600
        )
    return manifests


def render() -> dict[str, str]:
    """Relative path -> file text, for every generated file under infra/cloudrun."""
    import yaml  # dev dependency: this module is build-time only

    header = (
        "# GENERATED by `python -m app.jobs.generate_cloudrun` from the adapter registry.\n"
        "# Do not edit by hand; edit the generator. `${...}` is substituted by render.sh\n"
        "# or by the deploy workflow (see infra/cloudrun/README.md).\n"
    )
    files = {
        path: header + yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)
        for path, doc in build_manifests().items()
    }
    files["adapters.auto.tfvars.json"] = (
        json.dumps({"adapter_jobs": enabled_adapters()}, indent=2, sort_keys=True) + "\n"
    )
    return files


def write(out_dir: Path) -> list[Path]:
    written = []
    for rel, text in render().items():
        path = out_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        written.append(path)
    return written


def check(out_dir: Path) -> list[str]:
    """Names of files that are missing, stale or no longer generated."""
    expected = render()
    problems = [
        rel
        for rel, text in expected.items()
        if not (out_dir / rel).is_file() or (out_dir / rel).read_text() != text
    ]
    for existing in sorted(out_dir.glob("jobs/*.yaml")):
        rel = f"jobs/{existing.name}"
        if rel not in expected:
            problems.append(f"{rel} (no longer generated)")
    return sorted(problems)


def default_out_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "infra" / "cloudrun"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="generate infra/cloudrun manifests")
    parser.add_argument("--out", type=Path, default=None, help="infra/cloudrun directory")
    parser.add_argument("--check", action="store_true", help="exit 1 when the tree is stale")
    args = parser.parse_args(argv)
    out_dir = args.out or default_out_dir()
    if args.check:
        problems = check(out_dir)
        if problems:
            sys.stderr.write(
                "infra/cloudrun is stale; run `python -m app.jobs.generate_cloudrun`:\n  "
                + "\n  ".join(problems)
                + "\n"
            )
            return 1
        sys.stdout.write(f"infra/cloudrun is up to date ({len(render())} files)\n")
        return 0
    written = write(out_dir)
    sys.stdout.write(f"wrote {len(written)} files under {out_dir}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
