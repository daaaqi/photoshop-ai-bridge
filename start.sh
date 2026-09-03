#!/bin/bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

if [ -f "$DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$DIR/.env"
  set +a
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Install Python 3, then retry." >&2
  exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "docker not found. Install Docker Desktop, then retry." >&2
  exit 1
fi

if pgrep -f "$DIR/host-bridge.py" >/dev/null 2>&1; then
  pkill -f "$DIR/host-bridge.py" || true
  sleep 0.3
fi

nohup python3 "$DIR/host-bridge.py" > /tmp/psd-bridge.log 2>&1 &
echo "host-bridge started (pid $!) — logs: /tmp/psd-bridge.log"

docker compose up -d --build
echo "photoshop-ai-bridge ready → http://127.0.0.1:${PORT:-18080}"
