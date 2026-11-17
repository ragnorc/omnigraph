"""BEIR metrics from raw results: nDCG@10 (graded gains, as pytrec_eval's ndcg_cut_10),
Recall@100, MRR@10, plus edition agreement (top-10 overlap, identical rankings).
Usage: python -I metrics.py <beir-dir> <params.json> <raw.jsonl> [label]"""
import json, sys, math, csv
d, pf, raw = sys.argv[1], sys.argv[2], sys.argv[3]
label = sys.argv[4] if len(sys.argv) > 4 else raw
qids = json.load(open(pf))["qids"]
qrels = {}
with open(f"{d}/qrels/test.tsv") as f:
    r = csv.reader(f, delimiter="\t"); next(r)
    for qid, did, s in r: qrels.setdefault(qid, {})[did] = int(s)
runs = {"e2": {}, "gq3": {}}; err = {"e2": 0, "gq3": 0}
for l in open(raw):
    x = json.loads(l)
    if x["query"] != "rank" or x["rep"] != 0: continue
    if x["exit"] != 0: err[x["edition"]] += 1; runs[x["edition"]][qids[x["param"]]] = []; continue
    runs[x["edition"]][qids[x["param"]]] = [row["d.id"] for row in x["rows"]]
def ndcg(ranked, rel, k=10):
    dcg = sum((rel.get(doc, 0)) / math.log2(i + 2) for i, doc in enumerate(ranked[:k]))
    ideal = sorted([v for v in rel.values() if v > 0], reverse=True)[:k]
    idcg = sum(v / math.log2(i + 2) for i, v in enumerate(ideal))
    return dcg / idcg if idcg else 0.0
def recall(ranked, rel, k=100):
    R = {x for x, v in rel.items() if v > 0}
    return len(R & set(ranked[:k])) / len(R) if R else 0.0
def mrr(ranked, rel, k=10):
    for i, doc in enumerate(ranked[:k]):
        if rel.get(doc, 0) > 0: return 1 / (i + 1)
    return 0.0
res = {}
for e in runs:
    qs = [q for q in qids if q in runs[e]]
    res[e] = dict(n=len(qs), err=err[e],
                  ndcg10=sum(ndcg(runs[e][q], qrels[q]) for q in qs) / len(qs),
                  recall100=sum(recall(runs[e][q], qrels[q]) for q in qs) / len(qs),
                  mrr10=sum(mrr(runs[e][q], qrels[q]) for q in qs) / len(qs))
common = [q for q in qids if q in runs["e2"] and q in runs["gq3"]]
same10 = sum(runs["e2"][q][:10] == runs["gq3"][q][:10] for q in common)
same100 = sum(runs["e2"][q] == runs["gq3"][q] for q in common)
ov10 = sum(len(set(runs["e2"][q][:10]) & set(runs["gq3"][q][:10])) / max(1, min(10, len(runs["e2"][q][:10]))) for q in common) / len(common)
better = sum(ndcg(runs["gq3"][q], qrels[q]) > ndcg(runs["e2"][q], qrels[q]) + 1e-12 for q in common)
worse = sum(ndcg(runs["gq3"][q], qrels[q]) < ndcg(runs["e2"][q], qrels[q]) - 1e-12 for q in common)
print(json.dumps({"label": label, "e2": res["e2"], "gq3": res["gq3"], "queries": len(common),
                  "identical_top10_order": same10, "identical_top100": same100, "mean_top10_overlap": ov10,
                  "gq3_ndcg_better": better, "gq3_ndcg_worse": worse}))
