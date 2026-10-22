#!/usr/bin/env bash
# Start the whole field kit on this laptop: API (serving the dashboard and the offline map), worker and mesh bridge.
# Needs the local PostgreSQL (no containers). Stop with Ctrl-C or `make kit-stop`. See docs/field-kit.md.
set -euo pipefail
cd "$(dirname "$0")/../.."
# A sleeping machine receives nothing (the gateway's messages are lost), so block sleep while the kit runs.
# The lock is released when the kit stops. KIT_SLEEP=allow turns this off; without systemd-inhibit the kit runs with a warning.
if [ "${KIT_SLEEP:-block}" != "allow" ] && [ -z "${STOZHER_SLEEP_BLOCKED:-}" ]; then
  if command -v systemd-inhibit >/dev/null && systemd-inhibit --what=sleep --who=STOZHER --why=check --mode=block true 2>/dev/null; then
    export STOZHER_SLEEP_BLOCKED=1
    exec systemd-inhibit --what=sleep --who="STOZHER field kit" --why="receiving witness records" --mode=block "$0" "$@"
  fi
  echo "warning: cannot block sleep on this machine (no systemd-inhibit or not allowed); if it sleeps, the kit receives nothing" >&2
fi
. infra/kit/lib.sh
ROOT="$(pwd)"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/stozher"
mkdir -p "$STATE"
ENV=infra/.env
[ -f "$ENV" ] || { echo "no $ENV: run infra/kit/init.sh first" >&2; exit 1; }
set -a; . "$ENV"; set +a
# the API forwards questions to the assistant when it is switched on (ASSISTANT=on); otherwise the endpoint answers 503
[ "${ASSISTANT:-off}" = "on" ] && export STOZHER_ASSISTANT_URL="http://127.0.0.1:${STOZHER_ASSISTANT_PORT:-8100}"
for v in STOZHER_KEK STOZHER_SYSTEM_KEY; do
  [ -n "${!v:-}" ] || { echo "$v is missing in $ENV: run infra/kit/init.sh" >&2; exit 1; }
done
[ -n "${STOZHER_ED25519_SEED:-}" ] || echo "warning: no STOZHER_ED25519_SEED, acknowledgements to nodes are off"

PORT="${STOZHER_API_PORT:-8000}"
HOST="${STOZHER_API_HOST:-0.0.0.0}"
MESH_PORT="${MESH_PORT:-}"
[ -n "$MESH_PORT" ] || { echo "set MESH_PORT to the gateway's /dev/serial/by-id/... path, or MESH_PORT=none to run without a radio" >&2; exit 1; }
# Optional second gateway, for a mesh on another LoRa band (a 433 MHz one next to 868 MHz; radios on different bands never
# hear each other). It runs its own bridge with its own keys, spool, log and health port, and names itself in /health.
MESH_PORT_2="${MESH_PORT_2:-}"
MESH_NAME_2="${MESH_NAME_2:-433}"
if [ -n "$MESH_PORT_2" ] && [ "$MESH_PORT_2" != "none" ]; then
  [ "$MESH_PORT_2" != "$MESH_PORT" ] || { echo "MESH_PORT_2 is the same device as MESH_PORT: one radio, one bridge" >&2; exit 1; }
  for v in STOZHER_SYSTEM_KEY_2 STOZHER_RESPONDER_KEY_2; do
    [ -n "${!v:-}" ] || { echo "$v is missing in $ENV: run MESH_PORT_2=... infra/kit/init.sh (the second bridge has its own keys)" >&2; exit 1; }
  done
else
  MESH_PORT_2=""
fi

DASH="$ROOT/web/dashboard/dist"
if [ ! -f "$DASH/index.html" ] || [ ! -f "$DASH/assets/maplibre-gl-worker.mjs" ]; then
  echo "building the dashboard (relative API URL)..."
  (cd web/dashboard && VITE_API_URL= pnpm build >/dev/null)
fi
[ -f "$DASH/basemap/bulgaria.pmtiles" ] || echo "warning: no offline map in the dashboard build: run tools/basemap/fetch.sh once, then rebuild (make kit-build)"

echo "migrating the database..."
(cd services/api && uv run alembic upgrade head) >/dev/null || { echo "cannot reach the local PostgreSQL (see DATABASE_URL in $ENV)" >&2; exit 1; }

echo $$ >"$STATE/kit.pid"
# Logs are appended, never overwritten: a restart must not erase what happened before it (lost records are diagnosed
# from the bridge log). logto <name> marks the start in <name>.log, first moving a log over 20 MB to <name>.log.1.
logto() {
  local log="$STATE/$1.log"
  if [ -f "$log" ] && [ "$(stat -c %s "$log")" -gt 20000000 ]; then mv -f "$log" "$log.1"; fi
  printf '\n===== kit start %s =====\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$log"
  printf '%s' "$log"
}
pids=()
# each part runs in its own process group (setsid), so stopping it also stops the processes it started (uv, sg, python)
stop() { trap - INT TERM EXIT; for p in "${pids[@]:-}"; do [ -n "$p" ] && { kill -- "-$p" 2>/dev/null || kill "$p" 2>/dev/null || true; }; done; rm -f "$STATE/kit.pid"; }
trap stop INT TERM EXIT

STOZHER_DASHBOARD_DIR="$DASH" setsid uv run uvicorn --factory stozher_api.main:app_factory --host "$HOST" --port "$PORT" >>"$(logto api)" 2>&1 &
pids+=($!)
setsid uv run python -m stozher_worker >>"$(logto worker)" 2>&1 &
pids+=($!)
# start_bridge <device> <log name> <health port> [extra bridge options]: the radio needs group dialout
start_bridge() {
  local log; log="$(logto "$2")"
  local cmd="uv run --package stozher-mesh-bridge --extra radio python -m mesh_bridge --api http://127.0.0.1:$PORT --port '$1' run --health-port $3 ${*:4}"
  if id -nG | tr ' ' '\n' | grep -qx dialout; then setsid bash -c "$cmd" >>"$log" 2>&1 & else setsid sg dialout -c "$cmd" >>"$log" 2>&1 & fi
  pids+=($!)
}
if [ "$MESH_PORT" != "none" ]; then
  start_bridge "$MESH_PORT" bridge "${BRIDGE_HEALTH_PORT:-8766}"
fi
if [ -n "$MESH_PORT_2" ]; then
  STOZHER_SYSTEM_KEY="$STOZHER_SYSTEM_KEY_2" STOZHER_RESPONDER_KEY="$STOZHER_RESPONDER_KEY_2" \
    start_bridge "$MESH_PORT_2" bridge-2 "${BRIDGE_2_HEALTH_PORT:-8767}" \
    --name "'$MESH_NAME_2'" --spool "'$STATE/mesh-spool-2.jsonl'"
fi

if [ "${ASSISTANT:-off}" = "on" ]; then
  # optional: answers questions through the API with the asker's own key; the core runs without it
  setsid uv run python -m assistant >>"$(logto assistant)" 2>&1 &
  pids+=($!)
fi

sleep 4
echo
echo "STOZHER field kit is running. Open it from this laptop or any device on the same network:"
for ip in $(hostname -I 2>/dev/null); do echo "  http://$ip:$PORT/"; done
echo "Sign in with an API key for your role. Logs: $STATE/{api,worker,bridge,bridge-2,assistant}.log"
echo "Ctrl-C to stop."
wait -n "${pids[@]}" || true
echo "a component exited; stopping the kit. See $STATE/*.log" >&2
