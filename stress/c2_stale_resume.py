#!/usr/bin/env python3
"""A late settlement of an older run rewrites a newer run's resume position.

Run A (full catalog, gate hold 0 so it stops after its first table) commits
but its answer is lost (publish.post_merge_pre_ack fails, read-back fails),
so it is `publishing`. Writes then put several commits on main above A's, and
the resolution walk reads one commit per access (walk limit 1). Run B (full
catalog, from the stored position, gate hold 0) runs and succeeds while A is
still walking, storing B's position. Then lookups walk A to `succeeded`.
Question: which position does the next full-catalog run start at? (RFC 1.3:
the position is the first table the latest bounded run deferred.)
Usage: c2_stale_resume.py OUTDIR
"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import og
from c3_crash import Child, arm

out = Path(sys.argv[1]); import shutil; shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
root = og.make_root(out, "stale"); og.apply_cluster(root)
ctl = out / "ctl"; ctl.mkdir()
srv = Child(root, 13890, str(out / "child.log"), ctl, {"OMNIGRAPH_OPERATION_RESOLUTION_WALK_LIMIT": "1"}).start()
h = srv.http
res = {}
try:
    def frag():
        for i in range(2):
            for t in ("Person", "Company"):
                row = {"type": t, "data": {"name": f"{t}{time.time_ns()}"}}
                if t == "Person":
                    row["data"]["bio"] = "b"
                h.load("kg", json.dumps(row))
    frag()
    arm(ctl, "publish.read_back", "fail_always"); arm(ctl, "publish.post_merge_pre_ack", "fail_once")
    a = og.ulid(); h.submit("kg", a, hold_ms=0)
    time.sleep(2)
    arm(ctl, "publish.read_back", "clear")
    rec = og.read_record(root)
    opa = next(o for o in og.record_ops(rec) if o["operation_id"] == a)
    res["A_recorded"] = (opa["state"], opa["outcome"].get("tables") and [(t["type_key"], t["outcome"]) for t in opa["outcome"]["tables"]])
    for i in range(6):  # commits above A's
        h.load("kg", json.dumps({"type": "Company", "data": {"name": f"above{i}"}}))
    frag()
    b = og.ulid(); h.submit("kg", b, hold_ms=0)
    opb = h.wait_terminal("kg", b)
    rec = og.read_record(root)
    res["B"] = (opb["state"], opb["outcome"].get("start_at"), opb["outcome"].get("resume_at"))
    res["position_after_B"] = rec.get("kind_state")
    states = []
    for _ in range(12):
        s, bb, _ = h.lookup("kg", a)
        states.append(bb["operation"]["state"])
        if bb["operation"]["state"] in og.TERMINAL:
            break
    res["A_walk"] = states
    rec = og.read_record(root)
    opa = next(o for o in og.record_ops(rec) if o["operation_id"] == a)
    res["A_final"] = (opa["state"], opa["outcome"].get("resume_at"))
    res["position_after_A_settles"] = rec.get("kind_state")
    c = og.ulid(); h.submit("kg", c, hold_ms=0)
    opc = h.wait_terminal("kg", c)
    res["next_run_start_at"] = opc["outcome"].get("start_at")
finally:
    srv.kill(9)
print(json.dumps(res, indent=1, default=str))
(out / "result.json").write_text(json.dumps(res, indent=1, default=str))
