#!/usr/bin/env python3
"""Penny Usage-page calibration (no AI). The bot reads the owner's pasted Usage page (text or screenshot) and passes
the numbers here; this script only records and computes.

  python3 calibrate.py --pct-remaining 62 [--reset 2026-10-15] [--at "2026-10-04 21:30"]
                       [--remaining-usd 31.0] [--plan-usd 50] [--dry-run] [--no-refresh]
  python3 calibrate.py --json '{"pct_remaining": 62, "reset": "2026-10-15"}'

Computes % remaining, burn rate (vs the previous observation, or since the period start when it's the first),
projected run-out date at the current pace vs the reset date, and the gap between observed usage and Penny's own
estimate (exact coding + estimated bot work) over the same window. Appends each observation to the owner-local
calibration file (never shipped). summarize.py uses the rolling median bot factor once >= 2 observations exist.
--dry-run: compute and print, append nothing. Prints a short reply plus one JSON line; never prints secrets.
"""
import argparse, json, os, subprocess, sys, datetime as dt
HOME = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HOME)
from penny_common import P, load, spend_on_days, calibration_factor, apply_alert_level

ap = argparse.ArgumentParser()
ap.add_argument('--pct-remaining', type=float); ap.add_argument('--reset'); ap.add_argument('--at')
ap.add_argument('--remaining-usd', type=float); ap.add_argument('--plan-usd', type=float)
ap.add_argument('--json'); ap.add_argument('--dry-run', action='store_true'); ap.add_argument('--no-refresh', action='store_true')
a = ap.parse_args()
if a.json:
    j = json.loads(a.json)
    for k in ('pct_remaining', 'reset', 'at', 'remaining_usd', 'plan_usd'):
        if j.get(k) is not None and getattr(a, k) is None: setattr(a, k, j[k])
if a.pct_remaining is None or not (0 <= float(a.pct_remaining) <= 100): sys.exit('calibrate: --pct-remaining 0..100 is required')

CFG = load(P('config/thresholds.json')) or load(P('config/thresholds.default.json'))
OWNER = load(P(CFG['paths']['owner_file']), {}) or {}
CFG = apply_alert_level(CFG, OWNER); C = CFG['calibration']; EST = CFG['estimates']
def parse(s):
    for f in ('%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M', '%Y-%m-%d'):
        try: return dt.datetime.strptime(str(s)[:16], f)
        except ValueError: pass
    sys.exit(f'calibrate: cannot read date {s!r} (use YYYY-MM-DD or "YYYY-MM-DD HH:MM")')
at = parse(a.at) if a.at else dt.datetime.now().replace(second=0, microsecond=0)
reset = parse(a.reset) if a.reset else None
pct = float(a.pct_remaining)

