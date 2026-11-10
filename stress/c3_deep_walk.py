#!/usr/bin/env python3
"""A resolution walk n commits deep, partly through `__history` (RFC 2.4).

As c3_late_manifest: the owner fences while its manifest PUT is delayed, and
the put lands late, so the record is `publishing` and its commit is on main.
Before the successor's first lookup, DEPTH writes put commits above it (enough
to release the commit from main's buffer into `__history`), with walk limit L.
Lookups then walk one page each; after a few pages more writes land above the
tip (the branch grows mid-walk). Expected: `succeeded` naming the late commit,
after ceil(DEPTH/L)+1 lookups (one more for the orphan move, N3), `resolution`
showing a cursor and a growing `visited`, never `failed`.
Usage: c3_deep_walk.py OUTDIR [DEPTH] [L]
"""
import json, math, os, shutil, signal, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("STRESS_S3_PREFIX", "s3://omnigraph-stress/deepwalk")
import og, invariants

out = Path(sys.argv[1]); shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
depth = int(sys.argv[2]) if len(sys.argv) > 2 else 300
limit = int(sys.argv[3]) if len(sys.argv) > 3 else 16
PROXY = 14970
root = og.make_root(out, "dw"); og.apply_cluster(root)
key = root[len("s3://"):]
ctl = out / "proxy"; ctl.mkdir()
proxy = subprocess.Popen([sys.executable, str(Path(__file__).parent / "s3proxy.py"), str(PROXY), "9000", str(ctl)],
                         stdout=subprocess.PIPE, text=True)
proxy.stdout.readline()
env = {"AWS_ENDPOINT_URL": f"http://127.0.0.1:{PROXY}", "AWS_ENDPOINT_URL_S3": f"http://127.0.0.1:{PROXY}",
       "OMNIGRAPH_CLUSTER_LEASE_SECONDS": "4", "OMNIGRAPH_SHUTDOWN_GRACE_SECONDS": "5",
       "OMNIGRAPH_OPERATION_RESOLUTION_WALK_LIMIT": str(limit)}
srv = og.Server(root, 14971, str(out / "server.log"), env).start()
h = srv.http
res = {"depth": depth, "limit": limit}
try:
    for b in range(3):
        assert h.load("kg", "\n".join(json.dumps({"type": "Person", "data": {"name": f"b{b}p{i}", "bio": "x"}}) for i in range(20)))[0] == 200
    oid = og.ulid()
    h.submit("kg", oid, tables=["node:Person"])
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
    op = next(o for o in og.record_ops(rec) if o["operation_id"] == oid)
    res["record_after_owner"] = op["state"]
    nonce = op.get("nonce")
    srv2 = None
    for attempt in range(30):
        try:
            srv2 = og.Server(root, 14971, str(out / f"server{attempt + 2}.log"), env).start(timeout=30)
            break
        except (RuntimeError, TimeoutError):
            time.sleep(1)
    h2 = srv2.http
    t0 = time.time()
    for i in range(depth):
        assert h2.load("kg", json.dumps({"type": "Company", "data": {"name": f"above{i}"}}))[0] == 200
    res["writes_s"] = round(time.time() - t0, 1)
    commits = invariants.main_history(h2, "kg")
    chain = invariants.first_parent_chain(commits)
    landed = [c for c in chain if nonce and c["graph_commit_id"].endswith(nonce)]
    res["late_commit_on_main"] = bool(landed)
    res["late_commit_depth"] = next((i for i, c in enumerate(chain) if nonce and c["graph_commit_id"].endswith(nonce)), None)
    lookups, progress = 0, []
    while lookups < 200:
        lookups += 1
        s, b, _ = h2.lookup("kg", oid)
        op = b["operation"]
        progress.append((op["state"], (op.get("resolution") or {}).get("visited"), (op.get("resolution") or {}).get("cursor", "")[-8:]))
        if op["state"] in og.TERMINAL:
            break
        if lookups == 5:  # the branch grows while the walk is open
            for i in range(10):
                h2.load("kg", json.dumps({"type": "Company", "data": {"name": f"grow{i}"}}))
    res["lookups"] = lookups
    res["expected_lookups"] = math.ceil((res["late_commit_depth"] or 0) / limit) + 1
    res["progress_head"] = progress[:3]
    res["progress_tail"] = progress[-3:]
    res["final"] = (op["state"], op.get("reason"), op.get("published_commit"))
    res["final_is_late_commit"] = bool(landed) and op.get("published_commit") == landed[0]["graph_commit_id"]
    srv2.kill(signal.SIGTERM)
finally:
    srv.kill(signal.SIGKILL)
    proxy.kill()
print(json.dumps(res, default=str))
(out / "result.json").write_text(json.dumps(res, indent=1, default=str))
