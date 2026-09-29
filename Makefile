# BidRadar developer entrypoints (GNU Make 3.81 compatible; no GNU-4-only syntax).
SHELL := /bin/bash
COMPOSE := docker compose -f infra/docker-compose.yml
UV := uv
BACKEND := backend
LOAD_SCRIPTS := ../scripts/load
# M7-10: the SPEC 12 load corpus lives in its own database so a load run never
# collides with `make test` on bidradar_test.
LOAD_DATABASE_URL ?= postgresql+asyncpg://bidradar_app:bidradar_app@localhost:5433/bidradar_load
LOAD_DATABASE_URL_OWNER ?= postgresql+asyncpg://bidradar:bidradar@localhost:5433/bidradar_load
LOAD_SCALE ?= 0.1
LOAD_WORKERS ?= 8
LOAD_QUERIES ?= 500
LOAD_REPORT_DIR ?= load-report
LOAD_ENV := DATABASE_URL="$(LOAD_DATABASE_URL)" DATABASE_URL_OWNER="$(LOAD_DATABASE_URL_OWNER)"


.PHONY: help lint format test eval up down db-reset db-reset-dev seed migrate smoke acceptance india-check isolation load-db load-smoke load-full

help:
	@echo "targets: lint format test eval up down db-reset seed migrate smoke acceptance india-check isolation load-db load-smoke load-full"

lint:
	cd $(BACKEND) && $(UV) run ruff format --check app tests migrations
	cd $(BACKEND) && $(UV) run ruff check app tests migrations
	cd $(BACKEND) && $(UV) run ruff format --check --config pyproject.toml $(LOAD_SCRIPTS)
	cd $(BACKEND) && $(UV) run ruff check --config pyproject.toml $(LOAD_SCRIPTS)
	cd $(BACKEND) && $(UV) run mypy

format:
	cd $(BACKEND) && $(UV) run ruff format app tests migrations
	cd $(BACKEND) && $(UV) run ruff check --fix app tests migrations
	cd $(BACKEND) && $(UV) run ruff format --config pyproject.toml $(LOAD_SCRIPTS)
	cd $(BACKEND) && $(UV) run ruff check --fix --config pyproject.toml $(LOAD_SCRIPTS)

test: migrate
	cd $(BACKEND) && $(UV) run pytest --cov=app/core --cov-report=term-missing --cov-fail-under=85

eval:
	cd $(BACKEND) && $(UV) run pytest tests/evals -p no:cacheprovider

up:
	$(COMPOSE) up -d --wait

down:
	$(COMPOSE) down

db-reset:
	$(COMPOSE) exec -T postgres psql -v ON_ERROR_STOP=1 -U bidradar -d postgres -c "DROP DATABASE IF EXISTS bidradar_test WITH (FORCE)" -c "CREATE DATABASE bidradar_test OWNER bidradar"
	$(COMPOSE) exec -T postgres psql -v ON_ERROR_STOP=1 -U bidradar -d bidradar_test -f /docker-entrypoint-initdb.d/sql/extensions.sql

# Dev database: needed after a milestone migration was edited in place (see CLAUDE.md).
db-reset-dev:
	$(COMPOSE) exec -T postgres psql -v ON_ERROR_STOP=1 -U bidradar -d postgres -c "DROP DATABASE IF EXISTS bidradar WITH (FORCE)" -c "CREATE DATABASE bidradar OWNER bidradar"
	$(COMPOSE) exec -T postgres psql -v ON_ERROR_STOP=1 -U bidradar -d bidradar -f /docker-entrypoint-initdb.d/sql/extensions.sql

migrate:
	cd $(BACKEND) && $(UV) run alembic upgrade head

seed: migrate
	cd $(BACKEND) && $(UV) run python -m app.seed

isolation:
	cd $(BACKEND) && $(UV) run pytest tests/isolation -p no:cacheprovider

# Live smoke: >= 1 record per enabled adapter. Skipped (exit 0) unless BIDRADAR_LIVE=1.
smoke:
	cd $(BACKEND) && $(UV) run python -m app.jobs.smoke

# M7-14 (SPEC 12 "Acceptance criteria for MVP"): run the automated subset of the
# checklist and print it, one row per box, exit non-zero on any red row. The
# row -> pytest node id map is scripts/acceptance.json; docs/acceptance.md is
# the same table in prose with the manual steps.
# ACCEPTANCE_WITH_E2E=1 also runs the frontend Playwright + axe flows.
acceptance: migrate
	cd $(BACKEND) && $(UV) run python ../scripts/acceptance.py

# The SPEC 12 India checklist, automated subset (M7-11). The manual rows and
# their evidence fields are in docs/runbooks/india-testing.md.
india-check:
	cd $(BACKEND) && $(UV) run pytest tests/integration/test_india_checklist.py

# --- load tests (M7-10, SPEC 12) -------------------------------------------------------
# `make load-db` needs the compose Postgres; CI creates bidradar_load with psql instead.

load-db:
	$(COMPOSE) exec -T postgres psql -v ON_ERROR_STOP=1 -U bidradar -d postgres -c "DROP DATABASE IF EXISTS bidradar_load WITH (FORCE)" -c "CREATE DATABASE bidradar_load OWNER bidradar"
	$(COMPOSE) exec -T postgres psql -v ON_ERROR_STOP=1 -U bidradar -d bidradar_load -f /docker-entrypoint-initdb.d/sql/extensions.sql

# Seed + score + search at LOAD_SCALE (default 0.1 = 5,000 notices x 20 profiles).
# Every step fails the target: score exits 1 over its budget, search exits 1 over p95.
load-smoke:
	cd $(BACKEND) && DATABASE_URL_OWNER="$(LOAD_DATABASE_URL_OWNER)" $(UV) run alembic upgrade head
	cd $(BACKEND) && $(LOAD_ENV) $(UV) run python $(LOAD_SCRIPTS)/seed.py --scale $(LOAD_SCALE) --reset --report ../$(LOAD_REPORT_DIR)/seed.json
	cd $(BACKEND) && $(LOAD_ENV) $(UV) run python $(LOAD_SCRIPTS)/score.py --scale $(LOAD_SCALE) --workers $(LOAD_WORKERS) --report ../$(LOAD_REPORT_DIR)/score.json
	cd $(BACKEND) && $(LOAD_ENV) $(UV) run python $(LOAD_SCRIPTS)/search.py --queries $(LOAD_QUERIES) --explain --report ../$(LOAD_REPORT_DIR)/search.json

# The SPEC 12 size itself: 50,000 notices x 200 profiles, gate 600 s and p95 500 ms.
load-full:
	$(MAKE) load-smoke LOAD_SCALE=1.0 LOAD_REPORT_DIR=$(LOAD_REPORT_DIR)
