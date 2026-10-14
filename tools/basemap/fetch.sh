#!/usr/bin/env bash
# Prepare the offline base map for the dashboard: a Bulgaria extract of the Protomaps basemap (OpenStreetMap data,
# ODbL), plus the fonts and icons its style needs. Run once with internet; afterwards the map needs none.
# Usage: tools/basemap/fetch.sh [YYYYMMDD]   (default: yesterday's build; see https://maps.protomaps.com/builds/)
set -euo pipefail
cd "$(dirname "$0")/../.."
BUILD="${1:-$(date -u -d yesterday +%Y%m%d)}"
BBOX="${BBOX:-22.3,41.2,28.7,44.3}"   # Bulgaria with a margin
MAXZOOM="${MAXZOOM:-14}"
PMTILES_VER=1.31.2
OUT=data/basemap
PUB=web/dashboard/public/basemap
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$OUT" "$PUB/sprites"

if [ ! -f "$OUT/bulgaria.pmtiles" ]; then
  curl -fsSL -o "$work/p.tgz" "https://github.com/protomaps/go-pmtiles/releases/download/v${PMTILES_VER}/go-pmtiles_${PMTILES_VER}_Linux_x86_64.tar.gz"
  tar -xzf "$work/p.tgz" -C "$work"
  "$work/pmtiles" extract "https://build.protomaps.com/${BUILD}.pmtiles" "$OUT/bulgaria.pmtiles" --bbox="$BBOX" --maxzoom="$MAXZOOM"
fi

if [ ! -d "$PUB/fonts" ]; then
  curl -fsSL -o "$work/a.tgz" https://codeload.github.com/protomaps/basemaps-assets/tar.gz/refs/heads/main
  mkdir "$work/assets" && tar -xzf "$work/a.tgz" -C "$work/assets" --strip-components=1
  mkdir -p "$PUB/fonts"
  for f in "Noto Sans Regular" "Noto Sans Medium" "Noto Sans Italic"; do cp -r "$work/assets/fonts/$f" "$PUB/fonts/"; done
  cp "$work/assets/fonts/OFL.txt" "$PUB/fonts/"
  mkdir -p "$PUB/sprites/v4" && cp "$work"/assets/sprites/v4/dark* "$PUB/sprites/v4/"
fi

ln -sf "$(pwd)/$OUT/bulgaria.pmtiles" "$PUB/bulgaria.pmtiles"
echo "Base map ready: $(du -h "$OUT/bulgaria.pmtiles" | cut -f1) in $OUT, served from $PUB"
