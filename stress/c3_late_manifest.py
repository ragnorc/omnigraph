#!/usr/bin/env python3
"""The previous owner's late manifest put (RFC 3.6, 2.4) on S3 (RustFS).

The S3 proxy delays the run's `__manifest` version PUT by SLOW ms and drops
every lease beat, so the owner fences itself while its publication request is
in flight; the request reaches RustFS after the owner stopped. A successor
takes over after a lease and a half and settles the record from main's
history: `succeeded` naming the late commit if it landed, else `failed`
(`not_committed`) once main moves. Invariants as in campaign 3.
Usage: c3_late_manifest.py OUTDIR [SLOW_MS] [WRITE_BEFORE_LOOKUP]
"""
import json, os, shutil, signal, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("STRESS_S3_PREFIX", "s3://omnigraph-stress/latemanifest")
import og, invariants

out = Path(sys.argv[1]); shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
slow = int(sys.argv[2]) if len(sys.argv) > 2 else 6000
write_first = len(sys.argv) > 3 and sys.argv[3] == "write"
PROXY = 14980
root = og.make_root(out, "lm"); og.apply_cluster(root)
key = root[len("s3://"):]
ctl = out / "proxy"; ctl.mkdir()
proxy = subprocess.Popen([sys.executable, str(Path(__file__).parent / "s3proxy.py"), str(PROXY), "9000", str(ctl)],
                         stdout=subprocess.PIPE, text=True)
proxy.stdout.readline()
env = {"AWS_ENDPOINT_URL": f"http://127.0.0.1:{PROXY}", "AWS_ENDPOINT_URL_S3": f"http://127.0.0.1:{PROXY}",
       "OMNIGRAPH_CLUSTER_LEASE_SECONDS": "4", "OMNIGRAPH_SHUTDOWN_GRACE_SECONDS": "5"}
srv = og.Server(root, 14981, str(out / "server.log"), env).start()
h = srv.http
res = {"slow_ms": slow, "write_before_lookup": write_first}
try:
    acked = set()
    for b in range(4):
        rows = [{"type": "Person", "data": {"name": f"b{b}p{i}", "bio": "x"}} for i in range(50)]
        assert h.load("kg", "\n".join(json.dumps(r) for r in rows))[0] == 200
        acked.update(r["data"]["name"] for r in rows)
    rules = [{"match": f"PUT /{key}/graphs/kg.omni/__manifest/_versions/", "mode": "slow", "delay_ms": slow, "count": 1},
             {"match": f"/{key}/__cluster/lease/", "mode": "drop_request", "count": -1}]
    # arm once the run's record says publishing (its before-publish write landed)
    oid = og.ulid()
    h.submit("kg", oid, tables=["node:Person"])
    end = time.time() + 30
    while time.time() < end:
        rec = og.read_record(root)
        act = list((rec or {}).get("active", {}).values())
        if act and act[0]["state"] in ("running", "publishing"):
            break
        time.sleep(0.005)
    (ctl / "rules.json").write_text(json.dumps(rules))
    t0 = time.time()
    try:
        srv.proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        srv.kill(signal.SIGKILL)
    res["owner_exit"] = srv.proc.returncode
    res["owner_stopped_after_s"] = round(time.time() - t0, 1)
    (ctl / "rules.json").unlink(missing_ok=True)
    log = (ctl / "log").read_text().splitlines()
    res["manifest_puts"] = [l.split(" fault=")[0].split()[0] + " " + l.split(" fault=")[1] for l in log if "/__manifest/_versions/" in l and l.split(" ", 2)[1] == "PUT"][-3:]
    rec = og.read_record(root)
    op = next((o for o in og.record_ops(rec) if o["operation_id"] == oid), None)
    res["record_after_owner"] = op and (op["state"], op.get("nonce"))
    nonce = op and op.get("nonce")
    if not (len(sys.argv) > 4 and sys.argv[4] == "nowait"):
        time.sleep(max(0, slow / 1000 - (time.time() - t0)) + 1)  # let the late put land
    srv2 = None
    t1 = time.time()
    for attempt in range(30):
        try:
            srv2 = og.Server(root, 14981, str(out / f"server{attempt + 2}.log"), env).start(timeout=30)
            break
        except (RuntimeError, TimeoutError):
            time.sleep(1)
    res["successor_after_s"] = round(time.time() - t1, 1) if srv2 else None
    h2 = srv2.http
    if write_first:
        h2.load("kg", json.dumps({"type": "Company", "data": {"name": "move"}}))
    states = []
    for _ in range(6):
        s, b, _ = h2.lookup("kg", oid)
        op = b.get("operation") or op
        states.append(op["state"])
        if op["state"] in og.TERMINAL:
            break
        time.sleep(0.3)
    if op["state"] == "publishing":
        h2.load("kg", json.dumps({"type": "Company", "data": {"name": "settle"}}))
        for _ in range(4):
            s, b, _ = h2.lookup("kg", oid)
            op = b.get("operation") or op
            states.append(op["state"])
            if op["state"] in og.TERMINAL:
                break
    res["successor_states"] = states
    res["final"] = (op["state"], op.get("reason"), op.get("published_commit"))
    time.sleep(max(0, slow / 1000 - (time.time() - t0)) + 2)  # the late put has arrived by now
    res["late_put_answers"] = [l for l in (ctl / "log").read_text().splitlines() if "/__manifest/_versions/" in l and " PUT " in l][-2:]
    commits = invariants.main_history(h2, "kg")
    res["commits_answering_nonce"] = [c["graph_commit_id"] for c in commits if nonce and c["graph_commit_id"].endswith(nonce)]
    rec = og.read_record(root)
    v = invariants.check_operations(h2, "kg", {oid: op}, {oid: nonce} if nonce else {}, rec)
    missing = acked - og.keys_of(h2, "kg", "Person", "name")
    if missing:
        v.append(f"{len(missing)} acknowledged rows missing")
    res["violations"] = v
    nid = og.ulid(); h2.submit("kg", nid)
    res["next_run"] = h2.wait_terminal("kg", nid, 120)["state"]
    srv2.kill(signal.SIGTERM)
finally:
    srv.kill(signal.SIGKILL)
    proxy.kill()
print(json.dumps(res, default=str))
(out / "result.json").write_text(json.dumps(res, indent=1, default=str))
