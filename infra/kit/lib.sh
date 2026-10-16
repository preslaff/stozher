# Sourced by the kit scripts. In a bundle install (.venv/.stozher-bundle exists) nothing may touch the network:
# uv uses the environment the installer built and never re-resolves.
KIT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [ -f "$KIT_ROOT/.venv/.stozher-bundle" ]; then
  export UV_NO_SYNC=1 UV_OFFLINE=1
  export PATH="$KIT_ROOT/.kit-bin:$PATH"  # the uv binary shipped in the bundle
fi
