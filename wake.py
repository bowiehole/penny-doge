#!/usr/bin/env python3
"""Penny wake sender. Zero model calls. Never prints the webhook key or URL.

  wake.py --probe          POST {"probe": true}; print only the HTTP status.
  wake.py --post           POST the wake body if ledger/pending.json has eligible items.
  wake.py --post --dry-run Decide and print, but do not POST and do not touch the ledger.
  wake.py --record-skip K  Record owner skip(s) in ledger/raised.json['skipped'] with numeric saved, week, date.
Output (for weekly_scan.sh): one line "posted=yes|no reason=..." (no secrets).
"""
import json, os, sys, datetime as dt

HOME = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HOME)
from doge_webhook import webhook, post   # single home for key cleanup + both auth headers; never prints the key
def P(rel): return rel if os.path.isabs(rel) else os.path.join(HOME, rel)
def load(p, d=None):
    try:
        with open(p) as f: return json.load(f)
    except Exception: return d
CFG = load(P('config/thresholds.json')) or load(P('config/thresholds.default.json'))
from penny_common import apply_alert_level
CFG = apply_alert_level(CFG, load(P(CFG['paths']['owner_file']), {}) or {})   # 'normal' = unchanged
PATHS = CFG['paths']; TIMEOUT = CFG['wake']['timeout_s']
args = set(sys.argv[1:])

def today(): return dt.datetime.now().strftime('%Y-%m-%d')

def record_skips(keys, reason='owner-skipped'):
    """Write ledger['skipped'] entries with numeric saved + week + date. Used when the owner skips
    an item or a live-confirm drops it. Missing saved must never be written (that used to re-wake)."""
    summary = load(P(PATHS['summary_dir']) + '/latest.json', {}) or {}
    by = {c['key']: c for c in summary.get('candidates', []) if c.get('key')}
    # pending carries the wake-eligible saved numbers too
    pend = load(P(PATHS['pending_file']), {}) or {}
    for it in pend.get('items', []):
        by.setdefault(it['key'], it)
    led_p = P(PATHS['ledger_file']); led = load(led_p, {}) or {}; led.setdefault('raised', {}); led.setdefault('skipped', {})
    week = pend.get('week') or summary.get('week') or ''
    out = []
    for key in keys:
        c = by.get(key) or {}
        saved = c.get('saved', c.get('est_runs_wk_saved'))
        try: saved = float(saved if saved is not None else 0)
        except (TypeError, ValueError): saved = 0.0
        entry = dict(week=week, date=today(), advice=c.get('advice') or '', saved=saved, reason=f"{reason} {today()}")
        led['skipped'][key] = entry; out.append(dict(key=key, **entry))
        led.get('raised', {}).pop(key, None)   # a skip replaces a prior raise of the same key
    tmp = led_p + '.tmp'; json.dump(led, open(tmp, 'w'), indent=1); os.replace(tmp, led_p)
    return out

w = webhook(P(PATHS['webhook_file']))
if '--probe' in args:
    if not w: print('webhook not configured'); sys.exit(0)
    code, err = post(w, {'probe': True}, TIMEOUT); print(code if code else f'error {err}'); sys.exit(0)

# Record owner skips / failed live-confirms with a numeric saved (never omit it).
# Usage: python3 wake.py --record-skip KEY [KEY...]
if '--record-skip' in sys.argv:
    keys = [a for a in sys.argv[1:] if a != '--record-skip' and not a.startswith('--')]
    if not keys: print('usage: wake.py --record-skip KEY [KEY...]'); sys.exit(2)
    rows = record_skips(keys); print(json.dumps(dict(recorded=len(rows), items=rows))); sys.exit(0)

pend = load(P(PATHS['pending_file']), {}) or {}
items = pend.get('items', [])
unread = pend.get('unread', [])  # fresh unread senders not raised before: they also make the wake a report
warn = bool(pend.get('warn'))     # budget/runway early warning: also makes the wake a report
unmapped = pend.get('unmapped', [])   # large unmapped coding not asked about yet: ask the owner to map repos (report)
summary = load(P(PATHS['summary_dir']) + '/latest.json', {}) or {}
body = {'week': pend.get('week') or summary.get('week'), 'summary_path': os.path.relpath(os.path.join(P(PATHS['summary_dir']), 'latest.json'), HOME),   # relative to the Penny folder
        'candidate_count': len(summary.get('candidates', [])), 'mode': 'report' if (items or unread or warn or unmapped) else 'cost_only'}
ALWAYS = CFG['wake'].get('always_weekly_cost', False)
hook = 'configured' if w else 'not-configured'
if '--dry-run' in args: print(f'posted=no reason=dry-run would_post={"yes" if (items or unread or warn or unmapped or ALWAYS) else "no"} mode={body['mode']} items={len(items)} unread={len(unread)} unmapped={len(unmapped)} warn={warn} webhook={hook}'); sys.exit(0)
if not w: print(f'posted=no reason="webhook not configured" items={len(items)}'); sys.exit(0)
if not items and not unread and not warn and not unmapped and not ALWAYS: print('posted=no reason=no-new-or-worse-fresh-candidate'); sys.exit(0)
code, err = post(w, body, TIMEOUT)
if code and 200 <= code < 300:
    led_p = P(PATHS['ledger_file']); led = load(led_p, {}) or {}; led.setdefault('raised', {}); led.setdefault('skipped', {})
    for it in items: led['raised'][it['key']] = dict(week=body['week'], date=today(), advice=it['advice'], saved=float(it.get('saved') or 0))
    for it in unread: led.setdefault('unread', {})[it['id']] = dict(week=body['week'], unseen_msgs=it['unseen_msgs'])  # re-raise only when unseen_msgs doubles
    for it in unmapped: led.setdefault('unmapped', {})[it['repo']] = dict(week=body['week'], date=today(), tok=it['tok'])   # asked once; re-ask only if it doubles
    for it in pend.get('idle', []): led.setdefault('idle', {})[it['id']] = dict(week=body['week'], idle_days=it['idle_days'])  # A14: raised once the wake (report or cost_only) went out
    tmp = led_p + '.tmp'; json.dump(led, open(tmp, 'w'), indent=1); os.replace(tmp, led_p)
    print(f'posted=yes status={code} mode={body["mode"]} items={len(items)}')
else:
    with open(P(PATHS['wake_queue']), 'a') as f: f.write(json.dumps(body) + '\n')
    print(f'posted=no reason=post-failed status={code or err} queued=wake_queue.log')
