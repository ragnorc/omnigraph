"""Run one suite of queries for both editions over one frozen base.
Usage: python -I bench.py --suite S --base DIR --qdir e2=DIR --qdir gq3=DIR
         --bin e2=PATH --bin gq3=PATH --params P.json --reps R --out raw.jsonl [--only q1,q2] [--warmup 1]
Editions alternate their order per repetition. Rows are kept for repetition 0."""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import og
ap = argparse.ArgumentParser()
ap.add_argument("--suite"); ap.add_argument("--base"); ap.add_argument("--params")
ap.add_argument("--qdir", action="append"); ap.add_argument("--bin", action="append")
ap.add_argument("--reps", type=int, default=3); ap.add_argument("--out")
ap.add_argument("--only", default=""); ap.add_argument("--warmup", type=int, default=1)
ap.add_argument("--set", action="append", default=[]); ap.add_argument("--max-params", type=int, default=0)
ap.add_argument("--range", default="")
a = ap.parse_args()
qdir = dict(x.split("=", 1) for x in a.qdir); bins = dict(x.split("=", 1) for x in a.bin)
eds = list(bins)
P = json.load(open(a.params))
only = [q for q in a.only.split(",") if q] or list(P)
out = open(a.out, "a")
for q in only:
    plist = P[q][: a.max_params] if a.max_params else P[q]
    lo, hi = (int(x) for x in a.range.split(":")) if a.range else (0, len(plist))
    for ed in (eds if lo == 0 else []):  # warm the OS page cache for this query's files; discarded
        for _ in range(a.warmup):
            og.run_query(bins[ed], a.base, os.path.join(qdir[ed], q + ".gq"), q, plist[0], a.set)
    for i, p in enumerate(plist):
        if not lo <= i < hi: continue
        for rep in range(a.reps):
            order = eds if rep % 2 == 0 else eds[::-1]
            for ed in order:
                r = og.run_query(bins[ed], a.base, os.path.join(qdir[ed], q + ".gq"), q, p, a.set)
                doc = r["doc"]
                rec = {"suite": a.suite, "query": q, "param": i, "rep": rep, "edition": ed,
                       "wall_ms": r["wall_ms"], "exit": r["exit"], "stats": r["stats"],
                       "lance_calls": og.calls(r["stats"]), "clone_written": r["clone_written"],
                       "row_count": doc.get("row_count"), "digest": og.rows_digest(doc),
                       "digest_unordered": og.rows_digest(doc, ordered=False),
                       "error": None if r["exit"] == 0 else {k: doc.get(k) for k in ("error", "code", "stderr")}}
                if rep == 0:
                    rec["rows"] = doc.get("rows"); rec["columns"] = doc.get("columns"); rec["ranking"] = doc.get("ranking")
                out.write(json.dumps(rec) + "\n"); out.flush()
        print(q, i, "done", flush=True)
