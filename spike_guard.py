"""Penny fleet-wide daily spike guard (no AI). Called by new_bot_check.py once a day.

Per enabled routine, from local routine-run transcripts (fired time, model calls, replay proxy) and runs.json:
  spike  - runs or est. $ in the last 24h >= factor x the trailing daily average (baseline_days), above floors
  loop   - many runs inside a short window beyond what the schedule allows, or repeated identical failures
Needs a baseline (>= min_baseline_days of history before the window). Stale data never alerts: the newest
routine-run transcript must be within max_data_age_hours. Each routine+kind alerts at most once per cooldown_days.
Returns the items; the caller decides whether to POST mode=spike. Names and counts only; no prompt text read.
"""
import json, os, re, glob, statistics, datetime as dt
from penny_common import P, load, cron_rates, bot_usd

def scan_runs(T):
    """[(slug, fired datetime, calls, replay_tok)] from sand-subagent routine transcripts."""
    out = []
    for d in os.listdir(T):
        if not d.startswith('sand-subagent'): continue
        p = f'{T}/{d}/{d}.jsonl'
        try:
            with open(p, errors='replace') as f: L = f.read().splitlines()
        except OSError: continue
        if not L: continue
        head = L[0][:4000]
        m = re.search(r'\[routine\] \\?"[^"\\]+\\?" \(folder ([^)]+)\)', head)
        fm = re.search(r'fired (\d+/\d+/\d{4}, [\d:]+ [AP]M)', head)
        if not (m and fm): continue
        try: fired = dt.datetime.strptime(fm.group(1), '%m/%d/%Y, %I:%M:%S %p')
        except ValueError: continue
        pref = calls = replay = 0
        for l in L:
            if l.startswith('{"role":"assistant"'): calls += 1; replay += pref
            pref += len(l)
        out.append((m.group(1), fired, calls, replay / 4))
    return out

