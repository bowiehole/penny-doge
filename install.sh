#!/usr/bin/env bash
# Penny DOGE installer: idempotent. Safe to run any number of times; a second run changes nothing.
# Creates folders, copies default thresholds and the owner template if missing, installs the weekly
# cron line only if it is not already there (all other crontab lines are kept exactly), then prints the next step.
# Also installs/starts cron itself when it is missing (no systemd needed) and exits non-zero, loudly, if it can't.
set -eu
DOGE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; cd "$DOGE"
mkdir -p history ledger summary local logs config
[ -f config/thresholds.json ] || { cp config/thresholds.default.json config/thresholds.json; echo "created config/thresholds.json from defaults"; }
[ -f local/owner.json ] || { cp config/owner.example.json local/owner.json; echo "created local/owner.json (edit: handled items, exclusions, repo->bot map)"; }
for f in weekly_scan.sh install.sh run_all.sh new_bot_check.py setup_webhook.sh health_check.py; do [ -x "$f" ] || chmod +x "$f"; done
for f in .webhook.json .webhook.json.bak; do [ -f "$f" ] && [ "$(stat -c %a "$f")" != 600 ] && chmod 600 "$f"; done; true

# --- cron itself: make sure the crontab command exists and the cron daemon is running ---
# Boxes that run under tini (no systemd) can lose the cron package or the daemon after an update or
# restart, and nothing brings them back. Install/start it here; if that fails, stop loudly.
die() {
  echo "" >&2
  echo "PENNY DOGE: cron is NOT running; scheduled scans will not fire." >&2
  echo "Problem: $1" >&2
  echo "Fix: ${2:-run as a user with sudo: sudo apt-get update && sudo apt-get install -y cron && sudo cron   then re-run: bash $DOGE/install.sh}" >&2
  exit 1
}
as_root() {   # run a command as root: directly if root, else via passwordless sudo; fails if neither
  if [ "$(id -u)" = 0 ]; then "$@"
  elif command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then sudo -n "$@"
  else return 1; fi
}
cron_running() {   # pgrep when present, else scan /proc (procps may be missing too)
  if command -v pgrep >/dev/null 2>&1; then pgrep -x cron >/dev/null 2>&1 || pgrep -x crond >/dev/null 2>&1; return; fi
  grep -qxE 'cron|crond' /proc/[0-9]*/comm 2>/dev/null
}
if ! command -v crontab >/dev/null 2>&1 || ! { command -v cron >/dev/null 2>&1 || [ -x /usr/sbin/cron ]; }; then
  echo "cron: not installed; installing (apt-get install cron)"
  command -v apt-get >/dev/null 2>&1 || die "the crontab command is missing and apt-get is not available to install it" "install the cron package with your system's package manager, start the cron daemon, then re-run: bash $DOGE/install.sh"
  as_root env DEBIAN_FRONTEND=noninteractive apt-get update -qq >/dev/null 2>&1 \
    && as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq cron >/dev/null 2>&1 \
    || die "could not install cron (needs root or passwordless sudo, and network access for apt-get)"
  command -v crontab >/dev/null 2>&1 || die "cron was installed but the crontab command still isn't on PATH"
fi
if cron_running; then
  echo "cron: daemon running"
else
  echo "cron: daemon not running; starting it (no systemd needed)"
  as_root service cron start >/dev/null 2>&1 || true
  cron_running || as_root cron >/dev/null 2>&1 || as_root /usr/sbin/cron >/dev/null 2>&1 || true
  sleep 1
  cron_running || die "the cron daemon could not be started (needs root or passwordless sudo)" "start it as root: sudo cron   (or: sudo service cron start), then re-run: bash $DOGE/install.sh"
  echo "cron: daemon started"
fi
crontab -l >/dev/null 2>&1 || crontab -l 2>&1 | grep -qi 'no crontab' || die "crontab -l fails: $(crontab -l 2>&1 | head -1)"   # an empty crontab is fine

SCHED="$(python3 -c "import json;print(json.load(open('config/thresholds.json'))['schedule']['cron'])")"
COMMENT="$(python3 -c "import json;print(json.load(open('config/thresholds.json'))['schedule']['cron_comment'])")"
LINE="$SCHED /bin/bash $DOGE/weekly_scan.sh >>$DOGE/cron.log 2>&1"
CUR="$(crontab -l 2>/dev/null || true)"
if printf '%s\n' "$CUR" | grep -qxF "$LINE"; then
  echo "crontab: already installed"
