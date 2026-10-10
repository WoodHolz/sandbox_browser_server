#!/usr/bin/env bash
# Start sandbox-browser-server in background (distinct from other run.py services).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p logs
PIDFILE="$ROOT/logs/run.pid"
LOG="$ROOT/logs/run.log"

if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "already running pid=$(cat "$PIDFILE")"
  exit 0
fi

# shellcheck source=/dev/null
source "$ROOT/.venv/bin/activate"
export HOST="${HOST:-127.0.0.1}"
export PORT="${PORT:-8090}"
nohup "$ROOT/.venv/bin/python" "$ROOT/run.py" >>"$LOG" 2>&1 &
echo $! >"$PIDFILE"
echo "started pid=$(cat "$PIDFILE") HOST=$HOST PORT=$PORT log=$LOG"
