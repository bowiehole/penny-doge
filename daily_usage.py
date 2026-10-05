#!/usr/bin/env python3
"""Penny per-day usage scan (read-only, zero model calls). Writes daily_usage.json.

Per bot per local (box TZ) day, for the last `daily_usage.lookback_days` days:
  est_routine: [runs, model_calls, replay_tok]  from routine-run subagent transcripts (inventory signal #5:
               calls = assistant lines; replay proxy = sum of transcript bytes before each assistant line / 4).
  est_chat:    [turns, model_calls, replay_tok] main-thread turns per day from store.db requestIds (signal #16:
               distinct requestId per day from transcript_entries + kv requestIds `at`), times the bot's average
               calls/turn and capped replay/call from its main transcript (no per-line timestamps exist).
  coverage_end: newest local timestamp for the bot (days after it are "no fresh data", not zero).
CLI: exact tokens and ticks per (day, cwd) from `grok usage` per-turn endedAt (cli_sessions.json).
Only ids, counts and timestamps are read out. Never reads `metadata`, prompts, or message text.
Repo->bot attribution is done in summarize.py with the owner's map (owner data stays out of this script).
"""
import json, os, re, glob, sqlite3, datetime as dt, collections
HOME = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(HOME, 'config/thresholds.json')))
DU = CFG['daily_usage']; A = CFG['paths']['agents_dir']; T = os.path.join(os.path.dirname(A), 'agent-transcripts')
CAP_BYTES = DU['context_cap_tokens'] * DU['bytes_per_token']
TR = CFG.get('trend', {}); import math
_today = dt.date.today(); _monday = _today - dt.timedelta(days=_today.weekday())
_trend_weeks = math.ceil(TR.get('months', 0) * TR.get('weeks_per_month', 0))
# cover the configured lookback and every trend week (N full weeks before the current one)
_prev_month_start = (_today.replace(day=1) - dt.timedelta(days=1)).replace(day=1)   # monthly line compares with last month
since = min((dt.datetime.now() - dt.timedelta(days=DU['lookback_days'])).date(), _monday - dt.timedelta(days=7 * _trend_weeks), _prev_month_start)
def day(d): return d.date().isoformat() if d and d.date() >= since else None
def lday(ms): return day(dt.datetime.fromtimestamp(ms / 1000)) if ms else None

def replay(path):
    """#5 method on one transcript: (assistant calls, replay bytes with prefix capped at the context cap)."""
    pref = calls = rep = 0
    with open(path, errors='replace') as f:
        for l in f:
            if l.startswith('{"role":"assistant"'): calls += 1; rep += min(pref, CAP_BYTES)
            pref += len(l)
    return calls, rep

bots = {}
slug_owner = {}
for a in glob.glob(f'{A}/*/automations/*/automation.json'):
    slug_owner[a.split('/')[-2]] = a.split('/')[-4]
for d in sorted(glob.glob(A + '/*/')):
    aid = os.path.basename(d[:-1])
    if not os.path.exists(d + 'profile.json'): continue
    b = bots[aid] = dict(name=json.load(open(d + 'profile.json')).get('name'), est_chat={}, est_routine={}, coverage_end=None)
    newest = 0; oldest = 0; turns = {}
    db = f'/tmp/doge_{aid}.db'   # copy made by fleet_scan.py; never touch the live store
    if os.path.exists(db):
        c = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
        for (e,) in c.execute('select entry from transcript_entries'):
            j = json.loads(e); ts = j.get('timestampMs') or 0; newest = max(newest, ts); rid = j.get('requestId')
            if ts: oldest = min(oldest, ts) if oldest else ts
            if rid and ts: turns[rid] = min(turns.get(rid, ts), ts)
        row = c.execute("select value from kv where key='requestIds'").fetchone()
        for x in json.loads(row[0] or '[]') if row else []:
            if x.get('id') and x.get('at'): turns[x['id']] = min(turns.get(x['id'], x['at']), x['at']); newest = max(newest, x['at'])
        c.close()
    tp = f'{T}/{aid}/{aid}.jsonl'; cpt = rpc = 0
    if os.path.exists(tp):
        users = 0; calls, rep = replay(tp)
        with open(tp, errors='replace') as f: users = sum(1 for l in f if l.startswith('{"role":"user"'))
        cpt = calls / max(users, 1); rpc = rep / max(calls, 1) / DU['bytes_per_token']
    per_day = collections.Counter(lday(t) for t in turns.values())
    for k, n in per_day.items():
        if k: b['est_chat'][k] = [n, round(n * cpt), round(n * cpt * rpc)]
    if turns: oldest = min([oldest] + list(turns.values())) if oldest else min(turns.values())
    b['calls_per_turn'] = round(cpt, 1); b['_newest'] = newest; b['_newest_chat'] = newest; b['_newest_routine'] = 0
    b['_oldest_chat'] = oldest; b['_oldest_routine'] = 0
    b['has_routines'] = any(json.load(open(a)).get('enabled') for a in glob.glob(d + 'automations/*/automation.json'))

