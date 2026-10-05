import json,re,os,collections,statistics
T='/home/box/agent-data/agent-transcripts'
agg=collections.defaultdict(lambda: dict(n=0,calls=[],replay=[],bytes=[]))
for d in os.listdir(T):
    if not d.startswith('sand-subagent'): continue
    p=f'{T}/{d}/{d}.jsonl'
    if not os.path.exists(p): continue
    L=open(p).read().splitlines()
    if not L: continue
    m=re.search(r'\[routine\] \\?"([^"\\]+)\\?" \(folder ([^)]+)\)',L[0][:4000])
    if not m: continue
    pref=0; calls=0; replay=0
    for l in L:
        if l.startswith('{"role":"assistant"'): calls+=1; replay+=pref
        pref+=len(l)
    a=agg[m.group(2)]; a['n']+=1; a['calls'].append(calls); a['replay'].append(replay/4); a['bytes'].append(pref)
# runs/week from schedule
sched={}
import glob
for f in glob.glob('/home/box/agent-data/agents/*/automations/*/automation.json'):
    j=json.load(open(f)); sched[f.split('/')[-2]]=(j.get('schedule') or 'webhook', j.get('enabled'))
def per_week(s):
    s=re.sub(r'CRON_TZ=\S+ ','',s); parts=s.split()
    if len(parts)!=5: return None
    def cnt(field,lo,hi):
        n=0
        for part in field.split(','):
            step=1
            if '/' in part: part,step=part.split('/'); step=int(step)
            if part=='*': a,b=lo,hi
            elif '-' in part: a,b=map(int,part.split('-'))
            else: a=b=int(part)
            n+=len(range(a,b+1,step))
        return n
    mi,h,dom,mo,dow=parts
    days=7 if dow=='*' else cnt(dow,0,6)
    return cnt(mi,0,59)*cnt(h,0,23)*days
rows=[]
for slug,a in agg.items():
    s,en=sched.get(slug,(None,None)); pw=per_week(s) if s else None
    tok=statistics.mean(a['replay'])
    rows.append(dict(slug=slug,schedule=s,enabled=en,observed_runs=a['n'],runs_per_week=pw,calls_per_run=round(statistics.mean(a['calls']),1),est_replay_tok_per_run=round(tok),est_weekly_replay_tok=round(tok*pw) if pw else None,weekly_model_calls=round(statistics.mean(a['calls'])*pw) if pw else None))
rows.sort(key=lambda r:-(r['est_weekly_replay_tok'] or 0))
for r in rows: print(r)
json.dump(rows,open('routine_cost.json','w'),indent=1)
