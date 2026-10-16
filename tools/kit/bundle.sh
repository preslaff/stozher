#!/usr/bin/env bash
# Build an offline field-kit bundle: source (committed files only), the built dashboard, the Python dependencies (a uv cache),
# the uv binary and the installer. The offline map travels with it only with --with-map (260 MB), or separately.
#   tools/kit/bundle.sh [--with-map] [--out DIR]       result: DIR/stozher-kit-<version>.tar.zst (+ .sha256)
# Secrets are never included. The target must have the same CPU architecture and Python 3.12.
set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT="$(pwd)"
WITH_MAP=0; OUT="$ROOT/kit-dist"
while [ $# -gt 0 ]; do
  case "$1" in --with-map) WITH_MAP=1 ;; --out) OUT="$2"; shift ;; *) echo "unknown option $1" >&2; exit 2 ;; esac
  shift
done
command -v zstd >/dev/null || { echo "zstd is needed" >&2; exit 1; }
[ -z "$(git status --porcelain --untracked-files=no)" ] || echo "warning: uncommitted changes are NOT in the bundle (it holds HEAD)" >&2
VERSION="$(git describe --always --dirty=-modified)-$(uname -m)"
NAME="stozher-kit-$VERSION"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
STAGE="$WORK/$NAME"; mkdir -p "$STAGE" "$OUT"

echo "source..."
git archive HEAD --prefix=src/ | tar -x -C "$STAGE"
rm -rf "$STAGE/src/firmware" "$STAGE/src/spikes" "$STAGE/src/tests/vectors/__pycache__"

echo "dashboard (relative API URL)..."
(cd web/dashboard && VITE_API_URL= pnpm build >/dev/null)
mkdir -p "$STAGE/src/web/dashboard"
cp -r web/dashboard/dist "$STAGE/src/web/dashboard/dist"
if [ "$WITH_MAP" = 1 ]; then echo "(map included)"; else rm -f "$STAGE/src/web/dashboard/dist/basemap/bulgaria.pmtiles"; fi

echo "python dependencies (uv cache, no dev tools)..."
export UV_CACHE_DIR="$STAGE/uv-cache" UV_PROJECT_ENVIRONMENT="$WORK/venv"
(cd "$STAGE/src" && uv sync --frozen --all-packages --no-dev --all-extras -q)
mkdir -p "$STAGE/uv-bin" && cp "$(command -v uv)" "$STAGE/uv-bin/uv"

cp tools/kit/install.sh "$STAGE/install.sh"; chmod +x "$STAGE/install.sh"
echo "$VERSION" >"$STAGE/VERSION"
(cd "$STAGE" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum >SHA256SUMS)

echo "packing..."
tar -C "$WORK" -cf - "$NAME" | zstd -T0 -3 -q -o "$OUT/$NAME.tar.zst"
(cd "$OUT" && sha256sum "$NAME.tar.zst" >"$NAME.tar.zst.sha256")
du -h "$OUT/$NAME.tar.zst" | cut -f1 | xargs echo "built $OUT/$NAME.tar.zst"
