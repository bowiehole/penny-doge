> Public generic scanner tree for the **Penny DOGE** Grok Bot template. Clone into `/workspace/doge` (or any path), then follow Install below. Owner data (`local/`, ledgers, webhooks) is created on install and never ships here.

# Penny DOGE: weekly fleet-usage scan (no AI in the crons)

Penny DOGE (as in penny pincher) looks at how your bots, routines and CLI coding sessions use model time, and once a week tells you where money is probably being wasted and what you could change. The scanning is plain bash and Python, so it costs **zero model tokens**. A model only runs when a short report is worth sending.

> **Naming note:** Penny DOGE was first called DOGE. The folder name (`doge/`), script names (`doge_webhook.py`), env vars (`DOGE_WEBHOOK_URL`, `DOGE_WEBHOOK_KEY`) and config keys (`doge_bot_id`) keep the old name on purpose so existing installs don't break. They're historical, not a different product.

## What it does

- **Weekly scan (Mon 07:05, cron):** reads local files only: bot chat stores, routine definitions and run history, and CLI session records. It writes a small summary (`summary/latest.json`, capped at 7,000 bytes) and a readable table (`summary/daily_table.md`). For bots flagged under A21 (always-on load) or A13 (memory logging) it also writes a line-level trim list per bot (`summary/trim/<bot>.md`, `trim.py`, no AI): the exact lines to cut or shorten, old text next to new, ~tokens reclaimed per line and in total. The owner picks lines; the owning bot applies them.
- **Spend view:** exact coding (CLI) cost first, then estimated bot chat/routine cost by bot and by day, week over week, month to date with a projection, and an optional budget or runway warning.
- **Advice:** up to 5 fresh candidates per week. Each has an advice ID (A1–A21), the signal that triggered it, a suggested change, and a plain `why` sentence. Examples: routines that almost never find anything, cadences much denser than needed, near-duplicate jobs on two bots, very long chats that still get turns, heavy reasoning effort on small coding sessions, bots that carry a lot of instruction and memory text on every turn, bots nobody opens any more, and bots whose messages pile up unread.
- **Wake:** only when something is new or at least 2× worse than last time (or a budget warning fires), it POSTs one small body to a webhook routine on your Penny DOGE bot. That routine reads the summary, checks at most 5 items live with the owning bots, and sends you one short report with an approve/skip question.
- **Daily new-bot check + spike guard (07:35, cron, no AI):** flags a newly installed bot or template whose schedule is denser than hourly, or that polls without a stay-quiet rule. The spike guard (`spike_guard.py`) compares each enabled routine's last 24h (runs and estimated $) with its trailing daily average, and catches loops (many runs in a short window beyond the schedule, or the same failure repeating). It needs a baseline, never alerts on stale data, and wakes once (`mode=spike`) per routine per cooldown. It stays silent otherwise.
- **Quick audit (first chat, no AI):** `python3 quick_audit.py` names the top 1–3 likely burners with one why line each, before any cron or webhook exists.
- **Usage-page calibration (no AI):** paste or screenshot the app's Usage page in chat; the bot reads the % remaining and runs `calibrate.py`, which records the reading in `local/calibration.json`, computes the burn rate, the run-out date vs the reset date, and the gap vs Penny DOGE's estimate. From 2 readings on, bot ~$ estimates are scaled by a clamped rolling median and labeled "calibrated". With no readings nothing changes.
- **Ledger:** anything already raised or skipped won't be raised again unless it gets 2× worse.

## What it never does

- **It only recommends.** Penny DOGE never edits, pauses or deletes another bot's routines, skills, memory or settings. You approve a change, and the owning bot makes it.
- It never puts a model in the cron jobs, and it never sends anything beyond the one wake POST (plus the setup probe).
- It never reads message content into its outputs. Only bot and routine names and counts go into the summary. Routine prompts are read in memory to compare keywords and are never stored.
- It never prints the webhook key or URL. `.webhook.json` stays owner-only (chmod 600).
- It never treats local data as confirmed. Every local signal is checked live with the owning bot before it becomes a recommendation.

