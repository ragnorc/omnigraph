#!/usr/bin/env python3
"""Same-column predicate combinations over nullable columns: hunts optimizer
rewrites that break three-valued logic. One graph, many queries per batch."""
import json, os, random, subprocess, sys, shutil
sys.path.insert(0, os.path.dirname(__file__))
from qfuzz import and3, or3, not3, cmp

OG = os.environ.get("OG", "omnigraph")
D = "/tmp/og/nullfuzz"
SCHEMA = """node R {
  slug: String @key
  s: String?
  n: I64?
  b: Bool?
}
"""
SV = [None, "a", "b", "c"]
NV = [None, 0, 1, 2]
BV = [None, True, False]
ROWS = []
k = 0
for s in SV:
    for n in NV:
        for b in BV:
            ROWS.append({"slug": f"r{k:02d}", "s": s, "n": n, "b": b})
            k += 1


def atom(r):
    col = r.choice(["s", "s", "n", "b"])
    if col == "s":
        vals = ["a", "b", "c", "z"]
        q = lambda v: json.dumps(v)
    elif col == "n":
        vals = [0, 1, 2, 5]
        q = str
    else:
        vals = [True, False]
        q = lambda v: "true" if v else "false"
    kind = r.choice(["cmp", "cmp", "in", "notin", "isnull", "notnull"] + (["bare"] if col == "b" else []))
    if kind == "cmp":
        op = r.choice(["=", "!=", "<", "<=", ">", ">="] if col != "b" else ["=", "!="])
        v = r.choice(vals)
        return f"$x.{col} {op} {q(v)}", lambda it: cmp(op, it[col], v)
    if kind in ("in", "notin"):
        lst = r.sample(vals, r.randint(0, len(vals)))
        t = f"$x.{col} in [{', '.join(q(v) for v in lst)}]"
        f = lambda it: None if it[col] is None else (it[col] in lst)
        if kind == "notin":
            return f"not ({t})", lambda it: not3(f(it))
        return t, f
    if kind == "isnull":
        return f"$x.{col} is null", lambda it: it[col] is None
    if kind == "notnull":
        return f"$x.{col} is not null", lambda it: it[col] is not None
    return f"$x.{col}", lambda it: it[col]


def expr(r, d=0):
    if d >= 3 or (d > 0 and r.random() < 0.35):
        return atom(r)
    op = r.choice(["and", "or", "or", "not"])
    if op == "not":
        t, f = expr(r, d + 1)
        return f"not ({t})", lambda it: not3(f(it))
    t1, f1 = expr(r, d + 1)
    t2, f2 = expr(r, d + 1)
    if op == "and":
        return f"({t1}) and ({t2})", lambda it: and3(f1(it), f2(it))
    return f"({t1}) or ({t2})", lambda it: or3(f1(it), f2(it))


def main():
    seed, batches = int(sys.argv[1]), int(sys.argv[2])
    shutil.rmtree(D, ignore_errors=True)
    os.makedirs(D)
    open(f"{D}/s.pg", "w").write(SCHEMA)
    open(f"{D}/d.jsonl", "w").write("\n".join(json.dumps({"type": "R", "data": {k: v for k, v in x.items() if v is not None}}) for x in ROWS) + "\n")
    g = f"{D}/g.omni"
    subprocess.run([OG, "init", "--schema", f"{D}/s.pg", g], capture_output=True, check=True)
    subprocess.run([OG, "load", "--data", f"{D}/d.jsonl", "--mode", "overwrite", "--target", g], capture_output=True, check=True)
    r = random.Random(seed)
    bad = 0
    seen = set()
    for bt in range(batches):
        qs = []
        for i in range(25):
            t, f = expr(r)
            qs.append((f"q{i}", t, sorted(x["slug"] for x in ROWS if f(x) is True)))
        src = "\n".join(f"query {n}() {{ match {{ $x: R\n {t} }} return {{ $x.slug }} }}" for n, t, _ in qs)
        open(f"{D}/q.gq", "w").write(src + "\n")
        for n, t, exp in qs:
            p = subprocess.run([OG, "query", n, "--query", f"{D}/q.gq", "--json", "--target", g], capture_output=True, text=True)
            if p.returncode != 0:
                print("ERROR", t, p.stderr[-300:], flush=True)
                bad += 1
                continue
            got = sorted(x["x.slug"] for x in json.loads(p.stdout)["rows"])
            if got != exp:
                bad += 1
                wrong = [s for s in set(got) ^ set(exp)]
                ex = next(x for x in ROWS if x["slug"] == wrong[0])
                print(f"MISMATCH {t}\n  extra={sorted(set(got)-set(exp))} missing={sorted(set(exp)-set(got))} e.g. row={ex}", flush=True)
        print(f"batch {bt} done bad={bad}", flush=True)
    print("done bad", bad)


if __name__ == "__main__":
    main()
