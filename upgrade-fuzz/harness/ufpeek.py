"""Summarise findings in a cases.jsonl: one line per (kind, signature) with a sample seed."""
import collections
import json
import sys

path = sys.argv[1]
kinds = set(sys.argv[2:])
fs = collections.Counter()
ex = {}
st = collections.Counter()
for line in open(path):
    r = json.loads(line)
    st[r["status"]] += 1
    for f in r.get("findings") or []:
        if kinds and f["kind"] not in kinds:
            continue
        k = (f["kind"], f["sig"][:110])
        fs[k] += 1
        ex.setdefault(k, (r["seed"], f))
print(dict(st))
for k, v in fs.most_common():
    print(v, k, "seed", ex[k][0], ex[k][1]["where"])
