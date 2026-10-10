#!/usr/bin/env python3
"""Penny trim follow-up for A21 (always-on load) and A13 (memory log). No AI, recommend only.

For each bot flagged under A21 or A13 it lists specific lines to cut or shorten, old text next to proposed new
text, with ~tokens reclaimed per line (chars / A21.chars_per_token, same as always_on.py) and a total:
  dup        exact or near-duplicate facts (normalized token Jaccard, no differing numbers, or fully contained)
  superseded an older memory fact on the same subject as a newer one ('Label:' prefix or the same first words)
  one-off    a dated episode in always-on memory ([episode] tag, or only past dates) -> belongs in the log
  rollup     >= rollup_min short idle notes (<= idle_max_tokens, no links, most words shared) ("no change", "nothing new"...) on one subject in the log -> one line
  long       instruction / routine-prompt paragraphs or always-on memory facts over the token cap, shortened by fixed
             filler-word rules; when the rules can't shorten enough: 'needs owner wording' (no made-up rewrite)
Lines carrying rule words (never, always, must, only, required, approved...) are never cut except as exact duplicates.
Sources (read-only, the same files always_on.py reads): profile.json description, memory/profile.md + the bot's own
user-memory / project shards (else the own-memory part of the store.db prompt snapshot, read by explicit key, never
`metadata`), automations/*/automation.json prompts, and (A13) the newest memory/log/*.md. Other bots' shared shards are
read only as duplicate references and are never proposed for cuts.
Output is owner-local: <advice.trim.dir>/<bot>.md (default summary/trim/, never shipped) + the compact dict returned by
run() for latest.json. This script never edits any bot's memory, instructions, skills or routines.
  python3 trim.py --bot <agent_id> [--bot ...]   standalone: write the md for these bots and print the compact block
"""
import json, glob, os, re, shutil, sqlite3, sys, datetime as dt

HOME = os.path.dirname(os.path.abspath(__file__))
def P(rel): return rel if os.path.isabs(rel) else os.path.join(HOME, rel)
def load(p, d=None):
    try:
        with open(p) as f: return json.load(f)
    except Exception: return d
def read(p):
    try:
        with open(p, errors='replace') as f: return f.read()
    except OSError: return ''

STOP = set('a an the and or of to in on at for by with from as is are was were be been it its this that these those i we you he she they '
           'them his her our your their my me us so if then than but into via per about also can will would should could has have had do does '
           'did done am pm etc'.split())       # never 'no', 'not', 'never', 'only': negations change meaning
RULE = re.compile(r"\b(never|always|must|required?|requires|only|do not|don'?t|avoid|no longer|gate|before every|prefer|prefers|freeze|rule|"
                  r"approved|signed off|policy|forbidden|not allowed)\b", re.I)
RULE_LOG = re.compile(r"\b(never|always|must|required?|do not|don'?t|approved|signed off|policy|rule)\b", re.I)   # log rollups: 'only' is too common there
IDLE = re.compile(r"\b(idle|no (new|change|changes|updates?|activity|action|alerts?|movement)|nothing (new|to report|changed)|all clear|"
                  r"quiet (run|check|day)|no-op|unchanged|stayed quiet)\b", re.I)
ONEOFF = re.compile(r"^(completed|finished|shipped|pushed|published|backfill\w*|today|tonight|yesterday|tomorrow|this (week|morning|evening))\b", re.I)
DATE = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")
FACT = re.compile(r"^\s*[-*]\s+(?:\((\d{4}-\d{2}-\d{2})\)\s*)?(.+?)\s*$")
TAG = re.compile(r"^\s*(\[(episode|note|via [^\]]*)\]\s*)+", re.I)
FILLER = [(r"\bin order to\b", "to"), (r"\b(please|kindly)\s+", ""), (r"\bmake sure (that |to )?", ""), (r"\bbe sure to\s+", ""),
          (r"\bit is (very )?important (that |to )", ""), (r"\bnote that\s+", ""), (r"\b(basically|actually|really|simply|just|very|quite)\s+(?=[a-z])", ""),
          (r"\bat this point in time\b", "now"), (r"\bdue to the fact that\b", "because"), (r"\bin the event that\b", "if"),
          (r"\ba number of\b", "several"), (r"\bfor the purpose of\b", "for"), (r"\bwith regard to\b", "about"), (r"\bas well as\b", "and"),
          (r"\bin addition,?\s+", ""), (r"\bat all times\b", "always")]

