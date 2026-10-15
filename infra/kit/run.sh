#!/usr/bin/env bash
# Start the whole field kit on this laptop: API (serving the dashboard and the offline map), worker and mesh bridge.
# Needs the local PostgreSQL (no containers). Stop with Ctrl-C or `make kit-stop`. See docs/field-kit.md.
set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT="$(pwd)"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/stozher"
mkdir -p "$STATE"
ENV=infra/.env
[ -f "$ENV" ] || { echo "no $ENV: run infra/kit/init.sh first" >&2; exit 1; }
set -a; . "$ENV"; set +a
for v in STOZHER_KEK STOZHER_SYSTEM_KEY; do
  [ -n "${!v:-}" ] || { echo "$v is missing in $ENV: run infra/kit/init.sh" >&2; exit 1; }
done
[ -n "${STOZHER_ED25519_SEED:-}" ] || echo "warning: no STOZHER_ED25519_SEED, acknowledgements to nodes are off"

PORT="${STOZHER_API_PORT:-8000}"
HOST="${STOZHER_API_HOST:-0.0.0.0}"
MESH_PORT="${MESH_PORT:-}"
[ -n "$MESH_PORT" ] || { echo "set MESH_PORT to the gateway's /dev/serial/by-id/... path, or MESH_PORT=none to run without a radio" >&2; exit 1; }

DASH="$ROOT/web/dashboard/dist"
if [ ! -f "$DASH/index.html" ] || [ ! -f "$DASH/assets/maplibre-gl-worker.mjs" ]; then
  echo "building the dashboard (relative API URL)..."
  (cd web/dashboard && VITE_API_URL= pnpm build >/dev/null)
fi
[ -f "$DASH/basemap/bulgaria.pmtiles" ] || echo "warning: no offline map in the dashboard build: run tools/basemap/fetch.sh once, then rebuild (make kit-build)"

echo "migrating the database..."
(cd services/api && uv run alembic upgrade head) >/dev/null || { echo "cannot reach the local PostgreSQL (see DATABASE_URL in $ENV)" >&2; exit 1; }

echo $$ >"$STATE/kit.pid"
pids=()
stop() { trap - INT TERM EXIT; for p in "${pids[@]:-}"; do [ -n "$p" ] && kill "$p" 2>/dev/null || true; done; rm -f "$STATE/kit.pid"; }
trap stop INT TERM EXIT

STOZHER_DASHBOARD_DIR="$DASH" uv run uvicorn --factory stozher_api.main:app_factory --host "$HOST" --port "$PORT" >"$STATE/api.log" 2>&1 &
pids+=($!)
uv run python -m stozher_worker >"$STATE/worker.log" 2>&1 &
pids+=($!)
if [ "$MESH_PORT" != "none" ]; then
  cmd="uv run --package stozher-mesh-bridge --extra radio python -m mesh_bridge --api http://127.0.0.1:$PORT --port '$MESH_PORT' run --health-port ${BRIDGE_HEALTH_PORT:-8766}"
  if id -nG | tr ' ' '\n' | grep -qx dialout; then bash -c "$cmd" >"$STATE/bridge.log" 2>&1 & else sg dialout -c "$cmd" >"$STATE/bridge.log" 2>&1 & fi
  pids+=($!)
fi

sleep 4
echo
echo "STOZHER field kit is running. Open it from this laptop or any device on the same network:"
for ip in $(hostname -I 2>/dev/null); do echo "  http://$ip:$PORT/"; done
echo "Sign in with an API key for your role. Logs: $STATE/{api,worker,bridge}.log"
echo "Ctrl-C to stop."
wait -n "${pids[@]}" || true
echo "a component exited; stopping the kit. See $STATE/*.log" >&2
