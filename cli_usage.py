import json,glob,os,subprocess,collections,re,shutil
GROK=shutil.which('grok') or os.path.expanduser('~/.local/bin/grok')  # cron PATH lacks ~/.local/bin
rows=[]
for s in glob.glob('/home/box/.grok/sessions/*/*/summary.json'):
    d=os.path.dirname(s); sid=os.path.basename(d)
    try: sm=json.load(open(s))
    except: continue
    try: J=json.loads(subprocess.run([GROK,'usage',sid],capture_output=True,text=True,timeout=30,cwd=os.path.dirname(d)).stdout); u=J['session']
    except Exception as e: J={}; u={}
    turns=[[t.get('endedAt'),t.get('totalTokens',0),t.get('costUsdTicks',0)] for t in J.get('turns',[])]  # exact per-turn usage, UTC endedAt
    ch=os.path.join(d,'chat_history.jsonl'); mids=sorted(set(re.findall(r'"model_id":"([^"]+)"',open(ch,errors='replace').read()))) if os.path.exists(ch) else []
    rows.append(dict(sid=sid,cwd=sm.get('info',{}).get('cwd'),created=sm.get('created_at','')[:16],updated=sm.get('updated_at','')[:16],req=sm.get('request_id'),title=sm.get('session_summary'),tok=u.get('totalTokens',0),inp=u.get('inputTokens',0),cached=u.get('cachedReadTokens',0),out=u.get('outputTokens',0),calls=u.get('modelCalls',0),ticks=u.get('costUsdTicks',0),turns=turns,model=sm.get('current_model_id'),effort=sm.get('reasoning_effort'),chat_models=mids))
json.dump(rows,open('/workspace/doge/cli_sessions.json','w'),indent=1)
print(len(rows),'sessions; total tokens',sum(r['tok'] for r in rows),'ticks',sum(r['ticks'] for r in rows))
print('date range',min(r['created'] for r in rows),max(r['updated'] for r in rows))
by=collections.defaultdict(lambda:[0,0,0])
for r in rows: b=by[r['cwd']]; b[0]+=1;b[1]+=r['tok'];b[2]+=r['ticks']
for k,v in sorted(by.items(),key=lambda x:-x[1][2]): print(v[0],f"{v[1]:,}",f"${v[2]/1e10:.2f}?",k)
# request_id match
reqmap={}
for st in glob.glob('/home/box/agent-data/agents/*/store.db'):
    pass
