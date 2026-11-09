#!/usr/bin/env python3
"""Table-handle cache pressure (#1319) during long rewrites and gate holds.

The 1,030-table catalog (`fixture_big`, graph `big`). Writers insert into and
update rows of TABLES tables (default 200, far over the cache's 64-handle cap,
so every few requests evict), readers read random tables, and a submitter runs
full-catalog optimizes back to back with a gate hold. Every key has one owner
writer whose updates raise `v` by one, so a read that starts after an
acknowledged write must see that row and a `v` at least the acknowledged one:
a stale cached handle or a demoted pin served as current shows as a lower `v`
or a missing row. Also records read latency (reads must not wait for the
gates) and each run's gate hold against max_gate_hold + longest table.
Usage: c11_cache_pressure.py OUTDIR [SECONDS] [TABLES]
"""
import json, os, random, shutil, signal, sys, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import og

out = Path(sys.argv[1]); shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 600
ntables = int(sys.argv[3]) if len(sys.argv) > 3 else 200
log = og.Log(str(out / "log.txt"))
root = og.make_root(out, "cache"); og.apply_cluster(root, config=og.REPO / "stress" / "fixture_big")
HOLD = int(os.environ.get("STRESS_HOLD_MS", "2000"))
srv = og.Server(root, int(os.environ.get("STRESS_PORT", "13996")), str(out / "server.log"),
                {"OMNIGRAPH_OPTIMIZE_MAX_GATE_HOLD_SECONDS": "30"}).start(timeout=120)
h = srv.http
random.seed(int(os.environ.get("STRESS_SEED", "17")))
tables = sorted(random.sample([f"T{n:04d}" for n in range(1030)], ntables))
lock = threading.Lock()
acked = {}        # (table, key) -> (v, ack_time)
violations, read_lat, runs, wstats = [], [], [], {}
stop = threading.Event()


def violate(msg):
    with lock:
        violations.append(msg)
    log("VIOLATION", msg)


for t in tables:  # two fragments each
    for i in range(2):
        k = f"{t}-seed{i}"
        s, b, _ = h.load("big", json.dumps({"type": t, "data": {"k": k, "v": 0}}))
        if s != 200:
            raise SystemExit(f"seed {t}: {s} {b}")
        acked[(t, k)] = (0, time.time())


def writer(w):
    i, mine = 0, []
    while not stop.is_set():
        i += 1
        t = random.choice(tables)
        if mine and random.random() < 0.4:
            t, k = random.choice(mine)
            v = acked[(t, k)][0] + 1
            q = f"query u($k: String, $v: I32) {{ update {t} set {{ v: $v }} where k = $k }}"
            s, b, _ = h.mutate("big", q, {"k": k, "v": v})
            kind = "update"
        else:
            k, v = f"{t}-w{w}i{i}", 0
            s, b, _ = h.load("big", json.dumps({"type": t, "data": {"k": k, "v": v}}))
            kind = "insert"
        with lock:
            wstats[f"{kind}:{s}"] = wstats.get(f"{kind}:{s}", 0) + 1
        if s == 200:
            with lock:
                acked[(t, k)] = (v, time.time())
            if kind == "insert":
                mine.append((t, k))
        elif s not in (409, 429, 503):
            violate(f"writer {kind} {t} {s} {str(b)[:200]}")


def reader():
    while not stop.is_set():
        t = random.choice(tables)
        t0 = time.time()
        with lock:
            expect = {k: v for (tt, k), (v, at) in acked.items() if tt == t and at < t0}
        s, b, _ = h.query("big", f"query r() {{ match {{ $x: {t} }} return {{ $x.k as k, $x.v as v }} }}", token="rd-secret")
        dt = time.time() - t0
        with lock:
            read_lat.append(dt)
        if s != 200:
            violate(f"read {t} {s} {str(b)[:200]}")
            continue
        got = {r["k"]: r["v"] for r in b.get("rows", [])}
        for k, v in expect.items():
            if k not in got:
                violate(f"read {t}: acknowledged {k} missing")
            elif got[k] < v:
                violate(f"read {t}: {k} v={got[k]} below acknowledged {v}")


def submitter():
    while not stop.is_set():
        oid = og.ulid()
        s, b, _ = h.submit("big", oid, hold_ms=HOLD)
        if s not in (200, 202):
            time.sleep(0.2)
            continue
        op = h.wait_terminal("big", oid, 1800, poll=0.3)
        tl = op["outcome"].get("tables", [])
        longest = max([x.get("duration_ms", 0) for x in tl] or [0])
        hold = (op.get("timings") or {}).get("gate_hold_ms")
        runs.append({"state": op["state"], "hold": hold, "longest": longest,
                     "optimized": sum(1 for x in tl if x["outcome"] == "optimized"),
                     "failed": [(x["type_key"], str(x.get("error"))[:120]) for x in tl if x["outcome"] == "failed"]})
        if op["state"] not in ("succeeded", "cancelled"):
            violate(f"optimize {oid} {op['state']} {op.get('reason')}")
        for f in runs[-1]["failed"]:
            violate(f"optimize {oid}: {f}")


ths = [threading.Thread(target=writer, args=(w,), daemon=True) for w in range(3)]
ths += [threading.Thread(target=reader, daemon=True) for _ in range(2)]
ths += [threading.Thread(target=submitter, daemon=True)]
for t in ths:
    t.start()
end = time.time() + seconds
while time.time() < end:
    time.sleep(30)
    log("progress", "acked", len(acked), "runs", len(runs), "writes", sum(wstats.values()), "violations", len(violations))
stop.set()
for t in ths:
    t.join(1900)
# final: every acknowledged row and value, then again after a restart (cold cache)
def final_check(where):
    by_table = {}
    for (t, k), (v, _) in acked.items():
        by_table.setdefault(t, {})[k] = v
    bad = 0
    for t, exp in by_table.items():
        s, b, _ = h.query("big", f"query r() {{ match {{ $x: {t} }} return {{ $x.k as k, $x.v as v }} }}", token="rd-secret")
        got = {r["k"]: r["v"] for r in b.get("rows", [])} if s == 200 else {}
        for k, v in exp.items():
            if got.get(k) is None or got[k] < v:
                bad += 1
                if bad <= 5:
                    violate(f"{where}: {t}/{k} expected v>={v} got {got.get(k)}")
    return bad
bad1 = final_check("final")
srv.kill(signal.SIGTERM)
srv = og.Server(root, srv.port + 1, str(out / "server2.log")).start(timeout=120)
h = srv.http
bad2 = final_check("after restart")
srv.kill(signal.SIGTERM)
lat = sorted(read_lat)
excess = [r["hold"] - HOLD - r["longest"] for r in runs if r["hold"] is not None]
summary = {"tables": ntables, "acked_rows": len(acked), "writes": wstats, "runs": len(runs),
           "runs_states": {s: sum(1 for r in runs if r["state"] == s) for s in {r["state"] for r in runs}},
           "tables_optimized": sum(r["optimized"] for r in runs),
           "read_p50": lat[len(lat) // 2] if lat else None, "read_p99": lat[int(len(lat) * 0.99)] if lat else None,
           "read_max": lat[-1] if lat else None, "reads": len(lat),
           "gate_hold_max": max((r["hold"] for r in runs if r["hold"] is not None), default=None),
           "gate_hold_excess_max_ms": max(excess) if excess else None,
           "final_bad": bad1, "restart_bad": bad2, "violations": violations[:50], "violation_count": len(violations)}
log("SUMMARY", json.dumps(summary)[:3000])
(out / "summary.json").write_text(json.dumps(summary, indent=1))
