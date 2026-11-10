#!/usr/bin/env python3
"""The four-unresolved cap, across graphs (RFC 2.4, 3.2).

Four optimize runs on `kg` end with their publication in doubt and main
unchanged (publish.pre_merge fails, read-back fails). Then:
  - a fifth submission on `kg` and one on `other` are refused 409; the RFC
    says the 409 reports `resolution: walking` per record with its cursor;
  - a write on `other` settles none of them;
  - a write on `kg` settles all four (`failed`, `not_committed`) and admission
    reopens.
Usage: c2_unresolved_cap.py OUTDIR
"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import og
from c3_crash import Child, arm

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
root = og.make_root(out, "cap"); og.apply_cluster(root)
ctl = out / "ctl"; ctl.mkdir(exist_ok=True)
srv = Child(root, 13995, str(out / "child.log"), ctl, {"OMNIGRAPH_OPERATION_RESOLUTION_WALK_LIMIT": "1"}).start()
h = srv.http
res = {}
try:
    ids = []
    for k in range(3):
        h.load("kg", json.dumps({"type": "Person", "data": {"name": f"s{k}", "bio": "b"}}))
        h.load("kg", json.dumps({"type": "Company", "data": {"name": f"c{k}"}}))
        h.load("kg", json.dumps({"type": "Doc", "data": {"slug": f"d{k}", "title": "t", "embedding": [0.1]*8}}))
    h.load("kg", json.dumps({"edge": "WorksAt", "from": "s0", "to": "c0"}))
    h.load("kg", json.dumps({"edge": "WorksAt", "from": "s1", "to": "c1"}))
    h.load("kg", json.dumps({"edge": "WorksAt", "from": "s2", "to": "c2"}))
    for k, table in enumerate(["node:Person", "node:Company", "node:Doc", "edge:WorksAt"]):
        arm(ctl, "publish.read_back", "fail_always"); arm(ctl, "publish.pre_merge", "fail_once")
        oid = og.ulid(); s, b, _ = h.submit("kg", oid, tables=[table]); ids.append(oid)
        time.sleep(2.5)
        arm(ctl, "publish.read_back", "clear")
        s, b, _ = h.lookup("kg", oid)
        print("run", k, s, (b.get("operation") or {}).get("state"), flush=True)
        if k < 3:
            # a write on main between doubts would settle; keep main unchanged
            pass
    rec = og.read_record(root)
    res["unresolved"] = len(rec.get("unresolved", []))
    lat = []
    for _ in range(20):
        t0 = time.time(); h.lookup("kg", ids[0]); lat.append(time.time() - t0)
    res["lookup_ms_with_4_unresolved"] = sorted(round(x * 1000) for x in lat)[len(lat) // 2]
    s5, b5, _ = h.submit("kg", og.ulid())
    so, bo, _ = h.submit("other", og.ulid())
    res["fifth_kg"] = (s5, b5.get("error") if isinstance(b5, dict) else b5)
    res["fifth_other"] = (so, bo.get("error") if isinstance(bo, dict) else bo)
    h.load("other", json.dumps({"type": "Item", "data": {"sku": "o1"}}))
    s6, b6, _ = h.submit("other", og.ulid())
    res["after_other_write"] = (s6, b6.get("error") if isinstance(b6, dict) else None)
    res["states_after_other_write"] = [h.lookup("kg", i)[1]["operation"]["state"] for i in ids]
    h.load("kg", json.dumps({"type": "Company", "data": {"name": "settle"}}))
    states = []
    for _ in range(3):
        states = [(h.lookup("kg", i)[1]["operation"]["state"], h.lookup("kg", i)[1]["operation"].get("reason")) for i in ids]
        if all(s in og.TERMINAL for s, _ in states):
            break
    res["states_after_kg_write"] = states
    s7, b7, _ = h.submit("kg", og.ulid())
    res["admission_after_settlement"] = s7
    lat = []
    for _ in range(20):
        t0 = time.time(); h.lookup("kg", ids[0]); lat.append(time.time() - t0)
    res["lookup_ms_with_0_unresolved"] = sorted(round(x * 1000) for x in lat)[len(lat) // 2]
finally:
    srv.kill(9)
print(json.dumps(res, indent=1, default=str))
(out / "result.json").write_text(json.dumps(res, indent=1, default=str))
