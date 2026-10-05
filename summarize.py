#!/usr/bin/env python3
"""Penny DOGE weekly no-AI summarizer. Zero model calls.

Reads the JSON written by run_all.sh, saves a compact weekly snapshot to history/YYYY-WW.json,
applies the advice-catalog thresholds (config/thresholds.json) as plain code, compares with the
previous week's snapshot and the raised/skipped ledger, and writes a small summary/latest.json
(+ dated copy) plus ledger/pending.json (items eligible for a wake). Prints one line of JSON
for weekly_scan.sh.

Privacy: outputs bot/routine names, ids and counts only. It never reads or prints the store.db
`metadata` field, message contents, or credential files. Routine
prompts are read in memory only to compute an A5 keyword-overlap percentage; no text is output.

Advice implemented: A1 A2 A4 A5 A6 A7 A9 A10 A13 A15 A16 A17 A18 A19 A20 A21, plus A14 idle-bot tracking (idle_bots list only;
  it never wakes by itself and never recommends retiring without a live check), and the unread-sender signal
  (unread_senders; a fresh, not-yet-raised sender makes the wake mode 'report').
Skipped (the existing JSON does not support them, or they need a live/human check):
  A3  event triggers: needs knowledge of which event sources exist for a polling prompt.
  A8  watchdog->producer merge: needs prompt semantics (routine A checks routine B).
  A11 progress chatter: run_all does not export burst lengths per bot.
  A12 long chats: covered partly by chat_mb in the snapshot; no surfaced-into-chat link per routine.
  A14 (overlapping descriptions half): needs description semantics. The idle half is implemented.
Approximations:
  A1  "completion text repeats the same idle reason" is not in the JSON; quiet % + run count only.
  A9  only name/slug one-shot patterns; "finite watch past its end condition" needs prompt semantics.
  A13 memory log size only (bytes via stat); lines-per-routine would need reading log contents.
  A16 error count within the runs.json window (last 20, unordered); errorKind is not exported.
  A10 uses wake bursts in the whole local window (no 14-day cut available) + unreadState lastViewedAt.
"""
import json, os, re, sys, glob, time, statistics, datetime as dt

HOME = os.path.dirname(os.path.abspath(__file__))
def P(rel): return rel if os.path.isabs(rel) else os.path.join(HOME, rel)
def load(path, default=None):
    try:
        with open(path) as f: return json.load(f)
    except Exception: return default
