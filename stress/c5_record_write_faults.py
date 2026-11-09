#!/usr/bin/env python3
"""Campaign 5a: operation-record write faults on a local root (LD_PRELOAD shim).

The shim (`faultfs.c`) fails or delays the rename that lands
`__cluster/operations.json`. Scenarios, each on a fresh root:

  final_refused    the run's final (terminal) write fails once with EIO,
                   nothing applied; then the fault clears.
  final_sticky     the final write fails and the fault stays for a while;
                   lookups during the fault are recorded.
  accept_refused   the acceptance write fails once.
  running_refused  the `running` write fails once.
  publishing_refused the before-publish write fails once.

For every scenario it records: the status of a lookup during the fault, the
status of a lookup and a fresh submission after the fault cleared, and the
record's final state for the operation; it checks the RFC's §2.3 claims.

Usage: c5_record_write_faults.py OUTDIR [scenario ...]
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import og  # noqa: E402

SHIM = "/tmp/faultfs.so"
GRAPH = "kg"


def build_shim():
    src = Path(__file__).resolve().parent / "faultfs.c"
    if not Path(SHIM).exists() or Path(SHIM).stat().st_mtime < src.stat().st_mtime:
        os.system(f"cc -shared -fPIC -O2 -o {SHIM} {src} -ldl")


def hits(ctl: Path) -> int:
    p = ctl / "hits"
    return len(p.read_text().splitlines()) if p.exists() else 0


def seed(http):
    rows = [json.dumps({"type": "Person", "data": {"name": f"s{i}", "age": i, "bio": "seed bio"}}) for i in range(50)]
    for k in range(5):
        status, body, _ = http.load(GRAPH, "\n".join(rows[k * 10:(k + 1) * 10]))
        assert status == 200, body


def arm_at(ctl: Path, nth: int, mode: str, out: dict):
    """Arm the fault once the record has been renamed `nth - 1` times."""
    def watch():
        while hits(ctl) < nth - 1:
            time.sleep(0.0005)
        (ctl / "count").write_text("1\n")
        (ctl / mode).write_text("")
        out["armed_at"] = time.time()
    t = threading.Thread(target=watch, daemon=True)
    t.start()
    return t


PORT = [18210]


def run(out: Path, name: str, nth: int, mode: str = "fail", sticky_s: float = 0.0, noop: bool = False):
    PORT[0] += 1
    port = PORT[0]
    d = out / name
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    log = og.Log(str(d / "log.txt"))
    root = str(d / "root")
    os.makedirs(root)
    og.apply_cluster(root)
    ctl = d / "ctl"
    ctl.mkdir()
    env = {"LD_PRELOAD": SHIM, "FAULTFS_DIR": str(ctl), "OMNIGRAPH_OPTIMIZE_GATE_HOLD_SECONDS": "5"}
    srv = og.Server(root, port, str(d / "server.log"), env).start()
    http = srv.http
    seed(http)
    warm = og.ulid()
    http.submit(GRAPH, warm)
    http.wait_terminal(GRAPH, warm, 60)
    if not noop:
        seed(http)  # fresh fragments for the run under test
    time.sleep(0.2)
    base_hits = hits(ctl)
    info = {}
    if sticky_s:
        (ctl / "count").write_text("1000000\n")
        def watch():
            while hits(ctl) < base_hits + nth - 1:
                time.sleep(0.0005)
            (ctl / mode).write_text("")
            info["armed_at"] = time.time()
        threading.Thread(target=watch, daemon=True).start()
    else:
        arm_at_thread = None
        def watch():
            while hits(ctl) < base_hits + nth - 1:
                time.sleep(0.0005)
            (ctl / "count").write_text("1\n")
            (ctl / mode).write_text("")
            info["armed_at"] = time.time()
        threading.Thread(target=watch, daemon=True).start()
    oid = og.ulid()
    status, body, _ = http.submit(GRAPH, oid)
    log("submit", status, json.dumps(body)[:400])
    res = {"scenario": name, "nth_write": nth, "mode": mode, "submit": status}
    time.sleep(3)
    during = []
    t_end = time.time() + max(sticky_s, 0.5)
    while time.time() < t_end:
        s, b, _ = http.lookup(GRAPH, oid)
        during.append((s, ((b or {}).get("operation") or {}).get("state") if isinstance(b, dict) else None,
                       (b or {}).get("error") if isinstance(b, dict) else None))
        time.sleep(0.5)
    res["lookups_during_fault"] = during[:3] + (["..."] if len(during) > 3 else []) + during[-1:]
    res["lookup_statuses_during_fault"] = sorted({s for s, _, _ in during})
    for f in ("fail", "fail_after", "count"):
        (ctl / f).unlink(missing_ok=True)
    time.sleep(0.5)
    s, b, _ = http.lookup(GRAPH, oid)
    res["lookup_after"] = (s, b.get("operation", {}).get("state") if isinstance(b, dict) and b.get("operation") else b)
    log("lookup after", s, json.dumps(b)[:600])
    oid2 = og.ulid()
    s2, b2, _ = http.submit(GRAPH, oid2)
    res["fresh_submit_after"] = (s2, (b2 or {}).get("error") if isinstance(b2, dict) else None)
    log("fresh submit after", s2, json.dumps(b2)[:600])
    if s2 in (200, 202):
        try:
            op2 = http.wait_terminal(GRAPH, oid2, 60)
            res["fresh_terminal"] = op2["state"]
        except TimeoutError as e:
            res["fresh_terminal"] = str(e)[:300]
    rec = og.read_record(root)
    op = next((o for o in og.record_ops(rec) if o["operation_id"] == oid), None)
    res["record_state"] = op and op["state"]
    res["record_reason"] = op and op.get("reason")
    res["record_published"] = op and op.get("published_commit")
    commits = [c for c in __import__("invariants").main_history(http, GRAPH) if c.get("actor_id") == "operator"]
    res["operator_commits_on_main"] = len(commits)
    # restart and look again
    res["sigterm_exit"] = srv.kill(sig=15)
    try:
        srv2 = og.Server(root, port, str(d / "server2.log"), {"OMNIGRAPH_OPTIMIZE_GATE_HOLD_SECONDS": "5"}).start()
        res["restart_needed_unlock"] = False
    except RuntimeError:
        res["restart_needed_unlock"] = og.unlock(root)
        srv2 = og.Server(root, port, str(d / "server3.log"), {"OMNIGRAPH_OPTIMIZE_GATE_HOLD_SECONDS": "5"}).start()
    s3, b3, _ = srv2.http.lookup(GRAPH, oid)
    res["lookup_after_restart"] = (s3, b3.get("operation", {}).get("state") if isinstance(b3, dict) and b3.get("operation") else b3)
    oid3 = og.ulid()
    s4, b4, _ = srv2.http.submit(GRAPH, oid3)
    res["submit_after_restart"] = s4
    srv2.kill(sig=15)
    log("RESULT", json.dumps(res, default=str))
    return res


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    build_shim()
    wanted = sys.argv[2:] or ["final_refused", "final_sticky", "accept_refused", "running_refused", "publishing_refused"]
    # record renames of one run: 1 acceptance, 2 running, 3 publishing, 4 terminal
    table = {
        "accept_refused": (1, "fail", 0),
        "running_refused": (2, "fail", 0),
        "publishing_refused": (3, "fail", 0),
        "final_refused": (4, "fail", 0),
        "final_sticky": (4, "fail", 6),
        "final_lost_answer": (4, "fail_after", 0),
        "publishing_lost_answer": (3, "fail_after", 0),
        "accept_lost_answer": (1, "fail_after", 0),
        # nothing to publish: accept, running, final
        "noop_final_refused": (3, "fail", 0, True),
    }
    results = [run(out, name, *table[name]) for name in wanted]
    (out / "results.json").write_text(json.dumps(results, indent=1, default=str))
    for r in results:
        print(json.dumps(r, default=str))


if __name__ == "__main__":
    main()
