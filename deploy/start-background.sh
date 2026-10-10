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

# Only containers labeled by this service (never by image; never bare "sandbox-*" names).
if command -v docker >/dev/null 2>&1; then
  orphans=$(docker ps -aq --filter "label=app=sandbox-yuan" 2>/dev/null || true)
  if [[ -n "${orphans}" ]]; then
    # shellcheck disable=SC2086
    docker rm -f ${orphans} >/dev/null 2>&1 || true
    echo "removed leftover app=sandbox-yuan containers before start"
  fi
fi

nohup "$ROOT/.venv/bin/python" "$ROOT/run.py" >>"$LOG" 2>&1 &
echo $! >"$PIDFILE"
echo "started pid=$(cat "$PIDFILE") HOST=$HOST PORT=$PORT log=$LOG"
