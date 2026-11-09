#!/usr/bin/env python3
"""Maintenance policy across entry points after the dual-action grace ended.

Served: `optimize` (the only served maintenance) for every actor of
`fixture_policy` (operator: maintain+change+reclaim; maintainer: maintain;
branchmaint: maintain on unprotected branches only, `main` protected;
writer: change; reclaimer: reclaim; reader: read). A refusal must be 403
before acceptance: no record entry for the id, main's head and commit count
unchanged. Denied submissions are also hammered beside live writers, and the
record and history are checked for any trace of them.
Direct CLI on the stopped cluster root, with `--as`: optimize, index build,
the full-text rebuild, cleanup and the repair report, for writer (change
only), reclaimer and maintainer; docs say direct maintenance loads no policy,
so this records what each command does and whether a refused one moved main.
Usage: c10_policy.py OUTDIR
"""
import json, os, shutil, signal, subprocess, sys, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import og, invariants

out = Path(sys.argv[1]); shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
log = og.Log(str(out / "log.txt"))
FIX = Path(__file__).resolve().parent / "fixture_policy"
root = og.make_root(out, "pol"); og.apply_cluster(root, config=FIX)
TOKENS = {"operator": "op-secret", "writer": "wr-secret", "reader": "rd-secret", "maintainer": "mt-secret",
          "branchmaint": "bm-secret", "reclaimer": "rc-secret"}
env = {"OMNIGRAPH_SERVER_BEARER_TOKENS_JSON": json.dumps(TOKENS)}
port = int(os.environ.get("STRESS_PORT", "13995"))
srv = og.Server(root, port, str(out / "server.log"), env).start(timeout=90)
h = srv.http
res, violations = {"served": {}, "direct": {}}, []


def violate(msg):
    violations.append(msg)
    log("VIOLATION", msg)


def head():
    cs = invariants.main_history(h, "kg")
    return (cs[0]["graph_commit_id"] if cs else None, len(cs))


for i in range(3):
    h.load("kg", "\n".join(json.dumps({"type": "Person", "data": {"name": f"s{i}p{k}", "bio": "x"}}) for k in range(20)))
expect = {"operator": True, "maintainer": True, "branchmaint": False, "writer": False, "reclaimer": False, "reader": False}
for actor, allowed in expect.items():
    before = head()
    oid = og.ulid()
    s, b, _ = h.submit("kg", oid, token=TOKENS[actor])
    entry = {"status": s, "code": (b or {}).get("code"), "error": ((b or {}).get("error") or "")[:160]}
    if allowed:
        if s not in (200, 202):
            violate(f"{actor}: allowed optimize answered {s} {b}")
        else:
            op = h.wait_terminal("kg", oid)
            entry["state"] = op["state"]
            entry["principal"] = op.get("principal")
    else:
        rec = og.read_record(root)
        if s != 403:
            violate(f"{actor}: refused optimize answered {s} {b}")
        if any(o["operation_id"] == oid for o in og.record_ops(rec)):
            violate(f"{actor}: refused id {oid} is in the record")
        if head() != before:
            violate(f"{actor}: refused optimize moved main {before} -> {head()}")
        ls, lb, _ = h.lookup("kg", oid, token=TOKENS[actor])
        entry["lookup"] = (ls, (lb or {}).get("code") or ((lb or {}).get("operation") or {}).get("state"))
    res["served"][actor] = entry
    log("served", actor, entry)

# denied submissions beside live writers
stop = threading.Event()
acked, denied_ids, statuses = set(), [], {}


def writer(w):
    i = 0
    while not stop.is_set():
        i += 1
        names = [f"w{w}i{i}k{k}" for k in range(5)]
        s, _, _ = h.load("kg", "\n".join(json.dumps({"type": "Person", "data": {"name": n, "bio": "x"}}) for n in names))
        if s == 200:
            acked.update(names)


def denier(actor):
    while not stop.is_set():
        oid = og.ulid()
        s, _, _ = h.submit("kg", oid, token=TOKENS[actor])
        statuses[s] = statuses.get(s, 0) + 1
        denied_ids.append(oid)


before_n = head()[1]
ths = [threading.Thread(target=writer, args=(w,), daemon=True) for w in range(2)]
ths += [threading.Thread(target=denier, args=(a,), daemon=True) for a in ("writer", "reclaimer", "branchmaint")]
for t in ths:
    t.start()
time.sleep(float(os.environ.get("STRESS_SECONDS", "60")))
stop.set()
for t in ths:
    t.join(30)
rec = og.read_record(root)
ids = {o["operation_id"] for o in og.record_ops(rec)}
leaked = [i for i in denied_ids if i in ids]
history = invariants.main_history(h, "kg")
optimize_commits = [c for c in history[: len(history) - before_n] if (c.get("actor_id") or c.get("actor")) not in ("writer", None)]
missing = acked - og.keys_of(h, "kg", "Person", "name")
res["served_under_load"] = {"denied_submissions": len(denied_ids), "statuses": statuses, "leaked_into_record": len(leaked),
                            "new_commits": len(history) - before_n, "commits_by_non_writer": len(optimize_commits),
                            "acked": len(acked), "acked_missing": len(missing)}
if leaked:
    violate(f"{len(leaked)} denied ids in the record")
if set(statuses) - {403}:
    violate(f"denied submissions answered {statuses}")
if optimize_commits:
    violate(f"commits by another actor while only denied optimizes ran: {optimize_commits[:2]}")
if missing:
    violate(f"{len(missing)} acknowledged rows missing")
log("served under load", res["served_under_load"])
srv.kill(signal.SIGTERM)
og.unlock(root)

# direct CLI on the stopped root
cmds = {
    "optimize": ["optimize"],
    "index_build": ["index", "build"],
    "fts_rebuild": ["index", "rebuild", "--kind", "full-text"],
    "cleanup": ["cleanup", "--keep", "5", "--yes"],
    "repair": ["repair"],
}
cli_env = og.env_base("/tmp/oghome")
for actor in ("writer", "reclaimer", "maintainer", "nobody"):
    for name, args in cmds.items():
        before = None
        t0 = time.time()
        p = subprocess.run([og.OMNIGRAPH, "--target", root, "--graph", "kg", "--as", actor, *args, "--json"],
                           env=cli_env, capture_output=True, text=True, timeout=600)
        lock = og.unlock(root)
        text = (p.stdout + p.stderr).strip().replace("\n", " ")
        res["direct"][f"{actor}/{name}"] = {"rc": p.returncode, "out": text[:220], "unlocked": bool(lock),
                                            "secs": round(time.time() - t0, 1)}
        log("direct", actor, name, p.returncode, text[:220])
res["violations"] = violations
(out / "result.json").write_text(json.dumps(res, indent=1))
print(json.dumps({"served": res["served"], "under_load": res["served_under_load"], "violations": violations}, indent=1))
