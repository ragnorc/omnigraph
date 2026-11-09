#!/usr/bin/env python3
"""Settling an in-doubt publication by moves of main that are merges.

The run's publication is in doubt with main unchanged (publish.pre_merge
fails before the manifest write, publish.read_back fails), so the record is
`publishing`. Then main moves only by merging a branch:

  ff        branch created at the input head, two loads on it, merged into
            main (a fast-forward when main has not moved)
  ff_deep   as ff, with 6 loads on the branch and walk limit 1
  three_way branch created at the input head, one load on it and one on
            main, then merged (a merge commit)

RFC 2.4: the record resolves `failed` (`not_committed`) once main's graph head
has moved. Usage: c3_settle_by_merge.py OUTDIR [BIN_DIR]
"""
import json, os, shutil, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import og
from c3_crash import arm

out = Path(sys.argv[1]); bindir = Path(sys.argv[2] if len(sys.argv) > 2 else str(og.BIN))
shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
results = []
port = 13950
for case in ["ff", "ff_deep", "three_way"]:
    port += 1
    d = out / case; d.mkdir()
    root = og.make_root(d, case); og.apply_cluster(root)
    ctl = d / "ctl"; ctl.mkdir()
    env = {"STRESS_ROOT": root, "STRESS_CTL": str(ctl), "STRESS_BIND": f"127.0.0.1:{port}",
           "OMNIGRAPH_OPERATION_RESOLUTION_WALK_LIMIT": "1" if case == "ff_deep" else "256"}
    child = bindir / "stress_served_child"
    if not child.exists():
        import glob
        child = Path(os.environ.get("OG_CHILD_BIN") or sorted(glob.glob(str(og.REPO / "target/debug/deps/stress_served_child-*")), key=os.path.getmtime)[-1])
    srv = og.Server(root, port, str(d / "child.log"), env, binary=str(child),
                    args=["--exact", "stress_served_child", "--ignored", "--nocapture"]).start()
    h = srv.http
    try:
        for i in range(3):
            h.load("kg", json.dumps({"type": "Person", "data": {"name": f"s{i}", "bio": "b"}}))
        arm(ctl, "publish.read_back", "fail_always"); arm(ctl, "publish.pre_merge", "fail_once")
        oid = og.ulid(); h.submit("kg", oid); time.sleep(3)
        s, b, _ = h.lookup("kg", oid)
        first = (b.get("operation") or {}).get("state")
        arm(ctl, "publish.read_back", "clear")
        st, bb, _ = h.branch_create("kg", "side")
        loads = 6 if case == "ff_deep" else 2
        for i in range(loads):
            h.load("kg", json.dumps({"type": "Person", "data": {"name": f"side{i}", "bio": "b"}}), branch="side")
        if case == "three_way":
            h.load("kg", json.dumps({"type": "Company", "data": {"name": "main-move"}}))
        ms, mb, _ = h.merge("kg", "side")
        commits = [(c["graph_commit_id"], c["graph_manifest_version"], c.get("parent_commit_id"), c.get("merged_parent_commit_id"))
                   for c in h.commits("kg")[1]["commits"]][:loads + 4]
        states = []
        for _ in range(12):
            s, b, _ = h.lookup("kg", oid)
            op = b.get("operation") or {}
            states.append(op.get("state"))
            if op.get("state") in og.TERMINAL:
                break
            time.sleep(0.3)
        res = {"case": case, "before_merge": first, "merge_status": ms,
               "merge_kind": (mb or {}).get("outcome") if isinstance(mb, dict) else None,
               "lookups": states, "final": op.get("state"), "reason": op.get("reason"),
               "resolution": op.get("resolution"), "input_commit": op.get("input_commit"),
               "main_commits(newest first: id, version, parent, merged_parent)": commits}
        results.append(res)
        print(json.dumps(res, default=str)[:2500], flush=True)
    finally:
        srv.kill(9)
(out / "results.json").write_text(json.dumps(results, indent=1, default=str))
