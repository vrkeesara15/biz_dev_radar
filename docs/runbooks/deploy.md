# Runbook — deploying BidRadar

One pipeline, four environments (SPEC 12). Everything below is what
`.github/workflows/deploy.yml` already does; this page is for the times it does not
finish, or when you need to do it by hand.

## The shape of it

| trigger | what happens |
| --- | --- |
| merge to `main` | build both images once, push them to the dev Artifact Registry, deploy **dev** |
| tag `v*` | resolve the digest `main` built for that commit, deploy **staging-in** (approval), then **prod-us** and **prod-in** (approval each) |
| open / update a PR | a tagged, zero-traffic revision of the dev services (`preview.yml`) |
| close a PR | the tag is removed and the revision becomes unreachable |

Two rules the pipeline enforces rather than trusts:

1. **A promotion never rebuilds.** The `resolve` job looks up the digest of
   `…/backend:sha-<commit>` and every downstream deploy uses `…/backend@sha256:…`. The
   composite action refuses an image reference that is not pinned by digest, so a
   copy-pasted tag fails the job instead of quietly shipping different bits to India than
   to the US.
2. **Schema first, traffic second.** Every deploy updates the `…-migrate` Cloud Run job to
   the new image, runs it with `--wait`, and only then moves the services. A failed
   migration fails the deploy with production still on the old revision.

## Before the first deploy of a new environment

Terraform creates the secrets **empty** (OQ-75), and a Cloud Run revision will not start
without them. Fill them once:

```bash
ENV=dev                       # or staging-in / prod-us / prod-in
cd infra/terraform/envs/$ENV
terraform init -backend-config=backend.hcl
terraform apply -var-file=terraform.tfvars

terraform output -json secret_ids     # setting name -> secret id
printf '%s' "$ANTHROPIC_API_KEY" | gcloud secrets versions add bidradar-dev-anthropic-api-key --data-file=-
# ... and the rest: auth-secret, field-encryption-key, sam-api-key, voyage-api-key,
#     stripe-*, razorpay-*, sentry-dsn, langfuse-*
```

`DATABASE_URL`, `DATABASE_URL_OWNER` and `REDIS_URL` already have values — Terraform
generated those passwords.

Then confirm residency before any tenant data exists:

```bash
terraform output residency        # bucket, CMEK, DB, backups, Redis, secrets
terraform output residency_ok     # must be true
```

## Deploying by hand

Only when Actions is down. You need `roles/run.admin` and `actAs` on the runtime service
accounts.

```bash
PROJECT=bidradar-prod-in REGION=asia-south1 PREFIX=bidradar-prod-in
IMAGE=us-east1-docker.pkg.dev/bidradar-dev/bidradar/backend@sha256:<digest>

gcloud run jobs update  "$PREFIX-migrate" --region "$REGION" --image "$IMAGE"
gcloud run jobs execute "$PREFIX-migrate" --region "$REGION" --wait

for svc in api worker beat; do
  gcloud run services update "$PREFIX-$svc" --region "$REGION" --image "$IMAGE"
done
gcloud run services update "$PREFIX-web" --region "$REGION" --image "$FRONTEND_IMAGE"
curl -fsS "$(gcloud run services describe "$PREFIX-api" --region "$REGION" --format 'value(status.url)')/healthz"
```

## Rollback

A Cloud Run rollback is a traffic shift, so it takes seconds:

```bash
gcloud run revisions list --service "$PREFIX-api" --region "$REGION"
gcloud run services update-traffic "$PREFIX-api" --region "$REGION" \
  --to-revisions "$PREFIX-api-00042-abc=100"
```

Do the same for `worker`, `beat` and `web`. **The database does not roll back.** Migrations
are therefore written to be backward compatible with the previous revision — add a column,
deploy, backfill, and only remove the old column a release later. If a migration is not
backward compatible, say so in the PR and deploy it in its own release with a maintenance
window; rolling the code back under it will not work.

For a bad migration the recovery is PITR, not a code rollback: see
`docs/runbooks/restore-drill.md`.

## Preview environments

A preview is a tagged revision of the **dev** services, reachable at
`https://pr-<n>---bidradar-dev-api-<hash>.a.run.app`, with no traffic and sharing the dev
database. Consequences worth remembering:

- A PR that changes the schema will run its migration only when it merges to `main`. The
  preview runs new code against the **old** dev schema, so a preview that 500s on a new
  column is telling you the migration is not backward compatible.
- Previews do not exist for PRs from forks: a fork cannot mint an OIDC token for our
  project, and giving it one would hand a stranger the deploy identity.
- Teardown is `gcloud run services update-traffic --remove-tags pr-<n>`, which the
  `closed` trigger does. If a revision is left behind, that command is safe to re-run.

## When a deploy fails

| symptom | likely cause | what to do |
| --- | --- | --- |
| `resolve` cannot find a digest | the tag points at a commit `main` never built | tag a commit that has a green `deploy` run on main |
| the migrate job fails | a migration error, or the owner DSN secret is missing | read the execution log; traffic never moved, so nothing is broken yet |
| a revision never becomes ready | a missing secret, or clamd is still fetching definitions | check the revision logs; the container fails closed on uploads until clamd is up (OQ-68) |
| `/healthz` never answers | the VPC connector or Cloud SQL connection is wrong | `gcloud run services describe` and compare against `terraform output` |
| an approval is stuck | the environment's reviewers are unavailable | approvals are per environment in repository settings; prod-us and prod-in are separate |

## What the pipeline deliberately does not do

- It does not run Terraform. Infrastructure changes are applied by a human from
  `infra/terraform/envs/<env>` after review, because a plan that destroys a database
  should never be applied by a robot.
- It does not deploy on a failing CI run: `ci.yml` (lint, tests, isolation, docker build,
  terraform validate, security scan) must be green on the merge commit.
