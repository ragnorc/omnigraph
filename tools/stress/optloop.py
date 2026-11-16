#!/usr/bin/env python3
"""Submit a recorded optimize every 2 to 6 s to the server at 127.0.0.1:18300
(graph g, token ta), wait for each to end and count the outcomes. Run it
beside sserve.py. Usage: optloop.py <seconds>"""
import json, time, random, secrets, urllib.request, urllib.error, sys
B="http://127.0.0.1:18300"; secs=int(sys.argv[1]); r=random.Random(3)
def ulid():
    v=(int(time.time()*1000)<<80)|secrets.randbits(80); a="0123456789ABCDEFGHJKMNPQRSTVWXYZ"; s=""
    for _ in range(26): s=a[v&31]+s; v>>=5
    return s
def call(m,p,b=None):
    d=json.dumps(b).encode() if b is not None else None
    q=urllib.request.Request(B+p,data=d,method=m); q.add_header("Authorization","Bearer ta"); q.add_header("Omnigraph-Http-Api","0.14")
    if d: q.add_header("Content-Type","application/json")
    try:
        with urllib.request.urlopen(q,timeout=120) as x: return x.status, json.loads(x.read() or b"null")
    except urllib.error.HTTPError as e: return e.code, e.read().decode()[:300]
end=time.time()+secs; res={}
while time.time()<end:
    time.sleep(r.uniform(2,6)); oid=ulid()
    st,out=call("POST","/graphs/g/operations/optimize",{"operation_id":oid,"max_gate_hold_ms":r.choice([0,200,2000])})
    state=None
    for _ in range(120):
        st2,o2=call("GET",f"/graphs/g/operations/{oid}")
        if isinstance(o2,dict):
            rec=o2.get("operation") or {}; state=rec.get("state") or rec.get("status")
            if state in ("succeeded","failed","cancelled"): break
        time.sleep(0.5)
    k=f"{st}:{state}"; res[k]=res.get(k,0)+1
    if state=="failed": print("optimize failed", oid, str(o2)[:600], flush=True)
print("optimize", res, flush=True)