def check(CFG, OWNER, now=None, dry=True):
    S = CFG['spike_guard']; EST = CFG['estimates']; A = os.path.expanduser(CFG['paths']['agents_dir'])
    T = os.path.join(os.path.dirname(A.rstrip('/')), 'agent-transcripts')
    now = now or dt.datetime.now()
    status = dict(enabled=bool(S.get('enabled')), checked=0, baselined=0, newest_run=None, stale=True, items=[])
    if not S.get('enabled') or not os.path.isdir(T): return status
    runs = scan_runs(T)
    newest = max((r[1] for r in runs), default=None)
    status['newest_run'] = newest.strftime('%Y-%m-%d %H:%M') if newest else None
    status['stale'] = bool(not newest or (now - newest).total_seconds() / 3600 > S['max_data_age_hours'])
    if status['stale']: return status        # stale data never triggers a wake
    excl = set(OWNER.get('exclude_bot_ids', [])) | {OWNER.get('doge_bot_id')}
    defs = {}
    for f in glob.glob(f'{A}/*/automations/*/automation.json'):
        j = load(f, {}) or {}; bid, slug = f.split('/')[-4], f.split('/')[-2]
        if j.get('enabled') and bid not in excl: defs[slug] = dict(bot_id=bid, schedule=j.get('schedule'), path=os.path.dirname(f))
    names = {aid.split('/')[-2]: (load(aid, {}) or {}).get('name') for aid in glob.glob(f'{A}/*/profile.json')}
    by = {}
    for slug, fired, calls, replay in runs:
        if slug in defs and fired <= now: by.setdefault(slug, []).append((fired, bot_usd(calls, replay, EST)))
    w_start = now - dt.timedelta(hours=24); b_start = w_start - dt.timedelta(days=S['baseline_days'])
    led_p = P(S['ledger_file']); led = load(led_p, {}) or {}
    items = []
    def recent_alert(slug, kind):
        t = (led.get(slug) or {}).get(kind)
        return bool(t and (now - dt.datetime.strptime(t, '%Y-%m-%d %H:%M')).days < S['cooldown_days'])
    for slug, R in by.items():
        status['checked'] += 1; d = defs[slug]; R.sort()
        bot = names.get(d['bot_id']) or '?'
        recent = [r for r in R if r[0] >= w_start]
        base = [r for r in R if b_start <= r[0] < w_start]
        first = R[0][0]
        base_days = min(S['baseline_days'], (w_start - max(first, b_start)).days)
        if recent and base_days >= S['min_baseline_days']:
            status['baselined'] += 1
            b_runs = len(base) / base_days; b_usd = sum(x[1] for x in base) / base_days
            r_runs = len(recent); r_usd = sum(x[1] for x in recent)
            if r_runs >= S['factor'] * max(b_runs, 1e-9) and r_runs >= S['min_runs_per_day'] and r_runs - b_runs >= S['min_extra_runs'] and not recent_alert(slug, 'runs'):
                items.append(dict(kind='runs', bot=bot, routine=slug, last_24h=r_runs, baseline_per_day=round(b_runs, 1), jump=round(r_runs / b_runs, 1) if b_runs else None,
                                  why=f"{r_runs} runs in the last 24h vs ~{b_runs:.1f}/day normally; every run is a paid model session"))
            elif r_usd >= S['factor'] * max(b_usd, 1e-9) and r_usd >= S['min_usd_per_day'] and not recent_alert(slug, 'cost'):
                items.append(dict(kind='cost', bot=bot, routine=slug, last_24h_usd=round(r_usd, 2), baseline_usd_per_day=round(b_usd, 2), jump=round(r_usd / b_usd, 1) if b_usd else None,
                                  why=f"~${r_usd:.2f} in the last 24h vs ~${b_usd:.2f}/day normally (estimate); runs got heavier, so each one costs more"))
        # loop: many runs inside a short window, beyond what the schedule allows
        cr = cron_rates(d['schedule']); win = dt.timedelta(minutes=S['loop_window_min'])
        allowed = (cr['per_hour'] * S['loop_window_min'] / 60 * S['loop_schedule_factor']) if cr else 0
        ts = [r[0] for r in recent]; worst = 0
        for i in range(len(ts)):
            j = i
            while j < len(ts) and ts[j] - ts[i] <= win: j += 1
            worst = max(worst, j - i)
        if worst >= S['loop_min_runs'] and worst > allowed and not recent_alert(slug, 'loop'):
            items.append(dict(kind='loop', bot=bot, routine=slug, runs_in_window=worst, window_min=S['loop_window_min'],
                              why=f"{worst} runs within {S['loop_window_min']} min, more than its schedule allows; looks like a loop or retry storm, and each run is billed"))
        # repeated identical failures (runs.json), only if the latest one is fresh
        rj = load(os.path.join(d['path'], 'runs.json'), []) or []
        rj = sorted((x for x in rj if isinstance(x, dict) and x.get('startedAt')), key=lambda x: x['startedAt'])
        tail = rj[-S['fail_repeat']:]
        if len(tail) == S['fail_repeat'] and all(x.get('status') != 'ok' for x in tail):
            kinds = {x.get('errorKind') or x.get('status') for x in tail}
            last = dt.datetime.fromtimestamp(tail[-1]['startedAt'] / 1000)
            if len(kinds) == 1 and last >= w_start and not recent_alert(slug, 'fail'):
                items.append(dict(kind='fail', bot=bot, routine=slug, repeats=len(tail), error=str(kinds.pop())[:40],
                                  why=f"last {len(tail)} runs failed the same way; failed runs still bill for the tokens used before the error"))
    status['items'] = items
    if items and not dry:
        for it in items: led.setdefault(it['routine'], {})[it['kind']] = now.strftime('%Y-%m-%d %H:%M')
        os.makedirs(os.path.dirname(led_p), exist_ok=True); json.dump(led, open(led_p, 'w'), indent=1)
    return status
