#!/usr/bin/env python3
"""Per-write wait beside back-to-back served optimizes (RFC 1.2).

RFC 1.2: "The writer wait is at most `max_gate_hold`, plus one table's
rewrite, plus the publication, plus the configuration clear." That is a
bound per run. This checks it per write: one submitter submits the next
full-catalog optimize as soon as the previous one ends (what a client
looping `omnigraph optimize` does), small writers load Person/Company rows,
and every run is captured with its timings. A run's gate-hold interval is
placed at [accepted_at + queued_ms, + gate_hold_ms] (capture under the gates
is short). For each write it counts the runs whose gate holds overlap it.
A control phase without optimize gives the write baseline.
Usage: c1_writer_wait.py OUTDIR [SECONDS] [WRITERS]
"""
import datetime, json, os, shutil, signal, sys, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import og

out = Path(sys.argv[1]); shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 240
nwriters = int(sys.argv[3]) if len(sys.argv) > 3 else 2
HOLD_MS = int(os.environ.get("STRESS_HOLD_MS", "1000"))
root = og.make_root(out, "ww"); og.apply_cluster(root)
env = {"OMNIGRAPH_OPTIMIZE_GATE_HOLD_SECONDS": str(max(1, HOLD_MS // 1000)), "OMNIGRAPH_OPTIMIZE_MAX_GATE_HOLD_SECONDS": "5"}
srv = og.Server(root, int(os.environ.get("STRESS_PORT", "13950")), str(out / "server.log"), env).start(timeout=90)
h = srv.http


def ts(text):
    return datetime.datetime.fromisoformat(text.replace("Z", "+00:00")[:26] + "+00:00").timestamp()


def phase(name, optimize):
    stop = threading.Event()
    writes, runs = [], []

    def writer(w):
        i = 0
        while not stop.is_set():
            i += 1
            typ = "Person" if i % 2 else "Company"
            rows = [{"type": typ, "data": {"name": f"{name}w{w}i{i}k{k}", **({"bio": "x"} if typ == "Person" else {})}}
                    for k in range(5)]
            t0 = time.time()
            if KIND == "update":
                # one row of this writer's own seed, so writers never conflict with each other
                q = "query u($n: String, $a: I32) { update Person set { age: $a } where name = $n }"
                s, _, _ = h.mutate("kg", q, {"n": f"seed-w{w}-{i % 20}", "a": i % 1000})
                typ = "update"
            else:
                s, _, _ = h.load("kg", "\n".join(json.dumps(r) for r in rows))
            writes.append((t0, time.time(), typ, s))

    def submitter():
        while not stop.is_set():
            oid = og.ulid()
            s, b, _ = h.submit("kg", oid, hold_ms=HOLD_MS)
            if s not in (200, 202):
                time.sleep(0.05)
                continue
            while True:
                s, b, _ = h.lookup("kg", oid)
                op = (b or {}).get("operation") if s == 200 else None
                if op and op["state"] in og.TERMINAL:
                    runs.append(op)
                    break
                time.sleep(0.02)

    ths = [threading.Thread(target=writer, args=(w,), daemon=True) for w in range(nwriters)]
    if optimize:
        ths.append(threading.Thread(target=submitter, daemon=True))
    for t in ths:
        t.start()
    time.sleep(seconds)
    stop.set()
    for t in ths:
        t.join(120)
    return writes, runs


res = {}
KIND = os.environ.get("STRESS_WRITE_KIND", "load")
if KIND == "update":
    for w in range(nwriters):
        h.load("kg", "\n".join(json.dumps({"type": "Person", "data": {"name": f"seed-w{w}-{k}", "bio": "x", "age": 0}})
                               for k in range(20)))
for name, optimize in (("control", False), ("optimize", True), ("control2", False)):
    writes, runs = phase(name, optimize)
    ok = sorted(t1 - t0 for t0, t1, _, s in writes if s == 200)
    holds = []
    for op in runs:
        t = op.get("timings") or {}
        if t.get("gate_hold_ms") is None:
            continue
        start = ts(op["accepted_at"]) + t.get("queued_ms", 0) / 1000
        longest = max([x.get("duration_ms", 0) for x in op["outcome"].get("tables", [])] or [0])
        holds.append((start, start + t["gate_hold_ms"] / 1000, t["gate_hold_ms"], longest, op["operation_id"]))
    spans = []
    for t0, t1, typ, s in writes:
        if s != 200:
            continue
        over = [hd for hd in holds if hd[0] < t1 and hd[1] > t0]
        spans.append((t1 - t0, len(over), sum(min(t1, hd[1]) - max(t0, hd[0]) for hd in over)))
    spans.sort(reverse=True)
    r = {
        "writes_ok": len(ok), "writes_other": sum(1 for *_, s in writes if s != 200),
        "p50": ok[len(ok) // 2] if ok else None, "p99": ok[int(len(ok) * 0.99)] if ok else None,
        "max": ok[-1] if ok else None, "runs": len(runs),
        "gate_hold_ms_max": max((hd[2] for hd in holds), default=None),
        "gate_hold_fraction": round(sum(hd[1] - hd[0] for hd in holds) / seconds, 3),
        "writes_spanning_2plus_holds": sum(1 for s in spans if s[1] >= 2),
        "writes_spanning_3plus_holds": sum(1 for s in spans if s[1] >= 3),
        "slowest_writes (latency, holds overlapped, held seconds inside)": [(round(a, 2), b, round(c, 2)) for a, b, c in spans[:8]],
    }
    res[name] = r
    print(name, json.dumps(r), flush=True)
    (out / f"{name}.json").write_text(json.dumps({"writes": writes, "holds": holds}, default=str))
srv.kill(signal.SIGTERM)
(out / "result.json").write_text(json.dumps(res, indent=1))
