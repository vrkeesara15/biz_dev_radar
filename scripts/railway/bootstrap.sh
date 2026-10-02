#!/usr/bin/env bash
# PRINTS the ordered Railway bring-up for BidRadar. It executes nothing: every step
# below either costs money, mutates cloud state, or needs a browser, and a script that
# did them unattended would be a worse idea than a checklist you can read.
#
#   scripts/railway/bootstrap.sh            # the whole sequence
#   scripts/railway/bootstrap.sh | less -R
#
# Prose version, with the reasoning and the failure modes: docs/runbooks/railway.md
set -euo pipefail

REPO="${BIDRADAR_REPO:-vrkeesara15/biz_dev_radar}"
PROJECT="${BIDRADAR_RAILWAY_PROJECT:-bidradar}"

cat <<EOF
================================================================================
BidRadar on Railway — ordered bring-up
================================================================================
Topology: Postgres + Redis (Railway plugins) and four services of our own —
api, worker, beat (all three from backend/, one image, mode chosen by
BIDRADAR_MODE) and frontend (from frontend/). The schema migration is api's
preDeployCommand, not a separate service.

Deploys use \`railway up ./backend --path-as-root\` and the same for ./frontend,
run from the REPO ROOT. --path-as-root is not optional: without it the
archive is prefixed with the git root, Railway looks for railway.json and
the Dockerfile at the top of the repo, and the build fails. With it, those
two directories ARE the root, which is what both Dockerfiles' COPY paths
assume. Nothing here depends on a GitHub connection.

Each step is a command to run yourself, in order. Nothing is run for you.
--------------------------------------------------------------------------------

STEP 1 — sign in and link the project
    railway login
    railway link --project ${PROJECT}
    railway status

STEP 2 — the two datastores (skip any that already exists)
    railway add --database postgres
    railway add --database redis
  Railway provisions ghcr.io/railwayapp-templates/postgres-ssl:18 and publishes
  \${{Postgres.DATABASE_URL}} (the SUPERUSER dsn) and \${{Redis.REDIS_URL}}.

STEP 3 — the four services (skip any that already exists)
    railway add --service api
    railway add --service worker
    railway add --service beat
    railway add --service frontend
  (Deploying from GitHub instead? \`railway add --service api --repo ${REPO} --branch main\`,
   and then set each service's Root Directory to /backend or /frontend in the
   dashboard — the Dockerfiles do NOT build from the repo root. The railway.json
   path does not follow Root Directory, so it would be /backend/railway.json.)

STEP 4 — one shared AUTH_SECRET for api and frontend
  Create a project Shared Variable named AUTH_SECRET so both services reference
  the same value and cannot drift:
    openssl rand -base64 32
  Project -> Settings -> Shared Variables -> AUTH_SECRET. The env templates
  already reference it as \${{shared.AUTH_SECRET}}.

STEP 5 — fill in and push the variables
    cp infra/railway/env/api.env.example      infra/railway/env/api.env
    cp infra/railway/env/worker.env.example   infra/railway/env/worker.env
    cp infra/railway/env/beat.env.example     infra/railway/env/beat.env
    cp infra/railway/env/frontend.env.example infra/railway/env/frontend.env
    \$EDITOR infra/railway/env/*.env        # every <placeholder> must go

  APP_DB_PASSWORD must be the SAME string in api, worker and beat, and the same
  string that appears inside their DATABASE_URL. Generate it once:
    openssl rand -hex 24

  Dry run first (values masked), then apply:
    scripts/railway/set_vars.sh api      infra/railway/env/api.env
    scripts/railway/set_vars.sh api      infra/railway/env/api.env --apply
    scripts/railway/set_vars.sh worker   infra/railway/env/worker.env --apply
    scripts/railway/set_vars.sh beat     infra/railway/env/beat.env --apply

  (frontend's variables are set in STEP 9 — one of them is not known yet.)

  The .env files hold real secrets. .gitignore already covers *.env; do not
  commit them.

STEP 6 — storage, if you are taking the demo shortcut
  Railway has no object store. Preferred: an S3 bucket or a Cloudflare R2
  bucket, already in api.env / worker.env as STORAGE_BACKEND=s3.
  Demo-only alternative, api single-replica, worker CANNOT read what api wrote:
    railway volume add --service api --mount-path /data
    railway variables --service api --set STORAGE_BACKEND=local --set LOCAL_STORAGE_ROOT=/data

STEP 7 — deploy api FIRST
    railway up ./backend --path-as-root --service api --detach
  api's preDeployCommand is \`/app/docker/entrypoint.sh migrate\`, which (because
  api alone sets RUN_MIGRATIONS=1):
      1. python -m app.jobs.bootstrap_db   CREATE EXTENSION vector, pg_trgm;
                                           CREATE ROLE bidradar_app + grants
      2. alembic upgrade head
      3. python -m app.seed                (SEED_ON_START=1), idempotent
  Watch it, and keep the build log — the seed line is in it:
    railway logs --service api --build
    railway logs --service api

STEP 8 — read the internal tenant id out of the log
    railway logs --service api | grep internal_tenant_id
  One JSON line:  {"admin_email": "...", "internal_tenant_id": "<uuid>", ...}
  That uuid is what the front end's membership stub needs (OQ-11).

STEP 9 — frontend: variables, then deploy, then its domain
    \$EDITOR infra/railway/env/frontend.env      # paste the uuid into BIDRADAR_DEV_TENANT_ID
    scripts/railway/set_vars.sh frontend infra/railway/env/frontend.env --apply
    railway up ./frontend --path-as-root --service frontend --detach
    railway domain --service frontend
  The generated domain is the value of AUTH_URL, and of CORS_ORIGINS on api. It
  is not known before this point, so both have to be set after it:
    railway variables --service frontend --set AUTH_URL=https://<domain>
    railway variables --service api --set CORS_ORIGINS=https://<domain> \\
                                    --set APP_BASE_URL=https://<domain>

STEP 10 — the background services
    railway up ./backend --path-as-root --service worker --detach
    railway up ./backend --path-as-root --service beat --detach
  Their preDeployCommand is the same \`migrate\`, but neither sets RUN_MIGRATIONS,
  so each prints "skipping" and exits 0. Two concurrent alembic runs would race.

STEP 10b — a way to sign in (demo only)
  A fresh project has no OAuth client and no SMTP, so every control on /signin
  is disabled. The demo provider issues a session to an allowlisted address
  with NO PASSWORD and no proof the person owns it:
    railway variables --service frontend \\
      --set AUTH_DEV_LOGIN=1 --set AUTH_DEV_LOGIN_EMAILS=you@example.com
  The page then shows a "demo mode" badge. Because resolveMembership() gives
  every signed-in user the same tenant and role (OQ-11), an address on that
  list is tenant_owner on the internal tenant. DELETE BOTH VARIABLES and
  configure Google or Microsoft before anything real signs in.

STEP 11 — smoke
    railway domain --service api       # generate one only if you want the API public
    curl -fsS https://<api-domain>/healthz           # {"status":"ok",...}
    curl -fsI https://<frontend-domain>/signin       # 200
  Then sign in in a browser and confirm the top bar shows a tenant. If it does
  not, BIDRADAR_DEV_TENANT_ID is wrong or unset (STEP 8).

ROLLBACK / TEARDOWN
    railway deployment list --service api
    railway down --service api            # removes the most recent deployment
    railway volume delete --service api   # the demo volume, if you made one
================================================================================
EOF
