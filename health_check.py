#!/usr/bin/env python3
"""Penny DOGE health check (no AI, cheap). Is the box timer alive?

Checks: (a) the crontab command exists and both Penny lines are in the crontab, (b) a cron daemon is
running, (c) cron.log shows a *scheduled* weekly scan within 8 days and a scheduled daily new-bot check
within 2 days (a run counts as scheduled when its log time matches the cron schedule, so manual runs
don't hide a dead timer). Prints ONE JSON line:
  {"ok", "problems": [], "last_weekly", "last_daily", "repaired", "fix"}
  python3 health_check.py            check only
  python3 health_check.py --repair   if anything is wrong: run `bash install.sh` (installs/starts cron,
                                     restores missing Penny lines), re-check, set repaired true/false
Always exits 0; the caller reads the JSON. Never prints webhook data or other secrets.
"""
import datetime as dt, glob, json, os, re, shutil, subprocess, sys

DOGE = os.path.dirname(os.path.abspath(__file__))
WEEKLY_MAX_DAYS, DAILY_MAX_DAYS, MATCH_WINDOW_MIN = 8, 2, 5
NOW = dt.datetime.now()


def cfg():
    for name in ("config/thresholds.json", "config/thresholds.default.json"):
        try:
            return json.load(open(os.path.join(DOGE, name)))
        except Exception:
            pass
    return {}


C = cfg()
WEEKLY_CRON = C.get("schedule", {}).get("cron", "5 7 * * 1")
DAILY_CRON = C.get("new_bot_check", {}).get("cron", "35 7 * * *")
_log = C.get("paths", {}).get("cron_log", "cron.log")
CRON_LOG = _log if os.path.isabs(_log) else os.path.join(DOGE, _log)


def field_ok(spec, val, lo, hi):
    """Minimal cron field matcher: *, lists, ranges, steps."""
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/", 1); step = int(s)
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a, b = map(int, part.split("-", 1))
        else:
            a = b = int(part)
        if a <= val <= b and (val - a) % step == 0:
            return True
    return False


def matches_schedule(t, spec):
    """True if t is 0..MATCH_WINDOW_MIN minutes after a fire time of the 5-field cron spec."""
    try:
        mi, ho, dom, mon, dow = spec.split()[:5]
    except ValueError:
        return False
    for back in range(MATCH_WINDOW_MIN + 1):
        x = t - dt.timedelta(minutes=back)
        cdow = (x.weekday() + 1) % 7
        if (field_ok(mi, x.minute, 0, 59) and field_ok(ho, x.hour, 0, 23) and field_ok(mon, x.month, 1, 12)
                and field_ok(dom, x.day, 1, 31) and (field_ok(dow, cdow, 0, 7) or (cdow == 0 and field_ok(dow, 7, 0, 7)))):
            return True
    return False


TS = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)(?: \S+)? Penny (.*)$")


def last_runs():
    weekly = daily = None
    try:
        lines = open(CRON_LOG, errors="replace").read().splitlines()
    except OSError:
        return None, None
    for line in lines:
        m = TS.match(line)
        if not m:
            continue
        try:
            t = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        rest = m.group(2)
        if rest.startswith("start mode=run") and matches_schedule(t, WEEKLY_CRON):
            weekly = max(weekly, t) if weekly else t
        elif rest.startswith("new_bot_check ") and matches_schedule(t, DAILY_CRON):
            daily = max(daily, t) if daily else t
    return weekly, daily


def cron_running():
    for comm in glob.glob("/proc/[0-9]*/comm"):
        try:
            if open(comm).read().strip() in ("cron", "crond"):
                return True
        except OSError:
            pass
    return False


def installed_since():
    try:
        return dt.datetime.strptime(open(os.path.join(DOGE, "logs/cron_since")).read().strip(), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def check():
    infra, stale = [], []
    if not shutil.which("crontab"):
        infra.append("crontab command is missing (cron package not installed)")
    else:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        tab = r.stdout if r.returncode == 0 else ""
        live = [l for l in tab.splitlines() if l.strip() and not l.lstrip().startswith("#")]
        if not any(f"{DOGE}/weekly_scan.sh" in l for l in live):
            infra.append("weekly scan line is missing from the crontab")
        if not any(f"{DOGE}/new_bot_check.py" in l for l in live):
            infra.append("daily new-bot check line is missing from the crontab")
    if not cron_running():
        infra.append("cron daemon is not running")
    weekly, daily = last_runs()
    since = installed_since()
    for label, last, max_days in (("weekly scan", weekly, WEEKLY_MAX_DAYS), ("daily new-bot check", daily, DAILY_MAX_DAYS)):
        if last and NOW - last <= dt.timedelta(days=max_days):
            continue
        if not last and since and NOW - since <= dt.timedelta(days=max_days):
            continue   # fresh install: first scheduled run not due yet
        when = f"last one {last:%Y-%m-%d %H:%M}" if last else "none recorded"
        stale.append(f"no scheduled {label} in {max_days}+ days ({when})")
    fmt = lambda t: t.strftime("%Y-%m-%d %H:%M") if t else None
    return infra, stale, fmt(weekly), fmt(daily)


def main():
    infra, stale, lw, ld = check()
    repaired, fix, note = False, None, None
    if (infra or stale) and "--repair" in sys.argv[1:]:
        before, had_infra = infra + stale, bool(infra)
        try:
            r = subprocess.run(["bash", os.path.join(DOGE, "install.sh")], capture_output=True, text=True, timeout=600)
            err = [l for l in (r.stderr or "").splitlines() if l.startswith(("Problem:", "Fix:"))]
            rc = r.returncode
        except Exception as e:
            err, rc = [f"Problem: install.sh did not finish ({type(e).__name__})"], 1
        infra, stale, lw, ld = check()
        repaired = had_infra and rc == 0 and not infra
        if repaired and stale:
            note = "cron is back; the stale-run warning clears after the next scheduled run"
        elif not had_infra and rc == 0 and not infra:
            fix = f"cron and both lines look fine but scheduled runs were missed; check {os.path.basename(CRON_LOG)} and logs/run_all.last.log"
        elif not repaired:
            fix = " ".join(err) or f"Fix: run `bash {DOGE}/install.sh` as a user with sudo and read its message"
        problems = before
    else:
        problems = infra + stale
        if problems:
            fix = (f"run `python3 {DOGE}/health_check.py --repair` (or `bash {DOGE}/install.sh`)" if infra
                   else f"cron looks fine but runs were missed; check {os.path.basename(CRON_LOG)} and logs/run_all.last.log")
    ok = not (infra or stale)
    out = {"ok": ok, "problems": problems, "last_weekly": lw, "last_daily": ld, "repaired": repaired}
    if fix:
        out["fix"] = fix
    if note:
        out["note"] = note
    print(json.dumps(out))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:   # always one JSON line, always exit 0
        print(json.dumps({"ok": False, "problems": [f"health check crashed: {type(e).__name__}"], "last_weekly": None,
                          "last_daily": None, "repaired": False}))
    sys.exit(0)
