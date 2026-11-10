#!/usr/bin/env python3
"""S3 lease loss during a served optimize (RFC 3.4 "Lease", 3.6).

With a 4 s lease, the run compacts a large table; RustFS is frozen (SIGSTOP)
for FREEZE seconds at a chosen moment, so lease renewal and every in-flight
request stall; requests already sent may land after SIGCONT. The owner must
fence itself; a successor then resolves the record from main's history. The
checks: the successor starts, the record resolves (or stays `publishing`
until a write on main), at most one commit answers the run's nonce, a run
reported failed/cancelled/interrupted published nothing, acknowledged rows
are present.
Usage: c3_lease_loss.py OUTDIR [DELAY_AFTER_RUNNING_S] [FREEZE_S]
"""
import json, os, shutil, signal, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("STRESS_S3_PREFIX", "s3://omnigraph-stress/lease")
import og, invariants

out = Path(sys.argv[1]); shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
delay = float(sys.argv[2]) if len(sys.argv) > 2 else 0.5
freeze = float(sys.argv[3]) if len(sys.argv) > 3 else 10
rustfs = int(subprocess.run(["pgrep", "-f", "rustfs-bi[n]"], capture_output=True, text=True).stdout.split()[0])
root = og.make_root(out, "lease"); og.apply_cluster(root)
env = {"OMNIGRAPH_CLUSTER_LEASE_SECONDS": "4", "OMNIGRAPH_SHUTDOWN_GRACE_SECONDS": "5"}
srv = og.Server(root, 14997, str(out / "server.log"), env).start()
h = srv.http
acked = set()
for b in range(30):
    rows = [{"type": "Person", "data": {"name": f"b{b}p{i}", "age": i % 90, "bio": f"bio {b} {i}"}} for i in range(3000)]
    s, body, _ = h.load("kg", "\n".join(json.dumps(r) for r in rows), )
    assert s == 200, body
    acked.update(r["data"]["name"] for r in rows)
oid = og.ulid()
h.submit("kg", oid)
end = time.time() + 60
state = None
while time.time() < end:
    s, b, _ = h.lookup("kg", oid)
    state = (b.get("operation") or {}).get("state")
    if state in ("running", "publishing") or state in og.TERMINAL:
        break
    time.sleep(0.05)
res = {"state_before_freeze": state}
time.sleep(delay)
os.kill(rustfs, signal.SIGSTOP)
t0 = time.time()
try:
    srv.proc.wait(timeout=freeze)
except subprocess.TimeoutExpired:
    pass
os.kill(rustfs, signal.SIGCONT)
res["server_exit_during_freeze"] = srv.proc.poll()
try:
    srv.proc.wait(timeout=60)
except subprocess.TimeoutExpired:
    srv.kill(signal.SIGKILL)
res["server_exit"] = srv.proc.returncode
res["server_stopped_after_s"] = round(time.time() - t0, 1)
rec = og.read_record(root)
op = next((o for o in og.record_ops(rec) if o["operation_id"] == oid), None)
res["record_after_owner"] = op and op["state"]
nonce = op and op.get("nonce")
t1 = time.time()
srv2 = None
for attempt in range(40):
    try:
        srv2 = og.Server(root, 14997, str(out / f"server{attempt + 2}.log"), env).start(timeout=30)
        break
    except (RuntimeError, TimeoutError):
        time.sleep(1)
res["successor_started_after_s"] = round(time.time() - t1, 1) if srv2 else None
if srv2:
    h2 = srv2.http
    for _ in range(4):
        s, b, _ = h2.lookup("kg", oid)
        op = b.get("operation") or op
        if op and op["state"] in og.TERMINAL:
            break
        time.sleep(0.3)
    res["after_successor"] = op and op["state"]
    if op and op["state"] == "publishing":
        h2.load("kg", json.dumps({"type": "Company", "data": {"name": "settle"}}))
        for _ in range(4):
            s, b, _ = h2.lookup("kg", oid)
            op = b.get("operation") or op
            if op["state"] in og.TERMINAL:
                break
        res["after_write"] = op["state"]
    res["final"] = (op["state"], op.get("reason"), op.get("published_commit")) if op else None
    nonce = nonce or (op or {}).get("nonce")
    rec = og.read_record(root)
    v = invariants.check_operations(h2, "kg", {oid: op} if op else {}, {oid: nonce} if nonce else {}, rec)
    present = og.keys_of(h2, "kg", "Person", "name")
    v += [f"{len(acked - present)} acknowledged rows missing"] if acked - present else []
    res["violations"] = v
    nid = og.ulid(); h2.submit("kg", nid)
    res["next_run"] = h2.wait_terminal("kg", nid, 300)["state"]
    srv2.kill(signal.SIGTERM)
print(json.dumps(res, default=str))
(out / "result.json").write_text(json.dumps(res, indent=1, default=str))
