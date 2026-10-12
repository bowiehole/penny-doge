import json,glob,os,shutil,sqlite3,collections,datetime as dt,re,statistics
A='/home/box/agent-data/agents'; T='/home/box/agent-data/agent-transcripts'
def fmt(ms): return dt.datetime.fromtimestamp(ms/1000).strftime('%Y-%m-%d %H:%M') if ms else '-'
out={}
# Live chat activity: since the Oct 6 box move, per-bot store.db files are no longer written on this box
# (only -shm is touched); the synced search index is. Read timestamps/roles/lengths only, never bodies.
IDX={}
_si='/home/box/agent-data/search-index.db'
if os.path.exists(_si):
    _t='/tmp/doge_search-index.db'; shutil.copy(_si,_t)
    [shutil.copy(_si+s_,_t+s_) if os.path.exists(_si+s_) else (os.path.exists(_t+s_) and os.remove(_t+s_)) for s_ in ('-wal','-shm')]
    _c=sqlite3.connect(_t)
    for a_,n_,mx_,nu_,ln_ in _c.execute("select agent_id,count(*),max(timestamp_ms),sum(role='user'),sum(length(body)) from messages group by agent_id"): IDX[a_]=dict(n=n_,lastMs=mx_,user=nu_,chars=ln_)
    _c.close()