if not a.no_refresh:   # refresh the exact coding data and the per-day estimates (no AI, a few seconds)
    for s in ('cli_usage.py', 'daily_usage.py'):
        subprocess.run([sys.executable, os.path.join(HOME, s)], cwd=HOME, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
dud = load(P('daily_usage.json'), {}) or {}
excl = set(OWNER.get('exclude_bot_ids', [])) | {OWNER.get('doge_bot_id')}

path = P(C['file']); data = load(path, {}) or {}; obs = data.get('observations') or []
plan = a.plan_usd or (OWNER.get('plan') or {}).get('monthly_usd') or data.get('plan_usd')
prev = obs[-1] if obs else None
prev_at = parse(prev['at']) if prev else None
reset_happened = bool(prev and (pct > prev['pct_remaining'] + 0.5 or (prev.get('reset') and at.date().isoformat() >= prev['reset'])))
if prev and not reset_happened and at > prev_at:
    w0, used_pct, basis = prev_at, prev['pct_remaining'] - pct, 'since last observation'
elif reset:
    w0, used_pct, basis = reset - dt.timedelta(days=C['period_days_default']), 100 - pct, 'since the period start (estimated from the reset date)'
else:
    w0, used_pct, basis = None, None, None
elapsed_d = (at - w0).total_seconds() / 86400 if w0 else None
burn = used_pct / elapsed_d if w0 and elapsed_d and elapsed_d > 0.04 and used_pct is not None and used_pct >= 0 else None
run_out = at + dt.timedelta(days=pct / burn) if burn and burn > 0 else None

# observed $ in the window vs Penny's estimate over the same days
days = [(w0.date() + dt.timedelta(days=i)).isoformat() for i in range(1, (at.date() - w0.date()).days + 1)] if w0 else []
if w0 and w0.date() == at.date(): days = [at.date().isoformat()]
coding, bots, cov, poss = spend_on_days(days, dud, EST, excl) if days else (0.0, 0.0, 0, 0)
observed = None
if prev and not reset_happened and a.remaining_usd is not None and prev.get('remaining_usd') is not None:
    observed = prev['remaining_usd'] - a.remaining_usd
elif used_pct is not None and plan: observed = used_pct / 100 * float(plan)
est_total = coding + bots
gap = round(observed / est_total, 2) if observed is not None and est_total > 0 else None
bot_cov = round(cov / poss, 2) if poss else 0
bot_factor = round((observed - coding) / bots, 2) if observed is not None and bots > 0 and bot_cov >= C['min_bot_coverage'] and observed > coding else None

rec = dict(at=at.strftime('%Y-%m-%d %H:%M'), pct_remaining=pct, reset=reset.date().isoformat() if reset else None,
           remaining_usd=a.remaining_usd, window_start=w0.strftime('%Y-%m-%d %H:%M') if w0 else None, used_pct=None if used_pct is None else round(used_pct, 2),
           observed_usd=None if observed is None else round(observed, 2), est_coding_usd=round(coding, 2), est_bots_usd=round(bots, 2),
           bot_cov=bot_cov, gap_ratio=gap, bot_factor=bot_factor)
if not a.dry_run:
    obs.append(rec); data['observations'] = obs[-50:]
    if a.plan_usd: data['plan_usd'] = a.plan_usd
    os.makedirs(os.path.dirname(path), exist_ok=True); tmp = path + '.tmp'
    with open(tmp, 'w') as f: json.dump(data, f, indent=1)
    os.replace(tmp, path)
factor = calibration_factor(CFG)
sl = OWNER.get('auto_slowdown') or {}
slow_due = bool(sl.get('below_pct') is not None and sl.get('bots') and pct < float(sl['below_pct']))

lines = [f"Usage left: {pct:g}%" + (f" (resets {reset:%b %-d})" if reset else "") + "."]
if burn is not None:
    lines.append(f"Pace: {burn:.1f}% a day {basis}." + (f" At this pace it runs out {run_out:%b %-d}" + (
        f", {(reset - run_out).days} days before the reset." if reset and run_out < reset else (", after the reset, so you're fine." if reset else ".")) if run_out else ""))
else: lines.append("Pace: not enough to go on yet (I need a second reading, or the reset date).")
if gap is not None: lines.append(f"Estimate gap: you used about ${observed:.2f}; I estimated ${est_total:.2f} (coding ${coding:.2f} exact + bots ~${bots:.2f}), so actual/estimate = {gap:g}×.")
elif observed is None: lines.append("Estimate gap: can't compare yet; I need your plan's credit in $ (or the $ remaining) to turn % into dollars.")
else: lines.append("Estimate gap: no estimate for that window yet (local bot data doesn't cover it).")
if bot_factor is None and gap is not None: lines.append(f"Bot-work factor not updated: local bot data covers only {int(bot_cov * 100)}% of that window.")
lines.append(f"Calibration: {'active, bot-work estimates × ' + format(factor, 'g') if factor else 'not active yet (needs 2+ readings)'}." + (" (dry run: nothing saved)" if a.dry_run else ""))
if slow_due: lines.append(f"Auto slow-down threshold ({sl['below_pct']}%) is crossed: the routine may ask {', '.join(sl['bots'])} to slow down now and must tell the owner afterwards.")
print("\n".join(lines))
print(json.dumps(dict(observation=rec, observations=len(obs), run_out=run_out.strftime('%Y-%m-%d') if run_out else None,
                      burn_pct_per_day=None if burn is None else round(burn, 2), calibration_factor=factor,
                      auto_slowdown_due=slow_due, auto_slowdown_bots=sl.get('bots') if slow_due else [], dry_run=a.dry_run)))
