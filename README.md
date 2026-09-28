# BidRadar

BidRadar finds every public contract that fits a company's profile, alerts the team the
moment one appears, drafts the response package with AI agents, and reminds the team
until a human reviews and submits it on time. US federal and grant notices come from
free official APIs; Indian central, GeM and state tenders come from public portals. It
is built internally first — to feed our own biz-dev pipeline — and then as a
multi-tenant SaaS whose first external market is India. A human always submits the bid:
BidRadar never logs in to a portal, never solves a CAPTCHA and never auto-submits
(SPEC [§1](SPEC.md)).

> **Status: under construction.** This repository is built by an autonomous Claude Code
> loop against [SPEC.md](SPEC.md) and [tasks.json](tasks.json). 79 of 112 tasks are done;
> matching, the agent pipeline and the pursuit board are partly or not yet built. See
> [What works today](#what-works-today) before you believe any sentence below implies a
> finished product.

- **Spec** (authoritative): [SPEC.md](SPEC.md)
- **Agent rules** for the build loop: [CLAUDE.md](CLAUDE.md)
- **Build log and open questions**: [PROGRESS.md](PROGRESS.md)
- **All documentation**: [docs/index.md](docs/index.md)

## Architecture at a glance

Six layers, each reading only from the one above it. Agents never write to sources, and
nothing leaves the delivery layer without a person acting on it (SPEC §10).

| Layer | What it is | Where it lives | SPEC |
| --- | --- | --- | --- |
| **Sources** | SAM.gov, Grants.gov, USAspending, CPPP, GeM, GePNIC state portals, paid feeds | `backend/app/adapters/` | §2, §5.1 |
| **Ingestion** | Polite fetch → raw archive → normalize → dedupe → amendments/corrigenda → `source_runs` | `backend/app/adapters/http.py`, `app/services/source_runner.py`, `app/services/ingest.py`, `app/core/normalize/` | §5 |
| **Store** | One canonical opportunity schema in Postgres 16 + pgvector + pg_trgm, row-level security per `tenant_id`, files in per-region buckets | `backend/app/models/`, `backend/migrations/`, `app/services/storage.py` | §5.3, §10.2 |
| **Matching** | Rules + embeddings + an LLM rationale, scored against the company profile | `backend/app/core/matching/`, `app/core/eligibility*.py` | §6 |
| **Agents** | Document parsing, requirement extraction, bid/no-bid, draft package, all through one runner with a cost guard | `backend/app/agents/`, `app/core/parsing/` | §8 |
| **Delivery** | In-app screens, email/Slack/Teams/WhatsApp/push alerts, deadline ladder, exports, admin console | `backend/app/api/`, `app/notify/`, `frontend/` | §7, §9, §10.4 |

Deployment is one backend image in six modes (api, worker, beat, `job:<source_id>`,
migrate, smoke) on Cloud Run in `us-east1` and `asia-south1`; residency is the single
`REGION` variable. See [infra/cloudrun/README.md](infra/cloudrun/README.md) and
[infra/terraform/README.md](infra/terraform/README.md).

## Repo layout

```
SPEC.md              the requirements spec (authoritative; the PDF export lives beside it)
CLAUDE.md            binding rules for the build loop
tasks.json           the machine-readable task list the loop works through
PROGRESS.md          append-only build log + the open questions a human must answer
loop.sh              the headless build loop (SPEC 13.5)
Makefile             every developer entrypoint (lint, test, eval, up, migrate, seed, smoke)
.env.example         every backend setting, in sync with backend/app/core/config.py

backend/             Python 3.12 · FastAPI · SQLAlchemy 2 (async) · Alembic · Celery
  app/adapters/      one module per source, behind the SourceAdapter protocol
  app/agents/        the LLM pipeline (llm.py is the only place that calls Anthropic)
  app/api/           FastAPI routers (v1), middleware, auth dependencies
  app/core/          pure logic: normalizers, scoring, dates, money, eligibility (no I/O)
  app/jobs/          Cloud Run job entrypoints: run_source, smoke, notify, privacy, ...
  app/models/        SQLAlchemy models; every tenant table carries tenant_id + an RLS policy
  app/notify/        channels (email, Slack, Teams, WhatsApp, push) and the digest
  app/services/      I/O-bound services: ingest, storage, events, billing, ratelimit
  docker/            entrypoint.sh (six modes) and the ClamAV bootstrap
  migrations/        Alembic, one hand-numbered migration per milestone
  tests/             unit · adapters (fixtures) · integration · isolation · evals

frontend/            Next.js 15 App Router · TypeScript · Tailwind · shadcn/ui
evals/               golden set + scorers for the agent evals (SPEC 12 pass bars)
infra/               docker-compose.yml (local stack), Terraform, generated Cloud Run YAML
docs/                this documentation set (see docs/index.md)
scripts/             operator scripts (restore_drill.sh)
.github/workflows/   ci · deploy · preview · nightly-smoke
```

## Prerequisites

| Tool | Version | Notes |
| --- | --- | --- |
| Python | 3.12 | managed by `uv`; you do not need a system 3.12 |
| [uv](https://docs.astral.sh/uv/) | 0.11.14 | the lockfile is committed; always use `--frozen` in CI |
| Node | 22 | frontend only |
| [pnpm](https://pnpm.io/) | 10 | frontend only |
| Docker Desktop | any recent | Postgres 16 + pgvector, Redis 7, RustFS, Mailpit |
| GNU Make | 3.81+ | the macOS system `make` is enough |

Optional, and only needed for the deploy path: `gcloud`, `terraform` 1.15.x.
ClamAV and Tesseract are **not** needed locally — both sit behind interfaces whose
default backends are `noop` / `none` (PROGRESS OQ-10); the Docker image carries the real
binaries.

## Local setup

### 1. Start the local stack

```bash
make up          # docker compose -f infra/docker-compose.yml up -d --wait
```

This brings up Postgres 16 with pgvector, Redis 7, RustFS (an S3-compatible store that
replaced MinIO, PROGRESS OQ-12) and Mailpit.

> **Tip — Docker Desktop hangs on `make up`.** On a machine with no interactive keychain,
> Docker Desktop's `credsStore: desktop` credential helper blocks every image pull
> (PROGRESS OQ-13). Work around it by pointing Docker at a config without the helper:
>
> ```bash
> mkdir -p /tmp/dockercfg && \
>   python3 -c "import json,os,pathlib; p=pathlib.Path.home()/'.docker/config.json'; \
>   d=json.loads(p.read_text()) if p.exists() else {}; d.pop('credsStore',None); \
>   pathlib.Path('/tmp/dockercfg/config.json').write_text(json.dumps(d))"
> DOCKER_CONFIG=/tmp/dockercfg make up
> ```
>
> Once the images are cached locally, plain `make up` works again.

### 2. Configure the backend

```bash
cp .env.example backend/.env      # never commit backend/.env
```

The defaults point at the compose stack and need no credentials: storage is `local`,
the scanner and OCR are off, the email provider is `smtp` (Mailpit) and every
third-party key is empty, which disables that feature rather than breaking it. Fill in
`ANTHROPIC_API_KEY` when you want the agents, `SAM_API_KEY` for live US ingestion.

### 3. Migrate and seed

```bash
make migrate     # alembic upgrade head
make seed        # internal tenant + SEED_ADMIN_EMAIL owner
```

### 4. Run the backend

```bash
cd backend
uv sync --all-groups            # first time only
uv run uvicorn app.main:app --reload --port 8000
```

`http://localhost:8000/docs` is the OpenAPI UI; `http://localhost:8000/healthz` is the
probe Cloud Run uses.

To run the background workers as well (needed for scheduled ingestion and the agent
pipeline; otherwise set `CELERY_TASK_ALWAYS_EAGER=true` and everything runs inline):

```bash
cd backend
uv run celery -A app.celery_app worker --loglevel info    # one terminal
uv run celery -A app.celery_app beat   --loglevel info    # another
```

### 5. Run the frontend

```bash
cd frontend
pnpm install
cp .env.example .env.local      # fill AUTH_SECRET — the same value as the backend's
pnpm dev                        # http://localhost:3000
```

`AUTH_SECRET` is shared: the frontend signs the HS256 bearer token that the backend
verifies. See [frontend/README.md](frontend/README.md) for the rest of the frontend
environment and the API-client generator.

### Ports

| Service | URL / port | Notes |
| --- | --- | --- |
| Frontend (Next.js) | http://localhost:3000 | `pnpm dev` |
| Backend API | http://localhost:8000 | `uv run uvicorn app.main:app --reload` |
| Postgres 16 + pgvector | `localhost:5433` | compose maps 5433 → 5432; users `bidradar` (owner) and `bidradar_app` |
| Redis 7 | `localhost:6380` | compose maps 6380 → 6379 |
| RustFS (S3 API) | http://localhost:9000 | credentials `minioadmin` / `minioadmin` |
| RustFS console | http://localhost:9001 | |
| Mailpit (web UI) | http://localhost:8025 | every local email lands here |
| Mailpit (SMTP) | `localhost:1025` | `EMAIL_PROVIDER=smtp` |
| Playwright e2e server | http://localhost:3100 | `pnpm e2e` only |

The non-standard host ports are deliberate: they do not collide with a system Postgres
or Redis.

## Running the checks

| Command | What it runs |
| --- | --- |
| `make lint` | `ruff format --check`, `ruff check`, `mypy` over `app tests migrations` |
| `make format` | `ruff format` + `ruff check --fix` |
| `make test` | `alembic upgrade head`, then pytest with `--cov=app/core --cov-fail-under=85` |
| `make eval` | the agent evals in `backend/tests/evals` against the golden set in `evals/golden/` (partial — the full set is M5-15) |
| `make isolation` | the cross-tenant suite: two tenants, every endpoint, any 200 with foreign data fails |
| `make smoke` | the live adapter smoke; a no-op unless `BIDRADAR_LIVE=1` |
| `make db-reset` / `db-reset-dev` | drop and recreate `bidradar_test` / `bidradar` with the extensions |
| `make load-db` / `make load-smoke` | create `bidradar_load`, then seed + score + search it at 10% of the SPEC 12 size |

Narrower runs:

```bash
cd backend
uv run pytest tests/unit -q                       # fast, no database
uv run pytest tests/unit/test_docs_links.py -q    # the docs link checker
uv run pytest tests/adapters -q                   # contract tests over recorded fixtures
uv run pytest tests/integration -q                # needs the compose stack
```

CI runs the same targets ([.github/workflows/ci.yml](.github/workflows/ci.yml)):
`backend-lint` (plus the docs link checker), `backend-test`, `backend-isolation`,
`frontend-lint-build`, `docker-build`, `terraform-validate` and `security-scan`.
`nightly-smoke.yml` runs the live smoke at 03:00 UTC and pages the ops channel on
failure — see [docs/runbooks/broken-source.md](docs/runbooks/broken-source.md) when it
does.
`load.yml` runs the scaled load smoke at 03:30 UTC and uploads its JSON reports.

### The live smoke

```bash
cd backend
BIDRADAR_LIVE=1 SAM_API_KEY=... uv run python -m app.jobs.smoke --days 7
```

Every **enabled** adapter must return at least one record. The India half of the smoke
is a manual run from an Indian IP or an `asia-south1` job, because several portals
refuse connections from elsewhere (PROGRESS OQ-14); the recipe is in
[docs/adapters.md](docs/adapters.md#5-live-smoke).

### The load tests

SPEC 12's load target — *50k opportunities, 200 profiles scored in < 10 min; search p95
< 500 ms* — is measured by three scripts in [scripts/load/](scripts/load/README.md),
against their **own** database (`bidradar_load`) so a load run never collides with
`make test` on `bidradar_test`:

```bash
make load-db                 # CREATE DATABASE bidradar_load + extensions (compose)
make load-smoke              # 10% of the SPEC size: 5,000 notices x 20 profiles
make load-full               # the SPEC size itself: 50,000 notices x 200 profiles
```

`scripts/load/seed.py` writes the corpus (deterministic in `--seed`, `FakeEmbeddings`
so no provider key is needed), `scripts/load/score.py` shards the batch scorer per
profile over a process pool and **exits 1** over its wall-clock budget, and
`scripts/load/search.py` fires 500 randomized `GET /api/v1/opportunities` calls and
**exits 1** if p95 exceeds 500 ms. Each writes a JSON report under `load-report/`.
[scripts/load/README.md](scripts/load/README.md) has the measured numbers, the
extrapolation and what the budget means at a scaled size.

### One database per worktree

Parallel milestones run in git worktrees on their own branch **and their own databases**
(CLAUDE.md). Before working in a worktree, give it a database name of its own so two
agents never migrate the same schema:

```bash
# in the worktree's backend/.env
DATABASE_URL=postgresql+asyncpg://bidradar_app:bidradar_app@localhost:5433/bidradar_m5
DATABASE_URL_OWNER=postgresql+asyncpg://bidradar:bidradar@localhost:5433/bidradar_m5
TEST_DATABASE_URL=postgresql+asyncpg://bidradar_app:bidradar_app@localhost:5433/bidradar_m5_test
TEST_DATABASE_URL_OWNER=postgresql+asyncpg://bidradar:bidradar@localhost:5433/bidradar_m5_test
```

Create each one the way the compose init script does (`CREATE DATABASE … OWNER bidradar`
then `infra/postgres/sql/extensions.sql`). A worktree agent logs to
`PROGRESS.<branch>.md`, not to `PROGRESS.md`, and flips only its own milestone's entries
in `tasks.json`.

## Environment variables

Every backend setting is a field of
[`backend/app/core/config.py`](backend/app/core/config.py) and nowhere else — no model
id, quota or provider choice may be hard-coded (CLAUDE.md). `.env.example` lists all of
them with local defaults, and `tests/unit/test_env_example.py` fails when the two drift.
The frontend has its own set, documented in [frontend/README.md](frontend/README.md).

**Reading the table.** *Default* is the value in `config.py` when the variable is unset.
*Prod* says whether a production deployment must set it explicitly: `yes` means the
default is a local placeholder or empty and the feature is required; `-` means the
default is fine; a qualified value (`us only`, `in only`, `s3 only`, `if push`, …) means
it is required only on that path. *Secret* marks the settings listed in
`SECRET_SETTINGS`: Terraform creates one empty Secret Manager entry per name and the
Cloud Run manifests mount them with `secretKeyRef`, so they never appear in an image, a
manifest or Terraform state. `tests/unit/test_secret_settings.py` fails when a
credential-looking field is added without listing it, and
`tests/unit/test_docs_links.py` fails when this table stops matching `Settings`.

| Variable | Purpose | Default | Prod | Secret |
| --- | --- | --- | --- | --- |
| `APP_ENV` | Deployment name; `production` turns on JSON logging and the production config checks. | `local` | yes | - |
| `APP_VERSION` | Reported by `/healthz` and sent as the User-Agent version and the trace `service.version`. | `0.1.0` | - | - |
| `LOG_LEVEL` | Root log level for the structlog pipeline. | `INFO` | - | - |
| `CORS_ORIGINS` | Origins the API accepts (comma list or JSON array). | `["http://localhost:3000"]` | yes | - |
| `DATABASE_URL` | Application role DSN — non-owner, RLS enforced. Async (`postgresql+asyncpg://`). | `postgresql+asyncpg://bidradar_app:bidradar_app@localhost:5433/bidradar` | yes | **yes** |
| `DATABASE_URL_OWNER` | Owner role DSN: migrations and the admin bypass path. | `postgresql+asyncpg://bidradar:bidradar@localhost:5433/bidradar` | yes | **yes** |
| `REDIS_URL` | Celery broker/result backend and the rate-limit token buckets. | `redis://localhost:6380/0` | yes | **yes** |
| `CELERY_TASK_ALWAYS_EAGER` | Run Celery tasks inline instead of through the broker (tests, single-process dev). | `false` | - | - |
| `CELERY_BROKER_CONNECT_TIMEOUT` | Seconds the admin "run now" endpoint waits for the broker before running inline. | `2.0` | - | - |
| `STORAGE_BACKEND` | `local` \| `s3` \| `gcs` — where raw payloads, parsed text and exports go. | `local` | yes | - |
| `LOCAL_STORAGE_ROOT` | Directory the `local` backend writes under (one subdirectory per region). | `.storage` | - | - |
| `S3_ENDPOINT_URL` | S3-compatible endpoint (RustFS/MinIO locally). | `http://localhost:9000` | s3 only | - |
| `S3_REGION` | Region sent with S3 requests. | `us-east-1` | s3 only | - |
| `S3_ACCESS_KEY` | S3 access key id. | `minioadmin` | s3 only | **yes** |
| `S3_SECRET_KEY` | S3 secret access key. | `minioadmin` | s3 only | **yes** |
| `S3_BUCKET_US` | S3 bucket holding `us`-residency objects. | `bidradar-us` | s3 only | - |
| `S3_BUCKET_IN` | S3 bucket holding `in`-residency objects. | `bidradar-in` | s3 only | - |
| `GCS_BUCKET_US` | GCS bucket holding `us`-residency objects (CMEK, created by Terraform). | `bidradar-us` | yes | - |
| `GCS_BUCKET_IN` | GCS bucket holding `in`-residency objects; Indian data never leaves it. | `bidradar-in` | yes | - |
| `SIGNED_URL_EXPIRES_SECONDS` | Lifetime of a signed download URL (SPEC 11: short-lived). | `900` | - | - |
| `SCANNER_BACKEND` | `clamav` \| `noop`. Uploads are scanned before they are parsed; `noop` is local/test only. | `noop` | yes | - |
| `CLAMAV_HOST` | clamd TCP host. | `localhost` | - | - |
| `CLAMAV_PORT` | clamd TCP port. | `3310` | - | - |
| `CLAMAV_UNIX_SOCKET` | clamd unix socket path; takes precedence over host/port when set. | _(empty)_ | - | - |
| `OCR_BACKEND` | `tesseract` \| `none`. `none` skips pages that carry no extractable text. | `none` | yes | - |
| `OCR_LANGUAGES` | Tesseract language set for `us` documents. | `eng+hin` | - | - |
| `OCR_LANGUAGES_IN` | Tesseract language set for `in` documents (Hindi + English, SPEC 12). | `eng+hin` | - | - |
| `TESSERACT_CMD` | Path to the `tesseract` binary when it is not on `PATH`. | _(empty)_ | - | - |
| `REGION` | Data residency of this deployment: `us` or `in`. Picks the bucket and the regional database. | `us` | yes | - |
| `AUTH_SECRET` | HS256 secret shared with the frontend's Auth.js. `openssl rand -base64 32`. | `dev-only-change-me-0123456789abcdef0123456789abcdef` | yes | **yes** |
| `FIELD_ENCRYPTION_KEY` | base64 of 32 random bytes; AES-256-GCM for the SPEC 11 encrypted columns. | `ZGV2LW9ubHktMzItYnl0ZS1rZXktY2hhbmdlLW1lISE=` | yes | **yes** |
| `AUTH_RATE_LIMIT_PER_MINUTE` | Failed-auth attempts allowed per IP per minute. | `20` | - | - |
| `TRUST_PROXY_HEADERS` | Read the client IP from `X-Forwarded-For`. Only true behind a proxy that overwrites it (Cloud Run does). | `false` | yes | - |
| `RATE_LIMIT_ENABLED` | Master switch for the API token bucket. | `true` | - | - |
| `RATE_LIMIT_TENANT_PER_MINUTE` | Requests per minute per tenant. | `600` | - | - |
| `RATE_LIMIT_IP_PER_MINUTE` | Requests per minute per client IP (the unauthenticated flood guard). | `120` | - | - |
| `RATE_LIMIT_EXEMPT_PATHS` | Path prefixes the limiter never touches (health probe, signed webhooks). | `["/healthz","/api/v1/webhooks"]` | - | - |
| `CONTACT_EMAIL` | Mailbox named in the crawler User-Agent and in the public notices. | `ops@example.com` | yes | - |
| `APP_BASE_URL` | Public web app URL; every notification deep link is built from it. | `http://localhost:3000` | yes | - |
| `API_BASE_URL` | Public API URL; signed one-click action links point here. | `http://localhost:8000` | yes | - |
| `NOTIFY_MAX_ATTEMPTS` | Attempts per channel before a notification falls back to email. | `3` | - | - |
| `OPS_SLACK_WEBHOOK_URL` | Slack incoming webhook for operator alerts (`adapter.failing` after > 2 consecutive runs, nightly smoke failures). Empty disables the channel. | _(empty)_ | yes | **yes** |
| `OPS_EMAIL` | Operator mailbox that receives the same ops alerts by email. Empty disables it. | _(empty)_ | yes | - |
| `WHATSAPP_PROVIDER` | `gupshup` \| `twilio` \| empty. Selects the WhatsApp Business BSP; empty disables the channel (IN tenants only). | _(empty)_ | if WhatsApp | - |
| `WHATSAPP_TEMPLATE_DEADLINE` | Pre-approved BSP template name for deadline reminders. | _(empty)_ | if WhatsApp | - |
| `WHATSAPP_TEMPLATE_HIGH_MATCH` | Pre-approved BSP template name for high-fit alerts. | _(empty)_ | if WhatsApp | - |
| `WHATSAPP_TEMPLATE_LANGUAGE` | Template language code sent to the BSP. | `en` | - | - |
| `WHATSAPP_WEBHOOK_SECRET` | Shared secret used to verify BSP delivery-status webhooks. | _(empty)_ | if WhatsApp | **yes** |
| `GUPSHUP_API_KEY` | Gupshup API key. | _(empty)_ | if gupshup | **yes** |
| `GUPSHUP_API_URL` | Gupshup WhatsApp API base URL. | `https://api.gupshup.io/wa/api/v1` | - | - |
| `GUPSHUP_APP_NAME` | Gupshup app name. | `BidRadar` | if gupshup | - |
| `GUPSHUP_SOURCE_NUMBER` | Gupshup WhatsApp sender number (E.164). | _(empty)_ | if gupshup | - |
| `TWILIO_ACCOUNT_SID` | Twilio account SID. | _(empty)_ | if twilio | - |
| `TWILIO_AUTH_TOKEN` | Twilio auth token. | _(empty)_ | if twilio | **yes** |
| `TWILIO_API_URL` | Twilio API base URL. | `https://api.twilio.com` | - | - |
| `TWILIO_WHATSAPP_FROM` | Twilio WhatsApp sender (`whatsapp:+E164`). | _(empty)_ | if twilio | - |
| `HOURS_SAVED_PER_PACKAGE` | Labelled estimate of analyst hours saved per submitted package, used by the dashboard KPI. | `20` | - | - |
| `NOTIFY_BACKOFF_SECONDS` | Backoff ladder between those attempts, in seconds. | `[1.0,2.0,4.0]` | - | - |
| `NOTIFY_ACTION_TTL_SECONDS` | How long a signed Pursue/Watch/Pass/Assign or unsubscribe link stays valid. | `1209600` | - | - |
| `EMAIL_PROVIDER` | `ses` \| `sendgrid` \| `smtp` \| `memory`. `smtp` points at Mailpit locally; `memory` is for tests. | `smtp` | yes | - |
| `EMAIL_FROM` | Envelope and header sender address. | `alerts@bidradar.example` | yes | - |
| `EMAIL_FROM_NAME` | Display name on outgoing mail. | `BidRadar` | - | - |
| `EMAIL_REPLY_TO` | Reply-To header; empty means none. | _(empty)_ | - | - |
| `EMAIL_POSTAL_ADDRESS` | Physical address printed in every commercial message (CAN-SPAM). | `BidRadar, 1 Example Street, Wilmington, DE 19801, USA` | yes | - |
| `SES_REGION_US` | SES region for `us` tenants. | `us-east-1` | ses only | - |
| `SES_REGION_IN` | SES region for `in` tenants — `ap-south-1`, so Indian mail stays in region. | `ap-south-1` | ses only | - |
| `SENDGRID_API_KEY` | SendGrid API key. | _(empty)_ | sendgrid only | **yes** |
| `SENDGRID_BASE_URL` | SendGrid API base URL (override for a regional endpoint or a stub). | `https://api.sendgrid.com` | - | - |
| `SMTP_HOST` | SMTP host (Mailpit locally). | `localhost` | smtp only | - |
| `SMTP_PORT` | SMTP port (1025 for Mailpit). | `1025` | smtp only | - |
| `SMTP_USERNAME` | SMTP username; empty means no AUTH. | _(empty)_ | - | - |
| `SMTP_PASSWORD` | SMTP password. | _(empty)_ | - | **yes** |
| `SMTP_STARTTLS` | Issue STARTTLS before sending. | `false` | - | - |
| `SMTP_TIMEOUT_SECONDS` | Socket timeout for the SMTP conversation. | `10.0` | - | - |
| `VAPID_PUBLIC_KEY` | VAPID public key (RFC 8292); empty disables web push. Served to the browser at runtime by `GET /api/v1/me/push-config`, so the frontend's `NEXT_PUBLIC_VAPID_PUBLIC_KEY` is now optional (a build-time fallback for an older API). | _(empty)_ | if push | - |
| `VAPID_PRIVATE_KEY` | VAPID private key. | _(empty)_ | if push | **yes** |
| `VAPID_SUBJECT` | VAPID `sub` claim — a `mailto:` a push service can reach you at. | `mailto:ops@example.com` | if push | - |
| `SAM_API_KEY` | api.data.gov key for SAM.gov opportunities, awards and entity lookups. | _(empty)_ | yes | **yes** |
| `SAM_DAILY_QUOTA` | Requests per UTC day the key may spend; the polite client refuses beyond it (OQ-3). | `10` | - | - |
| `SAM_AWARDS_API_URL` | Contract-awards search endpoint (successor of the retired ATOM feed, OQ-40). | `https://api.sam.gov/contract-awards/v1/search` | - | - |
| `SAM_ENTITY_API_URL` | Entity Management API used by profile autofill by UEI. | `https://api.sam.gov/entity-information/v3/entities` | - | - |
| `SAM_AWARDS_NAICS` | NAICS codes the daily awards job asks for (comma list or JSON array). | _(empty)_ | - | - |
| `HTTP_DEFAULT_RATE_PER_SEC` | Default token-bucket rate per host. | `2.0` | - | - |
| `HTTP_GOV_IN_RATE_PER_SEC` | Rate for `*.gov.in` hosts (SPEC 5.1: 1 req/s). | `1.0` | - | - |
| `HTTP_RATE_LIMITS` | JSON map host -> req/s, overriding the two defaults. | _(empty)_ | - | - |
| `HTTP_MAX_ATTEMPTS` | Attempts per request before the adapter gives up (exponential backoff with jitter). | `5` | - | - |
| `HTTP_TIMEOUT_SECONDS` | Per-request timeout. | `30.0` | - | - |
| `ANTHROPIC_API_KEY` | Anthropic key for every agent and the match rationale. Empty disables LLM features. | _(empty)_ | yes | **yes** |
| `CPPP_BY_ORG_URL` | CPPP "tenders by organisation" page; empty disables that secondary page. | `https://eprocure.gov.in/cppp/tendersbyorganisation` | - | - |
| `CPPP_MAX_ORGS_PER_RUN` | Organisation listings CPPP follows per run. | `25` | - | - |
| `GEPNIC_MAX_ORGS_PER_RUN` | Organisation listings each GePNIC portal follows per run. | `200` | - | - |
| `GEM_BIDS_URL` | JSON endpoint behind the public GeM All Bids page. | `https://bidplus.gem.gov.in/all-bids-data` | - | - |
| `GEM_BID_PAGE_URL` | Per-bid document URL template (`{bid_id}`). | `https://bidplus.gem.gov.in/showbidDocument/{bid_id}` | - | - |
| `GEM_SELLER_REGISTRATION_URL` | Seller-registration link surfaced on every GeM record. | `https://gem.gov.in/register/seller/signup` | - | - |
| `GEM_MAX_PAGES` | Listing pages fetched per GeM run. | `20` | - | - |
| `LLM_MODEL_OPUS_CLASS` | Model id for the Opus-class calls (drafting). The only place ids live. | `claude-opus-5` | - | - |
| `LLM_MODEL_SONNET_CLASS` | Model id for the Sonnet-class calls (extraction, bid/no-bid). | `claude-sonnet-5` | - | - |
| `LLM_MODEL_HAIKU_CLASS` | Model id for the Haiku-class calls (cheap classification, summaries). | `claude-haiku-4-5` | - | - |
| `LLM_MODEL_RATIONALE` | Model class used for the match rationale (OQ-9). | `claude-sonnet-5` | - | - |
| `LLM_PRICES` | JSON map model id -> {input, output, cache_read, cache_write} USD per million tokens; the cost ledger uses these, never a guess. Empty means the defaults in `config.py`. | see `DEFAULT_LLM_PRICES` in `config.py` | - | - |
| `LLM_MAX_TOKENS` | Default output cap per LLM call. | `4096` | - | - |
| `LLM_OUTPUT_RETRIES` | Extra attempts when a JSON tool output fails schema validation. | `2` | - | - |
| `AGENT_FANOUT` | `inline` \| `celery`. How the section drafters fan out over the outline's volumes (M5-08). `celery` needs a broker and falls back to inline when none answers. | `inline` | - | - |
| `AGENT_FANOUT_CONCURRENCY` | Volumes drafted at once when the fan-out is inline. | `3` | - | - |
| `AGENT_FANOUT_TIMEOUT` | Seconds to wait for a Celery drafting group before the step fails. | `900.0` | - | - |
| `EMBEDDING_PROVIDER` | `voyage` \| `fake`. `fake` is deterministic and offline (tests). | `voyage` | - | - |
| `EMBEDDING_MODEL` | Embedding model id. | `voyage-3` | - | - |
| `EMBEDDING_DIM` | Vector width; must match the pgvector column (1024). | `1024` | - | - |
| `EMBEDDING_BATCH_SIZE` | Texts per embedding request. | `128` | - | - |
| `VOYAGE_API_KEY` | Voyage AI key. | _(empty)_ | voyage only | **yes** |
| `VOYAGE_API_URL` | Voyage embeddings endpoint. | `https://api.voyageai.com/v1/embeddings` | - | - |
| `FX_RATES` | JSON map currency -> USD rate used by `core.money.to_usd`; every currency needs an entry, USD included. Static until the FX job lands (OQ-22). | `{"USD" 1.0,"INR" 0.012}` | - | - |
| `STRIPE_SECRET_KEY` | Stripe secret key (billing for `us` tenants). | _(empty)_ | us only | **yes** |
| `STRIPE_WEBHOOK_SECRET` | Stripe webhook signing secret; the only thing that authenticates the webhook. | _(empty)_ | us only | **yes** |
| `STRIPE_API_URL` | Stripe API base URL. | `https://api.stripe.com/v1` | - | - |
| `STRIPE_PRICE_IDS` | JSON map plan -> Stripe price id. | _(empty)_ | us only | - |
| `RAZORPAY_KEY_ID` | Razorpay key id (billing for `in` tenants). | _(empty)_ | in only | **yes** |
| `RAZORPAY_KEY_SECRET` | Razorpay key secret. | _(empty)_ | in only | **yes** |
| `RAZORPAY_WEBHOOK_SECRET` | Razorpay webhook signing secret. | _(empty)_ | in only | **yes** |
| `RAZORPAY_API_URL` | Razorpay API base URL. | `https://api.razorpay.com/v1` | - | - |
| `RAZORPAY_PLAN_IDS` | JSON map plan -> Razorpay plan id. | _(empty)_ | in only | - |
| `BILLING_GSTIN` | Our GSTIN, the supplier of record on Indian invoices; its first two digits decide CGST/SGST vs IGST (OQ-59). | _(empty)_ | in only | - |
| `BILLING_GST_RATE_PCT` | GST rate applied to SaaS subscriptions (SAC 998314). | `18` | - | - |
| `DPDP_NOTICE_VERSION` | Version stamped on a DPDP consent row; bump it when the notice text changes. | `v1` | - | - |
| `PRIVACY_POLICY_VERSION` | Version stamped on a privacy-policy acceptance. | `v1` | - | - |
| `TERMS_VERSION` | Version stamped on a terms acceptance. | `v1` | - | - |
| `DATA_REQUEST_SLA_DAYS` | Statutory answer-by window for a data-principal request, in days from receipt. | `30` | - | - |
| `GRIEVANCE_OFFICER_NAME` | Grievance officer published in the DPDP notice. | _(empty)_ | in only | - |
| `GRIEVANCE_OFFICER_EMAIL` | Grievance officer mailbox published in the DPDP notice. | _(empty)_ | in only | - |
| `SEED_ADMIN_EMAIL` | Owner account `make seed` creates for the internal tenant. | `admin@example.com` | - | - |
| `SENTRY_DSN` | Sentry DSN; empty means Sentry is not initialised at all. | _(empty)_ | yes | **yes** |
| `SENTRY_TRACES_SAMPLE_RATE` | Fraction of transactions Sentry samples. | `0.0` | - | - |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP/HTTP collector base URL; spans go to `<endpoint>/v1/traces`. Empty means no tracer provider. | _(empty)_ | yes | - |
| `LANGFUSE_PUBLIC_KEY` | Langfuse public key for LLM traces; empty means a NoopTracer. | _(empty)_ | - | **yes** |
| `LANGFUSE_SECRET_KEY` | Langfuse secret key. | _(empty)_ | - | **yes** |
| `LANGFUSE_HOST` | Langfuse host (self-hosted or cloud). | `https://cloud.langfuse.com` | - | - |

## Deploy

Four environments, one pipeline (SPEC §12): every merge to `main` deploys **dev**
(`us-east1`), a `v*` tag promotes the *same image digest* to **staging-in**
(`asia-south1`), then **prod-us** and **prod-in**, each behind its own approval. A
promotion never rebuilds, and every deploy migrates the schema in a Cloud Run job
before it moves traffic.

Full procedure, hand-deploy, rollback and the first-deploy secret bootstrap:
**[docs/runbooks/deploy.md](docs/runbooks/deploy.md)**. Infrastructure is Terraform
([infra/terraform/README.md](infra/terraform/README.md)); the Cloud Run manifests are
generated from the adapter registry
([infra/cloudrun/README.md](infra/cloudrun/README.md)).

## Documentation

Everything is indexed in **[docs/index.md](docs/index.md)**. The pages you are most
likely to want:

| Page | When |
| --- | --- |
| [docs/adapters.md](docs/adapters.md) | adding a source, the politeness and compliance rules, fixtures, the smoke |
| [docs/runbooks/broken-source.md](docs/runbooks/broken-source.md) | a portal changed its layout or an API started erroring |
| [docs/runbooks/deploy.md](docs/runbooks/deploy.md) | shipping, promoting, rolling back |
| [docs/runbooks/observability.md](docs/runbooks/observability.md) | "what happened to this request?" |
| [docs/runbooks/restore-drill.md](docs/runbooks/restore-drill.md) | backups, PITR and the restore drill |
| [SECURITY.md](SECURITY.md) | reporting a vulnerability; the security posture in brief |
| [docs/security/asvs-l2.md](docs/security/asvs-l2.md) | the OWASP ASVS L2 control checklist, honest about the gaps |
| [docs/privacy/dpdp-notice.md](docs/privacy/dpdp-notice.md) · [docs/privacy/sub-processors.md](docs/privacy/sub-processors.md) | DPDP consent text and the sub-processor list |
| [docs/legal.md](docs/legal.md) | what we crawl, why we believe we may, and what needs counsel |

## The build loop

This repository is built by Claude Code running headless against the spec
(SPEC §13). Three files drive it and are the fastest way to understand where the
project is:

- **[tasks.json](tasks.json)** — 112 tasks across M0–M7, each with `depends_on` and
  testable `acceptance` lines. The loop takes the first `todo` whose dependencies are
  all `done`.
- **[PROGRESS.md](PROGRESS.md)** — an append-only log (task · what changed · how
  verified) and the **Open questions**: every place the spec was ambiguous, what was
  decided instead, and what still needs a human.
- **[loop.sh](loop.sh)** — the runner. `./loop.sh` iterates until no task is `todo`.
  Run it in a container with no production credentials and read PROGRESS.md twice a day
  (SPEC §13.5).

Rules the loop must follow — tests before code, never weaken a test, never commit a
secret, never add CAPTCHA solving or portal login — are in
[CLAUDE.md](CLAUDE.md).

## What works today

Counted from [tasks.json](tasks.json) when this was written — the number moves, the file
is the truth: **79 of 112 tasks done**. Milestones M4–M6 are still being built in
parallel worktrees, so their counts here lag the branches.

| Milestone | Done | Todo | State |
| --- | --- | --- | --- |
| M0 foundation (repo, compose, config, auth, RLS, CI) | 13 | 0 | complete |
| M1 company profile, knowledge base, onboarding wizard | 13 | 0 | complete |
| M2 ingestion: adapters, normalize, dedupe, versions, search | 18 | 0 | complete |
| M3 India: CPPP, GeM, GePNIC, dates, money, Hindi OCR | 10 | 0 | complete |
| M4 matching and alerts | 8 | 8 | partial |
| M5 agent pipeline | 7 | 11 | partial |
| M6 pursuits, deadlines, exports, pipeline board | 1 | 9 | barely started |
| M7 hardening, deploy, billing, privacy, admin, docs | 9 | 5 | partial |

**Built and tested.** The ingestion path end to end: nine enabled adapters (SAM.gov
opportunities and awards, USAspending, Grants.gov, CPPP, GeM, GePNIC Tamil Nadu / Uttar
Pradesh / central) plus documented stubs and paid-feed shells, polite HTTP with
robots/rate-limit/quota/raw-archive, normalization, dedupe, amendment versions, and
full-text plus vector search. Also: the company profile, knowledge base and onboarding
wizard; tenant isolation with RLS and the cross-tenant suite; hard filters, the weighted
fit score and the eligibility signal; the notification core with the email, Slack, Teams,
in-app and web-push channels, quiet hours and the digest; the agent runtime with its cost
guard and agents 1, 2, 3 and 7 (documents, requirements with page citations, compliance
matrix, pricing template); the Docker image, four Terraform environments, the deploy
pipeline with preview environments; rate limiting and dependency/secret scanning; billing
webhooks; the DPDP privacy endpoints; the admin console; and observability.

**Not yet built** — do not read anything above as a claim that these work:

- **Matching:** semantic similarity / BM25 / past-performance signals, the stage-3 LLM
  rationale, the batch scoring job, match feedback, saved searches and alert rules.
- **Alerts:** the event router that decides which event goes to which channel, and the
  ingest → match → notify integration test.
- **Agents:** bid/no-bid, outline and win themes, section drafters, red-team review, the
  grounding validator, the prompt-injection evals, and the DOCX/PDF/XLSX/ZIP exports.
  The golden-set evals are partial; `make eval` runs what exists, not the full SPEC 12
  set of 10 US + 10 Indian notices.
- **Pursuits:** stages, key dates, the reminder ladder, calendar and iCal, WhatsApp,
  recurring checks, tasks and comments, dashboard KPIs, the Kanban board.
- **Frontend:** everything past onboarding and the opportunity screens — the home
  dashboard, alerts inbox, pursuit workspace, settings and the pipeline board.
- **Verification:** load tests, the automated India checklist, the Playwright and
  accessibility flows, and the MVP acceptance checklist that maps SPEC 12 to tests.

### Open questions that need a human

The full list is at the top of [PROGRESS.md](PROGRESS.md); these are the ones that block
something real:

| Question | Why it matters |
| --- | --- |
| **OQ-14 — which Indian portals** | `mahatenders.gov.in` is `Disallow: /` and stays link-only; Telangana and Karnataka are not GePNIC. The shipped set is Tamil Nadu, Uttar Pradesh and central GePNIC. SPEC §14 proposed a different three; an owner has to confirm the swap. |
| **OQ-59 — GST treatment** | Place of supply falls back to inter-state (IGST) when a customer has neither `notes.place_of_supply` nor a GSTIN. A CA must confirm that, plus reverse charge for unregistered recipients and the export-of-services case, before the first Indian invoice. |
| **OQ-77 — the frontend API base URL** | `NEXT_PUBLIC_API_BASE_URL` is baked in at build time, so the one image promoted to all four environments carries the **dev** API URL. It must move to a runtime variable or the frontend must be built per environment. This is the one place the "same image everywhere" rule leaks. |
| **OQ-83 — the restore drill has never been run** | `scripts/restore_drill.sh` exists and has only been exercised with `--dry-run`; there is no GCP project yet. SPEC §11's "restore drill before launch" is **not** satisfied — the tooling is, the drill is not. |
| **Legal opinion (SPEC §14)** | Commercial use of Indian portal data, the DPDP obligations, the terms of service and the privacy policy all need written counsel. [docs/legal.md](docs/legal.md) marks each item **ACTION**; none of them is legal advice. |

Also unanswered and cheaper: the product name and domain (OQ-1, "BidRadar" is a
placeholder), which company profile seeds the internal tenant (OQ-2), and the SAM.gov
key type (OQ-3 — the code assumes one non-federal personal key with a 10/day quota).

## Licence

**Not yet chosen.** No licence file has been added, so by default no permission is
granted to use, copy or distribute this code. Pick one before the repository leaves the
team — that decision belongs with the same counsel review as the terms of service
(SPEC §14).
