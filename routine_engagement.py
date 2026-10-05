import json,re,os,collections,sqlite3,datetime as dt,bisect,statistics
T='/home/box/agent-data/agent-transcripts'
fired=collections.defaultdict(list)  # agent -> [(ts_ms, slug)]
for d in os.listdir(T):
    if not d.startswith('sand-subagent'): continue
    p=f'{T}/{d}/{d}.jsonl'
    if not os.path.exists(p): continue
    s=open(p).read(4000)
    m=re.search(r'\[routine\] \\?"([^"\\]+)\\?" \(folder ([^)]+)\)',s)
    if not m: continue
    f=re.search(r'fired (\d+/\d+/\d{4}, [\d:]+ [AP]M)',s); a=re.search(r'agent-transcripts/([0-9a-f-]{36})/',s)
    if not (f and a): continue
    t=dt.datetime.strptime(f.group(1),'%m/%d/%Y, %I:%M:%S %p').timestamp()*1000
    fired[a.group(1)].append((t,m.group(2)))
res=collections.defaultdict(lambda: dict(bursts=0,replied2h=0,replied24h=0,lat=[],reacted=0,delays_min=[],reply_hours=[],fire_hours=[]))
unmapped=0
for aid,L in fired.items():
    L.sort(); ts=[x[0] for x in L]
    db=f'/tmp/doge_{aid}.db'
    if not os.path.exists(db): continue
    c=sqlite3.connect(db)
    E=[json.loads(e) for (e,) in c.execute('select entry from transcript_entries order by seq')]
    human=sorted(e['timestampMs'] for e in E if e.get('kind')=='message' and e.get('role')=='user' and not e.get('fromAgent'))
    bursts=collections.OrderedDict()
    for e in E:
        if e.get('kind')=='send-message' and e.get('wake'):
            b=bursts.setdefault(e.get('requestId'),dict(t=e['timestampMs'],react=False)); b['react']|=bool(e.get('reactions'))
    for rid,b in bursts.items():
        i=bisect.bisect_right(ts,b['t'])-1
        if i<0 or b['t']-ts[i]>90*60000: unmapped+=1; continue
        slug=L[i][1]; r=res[slug]; r['bursts']+=1; r['reacted']+=b['react']; r['fire_hours'].append(dt.datetime.fromtimestamp(ts[i]/1000).hour)
        j=bisect.bisect_right(human,b['t'])
        if j<len(human):
            m=(human[j]-b['t'])/60000
            r['delays_min'].append(round(m)); r['reply_hours'].append(dt.datetime.fromtimestamp(human[j]/1000).hour)  # timestamps only
            if m<=120: r['replied2h']+=1
            if m<=1440: r['replied24h']+=1; r['lat'].append(m)
out=[]
for s,r in sorted(res.items(),key=lambda x:-x[1]['bursts']):
    r['median_lat_min']=round(statistics.median(r['lat'])) if r['lat'] else None; del r['lat']; r['slug']=s; out.append(r); print(r)
print('unmapped wake bursts',unmapped)
json.dump(out,open('routine_engagement.json','w'),indent=1)
