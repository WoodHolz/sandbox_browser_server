#!/usr/bin/env bash
# Stop only this repo's run.py (by pidfile or full path to run.py).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PIDFILE="$ROOT/logs/run.pid"

stop_pid() {
  local pid="$1"
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    sleep 1
    kill -9 "$pid" 2>/dev/null || true
    echo "stopped pid=$pid"
  fi
}

if [[ -f "$PIDFILE" ]]; then
  stop_pid "$(cat "$PIDFILE")"
  rm -f "$PIDFILE"
else
  # Fallback: match this project's run.py only (not other services' run.py)
  pkill -f "${ROOT}/run.py" && echo "pkill ${ROOT}/run.py" || echo "not running"
fi

# API memory is gone after stop; only remove this service's labeled containers.
if command -v docker >/dev/null 2>&1; then
  orphans=$(docker ps -aq --filter "label=app=sandbox-yuan" 2>/dev/null || true)
  if [[ -n "${orphans}" ]]; then
    # shellcheck disable=SC2086
    docker rm -f ${orphans} >/dev/null 2>&1 || true
    echo "removed app=sandbox-yuan containers after stop"
  fi
fi
