#!/usr/bin/env bash
trap 'rm -f /tmp/doge_*.db /tmp/doge_*.db-wal /tmp/doge_*.db-shm' EXIT   # temp DB copies hold message text
# Penny read-only fleet scan. Writes JSON next to this script. Never touches agent files (copies sqlite to /tmp).
set -e; cd "$(dirname "$0")"
python3 fleet_scan.py; python3 routine_runs.py >/dev/null; python3 routine_quiet.py; python3 routine_cost.py >/dev/null; python3 routine_engagement.py >/dev/null; python3 cli_usage.py; python3 daily_usage.py; python3 always_on.py
