#!/usr/bin/env python3
"""After a stress run: `cleanup --keep K` leaves every retained snapshot readable.

Stops nothing itself: point it at a stopped cluster root. Runs a direct
`cleanup --keep K --confirm`, force-unlocks, starts a server, and queries a
count on main at each of main's newest K+5 commits and on every live branch.
Retained (newest K) must answer 200; older ones may be reclaimed.
Usage: c8_cleanup_retained.py ROOT OUTDIR [K]
"""
import json, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import og, invariants

root, out = sys.argv[1], Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
k = int(sys.argv[3]) if len(sys.argv) > 3 else 5
og.unlock(root)
env = og.env_base("/tmp/oghome")
p = subprocess.run([og.OMNIGRAPH, "cleanup", "--target", root, "--graph", "kg", "--keep", str(k), "--confirm", "--yes", "--json"],
                   env=env, capture_output=True, text=True)
res = {"cleanup_rc": p.returncode}
og.unlock(root)
srv = og.Server(root, 13850, str(out / "server.log"), {}).start()
h = srv.http
commits = invariants.first_parent_chain(invariants.main_history(h, "kg"))
q = "query c() { match { $x: Person } return { count($x) as n } }"
reads = []
for i, c in enumerate(commits[: k + 5]):
    s, b, _ = h.call("POST", "/graphs/kg/query", {"query": q, "snapshot": c["graph_commit_id"]}, token="rd-secret")
    reads.append((i, s, (b or {}).get("rows", [{}])[0].get("n") if s == 200 else (b or {}).get("error", "")[:80]))
res["reads_newest_first"] = reads
res["retained_unreadable"] = [r for r in reads if r[0] < k and r[1] != 200]
s, b, _ = h.call("GET", "/graphs/kg/branches", token="rd-secret")
branches = [x if isinstance(x, str) else x.get("name") for x in (b or {}).get("branches", [])]
res["branches"] = {}
for br in branches:
    s, bb, _ = h.query("kg", q, branch=br)
    res["branches"][br] = s
srv.kill(15)
print(json.dumps(res, default=str, indent=1))
(out / "result.json").write_text(json.dumps(res, indent=1, default=str))
