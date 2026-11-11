import subprocess, threading, time, json, os
OG=os.environ.get("OG","omnigraph")
ev=[]; stop=False
def loop(kind, home):
    while not stop:
        t0=time.time()
        if kind=="w":
            a=[OG,"mutate","add_item","--target","bob","--params",json.dumps({"slug":f"win-{home[-2:]}-{t0}","n":1}),"--json"]
        else:
            a=[OG,"query","count_items","--target","carol","--json"]
        p=subprocess.run(a,capture_output=True,text=True,env=dict(os.environ,OMNIGRAPH_HOME=home))
        ev.append((kind,t0,time.time(),p.returncode))
ts=[threading.Thread(target=loop,args=("w","/tmp/og/h4")),threading.Thread(target=loop,args=("r","/tmp/og/h5")),threading.Thread(target=loop,args=("w","/tmp/og/h6"))]
[t.start() for t in ts]; time.sleep(2)
a0=time.time(); p=subprocess.run([OG,"cluster","apply","--target","alice","--config","/tmp/og/srv/bundle2","--json"],capture_output=True,text=True,env=dict(os.environ,OMNIGRAPH_HOME="/tmp/og/h15")); a1=time.time()
time.sleep(6); stop=True; [t.join() for t in ts]
print("apply rc",p.returncode,"took %.2fs"%(a1-a0))
bad=[(k,s-a0,e-a0,rc) for k,s,e,rc in ev if rc!=0]
print("failures (kind, start, end rel. to apply start, rc):")
for b in bad: print("  %s %.2f %.2f %d"%b)
ok_after=[(k,s-a0) for k,s,e,rc in ev if rc==0 and s>a1]
print("first ok writes after apply end:", [round(x[1],2) for x in ok_after if x[0]=="w"][:3])
print("totals", len(ev), "failed", len(bad))
