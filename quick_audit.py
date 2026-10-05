#!/usr/bin/env python3
"""Penny quick audit (no AI): run once on the very first chat, BEFORE any crons or webhook setup.
One cheap local pass over routines, chats and always-on text; names the top 1-3 burners with a one-line why.
Reuses the collectors (run_all.sh) and the same estimate math as the weekly report; borrows common routine health-check
flags (too frequent, noisy empty runs, recurring on a long chat). Recommend-only: it changes nothing.

  python3 quick_audit.py [--no-refresh]   -> summary/quick_audit.json (small) + summary/quick_audit.md
Local files can lag live state, so every item says "confirm live". Names and counts only; prompts are scanned
in memory for a quiet rule and never output.
"""
import json, os, re, subprocess, sys, glob, datetime as dt
HOME = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HOME)
from penny_common import P, load, cron_rates, bot_usd, apply_alert_level

CFG = load(P('config/thresholds.json')) or load(P('config/thresholds.default.json'))
OWNER = load(P(CFG['paths']['owner_file']), {}) or {}
CFG = apply_alert_level(CFG, OWNER); Q = CFG['quick_audit']; EST = CFG['estimates']; A20 = CFG['advice']['A20']; DU = CFG['daily_usage']
A = os.path.expanduser(CFG['paths']['agents_dir']); NOW = dt.datetime.now()
STALE = NOW - dt.timedelta(days=CFG['freshness']['stale_days'])
QUIET_RE = CFG['new_bot_check']['quiet_regex']
if '--no-refresh' not in sys.argv:
    subprocess.run(['bash', os.path.join(HOME, 'run_all.sh')], cwd=HOME, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

fleet = load(P('fleet_scan.json'), {}) or {}
cost = {r['slug']: r for r in (load(P('routine_cost.json'), []) or [])}
quiet = {r['slug']: r for r in (load(P('routine_quiet.json'), []) or [])}
runs = load(P('routine_runs.json'), []) or []
dud = load(P('daily_usage.json'), {}) or {}
excl = set(OWNER.get('exclude_bot_ids', [])) | {OWNER.get('doge_bot_id')}
def handled(bid, slug):
    return any(h.get('bot_id') == bid and h.get('slug') in ('*', slug) for h in OWNER.get('handled', []))
last_run = {}
for r in runs:
    if r.get('fired'): last_run[r['slug']] = max(last_run.get(r['slug'], ''), r['fired'])

items, n_rout = [], 0
for f in glob.glob(f'{A}/*/automations/*/automation.json'):
    bid, slug = f.split('/')[-4], f.split('/')[-2]
    j = load(f, {}) or {}
    if not j.get('enabled') or bid in excl or handled(bid, slug): continue
    n_rout += 1
    bname = (fleet.get(bid) or {}).get('name') or (load(f'{A}/{bid}/profile.json', {}) or {}).get('name') or '?'
    cr = cron_rates(j.get('schedule')); c = cost.get(slug) or {}; q = quiet.get(slug) or {}
    rpw = cr['per_week'] if cr else (c.get('observed_runs') or 0) / 4   # event routine: rough observed rate
    per_run = bot_usd(c.get('calls_per_run') or 0, c.get('est_replay_tok_per_run') or 0, EST) if c else None
    usd_wk = per_run * rpw if per_run is not None else None
    chat_mb = round(((fleet.get(bid) or {}).get('tx_bytes') or 0) / 1e6, 1)
    has_quiet = bool(re.search(QUIET_RE, j.get('prompt') or ''))
    qp = q.get('quiet_pct')
    why = []
    if cr and cr['per_hour'] > Q['max_fires_per_hour']:
        why.append(f"fires {cr['per_day']}×/day ({cr['per_hour']}/hour); hourly, a few times a day, or an event trigger would do")
    if qp is not None and qp >= Q['quiet_pct_noisy']:
        why.append(f"{qp}% of runs found nothing to report" + ("" if has_quiet else " and the prompt has no stay-quiet rule") + ", but every run still pays for a full session")
    elif not has_quiet and not cr is None:
        why.append("no stay-quiet-when-nothing-new rule, so empty runs still post or dig in")
    if chat_mb >= Q['long_chat_mb'] and (q.get('surfaced') or 0) > 0:
        why.append(f"recurring job on a bot whose chat is {chat_mb:g} MB, so each wake re-reads that long chat")
    if c.get('calls_per_run') and c['calls_per_run'] >= CFG['advice']['A7']['min_calls_per_run']:
        why.append(f"each run makes ~{c['calls_per_run']:g} model calls")
    if not why and not usd_wk: continue
    items.append(dict(bot=bname, routine=slug, kind='routine', usd_wk=None if usd_wk is None else round(usd_wk, 2),
                      runs_wk=round(rpw, 1), why='; '.join(why[:2]) if why else 'the heaviest scheduled routine by estimated cost',
                      also=len(why) - 2 if len(why) > 2 else 0, data_as_of=(last_run.get(slug) or '')[:10] or None))
# long main chats that still get turns (A20-style): every turn re-sends the long history
for bid, b in (dud.get('bots') or {}).items():
    if bid in excl or handled(bid, '*') or bid not in fleet: continue
    mb = ((fleet[bid].get('tx_bytes') or 0) / 1e6)
    if mb < A20['min_chat_mb']: continue
    ce = (b.get('chat_coverage_end') or '')[:10]
    if not ce: continue
    lo = (dt.date.fromisoformat(ce) - dt.timedelta(days=6)).isoformat()
    turns = sum(v[0] for d, v in (b.get('est_chat') or {}).items() if lo <= d <= ce)
    if turns < A20['min_turns_per_week']: continue
    per_call = min(mb * 1e6 / DU['bytes_per_token'], DU['context_cap_tokens'])
    usd_wk = turns * (b.get('calls_per_turn') or 1) * per_call * EST['bot_usd_per_m_tokens'] / 1e6
    items.append(dict(bot=fleet[bid].get('name'), routine='main chat', kind='chat', usd_wk=round(usd_wk, 2), runs_wk=turns,
                      why=f"chat is {mb:.0f} MB and still gets ~{turns} turns a week; each turn re-sends that history (old history gets summarized, so this $ is an upper bound)",
                      also=0, data_as_of=ce))
items.sort(key=lambda x: -(x['usd_wk'] or 0))
top, n_chat = [], 0   # chat $ is an upper-bound proxy, so cap chat items and keep routines visible
for x in items:
    if x['kind'] == 'chat':
        if n_chat >= Q.get('max_chat_items', 1): continue
        n_chat += 1
    top.append(x)
    if len(top) >= Q['top_n']: break
newest = max((x['data_as_of'] for x in items if x['data_as_of']), default=None)
stale = bool(not newest or dt.datetime.fromisoformat(newest) < STALE)
out = dict(generated_at=NOW.strftime('%Y-%m-%d %H:%M'), scanned=dict(bots=len(fleet), enabled_routines=n_rout),
           data_as_of=newest, data_stale=stale, price_usd_per_m=EST['bot_usd_per_m_tokens'],
           top=[{k: v for k, v in x.items() if v not in (None, 0) or k in ('usd_wk',)} for x in top],
           note='Local estimates; confirm live with the owning bot before changing anything. Recommend-only.')
def size(): return len(json.dumps(out, separators=(',', ':')).encode())
while size() > Q['max_bytes'] and out['top']: out['top'].pop()
os.makedirs(P(CFG['paths']['summary_dir']), exist_ok=True)
json.dump(out, open(os.path.join(P(CFG['paths']['summary_dir']), 'quick_audit.json'), 'w'), separators=(',', ':'))
md = [f"**Quick audit: top {len(out['top'])} likely burners** (local data{' to ' + newest if newest else ''}; estimates, confirm live)", ""]
for i, x in enumerate(out['top'], 1):
    md.append(f"{i}. **{x['bot']} › {x['routine']}**" + (f" ~${x['usd_wk']:.2f}/wk" if x.get('usd_wk') else "") + f": {x['why']}.")
if not out['top']: md.append("Nothing stands out in the local data.")
if stale: md += ["", f"*Local data is older than {CFG['freshness']['stale_days']} days, so some of these may already be paused or changed.*"]
open(os.path.join(P(CFG['paths']['summary_dir']), 'quick_audit.md'), 'w').write("\n".join(md) + "\n")
print("\n".join(md)); print(json.dumps(dict(quick_audit_bytes=size(), items=len(out['top']))))
