#!/usr/bin/env python3
"""Campaign 5c: a 1,030-table catalog through the served path, default settings.

Seeds three small fragments into 20 tables spread across the key order, then
runs full-catalog optimizes back to back. Checks the record lists at most
1,024 table outcomes and counts the rest (`more_tables`), its size, and how
many runs it takes to compact every seeded table.
Usage: c5_big_catalog.py OUTDIR BIN_DIR
"""
import json, os, shutil, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import og

out = Path(sys.argv[1]); bindir = Path(sys.argv[2])
shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
root = str(out / "root"); os.makedirs(root)
og.apply_cluster(root, config=og.REPO / "stress" / "fixture_big")
srv = og.Server(root, 14901, str(out / "server.log"), {}, binary=str(bindir / "omnigraph-server")).start()
h = srv.http
seeded = [f"T{n:04d}" for n in range(0, 1030, 52)]
for t in seeded:
    for i in range(3):
        s, b, _ = h.load("big", json.dumps({"type": t, "data": {"k": f"{t}-{i}", "v": i}}))
        assert s == 200, b
runs = []
for r in range(8):
    oid = og.ulid(); t0 = time.time()
    s, b, _ = h.submit("big", oid)
    op = h.wait_terminal("big", oid, 1800, poll=1)
    tables = op["outcome"].get("tables", [])
    by = {}
    for t in tables:
        key = t["outcome"] + (":" + t["reason"] if t.get("reason") else "")
        by[key] = by.get(key, 0) + 1
    optimized = sorted(t["type_key"] for t in tables if t["outcome"] == "optimized")
    rec = og.read_record(root)
    runs.append({"run": r, "state": op["state"], "start_at": op["outcome"].get("start_at"), "resume_at": op["outcome"].get("resume_at"),
                 "listed": len(tables), "more_tables": op["outcome"].get("more_tables"), "by_outcome": by,
                 "optimized": optimized, "gate_hold_ms": op["timings"].get("gate_hold_ms"), "took_s": round(time.time() - t0, 1),
                 "op_bytes": len(json.dumps(op)), "kind_state": rec.get("kind_state")})
    print(json.dumps(runs[-1])[:900], flush=True)
    if not optimized and not op["outcome"].get("resume_at"):
        break
done = set()
for r in runs:
    done.update(x.split(":", 1)[1] for x in r["optimized"])
print("seeded", len(seeded), "compacted", len(done & set(seeded)), "runs", len(runs), flush=True)
(out / "runs.json").write_text(json.dumps(runs, indent=1))
srv.kill(15)