## Install

```bash
python3 quick_audit.py     # first chat: top 1-3 likely burners (no AI, no cron, no webhook needed)
bash install.sh            # idempotent: folders, config, both cron lines; keeps every other crontab line
bash setup_webhook.sh      # webhook walkthrough + one probe (see below)
bash weekly_scan.sh --dry-run   # full scan without the POST; check summary/daily_table.md
```

`install.sh` copies `config/thresholds.default.json` to `config/thresholds.json` and `config/owner.example.json` to `local/owner.json` if they're missing. Running it again changes nothing.

Then send the first-run message in **FIRST_RUN.md** and fill in `local/owner.json` from the owner's answers.

## Webhook setup

1. On the Penny DOGE bot, create a **webhook-triggered** routine named `Penny DOGE threshold wake` with the prompt in `template/routine_prompts.md`. It must ignore bodies with `"probe": true`.
2. Run `bash setup_webhook.sh`. It asks for the **Webhook URL** and **Webhook key** from the routine panel. The key prompt is hidden. You can also set `DOGE_WEBHOOK_URL` / `DOGE_WEBHOOK_KEY` in the environment instead.
   - The key is cleaned: a pasted `Authorization:` or `Bearer ` prefix is stripped. Both auth headers are sent.
   - It sends ONE probe `{"probe": true}`. It saves `.webhook.json` (chmod 600) **only if the probe gets a 2xx**, and prints `probe OK`. A previous file is kept as `.webhook.json.bak`.
   - **401/403** means the key is wrong or was regenerated. Re-copy the Webhook key field from the routine panel and paste only the key, not "Bearer".
3. To re-check later without re-entering anything: `bash setup_webhook.sh --use-existing`.

All webhook code lives in `doge_webhook.py` (key cleanup, headers, no printing). `wake.py`, `new_bot_check.py` and `setup_webhook.sh` all use it.

## Config knobs

Generic thresholds live in `config/thresholds.json`. The shipped defaults are in `thresholds.default.json`, and every key has a `_doc` note. The ones most worth tuning after 2–3 weeks:

