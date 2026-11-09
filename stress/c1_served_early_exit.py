#!/usr/bin/env python3
"""Served consequence of the pipeline's early exit (PR-head binaries in /tmp/prhead).

A first run with a 1 ms gate hold stores a resume position; the next
full-catalog run, whose first rewrite is parked while the planner runs ahead,
reports later tables deferred(gate_hold_limit) though its gate hold was far
below the limit, and the record's resume position is cleared.
Usage: c1_served_early_exit.py OUTDIR [BIN_DIR]
"""
import json, os, shutil, sys, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import og
from c3_crash import arm, wait_reached

out = Path(sys.argv[1]); bindir = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/prhead")
shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
root = str(out / "root"); os.makedirs(root); og.apply_cluster(root)
ctl = out / "ctl"; ctl.mkdir()
env = {"STRESS_ROOT": root, "STRESS_CTL": str(ctl), "STRESS_BIND": "127.0.0.1:13901",
       "OMNIGRAPH_OPTIMIZE_PLANNING_CONCURRENCY": "1", "OMNIGRAPH_OPTIMIZE_GATE_HOLD_SECONDS": "60"}
srv = og.Server(root, 13901, str(out / "child.log"), env, binary=str(bindir / "stress_served_child"),
                args=["--exact", "stress_served_child", "--ignored", "--nocapture"]).start()
h = srv.http
import atexit; atexit.register(lambda: srv.kill(9))
def frag(typ, n):
    for i in range(n):
        if typ == "Person":
            h.load("kg", json.dumps({"type": "Person", "data": {"name": f"p{time.time_ns()}", "bio": "b"}}))
        else:
            h.load("kg", json.dumps({"type": "Company", "data": {"name": f"c{time.time_ns()}"}}))
frag("Person", 3); frag("Company", 3)
# run 1: a 1 ms gate hold stops after the first table and stores the position
a = og.ulid(); h.submit("kg", a, hold_ms=1); op1 = h.wait_terminal("kg", a)
rec = og.read_record(root)
print("run1", [(t["type_key"], t["outcome"], t.get("reason")) for t in op1["outcome"]["tables"]], "resume_at", op1["outcome"].get("resume_at"), "kind_state", rec.get("kind_state"))
frag("Company", 3); frag("Person", 3)
# run 2: full catalog from the stored position; its first rewrite is parked 1 s
arm(ctl, "optimize.before_compact", "hold")
b = og.ulid(); h.submit("kg", b)
print("run2 reached the parked rewrite:", wait_reached(ctl, "optimize.before_compact", 15))
time.sleep(1.0); (ctl / "optimize.before_compact.release").write_text("")
op2 = h.wait_terminal("kg", b)
rec = og.read_record(root)
print("run2 start_at", op2["outcome"].get("start_at"), "gate_hold_ms", op2["timings"].get("gate_hold_ms"))
print("run2", [(t["type_key"], t["outcome"], t.get("reason")) for t in op2["outcome"]["tables"]], "resume_at", op2["outcome"].get("resume_at"), "kind_state", rec.get("kind_state"))
c = og.ulid(); h.submit("kg", c); op3 = h.wait_terminal("kg", c)
print("run3 start_at", op3["outcome"].get("start_at"))
srv.kill(15)
