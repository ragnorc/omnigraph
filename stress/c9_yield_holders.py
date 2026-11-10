import json,datetime,sys
def ts(t):
    t=t.replace('Z','+00:00'); base,_,_=t.partition('+'); h,f=base.split('.'); return datetime.datetime.fromisoformat(h+'.'+f[:6]+'+00:00').timestamp()
for path in sys.argv[1:]:
    d=json.load(open(path))
    writes=[w for w in d['writes'] if '@side' not in w[2]]
    pubs=sorted(p[0] for p in d['pubs'])
    long_=0; by_sent_back=0; by_other=0
    for r in d['runs']:
        t=r['timings']; y=t.get('writer_yield_ms') or 0
        if y<2000: continue
        long_+=1
        ys=ts(r['accepted_at'])+t.get('queued_ms',0)/1000; ye=ys+y/1000
        prev=[p for p in pubs if p<ys]
        last=prev[-1] if prev else 0
        span=[w for w in writes if w[0]<=ys and w[1]>=ye-0.05]
        if any(w[0]<last for w in span): by_sent_back+=1
        else: by_other+=1
    print(path.split('/')[-2], 'runs',len(d['runs']),'yields>=2s',long_,'held by a write in flight across the last publication',by_sent_back,'held only by writes that started after it',by_other)
