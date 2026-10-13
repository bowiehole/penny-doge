#!/usr/bin/env bash
# Penny weekly no-AI fleet-usage scan. Zero model tokens: bash + Python only.
#   weekly_scan.sh            full run: scan, summarize, POST the wake only if over threshold and new/worse
#   weekly_scan.sh --dry-run  everything except the POST (still writes history + summary)
#   weekly_scan.sh --probe    POST {"probe": true} to the webhook and print only the HTTP status
trap 'rm -f /tmp/doge_*.db /tmp/doge_*.db-wal /tmp/doge_*.db-shm' EXIT   # temp DB copies hold message text
# Config: config/thresholds.json (generic). Owner data: local/owner.json, ledger/, .webhook.json (chmod 600).
set -u
# cron's PATH often omits ~/.local/bin (where grok lives); keep interactive shells unchanged
export PATH="${HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"
DOGE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; cd "$DOGE"
cfg() { python3 -c "import json,sys;c=json.load(open('config/thresholds.json'));print(c['paths'][sys.argv[1]])" "$1"; }
[ -f config/thresholds.json ] || { echo "config/thresholds.json missing: run install.sh"; exit 1; }
case "$(cfg cron_log)" in /*) LOG="$(cfg cron_log)";; *) LOG="$DOGE/$(cfg cron_log)";; esac
RUNLOG="$DOGE/$(cfg run_all_log)"; mkdir -p "$(dirname "$RUNLOG")"
MODE=run; for a in "$@"; do case "$a" in --dry-run) MODE=dry-run;; --probe) MODE=probe;; esac; done

if [ "$MODE" = probe ]; then python3 wake.py --probe; exit 0; fi

# log to cron.log; also echo to the terminal unless stdout already is cron.log (the cron case)
OUT="$(readlink -f /proc/$$/fd/1 2>/dev/null || true)"
log() { local l="$(date '+%Y-%m-%d %H:%M:%S %Z') Penny $*"; echo "$l" >>"$LOG"; [ "$OUT" = "$(readlink -f "$LOG")" ] || echo "$l"; }

START=$(date +%s.%N); export DOGE_START=$START
log "start mode=$MODE"
if ! bash ./run_all.sh >"$RUNLOG" 2>&1; then
  log "run_all.sh failed (see $(cfg run_all_log)); continuing with the last JSON on disk"
fi
RES="$(python3 summarize.py 2>>"$RUNLOG")" || { log "summarize failed (see $(cfg run_all_log))"; log "end wall_s=$(python3 -c "import time;print(round(time.time()-$START,1))") posted=no"; exit 1; }
if [ "$MODE" = dry-run ]; then WAKE="$(python3 wake.py --post --dry-run)"; else WAKE="$(python3 wake.py --post)"; fi
WALL=$(python3 -c "import time;print(round(time.time()-$START,1))")
log "summary $RES"
log "end wall_s=$WALL $WAKE"

