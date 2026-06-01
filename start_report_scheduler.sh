#!/bin/zsh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATE_DIR="$HOME/.codex/lioncare-scheduler"
DAEMON_SCRIPT="$SCRIPT_DIR/report_scheduler_daemon.sh"
PID_FILE="$STATE_DIR/scheduler.pid"
LOG_FILE="$STATE_DIR/scheduler.log"

if [[ -f "$PID_FILE" ]]; then
  existing_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "$existing_pid" ]] && kill -0 "$existing_pid" 2>/dev/null; then
    echo "Scheduler already running with PID $existing_pid"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

mkdir -p "$STATE_DIR"
touch "$LOG_FILE"

nohup "$DAEMON_SCRIPT" >> "$LOG_FILE" 2>&1 &
shell_pid=$!
sleep 1

daemon_pid="$(pgrep -n -f "$DAEMON_SCRIPT" || true)"
if [[ -z "$daemon_pid" ]]; then
  daemon_pid="$shell_pid"
fi

echo "$daemon_pid" > "$PID_FILE"
echo "Started scheduler with PID $daemon_pid"
