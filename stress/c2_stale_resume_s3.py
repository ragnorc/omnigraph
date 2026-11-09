#!/usr/bin/env python3
"""N8 on S3: a late settlement rewrites a newer run's resume position.

Run A (full catalog, gate hold 0: it stops after one table and its position
is the next table) is in doubt: its manifest PUT is delayed past the owner's
fence and lands late. On the successor (walk limit 1) several commits land
above A's, so A's walk needs several accesses. Run B (full catalog, no
bound) then runs while A is still walking: it covers every table and clears
the position. Lookups then settle A `succeeded`; `keep_kind_state` stores A's
position again. Question: where does the next full-catalog run start?
Usage: c2_stale_resume_s3.py OUTDIR
"""
import json, os, shutil, signal, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("STRESS_S3_PREFIX", "s3://omnigraph-stress/staleresume")
import og

out = Path(sys.argv[1]); shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
PROXY = 14950
root = og.make_root(out, "sr"); og.apply_cluster(root)
key = root[len("s3://"):]
ctl = out / "proxy"; ctl.mkdir()
proxy = subprocess.Popen([sys.executable, str(Path(__file__).parent / "s3proxy.py"), str(PROXY), "9000", str(ctl)],
                         stdout=subprocess.PIPE, text=True)
proxy.stdout.readline()
env = {"AWS_ENDPOINT_URL": f"http://127.0.0.1:{PROXY}", "AWS_ENDPOINT_URL_S3": f"http://127.0.0.1:{PROXY}",
       "OMNIGRAPH_CLUSTER_LEASE_SECONDS": "4", "OMNIGRAPH_SHUTDOWN_GRACE_SECONDS": "5",
       "OMNIGRAPH_OPERATION_RESOLUTION_WALK_LIMIT": "1"}
srv = og.Server(root, 14951, str(out / "server.log"), env).start()
h = srv.http
res = {}
try:
    for b in range(3):  # fragments on edge:Authored's neighbours so the first table has work
        h.load("kg", "\n".join(json.dumps({"type": "Person", "data": {"name": f"b{b}p{i}", "bio": "x"}}) for i in range(10)))
        h.load("kg", "\n".join(json.dumps({"edge": "Knows", "from": f"b{b}p{i}", "to": f"b{b}p{i+1}"}) for i in range(9)))
        h.load("kg", json.dumps({"type": "Doc", "data": {"slug": f"d{b}", "title": "t", "embedding": [0.1] * 8}}))
        h.load("kg", "\n".join(json.dumps({"edge": "Authored", "from": f"b{b}p{i}", "to": f"d{b}"}) for i in range(5)))
    a = og.ulid()
    h.submit("kg", a, hold_ms=0)
    end = time.time() + 30
    while time.time() < end:
        act = list((og.read_record(root) or {}).get("active", {}).values())
        if act and act[0]["state"] in ("running", "publishing"):
            break
        time.sleep(0.005)
    (ctl / "rules.json").write_text(json.dumps([
        {"match": f"PUT /{key}/graphs/kg.omni/__manifest/_versions/", "mode": "slow", "delay_ms": 6000, "count": 1},
        {"match": f"/{key}/__cluster/lease/", "mode": "drop_request", "count": -1}]))
    try:
        srv.proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        srv.kill(signal.SIGKILL)
    (ctl / "rules.json").unlink(missing_ok=True)
    time.sleep(3)
    rec = og.read_record(root)
    opa = next(o for o in og.record_ops(rec) if o["operation_id"] == a)
    res["A_after_owner"] = (opa["state"], [(t["type_key"], t["outcome"], t.get("reason")) for t in opa["outcome"].get("tables", [])][:3])
    srv2 = None
    for attempt in range(30):
        try:
            srv2 = og.Server(root, 14951, str(out / f"server{attempt + 2}.log"), env).start(timeout=30)
            break
        except (RuntimeError, TimeoutError):
            time.sleep(1)
    h2 = srv2.http
    for i in range(5):
        h2.load("kg", json.dumps({"type": "Company", "data": {"name": f"above{i}"}}))
    b = og.ulid(); h2.submit("kg", b, hold_ms=0)
    opb = h2.wait_terminal("kg", b)
    rec = og.read_record(root)
    res["B"] = (opb["state"], opb["outcome"].get("start_at"), opb["outcome"].get("resume_at"))
    res["position_after_B"] = (rec.get("kind_state") or {}).get("optimize", {}).get("kg")
    res["A_state_after_B"] = next(o for o in og.record_ops(rec) if o["operation_id"] == a)["state"]
    for _ in range(12):
        s, bb, _ = h2.lookup("kg", a)
        if bb["operation"]["state"] in og.TERMINAL:
            break
    rec = og.read_record(root)
    opa = next(o for o in og.record_ops(rec) if o["operation_id"] == a)
    res["A_final"] = (opa["state"], opa["outcome"].get("resume_at"))
    res["position_after_A_settles"] = (rec.get("kind_state") or {}).get("optimize", {}).get("kg")
    c = og.ulid(); h2.submit("kg", c)
    opc = h2.wait_terminal("kg", c)
    res["next_run_start_at"] = opc["outcome"].get("start_at")
    srv2.kill(signal.SIGTERM)
finally:
    srv.kill(signal.SIGKILL)
    proxy.kill()
print(json.dumps(res, default=str, indent=1))
(out / "result.json").write_text(json.dumps(res, indent=1, default=str))
