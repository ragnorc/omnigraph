#!/usr/bin/env python3
"""Merges into main beside unrelated branch creates and deletes, on one
server. One client writes a row to its own branch and merges it into main,
over and over; CH other clients only create and delete branches of their own
names, and nobody else writes main. Prints how many merges were refused and
with what. Run once with the candidate and once with the previous release to
see whether a branch create or delete refuses a concurrent merge.

Usage: OG=... OGS=... mcont.py <seed> <merges> [churners]"""
import json, os, sys, threading, time

seed, NM = int(sys.argv[1]), int(sys.argv[2])
CH = int(sys.argv[3]) if len(sys.argv) > 3 else 3
PACE = float(os.environ.get("PACE", "0"))
sys.argv = [sys.argv[0], str(seed), "0"]
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bcat as b  # noqa: E402

b.setup()
b.start()
stop = threading.Event()
res = {}
churn = {"ok": 0, "other": {}}


def churner(i):
    n = 0
    while not stop.is_set():
        n += 1
        name = f"x{i}/n{n}"
        st, out = b.http("POST", b.gpath("/branches"), {"name": name})
        k = ("create", st, b.code(out))
        if st == 200:
            st, out = b.http("DELETE", b.bpath(name))
            k = ("delete", st, b.code(out))
        if st == 200:
            churn["ok"] += 1
        else:
            churn["other"][repr(k)] = churn["other"].get(repr(k), 0) + 1
        if PACE:
            time.sleep(PACE)


ts = [threading.Thread(target=churner, args=(i,)) for i in range(CH)]
for t in ts:
    t.start()
t0 = time.time()
st, out = b.http("POST", b.gpath("/branches"), {"name": "m/src"})
assert st == 200, out
for k in range(NM):
    st, out = b.http("POST", b.gpath("/mutate"), {"query": b.INS, "params": {"slug": f"r{k}", "src": "m"}, "branch": "m/src"})
    if st != 200:
        res[("write", st, b.code(out))] = res.get(("write", st, b.code(out)), 0) + 1
        continue
    st, out = b.http("POST", b.gpath("/branches/merge"), {"source": "m/src", "target": "main"})
    key = ("merge", st, b.code(out), (out.get("error") or "")[:60] if st != 200 else "")
    res[key] = res.get(key, 0) + 1
stop.set()
for t in ts:
    t.join()
el = time.time() - t0
rows, _ = b.rows_of("main")
b.stop()
print(json.dumps({"seed": seed, "churners": CH, "pace": PACE, "seconds": round(el, 1), "churn_ok": churn["ok"], "churn_other": churn["other"],
                  "merges": {repr(k): v for k, v in res.items()}, "main_rows": len(rows or [])}))
