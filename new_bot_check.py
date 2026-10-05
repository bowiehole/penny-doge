#!/usr/bin/env python3
"""Penny daily template-install check. Zero model calls. Silent unless something is flagged.

Keeps known bots in local/known_bots.json. On the first run it seeds every current bot and flags nothing.
Later runs look at new bots, and at new routines on bots first seen within new_bot_check.watch_days, and flag
enabled scheduled AI routines that break the cheap pattern:
  - cron denser than max_fires_per_hour, or
  - time-based polling whose prompt has no quiet rule.
Webhook / event triggers count as good. Prompts are scanned in memory for the quiet rule only; no text is output.
Only when a flagged routine exists: write summary/new_bots.json and POST {"mode":"new_bot_check","bot":...,
"summary_path":...} to the Penny webhook (one POST per flagged bot). Then runs the fleet-wide spike guard
(spike_guard.py), which POSTs at most once: {"mode":"spike","summary_path":...,"count":n}.
--dry-run: never POST and write nothing. --seed: only create local/known_bots.json if missing (used by install.sh).
"""
import json, os, re, sys, glob, time, datetime as dt
HOME = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HOME)
from doge_webhook import webhook, post
def P(rel): return rel if os.path.isabs(rel) else os.path.join(HOME, rel)
def load(p, d=None):
    try:
        with open(p) as f: return json.load(f)
    except Exception: return d
CFG = load(P('config/thresholds.json')); NB = CFG['new_bot_check']; PATHS = CFG['paths']
OWNER = load(P(PATHS['owner_file']), {}) or {}
A = PATHS['agents_dir']; NOW = dt.datetime.now(); DRY = '--dry-run' in sys.argv; SEED = '--seed' in sys.argv   # --seed: only seed local/known_bots.json if missing (install.sh)
def log(msg): print(f"{NOW:%Y-%m-%d %H:%M:%S} {time.strftime('%Z')} Penny new_bot_check {msg}")

def per_hour(sched):
    parts = re.sub(r'CRON_TZ=\S+\s+', '', sched or '').split()
    if len(parts) != 5: return None
    n = 0
    for part in parts[0].split(','):
        step = 1
        if '/' in part: part, step = part.split('/'); step = int(step)
        a, b = (0, 59) if part == '*' else (tuple(map(int, part.split('-'))) if '-' in part else (int(part), int(part)))
        n += len(range(a, b + 1, step))
    return n

def bots_now():
    out = {}
    for d in glob.glob(A + '/*/profile.json'):
        aid = d.split('/')[-2]
        out[aid] = dict(name=(load(d, {}) or {}).get('name'), slugs=sorted(p.split('/')[-2] for p in glob.glob(f'{A}/{aid}/automations/*/automation.json')))
    return out

known_p = P(NB['known_file']); known = load(known_p)
cur = bots_now()
if known is None:   # first run: seed, flag nothing
    known = {aid: dict(name=b['name'], first_seen=NOW.strftime('%Y-%m-%d'), slugs=b['slugs'], seeded=True) for aid, b in cur.items()}
    if SEED or not DRY: os.makedirs(os.path.dirname(known_p), exist_ok=True); json.dump(known, open(known_p, 'w'), indent=1)   # dry-run writes nothing
    log(f"seeded {len(known)} bots; nothing flagged"); sys.exit(0)
if SEED: log(f"already seeded ({len(known)} bots); nothing to do"); sys.exit(0)

