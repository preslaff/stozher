#!/usr/bin/env bash
# First-run setup of a field kit: fills only the MISSING secrets in infra/.env. Never prints a secret and never overwrites one.
# A new encryption key (STOZHER_KEK) would orphan every node key already stored, so it is created only when there is none.
set -euo pipefail
cd "$(dirname "$0")/../.."
. infra/kit/lib.sh
ENV=infra/.env
[ -f "$ENV" ] || { cp infra/.env.example "$ENV"; chmod 600 "$ENV"; echo "created $ENV from the example"; }
has() { grep -q -E "^$1=.+" "$ENV"; }
add() { printf '%s=%s\n' "$1" "$2" >>"$ENV"; echo "added $1 to $ENV"; }

has STOZHER_KEK || add STOZHER_KEK "$(openssl rand -hex 32)"
has STOZHER_ED25519_SEED || add STOZHER_ED25519_SEED "$(uv run python -m stozher_api.cli gen-signing-key | sed -n 's/^STOZHER_ED25519_SEED=//p')"

set -a; . "$ENV"; set +a
(cd services/api && uv run alembic upgrade head) >/dev/null
has STOZHER_SYSTEM_KEY || add STOZHER_SYSTEM_KEY "$(uv run python -m stozher_api.cli create-key --role system --label 'field kit mesh bridge')"
has STOZHER_RESPONDER_KEY || add STOZHER_RESPONDER_KEY "$(uv run python -m stozher_api.cli create-key --role responder --label 'field kit alert relay')"
# a second gateway (MESH_PORT_2, another LoRa band) gets its own keys: one key per machine and purpose (docs/access.md)
if [ -n "${MESH_PORT_2:-}" ] && [ "${MESH_PORT_2}" != none ]; then
  has STOZHER_SYSTEM_KEY_2 || add STOZHER_SYSTEM_KEY_2 "$(uv run python -m stozher_api.cli create-key --role system --label "field kit mesh bridge ${MESH_NAME_2:-433}")"
  has STOZHER_RESPONDER_KEY_2 || add STOZHER_RESPONDER_KEY_2 "$(uv run python -m stozher_api.cli create-key --role responder --label "field kit alert relay ${MESH_NAME_2:-433}")"
fi
echo "ready. Next: MESH_PORT=/dev/serial/by-id/<gateway> make kit   (MESH_PORT=none starts without a radio; MESH_PORT_2 adds a gateway on another band)"
