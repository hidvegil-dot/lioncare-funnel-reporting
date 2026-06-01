#!/bin/zsh

set -euo pipefail

STATE_DIR="$HOME/.codex/lioncare-scheduler"
PID_FILE="$STATE_DIR/scheduler.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "Scheduler is not running"
  exit 0
fi

pid="$(cat "$PID_FILE" 2>/dev/null || true)"
if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
  kill "$pid"
  echo "Stopped scheduler PID $pid"
else
  echo "Scheduler PID file was stale"
fi

rm -f "$PID_FILE"
