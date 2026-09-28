# Load tests (SPEC 12, M7-10)

SPEC section 12 sets two numbers:

> **Load:** 50k opportunities, 200 profiles scored in **< 10 min**; search **p95 < 500 ms**.

Three scripts measure exactly those, and each one **exits 1** when its target is missed,
so they work as a gate and not only as a benchmark.

| Script | What it does | Fails when |
| --- | --- | --- |
| `seed.py` | writes the corpus: tenants, profiles, their knowledge base, and the notices | a seeded profile is not complete enough to be matched |
| `score.py` | shards `MatchScorer` per profile over a process pool and times it | wall clock > 600 s at 50k × 200 (scaled below that) |
| `search.py` | 500 randomized `GET /api/v1/opportunities` calls | p95 > 500 ms, or any request fails |

## Running them

They need their **own** database — never `bidradar_test`, which `make test` owns, and
never `bidradar`, which is your dev data:

```bash
make load-db        # DROP + CREATE bidradar_load, then infra/postgres/sql/extensions.sql
make load-smoke     # 10% of the SPEC size: 5,000 notices x 20 profiles  (~25 s)
make load-full      # the SPEC size: 50,000 notices x 200 profiles
```

`make load-smoke` is `alembic upgrade head` followed by seed → score → search, and drops
one JSON report per script into `load-report/` (gitignored; the CI workflow uploads the
directory as an artifact). Knobs: `LOAD_SCALE`, `LOAD_WORKERS`, `LOAD_QUERIES`,
`LOAD_REPORT_DIR`, `LOAD_DATABASE_URL`, `LOAD_DATABASE_URL_OWNER`.

By hand, from `backend/` (the scripts are files, not a package — run them by path):

```bash
export DATABASE_URL=postgresql+asyncpg://bidradar_app:bidradar_app@localhost:5433/bidradar_load
export DATABASE_URL_OWNER=postgresql+asyncpg://bidradar:bidradar@localhost:5433/bidradar_load

uv run python ../scripts/load/seed.py   --reset                 # 50k x 200
uv run python ../scripts/load/seed.py   --reset --scale 0.1     # 5k x 20
uv run python ../scripts/load/seed.py   --reset --profiles 40 --opportunities 2000
uv run python ../scripts/load/score.py  --workers 8
uv run python ../scripts/load/search.py --queries 500 --explain
uv run python ../scripts/load/search.py --base-url http://localhost:8000   # a live server
```

`--reset` is a `TRUNCATE` of every application table, so `seed.py` refuses to run it
against a database whose name contains neither `load` nor `test` unless you add
`--force`. Re-seeding is how you get a clean run; the generators are deterministic in
`--seed` (ids are `uuid5(seed, kind, index)`), so the same seed rebuilds the same corpus.

## What the corpus looks like

`seed.py` writes, per `--scale`:

* **tenants** (20), half `us` and half `in`, each with an owner user;
* **profiles** (200) spread evenly across them, each complete enough to clear the
  matching threshold (`core.profile_completeness.MATCHING_THRESHOLD` = 40): identity,
  registrations, three fiscal years of revenue, ≥ 3 codes (NAICS for `us`, GeM/India
  categories for `in`) with a primary, 5 include + 1 exclude keyword, one service line,
  one past performance, a value range, wanted notice types and target geography;
* their **knowledge base** (`kb_chunks`), embedded with `FakeEmbeddings`, so the
  semantic and past-performance signals do real pgvector work with no provider key
  (M4-03, M1-12);
* **opportunities** (50,000) from a 12-domain vocabulary — notice type and status drawn
  from per-region weighted tables, NAICS 70% from the profiles' own codes and 30% from
  unrelated ones, deadlines spread over 90 days with 10% already past and 8% never
  published, `india_category` set for `in` notices — each with its own `FakeEmbeddings`
  vector, so `opportunities.embedding` is never NULL when the scorer runs.

That mix is what makes the measurement honest: about **21%** of the nominal pairs
survive stage 1's SQL prefilter (region, country, wanted notice type, open, deadline
ahead), which is the shape a real corpus has and the shape M4-06's own load-marked test
deliberately does *not* have (there every pair survives).

## How the scoring is parallelised

OQ-95 measured one process at **~1,800 pairs/s** end to end, so 50k × 200 = 10 M pairs
would take ~90 minutes single-threaded and the SPEC target is unreachable without
sharding. Profiles are embarrassingly parallel and `MatchScorer.rescore_profile` is
exactly the per-profile slice of `score_batch`, so `score.py` round-robins the profiles
over a `spawn` process pool, one `asyncio` loop and one `Database` per worker. Stage 3
(the LLM rationale) is deliberately excluded — SPEC 12's load target is stages 1 and 2,
and an LLM call is a network budget, not a scoring one.

`--workers` defaults to `cpu_count - 2`, leaving room for Postgres, which is the other
half of this benchmark.

## Measured numbers

MacBook (Apple silicon, 10 cores, 32 GB), Postgres 16 + pgvector in the compose stack
(`infra/docker-compose.yml`, container limit 7.75 GB), 8 scoring workers, embeddings
`fake`. Two other worktrees were running their own test suites during these runs, so
these are *loaded-machine* numbers rather than best case.

### `make load-smoke` — 5,000 notices × 20 profiles (10% of the SPEC size)

| Stage | Number |
| --- | --- |
| seed | 5,000 notices in 4.4 s (**1,146 rows/s**), 20 profiles, 40 KB chunks, 5.9 s total |
| score | 100,000 nominal pairs, 21,295 scored after the prefilter (21.3% survival), **6.2 s wall**, 18,727 nominal pairs/s, **3,988 scored pairs/s**, 20,406 matches written |
| search | 500 queries, **p50 11.5 ms · p95 52.0 ms · p99 76.0 ms**, max 121.9 ms, 0 failures |

