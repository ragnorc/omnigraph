#!/usr/bin/env python3
"""A late record put from the previous owner (RFC 3.6) on S3 (RustFS).

The proxy delays the owner's final operation-record PUT by SLOW ms and drops
its lease beats, so the owner fences before its final write lands. A
successor takes over and settles the record from history (writing it); the
old owner's late PUT then arrives with a stale precondition. Expected: the
late PUT fails its precondition (412) and does not overwrite the successor's
record; the record ends as history says.
Usage: c3_late_record.py OUTDIR [SLOW_MS]
"""
import json, os, shutil, signal, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("STRESS_S3_PREFIX", "s3://omnigraph-stress/laterecord")
import og, invariants

out = Path(sys.argv[1]); shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
slow = int(sys.argv[2]) if len(sys.argv) > 2 else 15000
PROXY = 14960
root = og.make_root(out, "lr"); og.apply_cluster(root)
key = root[len("s3://"):]
ctl = out / "proxy"; ctl.mkdir()
proxy = subprocess.Popen([sys.executable, str(Path(__file__).parent / "s3proxy.py"), str(PROXY), "9000", str(ctl)],
                         stdout=subprocess.PIPE, text=True)
proxy.stdout.readline()
env = {"AWS_ENDPOINT_URL": f"http://127.0.0.1:{PROXY}", "AWS_ENDPOINT_URL_S3": f"http://127.0.0.1:{PROXY}",
       "OMNIGRAPH_CLUSTER_LEASE_SECONDS": "4", "OMNIGRAPH_SHUTDOWN_GRACE_SECONDS": "5"}
srv = og.Server(root, 14961, str(out / "server.log"), env).start()
h = srv.http
res = {"slow_ms": slow}
try:
    for b in range(3):
        assert h.load("kg", "\n".join(json.dumps({"type": "Person", "data": {"name": f"b{b}p{i}", "bio": "x"}}) for i in range(30)))[0] == 200
    rec_put = f"PUT /{key}/__cluster/operations.json"
    def puts():
        p = ctl / "log"
        return [l for l in p.read_text().splitlines() if rec_put in l] if p.exists() else []
    base = len(puts())
    oid = og.ulid()
    h.submit("kg", oid, tables=["node:Person"])
    # after accept, running, publishing: delay the final write, drop the beats
    end = time.time() + 30
    while time.time() < end and len(puts()) < base + 3:
        time.sleep(0.001)
    (ctl / "rules.json").write_text(json.dumps([
        {"match": rec_put, "mode": "slow", "delay_ms": slow, "count": 1},
        {"match": f"/{key}/__cluster/lease/", "mode": "drop_request", "count": -1}]))
    t0 = time.time()
    try:
        srv.proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        srv.kill(signal.SIGKILL)
    (ctl / "rules.json").unlink(missing_ok=True)
    res["owner_exit"] = srv.proc.returncode
    rec = og.read_record(root)
    op = next(o for o in og.record_ops(rec) if o["operation_id"] == oid)
    res["record_after_owner"] = op["state"]
    nonce = op.get("nonce")
    srv2 = None
    for attempt in range(30):
        try:
            srv2 = og.Server(root, 14961, str(out / f"server{attempt + 2}.log"), env).start(timeout=30)
            break
        except (RuntimeError, TimeoutError):
            time.sleep(1)
    h2 = srv2.http
    states = []
    for _ in range(4):
        s, b, _ = h2.lookup("kg", oid)
        states.append(b["operation"]["state"])
        if states[-1] in og.TERMINAL:
            break
    res["successor_states"] = states
    res["successor_view"] = (b["operation"]["state"], b["operation"].get("published_commit"))
    time.sleep(max(0, slow / 1000 - (time.time() - t0)) + 2)
    late = [l for l in (ctl / "log").read_text().splitlines() if rec_put in l]
    res["record_puts_tail"] = [l.split(" fault=")[0].split()[0] + " " + l.split("fault=")[-1] for l in late[-3:]]
    rec = og.read_record(root)
    op = next(o for o in og.record_ops(rec) if o["operation_id"] == oid)
    res["record_after_late_put"] = (op["state"], op.get("published_commit"))
    res["record_unchanged_by_late_put"] = res["record_after_late_put"] == res["successor_view"]
    commits = invariants.main_history(h2, "kg")
    res["commits_answering_nonce"] = sum(1 for c in commits if nonce and c["graph_commit_id"].endswith(nonce))
    srv2.kill(signal.SIGTERM)
finally:
    srv.kill(signal.SIGKILL)
    proxy.kill()
print(json.dumps(res, default=str))
(out / "result.json").write_text(json.dumps(res, indent=1, default=str))
