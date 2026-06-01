#!/bin/zsh

set -euo pipefail

PRIMARY_DIR="/Users/hidvegi/Library/CloudStorage/OneDrive-Személyes/ROAR/LionCare/CODEX/LionCare report"
BACKUP_DIR="/Users/hidvegi/Documents/New project"
LOCK_DIR="/tmp/lioncare-project-sync.lock"
STAMP_FILE="/tmp/lioncare-project-sync.last"
MIN_INTERVAL_SECONDS=2

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  exit 0
fi

cleanup() {
  rmdir "$LOCK_DIR" 2>/dev/null || true
}
trap cleanup EXIT

now_epoch=$(date +%s)
if [[ -f "$STAMP_FILE" ]]; then
  last_epoch=$(cat "$STAMP_FILE" 2>/dev/null || echo 0)
  if [[ $((now_epoch - last_epoch)) -lt $MIN_INTERVAL_SECONDS ]]; then
    exit 0
  fi
fi

mkdir -p "$BACKUP_DIR"

# Two-way sync using "newer wins".
# Deletes are intentionally not mirrored automatically to avoid accidental data loss.
rsync -a --update \
  --exclude ".DS_Store" \
  --exclude ".git/" \
  "$PRIMARY_DIR/" "$BACKUP_DIR/"

rsync -a --update \
  --exclude ".DS_Store" \
  --exclude ".git/" \
  "$BACKUP_DIR/" "$PRIMARY_DIR/"

printf '%s\n' "$now_epoch" > "$STAMP_FILE"
