#!/usr/bin/env python3
"""Reads beside a slow optimize, no seams: do reads block while a run holds the gates?

Loads Person in many small batches (many fragments), then runs a served
optimize while a reader thread queries main continuously and a writer thread
loads. Reports read latency percentiles inside and outside the run's window,
and write latency inside it against the run's gate hold.
Usage: c1_reads_during_optimize.py OUTDIR [ROWS_PER_LOAD] [LOADS]
(STRESS_S3_PREFIX selects an S3 root)
"""
import json, sys, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import og

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
rows, loads = int(sys.argv[2]) if len(sys.argv) > 2 else 2000, int(sys.argv[3]) if len(sys.argv) > 3 else 40
root = og.make_root(out, "reads"); og.apply_cluster(root)
srv = og.Server(root, 13990, str(out / "server.log"), {"OMNIGRAPH_OPTIMIZE_GATE_HOLD_SECONDS": "120"}).start()
h = srv.http
t0 = time.time()
for b in range(loads):
    nd = "\n".join(json.dumps({"type": "Person", "data": {"name": f"b{b}p{i}", "age": i % 90, "bio": f"bio {b} {i} about graph compaction"}}) for i in range(rows))
    s, body, _ = h.load("kg", nd)
    assert s == 200, body
print(f"seeded {rows*loads} rows in {loads} loads in {time.time()-t0:.1f}s", flush=True)
stop = False
reads, writes = [], []
q = "query c() { match { $x: Person } return { count($x) as n } }"
def reader():
    while not stop:
        a = time.time(); s, _, _ = h.call("POST", "/graphs/kg/query", {"query": q}, token="rd-secret", timeout=300); reads.append((a, time.time() - a, s))
def writer():
    n = 0
    while not stop:
        n += 1; a = time.time()
        s, _, _ = h.load("kg", json.dumps({"type": "Company", "data": {"name": f"w{n}"}}))
        writes.append((a, time.time() - a, s)); time.sleep(0.05)
ths = [threading.Thread(target=reader, daemon=True), threading.Thread(target=writer, daemon=True)]
for t in ths: t.start()
time.sleep(3)
oid = og.ulid(); start = time.time(); h.submit("kg", oid)
op = h.wait_terminal("kg", oid, 1200, poll=0.5); end = time.time()
time.sleep(3); stop = True
for t in ths: t.join(300)
def pct(xs, q):
    xs = sorted(xs); return round(xs[min(len(xs) - 1, int(len(xs) * q))], 3) if xs else None
inside = [l for a, l, s in reads if start <= a <= end]; outside = [l for a, l, s in reads if not (start <= a <= end)]
w_in = [l for a, l, s in writes if start <= a <= end]
res = {"root": root.split(":")[0], "rows": rows * loads, "state": op["state"], "gate_hold_ms": op["timings"].get("gate_hold_ms"),
       "run_s": round(end - start, 2), "tables": [(t["type_key"], t["outcome"], t["duration_ms"]) for t in op["outcome"].get("tables", [])],
       "reads_inside": (len(inside), pct(inside, .5), pct(inside, .99), pct(inside, 1.0)),
       "reads_outside": (len(outside), pct(outside, .5), pct(outside, .99), pct(outside, 1.0)),
       "writes_inside": (len(w_in), pct(w_in, .5), pct(w_in, 1.0)), "read_errors": sum(1 for _, _, s in reads if s != 200)}
print(json.dumps(res), flush=True)
(out / "result.json").write_text(json.dumps(res, indent=1))
srv.kill(15)