def words(s):
    return [w for w in re.findall(r"[a-z0-9](?:[a-z0-9'_./:+-]*[a-z0-9])?", s.lower()) if w not in STOP and (len(w) > 1 or w.isdigit())]
def nums(ws): return {w for w in ws if re.search(r"\d", w)}
def jac(a, b): return len(a & b) / len(a | b) if (a or b) else 1.0
def ntext(s): return ' '.join(words(s))

def squeeze(text, date=None):
    """Fixed, meaning-preserving shortening rules. Returns the shortened text (may equal the input)."""
    t = text
    if date:   # the fact's own date repeated in its body: "(2026-09-06) 2026-09-06: x" / "x (2026-09-06): y"
        t = re.sub(r"^\s*" + re.escape(date) + r"\s*[:,-]?\s*", "", t); t = re.sub(r"\s*\(" + re.escape(date) + r"\)", "", t)
    for pat, rep in FILLER: t = re.sub(pat, rep, t, flags=re.I)
    seen, out = set(), []
    for s in re.split(r"(?<=[.!?])\s+", t):            # drop repeated sentences
        k = ntext(s)
        if k and k in seen: continue
        seen.add(k); out.append(s)
    t = re.sub(r"[ \t]{2,}", " ", ' '.join(out)); t = re.sub(r"\s+([,.;:])", r"\1", t).strip()
    if text[:1].isupper() and t[:1].islower(): t = t[0].upper() + t[1:]
    return t

def facts_of(text, src, where_fmt, kind):
    out, in_comment = [], False
    for i, line in enumerate(text.splitlines(), 1):
        if '<!--' in line: in_comment = '-->' not in line; continue
        if in_comment:
            in_comment = '-->' not in line; continue
        m = FACT.match(line)
        if not m: continue
        date, body = m.group(1), m.group(2)
        core = TAG.sub('', body)
        ws = words(re.sub(r"^\s*" + re.escape(date) + r"\s*[:,-]?\s*", "", core) if date else core)
        if date: ws = [w for w in ws if w != date]
        out.append(dict(src=src, where=where_fmt.format(i), ln=i, date=date, raw=line.strip(), body=body, core=core, ws=ws, set=set(ws),
                        nums=nums(ws), rule=bool(RULE.search(core)), episode=bool(re.match(r"\s*\[episode\]", body, re.I)), kind=kind))
    return out

def paragraphs(text, where_fmt, kind):
    out = []
    for i, p in enumerate([p for p in re.split(r"\n\s*\n", text or '') if p.strip()], 1):
        out.append(dict(where=where_fmt.format(i), raw=p.strip(), kind=kind))
    return out

def snapshot_own_memory(db):
    """Own 'About the user:' part of the memory section in the store.db prompt snapshot (explicit key, never metadata)."""
    tmp = os.path.join('/tmp', 'doge_trim_' + os.path.basename(os.path.dirname(db)) + '.db')
    try:
        shutil.copy(db, tmp)
        for s in ('-wal', '-shm'):
            if os.path.exists(db + s): shutil.copy(db + s, tmp + s)
        c = sqlite3.connect(tmp); row = c.execute("select value from kv where key = 'promptSectionSnapshots'").fetchone(); c.close()
        r = ((json.loads(row[0]) if row else {}).get('memory') or {}).get('render') or ''
    except Exception: return ''
    finally:
        for s in ('', '-wal', '-shm'):
            if os.path.exists(tmp + s): os.remove(tmp + s)
    m = re.search(r"^About the user:\s*$(.*?)^(Recently:|\(\d+ more)", r, re.M | re.S)
    return m.group(1) if m else ''

def slug(name, aid, used):
    s = re.sub(r"[^a-z0-9]+", "-", (name or '').lower()).strip('-') or aid[:8]
    if s in used: s += '-' + aid[:4]
    used.add(s); return s

