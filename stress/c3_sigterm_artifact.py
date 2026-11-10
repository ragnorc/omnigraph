#!/usr/bin/env python3
"""SIGTERM during the adjacency-artifact scan (RFC 1.8, 3.4).

A node-only optimize on a graph with no artifact yet builds one after its
publication. Every open of an edge data file is delayed (fault shim), so the
edge scan is slow. SIGTERM arrives one second into the scan. The RFC: the scan
is abandoned, no artifact write is issued once a shutdown is pending, the run
is `succeeded` with its artifact skipped, and the process stops promptly.
Usage: c3_sigterm_artifact.py OUTDIR [BIN_DIR] [DELAY_MS]
"""
import json, os, shutil, signal, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import og

out = Path(sys.argv[1]); bindir = Path(sys.argv[2]) if len(sys.argv) > 2 else og.BIN
delay = sys.argv[3] if len(sys.argv) > 3 else "5000"
shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
root = str(out / "root"); os.makedirs(root); og.apply_cluster(root)
ctl = out / "ctl"; ctl.mkdir()
(ctl / "open_pattern").write_text("kg.omni/edges/")
(ctl / "pattern").write_text("csr-current.bin")
env = {"LD_PRELOAD": "/tmp/faultfs.so", "FAULTFS_DIR": str(ctl), "OMNIGRAPH_SHUTDOWN_GRACE_SECONDS": "25"}
srv = og.Server(root, 14995, str(out / "server.log"), env, binary=str(bindir / "omnigraph-server")).start()
h = srv.http
for i in range(4):
    h.load("kg", "\n".join(json.dumps({"type": "Person", "data": {"name": f"p{i}-{k}", "bio": "b"}}) for k in range(20)))
    h.load("kg", "\n".join(json.dumps({"type": "Company", "data": {"name": f"c{i}-{k}"}}) for k in range(3)))
for i in range(3):
    h.load("kg", "\n".join(json.dumps({"edge": "Knows", "from": f"p{i}-{k}", "to": f"p{i}-{k+1}"}) for k in range(19)))
    h.load("kg", "\n".join(json.dumps({"edge": "WorksAt", "from": f"p{i}-{k}", "to": f"c{i}-{k % 3}"}) for k in range(20)))
artifact = Path(root) / "graphs" / "kg.omni" / "__graph_index" / "csr-current.bin"
res = {"artifact_before": artifact.exists()}
(ctl / "open_delay_ms").write_text(delay)
oid = og.ulid(); h.submit("kg", oid, tables=["node:Person"])
end = time.time() + 60
while time.time() < end and not (ctl / "open_hits").exists():
    time.sleep(0.01)
res["scan_started"] = (ctl / "open_hits").exists()
time.sleep(1.0)
t_term = time.time()
srv.proc.send_signal(signal.SIGTERM)
try:
    srv.proc.wait(timeout=120)
except Exception:
    srv.proc.kill(); srv.proc.wait()
res["stop_s"] = round(time.time() - t_term, 2)
res["exit"] = srv.proc.returncode
res["edge_opens_after_term"] = sum(1 for l in (ctl / "open_hits").read_text().splitlines() if int(l.split()[0]) > t_term)
hits = (ctl / "hits").read_text().splitlines() if (ctl / "hits").exists() else []
res["artifact_renames_after_term"] = sum(1 for l in hits if int(l.split()[0]) >= int(t_term))
res["artifact_after"] = artifact.exists()
rec = og.read_record(root)
op = next((o for o in og.record_ops(rec) if o["operation_id"] == oid), None)
res["record"] = op and (op["state"], op.get("reason"), (op["outcome"].get("artifact") or {}))
(ctl / "open_delay_ms").unlink()
print(json.dumps(res, default=str))
(out / "result.json").write_text(json.dumps(res, indent=1, default=str))
