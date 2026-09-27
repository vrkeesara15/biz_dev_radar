# BidRadar developer entrypoints (GNU Make 3.81 compatible; no GNU-4-only syntax).
SHELL := /bin/bash
COMPOSE := docker compose -f infra/docker-compose.yml
UV := uv
BACKEND := backend


.PHONY: help lint format test eval up down db-reset seed migrate smoke acceptance isolation

help:
	@echo "targets: lint format test eval up down db-reset seed migrate smoke acceptance isolation"

lint:
	cd $(BACKEND) && $(UV) run ruff format --check app tests migrations
	cd $(BACKEND) && $(UV) run ruff check app tests migrations
	cd $(BACKEND) && $(UV) run mypy

format:
	cd $(BACKEND) && $(UV) run ruff format app tests migrations
	cd $(BACKEND) && $(UV) run ruff check --fix app tests migrations

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

migrate:
	cd $(BACKEND) && $(UV) run alembic upgrade head

seed:
	@echo "seed: seed script lands in M0-09"

isolation:
	cd $(BACKEND) && $(UV) run pytest tests/isolation -p no:cacheprovider

smoke:
	@echo "smoke: no live adapters yet (M2+); nothing to run"

acceptance:
	@echo "acceptance: placeholder until SPEC section 12 boxes are automated"