def analyze(aid, flags, cfg, A, today):
    T, CPT = cfg['advice']['trim'], cfg['advice']['A21']['chars_per_token']
    ROOT = os.path.dirname(A.rstrip('/')); d = os.path.join(A, aid)
    def tok(s): return len(s) / CPT
    prof = load(os.path.join(d, 'profile.json'), {}) or {}
    rows, used = [], set()
    def add(it, why, new, est=None):
        key = it['where']
        if key in used: return
        used.add(key); old = it['raw']
        saved = tok(old) - (tok(new) if new not in ('cut',) and not new.startswith('needs owner wording') else 0)
        if new.startswith('needs owner wording'):
            if (est or 0) < T.get('min_owner_wording_tokens', 10): used.discard(key); return
            rows.append(dict(it=it, why=why, new=new, tok=0, est=round(est or 0)))
        elif saved >= T['min_line_tokens']: rows.append(dict(it=it, why=why, new=new, tok=round(saved), est=None))
        else: used.discard(key)
    # ---- sources ----
    mem = []
    files = [(os.path.join(d, 'memory/profile.md'), 'profile.md:{}')]
    files += [(f'{ROOT}/user-memory/by-agent/{aid}/profile.md', 'shared shard profile.md:{}')]
    files += [(p, 'project ' + p.split('/projects/')[1].split('/')[0] + ' profile.md:{}') for p in sorted(glob.glob(f'{ROOT}/projects/*/memory/by-agent/{aid}/profile.md'))]
    for p, w in files:
        if os.path.exists(p): mem += facts_of(read(p), 'mem', w, 'always-on')
    if not mem and os.path.exists(os.path.join(d, 'store.db')):
        mem = facts_of(snapshot_own_memory(os.path.join(d, 'store.db')), 'mem', 'memory snapshot line {}', 'always-on')
    logs = []
    if 'A13' in flags:
        lf = sorted(glob.glob(os.path.join(d, 'memory/log/*.md')))
        if lf: logs = facts_of(read(lf[-1]), 'log', 'log/' + os.path.basename(lf[-1]) + ':{}', 'log')
    refs = []
    for p in sorted(glob.glob(f'{ROOT}/user-memory/by-agent/*/profile.md')):
        if f'/by-agent/{aid}/' in p: continue
        oid = p.split('/by-agent/')[1].split('/')[0]
        refs += facts_of(read(p), 'ref', 'shared memory from ' + ((load(os.path.join(A, oid, 'profile.json'), {}) or {}).get('name') or 'another bot'), 'ref')
    paras = []
    if 'A21' in flags:
        paras = paragraphs(prof.get('description') or '', 'instructions ¶{}', 'always-on')
        for ap in sorted(glob.glob(os.path.join(d, 'automations/*/automation.json'))):
            j = load(ap, {}) or {}
            if j.get('enabled', True) is False: continue
            paras += paragraphs(j.get('prompt') or '', 'routine ' + os.path.basename(os.path.dirname(ap)) + ' ¶{}', 'routine')
    own = mem + logs
    # ---- 0. idle-note rollups in the log (A13), first so repeated status notes become one line ----
    groups = {}
    for f in logs:
        if f['date'] and IDLE.search(f['core']) and not RULE_LOG.search(f['core']) and tok(f['raw']) <= T.get('idle_max_tokens', 80) and 'http' not in f['core']:
            k = ' '.join([w for w in f['ws'] if not re.search(r"\d", w)][:3]); groups.setdefault(k, []).append(f)
    for k, g in groups.items():
        g = [f for f in g if f['where'] not in used]
        if len(g) < T['rollup_min']: continue
        g.sort(key=lambda x: (x['date'], x['ln'])); last = g[-1]
        g = [x for x in g if x is last or jac(x['set'], last['set']) >= T['supersede_jaccard']]   # same status note, not just the same opening words
        if len(g) < T['rollup_min']: continue
        merged = f"- ({last['date']}) {last['body']} (rolled up {len(g)} quiet notes, {g[0]['date']} to {last['date']})"
        old = '\n'.join(x['raw'] for x in g)
        it = dict(last, where=f"{last['where'].split(':')[0]}: {len(g)} lines ({', '.join(str(x['ln']) for x in g[:6])}{'…' if len(g) > 6 else ''})", raw=old)
        for x in g: used.add(x['where'])
        add(it, f"{len(g)} idle notes on one subject", merged)
    # ---- 1. duplicates (exact, near, contained) ----
    pool = own + refs
    for i in range(len(pool)):
        for j in range(i + 1, len(pool)):
            a, b = pool[i], pool[j]
            if a['src'] == 'ref' and b['src'] == 'ref': continue
            if len(a['set']) < 4 or len(b['set']) < 4: continue
            exact = ntext(a['core']) == ntext(b['core'])
            J = jac(a['set'], b['set']); small, big = (a, b) if len(a['set']) <= len(b['set']) else (b, a)
            contained = (a['src'] != 'log' or b['src'] != 'log' or a['date'] == b['date']) and len(small['set']) >= 6 and len(small['set'] & big['set']) / len(small['set']) >= 0.9 and small['nums'] <= big['nums']
            if not (exact or (J >= T['dup_jaccard'] and a['nums'] == b['nums']) or contained): continue
            # which copy goes: never a ref; log copy before always-on copy; contained -> the smaller; else the older / earlier line
            cands = [x for x in (a, b) if x['src'] != 'ref']
            if len(cands) == 2:
                rank = lambda x: 0 if x['src'] == 'log' else (2 if x['where'].startswith(('shared shard', 'project ')) else 1)
                if rank(a) != rank(b): cut = min((a, b), key=rank)   # cut the log copy, then the private copy; keep what other bots share
                elif contained and not exact and J < T['dup_jaccard']: cut = small
                else: cut = min((a, b), key=lambda x: (x['date'] or '', x['ln']))
            else: cut = cands[0]
            keep = b if cut is a else a
            uniq = cut['set'] - keep['set']
            kw = keep['where']
            label = 'repeats' if (cut['date'] and keep['date'] and cut['date'] != keep['date']) else ('exact duplicate of' if exact else 'near duplicate of')
            if exact or (len(uniq) <= T['dup_max_unique_words'] and not nums(uniq) and not (cut['rule'] and not keep['rule'])):
                add(cut, f"{label} {kw}" + (f" (only drops: {', '.join(sorted(uniq))})" if uniq and not exact else ''), 'cut')
            else:
                add(cut, f"overlaps {kw} ({round(J * 100)}% same words)", f"needs owner wording: merge into {kw} (extra words: {', '.join(sorted(uniq)[:6])})", tok(cut['raw']))
    # ---- 2. superseded (always-on memory only; log history is supposed to keep old entries) ----
    def subject(f):
        m = re.match(r"^\s*([^:]{3,60}):", re.sub(r"^\s*" + re.escape(f['date'] or '#') + r"\s*:?\s*", "", f['core']))
        lab = words(m.group(1)) if m else []
        if 1 <= len(lab) <= 6: return 'L:' + ' '.join(w for w in lab if not DATE.fullmatch(w))
        k = [w for w in f['ws'] if not re.search(r"\d", w)][:T['subject_words']]
        return 'W:' + ' '.join(k) if len(k) == T['subject_words'] else None
    dated = [f for f in mem if f['date']]
    for f in dated:
        f['subj'] = subject(f)
    for old in dated:
        newer = [n for n in dated if n is not old and n['subj'] and n['subj'] == old['subj'] and n['date'] > old['date']]
        if not newer: continue
        n = max(newer, key=lambda x: (x['date'], x['ln'])); J = jac(old['set'], n['set'])
        if J >= T['supersede_jaccard'] and not old['rule']: add(old, f"superseded by newer {n['where']} ({n['date']})", 'cut')
        else: add(old, f"older fact on the same subject as {n['where']} ({n['date']})", f"needs owner wording: keep the newer line, merge anything still true", tok(old['raw']))
    # ---- 3. dated one-offs in always-on memory ----
    cutoff = (today - dt.timedelta(days=T['oneoff_age_days'])).strftime('%Y-%m-%d')
    for f in mem:
        if f['rule'] or not f['date'] or f['date'] >= cutoff: continue
        inner = [x for x in DATE.findall(f['core']) if x != f['date']]
        past_only = bool(inner) and all(x < cutoff for x in inner) and not re.search(r"\b(next|upcoming|due|until|from now)\b", f['core'], re.I)
        if f['episode'] or ONEOFF.match(TAG.sub('', f['core']).strip()) or past_only:
            add(f, f"dated one-off ({f['date']}); belongs in the log, not always-on memory", 'cut')
    # ---- 5. long memory facts / instruction and routine-prompt paragraphs ----
    for f in mem:   # log facts are history (and capped by the app); A13 trims them by rollup / duplicate only
        if tok(f['raw']) <= T['fact_max_tokens']: continue
        body = squeeze(f['body'], f['date']); new = f"- ({f['date']}) {body}" if f['date'] else f"- {body}"
        if (len(f['raw']) - len(new)) / len(f['raw']) >= T['min_rewrite_saving']: add(f, f"long fact (~{round(tok(f['raw']))} tok)", new)
        else: add(f, f"long fact (~{round(tok(f['raw']))} tok)", "needs owner wording: move the how-to detail to a skill or file, keep a one-line pointer", tok(f['raw']) - T['fact_max_tokens'])
    for p in paras:
        if tok(p['raw']) <= T['para_max_tokens']: continue
        new = squeeze(p['raw'])
        if (len(p['raw']) - len(new)) / len(p['raw']) >= T['min_rewrite_saving']: add(p, f"long paragraph (~{round(tok(p['raw']))} tok)", new)
        else: add(p, f"long paragraph (~{round(tok(p['raw']))} tok)", "needs owner wording: move the detail to a skill or file, keep a one-line pointer", tok(p['raw']) - T['para_max_tokens'])
    rows.sort(key=lambda r: (-(r['tok'] or r['est']), r['it']['where']))
    return prof.get('name'), rows

