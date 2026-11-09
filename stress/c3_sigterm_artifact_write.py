#!/usr/bin/env python3
"""SIGTERM while the adjacency-artifact write is in flight (RFC 1.8, 3.4).

The artifact's rename onto `csr-current.bin` is delayed DELAY ms (fault shim);
SIGTERM arrives once that write has started. RFC: an issued write is awaited
within the shutdown grace (25 s here): with DELAY 8000 the server should
finish, record `succeeded` with the artifact refreshed and exit 0; with DELAY
40000 the grace ends first and the server exits nonzero keeping the lock.
Usage: c3_sigterm_artifact_write.py OUTDIR BIN_DIR DELAY_MS
"""
import json, os, shutil, signal, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import og

out = Path(sys.argv[1]); bindir = Path(sys.argv[2]); delay = sys.argv[3]
shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
root = str(out / "root"); os.makedirs(root); og.apply_cluster(root)
ctl = out / "ctl"; ctl.mkdir()
(ctl / "pattern").write_text("csr-current.bin")
env = {"LD_PRELOAD": "/tmp/faultfs.so", "FAULTFS_DIR": str(ctl), "OMNIGRAPH_SHUTDOWN_GRACE_SECONDS": "25"}
srv = og.Server(root, 13885, str(out / "server.log"), env, binary=str(bindir / "omnigraph-server")).start()
h = srv.http
for i in range(3):
    h.load("kg", "\n".join(json.dumps({"type": "Person", "data": {"name": f"p{i}-{k}", "bio": "b"}}) for k in range(20)))
    h.load("kg", "\n".join(json.dumps({"edge": "Knows", "from": f"p{i}-{k}", "to": f"p{i}-{k+1}"}) for k in range(19)))
(ctl / "delay_ms").write_text(delay)
oid = og.ulid(); h.submit("kg", oid)
end = time.time() + 60
while time.time() < end and not (ctl / "hits").exists():
    time.sleep(0.01)
res = {"delay_ms": int(delay), "write_started": (ctl / "hits").exists()}
time.sleep(0.5)
t0 = time.time()
srv.proc.send_signal(signal.SIGTERM)
try:
    srv.proc.wait(timeout=120)
except Exception:
    srv.proc.kill(); srv.proc.wait()
res["stop_s"] = round(time.time() - t0, 1)
res["exit"] = srv.proc.returncode
artifact = Path(root) / "graphs" / "kg.omni" / "__graph_index" / "csr-current.bin"
res["artifact_exists"] = artifact.exists()
rec = og.read_record(root)
op = next((o for o in og.record_ops(rec) if o["operation_id"] == oid), None)
res["record"] = op and (op["state"], op.get("reason"), (op["outcome"].get("artifact") or {}))
res["lock_kept"] = (Path(root) / "__cluster" / "lock.json").exists()
(ctl / "delay_ms").unlink()
print(json.dumps(res, default=str))
(out / "result.json").write_text(json.dumps(res, indent=1, default=str))
