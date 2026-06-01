#!/bin/zsh

set -euo pipefail

SYNC_SCRIPT="/Users/hidvegi/Documents/New project/sync_both_ways.sh"

while true; do
  /bin/zsh "$SYNC_SCRIPT" >> /tmp/lioncare-project-sync.log 2>&1 || true
  sleep 3
done
