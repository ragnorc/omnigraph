#!/usr/bin/env python3
"""Run byte budget, per-table ceilings and the resume position (RFC 1.3, 1.4), served.

Seeds several small fragments in four tables. The server's run byte budget
lets roughly one table's rewrite through; a fragment ceiling defers one table
with `resource_limit`. Checks per run: the first table with work always runs;
a table that would pass the remaining budget is `deferred(byte_budget)` with
every later table; `resume_at` names it; the next run starts there; a
`resource_limit` deferral neither stops the run nor sets the position; over
successive runs every table within its ceilings is compacted.
Usage: c5_budget.py OUTDIR [BIN_DIR]
"""
import json, os, shutil, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import og

out = Path(sys.argv[1]); shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
bindir = Path(sys.argv[2]) if len(sys.argv) > 2 else og.BIN
root = og.make_root(out, "budget"); og.apply_cluster(root)
srv0 = og.Server(root, 13870, str(out / "seed.log"), {}, binary=str(bindir / "omnigraph-server")).start()
h = srv0.http
for b in range(4):
    h.load("kg", "\n".join(json.dumps({"type": "Person", "data": {"name": f"b{b}p{i}", "bio": "graph " * 50}}) for i in range(200)))
    h.load("kg", "\n".join(json.dumps({"type": "Company", "data": {"name": f"b{b}c{i}", "industry": "x" * 200}}) for i in range(200)))
    h.load("kg", "\n".join(json.dumps({"edge": "Knows", "from": f"b{b}p{i}", "to": f"b{b}p{i+1}"}) for i in range(150)))
    h.load("kg", "\n".join(json.dumps({"edge": "WorksAt", "from": f"b{b}p{i}", "to": f"b{b}c{i}"}) for i in range(150)))
for i in range(6):  # many fragments on Doc: over the fragment ceiling
    h.load("kg", json.dumps({"type": "Doc", "data": {"slug": f"d{i}", "title": "t", "embedding": [0.1] * 8}}))
srv0.kill(15)
env = {"OMNIGRAPH_OPTIMIZE_RUN_SOURCE_BYTES": "60000", "OMNIGRAPH_OPTIMIZE_MAX_SOURCE_BYTES": "60000",
       "OMNIGRAPH_OPTIMIZE_MAX_SOURCE_FRAGMENTS": "5"}
srv = og.Server(root, 13871, str(out / "server.log"), env, binary=str(bindir / "omnigraph-server")).start()
h = srv.http
runs, violations = [], []
for r in range(8):
    oid = og.ulid(); h.submit("kg", oid)
    op = h.wait_terminal("kg", oid)
    tables = [(t["type_key"], t["outcome"], t.get("reason"), t.get("source_bytes")) for t in op["outcome"].get("tables", [])]
    rec = og.read_record(root)
    pos = (rec.get("kind_state") or {}).get("optimize", {}).get("kg", {}).get("resume_at")
    runs.append({"run": r, "start_at": op["outcome"].get("start_at"), "resume_at": op["outcome"].get("resume_at"),
                 "stored": pos, "tables": tables})
    print(json.dumps(runs[-1]), flush=True)
    first_bound = next((t[0] for t in tables if t[1] == "deferred" and t[2] in ("byte_budget", "gate_hold_limit")), None)
    if op["outcome"].get("resume_at") != first_bound:
        violations.append(f"run {r}: resume_at {op['outcome'].get('resume_at')} vs first bound deferral {first_bound}")
    if pos != first_bound:
        violations.append(f"run {r}: stored position {pos} vs first bound deferral {first_bound}")
    work = [t for t in tables if t[1] in ("optimized", "deferred") and t[2] != "resource_limit"]
    if work and work[0][1] == "deferred":
        violations.append(f"run {r}: the first table with work was deferred {work[0]}")
    seen_budget = False
    for t in tables:
        if t[2] == "byte_budget":
            seen_budget = True
        elif seen_budget and t[1] != "deferred":
            violations.append(f"run {r}: {t[0]} {t[1]} after a byte_budget deferral")
    if not any(t[1] == "optimized" for t in tables) and first_bound is None:
        break
compacted = {t[0] for r in runs for t in r["tables"] if t[1] == "optimized"}
summary = {"runs": len(runs), "compacted": sorted(compacted), "violations": violations}
print(json.dumps(summary))
(out / "result.json").write_text(json.dumps({"runs": runs, **summary}, indent=1))
srv.kill(15)
