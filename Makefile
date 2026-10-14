.PHONY: up down field migrate test lint sim sim-hazards fw fw-upload worker api bridge

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
	uv run mypy packages/stozher_core/src services/api/src services/worker/src services/simulator/src services/mesh_bridge/src
	@if [ -f web/dashboard/package.json ]; then cd web/dashboard && pnpm lint; fi

test: lint
	uv run pytest
	@if [ -f web/dashboard/package.json ]; then cd web/dashboard && pnpm test; fi

api:
	uv run uvicorn --factory stozher_api.main:app_factory --reload

worker:
	uv run python -m stozher_worker

bridge:  # needs STOZHER_SYSTEM_KEY; MESH_PORT=fake runs with no radio
	uv run --package stozher-mesh-bridge --extra radio python -m mesh_bridge run

sim:
	uv run python -m simulator run $(S)

sim-hazards:  # needs api + worker running and STOZHER_OPERATOR_KEY
	uv run python -m simulator hazards

fw:
	pio run -d firmware/node

fw-upload:
	pio run -d firmware/node -t upload
