# Penny DOGE template manifest

## Ships with the template (generic, no owner data)

| Path | Purpose |
|---|---|
| `README.md`, `FIRST_RUN.md` | What Penny DOGE does or never does, install, webhook setup, knobs, data limits; first-run message and questions |
| `install.sh` | Idempotent installer: folders, default config, both cron lines (keeps all other crontab lines) |
| `setup_webhook.sh` | Webhook walkthrough: URL + key, one `{"probe": true}`, saves `.webhook.json` (chmod 600) only on a 2xx |
| `weekly_scan.sh`, `run_all.sh` | Weekly no-AI pipeline (`--dry-run`, `--probe`) |
| `fleet_scan.py`, `routine_runs.py`, `routine_quiet.py`, `routine_cost.py`, `routine_engagement.py`, `cli_usage.py`, `daily_usage.py`, `always_on.py` | Local-file collectors (always_on.py: lengths only, for A21) |
| `summarize.py` | Advice rules, ledger, spend tables, graceful degrade, `latest.json` (≤ 7,000 bytes) |
| `wake.py`, `new_bot_check.py`, `doge_webhook.py` | Wake sender, daily new-bot check, the single webhook module |
| `spike_guard.py` | Daily fleet-wide spike/loop guard, called by `new_bot_check.py` (no AI) |
| `quick_audit.py` | First-chat quick audit: top 1–3 likely burners (no AI) |
| `calibrate.py` | Usage-page calibration: records readings, burn rate, run-out date, estimate gap, correction factor (no AI) |
| `claim_repo.py` | Repo claims from coding bots ("map <repo> → me"): writes the owner repo map; refuses bare roots; never overwrites (conflicts go to the owner) |
| `trim.py` | Line-level trim lists for A21/A13 bots: old → new text, ~tokens per line (no AI, recommend only; called by `summarize.py`, output owner-local in `summary/trim/`) |
| `penny_common.py` | Shared helpers (alert level, cron rates, spend estimates, calibration factor) |
| `config/thresholds.default.json` | Generic thresholds with `_doc` notes (install copies it to `thresholds.json`) |
| `config/owner.example.json` | Blank owner file (install copies it to `local/owner.json`) |
| `template/routine_prompts.md` | Prompts for the "Penny DOGE threshold wake" (report / cost_only / new_bot_check / spike) and "Settings check-in" routines, plus chat instructions (quick audit, Usage-page calibration, auto-slowdown, alert level) |
| `template/skills/template-settings-checkin/SKILL.md` | Settings check-in skill (install it as a skill on the Penny DOGE bot) |
| `inventory.md` | Signals, gaps, advice catalog, design, outcomes-log template and template notes (generic only) |

## Never ships (owner-only, created on the owner's box)

`local/` (owner.json, known_bots.json, calibration.json (Usage-page readings), inventory_local.md with the owner's findings, roster, outcomes log and own-cost table), `ledger/` (incl. spike.json cooldowns), `history/`, `summary/` (incl. quick_audit.json/.md, spike.json, trim/<bot>.md line-level trim lists, which quote the owner's bot text), `logs/`, `.webhook.json`, `.webhook.json.bak`, `cron.log`, `wake_queue.log`, `config/thresholds.json` (owner-tuned copy), `config/routine_prompt.md` (notes on the owner's live routine), and the collector outputs `*.json` in the Penny DOGE folder (fleet_scan.json, cli_sessions.json, daily_usage.json, always_on.json, routine_*.json).

## Routines the owner's Penny DOGE bot needs

1. **Penny DOGE threshold wake**: webhook trigger, created during setup (README "Webhook setup").
2. **Settings check-in**: one-time, 14 days after first run, rescheduled by the skill (FIRST_RUN.md section 4).

The crons (weekly scan, daily new-bot check) are box crontab lines, not routines, and use no model.