### `make load-full` — 50,000 notices × 200 profiles (the SPEC 12 size)

| Stage | Number | SPEC 12 |
| --- | --- | --- |
| seed | 50,000 notices in 42.0 s (**1,189 rows/s**), 200 profiles, 400 KB chunks, 49.7 s total | — |
| score | 10,000,000 nominal pairs, 2,149,750 scored after the prefilter (21.5% survival), **461.2 s wall**, 21,790 nominal pairs/s, **4,684 scored pairs/s**, 2,065,705 matches written (29,132 high / 214,287 medium / 1,822,286 low) | **PASS** — 461 s of a 600 s budget |
| search | 500 queries, p50 25.8 ms, **p95 538.4 ms**, p99 1,568 ms, max 4,069 ms, 0 failures | **FAIL** — p95 538.4 ms against 500 ms |

Scoring clears the target with 23% of the budget to spare, and the shape is the one
OQ-95 predicted: 8 worker processes spend 3,606 s of CPU in 459 s of wall clock (7.9×
parallel efficiency), a median profile takes 18.0 s and the p95 profile 22.7 s.

**Search misses the target, and the load test is how we found out.** The report's
`by_kind` block splits the 500 queries by whether they ask for `min_score`, and the
whole miss lives on one side of that line:

| Query kind | Queries | p50 | p95 | p99 |
| --- | --- | --- | --- | --- |
| no `min_score` (FTS, region, type, NAICS, status, paging) | 408 | 22.5 ms | **258.5 ms** | 580.3 ms |
| with `min_score` | 92 | 144.5 ms | **1,594.2 ms** | 4,068.9 ms |

`min_score` is `app/services/matching/read.with_min_score`, an `EXISTS` over `matches`.
With 2.07 M match rows the planner picks a nested-loop semi join that probes
`ix_matches_opportunity_id` once per candidate notice and then discards ~43 rows per
probe on `tenant_id` and `score`, because the only usable index on `matches` is keyed on
`opportunity_id` alone:

```
Nested Loop Semi Join  (actual time=4502.820..4502.895 rows=0 loops=1)
  Buffers: shared hit=123106 read=1064497
  ->  Gather Merge ... Parallel Index Scan using ix_opportunities_response_due_at  (rows=25000)
  ->  Index Scan using ix_matches_opportunity_id on matches  (loops=25000)
        Filter: ((tenant_id = current_setting('app.tenant_id')::uuid) AND (score >= 70))
        Rows Removed by Filter: 43
```

A composite index on `matches (tenant_id, opportunity_id, score)` turns each of those
25,000 probes into a single index lookup inside the tenant. That is a migration on an
M4 table, so it is recorded as **OQ-106** rather than made here. Everything else — the
FTS GIN index, the NAICS GIN index, the deadline ordering, paging to page 7 — is inside
the budget at the same corpus size (p95 258.5 ms). Re-run `make load-full` after the
index lands to close the gap.


## Reading a report

Each script prints its JSON report and, with `--report PATH`, writes it too. The fields
that matter:

* `seed.json` — `opportunities`, `profiles`, `matchable_profiles` (must equal
  `profiles`), `kb_chunks`, `rows_per_second`, `stages_seconds`;
* `score.json` — `nominal_pairs` (profiles × notices, the number SPEC 12 names),
  `pairs_scored` (what reached stage 2), `prefilter_survival`, `wall_seconds`,
  `budget_seconds` + `budget_reason`, `nominal_pairs_per_second`,
  `scored_pairs_per_second`, `created` / `updated`, the `high`/`medium`/`low`
  histogram, `per_profile_seconds_p50` / `_p95`, and
  `extrapolated_full_scale_seconds`;
* `search.json` — `p50_ms` / `p95_ms` / `p99_ms` / `max_ms`, `budget_p95_ms`,
  `by_kind` (the same percentiles split into queries with and without `min_score`, so a
  regression in the matches join cannot hide behind the cheap queries), `slowest_query`,
  `failures`, and with `--explain` the `EXPLAIN (ANALYZE, BUFFERS)` plan of the slowest
  query (printed, not stored in the JSON).

### Budgets at a scaled size

The 600 s gate is the SPEC number **at 50k × 200**. Below that size `score.py` scales it
with the pair count but never below a 180 s floor, because a six-second budget on a
shared CI runner measures the runner and nothing else — a scaled run is a regression
smoke, not the SPEC measurement. `budget_reason` in the report always says which of the
two you are looking at, and `--budget-seconds` overrides both.

## In CI

[`.github/workflows/load.yml`](../../.github/workflows/load.yml) runs `make load-smoke`
at **03:30 UTC** nightly and on `workflow_dispatch` (with `scale` and `workers` inputs),
against a `pgvector/pgvector:pg16` service container and its own `bidradar_load`
database, and uploads `load-report/` as an artifact. It is the scaled smoke on purpose:
a two-core GitHub runner cannot produce the production-shaped number, but it will catch
a scoring or search regression overnight. The full-size measurement is a developer-
machine run, recorded above and in `PROGRESS.md`.

`backend/tests/unit/test_load_scripts.py` covers the argument surface, the budget
arithmetic, the determinism and distribution of the generators, and runs all three
scripts end to end at `--scale 0.01` (500 notices × 2 profiles) against the test
database, so the load scripts cannot rot between load runs.

## Re-measurement after the matches index (OQ-106)

With `ix_matches_tenant_opportunity_score` (migration 0012) applied to the same 50,000 × 200 corpus, `search.py --queries 500` reports p50 19.4 ms · p95 156.8 ms · p99 310.3 ms — inside the SPEC 12 gate of p95 < 500 ms. The earlier p95 538 ms figure above predates the index.
