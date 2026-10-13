.PHONY: up down field migrate test lint sim fw fw-upload

COMPOSE := docker compose -f infra/compose.yml
S ?= coastal

up:
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

field:
	$(COMPOSE) -f infra/compose.field.yml up -d

migrate:
	cd services/api && uv run alembic upgrade head

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy packages/stozher_core/src
	@if [ -f web/dashboard/package.json ]; then cd web/dashboard && pnpm lint; fi

test: lint
	uv run pytest
	@if [ -f web/dashboard/package.json ]; then cd web/dashboard && pnpm test --if-present; fi

sim:
	uv run python -m simulator run $(S)

fw:
	pio run -d firmware/node

fw-upload:
	pio run -d firmware/node -t upload
