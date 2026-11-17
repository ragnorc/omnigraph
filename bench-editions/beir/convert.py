"""BEIR corpus.jsonl -> Omnigraph JSONL; optional seeded subset of n docs.
Usage: python -I convert.py <corpus.jsonl> <out.jsonl> [n]"""
import json, sys, random
src, out = sys.argv[1], sys.argv[2]
n = int(sys.argv[3]) if len(sys.argv) > 3 else 0
docs = [json.loads(l) for l in open(src, encoding="utf-8")]
if n:
    docs = random.Random(20261117).sample(docs, n)
with open(out, "w", encoding="utf-8") as o:
    for d in docs:
        t = (d.get("title") or "").strip(); x = (d.get("text") or "").strip()
        o.write(json.dumps({"type": "Doc", "data": {"id": d["_id"], "text": (t + "\n" + x).strip()}}, ensure_ascii=False) + "\n")
print(len(docs))