def cell(s, n):
    s = s.replace('|', '\\|').replace('\n', ' ⏎ ')
    return s if len(s) <= n else s[:n - 1] + '…'

def write_md(path, name, flags, rows, T, now):
    shown = rows[:T['max_lines_per_bot']]
    firm = [r for r in rows if not r['new'].startswith('needs owner wording')]
    ao = sum(r['tok'] for r in firm if r['it'].get('kind') in ('always-on', 'routine')); lg = sum(r['tok'] for r in firm if r['it'].get('kind') == 'log')
    md = [f"# Trim list: {name}", "",
          "Flagged: " + '; '.join(f"{k} {v}" for k, v in sorted(flags.items())), "",
          f"Generated {now} by trim.py (no AI). **Recommend only:** pick the line numbers you want; {name} applies them in its own memory, "
          "instructions or routine prompt. Penny DOGE never edits another bot. Tokens = chars / 4. Always-on = instructions + profile memory, "
          "re-sent every turn; routine = re-sent every run; log = memory/log (A13, recalled on demand).", "",
          "| # | Where | Why | Old | New | ~Tok |", "|---|---|---|---|---|---|"]
    for i, r in enumerate(shown, 1):
        t = f"{r['tok']}" if not r['new'].startswith('needs owner wording') else f"(up to ~{r['est']})"
        md.append(f"| {i} | {cell(r['it']['where'], 60)} | {cell(r['why'], 90)} | {cell(r['it']['raw'], T['show_chars'])} | {cell(r['new'], T['show_chars'])} | {t} |")
    if len(rows) > len(shown): md.append(f"\n*{len(rows) - len(shown)} more lines not shown (smaller savings).*")
    ow = len(rows) - len(firm)
    md += ["", f"**Total: {len(firm)} lines ready, ~{ao + lg:,} tokens reclaimed (always-on/routine ~{ao:,}, log ~{lg:,})"
           + (f"; {ow} more need owner wording (up to ~{sum(r['est'] for r in rows if r['est']):,} tok)." if ow else ".") + "**"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + '.tmp', 'w') as f: f.write('\n'.join(md) + '\n')
    os.replace(path + '.tmp', path)
    return len(firm), ao + lg, ow

