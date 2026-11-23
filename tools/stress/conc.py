#!/usr/bin/env python3
"""Concurrent served writers: each worker inserts distinct items through the
stored mutation add_item; afterwards every acknowledged insert must be present
exactly once and nothing unacknowledged may appear unless its outcome was unknown."""
import json, os, subprocess, sys, threading, collections

OG = os.environ.get("OG", "omnigraph")
ENV = dict(os.environ, OMNIGRAPH_HOME="/tmp/og/home")
W, N = int(sys.argv[1]), int(sys.argv[2])
prefix = sys.argv[3]
branch = sys.argv[4] if len(sys.argv) > 4 else "main"
results = collections.defaultdict(list)
lock = threading.Lock()


def worker(w):
    tgt = "alice" if w % 2 == 0 else "bob"
    for i in range(N):
        slug = f"{prefix}-w{w}-{i}"
        args = [OG, "mutate", "add_item", "--target", tgt, "--params", json.dumps({"slug": slug, "n": i}), "--json"]
        if branch != "main":
            args += ["--branch", branch]
        p = subprocess.run(args, capture_output=True, text=True, env=dict(ENV, OMNIGRAPH_HOME=f"/tmp/og/h{w}"))
        try:
            out = json.loads(p.stdout)
        except Exception:
            out = {"raw": p.stdout[-300:], "err": p.stderr[-300:]}
        with lock:
            results[p.returncode].append((slug, out))


ts = [threading.Thread(target=worker, args=(w,)) for w in range(W)]
[t.start() for t in ts]
[t.join() for t in ts]
acked = {s for s, o in results[0] if o.get("commit")}
unknown = {s for rc, lst in results.items() if rc == 2 for s, _ in lst}
failed = {s for rc, lst in results.items() if rc not in (0, 2) for s, _ in lst}
p = subprocess.run([OG, "export", "--target", "alice", "--branch", branch, "--type", "Item"], capture_output=True, text=True, env=ENV)
rows = [json.loads(l) for l in p.stdout.splitlines() if l.strip()]
present = collections.Counter(r.get("id") or r["data"]["slug"] for r in rows)
mine = {k: v for k, v in present.items() if k.startswith(prefix + "-")}
print("rc counts:", {rc: len(v) for rc, v in results.items()})
codes = collections.Counter((rc, o.get("code")) for rc, lst in results.items() if rc != 0 for _, o in lst)
print("failure codes:", dict(codes))
print("acked", len(acked), "present", len(mine))
print("acked missing:", sorted(acked - set(mine))[:20])
print("present but failed (not unknown):", sorted((set(mine) & failed))[:20])
print("duplicates:", {k: v for k, v in mine.items() if v > 1})
commits = [o["commit"]["graph_commit_id"] for s, o in results[0] if o.get("commit")]
print("distinct commit ids:", len(set(commits)), "of", len(commits))
seen=set()
for rc, lst in results.items():
    for s,o in lst:
        if rc != 0 and (rc,o.get("code")) not in seen:
            seen.add((rc,o.get("code"))); print("example rc", rc, json.dumps(o)[:900])
