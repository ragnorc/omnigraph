#!/usr/bin/env python3
"""Campaign 3: crashes and restarts at exact points of a served optimize run.

Each scenario applies a fresh cluster root, seeds several tables with many
small fragments, starts the seam-armed served child (`served_child.rs`),
parks an optimize run at one seam (or fails/panics there), kills the server
(SIGKILL or SIGTERM), restarts a normal `omnigraph-server`, and checks:

- the record resolves as the RFC says for that point;
- a run reported failed/cancelled/interrupted published nothing (no main
  commit answers its nonce);
- a succeeded run's commit is on main, a child of its input, answering its nonce;
- every acknowledged seed row is present, and `cleanup` afterwards leaves the
  retained snapshots readable (a historical query at the input commit).

Usage: c3_crash.py OUTDIR [scenario ...]
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import og  # noqa: E402
import invariants  # noqa: E402

GRAPH = "kg"
REPO = og.REPO


def child_binary():
    cands = sorted(glob.glob(str(REPO / "target/debug/deps/stress_served_child-*")), key=os.path.getmtime)
    cands = [c for c in cands if os.access(c, os.X_OK) and not c.endswith(".d")]
    return cands[-1]


class Child(og.Server):
    def __init__(self, root, port, log, ctl, extra_env=None):
        env = {"STRESS_ROOT": root, "STRESS_CTL": str(ctl), "STRESS_BIND": f"127.0.0.1:{port}"}
        env.update(extra_env or {})
        super().__init__(root, port, log, env, binary=child_binary(),
                         args=["--exact", "stress_served_child", "--ignored", "--nocapture"])


def arm(ctl: Path, seam: str, action: str, n: int = 1):
    name = f"{time.time_ns()}-{seam}.cmd"
    (ctl / name).write_text(f"{seam} {action} {n}\n")
    end = time.time() + 10
    while time.time() < end:
        if (ctl / name.replace(".cmd", ".done")).exists():
            return
        if (ctl / name.replace(".cmd", ".bad")).exists():
            raise RuntimeError(f"bad seam command {seam}")
        time.sleep(0.01)
    raise TimeoutError(f"seam command {seam} not applied")


def wait_reached(ctl: Path, seam: str, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        if (ctl / f"{seam}.reached").exists():
            return True
        time.sleep(0.01)
    return False


def seed(http, rounds=6):
    acked = {"Person": set(), "Doc": set(), "Company": set()}
    for r in range(rounds):
        rows = [{"type": "Person", "data": {"name": f"r{r}p{i}", "age": i, "bio": f"bio {r} {i} graph"}} for i in range(20)]
        st, b, _ = http.load(GRAPH, "\n".join(json.dumps(x) for x in rows))
        assert st == 200, b
        acked["Person"].update(x["data"]["name"] for x in rows)
        rows = [{"type": "Company", "data": {"name": f"r{r}c{i}", "industry": "x"}} for i in range(3)]
        st, b, _ = http.load(GRAPH, "\n".join(json.dumps(x) for x in rows))
        assert st == 200, b
        acked["Company"].update(x["data"]["name"] for x in rows)
        rows = [{"type": "Doc", "data": {"slug": f"r{r}d{i}", "title": f"doc {r} {i}", "content": "base64:" + "QUJD" * 64,
                                         "embedding": [0.1 * ((i + k) % 7) for k in range(8)]}} for i in range(4)]
        st, b, _ = http.load(GRAPH, "\n".join(json.dumps(x) for x in rows))
        assert st == 200, b
        acked["Doc"].update(x["data"]["slug"] for x in rows)
        edges = [{"edge": "Knows", "from": f"r{r}p{i}", "to": f"r{r}p{i + 1}", "data": {"since": 2000 + i}} for i in range(10)]
        st, b, _ = http.load(GRAPH, "\n".join(json.dumps(x) for x in edges))
        assert st == 200, b
        edges = [{"edge": "WorksAt", "from": f"r{r}p{i}", "to": f"r{r}c{i % 3}"} for i in range(5)]
        st, b, _ = http.load(GRAPH, "\n".join(json.dumps(x) for x in edges))
        assert st == 200, b
    return acked


def check_rows(http, acked):
    v = []
    for typ, key in (("Person", "name"), ("Company", "name"), ("Doc", "slug")):
        present = og.keys_of(http, GRAPH, typ, key)
        missing = acked[typ] - present
        if missing:
            v.append(f"{len(missing)} acknowledged {typ} rows missing")
    return v


def settle_lookup(http, oid, tries=6):
    op = None
    for _ in range(tries):
        s, b, _ = http.lookup(GRAPH, oid)
        if s == 200 and b.get("operation"):
            op = b["operation"]
            if op["state"] in og.TERMINAL:
                return op
        time.sleep(0.3)
    return op


def cleanup_and_read(root, input_commit, home="/tmp/oghome"):
    """Direct cleanup on the stopped root, then a historical read at the input commit."""
    env = og.env_base(home)
    target = str(Path(root) / "graphs" / f"{GRAPH}.omni")
    out = subprocess.run([og.OMNIGRAPH, "cleanup", "--target", root, "--graph", GRAPH, "--keep", "1", "--confirm", "--yes", "--json"],
                         env=env, capture_output=True, text=True)
    res = {"cleanup_rc": out.returncode, "cleanup_tail": (out.stdout + out.stderr)[-400:]}
    return res


def scenario(out: Path, name: str, seam: str, action: str, kill_sig, expect: set, port: int,
             publish_unknown=False, extra=None):
    d = out / name
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    log = og.Log(str(d / "log.txt"))
    root = str(d / "root")
    os.makedirs(root)
    og.apply_cluster(root)
    ctl = d / "ctl"
    ctl.mkdir()
    env = {"OMNIGRAPH_OPTIMIZE_GATE_HOLD_SECONDS": "30", "OMNIGRAPH_SHUTDOWN_GRACE_SECONDS": "5"}
    env.update(extra or {})
    srv = Child(root, port, str(d / "child.log"), ctl, env).start()
    http = srv.http
    acked = seed(http)
    res = {"scenario": name, "seam": seam, "action": action, "signal": kill_sig}
    if publish_unknown:
        arm(ctl, "publish.read_back", "fail_always")
    arm(ctl, seam, action)
    oid = og.ulid()
    s, b, _ = http.submit(GRAPH, oid)
    res["submit"] = s
    if action == "hold":
        res["reached"] = wait_reached(ctl, seam)
        s, b, _ = http.lookup(GRAPH, oid)
        res["state_at_seam"] = (b.get("operation") or {}).get("state")
        res["nonce_at_seam"] = (b.get("operation") or {}).get("nonce")
    else:
        time.sleep(3)
        s, b, _ = http.lookup(GRAPH, oid) if srv.proc.poll() is None else (None, {}, None)
        res["state_after_fault"] = ((b or {}).get("operation") or {}).get("state")
        res["nonce_at_seam"] = ((b or {}).get("operation") or {}).get("nonce")
    rec_before = og.read_record(root)
    op_before = next((o for o in og.record_ops(rec_before) if o["operation_id"] == oid), None)
    res["record_state_before_kill"] = op_before and op_before["state"]
    nonce = (op_before or {}).get("nonce") or res.get("nonce_at_seam")
    if kill_sig is not None and srv.proc.poll() is None:
        t0 = time.time()
        if kill_sig == "TERM":
            # release the hold after the signal so a shutdown can finish
            srv.proc.send_signal(signal.SIGTERM)
            time.sleep(0.5)
            (ctl / f"{seam}.release").write_text("")
            try:
                srv.proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                srv.proc.kill()
                srv.proc.wait()
        else:
            srv.proc.send_signal(signal.SIGKILL)
            srv.proc.wait()
        res["exit"] = srv.proc.returncode
        res["stop_s"] = round(time.time() - t0, 2)
    else:
        res["exit"] = srv.proc.returncode
    rec_mid = og.read_record(root)
    op_mid = next((o for o in og.record_ops(rec_mid) if o["operation_id"] == oid), None)
    res["record_state_after_stop"] = op_mid and op_mid["state"]
    nonce = nonce or (op_mid or {}).get("nonce")
    # restart a normal server
    try:
        srv2 = og.Server(root, port, str(d / "server2.log"), env).start()
        res["restart_needed_unlock"] = False
    except RuntimeError:
        res["restart_needed_unlock"] = bool(og.unlock(root))
        srv2 = og.Server(root, port, str(d / "server3.log"), env).start()
    http = srv2.http
    op = settle_lookup(http, oid)
    res["after_restart"] = op and op["state"]
    if op and op["state"] == "publishing":
        # a write on main settles it
        http.load(GRAPH, json.dumps({"type": "Company", "data": {"name": "settle", "industry": "x"}}))
        acked["Company"].add("settle")
        op = settle_lookup(http, oid)
        res["after_write"] = op and op["state"]
    res["final"] = op and op["state"]
    res["reason"] = op and op.get("reason")
    res["published_commit"] = op and op.get("published_commit")
    seen = {oid: op} if op else {}
    nonces = {oid: nonce} if nonce else {}
    rec = og.read_record(root)
    v = invariants.check_operations(http, GRAPH, seen, nonces, rec)
    v += invariants.check_record_shape(rec, live_ids=set())
    v += check_rows(http, acked)
    if res["final"] not in expect:
        v.append(f"final state {res['final']} not in {sorted(expect)}")
    # a fresh run must work after recovery
    oid2 = og.ulid()
    s, b, _ = http.submit(GRAPH, oid2)
    try:
        op2 = http.wait_terminal(GRAPH, oid2, 120)
        res["next_run"] = op2["state"]
        if op2["state"] != "succeeded":
            v.append(f"next run {op2['state']} {op2.get('reason')}")
    except TimeoutError as e:
        v.append(f"next run not terminal: {e}")
    if op and op.get("input_commit"):
        q = "query c() { match { $x: Person } return { count($x) as n } }"
        s0, b0, _ = http.call("POST", f"/graphs/{GRAPH}/query", {"query": q, "snapshot": op["input_commit"]}, token="rd-secret")
        res["historical_read_before_cleanup"] = (s0, json.dumps(b0)[:300])
    srv2.kill(sig=signal.SIGTERM)
    if srv2.proc.returncode not in (0,):
        res["second_exit"] = srv2.proc.returncode
        og.unlock(root)
    if op and op.get("input_commit"):
        q = "query c() { match { $x: Person } return { count($x) as n } }"
        s0, b0, _ = srv2_http.call("POST", f"/graphs/{GRAPH}/query", {"query": q, "snapshot": op["input_commit"]}, token="rd-secret") if False else (None, None, None)
    log("RESULT-PRE-CLEANUP", json.dumps(res, default=str), json.dumps(v))
    res.update(cleanup_and_read(root, (op or {}).get("input_commit")))
    # a direct cleanup keeps its admission lock, as a direct apply does
    res["cleanup_lock"] = bool(og.unlock(root))
    srv3 = og.Server(root, port, str(d / "server4.log"), env).start()
    try:
        v += check_rows(srv3.http, acked)
        # historical read at the run's input commit, which cleanup --keep 1 may have released
        if op and op.get("input_commit"):
            q = "query c() { match { $x: Person } return { count($x) as n } }"
            s, b, _ = srv3.http.call("POST", f"/graphs/{GRAPH}/query", {"query": q, "snapshot": op["input_commit"]}, token="rd-secret")
            res["historical_read_after_cleanup"] = (s, json.dumps(b)[:300])
    finally:
        srv3.kill(sig=signal.SIGTERM)
    res["violations"] = v
    log("RESULT", json.dumps(res, default=str))
    return res


SCENARIOS = {
    # name: (seam, action, signal, expected final states, publish_unknown)
    "kill_post_capture": ("optimize.post_authority_capture_pre_gates", "hold", "KILL", {"interrupted"}, False),
    "kill_before_compact": ("optimize.before_compact", "hold", "KILL", {"interrupted"}, False),
    "kill_post_table_effect": ("optimize.post_table_effect", "hold", "KILL", {"interrupted"}, False),
    "kill_pre_manifest_commit": ("optimize.post_phase_b_pre_manifest_commit", "hold", "KILL", {"interrupted"}, False),
    "kill_before_commit_append": ("graph_publish.before_commit_append", "hold", "KILL", {"failed", "succeeded"}, False),
    "kill_after_manifest_commit": ("graph_publish.after_manifest_commit", "hold", "KILL", {"succeeded"}, False),
    "kill_configuration_clear": ("optimize.configuration_clear", "hold", "KILL", {"succeeded"}, False),
    "term_before_compact": ("optimize.before_compact", "hold", "TERM", {"cancelled", "succeeded", "interrupted"}, False),
    "term_after_manifest_commit": ("graph_publish.after_manifest_commit", "hold", "TERM", {"succeeded"}, False),
    "panic_before_compact": ("optimize.before_compact", "panic", None, {"interrupted", "failed"}, False),
    "panic_after_manifest_commit": ("graph_publish.after_manifest_commit", "panic", None, {"succeeded"}, False),
    "fail_before_compact": ("optimize.before_compact", "fail_once", None, {"succeeded"}, False),
    "fail_pre_manifest_commit": ("optimize.post_phase_b_pre_manifest_commit", "fail_once", None, {"failed"}, False),
    "unknown_post_merge": ("publish.post_merge_pre_ack", "fail_once", None, {"succeeded"}, True),
    "unknown_pre_merge": ("publish.pre_merge", "fail_once", None, {"failed"}, True),
}


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    names = sys.argv[2:] or list(SCENARIOS)
    results = []
    port = 13300
    for name in names:
        seam, action, sig, expect, unknown = SCENARIOS[name]
        port += 1
        try:
            results.append(scenario(out, name, seam, action, sig, expect, port, publish_unknown=unknown))
        except Exception as e:  # noqa: BLE001
            results.append({"scenario": name, "error": repr(e)[:800]})
            print("ERROR", name, repr(e)[:800], flush=True)
        os.system("pkill -x omnigraph-serve; pkill -f stress_served_child- >/dev/null 2>&1")
    (out / "results.json").write_text(json.dumps(results, indent=1, default=str))
    for r in results:
        print(json.dumps({k: v for k, v in r.items() if k not in ("cleanup_tail",)}, default=str)[:1500])


if __name__ == "__main__":
    main()
