#!/bin/zsh

set -euo pipefail

PRIMARY_DIR="/Users/hidvegi/Library/CloudStorage/OneDrive-Személyes/ROAR/LionCare/CODEX/LionCare report"

export PYTHONPYCACHEPREFIX="$PRIMARY_DIR/.pycache"
mkdir -p "$PYTHONPYCACHEPREFIX"

cd "$PRIMARY_DIR"
python3 main.py --report-type weekly_compare "$@"
