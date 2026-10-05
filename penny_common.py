"""Shared no-AI helpers for Penny scripts (alert level, calibration factor, cron math, spend sums).
Folder/file names keep the historical 'doge' prefix on purpose. Never prints secrets."""
import copy, json, os, re, statistics, datetime as dt

HOME = os.path.dirname(os.path.abspath(__file__))
def P(rel): return rel if os.path.isabs(rel) else os.path.join(HOME, rel)
def load(p, d=None):
    try:
        with open(p) as f: return json.load(f)
    except Exception: return d

# ---------- alert level: quiet | normal | chatty (owner choice in local/owner.json; normal = config as is) ----------
def apply_alert_level(cfg, owner):
    """Return cfg with the owner's alert-level overrides applied. 'normal' (the default) changes nothing."""
    lvl = (owner or {}).get('alert_level') or 'normal'
    over = ((cfg.get('alert_levels') or {}).get(lvl) or {}) if lvl != 'normal' else {}
    if not over: return cfg
    out = copy.deepcopy(cfg)
    for dotted, v in over.items():
        if dotted.startswith('_'): continue
        node = out; ks = dotted.split('.')
        for k in ks[:-1]: node = node.setdefault(k, {})
        node[ks[-1]] = v
    return out

# ---------- cron math ----------
def cron_vals(field, lo, hi):
    out = set()
    for part in field.split(','):
        step = 1
        if '/' in part: part, step = part.split('/'); step = int(step)
        if part == '*': a, b = lo, hi
        elif '-' in part: a, b = map(int, part.split('-'))
        else: a = b = int(part)
        out.update(range(a, b + 1, step))
    return sorted(out)
def cron_rates(sched):
    """{'per_hour', 'per_day', 'per_week'} for a 5-field cron (CRON_TZ prefix allowed), else None (webhook/event)."""
    if not sched or sched == 'webhook': return None
    parts = re.sub(r'CRON_TZ=\S+\s+', '', sched).split()
    if len(parts) != 5: return None
    try:
        mi, hr, dom, mo, dow = parts
        mins, hours = cron_vals(mi, 0, 59), cron_vals(hr, 0, 23)
        dows = sorted({d % 7 for d in cron_vals(dow, 0, 7)}) if dow != '*' else list(range(7))
        pw = len(mins) * len(hours) * len(dows)
        if dom != '*': pw = len(mins) * len(hours) * len(cron_vals(dom, 1, 31)) * 12 / 52
        return dict(per_hour=len(mins), per_day=len(mins) * len(hours), per_week=pw)
    except ValueError: return None

# ---------- spend over a set of days (coding exact, bots estimated) from daily_usage.json ----------
def bot_usd(calls, replay, est):
    opc = est['est_output_tokens_per_call']; pin, pout = est.get('bot_usd_per_m_input_tokens'), est.get('bot_usd_per_m_output_tokens')
    if pin is not None and pout is not None: return (replay * pin + calls * opc * pout) / 1e6
    return (replay + calls * opc) * est['bot_usd_per_m_tokens'] / 1e6
def spend_on_days(days, dud, est, excl=()):
    """Return (coding_usd, bots_usd_est, bot_days_covered, bot_days_possible). Bots count only days inside each bot's local coverage."""
    ds = set(days)
    coding = sum(t * est['cli_usd_per_tick'] for d, cwd, tok, t in dud.get('cli', []) if d in ds)
    bots, cov, poss = 0.0, 0, 0
    for aid, b in (dud.get('bots') or {}).items():
        if aid in excl or not b.get('coverage_end'): continue
        cs = (b.get('chat_coverage_start') or b.get('routine_coverage_start') or '')[:10]; ce = (b.get('coverage_end') or '')[:10]
        for d in ds:
            poss += 1
            if (cs and d < cs) or d > ce: continue
            cov += 1
            ch = (b.get('est_chat') or {}).get(d, [0, 0, 0]); ro = (b.get('est_routine') or {}).get(d, [0, 0, 0])
            bots += bot_usd(ch[1] + ro[1], ch[2] + ro[2], est)
    return coding, bots, cov, poss

# ---------- calibration factor (owner-local file; None until >= min_observations) ----------
def calibration_factor(cfg):
    c = cfg.get('calibration') or {}
    data = load(P(c.get('file', 'local/calibration.json')), {}) or {}
    obs = data.get('observations') or []
    if len(obs) < c.get('min_observations', 2): return None
    f = [o['bot_factor'] for o in obs if isinstance(o.get('bot_factor'), (int, float))][-c.get('rolling_n', 5):]
    if not f: return None
    lo, hi = c.get('clamp', [0.25, 4.0])
    return round(min(max(statistics.median(f), lo), hi), 2)
