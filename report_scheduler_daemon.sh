#!/bin/zsh

set -euo pipefail

BASE_DIR="/Users/hidvegi/Documents/New project"
STATE_DIR="$HOME/.codex/lioncare-scheduler"
DAILY_SCRIPT="$BASE_DIR/run_report_and_sync.sh"
WEEKLY_SCRIPT="$BASE_DIR/run_weekly_report_and_sync.sh"
STATE_FILE="$STATE_DIR/state.env"
LOG_FILE="$STATE_DIR/scheduler.log"

mkdir -p "$BASE_DIR" "$STATE_DIR"
touch "$LOG_FILE"

save_state() {
  cat > "$STATE_FILE" <<EOF
LAST_DAILY=${LAST_DAILY:-}
LAST_WEEKLY=${LAST_WEEKLY:-}
EOF
}

load_state() {
  LAST_DAILY=""
  LAST_WEEKLY=""
  if [[ -f "$STATE_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$STATE_FILE"
  fi
}

run_and_log() {
  local label="$1"
  local script_path="$2"
  echo "$(date '+%Y-%m-%d %H:%M:%S %Z') starting $label" >> "$LOG_FILE"
  if "$script_path" >> "$LOG_FILE" 2>&1; then
    echo "$(date '+%Y-%m-%d %H:%M:%S %Z') finished $label" >> "$LOG_FILE"
  else
    echo "$(date '+%Y-%m-%d %H:%M:%S %Z') failed $label" >> "$LOG_FILE"
  fi
}

while true; do
  load_state
  today="$(date '+%Y-%m-%d')"
  weekday="$(date '+%u')"
  hhmm="$(date '+%H%M')"

  if [[ "$hhmm" -ge 0700 ]]; then
    if [[ "${LAST_DAILY:-}" != "$today" ]]; then
      run_and_log "daily_report" "$DAILY_SCRIPT"
      LAST_DAILY="$today"
      save_state
    fi

    if [[ "$weekday" == "6" && "${LAST_WEEKLY:-}" != "$today" ]]; then
      run_and_log "weekly_report" "$WEEKLY_SCRIPT"
      LAST_WEEKLY="$today"
      save_state
    fi
  fi

  sleep 60
done
