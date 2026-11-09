#!/usr/bin/env python3
"""Quick served checks of notes N1, N2, N6, N9, N11 and N13 (campaign v2).

N1  an expired id (older than the eviction mark) with an over-ceiling hold:
    400 (ceiling first) or 409 operation_id_expired (mark first).
N2  a published run's terminal record keeps the nonce; the server log names
    the nonce and the commit.
N6  `tables: []` and duplicate table names: refused at admission or accepted
    and failed.
N9  an id 50 s in the future is accepted; one 120 s ahead is refused.
N11 a historical query at a commit whose versions cleanup reclaimed.
N13 an export while a recorded run holds the export exclusion (the run is
    parked at a seam in the seam-armed served child).
Usage: c_nitems.py OUTDIR
"""
import json, os, re, shutil, signal, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import og
from c3_crash import Child, arm, wait_reached

out = Path(sys.argv[1]); shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
res = {}
root = og.make_root(out, "n"); og.apply_cluster(root)
srv = og.Server(root, 13997, str(out / "server.log"), {"OMNIGRAPH_OPTIMIZE_GATE_HOLD_SECONDS": "5", "OMNIGRAPH_OPTIMIZE_MAX_GATE_HOLD_SECONDS": "10"}).start()
h = srv.http
for i in range(3):
    h.load("kg", "\n".join(json.dumps({"type": "Person", "data": {"name": f"n{i}p{k}", "bio": "x"}}) for k in range(10)))
# N2
oid = og.ulid(); h.submit("kg", oid); op = h.wait_terminal("kg", oid)
logtext = re.sub(r"\x1b\[[0-9;]*m", "", open(out / "server.log").read())
res["N2"] = {"state": op["state"], "published_commit": op.get("published_commit"), "nonce": op.get("nonce"),
             "log_names_nonce": bool(op.get("published_commit")) and op["published_commit"].rsplit(".", 1)[-1] in logtext,
             "log_names_commit": bool(op.get("published_commit")) and op["published_commit"] in logtext}
# N6
for name, tables in (("empty", []), ("duplicate", ["node:Person", "node:Person"])):
    oid = og.ulid(); s, b, _ = h.submit("kg", oid, tables=tables)
    st = h.wait_terminal("kg", oid)["state"] if s in (200, 202) else None
    res[f"N6_{name}"] = {"status": s, "final": st}
# N9
now = int(time.time() * 1000)
fut = og.ulid(now + 50_000)
s50, b50, _ = h.submit("kg", fut)
s120, b120, _ = h.submit("kg", og.ulid(now + 120_000))
res["N9"] = {"plus_50s": s50, "plus_120s": (s120, (b120 or {}).get("code"))}
if s50 in (200, 202):
    h.wait_terminal("kg", fut)
refused, refused_first = {}, [None, None]
# N1: evict enough to set a mark, then an old id with an over-ceiling hold
for _ in range(34):
    while True:
        i = og.ulid()
        st, bd, _ = h.submit("kg", i, tables=["node:Company"])
        if st in (200, 202):
            break
        refused[(st, (bd or {}).get("code"))] = refused.get((st, (bd or {}).get("code")), 0) + 1
        if refused_first[0] is None:
            refused_first[0] = time.time()
        refused_first[1] = time.time()
        time.sleep(0.2)
    h.wait_terminal("kg", i)
rec = og.read_record(root)
mark = rec.get("eviction_mark_ms")
old = og.ulid((mark or now) - 60_000)
s1, b1, _ = h.submit("kg", old, hold_ms=999_999)
s2, b2, _ = h.submit("kg", old)
res["N9"]["honest_ids_refused_after_future_eviction"] = {str(k): v for k, v in refused.items()}
res["N9"]["refusal_window_s"] = round(refused_first[1] - refused_first[0], 1) if refused_first[0] else 0
res["N1"] = {"mark": mark, "expired_over_ceiling": (s1, (b1 or {}).get("code")), "expired_plain": (s2, (b2 or {}).get("code"))}
# N11
cs = h.call("GET", "/graphs/kg/commits?branch=main&limit=1000", token="rd-secret")[1]["commits"]
oldest = cs[-2]["graph_commit_id"] if len(cs) > 1 else cs[-1]["graph_commit_id"]
for i in range(3):
    h.load("kg", json.dumps({"type": "Person", "data": {"name": f"late{i}", "bio": "x"}}))
srv.kill(signal.SIGTERM); og.unlock(root)
cl = subprocess.run([og.OMNIGRAPH, "--target", root, "--graph", "kg", "cleanup", "--keep", "1", "--yes", "--json"],
                    env=og.env_base("/tmp/oghome"), capture_output=True, text=True)
og.unlock(root)
srv = og.Server(root, 13998, str(out / "server2.log")).start()
h = srv.http
s, b, _ = h.query("kg", "query c() { match { $p: Person } return { count($p) as n } }", snapshot=oldest)
res["N11"] = {"cleanup_rc": cl.returncode, "historical_status": s, "code": (b or {}).get("code"),
              "error": ((b or {}).get("error") or "")[:160]}
srv.kill(signal.SIGTERM); og.unlock(root)
# N13
ctl = out / "ctl"; ctl.mkdir()
child = Child(root, 13999, str(out / "child.log"), ctl, {}).start(timeout=90)
h = child.http
for i in range(2):
    h.load("kg", "\n".join(json.dumps({"type": "Person", "data": {"name": f"e{i}p{k}", "bio": "x"}}) for k in range(10)))
arm(ctl, "optimize.before_compact", "hold", 1)
oid = og.ulid(); h.submit("kg", oid)
res_reached = wait_reached(ctl, "optimize.before_compact", 30)
s, b, _ = h.call("POST", "/graphs/kg/export", {"type_names": ["Person"]}, token="op-secret", timeout=30)
res["N13"] = {"seam_reached": res_reached, "export_status": s, "body": str(b)[:200]}
arm(ctl, "optimize.before_compact", "clear", 0)
h.wait_terminal("kg", oid)
s2, b2, _ = h.call("POST", "/graphs/kg/export", {"type_names": ["Person"]}, token="op-secret", timeout=30)
res["N13"]["export_after"] = s2
child.kill(signal.SIGTERM)
(out / "result.json").write_text(json.dumps(res, indent=1, default=str))
print(json.dumps(res, indent=1, default=str))