def dump(obj, path, compact=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(obj, f, separators=(',', ':')) if compact else json.dump(obj, f, indent=1)
    os.replace(tmp, path)

CFG = load(P('config/thresholds.json')) or load(P('config/thresholds.default.json'))
if not CFG: sys.exit('summarize: missing config/thresholds.json (run install.sh)')
TH, EST, PATHS = CFG['advice'], CFG['estimates'], CFG['paths']
OWNER = load(P(PATHS['owner_file']), {}) or {}
sys.path.insert(0, HOME)
from penny_common import apply_alert_level, calibration_factor
CFG = apply_alert_level(CFG, OWNER)   # owner alert level; 'normal' (default) leaves the config untouched
TH, EST, PATHS = CFG['advice'], CFG['estimates'], CFG['paths']
CAL = calibration_factor(CFG)          # Usage-page calibration for bot-work ~$; None until >= 2 observations
CAL_TAG = f", calibrated ×{CAL:g}" if CAL else ""
NOW = dt.datetime.now()
WEEK = NOW.strftime('%G-%V')           # file label, e.g. 2026-40
WEEK_LABEL = NOW.strftime('%G-W%V')     # display, e.g. 2026-W40
STALE_BEFORE = NOW - dt.timedelta(days=CFG['freshness']['stale_days'])
TZL = dt.datetime.now().astimezone().strftime('%Z') or 'local'   # box timezone label (e.g. CDT)

def parse_local(s):
    if not s or s == '-': return None
    for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M'):
        try: return dt.datetime.strptime(s[:19] if 'T' in s else s, fmt)
        except ValueError: pass
    return None
def from_ms(ms): return dt.datetime.fromtimestamp(ms / 1000) if ms else None
def from_utc(s):  # CLI session times are UTC
    try: return dt.datetime.strptime(s[:16], '%Y-%m-%dT%H:%M').replace(tzinfo=dt.timezone.utc).astimezone().replace(tzinfo=None)
    except Exception: return None
def fmt(d): return d.strftime('%Y-%m-%d %H:%M') if d else None
def redact(s):
    for t in OWNER.get('redact_terms', []):
        if t: s = re.sub(re.escape(t), '***', s, flags=re.I)
    return s

# ---------- cron expansion ----------
def cron_info(sched):
    if not sched or sched == 'webhook': return None
    parts = re.sub(r'CRON_TZ=\S+\s+', '', sched).split()
    if len(parts) != 5: return None
    def vals(field, lo, hi):
        out = set()
        for part in field.split(','):
            step = 1
            if '/' in part: part, step = part.split('/'); step = int(step)
            if part == '*': a, b = lo, hi
            elif '-' in part: a, b = map(int, part.split('-'))
            else: a = b = int(part)
            out.update(range(a, b + 1, step))
        return sorted(out)
    mi, hr, dom, mo, dow = parts
    mins, hours = vals(mi, 0, 59), vals(hr, 0, 23)
    dows = sorted({d % 7 for d in vals(dow, 0, 7)}) if dow != '*' else list(range(7))
    rpw = len(mins) * len(hours) * len(dows)
    if dom != '*':  # monthly-style schedule: days-of-month per year / weeks per year
        rpw = len(mins) * len(hours) * len(vals(dom, 1, 31)) * 12 / 52
    return dict(rpw=rpw, per_hour=len(mins), per_day=len(mins) * len(hours), hours=len(hours),
                days=len(dows), dows=set(dows), first_min=hours[0] * 60 + mins[0])

# ---------- load scan outputs ----------
fleet = load(P('fleet_scan.json'), {}) or {}
quiet = {r['slug']: r for r in (load(P('routine_quiet.json'), []) or [])}
cost = {r['slug']: r for r in (load(P('routine_cost.json'), []) or [])}
eng = {r['slug']: r for r in (load(P('routine_engagement.json'), []) or [])}
runs_raw = load(P('routine_runs.json'), []) or []
cli = load(P('cli_sessions.json'), []) or []

fired, tasks = {}, {}
for r in runs_raw:
    f = parse_local(r.get('fired'))
    if f: fired.setdefault(r['slug'], []).append(f)
    tasks.setdefault(r['slug'], []).append(r.get('task', 0))

routines, bots = {}, {}
for aid, b in fleet.items():
    u = b.get('unread') or {}
    mem_dir = os.path.join(PATHS['agents_dir'], aid, 'memory', 'log')
    mem = sorted(glob.glob(os.path.join(mem_dir, '*.md')))
    newest_bot = [d for d in [parse_local(b.get('last')), from_ms(u.get('lastActivityAt'))] if d]
    bots[aid] = dict(name=b.get('name'), chat_mb=round((b.get('tx_bytes') or 0) / 1e6, 1),
                     unread=dict(count=u.get('unreadCount'), last_activity=fmt(from_ms(u.get('lastActivityAt'))),
                                 last_viewed=fmt(from_ms(u.get('lastViewedAt')))) if u else None,
                     store_last=b.get('last'), mem_kb=round(os.path.getsize(mem[-1]) / 1e3, 1) if mem else 0,
                     n_routines=len(b.get('routines', [])))
    for x in b.get('routines', []):
        slug = x['slug']; ci = cron_info(x.get('schedule'))
        q = quiet.get(slug, {}); fl = sorted(fired.get(slug, []))
        newest = fl[-1] if fl else parse_local(x.get('last'))
        if newest: newest_bot.append(newest)
        n = q.get('n', 0)
        if ci: rpw = ci['rpw']
        elif fl and len(fl) > 1:  # event-driven: observed rate
            rpw = n / max((fl[-1] - fl[0]).total_seconds() / 86400, 1) * 7
        else: rpw = 0
        tl = tasks.get(slug, [])
        routines[slug] = dict(bot_id=aid, name=x.get('name'), enabled=bool(x.get('enabled')), schedule=x.get('schedule'),
                              cron=ci, runs=n, surfaced=q.get('surfaced', 0), quiet_pct=q.get('quiet_pct'),
                              calls_per_run=cost.get(slug, {}).get('calls_per_run'), rpw=round(rpw, 1),
                              newest=newest, first=fl[0] if fl else None, prompt_quiet=x.get('prompt_quiet'),
                              errors=sum(v for k, v in (x.get('status') or {}).items() if k != 'ok'),
                              window_runs=x.get('runs', 0), tasks_per_run=round(statistics.mean(tl), 2) if tl else 0,
                              bursts=eng.get(slug, {}).get('bursts', 0), replied24h=eng.get(slug, {}).get('replied24h', 0))
    bots[aid]['newest'] = max(newest_bot) if newest_bot else None
# deleted routines: seen in run transcripts, no definition on disk (snapshot only, never candidates)
for slug, q in quiet.items():
    if slug not in routines:
        fl = sorted(fired.get(slug, []))
        routines[slug] = dict(bot_id=None, deleted=True, runs=q['n'], surfaced=q['surfaced'], quiet_pct=q['quiet_pct'],
                              calls_per_run=cost.get(slug, {}).get('calls_per_run'), rpw=0, newest=fl[-1] if fl else None)

# ---------- CLI (live, exact) ----------
win = NOW - dt.timedelta(days=EST['cli_window_days'])
cli7 = [s for s in cli if (from_utc(s.get('updated', '')) or dt.datetime.min) >= win]
# repo roots: owner extras (local/owner.json cli_repo_roots) + generic config roots; '~' expands to the box user's home
REPO_ROOTS = [os.path.expanduser(x) for x in list(OWNER.get('cli_repo_roots') or []) + list(PATHS.get('cli_repo_roots') or []) if x]
def repo(cwd):
    c = (cwd or '').rstrip('/')
    for root in sorted(REPO_ROOTS, key=len, reverse=True):
        if c.startswith(root.rstrip('/') + '/'): return c[len(root.rstrip('/')) + 1:].split('/')[0]
    return os.path.basename(c) or '?'
by_repo = {}
for s in cli7:
    r = by_repo.setdefault(repo(s.get('cwd')), dict(tok=0, ticks=0, sessions=0, days={}))
    r['tok'] += s.get('tok', 0); r['ticks'] += s.get('ticks', 0); r['sessions'] += 1
    d = (from_utc(s.get('created', '')) or NOW).date().isoformat(); r['days'][d] = r['days'].get(d, 0) + 1
cli_tok = sum(s.get('tok', 0) for s in cli7); cli_usd = round(sum(s.get('ticks', 0) for s in cli7) * EST['cli_usd_per_tick'], 2)
cli_newest = max((from_utc(s.get('updated', '')) for s in cli if from_utc(s.get('updated', ''))), default=None)

# ---------- advice rules ----------
flags = []  # each: key, advice, bot_id, slug, signal, saved, change, newest
def add(key, adv, bot_id, slug, signal, saved, change, newest):
    flags.append(dict(key=key, advice=adv, bot_id=bot_id, slug=slug, signal=signal, saved=round(max(saved, 0), 1),
                      change=change, newest=newest))
live = {s: r for s, r in routines.items() if not r.get('deleted') and r.get('enabled')}
for slug, r in live.items():
    key = f"{r['bot_id']}|{slug}"; n, rpw = r['runs'], r['rpw']
    qp = r['quiet_pct'] if r['quiet_pct'] is not None else 0; qf = qp / 100
    surf_pct = 100 - qp if n else None; ci = r['cron']
    a = TH['A1']
    if n >= a['min_runs'] and qp >= a['min_quiet_pct']:
        add(key, 'A1', r['bot_id'], slug, f"{r['surfaced']}/{n} runs surfaced", rpw, 'pause until its trigger condition is true, then rearm', r['newest'])
    a = TH['A2']
    if ci and ci['per_hour'] > a['max_fires_per_hour']:
        add(key, 'A2', r['bot_id'], slug, f"{ci['per_hour']} fires/hour, {rpw:g} runs/wk", rpw - ci['hours'] * ci['days'] * a['max_fires_per_hour'],
            f"cut cadence to at most {a['max_fires_per_hour']}/hour", r['newest'])
    elif rpw >= a['min_runs_per_week'] and surf_pct is not None and surf_pct <= a['max_surfaced_pct']:
        add(key, 'A2', r['bot_id'], slug, f"{rpw:g} runs/wk, {surf_pct}% surfaced", rpw - a['target_runs_per_week'],
            f"cut cadence to about {a['target_runs_per_week']} runs/wk", r['newest'])
    a = TH['A6']
    if r.get('prompt_quiet') is False and n >= a['min_runs']:
        surf_wk = rpw * (r['surfaced'] / n)
        if surf_wk >= a['min_surfaced_per_week']:
            add(key, 'A6', r['bot_id'], slug, f"no quiet rule, {surf_wk:.0f} surfaced/wk", surf_wk * EST['a6_noise_fraction'],
                'add a stay-quiet-when-nothing-new rule', r['newest'])
        elif qp >= a['quiet_heavy_pct']:
            add(key, 'A6', r['bot_id'], slug, f"no quiet rule, {qp}% quiet runs", rpw * qf * EST['gate_saving_fraction'],
                'add an explicit quiet rule plus a cheap pre-check before the deep dive', r['newest'])
    a = TH['A7']
    if (r['calls_per_run'] or 0) >= a['min_calls_per_run'] and qp >= a['min_quiet_pct']:
        add(key, 'A7', r['bot_id'], slug, f"{r['calls_per_run']} calls/run, {qp}% quiet", rpw * qf * EST['gate_saving_fraction'],
            'add a cheap shell/API/diff gate that ends the run early when unchanged', r['newest'])
    a = TH['A9']
    if re.search(a['one_shot_regex'], f"{slug} {r.get('name') or ''}") and r['first'] and (NOW - r['first']).days >= a['min_age_days']:
        add(key, 'A9', r['bot_id'], slug, f"one-shot/dated name, first ran {r['first']:%b %d}", rpw, 'delete this finished one-time routine', r['newest'])
    a = TH['A10']
    if r['bursts'] >= a['min_bursts'] and r['replied24h'] <= a['max_replies']:
        add(key, 'A10', r['bot_id'], slug, f"{r['replied24h']}/{r['bursts']} wake bursts got a reply", rpw, 'pause it, or ask the owner if it is still wanted', r['newest'])
    else:
        lv = (bots[r['bot_id']].get('unread') or {}).get('last_viewed')
        lvd = parse_local(lv)
        if lvd and r['surfaced'] and r['newest'] and r['newest'] > lvd and (r['newest'] - lvd).days >= a['not_opened_days']:
            add(key, 'A10', r['bot_id'], slug, f"bot not opened for {(r['newest'] - lvd).days}d while surfacing", rpw, 'pause it until the owner reads this bot again', r['newest'])
    a = TH['A15']
    if r['tasks_per_run'] > a['max_tasks_per_run']:
        add(key, 'A15', r['bot_id'], slug, f"{r['tasks_per_run']} subagents/run", rpw * (r['tasks_per_run'] - a['max_tasks_per_run']) * EST['subagent_run_equiv'],
            f"cap fan-out at {a['max_tasks_per_run']} subagents per run", r['newest'])
    a = TH['A16']
    if r['errors'] >= a['min_errors_in_window']:
        add(key, 'A16', r['bot_id'], slug, f"{r['errors']} errors in last {r['window_runs']} runs", rpw * r['errors'] / max(r['window_runs'], 1),
            'fix the failure or shift the slot a few minutes', r['newest'])

# A4 digest fold: >=N daily-or-less routines on one bot starting within window_min, low reply rate
a = TH['A4']
by_bot = {}
for slug, r in live.items():
    ci = r['cron']
    if ci and ci['per_day'] <= a['max_fires_per_day']: by_bot.setdefault(r['bot_id'], []).append((ci['first_min'], slug))
for bid, L in by_bot.items():
    L.sort(); best = []
    for i in range(len(L)):
        grp = [x for x in L[i:] if x[0] - L[i][0] <= a['window_min']]
        grp = [x for x in grp if live[x[1]]['cron']['dows'] & live[grp[0][1]]['cron']['dows']]
        if len(grp) > len(best): best = grp
    if len(best) < a['min_routines']: continue
    slugs = [s for _, s in best]
    bursts = sum(live[s]['bursts'] for s in slugs); rep = sum(live[s]['replied24h'] for s in slugs)
    if bursts == 0 and not a['no_bursts_counts_as_unreplied']: continue
    if bursts and 100 * rep / bursts >= a['max_reply24h_pct']: continue
    rp = [live[s]['rpw'] for s in slugs]
    t0, t1 = best[0][0], best[-1][0]
    add(f"{bid}|{slugs[0]}", 'A4', bid, f"{slugs[0]}+{len(slugs) - 1}",
        f"{len(slugs)} routines {t0 // 60:02d}:{t0 % 60:02d}-{t1 // 60:02d}:{t1 % 60:02d}, replies {rep}/{bursts}",
        sum(rp) - max(rp), 'fold into one digest at the first slot, quiet if nothing new', max(live[s]['newest'] for s in slugs if live[s]['newest']))

# A5 duplicate cron across bots: start within window_min + prompt keyword overlap (prompts read in memory only)
a = TH['A5']
def words(bid, slug):
    j = load(os.path.join(PATHS['agents_dir'], bid, 'automations', slug, 'automation.json'), {}) or {}
    return {w for w in re.findall(r'[a-z0-9]+', (j.get('prompt') or '').lower()) if len(w) >= a['min_word_len']}
L = [(s, r) for s, r in live.items() if r['cron']]
for i in range(len(L)):
    for j in range(i + 1, len(L)):
        (s1, r1), (s2, r2) = L[i], L[j]
        if r1['bot_id'] == r2['bot_id'] or not (r1['cron']['dows'] & r2['cron']['dows']): continue
        if abs(r1['cron']['first_min'] - r2['cron']['first_min']) > a['window_min']: continue
        w1, w2 = words(r1['bot_id'], s1), words(r2['bot_id'], s2)
        if min(len(w1), len(w2)) < a['min_prompt_words']: continue  # tiny prompts inflate overlap
        ov = round(100 * len(w1 & w2) / min(len(w1), len(w2)))
        if ov < a['min_keyword_overlap_pct']: continue
        drop, keep = ((s1, s2) if (r1['quiet_pct'] or 0) >= (r2['quiet_pct'] or 0) else (s2, s1))
        rd = live[drop]
        add(f"{rd['bot_id']}|{drop}", 'A5', rd['bot_id'], drop, f"same slot as {keep} on {fleet[live[keep]['bot_id']]['name']}, {ov}% keyword overlap",
            rd['rpw'], f"remove; keep {keep} as the single owner", rd['newest'])

# A13 memory log bloat (bot level, size only)
for bid, b in bots.items():
    if b['mem_kb'] > TH['A13']['max_log_kb'] and b['n_routines']:
        add(f"{bid}|memory-log", 'A13', bid, 'memory-log', f"memory log {b['mem_kb']:g} KB", 0,
            'log only changes from routines; roll up idle notes', b['newest'])

# A17 CLI repo heavy (live)
a = TH['A17']; repo_bots = OWNER.get('cli_repo_bots', {})
name2id = {b.get('name'): aid for aid, b in fleet.items()}
def repo_bot(rp):
    """Owner repo map: longest matching prefix -> (bot name, bot id). Values are a name or {bot, bot_id}."""
    for k in sorted(repo_bots, key=len, reverse=True):
        if rp.startswith(k):
            v = repo_bots[k]
            return (v, name2id.get(v)) if isinstance(v, str) else (v.get('bot'), v.get('bot_id') or name2id.get(v.get('bot')))
    return (None, None)
def is_resolved(rp, s_=None):
    """Owner resolved_repos: {repo, note[, before: YYYY-MM-DD]}. With 'before', only sessions created before that local date are resolved."""
    for r_ in OWNER.get('resolved_repos', []):
        x = r_.get('repo') if isinstance(r_, dict) else r_
        if not x or not rp.startswith(x): continue
        before = r_.get('before') if isinstance(r_, dict) else None
        if not before: return True
        if s_ is not None and (from_utc(s_.get('created', '')) or NOW).date().isoformat() < before: return True
    return False
adv_repo = {}   # A17 input: last-window sessions minus resolved ones (spend is still attributed in the tables)
for s_ in cli7:
    rp_ = repo(s_.get('cwd'))
    if is_resolved(rp_, s_): continue
    r = adv_repo.setdefault(rp_, dict(tok=0, ticks=0, sessions=0, days={}))
    r['tok'] += s_.get('tok', 0); r['ticks'] += s_.get('ticks', 0); r['sessions'] += 1
    d_ = (from_utc(s_.get('created', '')) or NOW).date().isoformat(); r['days'][d_] = r['days'].get(d_, 0) + 1
for rp, r in adv_repo.items():
    busiest = max(r['days'].values()) if r['days'] else 0
    if r['tok'] > a['max_tokens_per_week'] or busiest > a['max_sessions_per_day']:
        bname, bid_ = repo_bot(rp)
        add(f"cli|{rp}", 'A17', bid_, rp, f"{r['tok'] / 1e6:.1f}M CLI tokens/{EST['cli_window_days']}d, {r['sessions']} sessions, ${r['ticks'] * EST['cli_usd_per_tick']:.2f}",
            r['sessions'] - EST['cli_batch_target_sessions_per_week'], 'batch CLI coding into fewer, larger sessions', cli_newest)
        flags[-1]['bot_name'] = bname or f"CLI repo {rp}"

# A18 retime to reading: surfaced runs read hours later -> move the routine to the owner's typical reply hour
a = TH['A18']
for slug, r in live.items():
    e = eng.get(slug) or {}; ci = r['cron']
    if not ci or ci['per_day'] > a['max_fires_per_day']: continue   # event-driven or frequent: retiming doesn't apply
    pairs = [(d, h) for d, h in zip(e.get('delays_min', []), e.get('reply_hours', [])) if d <= a['max_delay_h'] * 60]
    if len(pairs) < a['min_samples']: continue
    med_h = statistics.median(d for d, _ in pairs) / 60
    if med_h < a['min_delay_h']: continue
    rh = int(round(statistics.median(h for _, h in pairs)))
    if abs(rh - ci['first_min'] // 60) < a['min_shift_h']: continue   # already fires near the reading hour; slow reads are an A10 question
    dow = re.sub(r'CRON_TZ=\S+\s+', '', r['schedule']).split()[4]
    fire = f"{ci['first_min'] // 60:02d}:{ci['first_min'] % 60:02d}"
    add(f"{r['bot_id']}|{slug}", 'A18', r['bot_id'], slug, f"fires {fire}, median read delay {med_h:.1f}h over {len(pairs)}, owner usually replies ~{rh:02d}:00",
        0, f"move to ~{rh:02d}:00 (cron `0 {rh} * * {dow}`) or fold into a digest at that hour", r['newest'])

# A19 heavy model / effort on small CLI sessions (bot and routine model choice is not visible locally)
a = TH['A19']; win19 = NOW - dt.timedelta(days=a['window_days'])
by19 = {}
for s_ in cli:
    if (from_utc(s_.get('updated', '')) or dt.datetime.min) < win19 or not s_.get('tok') or is_resolved(repo(s_.get('cwd')), s_): continue
    by19.setdefault(repo(s_.get('cwd')), []).append(s_)
for rp, L in by19.items():
    if is_resolved(rp) or len(L) < a['min_sessions']: continue
    med_tok = statistics.median(x['tok'] for x in L)
    heavy = [x for x in L if (x.get('effort') or '').lower() in a['heavy_efforts'] or (x.get('model') in a['heavy_models'])
             or any(m in a['heavy_models'] for m in x.get('chat_models') or [])]
    share = len(heavy) / len(L)
    if med_tok >= a['small_session_tokens'] or share < a['min_heavy_share']: continue
    bname, bid_ = repo_bot(rp); per_wk = len(L) / a['window_days'] * 7
    usd_ = sum(x.get('ticks', 0) for x in L) * EST['cli_usd_per_tick']
    efforts = sorted({x.get('effort') for x in heavy if x.get('effort')})
    add(f"cli|{rp}", 'A19', bid_, rp, f"{len(heavy)}/{len(L)} sessions at {'/'.join(efforts) or 'heavy model'} effort, median {med_tok / 1e3:.0f}k tok, ${usd_:.2f}/{a['window_days']}d",
        per_wk * a['effort_saving_fraction'], 'use a lower reasoning effort (or a lighter model) for small sessions in this repo',
        max(from_utc(x.get('updated', '')) for x in L))
    flags[-1]['bot_name'] = bname or f"CLI repo {rp}"

# A20 chat bloat: long main chat that still gets turns or routine wakes
a = TH['A20']; dud0 = load(P('daily_usage.json'), {}) or {}; DU0 = CFG['daily_usage']
for bid, b in bots.items():
    if b['chat_mb'] < a['min_chat_mb']: continue
    db_ = (dud0.get('bots') or {}).get(bid) or {}
    ce = parse_local(db_.get('chat_coverage_end'))
    turns_wk = 0
    if ce:
        lo = (ce - dt.timedelta(days=6)).date().isoformat(); hi = ce.date().isoformat()
        turns_wk = sum(v[0] for d, v in (db_.get('est_chat') or {}).items() if lo <= d <= hi)
    wakes_wk = sum(r['rpw'] * r['surfaced'] / r['runs'] for r in live.values() if r['bot_id'] == bid and r['runs'])
    if turns_wk < a['min_turns_per_week'] and wakes_wk < a['min_wakes_per_week']: continue
    chat_tok = b['chat_mb'] * 1e6 / DU0['bytes_per_token']
    per_call = min(chat_tok, DU0['context_cap_tokens'])
    tok_wk = (turns_wk + wakes_wk) * (db_.get('calls_per_turn') or 1) * max(per_call - a['reset_tokens'], 0)
    add(f"{bid}|chat-bloat", 'A20', bid, 'main-chat', f"chat {b['chat_mb']:g} MB, {turns_wk} turns + {wakes_wk:.0f} wakes/wk, ~{tok_wk / 1e6:.0f}M replay tok/wk (~${tok_wk * EST['bot_usd_per_m_tokens'] / 1e6:.0f})",
        (turns_wk + wakes_wk) * a['turn_saving_fraction'], 'summarize and reset the chat, or move its routines to a fresh short-chat bot', ce)

# A21 always-on load: text carried on every turn (instructions + memory + visible platform sections), lengths only
a = TH['A21']; ao = load(P('always_on.json'), {}) or {}; CPT_ = a['chars_per_token']
PRICE = EST['bot_usd_per_m_tokens']; ao_rows = []
for bid, x in (ao.get('bots') or {}).items():
    if bid not in bots: continue
    sec = (x.get('snap') or {}).get('sections') or {}
    instr_t = x['instr_chars'] / CPT_
    mem_t = (sec.get('memory') or x['mem_chars']) / CPT_
    plat_t = sum(sec.get(k, 0) for k in a['platform_sections']) / CPT_   # always shown for context
    own_t = instr_t + mem_t
    load_t = own_t + (plat_t if a['count_platform_sections'] else 0)     # what is flagged and priced
    db_ = (dud0.get('bots') or {}).get(bid) or {}
    ce = parse_local(db_.get('chat_coverage_end')); turns_wk = 0
    if ce:
        lo = (ce - dt.timedelta(days=6)).date().isoformat(); hi = ce.date().isoformat()
        turns_wk = sum(v[0] for d, v in (db_.get('est_chat') or {}).items() if lo <= d <= hi)
    prompts = [(sl, n / CPT_, live[sl]['rpw'], live[sl].get('calls_per_run') or 1) for sl, n in (x.get('routine_prompt_chars') or {}).items() if sl in live]
    big = max(prompts, key=lambda t: t[1], default=None)
    fires = sum(t[2] for t in prompts)
    calls_wk = turns_wk * (db_.get('calls_per_turn') or 1)
    resend = load_t * calls_wk + sum(t[1] * t[2] * t[3] for t in prompts)
    usd_wk = resend * PRICE / 1e6
    ao_rows.append(dict(bid=bid, name=x.get('name'), load=load_t, own=own_t, instr=instr_t, mem=mem_t if (sec.get('memory') or x['mem_chars']) else None,
                        plat=plat_t, snap=(x.get('snap') or {}).get('at'), big=big, usd=usd_wk, turns=turns_wk, fires=fires))
    heavy = [t for t in prompts if t[1] > a['max_routine_prompt_tokens'] and t[2] >= a['min_fires_per_week']]
    if load_t <= a['max_always_on_tokens'] and not heavy: continue
    excess = min(max(load_t - a['max_always_on_tokens'], 0), own_t) / load_t if load_t else 0
    if heavy: excess = max(excess, max(1 - a['max_routine_prompt_tokens'] / t[1] for t in heavy))
    sig = (f"~{load_t / 1e3:.1f}k owner tok/turn (instr {instr_t / 1e3:.1f}k + memory {mem_t / 1e3:.1f}k; app sections {plat_t / 1e3:.1f}k not counted)" if not a['count_platform_sections'] else
           f"~{load_t / 1e3:.1f}k tok/turn (instr {instr_t / 1e3:.1f}k + memory {mem_t / 1e3:.1f}k + app sections {plat_t / 1e3:.1f}k)"
           + (f", prompt {big[1] / 1e3:.1f}k×{big[2]:g}/wk" if big else "") + f", ~${usd_wk:.2f}/wk est.")
    add(f"{bid}|always-on", 'A21', bid, 'always-on', sig, (turns_wk + fires) * excess, a['fix'], bots[bid].get('newest'))

# ---------- one candidate per routine (best advice), staleness, week-over-week ----------
hist_dir = P(PATHS['history_dir'])
prev_files = sorted(f for f in glob.glob(os.path.join(hist_dir, '*.json')) if os.path.basename(f)[:-5] < WEEK)
prev = load(prev_files[-1], {}) if prev_files else {}
prev_saved = {c['key']: c['saved'] for c in prev.get('flags', [])}
worse = CFG['wake']['worse_factor']
cands = {}
for f in sorted(flags, key=lambda f: -f['saved']):
    c = cands.get(f['key'])
    if c: c['also'].append(f['advice']); continue
    c = dict(f); c['also'] = []
    c['stale'] = bool(not f['newest'] or f['newest'] < STALE_BEFORE)
    p = prev_saved.get(f['key'])
    c['new_or_worse'] = p is None or (p > 0 and f['saved'] >= worse * p)
    cands[f['key']] = c

# ledger: raised / skipped (owner-local). Owner "handled" items are seeded as skipped with today's numbers.
led_path = P(PATHS['ledger_file'])
ledger = load(led_path, {}) or {}
ledger.setdefault('raised', {}); ledger.setdefault('skipped', {})
excl = set(OWNER.get('exclude_bot_ids', []))
def handled(c):
    for h in OWNER.get('handled', []):
        if h.get('bot_id') == c['bot_id'] and h.get('slug') in ('*', c['slug'], c['slug'].split('+')[0]): return h
    return None
seeded = 0
for c in cands.values():
    h = handled(c)
    if h and c['key'] not in ledger['skipped']:
        ledger['skipped'][c['key']] = dict(week=WEEK_LABEL, date=NOW.strftime('%Y-%m-%d'), advice=c['advice'],
                                           saved=float(c['saved'] or 0), reason='owner-handled'); seeded += 1
# Heal legacy raised/skipped entries that were written without a numeric saved (missing saved used to
# make max(..., 1e-9) treat every skip as always eligible). Fill saved from today's candidate so the
# item stays suppressed until it is worse_factor × this baseline; never invent a positive floor.
healed = 0
for c in cands.values():
    for bucket in ('raised', 'skipped'):
        e = ledger[bucket].get(c['key'])
        if e is None or 'saved' in e: continue
        e['saved'] = float(c['saved'] or 0)
        e.setdefault('date', e.get('date') or NOW.strftime('%Y-%m-%d'))
        e.setdefault('week', WEEK_LABEL); healed += 1
if seeded or healed or not os.path.exists(led_path): dump(ledger, led_path)

def bot_name(c): return c.get('bot_name') or (fleet.get(c['bot_id'], {}).get('name') if c['bot_id'] else None) or '?'
fresh, stale, suppressed = [], [], 0
for c in sorted(cands.values(), key=lambda c: -c['saved']):
    if c['bot_id'] in excl: continue
    prior = ledger['raised'].get(c['key']) or ledger['skipped'].get(c['key'])
    # Raised and skipped share one rule: re-raise only when est. savings is worse_factor × the ledger
    # baseline. Missing saved is 0 (not a 1e-9 floor): with no positive baseline the item stays
    # suppressed — same as a skip recorded at saved=0. Docs: "won't be raised again unless 2× worse."
    if c['stale']:
        c['eligible'] = False
    elif prior is None:
        c['eligible'] = True
    else:
        prior_saved = prior.get('saved')
        if prior_saved is None: prior_saved = 0
        try: prior_saved = float(prior_saved)
        except (TypeError, ValueError): prior_saved = 0.0
        c['eligible'] = prior_saved > 0 and c['saved'] >= worse * prior_saved
    if prior and not c['eligible'] and not c['stale']: suppressed += 1
    (stale if c['stale'] else fresh).append(c)

WHY = {   # one plain sentence per advice: what looked wasteful and why it costs money (template text, no owner data)
    'A1': "Most runs find nothing to report, yet each run still pays for a full model session.",
    'A2': "It fires far more often than its results change, so most runs pay to repeat the same work.",
    'A4': "Several routines wake one bot at nearly the same time; each pays its own setup and chat replay, one digest would pay once.",
    'A5': "Two bots run a near-identical job in the same slot, so the same work is paid for twice.",
    'A6': "With no stay-quiet rule the routine digs in and posts every run, paying for output nobody needs.",
    'A7': "Runs make many model calls even when nothing changed; a cheap pre-check could end them early.",
    'A9': "A dated or one-time routine is still running after its purpose ended, paying for runs nobody uses.",
    'A10': "Its messages mostly go unread or unanswered, so the runs cost money without being used.",
    'A13': "A large memory log is reloaded into context, so every run pays for those extra tokens.",
    'A15': "Each run spawns many subagents, and every subagent is a separate paid model session.",
    'A16': "Runs keep failing, and failed runs still bill for the tokens used before the error.",
    'A17': "Heavy CLI use spread over many sessions re-reads the code each time, and those repeated tokens are billed.",
    'A18': "Results sit unread for hours and are often stale by then; running near reading time avoids repeat runs.",
    'A19': "Small sessions run at a high reasoning effort or heavy model, which costs more per task than they need.",
    'A21': "This bot re-sends a large block of instructions, memory and routine prompt text on every turn and run, so every call pays for it again.",
    'A20': "Every turn re-sends a very long chat; old history gets summarized, so the $ is an upper bound. Save learnings to memory, a skill or a file before any reset.",
}
def compact(c, full=True):
    d = dict(bot=redact(bot_name(c)), bot_id=c['bot_id'], routine=redact(c['slug']), advice=c['advice'],
             signal=redact(c['signal']), est_runs_wk_saved=c['saved'])
    if full:
        d.update(change=c['change'], why=WHY.get(c['advice'], ''), also=c['also'], stale=c['stale'], new_or_worse=c['new_or_worse'],
                 already_raised=not c['eligible'], newest_run=fmt(c['newest']), key=c['key'])
    else:
        d.update(stale=True, newest_run=fmt(c['newest']))
    return d

top_n, sw_n = CFG['output']['top_n'], CFG['output']['stale_watch_n']
top = fresh[:top_n]
eligible = [c for c in top if c['eligible']]

# ---------- A14 idle bots (decommission candidates; local signals, confirm live) ----------
IB = CFG['idle_bots']
keep = set(OWNER.get('keep_bots', [])) | set(excl) | {OWNER.get('doge_bot_id')}
cli_recent_win = NOW - dt.timedelta(days=IB['cli_recent_days'])
cli_by_bot, cli_last = {}, {}   # recent session count + newest CLI session per bot (proof of use)
for s_ in cli:
    up = from_utc(s_.get('updated', '')) or dt.datetime.min
    bid_ = repo_bot(repo(s_.get('cwd')))[1]
    if not bid_: continue
    if up > cli_last.get(bid_, dt.datetime.min): cli_last[bid_] = up
    if up >= cli_recent_win: cli_by_bot[bid_] = cli_by_bot.get(bid_, 0) + 1
def days(d): return (NOW - d).days if d else None
# Guardrail (template): an idle / "unviewed" claim needs a FRESH local store (synced within stale_days) AND
# at least min_signals agreeing local signals (owner view stamp, last human message) AND no recent coding
# (CLI) mapped to the bot. Recent use on any signal clears the bot. A stale store is never judged: it goes to
# idle_unconfirmed (names only, no day counts), because its unreadState and transcript can lag live use by weeks.
REQ_FRESH = IB.get('require_fresh_store', True); MIN_SIG = IB.get('min_signals', 2)
idle, idle_unknown, idle_warn, idle_unconfirmed = [], [], [], []
active_by_cli = 0
for aid, b in fleet.items():
    if aid in keep or b.get('name') in keep: continue
    u = b.get('unread') or {}
    viewed = from_ms(u.get('lastViewedAt')); human = from_ms(b.get('lastHumanMs'))
    du, dh = days(viewed), days(human)
    dc = days(cli_last.get(aid))
    bots[aid].update(days_unviewed=du, days_since_human_msg=dh)
    if not b.get('entries') or not u or not viewed:   # empty local store or no unreadState: can't judge
        idle_unknown.append(b.get('name')); continue
    dh_eff = dh if dh is not None else float('inf')   # no human message ever in the local store
    unused = min(du, dh_eff)
    if dc is not None and dc < IB['warn_days']:       # recent coding for this bot = the owner is using it
        if unused >= IB['warn_days']: active_by_cli += 1
        continue
    if unused < IB['warn_days']: continue
    newest = bots[aid]['newest']
    stale_store = bool(not newest or newest < STALE_BEFORE)
    agree = sum(1 for d_ in (du, dh) if d_ is not None and d_ >= IB['min_days_unused'])
    if (REQ_FRESH and stale_store) or (unused >= IB['min_days_unused'] and agree < MIN_SIG):
        idle_unconfirmed.append(redact(b.get('name') or '?')); continue
    if unused >= IB['min_days_unused']:
        prior = ledger.setdefault('idle', {}).get(aid)
        idle.append(dict(name=redact(b.get('name') or '?'), id=aid, days_unviewed=du, days_since_human_msg=dh,
                         has_routines=any(x.get('enabled') for x in b.get('routines', [])),
                         recent_cli_sessions=cli_by_bot.get(aid, 0), data_missing=False,
                         store_stale=stale_store, signals=agree, confirm_live=True,
                         raise_=prior is None or unused >= IB['reraise_factor'] * max(prior.get('idle_days', 0), 1),
                         _unused=unused))
    else:
        idle_warn.append(f"{redact(b.get('name') or '?')} ({int(unused)}d)")
idle.sort(key=lambda x: (not x['raise_'], -x['_unused']))
for x in idle:
    x['raise'] = x.pop('raise_'); un = x.pop('_unused')
    x['idle_days'] = un if un != float('inf') else x['days_unviewed']
idle_pending = [dict(id=x['id'], idle_days=x['idle_days']) for x in idle if x['raise']]

# ---------- unread sender: bot keeps messaging the owner while its chat goes unopened ----------
US = CFG['unread_sender']; unread_senders = []; unread_unconfirmed = 0
for aid, b in fleet.items():
    u = b.get('unread')
    if aid in keep or b.get('name') in keep or not u or not u.get('lastViewedAt'): continue  # skip missing unreadState
    unseen = b.get('sendsAfterView') or 0; dv = days(from_ms(u.get('lastViewedAt')))
    if unseen < US['min_unseen_msgs'] or dv is None or dv < US['min_days_unviewed']: continue
    last_send = from_ms(b.get('lastSendMs'))
    prior = ledger.setdefault('unread', {}).get(aid)
    stale_ = not last_send or last_send < STALE_BEFORE
    if stale_ and IB.get('require_fresh_store', True): unread_unconfirmed += 1; continue   # stale unread stamp: don't state days_unviewed
    unread_senders.append(dict(name=redact(b.get('name') or '?'), id=aid, unseen_msgs=unseen, days_unviewed=dv,
                               has_routines=any(x.get('enabled') for x in b.get('routines', [])), stale=stale_,
                               raise_=not stale_ and (prior is None or unseen >= US['reraise_factor'] * max(prior.get('unseen_msgs', 0), 1))))
unread_senders.sort(key=lambda x: (not x['raise_'], -x['unseen_msgs']))
for x in unread_senders: x['raise'] = x.pop('raise_')
unread_pending = [dict(id=x['id'], unseen_msgs=x['unseen_msgs']) for x in unread_senders if x['raise']]

# ---------- snapshot ----------
def iso(d): return fmt(d) if isinstance(d, dt.datetime) else d
snap = dict(week=WEEK_LABEL, generated_at=fmt(NOW),
            routines={s: dict(bot_id=r.get('bot_id'), enabled=r.get('enabled'), deleted=r.get('deleted', False), runs=r['runs'],
                              surfaced=r['surfaced'], quiet_pct=r['quiet_pct'], calls_per_run=r['calls_per_run'],
                              runs_wk=r['rpw'], newest=iso(r['newest'])) for s, r in sorted(routines.items())},
            bots={aid: dict(name=b['name'], chat_mb=b['chat_mb'], mem_kb=b['mem_kb'], unread=b['unread'], newest=iso(b['newest']),
                            days_unviewed=b.get('days_unviewed'), days_since_human_msg=b.get('days_since_human_msg'))
                  for aid, b in sorted(bots.items())},
            cli_7d=dict(tokens=cli_tok, usd=cli_usd, sessions=len(cli7),
                        by_repo={k: dict(tok=v['tok'], sessions=v['sessions'], usd=round(v['ticks'] * EST['cli_usd_per_tick'], 2)) for k, v in by_repo.items()}),
            flags=[dict(key=c['key'], advice=c['advice'], saved=c['saved'], stale=c['stale']) for c in cands.values()])
if prev:
    pr = prev.get('routines', {})
    snap['delta_vs'] = prev.get('week')
    snap['routine_deltas'] = {s: dict(runs=r['runs'] - pr[s].get('runs', 0), runs_wk=round(r['runs_wk'] - (pr[s].get('runs_wk') or 0), 1))
                              for s, r in snap['routines'].items() if s in pr and (r['runs'] != pr[s].get('runs') or r['runs_wk'] != pr[s].get('runs_wk'))}
dump(snap, os.path.join(hist_dir, WEEK + '.json'))

# ---------- per-day consumption table (CLI exact, chat+routine estimates) ----------
DUC = CFG['daily_usage']; dud = load(P('daily_usage.json'), {}) or {}
today = NOW.date(); monday = today - dt.timedelta(days=today.weekday())
wk_start = monday - dt.timedelta(days=7) if DUC['week'] == 'last_full' else monday
days7 = [(wk_start + dt.timedelta(days=i)).isoformat() for i in range(7)]
future = {d for d in days7 if d > today.isoformat()}
cli_rows = {}
for d, cwd, tok, ticks in dud.get('cli', []):
    if d not in days7: continue
    name = repo_bot(repo(cwd))[0] or 'Unmapped CLI'
    row = cli_rows.setdefault(name, [[0, 0] for _ in days7]); c = row[days7.index(d)]; c[0] += tok; c[1] += ticks
if 'Unmapped CLI' in cli_rows and not any(c[0] for c in cli_rows['Unmapped CLI']): del cli_rows['Unmapped CLI']   # only empty folders left (0 tokens)
for name, row in cli_rows.items():
    for i, d in enumerate(days7): row[i] = None if d in future else [row[i][0], row[i][1] * EST['cli_usd_per_tick']]
# ---------- ask before reporting large unmapped coding (recommend-only; Penny never auto-maps) ----------
UM = CFG.get('unmapped_cli') or {}
_um_tok, _um_ticks, _all_ticks = {}, {}, 0
for d, cwd, tok, ticks in dud.get('cli', []):
    if d not in days7: continue
    _all_ticks += ticks
    if not repo_bot(repo(cwd))[0] and tok:
        r_ = repo(cwd); _um_tok[r_] = _um_tok.get(r_, 0) + tok; _um_ticks[r_] = _um_ticks.get(r_, 0) + ticks
um_usd = sum(_um_ticks.values()) * EST['cli_usd_per_tick']; all_usd = _all_ticks * EST['cli_usd_per_tick']
um_share = um_usd / all_usd if all_usd else 0
unmapped_ask, unmapped_pending = None, []
# Normal flow: coding bots claim their own folder by messaging Penny DOGE once (claim_repo.py), so the owner is NOT
# asked about every new repo. Ask the owner only when unmapped spend is large AND still unclaimed after
# claim_grace_days, or when two bots claimed the same repo (cli_repo_conflicts).
_first = {}
for d, cwd, tok, ticks in dud.get('cli', []):
    if tok: r_ = repo(cwd); _first[r_] = min(_first.get(r_, d), d)
_grace = (today - dt.timedelta(days=UM.get('claim_grace_days', 3))).isoformat()
_bare = {'workspace', 'home', 'tmp', 'root', 'box'} | {os.path.basename(os.path.expanduser(r_).rstrip('/')) for r_ in REPO_ROOTS}
_unclaimed = {r_: t for r_, t in _um_tok.items() if _first.get(r_, '9999') <= _grace}   # older than the grace period
uc_usd = sum(_um_ticks[r_] for r_ in _unclaimed) * EST['cli_usd_per_tick']; uc_share = uc_usd / all_usd if all_usd else 0
conflicts = [dict(repo=redact(c.get('repo', '?')), mapped_to=redact(c.get('mapped_to') or '?'), claimed_by=redact(c.get('claimed_by') or '?'))
             for c in OWNER.get('cli_repo_conflicts', []) if isinstance(c, dict)]
large = bool(_unclaimed) and (uc_usd >= UM.get('min_usd', 10) or (uc_share >= UM.get('min_share', 0.25) and uc_usd >= UM.get('min_usd_floor', 5)))
if UM.get('enabled', True) and (large or conflicts):
    led_um = ledger.setdefault('unmapped', {})
    repos_ = sorted(_unclaimed, key=lambda r_: -_unclaimed[r_]) if large else []
    for r_ in repos_:
        pr_ = led_um.get(r_)
        if pr_ is None or _unclaimed[r_] >= UM.get('reraise_factor', 2.0) * max(pr_.get('tok', 0), 1): unmapped_pending.append(dict(repo=r_, tok=_unclaimed[r_]))
    for c in conflicts:
        k_ = 'conflict:' + c['repo'] + ':' + c['claimed_by']
        if k_ not in led_um: unmapped_pending.append(dict(repo=k_, tok=0))
    unmapped_ask = dict(usd=round(uc_usd, 2), share=round(uc_share, 2), new=bool(unmapped_pending),
                        repos=[dict(repo=redact(r_), tok_m=round(_unclaimed[r_] / 1e6, 1), usd=round(_um_ticks[r_] * EST['cli_usd_per_tick'], 2),
                                    **({'bare': True} if r_ in _bare else {})) for r_ in repos_[:UM.get('max_listed', 5)]],
                        more=max(len(repos_) - UM.get('max_listed', 5), 0), conflicts=conflicts[:5],
                        why="Why I flagged it: this coding spend isn't linked to a bot yet, so the report can't show who ran it." if large else
                            "Why I flagged it: two bots claimed the same folder, so I can't tell who ran it.",
                        ask="No bot has claimed these folders. Which bot owns each? Coding bots normally claim their own folder; I never guess."
                            if large else "Which bot owns this folder? I left the existing mapping unchanged.")
est_rows, no_fresh, est_week = {}, [], {}
def est_row(b, days):
    """Per-day [calls, replay_tok(, 1=partial)] or None where the bot has no local data for that day (never 0)."""
    cs, ce = (b.get('chat_coverage_start') or '')[:10], (b.get('chat_coverage_end') or '')[:10]
    rs, re_ = (b.get('routine_coverage_start') or '')[:10], (b.get('routine_coverage_end') or '')[:10]
    row = []
    for d in days:
        chat_ok = bool(ce) and (not cs or d >= cs) and d <= ce; rout_ok = bool(re_) and (not rs or d >= rs) and d <= re_
        if d > today.isoformat() or d < (dud.get('since') or '') or not (chat_ok or rout_ok): row.append(None); continue  # outside the scanned window = n/a
        ch = b['est_chat'].get(d, [0, 0, 0]) if chat_ok else [0, 0, 0]; ro = b['est_routine'].get(d, [0, 0, 0]) if rout_ok else [0, 0, 0]
        cell = [ch[1] + ro[1], ch[2] + ro[2]]
        if (not rout_ok and b.get('has_routines')) or (not chat_ok and ce): cell.append(1)   # partial: one source missing that day
        row.append(cell)
    return row
for aid, b in (dud.get('bots') or {}).items():
    if aid in excl: continue
    row = est_row(b, days7)
    est_week[b.get('name') or aid] = row
    if all(c is None for c in row):
        if b.get('est_chat') or b.get('est_routine'): no_fresh.append(b.get('name'))
        continue
    if any(c and c[0] for c in row): est_rows[b.get('name') or aid] = row
def topn(rows, key):
    tot = {n: sum(key(c) for c in r if c) for n, r in rows.items()}
    keep_ = sorted(rows, key=lambda n: -tot[n])[:DUC['top_bots']]; out = {n: rows[n] for n in keep_}
    rest = [rows[n] for n in rows if n not in keep_]
    if rest:
        out['Other'] = [None if all(r[i] is None for r in rest) else [sum(r[i][j] for r in rest if r[i]) for j in (0, 1)] + ([1] if any(r[i] and r[i][2:] for r in rest) else []) for i in range(7)]
    return out
def est_usd(calls, replay):
    opc = EST['est_output_tokens_per_call']; pin, pout = EST.get('bot_usd_per_m_input_tokens'), EST.get('bot_usd_per_m_output_tokens')
    if pin is not None and pout is not None: v = (replay * pin + calls * opc * pout) / 1e6
    else: v = (replay + calls * opc) * EST['bot_usd_per_m_tokens'] / 1e6
    return v * CAL if CAL else v
spend = {}   # name -> [bot_usd_est or None (no fresh data), coding_usd, partial]
for n, r in est_week.items():
    vals = [c for c in r if c]
    spend[n] = [sum(est_usd(c[0], c[1]) for c in vals) if vals else None, 0.0, any(c[2:] for c in vals)]
for n, r in cli_rows.items():
    spend.setdefault(n, [None, 0.0, False])[1] = sum(c[1] for c in r if c)
if 'Unmapped CLI' in spend: spend['Unmapped CLI'][0] = 'na'   # not a bot: no bot-work column
spend = {n: v for n, v in spend.items() if v[1] or (isinstance(v[0], float) and v[0] > 0)}
ordered = sorted(spend, key=lambda n: -(spend[n][1] + (spend[n][0] if isinstance(spend[n][0], float) else 0)))
top_sp = {n: spend[n] for n in ordered[:DUC['top_bots']]}
rest_sp = [spend[n] for n in ordered[DUC['top_bots']:]]
if rest_sp:
    bots_ = [v[0] for v in rest_sp if isinstance(v[0], float)]
    top_sp['Other'] = [sum(bots_) if bots_ else None, sum(v[1] for v in rest_sp), any(v[2] for v in rest_sp)]
bots_usd = sum(v[0] for v in spend.values() if isinstance(v[0], float)); coding_usd = sum(v[1] for v in spend.values())
real_bots = {n: v for n, v in est_week.items() if n != 'Deleted bots'}
n_fresh = sum(1 for v in real_bots.values() if any(c for c in v)); n_known = len(real_bots)
# graceful degrade: when too few bots have fresh local activity data, show coding (exact) only, no n/a walls
_doge = OWNER.get('doge_bot_id')
_cov_ids = {aid for aid in set(fleet) | set(dud.get('bots') or {}) if aid not in excl and aid != _doge
            and ((dud.get('bots') or {}).get(aid) or fleet.get(aid) or {}).get('name') != 'Deleted bots'}
cov_total = len(_cov_ids)
cov_fresh = sum(1 for aid in _cov_ids if (parse_local(((dud.get('bots') or {}).get(aid) or {}).get('coverage_end')) or dt.datetime.min) >= STALE_BEFORE)
bots_hidden = bool(cov_total) and cov_fresh / cov_total < DUC.get('min_bot_coverage', 0.5)
thin_note = (f"Bot chat/routine costs are hidden this week: only {cov_fresh} of {cov_total} bots have local activity data newer than "
             f"{CFG['freshness']['stale_days']} days, so a bots figure would be mostly blanks." if bots_hidden else None)

# ---------- week-over-week trend (persisted in history/weekly_totals.json) ----------
import math
TRC = CFG['trend']; n_weeks = max(1, math.ceil(TRC['months'] * TRC['weeks_per_month']))
last_full = monday - dt.timedelta(days=7)
trend_starts = [(last_full - dt.timedelta(days=7 * i)) for i in reversed(range(n_weeks))]
cli_first = dud.get('cli_first_day')
def week_calc(ws):
    days = [(ws + dt.timedelta(days=i)).isoformat() for i in range(7)]
    per = {}   # name -> [bot_usd or None, coding_usd or None, partial]
    coding_known = bool(cli_first) and days[-1] >= cli_first
    if coding_known:
        for d, cwd, tok, ticks in dud.get('cli', []):
            if d in days:
                n = repo_bot(repo(cwd))[0] or 'Unmapped CLI'
                e_ = per.setdefault(n, [None, 0.0, False]); e_[1] = (e_[1] or 0) + ticks * EST['cli_usd_per_tick']
    any_bot, partial, cov = False, False, 0
    for aid, b in (dud.get('bots') or {}).items():
        if aid in excl: continue
        row = est_row(b, days); vals = [c for c in row if c]
        if b.get('coverage_end') and (len(vals) < 7 or any(c[2:] for c in vals)):
            partial = partial or bool(vals) or (b.get('chat_coverage_start') or b.get('routine_coverage_start') or '9')[:10] <= days[-1]
        if not vals: continue
        any_bot = True; cov += len(vals)
        e = per.setdefault(b.get('name') or aid, [None, 0.0 if coding_known else None, False])
        e[0] = sum(est_usd(c[0], c[1]) for c in vals); e[2] = len(vals) < 7 or any(c[2:] for c in vals)
    for e in per.values():
        if coding_known and e[1] is None: e[1] = 0.0
    bots_tot = sum(e[0] for e in per.values() if e[0] is not None) if any_bot else None
    return dict(bots_usd_est=None if bots_tot is None else round(bots_tot, 2), bots_partial=bool(any_bot and partial), bots_cov=cov,
                coding_usd=round(sum(e[1] or 0 for e in per.values()), 2) if coding_known else None,
                coding_partial=bool(coding_known and days[0] < cli_first),
                per_bot={n: [None if e[0] is None else round(e[0], 2), None if e[1] is None else round(e[1], 2), 0 if e[2] or e[0] is None else 1] for n, e in per.items()})  # [bot~$, coding$, 1=bot fully covered]
wt_path = os.path.join(hist_dir, 'weekly_totals.json')
wt = load(wt_path, {}) or {}; wt.setdefault('weeks', {})
for sp in glob.glob(os.path.join(P(PATHS['summary_dir']), '20*.json')):   # fallback: earlier dated summaries
    du_ = (load(sp, {}) or {}).get('daily_usage') or {}; t_ = du_.get('totals') or {}
    if du_.get('week_start') and du_.get('week') == 'last_full' and du_['week_start'] not in wt['weeks'] and 'coding_usd' in t_:
        wt['weeks'][du_['week_start']] = dict(bots_usd_est=t_.get('bots_usd_est'), bots_partial=True, coding_usd=t_.get('coding_usd'),
                                             coding_partial=False, per_bot={}, source='summary')
def keep_known(new, old):
    """Idempotent merge: a fresh value overwrites; a missing (None) value never erases a persisted one."""
    if old is None: return new
    if new is None: return old
    if isinstance(new, dict):
        return {k: keep_known(new.get(k), old.get(k)) for k in sorted(set(new) | set(old))}
    if isinstance(new, list): return [keep_known(a, b) for a, b in zip(new, old)] if len(new) == len(old) else new
    return new
for ws in trend_starts:
    k = ws.isoformat(); new = week_calc(ws); old = wt['weeks'].get(k, {})
    merged = keep_known({kk: v for kk, v in new.items()}, {kk: v for kk, v in old.items() if kk in new})
    if new['bots_usd_est'] is None and old.get('bots_usd_est') is not None: merged['bots_partial'] = old.get('bots_partial', True)
    pb = merged.get('per_bot') or {}
    tot = lambda v: (v[0] or 0) + (v[1] or 0)
    merged['per_bot'] = dict(sorted(pb.items(), key=lambda x: (-tot(x[1]), x[0]))[:TRC['top_bots_persisted']])
    merged['source'] = 'scan'
    if {kk: v for kk, v in merged.items() if kk != 'updated'} != {kk: v for kk, v in old.items() if kk != 'updated'}:
        merged['updated'] = fmt(NOW); wt['weeks'][k] = merged   # only touch a week when its numbers changed (idempotent)
wt['weeks'] = dict(sorted(wt['weeks'].items()))
if wt != load(wt_path, {}): dump(wt, wt_path)
trend_rows = [(ws.isoformat(), wt['weeks'].get(ws.isoformat(), {})) for ws in trend_starts]
def wtotal(r):
    b_, c_ = r.get('bots_usd_est'), r.get('coding_usd')
    return None if b_ is None and c_ is None else (b_ or 0) + (c_ or 0)
def change(cur, prv):
    """Compare like with like: full totals when both weeks have bot estimates, else coding only."""
    if not prv: return None
    cv, pv = cur.get('bots_cov') or 0, prv.get('bots_cov') or 0
    comparable = bool(cv and pv) and min(cv, pv) / max(cv, pv) >= TRC['comparable_coverage']
    if comparable and not bots_hidden and cur.get('bots_usd_est') is not None and prv.get('bots_usd_est') is not None and cur.get('coding_usd') is not None and prv.get('coding_usd') is not None:
        a, b_, basis = wtotal(cur), wtotal(prv), 'total'
    elif cur.get('coding_usd') is not None and prv.get('coding_usd') is not None:
        a, b_, basis = cur['coding_usd'], prv['coding_usd'], 'coding'
    else: return None
    return [round(a - b_, 2), round(100 * (a - b_) / b_) if b_ else None, basis]
changes = [None] + [change(trend_rows[i][1], trend_rows[i - 1][1]) for i in range(1, len(trend_rows))]
movers = []
if len(trend_rows) >= 2:
    cur_pb, prv_pb = trend_rows[-1][1].get('per_bot') or {}, trend_rows[-2][1].get('per_bot') or {}
    for n in set(cur_pb) | set(prv_pb):
        c_, p_ = (cur_pb.get(n) or [None, None, 0]) + [0], (prv_pb.get(n) or [None, None, 0]) + [0]
        dlt = 0.0; known = False
        for j in ((0, 1) if c_[2] and p_[2] and not bots_hidden else (1,)):   # bot work only if fully covered both weeks
            cj = c_[j] if c_[j] is not None else (0.0 if j == 1 and trend_rows[-1][1].get('coding_usd') is not None else None)
            pj = p_[j] if p_[j] is not None else (0.0 if j == 1 and trend_rows[-2][1].get('coding_usd') is not None else None)
            if cj is not None and pj is not None: dlt += cj - pj; known = True
        if known and round(dlt, 2): movers.append([n, round(dlt, 2)])
    movers = sorted(movers, key=lambda x: -abs(x[1]))[:TRC['movers']]
top_trend_bots = []
for ws_, r in reversed(trend_rows):
    for n in sorted(r.get('per_bot') or {}, key=lambda n: -((r['per_bot'][n][0] or 0) + (r['per_bot'][n][1] or 0))):
        if n not in top_trend_bots: top_trend_bots.append(n)
top_trend_bots = top_trend_bots[:TRC['top_bots_in_summary']]
trend = dict(months=TRC['months'], weeks=n_weeks, cell='[week_start, bots_usd_est|null, coding_usd|null, flags: b=bots partial, c=coding partial]',
             rows=[[k[5:], r.get('bots_usd_est'), r.get('coding_usd'), ('b' if r.get('bots_partial') else '') + ('c' if r.get('coding_partial') else '')] for k, r in trend_rows],
             change=[c_[:2] + ([c_[2]] if c_[2] != 'total' else []) if c_ else None for c_ in changes],
             movers=movers,
             per_bot={n: [((r.get('per_bot') or {}).get(n) or [None, None])[:2] for _, r in trend_rows] for n in top_trend_bots})
# ---------- monthly line + early warning ----------
BG = CFG['budget']; OB = OWNER.get('budget') or {}
import calendar
m_start = today.replace(day=1); dim = calendar.monthrange(today.year, today.month)[1]
pm_end = m_start - dt.timedelta(days=1); pm_start = pm_end.replace(day=1)
def span_days(a_, b_): return [(a_ + dt.timedelta(days=i)).isoformat() for i in range((b_ - a_).days + 1)]
def coding_sum(days):
    ds = set(days); return sum(t * EST['cli_usd_per_tick'] for d, cwd, tok, t in dud.get('cli', []) if d in ds)
def bots_sum(days):
    tot, any_ = 0.0, False
    for aid, b in (dud.get('bots') or {}).items():
        if aid in excl: continue
        vals = [c for c in est_row(b, days) if c]
        if vals: any_ = True; tot += sum(est_usd(c[0], c[1]) for c in vals)
    return tot if any_ else None
mtd_days = span_days(m_start, today); pm_days = span_days(pm_start, pm_end)
rate_days = span_days(today - dt.timedelta(days=BG['rate_days'] - 1), today)
mtd_c, mtd_b = coding_sum(mtd_days), bots_sum(mtd_days)
rate_c = coding_sum(rate_days) / BG['rate_days']; rb = bots_sum(rate_days); rate_b = rb / BG['rate_days'] if rb is not None else None
remaining = dim - today.day
proj_c = mtd_c + rate_c * remaining
proj_b = (mtd_b or 0) + rate_b * remaining if rate_b is not None else mtd_b
pm_c, pm_b = coding_sum(pm_days), bots_sum(pm_days)
pm_c_partial = bool(cli_first) and pm_days[0] < cli_first
chg_c = round(100 * (proj_c - pm_c) / pm_c) if pm_c else None
budget = OB.get('monthly_usd'); bal = OB.get('credit_balance_usd'); as_of = OB.get('balance_as_of') or ''
runway = None
daily = rate_c + (rate_b or 0)
if bal not in (None, '') and daily > 0:
    try: since_days = span_days(dt.date.fromisoformat(as_of), today) if as_of else []
    except ValueError: since_days = []
    spent = coding_sum(since_days) + (bots_sum(since_days) or 0)
    runway = round((float(bal) - spent) / daily, 1)
warn = bool((runway is not None and runway < BG['warn_days']) or (budget not in (None, '') and proj_c + (proj_b or 0) > float(budget)))
cut = (NOW - dt.timedelta(days=BG['outage_days'])).timestamp() * 1000
outage = [(r['slug'], k) for b in fleet.values() for r in b.get('routines', []) for t, k in (r.get('err_kinds') or [])
          if (t or 0) >= cut and re.search(BG['outage_regex'], k or '')]
newest_run = max((r['newest'] for r in routines.values() if r.get('newest')), default=None)   # routine data freshness
newest_err = newest_run.timestamp() * 1000 if newest_run else 0
monthly = dict(month=m_start.strftime('%Y-%m'), mtd_coding=round(mtd_c, 2), mtd_bots_est=None if mtd_b is None else round(mtd_b, 2),
               proj_coding=round(proj_c, 2), proj_bots_est=None if proj_b is None else round(proj_b, 2),
               last_month=[round(pm_c, 2), None if pm_b is None else round(pm_b, 2)], last_month_coding_partial=pm_c_partial,
               chg_coding_pct=chg_c, budget=budget, balance=bal, runway_days=runway, warn=warn, outage_7d=len(outage),
               rate_days=BG['rate_days'])
def mus(x, est=False): return ('~' if est else '') + f"${x:,.2f}"
proj_tot = proj_c + (proj_b or 0)
monthly_md = ((f"**Month to date ({m_start:%b}):** coding {mus(mtd_c)} · projected {mus(proj_c, True)} for the month" if bots_hidden else
               f"**Month to date ({m_start:%b}):** coding {mus(mtd_c)} · bots {'n/a' if mtd_b is None else mus(mtd_b, True)} · projected {mus(proj_tot, True)}"
               + ("" if proj_b is not None else " coding-only"))
              + (" (no budget or balance set)" if budget in (None, '') and bal in (None, '') else
                 f" ({'budget ' + mus(float(budget)) if budget not in (None, '') else 'no budget set'}"
                 + (f", ~{runway:g} days of runway" if runway is not None else (", no balance set" if bal in (None, '') else ", runway n/a")) + ")")
              + (f" · vs {pm_start:%b} coding {mus(pm_c)}{'‡' if pm_c_partial else ''} ({'+' if chg_c >= 0 else '−'}{abs(chg_c)}%)" if chg_c is not None else "")
              + (" · **⚠ budget warning**" if warn else "")
              + (f" · outage-type routine errors last {BG['outage_days']}d: {len(outage)}" if outage else
                 f" · no outage-type routine errors in the last {BG['outage_days']}d" + (f" (routine data only to {fmt(from_ms(newest_err))[:10] if newest_err else 'n/a'})" if not newest_err or newest_err < cut else "")))
def tusd(x, est=False): return ('~' if est else '') + f"${x:,.2f}"
_shown = [kr for kr in trend_rows if not bots_hidden or kr[1].get('coding_usd') is not None]   # thin mode: skip weeks before the first CLI record
trend_md = [f"### Week over week (last {len(_shown)} weeks)", ""] + (["| Week | Coding $ (exact) | Change vs prior week |", "|---|---|---|"] if bots_hidden else
            ["| Week | Coding $ | Bots ~$ | Total | Change vs prior week |", "|---|---|---|---|---|"])
for (k, r), ch in zip(trend_rows, changes):
    ws = dt.date.fromisoformat(k); we = ws + dt.timedelta(days=6)
    lab = f"{ws:%b %-d}–{we:%-d}" if ws.month == we.month else f"{ws:%b %-d}–{we:%b %-d}"
    b_, c_ = r.get('bots_usd_est'), r.get('coding_usd')
    bcell = 'n/a' if b_ is None else tusd(b_, True) + ('†' if r.get('bots_partial') else '')
    ccell = 'n/a' if c_ is None else tusd(c_) + ('‡' if r.get('coding_partial') else '')
    tcell = 'n/a' if b_ is None and c_ is None else (tusd(c_) + ' + n/a' if b_ is None else (tusd(b_, True) + ' + n/a' if c_ is None else tusd(b_ + c_, True)))
    chcell = 'n/a' if not ch else (f"{'+' if ch[0] >= 0 else '−'}${abs(ch[0]):,.2f}" + (f" ({'+' if ch[1] >= 0 else '−'}{abs(ch[1])}%" if ch[1] is not None else " (prior week $0") + (", coding only)" if ch[2] == 'coding' else ")"))
    if bots_hidden and c_ is None: continue
    if bots_hidden: chcell = '—' if chcell == 'n/a' else chcell.replace(', coding only)', ')')
    trend_md.append(f"| {lab} | {ccell} | {chcell} |" if bots_hidden else f"| {lab} | {ccell} | {bcell} | {tcell} | {chcell} |")
_cli_src = f"Grok Build CLI per-turn data{', records start ' + dt.date.fromisoformat(cli_first).strftime('%b %-d') if cli_first else ''}; ‡ = week starts before the first record"
trend_md += ["", ("**Biggest movers this week vs last" + (" (coding):** " if bots_hidden else ":** ") + " · ".join(f"{redact(n)} {'+' if d_ >= 0 else '−'}${abs(d_):,.2f}" for n, d_ in movers)) if movers else "**Biggest movers this week vs last:** none",
             ""] + ([f"*Coding is exact ({_cli_src}). Weeks are persisted in history/weekly_totals.json, bot estimates included, so the full table returns when bot data is fresh again.*", ""] if bots_hidden else
            [f"*Coding is exact (Grok Build CLI per-turn data{', records start ' + dt.date.fromisoformat(cli_first).strftime('%b %-d') if cli_first else ''}; ‡ = week starts before the first record). Bots ~$ is an estimate from local data; † = partial (some bots or days missing). Change compares full totals only when both weeks have similar bot-data coverage, otherwise coding only; movers count bot work only for bots fully covered both weeks. Weeks are persisted in history/weekly_totals.json.*", ""])
cli_rows = topn(cli_rows, lambda c: c[0]); est_rows = topn(est_rows, lambda c: c[0])
def col_tot(rows):
    return [None if all(r[i] is None for r in rows.values()) else [sum(r[i][j] for r in rows.values() if r[i]) for j in (0, 1)] + ([1] if any(r[i] and r[i][2:] for r in rows.values()) else []) for i in range(7)]
cli_tot, est_tot = col_tot(cli_rows), col_tot(est_rows)
K = 1000
daily_usage = dict(week_start=days7[0], week=DUC['week'], days=[d[5:] for d in days7], units=dict(tok='k', usd='$'),
    cli_exact={n: [None if c is None else [round(c[0] / K), round(c[1], 2)] for c in r] for n, r in cli_rows.items()},
    est={n: [None if c is None else [c[0], round(c[1] / K)] + c[2:] for c in r] for n, r in est_rows.items()},
    est_cell='[calls, replay_ktok(, 1 = partial: chat only)]',
    totals=dict(cli_ktok=round(sum(c[0] for c in cli_tot if c) / K), cli_usd=round(sum(c[1] for c in cli_tot if c), 2),
                est_calls=sum(c[0] for c in est_tot if c), est_replay_ktok=round(sum(c[1] for c in est_tot if c) / K),
                bots_usd_est=round(bots_usd, 2), coding_usd=round(coding_usd, 2), total_usd_est=round(bots_usd + coding_usd, 2)),
    spend={n: [round(v[0], 2) if isinstance(v[0], float) else None, round(v[1], 2)] for n, v in top_sp.items()},
    spend_cell='[bot_work_usd_est or null = no fresh data, coding_usd_exact]', bot_usd_per_m=EST['bot_usd_per_m_tokens'],
    est_label='estimate', no_fresh_local=len(no_fresh))
def ftok(t):
    return f"{t / 1e6:.1f}M" if t >= 1e6 else (f"{t / 1e3:.0f}k" if t >= 1e3 else f"{int(t)}")
def mdrow(name, r, cellf, totf):
    vals = [c for c in r if c]
    tot = [sum(c[0] for c in vals), sum(c[1] for c in vals)] + ([1] if any(c[2:] for c in vals) else []) if vals else None
    return f"| {name} | " + " | ".join('n/a' if c is None else cellf(c) for c in r) + f" | {'n/a' if tot is None else totf(tot)} |"
cli_cell = lambda c: '0' if not c[0] else f"{ftok(c[0])} · ${c[1]:.2f}"
est_cell = lambda c: ('0' if not c[0] else f"{c[0]:,} calls · {ftok(c[1])} tok") + ('†' if c[2:] else '')
hdr = "| Bot | " + " | ".join(f"{dt.date.fromisoformat(d):%a %-m/%-d}" for d in days7) + " | Week |\n|---|" + "---|" * 8
cov_list = sorted(((b.get('name'), (b.get('coverage_end') or '')[:10]) for b in (dud.get('bots') or {}).values() if b.get('coverage_end')), key=lambda x: x[1], reverse=True)
md = [f"## Penny DOGE: fleet usage by day, week of {dt.date.fromisoformat(days7[0]):%b %-d}–{dt.date.fromisoformat(days7[-1]):%b %-d, %Y} ({'last full Mon–Sun week' if DUC['week'] == 'last_full' else 'current partial week'})",
      "", (f"*Data freshness ({TZL}): coding (Grok Build CLI) exact, live to {fmt(cli_newest)}.*" if bots_hidden else
           f"*Data freshness ({TZL}): CLI exact, live to {fmt(cli_newest)}. Chat/routine estimates come from the local snapshot; newest local data: "
           + ", ".join(f"{n} {c[5:]}" for n, c in cov_list[:4]) + ". `n/a` = no fresh local data (not zero).*"), ""]
layout = DUC.get('table_layout', 'split_spend')
if layout == 'split_spend' and bots_hidden:
    def usd(x): return f"${x:,.2f}"
    csp = sorted(((n, v[1]) for n, v in spend.items() if v[1]), key=lambda x: -x[1])
    ctop = csp[:DUC['top_bots']] + ([('Other', sum(v for _, v in csp[DUC['top_bots']:]))] if len(csp) > DUC['top_bots'] else [])
    md += [f"**Overall: coding {usd(coding_usd)} (exact)**", "", thin_note, "", monthly_md, ""] + trend_md
    md += ["### Coding spend by bot (exact)", "", "| Bot | Coding $ |", "|---|---|"] + [f"| {redact(n)} | {usd(v)} |" for n, v in ctop]
    md += [f"| **Total** | **{usd(coding_usd)}** |", "", "*Coding = exact Grok Build CLI cost, attributed to bots through the owner repo map.*"]
    md += ["", "### Coding by day (exact)", "", hdr]
    md += [mdrow(redact(n), r, cli_cell, cli_cell) for n, r in cli_rows.items()] + [mdrow('**Total**', cli_tot, cli_cell, cli_cell)]
elif layout == 'split_spend':
    def usd(x, est=False): return ('~' if est else '') + f"${x:,.2f}"
    def bw(v): return '—' if v[0] == 'na' else ('n/a' if v[0] is None else usd(v[0], True) + ('†' if v[2] else ''))
    def tot(v): return usd(v[1] + v[0], True) if isinstance(v[0], float) else (usd(v[1]) + (' + n/a' if v[0] is None else ''))
    pin, pout = EST.get('bot_usd_per_m_input_tokens'), EST.get('bot_usd_per_m_output_tokens')
    price = (f"${pin}/M input + ${pout}/M output" if pin is not None and pout is not None else f"${EST['bot_usd_per_m_tokens']}/M tokens blended")
    md += [f"**Overall: coding {usd(coding_usd)} (exact) · bots {usd(bots_usd, True)} (estimate{CAL_TAG}) · total {usd(bots_usd + coding_usd, True)}**", "", monthly_md, ""] + trend_md
    md += ["### Spend by bot", "", f"| Bot | Coding $ (exact) | Bot work ~$ (estimate{CAL_TAG}) | Total |", "|---|---|---|---|"]
    md += [f"| {redact(n)} | {usd(v[1])} | {bw(v)} | {tot(v)} |" for n, v in top_sp.items()]
    md += [f"| **Totals** | **{usd(coding_usd)}** | **{usd(bots_usd, True)}** | **{usd(bots_usd + coding_usd, True)}** |", "",
           f"*Bot work = chat + routine model use, estimated from local transcripts (replay-token proxy + {EST['est_output_tokens_per_call']:,} output tokens per est. call) and priced at {price}. That price is the per-token rate implied by the exact Grok Build CLI data (total $ / total tokens). Coding = exact Grok Build CLI cost. "
           f"**Bot-work estimates are thin while local data is stale: only {n_fresh} of {n_known} bots have fresh local data this week, so treat the bots figure as a floor.** `n/a` = no fresh local data; — = not a bot.*"]
    if any(v[2] for v in top_sp.values()): md += ["", "*† partial: chat turns only; this bot has routines but its routine runs for the week aren't in the local data.*"]
    md += ["", "### Coding by day (exact)", "", hdr]
    md += [mdrow(redact(n), r, cli_cell, cli_cell) for n, r in cli_rows.items()] + [mdrow('**Total**', cli_tot, cli_cell, cli_cell)]
else:  # 'by_day': older layout
    md += [monthly_md, ""] + trend_md
    md += ["### Grok Build CLI (exact tokens · $)", "", hdr]
    md += [mdrow(redact(n), r, cli_cell, cli_cell) for n, r in cli_rows.items()] + [mdrow('**Total**', cli_tot, cli_cell, cli_cell)]
    md += ["", "### Chat + routine work (estimate: model calls · replay-token proxy)", "",
           f"*Estimate, not billed tokens. Routine runs: assistant lines per run transcript, replay = transcript bytes before each call / {DUC['bytes_per_token']}. Chat: main-thread turns per day × the bot's average calls/turn, replay per call capped at {ftok(DUC['context_cap_tokens'])} tokens.*", "", hdr]
    if est_rows: md += [mdrow(redact(n), r, est_cell, est_cell) for n, r in est_rows.items()] + [mdrow('**Total (estimate)**', est_tot, est_cell, est_cell)]
    else: md += ["| (no bot has fresh local data this week) | " + " | ".join(['n/a'] * 8) + " |"]
    if any(c and c[2:] for r in est_rows.values() for c in r): md += ["", "*† partial: chat turns only; this bot has routines but its routine runs for that day aren't in the local data.*"]
unm = {}
for d, cwd, tok, ticks in dud.get('cli', []):
    if d in days7 and not repo_bot(repo(cwd))[0]: unm[repo(cwd)] = unm.get(repo(cwd), 0) + tok
unm = {k: v for k, v in unm.items() if v}   # empty folders (0 tokens) aren't worth asking about
if unmapped_ask:
    if unmapped_ask['repos']: md += ["", f"**Unclaimed coding: ${unmapped_ask['usd']:.2f} ({unmapped_ask['share']:.0%} of coding).** {unmapped_ask['why']} " + unmapped_ask['ask']]
    for c in unmapped_ask['conflicts']: md += ["", f"**Folder claimed twice: {c['repo']}** (mapped to {c['mapped_to']}, also claimed by {c['claimed_by']}). Which bot owns it?"]
if unm: md += ["", "*Unmapped CLI repos (no bot has claimed these yet; coding bots claim their own folder with Penny DOGE): " + ", ".join(f"{redact(k)} {ftok(v)}" for k, v in sorted(unm.items(), key=lambda x: -x[1])) + ".*"]
if no_fresh and not bots_hidden: md += ["", f"*No fresh local data this week ({len(no_fresh)} bots): " + ", ".join(sorted(redact(n) for n in no_fresh if n)) + ".*"]
ao_top = sorted(ao_rows, key=lambda r: (-r['load'], -r['plat']))[:TH['A21']['md_top_bots']]
if ao_top:
    def kt(t): return f"{t / 1e3:.1f}k"
    _cp = TH['A21']['count_platform_sections']
    md += ["", f"### Always-on load (estimate, top {len(ao_top)})", "", f"| Bot | {'Load' if _cp else 'Owner text'}/turn | Instructions | Memory | App's own sections{'' if _cp else ' (not counted)'} | Biggest routine prompt | Re-send ~$/wk |", "|---|---|---|---|---|---|---|"]
    md += [f"| {redact(r['name'])} | {kt(r['load'])} tok | {kt(r['instr'])} | {'n/a' if r['mem'] is None else kt(r['mem'])} | {kt(r['plat']) if r['plat'] else 'n/a'} | "
           + (f"{kt(r['big'][1])} × {r['big'][2]:g}/wk" if r['big'] else '—') + f" | ~${r['usd']:.2f} |" for r in ao_top]
    snaps = sorted({r['snap'][:10] for r in ao_top if r['snap']})
    md += ["", f"*Tokens = chars / {TH['A21']['chars_per_token']}. Instructions are live; memory and platform sections come from each bot's last prompt snapshot ({', '.join(s_[5:] for s_ in snaps) or 'none'}). App's own sections = bot directory, routine and connector guidance: they grow with fleet size and owners can't trim them, so they are {'counted' if _cp else 'shown for context only, never flagged'}. Re-send $ = {'load' if _cp else 'owner text'} × model calls/wk at ${PRICE}/M, an upper bound (caching may discount repeats). Flag: {'load' if _cp else 'owner text'} > {TH['A21']['max_always_on_tokens']:,} tok, or a routine prompt > {TH['A21']['max_routine_prompt_tokens']:,} tok firing daily or more.*"]
with open(os.path.join(P(PATHS['summary_dir']), 'daily_table.md'), 'w') as f: f.write("\n".join(md) + "\n")

daily_usage['bot_coverage'] = [cov_fresh, cov_total]
if CAL: daily_usage['calibrated'] = CAL   # only present once Usage-page calibration has >= 2 observations
if bots_hidden:   # same rule as the table: never hand the wake a bots figure built from blanks
    daily_usage.update(bots_hidden=True, bots_hidden_why=thin_note, est={}, spend={n: [None, v[1]] for n, v in daily_usage['spend'].items() if v[1]})
    daily_usage['totals'].update(bots_usd_est=None, total_usd_est=None, est_calls=None, est_replay_ktok=None)
    for r in trend['rows']: r[1] = None
    trend['per_bot'] = {n: [[None, x[1]] for x in v] for n, v in trend['per_bot'].items() if any(x[1] for x in v)}
    monthly.update(mtd_bots_est=None, proj_bots_est=None, last_month=[monthly['last_month'][0], None])

# ---------- summary (small) ----------
def newest(vals):
    v = [x for x in vals if x]; return fmt(max(v)) if v else None
start = float(os.environ.get('DOGE_START', time.time()))
summary = dict(
    week=WEEK_LABEL, generated_at=fmt(NOW) + ' ' + TZL, prev_week=prev.get('week'),
    freshness=dict(routine_runs=newest(x for r in routines.values() for x in [r.get('newest')]),
                   routine_defs=newest(parse_local(x.get('last')) for b in fleet.values() for x in b.get('routines', [])),
                   chat_store=newest(parse_local(b.get('last')) for b in fleet.values()),
                   unread_state=newest(from_ms((b.get('unread') or {}).get('lastViewedAt')) for b in fleet.values()),
                   cli_sessions=fmt(cli_newest), stale_cutoff=fmt(STALE_BEFORE)),
    cli_7d=dict(tokens_m=round(cli_tok / 1e6, 1), usd=cli_usd, sessions=len(cli7)),
    doge_cost=dict(scan_wall_s=round(time.time() - start, 1), cron_model_tokens=0,
                   wake_tool_calls=None, wake_agent_msgs=None, wake_est_model_calls=None),
    counts=dict(over_threshold=len(cands), fresh=len(fresh), excluded_stale=len(stale),
                suppressed_already_raised=suppressed, wake_eligible=len(eligible)),
    unmapped_ask=unmapped_ask,
    candidates=[compact(c) for c in top],
    stale_watch=[compact(c, full=False) for c in stale[:sw_n]],
    idle_bots=[{k: v for k, v in x.items() if k != 'idle_days'} for x in idle[:IB['max_listed']]],
    idle_counts=dict(idle=len(idle), new_or_doubled=len(idle_pending), unknown=len(idle_unknown), warn=len(idle_warn),
                     unconfirmed=len(idle_unconfirmed), active_by_cli=active_by_cli),
    idle_unknown=sorted(n for n in idle_unknown if n),
    idle_unconfirmed=sorted(n for n in idle_unconfirmed if n),
    idle_rule=IB.get('wording_rule', ''),
    idle_warn=idle_warn[:IB['max_warn_listed']],
    unread_senders=unread_senders[:US['max_listed']],
    unread_counts=dict(flagged=len(unread_senders), wake=len(unread_pending), unconfirmed=unread_unconfirmed),
    monthly=monthly, trend=trend, daily_usage=daily_usage, daily_table_md=os.path.relpath(os.path.join(P(PATHS['summary_dir']), 'daily_table.md'), HOME),
    note='Local-file signals are unconfirmed; live-check the owning bot before reporting. Names and counts only.')
limit = CFG['output']['summary_max_bytes']
def size(): return len(json.dumps(summary, separators=(',', ':')).encode())
while size() > limit and summary['stale_watch']: summary['stale_watch'].pop()
while size() > limit and summary['idle_unknown']: summary['idle_unknown'].pop()
while size() > limit and summary['idle_unconfirmed']: summary['idle_unconfirmed'].pop()
while size() > limit and summary['idle_warn']: summary['idle_warn'].pop()
while size() > limit and len(summary['unread_senders']) > len(unread_pending): summary['unread_senders'].pop()  # keep wake-worthy ones
while size() > limit and summary['idle_bots']: summary['idle_bots'].pop()
while size() > limit and summary['trend']['per_bot']: summary['trend']['per_bot'].popitem()
for tbl in ('est', 'cli_exact'):  # then shrink the day table: drop the smallest named rows (keep 'Other')
    while size() > limit and len([n for n in daily_usage[tbl] if n != 'Other']) > 1:
        daily_usage[tbl].pop([n for n in daily_usage[tbl] if n != 'Other'][-1]); daily_usage['trimmed'] = True
while size() > limit and summary['candidates']: summary['candidates'].pop()
sd = P(PATHS['summary_dir'])
dump(summary, os.path.join(sd, 'latest.json'), compact=True)
dump(summary, os.path.join(sd, WEEK + '.json'), compact=True)
dump(dict(week=WEEK_LABEL, items=[dict(key=c['key'], advice=c['advice'], saved=c['saved']) for c in eligible], idle=idle_pending, unread=unread_pending, warn=warn,
          unmapped=unmapped_pending), P(PATHS['pending_file']))
print(json.dumps(dict(week=WEEK_LABEL, candidates=len(top), eligible=len(eligible), stale=len(stale), unread_wake=len(unread_pending), summary_bytes=size())))
