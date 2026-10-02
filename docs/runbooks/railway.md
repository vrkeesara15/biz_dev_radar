# Runbook — BidRadar on Railway

The production target is Cloud Run ([deploy.md](deploy.md)). This page is the other
one: a single-region demo on [Railway](https://railway.app) that costs tens of dollars a
month instead of hundreds, brings the whole stack up in about twenty minutes, and is
honest about the four things it cannot do.

Everything here is driven by `railway up` from `backend/` and `frontend/` — no GitHub
connection, no dashboard clicking. The ordered command list is
[`scripts/railway/bootstrap.sh`](../../scripts/railway/bootstrap.sh), which prints and
executes nothing; this page is the same sequence with the reasoning attached.

## What you need first

| | |
| --- | --- |
| Railway account | A Hobby plan is enough. Six services at the sizes below land around $25–40/month. |
| Railway CLI | `brew install railway` (or `npm i -g @railway/cli`), then `railway login`. Version 5 or newer: the commands here use `railway volume` and `railway variables --set`. |
| An S3-compatible bucket | Cloudflare R2 or AWS S3. **Railway has no object store.** The alternative is a volume, and it is worse — see [Storage](#storage-the-one-real-gap). |
| An Anthropic key | `ANTHROPIC_API_KEY`. Without it every agent is disabled and the demo is a search UI. |
| Optional | `VOYAGE_API_KEY` (or `EMBEDDING_PROVIDER=fake`), `SENDGRID_API_KEY` (or `EMAIL_PROVIDER=memory`), `SAM_API_KEY`. |

### pgvector

Railway provisions `ghcr.io/railwayapp-templates/postgres-ssl:18` — **Postgres 18**, not
the 16 the compose stack runs. The schema needs the `vector` extension, and
`app/jobs/bootstrap_db.py` is where it is created, inside api's pre-deploy hook. So the
first `railway up --service api` is also the pgvector test: if the extension is not
available the deploy fails there, before any traffic, with
`ERROR: extension "vector" is not available`.

Check it ahead of time if you would rather not find out that way:

```bash
railway connect postgres          # opens psql on the provisioned database
CREATE EXTENSION IF NOT EXISTS vector;
SELECT extversion FROM pg_extension WHERE extname = 'vector';
```

**If it fails**, replace the plugin with a pgvector image of your own and point the two
DSNs at it instead:

```bash
railway add --service pgvector --image pgvector/pgvector:pg16
railway volume add --service pgvector --mount-path /var/lib/postgresql/data
railway variables --service pgvector \
  --set POSTGRES_USER=bidradar --set POSTGRES_PASSWORD="$(openssl rand -hex 24)" \
  --set POSTGRES_DB=bidradar --set PGDATA=/var/lib/postgresql/data/pgdata
```

Then `DATABASE_URL_OWNER` becomes
`postgresql://bidradar:<pw>@${{pgvector.RAILWAY_PRIVATE_DOMAIN}}:5432/bidradar`. The
cost of that path is that it is a database **you** now operate: no managed backups, no
point-in-time recovery, and the volume is the only copy. Fine for a demo, not for
tenant data.

## The topology

```
                    ┌──────────────┐
  browser ────────► │  frontend    │  Next.js standalone, port 3000, public domain
                    │  (frontend/) │  every /api/v1/* call is proxied same-origin
                    └──────┬───────┘
                           │  API_URL = http://api.railway.internal:8080  (private)
                    ┌──────▼───────┐
                    │     api      │  BIDRADAR_MODE=api   · /healthz · RUN_MIGRATIONS=1
                    │  (backend/)  │  pre-deploy: bootstrap_db + alembic + seed
                    └──┬────────┬──┘
   ┌───────────────────┘        └───────────────────┐
┌──▼─────────┐   ┌────────────┐   ┌──────────────┐  │
│  Postgres  │   │   worker   │   │     beat     │  │   worker/beat: same image,
│   pg18     │◄──┤ MODE=worker│◄──┤  MODE=beat   │◄─┘   same backend/railway.json,
│  +pgvector │   │ celery     │   │ scheduler,   │      no RUN_MIGRATIONS
└────────────┘   └─────┬──────┘   │ 1 replica    │
                       │          └──────┬───────┘
                 ┌─────▼─────────────────▼──┐
                 │          Redis            │  broker + rate-limit buckets
                 └───────────────────────────┘
```

Six services: two Railway plugins (Postgres, Redis) and four of ours. There is no
seventh "migrate" service — the schema migration is api's `preDeployCommand`, which
Railway runs to completion before the new api container takes traffic, and fails the
deploy if it exits non-zero. That is the same ordering rule as Cloud Run's migrate job
(deploy.md: *schema first, traffic second*), with one fewer moving part.

### One image, one config file, three services

`api`, `worker` and `beat` are the same backend image and the same
[`backend/railway.json`](../../backend/railway.json). They differ only by environment
variable:

| | api | worker | beat |
| --- | --- | --- | --- |
| `BIDRADAR_MODE` | `api` | `worker` | `beat` |
| `RUN_MIGRATIONS` | `1` | *unset* | *unset* |
| `SEED_ON_START` | `1` | *unset* | *unset* |
| `BOOTSTRAP_DB` | `1` | `0` | `0` |
| replicas | 1 | 1 (scale up freely) | **1, never more** |

`docker/entrypoint.sh` reads `MODE="${1:-${BIDRADAR_MODE:-api}}"`, and the backend
Dockerfile's `CMD` is empty so nothing pins `$1`. Cloud Run keeps passing the mode as an
argument and is unaffected.

The schema work lives in one shell function, `run_migrations()` in
`docker/entrypoint.sh`, and is reached two ways:

- **`preDeployCommand`** — `/app/docker/entrypoint.sh migrate`, declared once in
  `backend/railway.json` and therefore inherited by all three backend services.
- **the `api` branch on start** — because *Railway did not actually run the
  pre-deploy command for a CLI-uploaded service*. The deploy reported success, the log
  had no bootstrap, alembic or seed lines, and the first request failed with
  `password authentication failed for user "bidradar_app"` — the role had never been
  created. So `api)` calls `run_migrations` itself before starting uvicorn. It is
  idempotent and api is single-replica, so running it twice costs a few hundred
  milliseconds and nothing else; the `preDeployCommand` stays in `railway.json` as
  belt-and-braces (and because it is the correct mechanism on a GitHub-connected
  service) until the IaC migration in OQ-158.

Both paths are gated on `RUN_MIGRATIONS=1`, which is the first thing `run_migrations()`
checks; without it the function prints `skipping` and returns 0, which is what makes the
shared `preDeployCommand` safe on worker and beat. **Do not set `RUN_MIGRATIONS=1` on
more than one service** — two concurrent `alembic upgrade head` runs race for the same
advisory lock and one of them fails its deploy.

Beat at two replicas would fire every schedule twice: every adapter polled twice, every
digest sent twice. `numReplicas: 1` in `backend/railway.json` is the guard, and it is
not a performance setting.

### Why the build roots are `backend/` and `frontend/`

Both Dockerfiles were written for a context of their own directory, and they do not work
from the repo root — verified, not assumed:

| command | result |
| --- | --- |
| `docker build -f backend/Dockerfile .` | **fails**: `COPY docker ./docker` → `"/docker": not found` |
| `docker build backend/` | **succeeds** |
| `docker build -f frontend/Dockerfile .` | **fails**: `COPY package.json pnpm-lock.yaml ./` → `"/pnpm-lock.yaml": not found` |
| `docker build frontend/` | **succeeds** (this is also what `ci.yml`'s `docker-build` matrix does) |

So each service uploads only its own directory, and that is what `--path-as-root`
means — run from the repo root:

```bash
railway up ./backend  --path-as-root --service api      --detach
railway up ./frontend --path-as-root --service frontend --detach
```

**`--path-as-root` is not optional.** Without it `railway up ./backend` still prefixes
the archive with the git root, so Railway looks for `railway.json` and the Dockerfile at
the top of the repo and the build fails. With it, `backend/` *is* the root: Railway
finds `backend/railway.json` automatically, `"dockerfilePath": "Dockerfile"` resolves
beside it, and the COPY paths line up. `backend/.railwayignore` and
`frontend/.railwayignore` keep `.venv` (~390 MB), `node_modules` (~990 MB) and `.next`
(~685 MB) out of the upload.

**There are no BuildKit cache mounts in either Dockerfile, and that is deliberate.**
Railway's builder first rejects an anonymous mount (*"flag
'--mount=type=cache,target=...' is missing an id argument"*) and then, once you add an
id, rejects that too unless it carries the builder's own per-service prefix
(*"missing the cacheKey prefix from its id"*, i.e. `id=s/<service-id>-<path>`). Naming
a mount that way would bind one shared Dockerfile to one Railway service id — and
`backend/Dockerfile` is shared by three. So the three `--mount=type=cache` lines in
`backend/Dockerfile` (pip, and uv twice) and the one in `frontend/Dockerfile` (the pnpm
store) were removed; each is now a plain `RUN`.

What that costs: a local `docker build` no longer reuses the pip / uv / pnpm download
caches between builds, so a cold build re-downloads its dependency set. Layer caching
itself is unaffected — the lockfile-only `COPY` before each install still means
unchanged dependencies do not reinstall — and correctness is identical everywhere. If
you add a cache mount back for local speed, Railway's builder will fail on it.

> **If you switch to GitHub-connected services** (`railway add --service api --repo
> vrkeesara15/biz_dev_radar --branch main`), set each service's **Root Directory** to
> `/backend` or `/frontend` for the same reason. Railway's config file does *not* follow
> the root directory — the docs are explicit: *"You have to specify the absolute path for
> the `railway.json` or `railway.toml` file, for example `/backend/railway.toml`"* — so
> the config path is `/backend/railway.json` and `/frontend/railway.json`, repo-absolute.
> Add `watchPatterns` of `backend/**` and `frontend/**` so a frontend commit does not
> rebuild three backend services.

### Per-service settings, in full

| | api | worker | beat | frontend |
| --- | --- | --- | --- | --- |
| upload root (`railway up <path> --path-as-root`) | `./backend` | `./backend` | `./backend` | `./frontend` |
| config file | `backend/railway.json` | same | same | `frontend/railway.json` |
| builder | `DOCKERFILE` | `DOCKERFILE` | `DOCKERFILE` | `DOCKERFILE` |
| `dockerfilePath` | `Dockerfile` | `Dockerfile` | `Dockerfile` | `Dockerfile` |
| start command | *none* — image `ENTRYPOINT` + `BIDRADAR_MODE` | *none* | *none* | *none* — `CMD ["node","server.js"]` |
| `preDeployCommand` | `/app/docker/entrypoint.sh migrate` | same (no-ops) | same (no-ops) | *none* |
| `healthcheckPath` | `/healthz` | `/healthz` (sidecar, `app/jobs/health_server.py`) | `/healthz` (sidecar) | `/signin` |
| `healthcheckTimeout` | 300 | 300 | 300 | 300 |
| restart policy | `ON_FAILURE`, 10 retries | same | same | same |
| `numReplicas` | 1 | 1 | **1** | 1 |
| port | 8080 (`PORT`) | 8080 (health only) | 8080 (health only) | 3000 (`PORT`) |
| build arg | none needed | none | none | **none** — `railway up` cannot pass one; the API URL is runtime (`API_URL`) |

`/signin` rather than `/` for the frontend health check because `/` redirects (307) to
it, and a health check that follows a redirect is testing the redirect.

> **`railway.json` is deprecated (OQ-158).** As of the CLI version used here,
> `railway up` prints: *"Config as Code (railway.json / railway.toml) is deprecated.
> Prefer Infrastructure as Code (`.railway/railway.ts`). Run `railway config migrate`…
> Existing files keep working until **2026-12-01**."* Both files here are the JSON form
> and work today; the migration to `.railway/railway.ts` is deliberately **not** done in
> this change, because it would alter the deploy semantics of a stack that is mid
> bring-up. Do it before 2026-12-01, in its own commit, with a redeploy of all four
> services behind it.

## Environment variables

Templates with a comment per line are in
[`infra/railway/env/`](../../infra/railway/env/): `api.env.example`,
`worker.env.example`, `beat.env.example`, `frontend.env.example`. Copy each to
`*.env`, fill in every `<placeholder>`, and push it with
[`scripts/railway/set_vars.sh`](../../scripts/railway/set_vars.sh), which dry-runs with
values masked unless you pass `--apply`. `.gitignore` already excludes
`infra/railway/env/*.env`.

`${{Service.VAR}}` is Railway's reference syntax, resolved by the platform at deploy
time. Leave those strings verbatim — copying the resolved value instead is how a DSN
goes stale the first time the database is re-provisioned.

### Shared across api, worker and beat

| Variable | Value | Why |
| --- | --- | --- |
| `DATABASE_URL_OWNER` | `${{Postgres.DATABASE_URL}}` | The plugin publishes one DSN and it is the superuser's. That is our **owner** role: migrations and the audited admin bypass. |
| `APP_DB_PASSWORD` | `openssl rand -hex 24`, **identical on all three** | The password `bootstrap_db` creates `bidradar_app` with. |
| `DATABASE_URL` | `postgresql+asyncpg://bidradar_app:<APP_DB_PASSWORD>@${{Postgres.RAILWAY_PRIVATE_DOMAIN}}:5432/railway` | The app role: not the owner, `NOBYPASSRLS`, so RLS can never be skipped. |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` | Celery broker/backend and the rate-limit buckets. |
| `AUTH_SECRET` | `${{shared.AUTH_SECRET}}` | Same value as the frontend; the API verifies the frontend's HS256 bearer tokens with it. |
| `FIELD_ENCRYPTION_KEY` | `openssl rand -base64 32` | AES-256-GCM for the SPEC 11 encrypted columns. |
| `REGION` | `us` | Residency switch; picks the bucket and the regional behaviour. |
| `APP_ENV` | `production` | JSON logging and the production config checks. |
| `CELERY_TASK_ALWAYS_EAGER` | `false` | Real queueing; the worker is what executes. |
| `SCANNER_BACKEND` | `noop` | See [Cost](#cost). |
| `STORAGE_BACKEND` + `S3_*` | see below | |

`DATABASE_URL` does not need `?sslmode=`: `postgres.railway.internal` is on the
project's private IPv6 network and never leaves it. The public
`*.proxy.rlwy.net` host does need TLS and its DSN already says
`?sslmode=require`; `app/core/config.py` rewrites that to `?ssl=require`, which is what
asyncpg understands (`sslmode` is a libpq keyword that asyncpg rejects outright). It
also rewrites `postgres://` and `postgresql://` to `postgresql+asyncpg://`, so pasting
Railway's DSN verbatim into either variable works.

### api only

| Variable | Value | Why |
| --- | --- | --- |
| `BIDRADAR_MODE` | `api` | |
| `RUN_MIGRATIONS` | `1` | The one service that owns the schema. |
| `SEED_ON_START` | `1` | Runs `python -m app.seed` after the migration, idempotently. |
| `BOOTSTRAP_DB` | `1` | Extensions + role + grants before the migration. |
| `SEED_ADMIN_EMAIL` | your address | The platform-admin account the seed creates. |
| `CORS_ORIGINS` | `https://<frontend-domain>` | Only matters for direct API clients; the browser goes through the frontend proxy. |
| `APP_BASE_URL` | `https://<frontend-domain>` | Deep links in notification emails. |
| `ANTHROPIC_API_KEY` | `sk-ant-…` | |
| `EMBEDDING_PROVIDER` / `VOYAGE_API_KEY` | `voyage` + key, or `fake` | `fake` is deterministic, offline and free; its vectors are meaningless, so semantic search returns nonsense. Acceptable for a UI demo, not for an evaluation. |
| `EMAIL_PROVIDER` | `sendgrid` + `SENDGRID_API_KEY`, or `memory` | |
| `CONTACT_EMAIL` | your address | Published in the privacy and legal pages. |
| `SAM_API_KEY` | optional | Without it the SAM.gov adapter is off. |
| `SENTRY_DSN`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `LANGFUSE_*` | optional | Each is off when empty. |

### frontend

| Variable | Value | Why |
| --- | --- | --- |
| `AUTH_SECRET` | `${{shared.AUTH_SECRET}}` | Must equal api's. |
| `AUTH_URL` | `https://<frontend-domain>` | Magic-link callbacks. Not known until `railway domain` has run. |
| `AUTH_TRUST_HOST` | `true` | Not Vercel; Auth.js must be told to trust the proxy's Host header. |
| `API_URL` | `http://${{api.RAILWAY_PRIVATE_DOMAIN}}:8080` | **The one that matters.** Read at runtime by `src/lib/api/client.ts`. Private network, no egress charge, API never exposed publicly. |
| `NEXT_PUBLIC_API_URL` | same | Build-time fallback only; `API_URL` wins. |
| `NEXT_PUBLIC_REGION` | `US` | The region badge in the top bar. |
| `BIDRADAR_DEV_TENANT_ID` | the uuid from the seed log | OQ-11. Unset means every signed-in user has `tenant_id: null` and sees nothing. |
| `BIDRADAR_DEV_ROLE` | `tenant_owner` | |
| `EMAIL_SERVER` / `EMAIL_FROM` | optional | Leave unset to hide the magic-link form. |
| `AUTH_GOOGLE_*`, `AUTH_MICROSOFT_ENTRA_ID_*` | optional | Empty hides the button. |
| `AUTH_DEV_LOGIN` / `AUTH_DEV_LOGIN_EMAILS` | **demo only**, see below | Without one of these three, `/signin` has no working control at all. |

#### Signing in when there is no OAuth client and no SMTP

A fresh Railway project has neither, so every control on `/signin` renders disabled and
the demo is unreachable. The way out is the demo credentials provider:

```bash
railway variables --service frontend \
  --set AUTH_DEV_LOGIN=1 \
  --set AUTH_DEV_LOGIN_EMAILS=you@example.com
```

It issues a session to any address on that comma-separated list, **with no password and
no proof that the person owns the address**. It is not authentication; it is a door with
a guest list. Three things keep it honest, and all three are tested
(`src/lib/auth-dev-login.test.ts`):

- `AUTH_DEV_LOGIN` must be exactly `1`. Unset, the provider is not registered at all —
  it is absent from `/api/auth/providers`, the form is not rendered, and the callback
  404s.
- `AUTH_DEV_LOGIN_EMAILS` must be non-empty. An empty list admits **nobody**, not
  everybody, and the provider is not registered either.
- The sign-in page shows a **demo mode** badge whenever the form is live.

Remember that `resolveMembership()` gives every signed-in user the same
`BIDRADAR_DEV_TENANT_ID` and `BIDRADAR_DEV_ROLE` (OQ-11), so an address on that list is
`tenant_owner` on the internal tenant. **Before any real use, delete both variables and
configure Google or Microsoft** (`AUTH_GOOGLE_ID`/`AUTH_GOOGLE_SECRET`, or
`AUTH_MICROSOFT_ENTRA_ID_ID`/`AUTH_MICROSOFT_ENTRA_ID_SECRET`), redeploy, and confirm
the demo form is gone.

#### Why `API_URL` and not `NEXT_PUBLIC_API_URL` (OQ-77)

Next inlines `NEXT_PUBLIC_*` into the bundle at **build** time. That was the one place
the "same image everywhere" rule leaked: an image built once and promoted carried the
build machine's API URL. It is also unusable here, because `railway up` has no way to
pass a Docker build argument.

Nothing in the browser needs the backend origin — client components call `/api/v1/...`
on their own origin and `src/app/api/v1/[...path]/route.ts` proxies it with the bearer
token `getApiToken()` mints. So the server resolves
`process.env.API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"`,
per request, and a plain runtime variable is enough. `getApiToken()` itself only signs a
JWT and never touches the base URL.

## Storage: the one real gap

Railway has no object store, and BidRadar needs one: the API receives an upload and the
**worker** parses it, in a different container.

**Do this.** Cloudflare R2 (S3 API, no egress fee) or AWS S3, with the same
`STORAGE_BACKEND=s3` and `S3_*` values on api and worker:

```
STORAGE_BACKEND=s3
S3_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com
S3_REGION=auto
S3_ACCESS_KEY=…
S3_SECRET_KEY=…
S3_BUCKET_US=bidradar-us
```

**The alternative, and what it costs you.** A Railway volume:

```bash
railway volume add --service api --mount-path /data
railway variables --service api --set STORAGE_BACKEND=local --set LOCAL_STORAGE_ROOT=/data
```

A Railway volume attaches to **exactly one service** and cannot be mounted by a second
one. So the worker cannot read what the api wrote: every uploaded document fails to
parse, with a missing-file error, and the extraction and drafting agents have nothing to
work on. It also pins api to a single replica. Take this path only for a click-through
of the screens that do not involve a document.

## Bring-up

`scripts/railway/bootstrap.sh` prints this list with the exact commands. The short form:

1. `railway login` · `railway link --project bidradar`
2. `railway add --database postgres` · `railway add --database redis`
3. `railway add --service api|worker|beat|frontend`
4. Project → Settings → **Shared Variables** → `AUTH_SECRET` = `openssl rand -base64 32`
5. Fill `infra/railway/env/*.env`; `scripts/railway/set_vars.sh <svc> <file> --apply` for api, worker, beat
6. Storage: the R2/S3 keys (already in the env files), or `railway volume add --service api --mount-path /data`
7. **api first**: `railway up ./backend --path-as-root --service api --detach`
   `run_migrations()` runs `bootstrap_db` → `alembic upgrade head` → `app.seed`, on
   start and (where Railway honours it) in the pre-deploy hook.
8. `railway logs --service api | grep internal_tenant_id` → the uuid
9. Paste it into `frontend.env`, `set_vars.sh frontend … --apply`, `railway up ./frontend --path-as-root --service frontend --detach`, then `railway domain --service frontend`; set `AUTH_URL` on frontend and `CORS_ORIGINS`/`APP_BASE_URL` on api to that domain
10. `railway up ./backend --path-as-root --service worker --detach`, then the same for `beat`
11. Verify (below)

The order is not negotiable in two places: **api before frontend**, because the tenant id
does not exist until the seed has run; and **api before worker and beat**, because they
would otherwise start against a database with no schema.

## Verification

```bash
# 1. the API is alive (status and version only; `/api/v1/system/info` has the region)
curl -fsS https://<api-domain>/healthz
# {"status":"ok","version":"0.1.0"}

# 2. the front end serves its sign-in page (200, not a redirect)
curl -fsI https://<frontend-domain>/signin

# 3. the bootstrap really happened
railway connect postgres
  \dx                                    -- vector and pg_trgm listed
  \du bidradar_app                       -- "Cannot login" absent; no Bypass RLS
  SELECT count(*) FROM alembic_version;  -- 1
  SELECT id, slug FROM tenants;          -- the internal tenant

# 4. the proxy path, end to end: sign in in a browser, then
#    the top bar must show a tenant. If it says none, BIDRADAR_DEV_TENANT_ID is
#    wrong — it is the uuid from step 8, not the user id.

# 5. an adapter actually runs (admin only, and it needs the worker up)
curl -fsS -X POST https://<api-domain>/api/v1/admin/sources/sam_opps/run \
     -H "Authorization: Bearer <token>"
railway logs --service worker       # the task is picked up and the run row closes
```

**Mail.** There is no Mailpit here. With `EMAIL_PROVIDER=sendgrid` the messages are
real, so verify a sender domain in SendGrid first or everything bounces. With
`EMAIL_PROVIDER=memory` nothing leaves the process and the rendered messages are
readable through the API's own outbox — enough to show that a digest was produced,
not enough to prove delivery.

## Cost

Rough monthly figures at Railway's usage pricing, for a demo that is left running:

| service | shape | why |
| --- | --- | --- |
| api | ~0.5 vCPU / 1 GB, always on | A health check keeps it awake; `sleepApplication` would make the first request after idle take 30 s. |
| worker | ~0.5 vCPU / 1 GB, **always on** | A Celery worker that sleeps does not consume the queue. This is the line item that makes "serverless pricing" not apply. |
| beat | ~0.1 vCPU / 256 MB, **always on**, 1 replica | Tiny, but it cannot sleep either: a scheduler that is asleep at 03:00 does not schedule the 03:00 run. |
| frontend | ~0.25 vCPU / 512 MB | |
| Postgres | 1 GB volume to start | |
| Redis | smallest | |

Two deliberate savings:

- **ClamAV is off** (`SCANNER_BACKEND=noop`). The signature database is about 1 GB of
  RAM plus a `freshclam` download on every cold start — more than the rest of the demo
  costs. It is on in the Cloud Run deployment and it is required for real tenants
  (SPEC 11); `noop` is defensible here only because nothing untrusted is uploaded.
- **`EMBEDDING_PROVIDER=fake`** removes the Voyage bill entirely, at the price of
  meaningless vectors.

The Anthropic and Voyage keys are billed by their own providers, not by Railway, and
the agents are where the real money is.

## Limitations

What this deployment is not, stated plainly so nobody discovers it in a demo:

- **No object store.** S3/R2 is an external dependency, or the volume path breaks
  document parsing. See [Storage](#storage-the-one-real-gap).
- **The membership stub (OQ-11).** `resolveMembership()` in `src/auth.config.ts` does
  not ask the backend who the signed-in user is; it returns
  `BIDRADAR_DEV_TENANT_ID` / `BIDRADAR_DEV_ROLE` for **everyone who signs in**. One
  tenant, one role, no per-user authorisation. That is fine for a single-tenant demo and
  is not fit for a second customer.
- **The demo sign-in is not authentication.** With `AUTH_DEV_LOGIN=1`, anyone who
  knows an allowlisted address is in — no password, no verification. It exists because
  a Railway project has no OAuth client and no SMTP; it has to be turned off before
  anything real.
- **Magic-link sessions do not survive a redeploy.** The Auth.js adapter is in-memory
  (`src/lib/auth-adapter.ts`, dev-only), so verification tokens are lost on restart.
  OAuth sign-in is unaffected.
- **US only.** `REGION=us`, one Railway region, one bucket. The SPEC 12 residency
  guarantee — Indian tenant data never leaving `asia-south1` — is a Cloud Run property
  that this deployment does not have, so no Indian tenant data belongs here.
- **No WhatsApp and no Razorpay.** Both are the India path (SPEC 7, SPEC 10.1) and both
  need verified business accounts. `WHATSAPP_PROVIDER` empty means the channel is simply
  not attempted; Razorpay keys empty means Indian billing is off.
- **No PITR, no backup drill.** Railway's Postgres has backups on paid plans; the
  restore drill in [restore-drill.md](restore-drill.md) has not been run against it.
- **One of everything.** Single replica per service, no multi-region, no canary. A
  Railway deploy replaces the container; the rollback is `railway down` or redeploying a
  previous deployment from `railway deployment list`.

## When something is wrong

| symptom | likely cause | what to do |
| --- | --- | --- |
| api deploy fails in pre-deploy with `extension "vector" is not available` | the plugin image has no pgvector | the custom `pgvector/pgvector:pg16` service above |
| `TypeError: connect() got an unexpected keyword argument 'sslmode'` | a DSN bypassed the normaliser | it only runs on `DATABASE_URL` / `DATABASE_URL_OWNER`; anything else must be written as `?ssl=` |
| `role "bidradar_app" does not exist`, or `password authentication failed for user "bidradar_app"`, on a deploy that otherwise succeeded | the migration never ran — `RUN_MIGRATIONS` unset on api, `BOOTSTRAP_DB=0`, or Railway skipped the pre-deploy hook | set `BOOTSTRAP_DB=1` and `RUN_MIGRATIONS=1` on api and redeploy; the api branch migrates on start, so the bootstrap lines must appear in `railway logs --service api` before uvicorn's |
| `password authentication failed for user "bidradar_app"` | `APP_DB_PASSWORD` and the password inside `DATABASE_URL` differ | make them equal; to change an existing role's password run the bootstrap once with `--rotate-password` |
| two deploys both run alembic and one fails | `RUN_MIGRATIONS=1` on more than one service | remove it from worker and beat |
| every schedule fires twice | beat at two replicas | `numReplicas: 1`, and check nothing overrode it in the dashboard |
| build fails: `missing an id argument` / `missing the cacheKey prefix from its id` | somebody added a BuildKit cache mount back | remove it; see the note above — Railway wants `id=s/<service-id>-…`, which cannot be shared by three services |
| build fails: no `railway.json` / no Dockerfile found | `--path-as-root` was omitted | `railway up ./backend --path-as-root --service api` |
| the UI is empty after signing in | `BIDRADAR_DEV_TENANT_ID` unset or wrong | `railway logs --service api \| grep internal_tenant_id` |
| frontend 502s on every `/api/v1/*` | `API_URL` wrong, or api not up | it is `http://<api private domain>:8080` — plain http, port 8080, not the public https domain |
| uploads parse as missing files | `STORAGE_BACKEND=local` with a volume | the volume is on api only; switch both services to s3 |
