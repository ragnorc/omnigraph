#!/usr/bin/env python3
"""Graph deletion with an active `publishing` record a killed process left
(RFC 3.4), served and resumed.

  D1  the run is SIGKILLed inside its publication (graph_publish.before_commit_append
      held), so the record holds it active and `publishing`; a fresh server is
      started and, before any lookup, a deployment deletes graph `kg`.
  D2  as D1, but the deletion fails right after `Started`
      (deployment.after_started); the server stops, the lock is forced, and an
      offline reconcile (`cluster apply --deployment-id ID --writers-stopped`)
      resumes it.
Expected: the record ends `unsettled`, reason `graph_deleted`, the unresolved
list holds nothing of `kg`, `kg`'s kind state is gone.
Usage: c4_delete_orphan.py OUTDIR [D1|D2 ...]
"""
import json, os, re, shutil, signal, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import og
from c3_crash import Child, arm, wait_reached
from c4_deploy import operator_config, start_proxy, deploy

STRESS = Path(__file__).resolve().parent


def orphan(d, port):
    root = og.make_root(d, d.name); og.apply_cluster(root, config=STRESS / "fixture")
    ctl = d / "ctl"; ctl.mkdir()
    srv = Child(root, port, str(d / "child1.log"), ctl).start()
    h = srv.http
    for i in range(3):
        h.load("kg", json.dumps({"type": "Person", "data": {"name": f"s{i}", "bio": "b"}}))
    arm(ctl, "graph_publish.before_commit_append", "hold")
    oid = og.ulid(); h.submit("kg", oid)
    assert wait_reached(ctl, "graph_publish.before_commit_append")
    srv.kill(signal.SIGKILL)
    og.unlock(root)
    rec = og.read_record(root)
    return root, ctl, oid, [(o["operation_id"], o["state"]) for o in rec.get("active", {}).values()]


def run(out, case, port):
    d = out / case; shutil.rmtree(d, ignore_errors=True); d.mkdir(parents=True)
    log = og.Log(str(d / "log.txt"))
    root, ctl, oid, active = orphan(d, port)
    res = {"case": case, "active_before": active}
    srv = Child(root, port, str(d / "child2.log"), ctl).start()
    srv.proxy = start_proxy(port, d / "proxy")
    cfg = d / "operator.yaml"; operator_config(cfg, port)
    try:
        if case == "D2":
            arm(ctl, "deployment.after_started", "fail_always")
        rc, took, stdout = deploy(cfg, STRESS / "fixture_nokg", log, wait="20" if case == "D2" else "900")
        res["deploy_rc"] = rc
        m = re.search(r"\b(\w{26}:\d+:\w{26})\b", stdout)
        dep_id = m.group(1) if m else None
        res["deployment_id"] = dep_id
        if case == "D2":
            rec = og.read_record(root)
            res["record_after_failed_deploy"] = [(o["operation_id"], o["state"], o.get("reason")) for o in og.record_ops(rec) if o["operation_id"] == oid]
            srv.kill(signal.SIGTERM)
            og.unlock(root)
            env = og.env_base("/tmp/oghome")
            p = subprocess.run([og.OMNIGRAPH, "cluster", "apply", "--target", root, "--deployment-id", dep_id,
                                "--writers-stopped", "--json"], env=env, capture_output=True, text=True)
            res["reconcile_rc"] = p.returncode
            res["reconcile_tail"] = (p.stdout + p.stderr)[-300:].replace("\n", " ")
    finally:
        srv.kill(signal.SIGTERM)
        srv.proxy.kill()
    rec = og.read_record(root)
    found = next((o for o in og.record_ops(rec) if o["operation_id"] == oid), None)
    res["record_final"] = found and (found["state"], found.get("reason"), found.get("published_commit"))
    res["unresolved_left"] = [o["operation_id"] for o in rec.get("unresolved", [])]
    res["active_left"] = [o["operation_id"] for o in rec.get("active", {}).values()]
    res["kind_state"] = rec.get("kind_state")
    res["kg_root_exists"] = (Path(root) / "graphs" / "kg.omni").exists()
    res["ok"] = bool(found) and found["state"] == "unsettled" and found.get("reason") == "graph_deleted" and not res["active_left"]
    log("RESULT", json.dumps(res, default=str))
    return res


if __name__ == "__main__":
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    port = 14600
    for case in sys.argv[2:] or ["D1", "D2"]:
        port += 1
        print(json.dumps(run(out, case, port), default=str)[:1500], flush=True)


def run_d3(out, port):
    """D3: the settlement write after the root deletion fails (fault shim), so
    the deployment stops with the root gone; an offline reconcile resumes it
    and must settle the record `graph_deleted` from root absence alone."""
    import threading
    d = out / "D3"; shutil.rmtree(d, ignore_errors=True); d.mkdir(parents=True)
    log = og.Log(str(d / "log.txt"))
    root, ctl, oid, active = orphan(d, port)
    res = {"case": "D3", "active_before": active}
    (ctl / "count").unlink(missing_ok=True)
    srv = Child(root, port, str(d / "child2.log"), ctl, {"LD_PRELOAD": "/tmp/faultfs.so", "FAULTFS_DIR": str(ctl)}).start()
    srv.proxy = start_proxy(port, d / "proxy")
    cfg = d / "operator.yaml"; operator_config(cfg, port)
    hits = lambda: len((ctl / "hits").read_text().splitlines()) if (ctl / "hits").exists() else 0
    base = hits()
    def arm2():
        while hits() < base + 1:
            time.sleep(0.001)
        (ctl / "count").write_text("1\n"); (ctl / "fail").write_text("")
    threading.Thread(target=arm2, daemon=True).start()
    try:
        rc, took, stdout = deploy(cfg, STRESS / "fixture_nokg", log, wait="20")
        res["deploy_rc"] = rc
        m = re.search(r"\b(\w{26}:\d+:\w{26})\b", stdout)
        dep_id = m.group(1) if m else None
        res["root_after_failed_deploy"] = (Path(root) / "graphs" / "kg.omni").exists()
        rec = og.read_record(root)
        res["record_after_failed_deploy"] = [(o["operation_id"], o["state"], o.get("reason")) for o in og.record_ops(rec) if o["operation_id"] == oid]
    finally:
        srv.kill(signal.SIGTERM)
        srv.proxy.kill()
    (ctl / "fail").unlink(missing_ok=True)
    og.unlock(root)
    env = og.env_base("/tmp/oghome")
    p = subprocess.run([og.OMNIGRAPH, "cluster", "apply", "--target", root, "--deployment-id", dep_id or "",
                        "--writers-stopped", "--as", "operator", "--json"], env=env, capture_output=True, text=True)
    res["reconcile_rc"] = p.returncode
    rec = og.read_record(root)
    found = next((o for o in og.record_ops(rec) if o["operation_id"] == oid), None)
    res["record_final"] = found and (found["state"], found.get("reason"))
    res["active_left"] = list(rec.get("active", {}))
    res["ok"] = bool(found) and found["state"] == "unsettled" and found.get("reason") == "graph_deleted"
    log("RESULT", json.dumps(res, default=str))
    return res
