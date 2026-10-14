#!/usr/bin/env bash
# Put the STOZHER pieces into a Meshtastic firmware checkout (use a git worktree on its own branch).
#   firmware/tbeam/apply.sh /path/to/Meshtastic-firmware-stozher
# Then build:  pio run -e tbeam-stozher    (in that checkout)
set -euo pipefail
DEST=$(cd "${1:?usage: apply.sh <meshtastic checkout>}" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)

mkdir -p "$DEST/lib"
ln -sfn "$ROOT/firmware/node/lib/stozher" "$DEST/lib/stozher"
ln -sfn "$ROOT/firmware/node/lib/tweetnacl" "$DEST/lib/tweetnacl"
cp "$HERE"/src/*.h "$HERE"/src/*.cpp "$DEST/src/gps/"
EXCLUDE=$(git -C "$DEST" rev-parse --git-path info/exclude)
grep -qx 'lib/' "$EXCLUDE" 2>/dev/null || echo 'lib/' >> "$EXCLUDE"

for patch in "$HERE"/patches/*.patch; do
  if git -C "$DEST" apply --reverse --check "$patch" 2>/dev/null; then
    echo "already applied: $(basename "$patch")"
  else
    git -C "$DEST" apply "$patch"
    echo "applied: $(basename "$patch")"
  fi
done
