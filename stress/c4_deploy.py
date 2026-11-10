#!/usr/bin/env python3
"""Campaign 4: deployments while a recorded optimize runs.

Scenarios (fresh root each, seam-armed child server with the fault shim):

  affects_held      run parked at optimize.before_compact; a deployment that
                    changes kg's schema; the hold releases after a pause.
                    RFC 3.4: the deployment cancels the run before its drain;
                    the record's terminal reason names the deployment.
  unaffected_held   same, but the deployment changes only graph `other`:
                    the run is not cancelled and keeps going.
  race              the submission is still before the record gate (its
                    record reads are delayed by the shim) when the
                    deployment cancels live runs; it is admitted afterwards.
                    Expected by the RFC: cancelled (or refused) like any run
                    on an affected graph. Observed: see results.
  delete_publishing a run whose publication is in doubt (publish read-back
                    failed) is unresolved; a deployment deletes graph kg.
                    RFC 3.4: the deletion settles it (walk, then
                    graph_deleted), before `Deleted`.
  delete_running    a run parked at before_compact; a deployment deletes kg.

Usage: c4_deploy.py OUTDIR [scenario ...]
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import og  # noqa: E402
from c3_crash import Child, arm, wait_reached, seed, settle_lookup  # noqa: E402

GRAPH = "kg"
STRESS = Path(__file__).resolve().parent
SHIM = "/tmp/faultfs.so"


TLS_DIR = Path("/tmp/tls")


def operator_config(path: Path, port: int):
    """A server target through the TLS proxy on port + 1000."""
    path.write_text(
        "version: 1\n"
        "targets:\n"
        "  srv:\n"
        "    kind: server\n"
        f"    endpoint: https://127.0.0.1:{port + 1000}\n"
        "    default_graph: kg\n"
        "    auth: {provider: token, source: {env: OG_STRESS_TOKEN}}\n"
        "    transport:\n"
        "      tls:\n"
        f"        ca: {{mode: replace, file: '{TLS_DIR / 'ca.pem'}'}}\n"
    )


def start_proxy(port: int, ctl: Path):
    proc = subprocess.Popen([sys.executable, str(STRESS / "tlsproxy.py"), str(port + 1000), str(port), str(ctl),
                             str(TLS_DIR / "cert.pem"), str(TLS_DIR / "key.pem")],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    line = proc.stdout.readline()
    if not line.startswith("listening"):
        raise RuntimeError(f"proxy did not start: {line} {proc.stdout.read()[:2000]}")
    return proc


def deploy(cfg: Path, source: Path, log, timeout=600, wait="900"):
    env = og.env_base("/tmp/oghome")
    env["OG_STRESS_TOKEN"] = "op-secret"
    t0 = time.time()
    out = subprocess.run([og.OMNIGRAPH, "cluster", "apply", "--operator-config", str(cfg), "--target", "srv",
                          "--config", str(source), "--json", "--timeout", wait],
                         env=env, capture_output=True, text=True, timeout=timeout)
    took = time.time() - t0
    log("deploy rc", out.returncode, "took", round(took, 2), out.stdout[-600:].replace("\n", " "), out.stderr[-600:].replace("\n", " "))
    return out.returncode, took, out.stdout


def setup(out: Path, name: str, port: int, extra_env=None):
    d = out / name
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    log = og.Log(str(d / "log.txt"))
    root = str(d / "root")
    os.makedirs(root)
    og.apply_cluster(root, config=STRESS / "fixture")
    ctl = d / "ctl"
    ctl.mkdir()
    env = {"OMNIGRAPH_OPTIMIZE_GATE_HOLD_SECONDS": "60", "OMNIGRAPH_SHUTDOWN_GRACE_SECONDS": "5",
           "LD_PRELOAD": SHIM, "FAULTFS_DIR": str(ctl)}
    env.update(extra_env or {})
    srv = Child(root, port, str(d / "child.log"), ctl, env).start()
    cfg = d / "operator.yaml"
    operator_config(cfg, port)
    srv.proxy = start_proxy(port, d / "proxy")
    acked = seed(srv.http)
    return d, log, root, ctl, srv, cfg, acked


def held_scenario(out, name, port, source, expect_cancel):
    d, log, root, ctl, srv, cfg, acked = setup(out, name, port)
    http = srv.http
    arm(ctl, "optimize.before_compact", "hold")
    oid = og.ulid()
    s, b, _ = http.submit(GRAPH, oid)
    assert wait_reached(ctl, "optimize.before_compact"), "never reached the seam"
    res = {"scenario": name, "submit": s}
    holder = threading.Timer(4.0, lambda: (ctl / "optimize.before_compact.release").write_text(""))
    holder.start()
    rc, took, stdout = deploy(cfg, source, log)
    res["deploy_rc"], res["deploy_s"] = rc, round(took, 2)
    op = http.wait_terminal(GRAPH, oid, 120)
    res["final"], res["reason"] = op["state"], op.get("reason")
    res["tables"] = [(t["type_key"], t["outcome"], t.get("reason")) for t in op["outcome"].get("tables", [])]
    res["expect_cancel"] = expect_cancel
    res["ok"] = (op["state"] == "cancelled" and "deployment" in (op.get("reason") or "")) if expect_cancel else op["state"] == "succeeded"
    srv.kill(signal.SIGTERM)
    srv.proxy.kill()
    log("RESULT", json.dumps(res, default=str))
    return res


def race_scenario(out, name, port):
    d, log, root, ctl, srv, cfg, acked = setup(out, name, port)
    http = srv.http
    arm(ctl, "optimize.before_compact", "hold")
    # every read of the record now takes 1.5 s, so the submission's peek and
    # its read under the gate straddle the deployment's cancel step
    (ctl / "open_delay_ms").write_text("1500\n")
    oid = og.ulid()
    result = {}

    def submit():
        result["submit"] = http.submit(GRAPH, oid)[:2]
    t = threading.Thread(target=submit)
    t.start()
    time.sleep(0.3)
    res = {"scenario": name}
    dep = {}

    def run_deploy():
        dep["rc"], dep["took"], _ = deploy(cfg, STRESS / "fixture_v2", log)
    td = threading.Thread(target=run_deploy)
    td.start()
    reached = wait_reached(ctl, "optimize.before_compact", 60)
    (ctl / "open_delay_ms").unlink()
    res["reached_seam"] = reached
    time.sleep(4.0)
    (ctl / "optimize.before_compact.release").write_text("")
    t.join(120)
    td.join(900)
    res["submit"] = result.get("submit", [None])[0]
    res["deploy_rc"], res["deploy_s"] = dep.get("rc"), round(dep.get("took", -1), 2)
    s, b, _ = http.lookup(GRAPH, oid)
    op = b.get("operation") if isinstance(b, dict) else None
    if op and op["state"] not in og.TERMINAL:
        op = http.wait_terminal(GRAPH, oid, 120)
    res["final"] = op and op["state"]
    res["reason"] = op and op.get("reason")
    res["tables"] = op and [(x["type_key"], x["outcome"], x.get("reason")) for x in op["outcome"].get("tables", [])]
    res["ok"] = bool(op) and op["state"] == "cancelled" and "deployment" in (op.get("reason") or "")
    srv.kill(signal.SIGTERM)
    srv.proxy.kill()
    log("RESULT", json.dumps(res, default=str))
    return res


def delete_publishing_scenario(out, name, port, kill_and_resume=False):
    d, log, root, ctl, srv, cfg, acked = setup(out, name, port)
    http = srv.http
    # an in-doubt publication: the manifest lands, its acknowledgement and the read-back fail
    arm(ctl, "publish.read_back", "fail_always")
    arm(ctl, "publish.pre_merge", "fail_once")
    oid = og.ulid()
    http.submit(GRAPH, oid)
    time.sleep(3)
    s, b, _ = http.lookup(GRAPH, oid)
    op = b.get("operation")
    res = {"scenario": name, "state_before_delete": op and op["state"], "in_progress": b.get("in_progress")}
    arm(ctl, "publish.read_back", "clear")
    rec = og.read_record(root)
    res["unresolved_before"] = [o["operation_id"] for o in rec.get("unresolved", [])]
    rc, took, stdout = deploy(cfg, STRESS / "fixture_nokg", log)
    res["deploy_rc"], res["deploy_s"] = rc, round(took, 2)
    rec = og.read_record(root)
    found = next((o for o in og.record_ops(rec) if o["operation_id"] == oid), None)
    res["record_after_delete"] = found and (found["state"], found.get("reason"), found.get("published_commit"))
    res["kind_state"] = rec.get("kind_state")
    res["unresolved_after"] = len(rec.get("unresolved", []))
    # the run's commit landed (post-merge ack failed): the walk should settle it succeeded
    res["ok"] = bool(found) and found["state"] == "unsettled" and found.get("reason") == "graph_deleted"
    s, b, _ = http.lookup(GRAPH, oid)
    res["lookup_after_delete"] = (s, (b or {}).get("code") if isinstance(b, dict) else None)
    srv.kill(signal.SIGTERM)
    srv.proxy.kill()
    log("RESULT", json.dumps(res, default=str))
    return res


def delete_running_scenario(out, name, port):
    d, log, root, ctl, srv, cfg, acked = setup(out, name, port)
    http = srv.http
    arm(ctl, "optimize.before_compact", "hold")
    oid = og.ulid()
    http.submit(GRAPH, oid)
    assert wait_reached(ctl, "optimize.before_compact")
    threading.Timer(4.0, lambda: (ctl / "optimize.before_compact.release").write_text("")).start()
    rc, took, stdout = deploy(cfg, STRESS / "fixture_nokg", log)
    rec = og.read_record(root)
    found = next((o for o in og.record_ops(rec) if o["operation_id"] == oid), None)
    res = {"scenario": name, "deploy_rc": rc, "deploy_s": round(took, 2),
           "record": found and (found["state"], found.get("reason")),
           "graph_root_exists": (Path(root) / "graphs" / "kg.omni").exists()}
    res["ok"] = bool(found) and found["state"] in ("cancelled", "interrupted", "unsettled")
    srv.kill(signal.SIGTERM)
    srv.proxy.kill()
    log("RESULT", json.dumps(res, default=str))
    return res


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    names = sys.argv[2:] or ["affects_held", "unaffected_held", "race", "delete_publishing", "delete_running"]
    port = 14400
    results = []
    for name in names:
        port += 1
        try:
            if name == "affects_held":
                results.append(held_scenario(out, name, port, STRESS / "fixture_v2", True))
            elif name == "unaffected_held":
                results.append(held_scenario(out, name, port, STRESS / "fixture_other2", False))
            elif name == "race":
                results.append(race_scenario(out, name, port))
            elif name == "delete_publishing":
                results.append(delete_publishing_scenario(out, name, port))
            elif name == "delete_running":
                results.append(delete_running_scenario(out, name, port))
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            results.append({"scenario": name, "error": repr(e)[:800]})
    (out / "results.json").write_text(json.dumps(results, indent=1, default=str))
    for r in results:
        print(json.dumps(r, default=str)[:1500])


if __name__ == "__main__":
    main()