| Knob | Default | What it does |
|---|---|---|
| `schedule.cron` / `new_bot_check.cron` | `5 7 * * 1` / `35 7 * * *` | When the scans run (box local time) |
| `freshness.stale_days` | 10 | Signals older than this are listed as stale, never raised |
| `output.top_n` / `summary_max_bytes` | 5 / 7000 | Candidates per week; size cap of latest.json |
| `wake.worse_factor` | 2.0 | Re-raise an item only when it gets this much worse |
| `wake.always_weekly_cost` | false | true = quiet weeks still send a one-line cost note (one short wake) |
| `alert_level` (in `local/owner.json`) | `normal` | `quiet` = top 3, re-raise at 3×, spike guard 3×; `normal` = these defaults; `chatty` = top 8, re-raise at 1.5×, spike guard 1.5×, cost note on quiet weeks. Mapping in `alert_levels` |
| `spike_guard.*` | factor 2.0, 14-day baseline | Daily spike/loop guard floors, stale cutoff (36 h) and cooldown (3 days) |
| `calibration.*` | 2 readings, last 5, clamp 0.25–4 | Usage-page correction factor for bot ~$ estimates |
| `quick_audit.*` | top 3 | First-chat quick audit size and floors |
| `daily_usage.min_bot_coverage` | 0.5 | Below this share of bots with fresh local data, bot-work $ is hidden and only exact coding is shown |
| `daily_usage.table_layout` | `split_spend` | `split_spend` (coding vs bot work by bot) or `by_day` |
| `estimates.*` | derived | Token prices used for bot-work estimates (they should match the CLI's implied $/token) |
| `advice.A*` | see file | Per-advice thresholds (runs, quiet %, fires/hour, MB, ...) |
| `advice.trim.*` (`para_max_tokens`, `fact_max_tokens`, `dup_jaccard`, `oneoff_age_days`, `rollup_min`, `max_lines_per_bot`, `enabled`) | 120 / 100 / 0.85 / 14 / 3 / 25 / true | Line-level trim lists for A21/A13 bots (`summary/trim/`, owner-local) |
| `advice.A21.max_always_on_tokens` / `max_routine_prompt_tokens` / `count_platform_sections` | 6000 / 1500 / true | Always-on load flag; set `count_platform_sections` false to judge only the owner-controlled part (instructions + memory) |
| `idle_bots.min_days_unused` | 30 | Days both unopened and without a human message before a bot counts as idle |
| `budget.warn_days` / `rate_days` | 7 / 7 | Runway warning and trailing-rate window |
| `paths.cli_repo_roots` | `~`, `/workspace` | Folders whose first subfolder is a repo (`~` = home). Add owner-specific roots in `local/owner.json` `cli_repo_roots` |

Owner data lives only in `local/owner.json`: `doge_bot_id`, `exclude_bot_ids`, `keep_bots`, `cli_repo_bots` (repo → bot), `cli_repo_roots`, `resolved_repos` (optional `before` date), `handled`, `redact_terms`, `budget` (`monthly_usd`, `credit_balance_usd`, `balance_as_of`; all optional), `alert_level`, `plan.monthly_usd` (for calibration when the Usage page shows only %), and `auto_slowdown` (`below_pct`, `bots`). **Auto-slowdown is OFF** (`below_pct: null`). Only the owner can turn it on, in chat, by naming the % and the bots. When it is on and the remaining % drops below it, the bot may send only those named bots one slow-down request without asking first, and must tell the owner right after. It never edits their routines. Usage readings go to `local/calibration.json`. The other owner-only items are `local/calibration.json`, `ledger/` (incl. `spike.json`), `history/`, `summary/` (incl. `quick_audit.*`, `spike.json`, `trim/`), `.webhook.json` and `cron.log`. None of these ship with the template.

## Data limits (read before trusting a number)

- **Coding (CLI) cost is exact.** It comes from per-turn CLI records, starting at the first record on the box.
- **Bot chat/routine cost is an estimate.** It uses a replay-token proxy from local transcripts, priced at the CLI's implied $/token. Local snapshots can lag live state by weeks. When fewer than `min_bot_coverage` of bots have fresh data, the report hides bot cost and says so in one sentence instead of showing walls of `n/a`.
- **A20 (long chat) dollars are an upper bound.** The app summarizes old history, so the real cost per reply levels off. The fix is to save learnings to memory, a skill or a file first, then reset.
- **Model and effort for bots and routines aren't recorded locally.** A19 only covers CLI sessions.
- **Always-on load (A21) is partial.** Instructions are live; memory and platform section sizes come from each bot's last prompt snapshot. Skills, tool schemas and several platform sections are only visible as hashes, so the load is a lower bound for those parts.
- **View and last-message stamps can be stale.** A bot's local store can stop syncing while the owner keeps using it live. Penny DOGE only calls a bot idle when its store is fresh, two signals agree, and there's no recent coding for it. Otherwise the bot is listed as "stale local data, not judged" with no day counts.
- **Routine state can be stale.** A routine paused live may still look busy locally. That's why the wake routine confirms live before reporting anything.
- **Unmapped CLI repos** are listed until you add them to `cli_repo_bots`. **Normal flow: coding bots claim their own folders.** A bot that starts a new repo messages Penny DOGE once ("map <repo> → me"). Penny runs `claim_repo.py`, which updates the map; bare roots are refused and an existing mapping is never overwritten. You're asked only when unclaimed coding is large (≥ $10, or ≥ 25% of the week's coding and ≥ $5) after a 3-day grace period, or when two bots claim the same folder (`unmapped_ask`, `unmapped_cli.*`): "Why I flagged it: this coding spend isn't linked to a bot yet, so the report can't show who ran it." Penny DOGE never guesses a mapping. **Coding bots must use named project folders** (`/workspace/<product>`), never the bare `/workspace` or home folder. Run CLI jobs from the project folder, not a bare root like `/workspace`, so each repo maps cleanly to one bot.
