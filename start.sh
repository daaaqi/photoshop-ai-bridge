#!/bin/bash
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"

# Start host-bridge (macOS native, talks to Photoshop via osascript)
pkill -f host-bridge.py 2>/dev/null || true
nohup python3 "$DIR/host-bridge.py" > /tmp/psd-bridge.log 2>&1 &
echo "host-bridge started (pid $!)"

# Start Docker container
cd "$DIR"
docker compose up -d --build
echo "psd-picker ready → http://localhost:${PORT:-18080}"
