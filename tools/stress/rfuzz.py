#!/usr/bin/env python3
"""Filters whose later conditions run only on undecided rows (#1346). The
right operand of `and` runs on the rows its left operand did not make false,
that of `or` on the rows it did not make true, and each later conjunct of a
join residual on the rows the earlier ones kept. At most 20% undecided rows
are gathered, evaluated and scattered back; above that the whole batch runs.

This fuzzer generates deep and/or/not trees with selective and unselective
atoms, so both sides of the 20% threshold occur, over:
- one variable on a larger table (up to 600 rows),
- a traversal `$a rel $b` with conditions on both ends and across them,
- a cross join `$a: Item $b: Item` whose filter is a join residual.
It compares the rows with three-valued logic in Python (qfuzz's atoms).

Usage: rfuzz.py <first seed> <count>    (OG: CLI binary)"""
import importlib.util, json, os, random, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("qfuzz", os.path.join(HERE, "qfuzz.py"))
qf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qf)

OG = os.environ.get("OG", "omnigraph")
FIRST, COUNT = int(sys.argv[1]), int(sys.argv[2])
ROOT = "/tmp/og/rfuzz"


def gen_items(r, k):
    items = []
    for i in range(k):
        items.append({
            "slug": "i%04d" % i,
            "n": qf.maybe(r, lambda: r.choice(qf.INTS[:8])),
            "m": qf.maybe(r, lambda: r.choice(qf.INTS[:8])),
            "f": qf.maybe(r, lambda: r.choice([-1.5, 0.0, 0.5, 1.0, 2.25, 3.0, -2.0])),
            "s": qf.maybe(r, lambda: r.choice(qf.STRS)),
            "b": qf.maybe(r, lambda: r.random() < .5),
            "d": qf.maybe(r, lambda: r.choice(qf.DATES)),
            "tags": qf.maybe(r, lambda: r.sample(qf.TAGS, r.randint(0, 3))),
        })
    return items


def cross_atom(r):
    kind = r.choice(["nm", "nn", "ss", "dd", "ff", "bb"])
    op = r.choice(qf.OPS)
    fld = {"nm": ("n", "m"), "nn": ("n", "n"), "ss": ("s", "s"), "dd": ("d", "d"), "ff": ("f", "f"), "bb": ("b", "b")}[kind]
    if kind == "bb":
        op = r.choice(["=", "!="])
    return f"$a.{fld[0]} {op} $b.{fld[1]}", lambda a, b: qf.cmp(op, a[fld[0]], b[fld[1]])


def gen2(r, depth):
    """Expression over ($a, $b) -> (text, fn(a, b))."""
    if depth >= 4 or (depth > 0 and r.random() < 0.35):
        c = r.random()
        if c < 0.35:
            t, f = qf.gen_atom(r, "$a")
            return t, lambda a, b: f(a)
        if c < 0.7:
            t, f = qf.gen_atom(r, "$b")
            return t, lambda a, b: f(b)
        return cross_atom(r)
    k = r.choice(["and", "and", "or", "or", "not"])
    if k == "not":
        t, f = gen2(r, depth + 1)
        return f"not ({t})", lambda a, b: qf.not3(f(a, b))
    t1, f1 = gen2(r, depth + 1)
    t2, f2 = gen2(r, depth + 1)
    if k == "and":
        return f"({t1}) and ({t2})", lambda a, b: qf.and3(f1(a, b), f2(a, b))
    return f"({t1}) or ({t2})", lambda a, b: qf.or3(f1(a, b), f2(a, b))


def gen1(r, depth):
    if depth >= 4 or (depth > 0 and r.random() < 0.35):
        return qf.gen_atom(r, "$a")
    k = r.choice(["and", "and", "or", "or", "not"])
    if k == "not":
        t, f = gen1(r, depth + 1)
        return f"not ({t})", lambda a: qf.not3(f(a))
    t1, f1 = gen1(r, depth + 1)
    t2, f2 = gen1(r, depth + 1)
    if k == "and":
        return f"({t1}) and ({t2})", lambda a: qf.and3(f1(a), f2(a))
    return f"({t1}) or ({t2})", lambda a: qf.or3(f1(a), f2(a))


