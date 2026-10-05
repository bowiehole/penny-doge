import os,json,re,collections,datetime as dt,statistics
T='/home/box/agent-data/agent-transcripts'
runs=[]
for d in os.listdir(T):
    if not d.startswith('sand-subagent'): continue
    p=f'{T}/{d}/{d}.jsonl'
    if not os.path.exists(p): continue
    L=[l for l in open(p).read().splitlines() if l.strip()]
    if not L: continue
    first=json.loads(L[0]); c=first['message']['content']
    t=c if isinstance(c,str) else ''.join(x.get('text','') for x in c if isinstance(x,dict))
    m=re.search(r'\[routine\] "([^"]+)" \(folder ([^)]+)\)',t)
    if not m: continue
    f=re.search(r'fired (\d+/\d+/\d{4}, [\d:]+ [AP]M)',t)
    fired=dt.datetime.strptime(f.group(1),'%m/%d/%Y, %I:%M:%S %p') if f else None
    tools=collections.Counter(); sends=0; finaltext=''
    for l in L[1:]:
        try: j=json.loads(l)
        except: continue
        cc=j['message']['content']
        if isinstance(cc,list):
            for x in cc:
                if isinstance(x,dict) and x.get('type')=='tool_use':
                    tools[x.get('name')]+=1
                    if x.get('name')=='send_message' and (x.get('input',{}).get('text') or x.get('input',{}).get('attachment')): sends+=1
    runs.append(dict(name=m.group(1),slug=m.group(2),fired=fired.isoformat() if fired else None,bytes=os.path.getsize(p),lines=len(L),sends=sends,tools=dict(tools),comm=tools.get('communicate_update',0),task=tools.get('task',0),web=tools.get('web_search',0)+tools.get('web_fetch',0)+tools.get('search_posts_all',0)+tools.get('search_news',0)))
json.dump(runs,open('/workspace/doge/routine_runs.json','w'),indent=1)
by=collections.defaultdict(list)
for r in runs: by[r['slug']].append(r)
print(len(runs),'routine-run transcripts;',len(by),'distinct routines')
print(f"{'slug':40} n  first..last         quiet% sends/run medKB  tools/run task web")
for s,R in sorted(by.items(),key=lambda x:-sum(r['bytes'] for r in x[1])):
    fs=sorted(r['fired'] for r in R if r['fired'])
    q=sum(1 for r in R if r['sends']==0)/len(R)
    print(f"{s[:40]:40} {len(R):3} {fs[0][:10] if fs else '-'}..{fs[-1][:10] if fs else '-'} {q*100:4.0f}% {sum(r['sends'] for r in R)/len(R):4.1f} {statistics.median(r['bytes'] for r in R)/1000:6.0f} {sum(sum(r['tools'].values()) for r in R)/len(R):5.1f} {sum(r['task'] for r in R):3} {sum(r['web'] for r in R):4}  totMB={sum(r['bytes'] for r in R)/1e6:.1f}")
