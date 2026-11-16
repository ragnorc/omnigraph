#!/usr/bin/env python3
"""Writers on several branches, a merger folding them into main, readers
polling main. Invariants: main's count never decreases across reads; every
acked branch insert reaches main after a final merge round; no duplicates;
failures are typed."""
import json, os, subprocess, threading, time, collections, random

OG = os.environ.get("OG", "omnigraph")
NB, PER = 4, 25
tag = "bs%d" % int(time.time())
stop = False
lock = threading.Lock()
log = collections.defaultdict(list)


def og(args, home):
    p = subprocess.run([OG] + args, capture_output=True, text=True, env=dict(os.environ, OMNIGRAPH_HOME=home))
    try:
        out = json.loads(p.stdout)
    except Exception:
        out = {"raw": p.stdout[-200:], "stderr": p.stderr[-300:]}
    return p.returncode, out


for b in range(NB):
    rc, out = og(["branch", "create", f"{tag}-b{b}", "--from", "main", "--target", "alice", "--json"], "/tmp/og/h0")
    assert rc == 0, out

acked = set()


def writer(b):
    home = f"/tmp/og/h{b + 1}"
    for i in range(PER):
        slug = f"{tag}-b{b}-{i}"
        rc, out = og(["mutate", "add_item", "--target", "bob", "--branch", f"{tag}-b{b}", "--params",
                      json.dumps({"slug": slug, "n": i}), "--json"], home)
        with lock:
            log[("write", rc, out.get("code"))].append(slug)
            if rc == 0 and out.get("commit"):
                acked.add(slug)


def merger():
    home = "/tmp/og/h8"
    while not stop:
        b = random.randrange(NB)
        rc, out = og(["branch", "merge", f"{tag}-b{b}", "--into", "main", "--target", "alice", "--json"], home)
        with lock:
            log[("merge", rc, out.get("code"))].append(json.dumps(out)[:300] if rc else "")


counts = []


def reader(k):
    home = f"/tmp/og/h{10 + k}"
    last = -1
    while not stop:
        rc, out = og(["query", "count_items", "--target", "carol", "--json"], home)
        if rc == 0:
            c = out["rows"][0]["c"]
            with lock:
                counts.append((k, c))
                if c < last:
                    log[("DECREASE",)].append((last, c))
            last = c
        else:
            with lock:
                log[("read", rc, out.get("code"))].append(json.dumps(out)[:200])


ws = [threading.Thread(target=writer, args=(b,)) for b in range(NB)]
ms = [threading.Thread(target=merger) for _ in range(2)]
rs = [threading.Thread(target=reader, args=(k,)) for k in range(2)]
for t in ws + ms + rs:
    t.start()
for t in ws:
    t.join()
stop = True
for t in ms + rs:
    t.join()
# final merge round
for b in range(NB):
    for attempt in range(5):
        rc, out = og(["branch", "merge", f"{tag}-b{b}", "--into", "main", "--target", "alice", "--json"], "/tmp/og/h0")
        if rc == 0:
            break
        time.sleep(0.5)
    print("final merge", b, rc, json.dumps(out)[:200])
p = subprocess.run([OG, "export", "--target", "alice", "--branch", "main", "--type", "Item"], capture_output=True, text=True,
                   env=dict(os.environ, OMNIGRAPH_HOME="/tmp/og/h0"))
ids = collections.Counter(json.loads(l).get("id") for l in p.stdout.splitlines() if l.strip())
mine = {k: v for k, v in ids.items() if k and k.startswith(tag)}
for k, v in log.items():
    print(k, len(v), (v[0] if k[1:2] != (0,) and v else ""))
print("acked", len(acked), "on main", len(mine), "missing", sorted(acked - set(mine))[:10],
      "dups", {k: v for k, v in mine.items() if v > 1}, "reads", len(counts))
