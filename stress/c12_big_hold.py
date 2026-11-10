#!/usr/bin/env python3
"""Gate hold beyond the request on a 1,030-table catalog (N15 on object storage).

No writers. Seeds two fragments into SEEDED tables of `fixture_big`, then runs
RUNS full-catalog optimizes back to back with max_gate_hold_ms HOLD, reseeding
one table before each run so each has work. For every run it records the gate
hold, the longest table, the excess over (hold + longest table), the record's
size when the run started, and the run's table count. The excess is the
publication (including the before-publish record write, under the gates) plus
the configuration clear.
Usage: c12_big_hold.py OUTDIR [RUNS] [SEEDED]
"""
import json, os, random, shutil, signal, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import og

out = Path(sys.argv[1]); shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
nruns = int(sys.argv[2]) if len(sys.argv) > 2 else 40
seeded = int(sys.argv[3]) if len(sys.argv) > 3 else 40
HOLD = int(os.environ.get("STRESS_HOLD_MS", "1000"))
log = og.Log(str(out / "log.txt"))
root = og.make_root(out, "bighold"); og.apply_cluster(root, config=og.REPO / "stress" / "fixture_big")
srv = og.Server(root, int(os.environ.get("STRESS_PORT", "13970")), str(out / "server.log"),
                {"OMNIGRAPH_OPTIMIZE_GATE_HOLD_SECONDS": "1", "OMNIGRAPH_OPTIMIZE_MAX_GATE_HOLD_SECONDS": "30"}).start(timeout=180)
h = srv.http
random.seed(5)
tables = sorted(random.sample([f"T{n:04d}" for n in range(1030)], seeded))
for t in tables:
    for i in range(2):
        h.load("big", json.dumps({"type": t, "data": {"k": f"{t}-{i}", "v": i}}))
import threading
POLLERS = int(os.environ.get("STRESS_POLLERS", "0"))
poll_stop = threading.Event()
recent = []
poll_lat = []


def poller():
    while not poll_stop.is_set():
        if not recent:
            time.sleep(0.05)
            continue
        t0 = time.time()
        h.lookup("big", random.choice(recent[-8:]))
        poll_lat.append(time.time() - t0)


for _ in range(POLLERS):
    threading.Thread(target=poller, daemon=True).start()
runs = []
for r in range(nruns):
    t = random.choice(tables)
    for i in range(2):
        h.load("big", json.dumps({"type": t, "data": {"k": f"{t}-r{r}-{i}", "v": i}}))
    rec = og.read_record(root)
    size = len(json.dumps(rec)) if rec else 0
    oid = og.ulid()
    while h.submit("big", oid, hold_ms=HOLD)[0] not in (200, 202):
        time.sleep(0.1)
    recent.append(oid)
    t0 = time.time()
    op = h.wait_terminal("big", oid, 1800, poll=0.2)
    tl = op["outcome"].get("tables", [])
    longest = max([x.get("duration_ms", 0) for x in tl] or [0])
    hold = (op.get("timings") or {}).get("gate_hold_ms")
    runs.append({"run": r, "record_bytes_before": size, "hold": hold, "longest": longest,
                 "excess": (hold - HOLD - longest) if hold is not None else None,
                 "optimized": sum(1 for x in tl if x["outcome"] == "optimized"), "listed": len(tl),
                 "wall_s": round(time.time() - t0, 2), "state": op["state"]})
    log("run", json.dumps(runs[-1]))
poll_stop.set()
srv.kill(signal.SIGTERM)
ex = sorted(x["excess"] for x in runs if x["excess"] is not None)
pl = sorted(poll_lat)
summary = {"root": root, "hold_ms": HOLD, "pollers": POLLERS, "lookups": len(pl),
           "lookup_p50": pl[len(pl) // 2] if pl else None, "lookup_max": pl[-1] if pl else None,
           "hold_max": max(x["hold"] for x in runs if x["hold"] is not None), "runs": len(runs), "excess_p50": ex[len(ex) // 2], "excess_max": ex[-1],
           "first5": [(x["record_bytes_before"], x["excess"]) for x in runs[:5]],
           "last5": [(x["record_bytes_before"], x["excess"]) for x in runs[-5:]]}
log("SUMMARY", json.dumps(summary))
(out / "result.json").write_text(json.dumps({"summary": summary, "runs": runs}, indent=1))
