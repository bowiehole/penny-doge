#!/usr/bin/env python3
"""Penny DOGE repo claim (no AI). A coding bot claims its own project folder once: "map <repo> -> me".
The Penny DOGE bot runs:  python3 claim_repo.py --repo <name> --bot-id <id> [--dry-run]

- Writes local/owner.json cli_repo_bots[<repo>] = {"bot": <name>, "bot_id": <id>} (backup: owner.json.bak-claim).
- Idempotent: the same claim again changes nothing.
- Refuses bare roots (workspace, home, tmp, '~', or any cli_repo_roots folder): bots must use a named project folder.
- Conflict: if the repo is already mapped to a different bot, nothing is overwritten. The claim is recorded in
  cli_repo_conflicts and the owner is asked (unmapped_ask lists conflicts). Exit code 2.
Prints one JSON line. Never touches anything outside local/owner.json.
"""
import json, os, sys, glob, shutil, argparse, datetime as dt
HOME = os.path.dirname(os.path.abspath(__file__))
def P(rel): return rel if os.path.isabs(rel) else os.path.join(HOME, rel)
def load(p, d=None):
    try:
        with open(p) as f: return json.load(f)
    except Exception: return d
CFG = load(P('config/thresholds.json')) or load(P('config/thresholds.default.json'))
ap = argparse.ArgumentParser(); ap.add_argument('--repo', required=True); ap.add_argument('--bot-id', required=True)
ap.add_argument('--dry-run', action='store_true'); a = ap.parse_args()
owner_p = P(CFG['paths']['owner_file']); OWNER = load(owner_p, {}) or {}
repo = a.repo.strip().rstrip('/').split('/')[-1]
roots = set(CFG['paths'].get('cli_repo_roots', [])) | set(OWNER.get('cli_repo_roots', []))
bare = {'', '.', '~', 'workspace', 'home', 'tmp', 'root', 'box'} | {os.path.basename(os.path.expanduser(r).rstrip('/')) for r in roots}
def out(code, **kw): print(json.dumps(dict(repo=repo, **kw))); sys.exit(code)
if repo.lower() in bare:
    out(3, status='refused', why='bare root folder; use a named project folder like /workspace/<product>')
prof = load(os.path.join(os.path.expanduser(CFG['paths']['agents_dir']), a.bot_id, 'profile.json'))
if not prof: out(4, status='refused', why='unknown bot id')
name = prof.get('name') or a.bot_id
m = OWNER.setdefault('cli_repo_bots', {})
cur = m.get(repo)
cur_id = (cur.get('bot_id') if isinstance(cur, dict) else None) if cur else None
cur_name = (cur.get('bot') if isinstance(cur, dict) else cur) if cur else None
if cur and (cur_id == a.bot_id or (not cur_id and cur_name == name)):
    out(0, status='already_mapped', bot=name)
today = dt.date.today().isoformat()
if cur:   # owned by someone else: never overwrite; ask the owner
    conf = OWNER.setdefault('cli_repo_conflicts', [])
    if not any(c.get('repo') == repo and c.get('claimed_by_id') == a.bot_id for c in conf):
        conf.append(dict(repo=repo, mapped_to=cur_name, claimed_by=name, claimed_by_id=a.bot_id, date=today))
    status, code = 'conflict', 2
else:
    m[repo] = dict(bot=name, bot_id=a.bot_id); status, code = 'mapped', 0
if a.dry_run: out(code, status=status, bot=name, dry_run=True)
if os.path.exists(owner_p): shutil.copy2(owner_p, owner_p + '.bak-claim')
tmp = owner_p + '.tmp'; json.dump(OWNER, open(tmp, 'w'), indent=2); os.replace(tmp, owner_p)
out(code, status=status, bot=name)
