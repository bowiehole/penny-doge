#!/usr/bin/env bash
# Penny webhook walkthrough: take the wake routine's URL + key, send ONE probe POST {"probe": true}, and save
# them to .webhook.json (chmod 600) only when the probe gets a 2xx, so a typo never replaces a working setup.
# Never prints the key or the URL. The key reaches Python through the environment, never argv.
#
#   bash setup_webhook.sh                  ask for URL and key (or read env DOGE_WEBHOOK_URL / DOGE_WEBHOOK_KEY)
#   bash setup_webhook.sh --use-existing   no prompt: re-check the saved .webhook.json with one probe
#
# The "Penny threshold wake" routine must ignore bodies with "probe": true (see README.md).
set -eu
DOGE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; cd "$DOGE"
MODE=new
case "${1:-}" in
  --use-existing) MODE=existing ;;
  "") ;;
  *) echo "usage: bash setup_webhook.sh [--use-existing]"; exit 2 ;;
esac
if [ "$MODE" = new ]; then
  URL="${DOGE_WEBHOOK_URL:-}"; KEY="${DOGE_WEBHOOK_KEY:-}"
  if [ -z "$URL" ]; then read -r -p "Webhook URL (copy it from the routine panel): " URL; fi
  if [ -z "$KEY" ]; then read -r -s -p "Webhook key (paste only the key; typing is hidden): " KEY; echo; fi
  export DOGE_WEBHOOK_URL="$URL" DOGE_WEBHOOK_KEY="$KEY"
fi
DOGE_SETUP_MODE="$MODE" DOGE_HOME="$DOGE" exec python3 - <<'PY'
import json, os, sys
HOME = os.environ['DOGE_HOME']; sys.path.insert(0, HOME)
from doge_webhook import norm_key, webhook, post   # the one place that cleans the key and sets both auth headers
def load(p):
    try:
        with open(p) as f: return json.load(f)
    except Exception: return None
CFG = load(os.path.join(HOME, 'config/thresholds.json')) or load(os.path.join(HOME, 'config/thresholds.default.json'))
wf = CFG['paths']['webhook_file']; path = wf if os.path.isabs(wf) else os.path.join(HOME, wf)
if os.environ['DOGE_SETUP_MODE'] == 'new':
    url = os.environ.pop('DOGE_WEBHOOK_URL', '').strip(); key = norm_key(os.environ.pop('DOGE_WEBHOOK_KEY', ''))
    if not url.startswith('https://'): print('The URL should start with https:// . Copy the Webhook URL field from the routine panel and run again.'); sys.exit(1)
    if not key: print('No key given. Copy the Webhook key field from the routine panel and run again.'); sys.exit(1)
    if any(c.isspace() for c in key): print('The key has spaces in it. Paste only the Webhook key field, nothing else, and run again.'); sys.exit(1)
    w = {'url': url, 'key': key}   # probe first; save only after a 2xx so a typo never replaces a working setup
else:
    w = webhook(path)   # re-reads, re-normalizes, keeps chmod 600
    if not w: print('webhook not configured: run  bash setup_webhook.sh  and paste the URL and key from the routine panel.'); sys.exit(1)
code, err = post(w, {'probe': True}, CFG['wake']['timeout_s'])
def save(w):
    if load(path) == w: return 'already saved'
    old = os.umask(0o077)
    try:
        if os.path.exists(path): os.replace(path, path + '.bak')   # previous setup kept owner-only as .bak
        tmp = path + '.tmp'
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as f: json.dump(w, f)
        os.replace(tmp, path)
    finally: os.umask(old)
    return 'saved (owner-only, chmod 600)'
if code and 200 <= code < 300:
    note = save(w) if os.environ['DOGE_SETUP_MODE'] == 'new' else 'unchanged'
    print(f'probe OK (HTTP {code}); {os.path.basename(path)} {note}'); sys.exit(0)
if os.environ['DOGE_SETUP_MODE'] == 'new': print(f'Not saved: {os.path.basename(path)} was left as it was.')
if code in (401, 403):
    print(f'probe failed (HTTP {code}): the key is wrong or was regenerated. Open the "Penny threshold wake" routine panel, '
          're-copy the Webhook key field, and run  bash setup_webhook.sh  again. Paste only the key, not "Bearer" or "Authorization:".')
elif code == 404:
    print('probe failed (HTTP 404): the URL is not found. The routine may have been deleted or recreated; re-copy the Webhook URL field and run again.')
elif code:
    print(f'probe failed (HTTP {code}). Check the routine is enabled, then run  bash setup_webhook.sh --use-existing  again.')
else:
    print(f'probe failed ({err}): no answer from the webhook. Check the network and the URL, then run again.')
sys.exit(1)
PY
