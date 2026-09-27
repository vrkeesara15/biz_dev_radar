# infra/cloudrun — Cloud Run service and job manifests

**Generated.** Everything here except this file and `render.sh` comes out of
`python -m app.jobs.generate_cloudrun` (run it from `backend/`). Edit the generator,
not the YAML. `backend/tests/unit/test_cloudrun_manifests.py` regenerates the tree and
fails the build when it drifts, so an adapter added to the registry *must* come with its
Cloud Run job in the same commit.

```
services/api.yaml        Knative Service, public ingress, minScale 1, /healthz probes
services/worker.yaml     Celery worker; cpu-throttling=false, minScale 1, concurrency 1
services/beat.yaml       Celery beat; min=max=1 (two beats would double every schedule)
services/frontend.yaml   Next.js standalone server on port 3000
jobs/<source_id>.yaml    one Cloud Run job per ENABLED adapter, args ["job:<source_id>"]
jobs/migrate.yaml        `alembic upgrade head`, executed before every traffic shift
jobs/smoke.yaml          the nightly live smoke, runnable from inside the region
adapters.auto.tfvars.json  {source_id, schedule, region} consumed by infra/terraform
```

## One image, two regions

The api, worker, beat and every job run **the same backend image**
(`backend/Dockerfile`); `docker/entrypoint.sh` takes the mode as its first argument
(`api`, `worker`, `beat`, `job:<source_id>`, `migrate`, `smoke`). Residency is a single
environment variable: `REGION=us` or `REGION=in` makes
`backend/app/core/config.py` pick `GCS_BUCKET_US`/`GCS_BUCKET_IN` and the regional
`DATABASE_URL` secret, so `prod-us` (us-east1) and `prod-in` (asia-south1) are the same
image digest with different env (SPEC 12).

## Placeholders

| Placeholder | Meaning |
| --- | --- |
| `APP_ENV` | `dev`, `staging`, `production` — drives JSON logging and the prod checks |
| `BACKEND_IMAGE` | fully-qualified Artifact Registry digest of the backend image |
| `FRONTEND_IMAGE` | fully-qualified Artifact Registry digest of the frontend image |
| `CLOUD_SQL_INSTANCE` | `project:region:instance` for the Cloud SQL connector |
| `CORS_ORIGINS` | comma-separated origins the API accepts |
| `GCP_REGION` | `us-east1` or `asia-south1` |
| `GCS_BUCKET_US` / `GCS_BUCKET_IN` | per-region buckets (CMEK, created by Terraform) |
| `NEXT_PUBLIC_API_BASE_URL` | API origin inlined into the front-end bundle at build time |
| `REGION` | residency switch, `us` or `in` |
| `SECRET_PREFIX` | Secret Manager name prefix, e.g. `bidradar-dev` |
| `SERVICE_ACCOUNT` | runtime service account email |
| `VPC_CONNECTOR` | Serverless VPC Access connector for private Cloud SQL / Memorystore |

Secrets are never values here: each one is a `secretKeyRef` to
`${SECRET_PREFIX}-<setting-name-with-hyphens>`, one per entry in
`SECRET_SETTINGS` in `backend/app/core/config.py`.

## Rendering

```bash
export APP_ENV=dev REGION=us GCP_REGION=us-east1 ...   # every row of the table
./infra/cloudrun/render.sh --all --out /tmp/rendered
gcloud run services replace /tmp/rendered/services/api.yaml --region us-east1
```

In practice Terraform owns the deploy (`infra/terraform/envs/<env>`), and
`.github/workflows/deploy.yml` executes `jobs/migrate.yaml` before shifting traffic.