elif printf '%s\n' "$CUR" | grep -qF "$DOGE/weekly_scan.sh"; then
  echo "crontab: a different weekly_scan.sh line exists; left unchanged. Expected: $LINE"
else
  { [ -n "$CUR" ] && printf '%s\n' "$CUR"; printf '%s\n%s\n' "$COMMENT" "$LINE"; } | crontab -
  echo "crontab: installed: $LINE"
fi

# daily new-bot (template-install) check, also idempotent
NSCHED="$(python3 -c "import json;print(json.load(open('config/thresholds.json'))['new_bot_check']['cron'])")"
NCOMMENT="$(python3 -c "import json;print(json.load(open('config/thresholds.json'))['new_bot_check']['cron_comment'])")"
NLINE="$NSCHED /usr/bin/python3 $DOGE/new_bot_check.py >>$DOGE/cron.log 2>&1"
CUR="$(crontab -l 2>/dev/null || true)"
if printf '%s\n' "$CUR" | grep -qxF "$NLINE"; then
  echo "crontab: new-bot check already installed"
elif printf '%s\n' "$CUR" | grep -qF "$DOGE/new_bot_check.py"; then
  echo "crontab: a different new_bot_check.py line exists; left unchanged. Expected: $NLINE"
else
  { [ -n "$CUR" ] && printf '%s\n' "$CUR"; printf '%s\n%s\n' "$NCOMMENT" "$NLINE"; } | crontab -
  echo "crontab: installed: $NLINE"
fi
# keep the comment line right above each cron line current (e.g. after a rename); idempotent, touches nothing else
fix_comment() {
  local CUR NEW
  CUR="$(crontab -l 2>/dev/null || true)"
  NEW="$(printf '%s\n' "$CUR" | CRON_LINE="$1" WANT="$2" python3 -c '
import os, sys
L = sys.stdin.read().split("\n")
if L and L[-1] == "": L = L[:-1]
line, want = os.environ["CRON_LINE"], os.environ["WANT"]
out = []
for l in L:
    if l == line:
        prev = out[-1] if out else None
        if prev == want: pass
        elif prev is not None and prev.startswith("#") and ("DOGE" in prev or "Penny" in prev): out[-1] = want   # old or renamed comment
        else: out.append(want)
    out.append(l)
print("\n".join(out))')"
  if [ "$NEW" != "$CUR" ]; then printf '%s\n' "$NEW" | crontab -; echo "crontab: comment updated: $2"; else echo "crontab: comment ok: $2"; fi
}
fix_comment "$LINE" "$COMMENT"
fix_comment "$NLINE" "$NCOMMENT"
# --- final check: both Penny lines are in the crontab and the daemon is up; loud failure otherwise ---
FINAL="$(crontab -l 2>/dev/null)" || die "crontab -l fails after install"
printf '%s\n' "$FINAL" | grep -qF "$DOGE/weekly_scan.sh" || die "the weekly_scan.sh line is not in the crontab"
printf '%s\n' "$FINAL" | grep -qF "$DOGE/new_bot_check.py" || die "the new_bot_check.py line is not in the crontab"
cron_running || die "the cron daemon stopped during install"
[ -f logs/cron_since ] || date '+%Y-%m-%d %H:%M:%S' > logs/cron_since   # health_check.py: grace period before the first scheduled run
echo "cron: OK (crontab has both Penny lines, daemon running). Health check: python3 $DOGE/health_check.py"
[ -f local/known_bots.json ] || python3 new_bot_check.py --seed   # seeds known bots only; no POST, flags nothing

if [ -f .webhook.json ]; then
  echo "webhook: configured (.webhook.json). Re-check with: bash $DOGE/setup_webhook.sh --use-existing"
else
  cat <<MSG
Next step (see README.md, "Webhook setup"):
  1. Create a webhook-triggered routine named "Penny DOGE threshold wake" on the Penny DOGE bot, using the prompt in
     template/routine_prompts.md, with <PENNY_DIR> set to $DOGE. It must ignore bodies with "probe": true.
  2. Run:  bash $DOGE/setup_webhook.sh
     It asks for the Webhook URL and Webhook key from the routine panel (or reads DOGE_WEBHOOK_URL /
     DOGE_WEBHOOK_KEY), sends one probe, and saves .webhook.json (chmod 600) only when the probe says OK.
  Until then the weekly scan still runs and logs "webhook not configured".
  3. Then send the first-run message in FIRST_RUN.md.
MSG
fi