def run(flags, cfg, home=HOME, now=None):
    """flags: {agent_id: {'A21': signal, 'A13': signal}}. Writes <trim.dir>/<bot>.md, returns the compact latest.json block."""
    T = cfg['advice'].get('trim') or {}
    if not T.get('enabled', True) or not flags: return None
    A = os.path.expanduser(cfg['paths']['agents_dir']); now = now or dt.datetime.now()
    out_dir = T['dir'] if os.path.isabs(T['dir']) else os.path.join(home, T['dir'])
    used, written, bots = set(), set(), []
    for aid in sorted(flags):
        name, rows = analyze(aid, flags[aid], cfg, A, now)
        fn = slug(name, aid, used) + '.md'; written.add(fn)
        n, t, ow = write_md(os.path.join(out_dir, fn), name or aid[:8], flags[aid], rows, T, now.strftime('%Y-%m-%d %H:%M'))
        bots.append(dict(bot=name, adv='+'.join(sorted(flags[aid])), n=n, tok=t, ow=ow, md=os.path.relpath(os.path.join(out_dir, fn), home)))
    for old in glob.glob(os.path.join(out_dir, '*.md')):   # drop lists for bots no longer flagged
        if os.path.basename(old) not in written: os.remove(old)
    bots.sort(key=lambda b: -b['tok'])
    return dict(lines=sum(b['n'] for b in bots), tok=sum(b['tok'] for b in bots), ow=sum(b['ow'] for b in bots), bots=bots)

if __name__ == '__main__':
    cfg = load(P('config/thresholds.json')) or load(P('config/thresholds.default.json'))
    ids = [sys.argv[i + 1] for i, a in enumerate(sys.argv[:-1]) if a == '--bot']
    if not ids: sys.exit(__doc__)
    print(json.dumps(run({i: {'A21': '(manual run)'} for i in ids}, dict(cfg, advice=dict(cfg['advice'], trim=dict(cfg['advice']['trim'], dir='/tmp/doge_trim_manual'))))))