for d in sorted(glob.glob(A+'/*/')):
    aid=os.path.basename(d[:-1])
    if not os.path.exists(d+'profile.json'): continue
    prof=json.load(open(d+'profile.json'))
    r=dict(id=aid,name=prof.get('name'))
    tmp=f'/tmp/doge_{aid}.db'; shutil.copy(d+'store.db',tmp)
    [shutil.copy(d+'store.db'+s,tmp+s) if os.path.exists(d+'store.db'+s) else (os.path.exists(tmp+s) and os.remove(tmp+s)) for s in ('-wal','-shm')]
    c=sqlite3.connect(tmp)
    kv={k:v for k,v in c.execute('select key,value from kv')}
    r['unread']=json.loads(kv.get('unreadState','null') or 'null')
    r['settle']=json.loads(kv.get('lastTurnSettlement','null') or 'null')
    rq=json.loads(kv.get('requestIds','[]') or '[]'); r['reqIds']=len(rq)
    r['reqRange']=(fmt(min(x['at'] for x in rq)),fmt(max(x['at'] for x in rq))) if rq else None
    r['partners']=len(json.loads(kv.get('conversationPartners','[]') or '[]'))
    # store.db `metadata` is never read or decoded (privacy); nothing downstream needs it
    E=[json.loads(e) for (e,) in c.execute('select entry from transcript_entries order by seq')]
    r['entries']=len(E)
    tsl=[e.get('timestampMs') for e in E if e.get('timestampMs')]
    r['first']=fmt(min(tsl)) if tsl else None; r['last']=fmt(max(tsl)) if tsl else None; r['lastMs']=max(tsl) if tsl else 0
    r['store_last']=r['last']; r['index']=IDX.get(aid)
    if r['index'] and (r['index']['lastMs'] or 0)>r['lastMs']:   # newer activity from the synced search index
        r['lastMs']=r['index']['lastMs']; r['last']=fmt(r['lastMs']); r['last_src']='search-index'
    cnt=collections.Counter()
    human=[];botsends=[]
    for e in E:
        k=e.get('kind')
        if k=='message' and e.get('role')=='user':
            if e.get('fromAgent'): cnt['from_agent']+=1
            else: cnt['human']+=1; human.append(e['timestampMs'])
            if e.get('replyTo'): cnt['human_replyTo']+=1
        elif k=='message' and e.get('role')=='assistant':
            cnt['to_agent' if e.get('toAgent') else 'assistant_other']+=1
        elif k=='send-message':
            mt=e['message'].get('type'); cnt['bot_send']+=1; cnt['bot_send_'+str(mt)]+=1
            if e.get('wake'): cnt['bot_send_wake']+=1
            if mt=='widget':
                if e.get('widgetDismissed'): cnt['widget_dismissed']+=1
                elif e.get('widgetSkipped'): cnt['widget_skipped']+=1
                elif 'respondedValue' in e: cnt['widget_answered']+=1
                else: cnt['widget_open']+=1
            botsends.append(e)
        elif k=='spend-initiation':
            cnt['spend']+=1; iid=e['initiation'].get('id','')
            t=e['initiation'].get('type')
            cnt['spend_'+t+('_offrec' if iid.startswith('offrec') else ('_usermsg' if re.match(r't\d+u',iid) else '_other'))]+=1
        elif k=='event':
            cnt['event_'+e['event'].get('type','?')+'_'+str(e['event'].get('action'))]+=1
        else: cnt[k]+=1
        for kk in e:
            if re.search('react|tapback|read|seen|viewed',kk,re.I): cnt['FIELD_'+kk]+=1
    r['counts']=dict(cnt)
    r['lastHumanMs']=max(human) if human else 0  # newest human message (role=user, no fromAgent); timestamp only
    # reply latency: for each bot text/widget send, time to next human message
    human.sort(); lat=[]; replied24=0; n=0
    import bisect
    for e in botsends:
        t=e['timestampMs']; n+=1
        i=bisect.bisect_right(human,t)
        if i<len(human):
            dtm=(human[i]-t)/60000
            if dtm<=1440: replied24+=1; lat.append(dtm)
    r['eng']=dict(sends=n,followed_by_human_24h=replied24,median_min=round(statistics.median(lat),1) if lat else None)
    lv=(r['unread'] or {}).get('lastViewedAt') or 0; sts=[e.get('timestampMs') or 0 for e in botsends]
    r['sendsAfterView']=sum(1 for t in sts if t>lv) if r['unread'] else None; r['lastSendMs']=max(sts) if sts else 0  # bot->user send-message counts/timestamps only
    r['completion_inbox']=c.execute('select count(*),sum(acknowledged) from automation_completion_inbox').fetchone()
    # audit
    ap=d+'audit.jsonl'; r['audit']=None
    if os.path.exists(ap):
        L=[json.loads(l) for l in open(ap) if l.strip()]
        ty=collections.Counter(l.get('type') for l in L)
        r['audit']=dict(n=len(L),types=dict(ty),last=(L[-1]['ts'] if L else None),mcp_ms=sum(l.get('durationMs',0) or 0 for l in L))
    tp=f'{T}/{aid}/{aid}.jsonl'
    r['tx_bytes']=os.path.getsize(tp) if os.path.exists(tp) else 0
    if r['tx_bytes']:
        s=open(tp).read(); r['tx_comm_update']=s.count('"type":"tool_use","name":"communicate_update"'); r['tx_send']=s.count('"type":"tool_use","name":"send_message"'); r['tx_task']=s.count('"type":"tool_use","name":"task"')
    r['routines']=[]
    for a in sorted(glob.glob(d+'automations/*/automation.json')):
        j=json.load(open(a)); rp=os.path.join(os.path.dirname(a),'runs.json')
        runs=json.load(open(rp)) if os.path.exists(rp) else []
        st=collections.Counter(x.get('status') for x in runs)
        dur=[(x['finishedAt']-x['startedAt'])/1000 for x in runs if x.get('finishedAt') and x.get('startedAt')]
        reqs={x.get('requestId') for x in runs}
        sent_runs=len({e.get('requestId') for e in botsends if e.get('requestId') in reqs})
        r['routines'].append(dict(slug=os.path.basename(os.path.dirname(a)),name=j.get('name'),schedule=j.get('schedule') or j.get('triggerPresentation',{}).get('trigger',{}).get('type'),enabled=j.get('enabled'),last=fmt(j.get('lastRunAt')),runs=len(runs),status=dict(st),med_dur_s=round(statistics.median(dur)) if dur else None,runs_with_user_msg=sent_runs,first_run=fmt(min((x['startedAt'] for x in runs),default=0)),errors=[x.get('error') for x in runs if x.get('status')!='ok'][:3],err_kinds=[[x.get('startedAt'),x.get('errorKind') or 'unknown'] for x in runs if x.get('status')!='ok'],prompt_quiet=bool(re.search(r'stay quiet|stay silent|no filler|quiet if|silent if|nothing new',j.get('prompt',''),re.I)),prompt_len=len(j.get('prompt',''))))
    out[aid]=r
json.dump(out,open('/workspace/doge/fleet_scan.json','w'),indent=1,default=str)
print('ok',len(out))
