#!/usr/bin/env bash
# Penny DOGE installer: idempotent. Safe to run any number of times; a second run changes nothing.
# Creates folders, copies default thresholds and the owner template if missing, installs the weekly
# cron line only if it is not already there (all other crontab lines are kept exactly), then prints the next step.
set -eu
DOGE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; cd "$DOGE"
mkdir -p history ledger summary local logs config
[ -f config/thresholds.json ] || { cp config/thresholds.default.json config/thresholds.json; echo "created config/thresholds.json from defaults"; }
[ -f local/owner.json ] || { cp config/owner.example.json local/owner.json; echo "created local/owner.json (edit: handled items, exclusions, repo->bot map)"; }
for f in weekly_scan.sh install.sh run_all.sh new_bot_check.py setup_webhook.sh; do [ -x "$f" ] || chmod +x "$f"; done
for f in .webhook.json .webhook.json.bak; do [ -f "$f" ] && [ "$(stat -c %a "$f")" != 600 ] && chmod 600 "$f"; done; true

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
