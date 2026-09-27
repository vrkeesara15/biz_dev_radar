# infra/terraform — the four BidRadar environments

SPEC 12 asks for four environments and one pipeline. Here they are four directories that
differ only in a `terraform.tfvars` file: everything else is the same composite module,
so "prod-us and prod-in run the same image" is enforced by the shape of the code rather
than by discipline.

| env | region | residency | database | scheduled adapters |
| --- | --- | --- | --- | --- |
| `dev` | us-east1 | us | ZONAL, disposable, 7 backups | us + in |
| `staging-in` | asia-south1 | in | ZONAL, protected, 30 backups | in |
| `prod-us` | us-east1 | us | REGIONAL HA, protected | us |
| `prod-in` | asia-south1 | in | REGIONAL HA, protected | in |

## Layout

```
modules/
  network             VPC, private service access, Serverless VPC connector, Cloud NAT
  cloud_sql           Postgres 16, private IP, daily backups, PITR 7 days, two roles
  memorystore         Redis 7 (Celery broker + the M7-06 rate-limit buckets)
  gcs                 one bucket per region with its OWN KMS keyring (CMEK) + lifecycle
  secrets             one Secret Manager entry per SECRET_SETTINGS name, no values
  iam                 five service accounts, least privilege, GitHub OIDC (no JSON keys)
  artifact_registry   Docker repo with immutable tags and a cleanup policy
  cloud_run_service   api / worker / beat / frontend
  cloud_run_job       one job per adapter, plus migrate and smoke
  scheduler           one Cloud Scheduler trigger per adapter cron
  monitoring          5xx rate, failed job executions, disk, failed backups
  environment         the composite an env directory instantiates
envs/{dev,staging-in,prod-us,prod-in}/
  main.tf             module "bidradar" with this environment's numbers
  terraform.tfvars    project, region, state bucket, image references
  backend.hcl         partial backend config (a backend block cannot read variables)
```

## Running it

```bash
cd infra/terraform/envs/dev
terraform init -backend-config=backend.hcl
terraform plan  -var-file=terraform.tfvars
terraform apply -var-file=terraform.tfvars
```

CI checks the configuration without any credentials:

```bash
terraform fmt -check -recursive
terraform -chdir=envs/dev init -backend=false && terraform -chdir=envs/dev validate
```

`backend/tests/unit/test_terraform_config.py` additionally parses the HCL in `make test`
and fails when the config drifts from the Python code or from the spec.

## Two lists are shared with the backend, and neither is copied by hand

1. **Secrets.** `modules/environment` has a `secret_settings` default that must equal
   `SECRET_SETTINGS` in `backend/app/core/config.py`. Terraform creates one **empty**
   Secret Manager secret per name; values are added out of band
   (`gcloud secrets versions add bidradar-dev-sam-api-key --data-file=-`). The only
   versions Terraform writes are the Cloud SQL DSNs and the Redis URL, because it
   generated those passwords itself.
2. **Adapter crons.** `modules/environment` reads
   `infra/cloudrun/adapters.auto.tfvars.json`, which
   `python -m app.jobs.generate_cloudrun` writes from the adapter registry. Adding an
   adapter therefore adds its Cloud Run job and its Cloud Scheduler trigger with no HCL
   change — and the registry's own `schedule` string is the cron that runs.

## Residency (SPEC 11 and the SPEC 12 India checklist)

`terraform output residency` prints where the data physically is — bucket location, CMEK
key location, database region, backup location, Redis region and the Secret Manager
replica location — and `terraform output residency_ok` is a single boolean over all six.
A `check` block refuses to plan when `residency` and `region` disagree.

A deployment serves **one** residency. The other region's bucket name is deliberately set
to a bucket that does not exist, so a tenant with the wrong `data_residency` fails loudly
instead of quietly writing Indian documents into a US bucket.

## Backups (SPEC 11)

Daily automated backups, `point_in_time_recovery_enabled = true` and 7 days of
transaction logs, with the backup location pinned to the instance's own region. The
7-day floor is a variable `validation`, so no tfvars file can lower it. Drill it with
`scripts/restore_drill.sh` (see `docs/runbooks/restore-drill.md`).

## What Terraform does *not* do

- It never holds a secret value (except the passwords it generated).
- It does not build or push images; `.github/workflows/deploy.yml` does, and passes the
  digest in as `backend_image` / `frontend_image`.
- It does not create per-PR preview revisions; those are tagged, zero-traffic revisions
  added out of band, which is why the service resource ignores revision changes.
