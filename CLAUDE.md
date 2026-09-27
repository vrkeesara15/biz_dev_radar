# CLAUDE.md — agent rules for the BidRadar build loop

These rules come from SPEC.md §13.3 and are binding for every iteration.

- Read SPEC.md and tasks.json before any change. Work on ONE task: the first
  with status "todo" whose depends_on are all "done".
- Write or update tests first from the task's acceptance criteria, then code
  until they pass.
- Run: `make lint && make test` (and `make eval` when touching
  backend/app/agents). Never mark a task done with failing checks.
- Never weaken, skip or delete a test to make it pass. If the spec is
  ambiguous, add a question to PROGRESS.md under "Open questions", pick the
  safest option, and continue.
- Never commit secrets; read keys from env. Never add CAPTCHA solving, portal
  login automation or auto-submission.
- Keep adapters behind the SourceAdapter protocol; keep model IDs in config.
- After finishing: set task status "done", append a 3-line entry to
  PROGRESS.md (task, what changed, how verified), git commit with the task id.
- If blocked 3 iterations on the same task, set status "blocked" with the
  reason and move to the next unblocked task.

## Repo conventions (decided in M0, keep consistent)

- Backend: Python 3.12, FastAPI, SQLAlchemy 2 (async, `asyncpg`), Alembic,
  Pydantic v2, Celery + Redis. Package manager: `uv` (lockfile committed).
  Lint: `ruff` (format + check) and `mypy`. Tests: `pytest` + `pytest-asyncio`.
- Frontend: Next.js 15 App Router, TypeScript, Tailwind, shadcn/ui, TanStack
  Table, TipTap. Package manager: `pnpm`. Typed API client generated from
  the backend OpenAPI spec into `frontend/src/lib/api/`.
- Layout: `backend/app/{api,core,models,services,agents,adapters,notify}`.
  `core/` holds pure logic (normalizers, scoring, date/money, eligibility)
  and must stay free of I/O so it can reach ≥ 85% line coverage.
- Every tenant-scoped table has `tenant_id uuid NOT NULL` and an RLS policy
  keyed on `current_setting('app.tenant_id')`. Use `TenantMixin`. The
  isolation test enumerates `information_schema` and fails on any tenant
  table without RLS.
- Alembic: one migration per milestone, hand-numbered
  `0001_m0_foundation.py`, `0002_m1_profile.py`, … so heads never fork.
  Revision ids are the file stem. Later tasks in the same milestone edit
  that milestone's migration in place until the milestone is done.
- Config: `backend/app/core/config.py` (`pydantic-settings`). All model IDs,
  provider choices and quotas live there and in `.env.example`, never in code.
- LLM calls go through `backend/app/agents/llm.py` (Anthropic SDK, JSON-schema
  tool outputs, prompt caching, Langfuse hooks, cost accounting). Tests use
  the `FakeLLM` in `backend/tests/conftest.py`; never hit the network in tests.
- Embeddings go through `backend/app/services/embeddings.py`
  (`EmbeddingProvider` protocol, 1024 dims; Voyage default, `FakeEmbeddings`
  in tests).
- Adapters: `backend/app/adapters/<source_id>.py` implementing
  `SourceAdapter`; recorded fixtures under `backend/tests/adapters/fixtures/
  <source_id>/`. HTTP only via `backend/app/adapters/http.py` (rate limits,
  backoff, robots.txt, raw payload archiving).
- Local infra: `docker compose -f infra/docker-compose.yml up -d` gives
  Postgres 16 + pgvector, Redis, MinIO, Mailpit. Tests use
  `DATABASE_URL` from env (default points at the compose Postgres,
  database `bidradar_test`).
- Commit message format: `<task-id>: <title>` followed by a short body.
