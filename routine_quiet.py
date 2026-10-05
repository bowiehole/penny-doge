# Per-routine: runs seen in subagent transcripts, how many surfaced (WakeParent or SendToAgent) vs stayed quiet.
import json,re,os,collections,statistics
T='/home/box/agent-data/agent-transcripts'
idx={}
for d in os.listdir(T):
    if not d.startswith('sand-subagent'): continue
    p=f'{T}/{d}/{d}.jsonl'
    if not os.path.exists(p): continue
    s=open(p).read()
    m=re.search(r'\[routine\] \\?"([^"\\]+)\\?" \(folder ([^)]+)\)',s[:3000])
    if not m: continue
    f=re.search(r'fired (\d+/\d+/\d{4}, [\d:]+ [AP]M)',s[:3000])
    idx[(m.group(2),f.group(1) if f else d)]=dict(wake=s.count('\\"tool\\":\\"WakeParent\\"'),s2a=s.count('\\"tool\\":\\"SendToAgent\\"'),bytes=len(s))
by=collections.defaultdict(list)
for (slug,_),v in idx.items(): by[slug].append(v)
rows=[]
for slug,V in by.items():
    n=len(V); surf=sum(1 for v in V if v['wake'] or v['s2a'])
    rows.append(dict(slug=slug,n=n,surfaced=surf,quiet_pct=round(100*(n-surf)/n),totMB=round(sum(v['bytes'] for v in V)/1e6,1),medKB=round(statistics.median(v['bytes'] for v in V)/1e3)))
rows.sort(key=lambda r:-r['totMB']); json.dump(rows,open('routine_quiet.json','w'),indent=1); print(len(idx),'runs')
