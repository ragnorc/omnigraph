"""Recall@10 against exact ground truth, ann-benchmarks style (a result counts when its exact
squared-L2 distance is within the 10th true distance plus 1e-3 relative slack), plus the
id-based recall, returned-distance agreement and the ranking descriptor.
Usage: python -I metrics.py <prefix> <raw.jsonl> <label>"""
import sys, json, numpy as np
pre, raw, label = sys.argv[1], sys.argv[2], sys.argv[3]
g = np.load(pre + ".gt.npz"); X = g["X"].astype(np.float64); Q = g["Q"].astype(np.float64); gi = g["ids"]; gd = g["d2"]
out = {}
for l in open(raw):
    r = json.loads(l)
    if r["rep"] != 0 or r["exit"] != 0: continue
    key = (r["query"], r["edition"]); j = r["param"]
    ids = [row["i.id"] for row in r["rows"]]
    d = ((X[ids] - Q[j]) ** 2).sum(1) if ids else np.array([])
    thr = gd[j, 9] * (1 + 1e-3) + 1e-9
    rec = float((d <= thr).sum()) / 10
    idrec = len(set(ids) & set(gi[j, :10].tolist())) / 10
    reported = np.array([row["d"] for row in r["rows"]])
    err = float(np.max(np.abs(reported - d) / np.maximum(d, 1e-9))) if len(d) else 0
    o = out.setdefault(key, {"n": 0, "recall": 0, "id_recall": 0, "max_rel_dist_err": 0, "exhaustive": set()})
    o["n"] += 1; o["recall"] += rec; o["id_recall"] += idrec; o["max_rel_dist_err"] = max(o["max_rel_dist_err"], err)
    o["exhaustive"].add(str((r.get("ranking") or {}).get("exhaustive")))
for (q, e), o in sorted(out.items()):
    print(json.dumps({"label": label, "query": q, "edition": e, "n": o["n"], "recall@10": round(o["recall"] / o["n"], 4),
                      "id_recall@10": round(o["id_recall"] / o["n"], 4), "max_rel_dist_err": o["max_rel_dist_err"],
                      "exhaustive": sorted(o["exhaustive"])}))
