# Runbook — backup restore drill

SPEC 11: *"Backups: daily Cloud SQL backups + PITR 7 days; restore drill before launch."*

A backup you have never restored is a hope, not a backup. This page is how we turn it
into a fact, and what to do when the day comes that we need it for real.

## What is configured

Terraform (`infra/terraform/modules/cloud_sql`) sets, for every environment:

| setting | value | why |
| --- | --- | --- |
| `backup_configuration.enabled` | true | the daily automated backup |
| `start_time` | 03:00 UTC | quietest hour in both regions |
| `point_in_time_recovery_enabled` | true | restore to any second, not just to 03:00 |
| `transaction_log_retention_days` | 7 | SPEC 11; a variable `validation` refuses less |
| `retained_backups` | 30 (7 in dev) | a month of daily restore points |
| `location` | the instance's own region | Indian backups stay in asia-south1 |
| `deletion_protection` | true outside dev | an `apply` cannot drop the instance |

Check any environment without leaving your terminal:

```bash
cd infra/terraform/envs/prod-in && terraform output backups
```

## The drill

Run it **before launch**, then quarterly, and after any change to the Cloud SQL module.

```bash
scripts/restore_drill.sh --env prod-in --dry-run   # read the plan first
scripts/restore_drill.sh --env prod-in             # ~20-40 minutes, mostly waiting
```

What it does, and what each step is actually checking:

1. **List backups.** Takes the newest `SUCCESSFUL` `AUTOMATED` backup and fails if it is
   more than 36 hours old — a stale newest backup means the schedule stopped, which is
   the failure most likely to go unnoticed.
2. **Confirm the configuration.** Backups on, PITR on, at least 7 days of logs, and the
   backup location inside the instance's region. A backup of Indian data sitting in a US
   multi-region would be a DPDP problem, not just an ops one.
3. **Create a scratch instance.** A new instance every time, named
   `<prefix>-drill-<timestamp>`. The script refuses to proceed if that name already
   exists, because `gcloud sql backups restore` into an existing instance **overwrites
   it** — that is the one command in this runbook that can destroy production.
4. **Restore** the backup into the scratch instance.
5. **Verify.** Through the Cloud SQL Auth Proxy: `alembic_version` must hold a revision,
   and `tenants`, `users`, `opportunities` and `audit_log` must not all be empty. A
   restore that produces an empty schema is a failure that a "restore completed" message
   will happily hide.
6. **Delete the scratch instance** (unless `--keep`), including on failure.

Then **write the result down** in the log at the bottom of this page: date, environment,
backup id, wall-clock time. The elapsed time is the number that matters during an
incident — it is our real RTO, and it is longer than people guess.

### Flags

| flag | effect |
| --- | --- |
| `--dry-run` | prints every command, creates nothing |
| `--keep` | leaves the scratch instance up for poking at (delete it yourself) |
| `--skip-verify` | skips the content checks (use when `cloud-sql-proxy` is unavailable) |
| `--instance` / `--project` / `--region` | override the per-environment defaults |

## Doing it for real

### A bad migration or a bad deploy (the common case)

Code rolls back with a traffic shift; the database does not (see
`docs/runbooks/deploy.md`). If the schema is wrong, restore to the second before the
migration ran:

```bash
gcloud sql instances clone bidradar-prod-in-pg bidradar-prod-in-recovered \
  --project bidradar-prod-in \
  --point-in-time '2026-09-27T09:14:00.000Z'
```

A clone at a point in time gives you a second instance to inspect *before* you commit to
it. Only once you are satisfied do you move the application onto it:

1. Scale the api, worker and beat services to zero (`--min-instances 0 --max-instances 0`)
   so nothing writes while you switch.
2. Update the `DATABASE_URL` / `DATABASE_URL_OWNER` secrets to the recovered instance.
3. Deploy a new revision so the secrets are re-read (Cloud Run does not hot-reload them).
4. Scale back up and watch `/healthz` and the error rate.

Write off everything between the recovery point and now, and say so in the incident
notes: for BidRadar that is ingested opportunities (re-crawled within a cycle) and, more
painfully, tenant drafts and uploads (not recoverable from the source).

### The instance is gone

`gcloud sql backups restore` needs an instance to restore *into*. Create one with the
same shape first — the quickest correct way is Terraform, with the instance name changed:

```bash
cd infra/terraform/envs/prod-in
terraform apply -var-file=terraform.tfvars   # recreates the instance from the module
gcloud sql backups list --instance bidradar-prod-in-pg --project bidradar-prod-in
gcloud sql backups restore <BACKUP_ID> \
  --restore-instance <new-instance> --backup-instance bidradar-prod-in-pg \
  --project bidradar-prod-in
```

Note the ordering trap: Terraform will want to create the users too, and a restore
overwrites them. Restore first, then rotate the passwords and update the secrets.

### After any restore

- `cd backend && alembic upgrade head` against the restored database if the backup
  predates the current release.
- Re-run the tenant isolation suite against it if the restore was into a shared project:
  RLS policies travel with the schema, but a hand-made instance may not have the roles.
- Confirm residency again: `terraform output residency_ok`.

## What this does not cover

- **Object storage.** GCS buckets are versioned with a CMEK key that has
  `prevent_destroy`, but there is no bucket restore drill yet. A tenant's uploaded
  documents are recoverable from object versioning; their *rows* come from Cloud SQL, and
  the two can end up at different points in time after a restore. Worth a drill of its
  own before launch.
- **Redis.** Deliberately not backed up: it holds the Celery queue and the rate-limit
  buckets, both of which are reconstructed.
- **Secret Manager.** Versions are not deleted by anything here, but a lost project is a
  lost secret store; the values also live in the team password manager.

## Drill log

Append a row every time. An empty table means the drill has never been run.

| date | environment | backup id | elapsed | verified by | notes |
| --- | --- | --- | --- | --- | --- |
| _(not yet run — no GCP project exists; the script has only been dry-run)_ | | | | | |
