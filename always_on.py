#!/usr/bin/env python3
"""A21 always-on load collector (no AI). For each bot: sizes of the text it carries on every turn.
Outputs lengths and counts only, never text. Reads store.db kv rows by explicit key name (the prompt
snapshots); never selects or decodes `metadata`. Writes always_on.json next to this script."""
import json, glob, os, shutil, sqlite3, datetime as dt
HOME = os.path.dirname(os.path.abspath(__file__))
def load(p, d=None):
    try:
        with open(p) as f: return json.load(f)
    except Exception: return d
CFG = load(os.path.join(HOME, 'config/thresholds.json')) or load(os.path.join(HOME, 'config/thresholds.default.json'))
A = os.path.expanduser(CFG['paths']['agents_dir']).rstrip('/'); ROOT = os.path.dirname(A)
SNAP_KEYS = ('agentProfilePromptSnapshot', 'promptPrefixSnapshot', 'promptSectionSnapshots')   # never 'metadata'
def text_len(p):
    try:
        with open(p, errors='replace') as f: t = f.read()
    except OSError: return 0, 0
    return len(t), sum(1 for l in t.splitlines() if l.lstrip().startswith(('- ', '* ')))
def snapshot(db):
    tmp = os.path.join('/tmp', 'doge_ao_' + os.path.basename(os.path.dirname(db)) + '.db')
    try:
        shutil.copy(db, tmp)
        for s in ('-wal', '-shm'):
            if os.path.exists(db + s): shutil.copy(db + s, tmp + s)
        c = sqlite3.connect(tmp)
        kv = {k: v for k, v in c.execute('select key, value from kv where key in (%s)' % ','.join('?' * len(SNAP_KEYS)), SNAP_KEYS)}
        c.close()
    except Exception: return None
    finally:
        for s in ('', '-wal', '-shm'):
            if os.path.exists(tmp + s): os.remove(tmp + s)
    if not kv: return None
    try:
        ps = json.loads(kv.get('promptSectionSnapshots') or '{}'); pp = json.loads(kv.get('promptPrefixSnapshot') or '{}')
        ap = json.loads(kv.get('agentProfilePromptSnapshot') or '{}')
    except Exception: return None
    sec = {k: len((v or {}).get('render') or '') for k, v in ps.items() if isinstance(v, dict)}
    sec['profile'] = len(ap.get('profileSection') or '')
    at = pp.get('recordedAtMs')
    return dict(at=dt.datetime.fromtimestamp(at / 1000).strftime('%Y-%m-%d %H:%M') if at else None, sections=sec,
                prefix_sections=len(pp.get('sections') or []), tools=pp.get('toolCount'))
bots = {}
for d in sorted(glob.glob(A + '/*/')):
    aid = os.path.basename(d.rstrip('/'))
    prof = load(d + 'profile.json')
    if not prof: continue
    mem_files = [d + 'memory/profile.md', f'{ROOT}/user-memory/by-agent/{aid}/profile.md'] + glob.glob(f'{ROOT}/projects/*/memory/by-agent/{aid}/profile.md')
    mem = [text_len(p) for p in mem_files if os.path.exists(p)]
    skills = glob.glob(d + 'memory/skills/*.md')
    prompts = {}
    for p in glob.glob(d + 'automations/*/automation.json'):
        j = load(p, {}) or {}
        prompts[os.path.basename(os.path.dirname(p))] = len(j.get('prompt') or '')
    bots[aid] = dict(name=prof.get('name'), instr_chars=len(prof.get('description') or ''),
                     mem_chars=sum(m[0] for m in mem), mem_facts=sum(m[1] for m in mem), mem_files=len(mem),
                     skills=len(skills), skills_chars=sum(text_len(p)[0] for p in skills),
                     routine_prompt_chars=prompts, snap=snapshot(d + 'store.db') if os.path.exists(d + 'store.db') else None)
lib = glob.glob(f'{ROOT}/workflows/*/SKILL.md')
json.dump(dict(generated_at=dt.datetime.now().strftime('%Y-%m-%d %H:%M'), skill_library=len(lib), bots=bots),
          open(os.path.join(HOME, 'always_on.json'), 'w'), separators=(',', ':'))
print('always_on ok', len(bots), 'bots;', sum(1 for b in bots.values() if b['snap']), 'with prompt snapshots')
