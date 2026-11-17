"""Summarize raw.jsonl files: latency, memory, Lance calls and row parity per query.
Usage: python -I analyze.py raw.jsonl [...] > table.md"""
import json, sys, statistics as st
from collections import defaultdict
recs = [json.loads(l) for f in sys.argv[1:] for l in open(f)]
def pct(xs, p):
    xs = sorted(xs); 
    if not xs: return float("nan")
    k = (len(xs) - 1) * p; f = int(k); c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)
by = defaultdict(list)
for r in recs: by[(r["suite"], r["query"], r["edition"])].append(r)
first = {}
for r in recs:
    if r["rep"] == 0: first[(r["suite"], r["query"], r["param"], r["edition"])] = r
print("| suite | query | ed | n | err | wall p50 ms | wall p95 ms | engine p50 ms | peak RSS p50 MiB | Lance calls p50 | rows equal (ordered/unordered/differ) |")
print("|---|---|---|---|---|---|---|---|---|---|---|")
seen = []
for (s, q, e) in by:
    if (s, q) not in seen: seen.append((s, q))
for s, q in seen:
    params = sorted({p for (ss, qq, p, e) in first if ss == s and qq == q})
    eq = uq = df = 0
    for p in params:
        a, b = first.get((s, q, p, "e2")), first.get((s, q, p, "gq3"))
        if not a or not b: continue
        if a["digest"] == b["digest"] and a["digest"] is not None: eq += 1
        elif a["digest_unordered"] == b["digest_unordered"] and a["digest"] is not None: uq += 1
        else: df += 1
    for e in ("e2", "gq3"):
        rs = by.get((s, q, e), [])
        ok = [r for r in rs if r["exit"] == 0]
        w = [r["wall_ms"] for r in ok]; el = [r["stats"]["elapsed_ms"] for r in ok if r["stats"]]
        rss = [r["stats"]["peak_rss_bytes"] / 2**20 for r in ok if r["stats"]]
        lc = [r["lance_calls"] for r in ok if r["lance_calls"] is not None]
        par = f"{eq}/{uq}/{df}" if e == "gq3" else ""
        print(f"| {s} | {q} | {e} | {len(rs)} | {len(rs)-len(ok)} | {pct(w,.5):.0f} | {pct(w,.95):.0f} | {pct(el,.5):.0f} | {pct(rss,.5):.0f} | {pct(lc,.5):.0f} | {par} |")
