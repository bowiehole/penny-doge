# Penny DOGE first run

Start with the quick audit (section 0) on the very first chat, before the crons or the webhook exist. Then, after `install.sh` and `setup_webhook.sh` (see README.md), Penny DOGE sends **one** short message, saves the answers to `local/owner.json`, and sets up the settings check-in. Everything after that runs on its own.

## 0. Quick audit (first chat, before crons or webhook)

- Run `python3 quick_audit.py` (no AI; it refreshes the local collectors first, or add `--no-refresh`). It writes `summary/quick_audit.md` and `summary/quick_audit.json`.
- Send `summary/quick_audit.md` as is: the top 1–3 likely burners (heavy routines first; at most one long chat, whose $ is an upper bound), each with one why line, plus a note when local data is old.
- Say the numbers come from local files and are confirmed live with the owning bot before anything changes. Recommend only.
- Then continue with install (README) and the steps below.

## 1. Before messaging (no questions yet)

- Confirm the owner's timezone from the app or their profile. Don't assume it. The cron times and the check-in use it.
- Run `bash weekly_scan.sh --dry-run`, then read the top of `summary/daily_table.md` and the list of unmapped CLI repos. They help you pre-fill the repo → bot question below.
- Set `doge_bot_id` in `local/owner.json` to this bot's id, so Penny DOGE never flags itself.

## 2. The first-run message (send once)

> Hi, I'm Penny DOGE. Once a week (Mondays, 7:05 your time) I scan how your bots, routines and coding sessions use model time, using plain scripts with no AI. I only message you when something looks wasteful and is new or getting worse. I **only recommend**: I never change another bot's routines, skills or memory; you approve and that bot makes the change.
>
> A few quick questions, all optional:
> 1. **Budget:** do you want a monthly budget, or your current credit balance, so I can warn you before you run out? (A number and today's date is enough. Skip if not.)
> 2. **Coding repos** *(only if large unclaimed coding exists)*: which bot does each of these repos belong to? *(list the top unmapped repos)* From now on your coding bots claim their own folders with me, so I won't ask again unless something big stays unclaimed.
> 3. **Keep list:** are there bots you never want me to suggest retiring, even if they look unused?
> 4. **How chatty?** quiet (top 3, only big jumps), normal (default), or chatty (top 8, smaller jumps, plus a one-line cost note on quiet weeks)?
> 5. **Calibrate:** paste or screenshot your Usage page any time and I'll tell you how much is left, when you'd run out at this pace, and how far off my estimates are. After two readings I scale my estimates to match.
>
> In two weeks I'll show you your settings once so you can tweak anything; you pick how often after that.

Keep it to this one message. No report yet: the first weekly scan sends that.

## 3. Save the answers (owner data only, in `local/owner.json`)

| Answer | Field |
|---|---|
| Monthly budget | `budget.monthly_usd` |
| Credit balance + date | `budget.credit_balance_usd`, `budget.balance_as_of` (YYYY-MM-DD) |
| Repo → bot | `cli_repo_bots`: `{"repo-prefix": "Bot name"}` |
| Keep list | `keep_bots`: `["Bot name", ...]` |
| Alert level | `alert_level`: `quiet` / `normal` / `chatty` (default `normal`) |
| Plan $ per period (if the Usage page shows only %) | `plan.monthly_usd` |
| Usage-page readings | not here: `calibrate.py` appends them to `local/calibration.json` |
| Auto-slowdown (OFF) | `auto_slowdown`: `{"below_pct": null, "bots": []}`. Only the owner turns it on, in chat, by naming the % and the bots. Never offer it pre-filled |
| Already handled elsewhere | `handled`: `[{"bot_id": "...", "slug": "*"}]` |

Leave anything unanswered blank. Penny DOGE works without it and says "no budget set". Never put owner data in `config/`.

## 4. Settings check-in (from `template/skills/template-settings-checkin/SKILL.md`)

At the end of first-run setup, follow that skill's "At the end of first-run setup" steps:

1. If a routine named **Settings check-in** already exists, keep it.
2. Otherwise create it **once**, 14 days from today, at a daytime hour in the confirmed timezone, at an off-the-hour minute (for example 10:17). Prompt: `Run the template-settings-checkin skill (When it fires).`
3. The one-line notice is already the last line of the first-run message above.
4. If a routine named **Penny DOGE health check** doesn't exist yet, create it: scheduled weekly, Mondays at about 8:15am in the confirmed timezone (after the 7:05 scan), with the prompt in template/routine_prompts.md and `<PENNY_DIR>` filled in. It runs `health_check.py --repair` and stays silent unless cron is broken or scans were missed. This is what catches a box update that silently removed cron.

When it fires, the skill sends one message listing Penny DOGE's settings in plain words:
- the weekly scan day and time, and the daily new-bot check
- the wake routine's name, and whether quiet weeks send a cost note (`always_weekly_cost`)
- the alert level (quiet / normal / chatty) and what it means: quiet = top 3, re-raise at 3×, spike guard at 3×; normal = defaults; chatty = top 8, re-raise at 1.5×, spike guard at 1.5×, cost note on quiet weeks
- the daily spike guard (on/off, its factor) and Usage-page calibration (number of readings; whether estimates are calibrated)
- auto-slowdown: off, or the % and the named bots the owner set
- the candidates per week (`top_n`), the re-raise rule (2× worse), and the stale cutoff (`stale_days`)
- the budget or balance (or "not set"), the keep list, and how many repos are mapped (unmapped ones named)

It ends with "Want to change anything?" and a 2/3/4/6-week question widget. It then reschedules the same routine, and never changes a setting unasked.
