"""BEIR test queries -> params JSON (+ query id list). Usage: python -I params.py <beir-dir> <out.json> [sample_n]"""
import json, sys, random, csv
d, out = sys.argv[1], sys.argv[2]; n = int(sys.argv[3]) if len(sys.argv) > 3 else 0
qrels = {}
with open(f"{d}/qrels/test.tsv") as f:
    r = csv.reader(f, delimiter="\t"); next(r)
    for qid, did, s in r: qrels.setdefault(qid, {})[did] = int(s)
qs = {}
for l in open(f"{d}/queries.jsonl", encoding="utf-8"):
    q = json.loads(l); qs[q["_id"]] = q["text"]
ids = [q for q in qs if q in qrels]
if n and n < len(ids): ids = sorted(random.Random(20261117).sample(ids, n), key=ids.index)
json.dump({"qids": ids, "rank": [{"q": qs[i]} for i in ids], "lexcount": [{"q": qs[i]} for i in ids]}, open(out, "w"))
print(len(ids))
