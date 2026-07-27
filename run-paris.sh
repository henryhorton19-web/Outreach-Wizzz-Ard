#!/usr/bin/env bash
# macOS / Linux launcher for Paris Outreach (Desktop App + Auto Git Sync)
cd "$(dirname "$0")" || exit 1

if [ -f "./venv/bin/activate" ]; then
    source "./venv/bin/activate"
elif [ -f "./.venv/bin/activate" ]; then
    source "./.venv/bin/activate"
fi

python3 run_synced.py "$@"
