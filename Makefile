.PHONY: up down field kit kit-init kit-build kit-stop migrate test lint sim sim-hazards fw fw-test fw-vectors fw-upload worker api bridge

S ?= coastal

# The database is the local PostgreSQL 16 (no containers). The field kit starts the API, worker and mesh bridge on this laptop.
up down field:
	@echo "No containers: the database is the local PostgreSQL. Use 'make kit' (docs/field-kit.md)."

kit-init:  # first run: fills missing secrets in infra/.env, creates the bridge and relay keys
	infra/kit/init.sh

kit-build:  # the dashboard with a relative API URL, served by the API itself
	cd web/dashboard && VITE_API_URL= pnpm build

kit:  # MESH_PORT=/dev/serial/by-id/<gateway> (or none); serves http://<this laptop>:8000/
	infra/kit/run.sh

kit-stop:
	@if [ -f $${XDG_STATE_HOME:-$$HOME/.local/state}/stozher/kit.pid ]; then kill $$(cat $${XDG_STATE_HOME:-$$HOME/.local/state}/stozher/kit.pid) && echo "stopping the kit"; else echo "the kit is not running"; fi

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

fw-vectors:  # regenerate the C++ test header from tests/vectors
	python3 firmware/node/tools/gen_vectors_h.py
	uv run python firmware/node/tools/gen_ubx_vectors.py

fw-test: fw-vectors  # codec and detection tests on the host, no hardware needed
	pio test -d firmware/node -e native

fw-upload:
	pio run -d firmware/node -t upload