flagged = {}
for aid, b in cur.items():
    k = known.get(aid)
    if k is None:
        new_slugs = b['slugs']; known[aid] = dict(name=b['name'], first_seen=NOW.strftime('%Y-%m-%d'), slugs=[])
    else:
        recent = not k.get('seeded') and (NOW - dt.datetime.strptime(k['first_seen'], '%Y-%m-%d')).days <= NB['watch_days']
        new_slugs = [s for s in b['slugs'] if s not in k.get('slugs', [])] if recent else []
    for slug in new_slugs:
        j = load(f'{A}/{aid}/automations/{slug}/automation.json', {}) or {}
        sched = j.get('schedule'); trig = (j.get('triggerPresentation') or {}).get('trigger', {}).get('type')
        if not j.get('enabled') or not sched or trig == 'webhook' or sched == 'webhook': continue   # event-driven = good
        reasons = []
        ph = per_hour(sched)
        if ph and ph > NB['max_fires_per_hour']: reasons.append(f"cron denser than {NB['max_fires_per_hour']}/hour ({ph}/hour)")
        if not re.search(NB['quiet_regex'], j.get('prompt') or ''): reasons.append('time-based polling with no quiet rule')
        if reasons: flagged.setdefault(aid, []).append(dict(routine=slug, schedule=sched, reasons=reasons))
    known[aid]['slugs'] = sorted(set(known[aid].get('slugs', [])) | set(b['slugs'])); known[aid]['name'] = b['name']

if not DRY: json.dump(known, open(known_p, 'w'), indent=1)   # dry-run writes nothing
w = webhook(P(PATHS['webhook_file']))

def send(body, label):
    if DRY: log(f"{label}; dry-run, no POST"); return
    if not w: log(f"{label}; webhook not configured"); return
    code, err = post(w, body, CFG['wake']['timeout_s'])
    if code and 200 <= code < 300: log(f"{label}; posted status={code}")
    else:
        with open(P(PATHS['wake_queue']), 'a') as f: f.write(json.dumps(body) + '\n')
        log(f"{label}; post failed status={code or err}; queued")

def stamp(): return NOW.strftime('%Y-%m-%d %H:%M') + ' ' + (dt.datetime.now().astimezone().strftime('%Z') or 'local')

if not flagged: log(f"checked {len(cur)} bots; nothing flagged")
else:
    summ = dict(generated_at=stamp(),
                bots=[dict(bot=cur[a]['name'], bot_id=a, routines=r) for a, r in flagged.items()],
                advice='Prefer webhook/event triggers; if polling is needed, run at most hourly and add a stay-quiet-when-nothing-new rule plus a cheap pre-check.')
    sp = P(NB['summary_file'])
    if not DRY: os.makedirs(os.path.dirname(sp), exist_ok=True); json.dump(summ, open(sp, 'w'), indent=1)
    for aid in flagged:
        body = {'mode': 'new_bot_check', 'bot': cur[aid]['name'], 'summary_path': os.path.relpath(sp, HOME)}   # relative to the Penny folder
        send(body, f"flagged {cur[aid]['name']} ({len(flagged[aid])} routines)")

# ---------- fleet-wide daily spike guard (spike_guard.py); one POST total, only when something jumped ----------
try:
    from penny_common import apply_alert_level
    import spike_guard
    CFG2 = apply_alert_level(CFG, OWNER)
    sg = spike_guard.check(CFG2, OWNER, NOW, dry=DRY)
except Exception as e:
    log(f"spike guard error: {type(e).__name__}"); sys.exit(0)
if not sg['enabled']: log("spike guard off"); sys.exit(0)
if sg['stale']: log(f"spike guard: data stale (newest routine run {sg['newest_run']}); no alert"); sys.exit(0)
if not sg['items']: log(f"spike guard: {sg['checked']} routines checked, {sg['baselined']} with baseline; nothing jumped"); sys.exit(0)
SG = CFG2['spike_guard']
spp = P(SG['summary_file'])
sumsp = dict(generated_at=stamp(), newest_run=sg['newest_run'], items=sg['items'],
             advice='Ask the owner before telling the owning bot anything; Penny is recommend-only.')
if not DRY: os.makedirs(os.path.dirname(spp), exist_ok=True); json.dump(sumsp, open(spp, 'w'), indent=1)
send({'mode': 'spike', 'summary_path': os.path.relpath(spp, HOME), 'count': len(sg['items'])},
     f"spike guard: {len(sg['items'])} item(s) ({', '.join(i['kind'] + ':' + i['routine'] for i in sg['items'])})")