def run(args):
    return subprocess.run([OG] + args, capture_output=True, text=True, timeout=300)


def case(seed):
    r = random.Random(seed)
    d = os.path.join(ROOT, str(seed))
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    g = os.path.join(d, "g.omni")
    open(f"{d}/s.pg", "w").write(qf.SCHEMA)
    p = run(["init", "--schema", f"{d}/s.pg", g])
    if p.returncode:
        return [f"init {p.stderr[-300:]}"]
    big = gen_items(r, r.choice([40, 150, 600]))
    edges = []
    for _ in range(len(big) * 2):
        edges.append({"from": r.choice(big)["slug"], "to": r.choice(big)["slug"], "w": None})
    open(f"{d}/seed.jsonl", "w").write(qf.jsonl(big, edges))
    p = run(["load", "--target", g, "--data", f"{d}/seed.jsonl", "--mode", "overwrite", "--yes"])
    if p.returncode:
        return [f"load {p.stderr[-300:]}"]
    if r.random() < 0.5:
        run(["index", "build", "--target", g])
    by = {it["slug"]: it for it in big}
    small = big[:r.choice([20, 40, 60])]
    smallset = {it["slug"] for it in small}
    fails = []
    for qi in range(12):
        kind = r.choice(["one", "one", "trav", "trav", "cross"])
        if kind == "one":
            t, f = gen1(r, 0)
            src = f"query q() {{ match {{ $a: Item\n {t} }} return {{ $a.slug }} }}"
            want = sorted(it["slug"] for it in big if f(it) is True)
        elif kind == "trav":
            t, f = gen2(r, 0)
            src = f"query q() {{ match {{ $a: Item\n $a rel $b\n {t} }} return {{ $a.slug, $b.slug }} }}"
            want = sorted({(e["from"], e["to"]) for e in edges if f(by[e["from"]], by[e["to"]]) is True})  # unbound hop: set semantics
        else:
            t, f = gen2(r, 0)
            # bound both sides to the small slice so the cross join stays small
            lst = ", ".join(json.dumps(s) for s in sorted(smallset))
            src = (f"query q() {{ match {{ $a: Item\n $b: Item\n $a.slug in [{lst}]\n $b.slug in [{lst}]\n ({t}) }} "
                   f"return {{ $a.slug, $b.slug }} }}")
            want = sorted((a["slug"], b["slug"]) for a in small for b in small if f(a, b) is True)
        p = run(["query", "--target", g, "-e", src, "--json"])
        if p.returncode:
            msg = (p.stdout + p.stderr)[-400:]
            if "Q006" in msg:
                continue
            fails.append({"kind": kind, "query": src, "error": msg})
            continue
        rows = json.loads(p.stdout)["rows"]
        if kind == "one":
            got = sorted(x["a.slug"] for x in rows)
        else:
            got = sorted((x["a.slug"], x["b.slug"]) for x in rows)
        if got != want:
            gs, ws = set(map(str, got)), set(map(str, want))
            fails.append({"kind": kind, "query": src, "missing": sorted(ws - gs)[:5], "extra": sorted(gs - ws)[:5],
                          "n_got": len(got), "n_want": len(want)})
    if fails:
        json.dump({"seed": seed, "fails": fails}, open(f"{d}/fail.json", "w"), indent=1)
    else:
        shutil.rmtree(d, ignore_errors=True)
    return fails


def main():
    bad = 0
    for s in range(FIRST, FIRST + COUNT):
        f = case(s)
        if f:
            bad += 1
            print("FAIL", s, json.dumps(f[0])[:600], flush=True)
        else:
            print("ok", s, flush=True)
    print(json.dumps({"first": FIRST, "count": COUNT, "failed": bad}))


if __name__ == "__main__":
    main()
