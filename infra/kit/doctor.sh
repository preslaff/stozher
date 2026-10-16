#!/usr/bin/env bash
# Preflight and status of a field kit: what works, what is missing and how to fix it. Prints no secret.
# Exit status 1 if anything essential fails. Run it before a deployment, and when something does not start.
set -uo pipefail
cd "$(dirname "$0")/../.."
. infra/kit/lib.sh
ENV=infra/.env
fails=0
ok()   { printf '  \033[32mok\033[0m    %s\n' "$*"; }
warn() { printf '  \033[33mwarn\033[0m  %s\n' "$*"; }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; fails=$((fails+1)); }

echo "Secrets and settings ($ENV)"
if [ -f "$ENV" ]; then
  ok "$ENV exists"
  [ "$(stat -c %a "$ENV")" = 600 ] && ok "permissions 600" || warn "permissions are not 600: chmod 600 $ENV"
  set -a; . "$ENV"; set +a
  for v in STOZHER_KEK STOZHER_SYSTEM_KEY; do [ -n "${!v:-}" ] && ok "$v set" || bad "$v missing: run infra/kit/init.sh"; done
  for v in STOZHER_ED25519_SEED STOZHER_RESPONDER_KEY; do [ -n "${!v:-}" ] && ok "$v set" || warn "$v missing (acks / alert relay off): run infra/kit/init.sh"; done
  if [ -n "${STOZHER_CLOUD_URL:-}" ]; then [ -n "${STOZHER_CLOUD_KEY:-}" ] && ok "cloud sync configured" || bad "STOZHER_CLOUD_URL is set but STOZHER_CLOUD_KEY is not"; else warn "cloud sync not configured (optional)"; fi
else
  bad "$ENV missing: run infra/kit/init.sh"
fi

echo "Software"
command -v uv >/dev/null && ok "uv $(uv --version | cut -d' ' -f2)" || bad "uv not found"
if [ -f .venv/.stozher-bundle ]; then ok "Python environment from the bundle"; elif [ -d .venv ]; then ok "Python environment (.venv)"; else bad "no .venv: run uv sync, or install from a bundle"; fi
command -v openssl >/dev/null && ok "openssl" || warn "openssl missing (init.sh needs it for the first run)"

echo "Database"
URL="${DATABASE_URL:-postgresql+asyncpg://$USER@/stozher?host=/var/run/postgresql}"
DB="$(printf '%s' "$URL" | sed -E 's|^[^/]*//[^/]*/([^?]*).*|\1|')"
if command -v psql >/dev/null && psql -d "$DB" -Atc 'select 1' >/dev/null 2>&1; then
  ok "PostgreSQL reachable, database '$DB'"
  psql -d "$DB" -Atc "select extname from pg_extension where extname='postgis'" 2>/dev/null | grep -q postgis && ok "PostGIS enabled" || bad "PostGIS missing in '$DB' (installer step, needs a superuser)"
  if [ -d services/api ] && (cd services/api && uv run alembic current 2>/dev/null | grep -q '(head)'); then ok "migrations at head"; else warn "migrations not at head: they run when the kit starts"; fi
else
  bad "cannot reach database '$DB' on the local PostgreSQL (is it running? is DATABASE_URL right?)"
fi

echo "Dashboard and map"
D=web/dashboard/dist
[ -f "$D/index.html" ] && ok "dashboard build present" || bad "no dashboard build at $D"
[ -f "$D/assets/maplibre-gl-worker.mjs" ] && ok "map worker present" || bad "map worker file missing in the build (rebuild: make kit-build)"
if [ -f "$D/basemap/bulgaria.pmtiles" ]; then ok "offline map ($(du -h "$D/basemap/bulgaria.pmtiles" | cut -f1))"; else warn "no offline map: the dashboard works but the background is blank offline"; fi

echo "Radio"
if id -nG | tr ' ' '\n' | grep -qx dialout; then ok "user is in group dialout"; else warn "user is not in group dialout: sudo usermod -aG dialout $USER, then log in again"; fi
ports=(/dev/serial/by-id/*)
if [ -e "${ports[0]}" ]; then for p in "${ports[@]}"; do ok "serial device: ${p##*/}"; done; else warn "no serial device plugged in (MESH_PORT=none runs without a radio)"; fi
if [ -n "${MESH_PORT:-}" ] && [ "$MESH_PORT" != none ]; then [ -e "$MESH_PORT" ] && ok "MESH_PORT exists" || bad "MESH_PORT $MESH_PORT is not there"; fi

echo "Network"
PORT="${STOZHER_API_PORT:-8000}"
H="$(curl -s -m 3 "http://127.0.0.1:$PORT/health" 2>/dev/null || true)"
if [ -n "$H" ]; then
  ok "kit is running on port $PORT"
  printf '%s' "$H" | python3 -c 'import json,sys; m=json.load(sys.stdin).get("modules",{}); print("        modules: "+", ".join(k+"="+str(v) for k,v in m.items()))' 2>/dev/null || true
elif ss -ltn 2>/dev/null | grep -q ":$PORT "; then bad "port $PORT is taken by something that is not the kit"
else ok "port $PORT is free (kit not running)"; fi
for ip in $(hostname -I 2>/dev/null); do echo "        phones use: http://$ip:$PORT/"; done

echo
if [ "$fails" -eq 0 ]; then echo "Ready."; else echo "$fails problem(s) to fix."; fi
exit $((fails > 0))