deleted_runs = 0
for dname in os.listdir(T):
    if not dname.startswith('sand-subagent'): continue
    p = f'{T}/{dname}/{dname}.jsonl'
    if not os.path.exists(p): continue
    head = open(p, errors='replace').read(4000)
    m = re.search(r'\[routine\] \\?"[^"\\]+\\?" \(folder ([^)]+)\)', head)
    f = re.search(r'fired (\d+/\d+/\d{4}, [\d:]+ [AP]M)', head)
    if not (m and f): continue
    fired = dt.datetime.strptime(f.group(1), '%m/%d/%Y, %I:%M:%S %p')
    a = re.search(r'agent-transcripts/([0-9a-f-]{36})/', head)
    aid = a.group(1) if a and a.group(1) in bots else slug_owner.get(m.group(1))
    k = day(fired)
    if not aid:  # run from a deleted bot (orphan transcript folder): keep the usage under one bucket
        if not k: continue
        deleted_runs += 1; aid = 'deleted'
        bots.setdefault(aid, dict(name='Deleted bots', est_chat={}, est_routine={}, coverage_end=None, calls_per_turn=0, has_routines=False, _newest=0, _newest_chat=0, _newest_routine=0, _oldest_chat=0, _oldest_routine=0))
    fms = fired.timestamp() * 1000
    b = bots[aid]; b['_newest'] = max(b['_newest'], fms); b['_newest_routine'] = max(b['_newest_routine'], fms)
    b['_oldest_routine'] = min(b['_oldest_routine'], fms) if b['_oldest_routine'] else fms
    if not k: continue
    calls, rep = replay(p)
    r = b['est_routine'].setdefault(k, [0, 0, 0]); r[0] += 1; r[1] += calls; r[2] += round(rep / DU['bytes_per_token'])
def ts(n): return dt.datetime.fromtimestamp(n / 1000).strftime('%Y-%m-%d %H:%M') if n else None
for b in bots.values():  # chat and routine data can end on different days; days after each end are 'no fresh data'
    b['coverage_end'] = ts(b.pop('_newest')); b['chat_coverage_end'] = ts(b.pop('_newest_chat')); b['routine_coverage_end'] = ts(b.pop('_newest_routine'))
    b['chat_coverage_start'] = ts(b.pop('_oldest_chat')); b['routine_coverage_start'] = ts(b.pop('_oldest_routine'))  # days before = no data, not zero

cli = collections.defaultdict(lambda: [0, 0]); cli_first = None
for s in json.load(open(os.path.join(HOME, 'cli_sessions.json'))):
    for ended, tok, ticks in s.get('turns') or []:
        try: d = dt.datetime.fromisoformat(re.sub(r'(\.\d{6})\d+', r'\1', ended).replace('Z', '+00:00')).astimezone().replace(tzinfo=None)
        except Exception: continue
        cli_first = min(cli_first, d.date().isoformat()) if cli_first else d.date().isoformat()  # first day with any CLI record
        k = day(d)
        if k: c = cli[(k, s.get('cwd') or '')]; c[0] += tok; c[1] += ticks
cli_newest = max((s.get('updated') or '' for s in json.load(open(os.path.join(HOME, 'cli_sessions.json')))), default=None)
json.dump(dict(generated_at=dt.datetime.now().strftime('%Y-%m-%d %H:%M'), since=since.isoformat(), deleted_bot_routine_runs=deleted_runs,
               cli_newest_utc=cli_newest, cli_first_day=cli_first, bots=bots, cli=[[k, cwd, v[0], v[1]] for (k, cwd), v in sorted(cli.items())]),
          open(os.path.join(HOME, 'daily_usage.json'), 'w'), separators=(',', ':'))
print('daily_usage ok', len(bots), 'bots; routine runs from deleted bots', deleted_runs)
