#!/bin/bash
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"

# Load PORT / PS_APP / BRIDGE_PORT / EXPORT_DIR without echoing the file
if [ -f "$DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$DIR/.env"
  set +a
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "photoshop-ai-bridge: python3 not found (needed for host-bridge.py)" >&2
  exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "photoshop-ai-bridge: docker not found (needed for the psd-picker container)" >&2
  exit 1
fi

# Only stop the bridge belonging to this checkout
pkill -f "$DIR/host-bridge.py" 2>/dev/null || true
nohup python3 "$DIR/host-bridge.py" > /tmp/psd-bridge.log 2>&1 &
echo "photoshop-ai-bridge host-bridge started (pid $!)"

cd "$DIR"
docker compose up -d --build
echo "photoshop-ai-bridge (psd-picker) ready → http://localhost:${PORT:-18080}"
