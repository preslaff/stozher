#!/usr/bin/env bash
# Install a STOZHER field kit from an unpacked bundle (see tools/kit/bundle.sh). Works offline.
#   ./install.sh [--dir DIR] [--db-name NAME] [--map FILE] [--service]
# --dir      where the kit lives (default ~/stozher-kit); run again with a newer bundle to upgrade (keeps infra/.env and the database)
# --db-name  PostgreSQL database on the local socket (default stozher); never a container
# --map      the offline map (bulgaria.pmtiles) if it did not come inside the bundle
# --service  also install a systemd user service that starts the kit at login (it never starts by itself otherwise)
# It never changes the network and never sends anything anywhere. Steps that need an administrator are printed, not hidden.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DIR="$HOME/stozher-kit"; DB="stozher"; MAP=""; SERVICE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dir) DIR="$2"; shift ;; --db-name) DB="$2"; shift ;; --map) MAP="$2"; shift ;; --service) SERVICE=1 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done
say() { printf '\n== %s\n' "$*"; }
die() { echo "error: $*" >&2; exit 1; }

say "Checking the bundle"
[ -f "$HERE/SHA256SUMS" ] || die "SHA256SUMS missing: is this an unpacked bundle?"
(cd "$HERE" && sha256sum -c SHA256SUMS --quiet) || die "the bundle is damaged or was changed: copy it again"
echo "bundle $(cat "$HERE/VERSION") verified"

say "Checking this laptop"
WANT="$(cat "$HERE/VERSION")"; WANT="${WANT##*-}"
[ "$(uname -m)" = "$WANT" ] || die "the bundle is for $WANT, this machine is $(uname -m)"
PY=""; for c in python3.12 python3; do command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(sys.version_info < (3,12))' && { PY="$c"; break; }; done
[ -n "$PY" ] || die "Python 3.12 or newer is needed:  sudo apt install python3.12 python3.12-venv"
command -v psql >/dev/null || die "PostgreSQL is needed:  sudo apt install postgresql postgresql-postgis  (version 14 or newer with PostGIS)"
pg_isready -q -h /var/run/postgresql || die "PostgreSQL is not running on the local socket:  sudo systemctl start postgresql"
ls /usr/share/postgresql/*/extension/postgis.control >/dev/null 2>&1 || die "PostGIS is not installed:  sudo apt install postgresql-postgis"
command -v openssl >/dev/null || die "openssl is needed:  sudo apt install openssl"
echo "python ($PY), postgresql, postgis, openssl: ok"

say "Copying the kit to $DIR"
mkdir -p "$DIR"
(cd "$HERE/src" && tar -cf - --exclude=./infra/.env .) | tar -xf - -C "$DIR"
mkdir -p "$DIR/.kit-bin" && install -m 755 "$HERE/uv-bin/uv" "$DIR/.kit-bin/uv"
if [ -n "$MAP" ]; then
  [ -f "$MAP" ] || die "map file $MAP not found"
  mkdir -p "$DIR/web/dashboard/dist/basemap"; cp "$MAP" "$DIR/web/dashboard/dist/basemap/bulgaria.pmtiles"
fi
[ -f "$DIR/web/dashboard/dist/basemap/bulgaria.pmtiles" ] || echo "note: no offline map yet; copy it later: ./install.sh --map bulgaria.pmtiles (docs/basemap.md)"

say "Installing the Python environment (offline)"
cd "$DIR"
UV_CACHE_DIR="$HERE/uv-cache" UV_OFFLINE=1 "$DIR/.kit-bin/uv" sync --frozen --all-packages --no-dev --all-extras -q --python "$PY"
touch .venv/.stozher-bundle

say "Database '$DB' on the local PostgreSQL"
ME="$(id -un)"
psu() {  # run psql as a PostgreSQL superuser: as ourselves if we are one, else through sudo
  if [ "$(psql -d postgres -Atc "select rolsuper from pg_roles where rolname=current_user" 2>/dev/null || true)" = t ]; then psql -d postgres -v ON_ERROR_STOP=1 -qAt "$@"
  else echo "(needs the PostgreSQL administrator: sudo)" >&2; sudo -u postgres psql -v ON_ERROR_STOP=1 -qAt "$@"; fi
}
[ "$(psu -c "select 1 from pg_roles where rolname='$ME'")" = 1 ] || psu -c "CREATE ROLE \"$ME\" LOGIN"
[ "$(psu -c "select 1 from pg_database where datname='$DB'")" = 1 ] || psu -c "CREATE DATABASE \"$DB\" OWNER \"$ME\""
psu -d "$DB" -c "CREATE EXTENSION IF NOT EXISTS postgis" >/dev/null
echo "database ready (owner $ME)"

say "Settings and keys"
if [ ! -f infra/.env ]; then  # a new kit: its own database URL, never the example's (that names the developer's database)
  grep -v -E '^DATABASE_URL=' infra/.env.example >infra/.env; chmod 600 infra/.env
  echo "DATABASE_URL=postgresql+asyncpg://$ME@/$DB?host=/var/run/postgresql" >>infra/.env
fi
infra/kit/init.sh
[ -f infra/kit/kit.env ] || printf '# Gateway radio: /dev/serial/by-id/<device>, or none to run without a radio\nMESH_PORT=none\n# Optional second gateway on another LoRa band (then run stozher-kit init once for its keys)\n# MESH_PORT_2=/dev/serial/by-id/<device>\n# MESH_NAME_2=433\n' >infra/kit/kit.env

say "Command and (optional) service"
mkdir -p "$HOME/.local/bin" && ln -sf "$DIR/infra/kit/stozher-kit" "$HOME/.local/bin/stozher-kit"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "note: add \$HOME/.local/bin to your PATH to use 'stozher-kit' directly" ;; esac
if [ "$SERVICE" = 1 ]; then
  mkdir -p "$HOME/.config/systemd/user"
  cat >"$HOME/.config/systemd/user/stozher-kit.service" <<U
[Unit]
Description=STOZHER field kit
After=postgresql.service

[Service]
ExecStart=$DIR/infra/kit/stozher-kit start
ExecStop=$DIR/infra/kit/stozher-kit stop
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
U
  systemctl --user daemon-reload
  echo "service installed, not started. Start it:  systemctl --user enable --now stozher-kit"
fi

say "Check"
infra/kit/doctor.sh || true
cat <<M

Installed in $DIR
  1. Plug in the gateway radio and put its /dev/serial/by-id/... path in $DIR/infra/kit/kit.env (MESH_PORT=...).
  2. stozher-kit start        (then open the addresses it prints, from a phone on the same network)
  3. stozher-kit doctor       any time something looks wrong
  Register the nodes (same node IDs and keys as the cloud) with the operator API: docs/field-kit.md.
M
