#!/usr/bin/env python3
"""Black-box differential fuzzer: OmniGraph CLI vs a Python oracle.

Covers scalar filters with three-valued null logic, list membership, string
predicates, Date comparisons, order/limit with nulls and id tiebreak,
aggregates (grouped and ungrouped), correlated blocks over one edge type, and
mutation `where` predicates (update and delete, with edge cascade).
"""
import json, os, random, shutil, subprocess, sys, math

OG = os.environ.get("OG", "omnigraph")
ROOT = os.environ.get("FUZZ_ROOT", "/tmp/og/fuzzrun")
FAIL = os.environ.get("FUZZ_FAIL", "/tmp/og/fuzzfail")

SCHEMA = """node Item {
  slug: String @key
  n: I64?
  m: I64?
  f: F64?
  s: String?
  b: Bool?
  d: Date?
  tags: [String]?
}

edge Rel: Item -> Item {
  w: I64?
}
"""

STRS = ["", "a", "A", "ab", "abc", "b", "ba", "%", "_", "a_b", "é", "z", "10", "9"]
TAGS = ["x", "y", "z", "", "xy"]
DATES = ["2026-01-01", "2026-01-02", "2026-01-03", "2025-12-31", "1970-01-01"]
INTS = [-3, -1, 0, 1, 2, 3, 5, 10, 9007199254740993, -9007199254740992]


def maybe(r, gen, p=0.25):
    return None if r.random() < p else gen()


def gen_items(r):
    k = r.randint(0, 18)
    items = []
    for i in range(k):
        it = {
            "slug": "i%02d" % i,
            "n": maybe(r, lambda: r.choice(INTS[:8]) if r.random() < .9 else r.choice(INTS)),
            "m": maybe(r, lambda: r.choice(INTS[:8])),
            "f": maybe(r, lambda: r.choice([-1.5, 0.0, 0.5, 1.0, 2.25, 3.0, 1e20, -2.0])),
            "s": maybe(r, lambda: r.choice(STRS)),
            "b": maybe(r, lambda: r.random() < .5),
            "d": maybe(r, lambda: r.choice(DATES)),
            "tags": maybe(r, lambda: r.sample(TAGS, r.randint(0, 3))),
        }
        items.append(it)
    edges = []
    if items:
        for _ in range(r.randint(0, 2 * len(items))):
            a, b = r.choice(items)["slug"], r.choice(items)["slug"]
            edges.append({"from": a, "to": b, "w": maybe(r, lambda: r.randint(-2, 4))})
    return items, edges


# ---------------- expression generation + 3VL oracle ----------------

def lit_str(s):
    return json.dumps(s, ensure_ascii=False)


def cmp(op, a, b):
    if a is None or b is None:
        return None
    return {"=": a == b, "!=": a != b, "<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]


OPS = ["=", "!=", "<", "<=", ">", ">="]


def gen_atom(r, v):
    """Return (gq_text, fn(item)->True/False/None)."""
    kind = r.choice(["n", "nf", "f", "s", "sc", "ssw", "b", "bare", "null", "inn", "ins", "tags", "d", "nm"])
    if kind == "n":
        op, c = r.choice(OPS), r.choice([0, 1, 2, 3, 5, 10])
        return f"{v}.n {op} {c}", lambda it: cmp(op, it["n"], c)
    if kind == "nf":
        op, c = r.choice(OPS), r.choice([0.5, 2.5, 0.0, 1.0])
        return f"{v}.n {op} {c}", lambda it: cmp(op, None if it["n"] is None else float(it["n"]), c)
    if kind == "f":
        op, c = r.choice(OPS), r.choice([0.5, 1, 0, 2, 3.0, 2.25])
        return f"{v}.f {op} {c}", lambda it: cmp(op, it["f"], c)
    if kind == "s":
        op, c = r.choice(OPS), r.choice(STRS)
        return f"{v}.s {op} {lit_str(c)}", lambda it: cmp(op, it["s"], c)
    if kind == "sc":
        c = r.choice(STRS)
        return f"{v}.s contains {lit_str(c)}", lambda it: None if it["s"] is None else (c in it["s"])
    if kind == "ssw":
        c = r.choice(STRS)
        return f"{v}.s starts_with {lit_str(c)}", lambda it: None if it["s"] is None else it["s"].startswith(c)
    if kind == "b":
        c = r.random() < .5
        return f"{v}.b = {'true' if c else 'false'}", lambda it: cmp("=", it["b"], c)
    if kind == "bare":
        return f"{v}.b", lambda it: it["b"]
    if kind == "null":
        fld = r.choice(["n", "f", "s", "b", "d", "tags", "m"])
        neg = r.random() < .5
        return (f"{v}.{fld} is {'not ' if neg else ''}null",
                lambda it: (it[fld] is not None) if neg else (it[fld] is None))
    if kind == "inn":
        lst = r.sample([0, 1, 2, 3, 5, 10], r.randint(0, 3))
        return f"{v}.n in [{', '.join(map(str, lst))}]", lambda it: None if it["n"] is None else (it["n"] in lst)
    if kind == "ins":
        lst = r.sample(STRS, r.randint(0, 3))
        return (f"{v}.s in [{', '.join(lit_str(x) for x in lst)}]",
                lambda it: None if it["s"] is None else (it["s"] in lst))
    if kind == "tags":
        c = r.choice(TAGS)
        return f"{v}.tags contains {lit_str(c)}", lambda it: None if it["tags"] is None else (c in it["tags"])
    if kind == "d":
        op, c = r.choice(OPS), r.choice(DATES)
        return f'{v}.d {op} date("{c}")', lambda it: cmp(op, it["d"], c)
    if kind == "nm":
        op = r.choice(OPS)
        return f"{v}.n {op} {v}.m", lambda it: cmp(op, it["n"], it["m"])
    raise AssertionError


def and3(a, b):
    if a is False or b is False:
        return False
    if a is None or b is None:
        return None
    return True


def or3(a, b):
    if a is True or b is True:
        return True
    if a is None or b is None:
        return None
    return False


def not3(a):
    return None if a is None else (not a)


def gen_expr(r, v, depth=0):
    if depth >= 2 or r.random() < 0.45:
        return gen_atom(r, v)
    k = r.choice(["and", "or", "not"])
    if k == "not":
        t, f = gen_expr(r, v, depth + 1)
        return f"not ({t})", lambda it: not3(f(it))
    t1, f1 = gen_expr(r, v, depth + 1)
    t2, f2 = gen_expr(r, v, depth + 1)
    if k == "and":
        return f"({t1}) and ({t2})", lambda it: and3(f1(it), f2(it))
    return f"({t1}) or ({t2})", lambda it: or3(f1(it), f2(it))


# ---------------- harness ----------------

def run(args, check=True):
    p = subprocess.run([OG] + args, capture_output=True, text=True, timeout=300)
    if check and p.returncode != 0:
        raise RuntimeError(f"cmd failed rc={p.returncode}: {' '.join(args)}\n{p.stderr[-3000:]}\n{p.stdout[-2000:]}")
    return p


def jsonl(items, edges):
    out = []
    for it in items:
        data = {k: v for k, v in it.items() if v is not None}
        out.append(json.dumps({"type": "Item", "data": data}, ensure_ascii=False))
    for e in edges:
        d = {} if e["w"] is None else {"w": e["w"]}
        out.append(json.dumps({"edge": "Rel", "from": e["from"], "to": e["to"], "data": d}))
    return "\n".join(out) + "\n"


def sortkey_asc(val, slug):
    # nulls first ascending, id tiebreak ascending
    return (0 if val is None else 1, val if val is not None else 0, slug)


class Mismatch(Exception):
    pass


def num_eq(a, b):
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if a == b:
            return True
        return math.isclose(float(a), float(b), rel_tol=1e-6, abs_tol=1e-9)
    return a == b


def case(seed):
    r = random.Random(seed)
    items, edges = gen_items(r)
    d = os.path.join(ROOT, str(seed))
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    open(os.path.join(d, "schema.pg"), "w").write(SCHEMA)
    open(os.path.join(d, "data.jsonl"), "w").write(jsonl(items, edges))
    g = os.path.join(d, "g.omni")
    run(["init", "--schema", os.path.join(d, "schema.pg"), g])
    if items:
        run(["load", "--data", os.path.join(d, "data.jsonl"), "--mode", "overwrite", "--target", g])
    byslug = {it["slug"]: it for it in items}

    checks = []  # (name, gq, expected, comparator)
    for qi in range(12):
        t = r.choice(["filter", "filter", "filter", "order", "agg", "group", "block", "distinct", "edgefilter"])
        name = f"q{qi}"
        if t == "filter":
            gq, f = gen_expr(r, "$i")
            src = f"query {name}() {{ match {{ $i: Item\n {gq} }} return {{ $i.slug }} }}"
            exp = sorted(it["slug"] for it in items if f(it) is True)
            checks.append((name, src, exp, "set_slug"))
        elif t == "order":
            gq, f = gen_expr(r, "$i") if r.random() < .5 else ("", lambda it: True)
            fld = r.choice(["n", "s", "f", "d", "b"])
            desc = r.random() < .5
            lim = r.randint(1, 8)
            src = (f"query {name}() {{ match {{ $i: Item\n {gq} }} return {{ $i.slug, $i.{fld} }} "
                   f"order {{ $i.{fld} {'desc' if desc else 'asc'} }} limit {lim} }}")
            rows = [it for it in items if f(it) is True]
            if desc:
                # descending: nulls last; ties by id ascending
                nn = sorted([x for x in rows if x[fld] is not None], key=lambda x: x["slug"])
                nn = sorted(nn, key=lambda x: x[fld], reverse=True)
                rows = nn + sorted([x for x in rows if x[fld] is None], key=lambda x: x["slug"])
            else:
                rows = sorted(rows, key=lambda x: sortkey_asc(x[fld], x["slug"]))
            exp = [x["slug"] for x in rows[:lim]]
            checks.append((name, src, (exp, fld, [x[fld] for x in rows[:lim]]), "order"))
        elif t == "agg":
            gq, f = gen_expr(r, "$i") if r.random() < .7 else ("", lambda it: True)
            src = (f"query {name}() {{ match {{ $i: Item\n {gq} }} return {{ count($i) as c, sum($i.n) as sn, "
                   f"min($i.n) as mn, max($i.n) as mx, avg($i.f) as af, min($i.s) as ms, max($i.d) as md, count($i.s) as cs }} }}")
            rows = [it for it in items if f(it) is True]
            ns = [x["n"] for x in rows if x["n"] is not None]
            fs = [x["f"] for x in rows if x["f"] is not None]
            ss = [x["s"] for x in rows if x["s"] is not None]
            ds = [x["d"] for x in rows if x["d"] is not None]
            exp = {"c": len(rows), "sn": float(sum(ns)) if ns else None, "mn": min(ns) if ns else None,
                   "mx": max(ns) if ns else None, "af": (sum(fs) / len(fs)) if fs else None,
                   "ms": min(ss) if ss else None, "md": max(ds) if ds else None, "cs": len(ss)}
            checks.append((name, src, exp, "agg"))
        elif t == "group":
            gq, f = gen_expr(r, "$i") if r.random() < .5 else ("", lambda it: True)
            gk = r.choice(["b", "s"])
            src = (f"query {name}() {{ match {{ $i: Item\n {gq} }} return {{ $i.{gk}, count($i) as c, sum($i.n) as sn, max($i.f) as mf }} }}")
            groups = {}
            for it in items:
                if f(it) is True:
                    groups.setdefault(it[gk], []).append(it)
            exp = {}
            for k, rows in groups.items():
                ns = [x["n"] for x in rows if x["n"] is not None]
                fs = [x["f"] for x in rows if x["f"] is not None]
                exp[json.dumps(k)] = {"c": len(rows), "sn": float(sum(ns)) if ns else None, "mf": max(fs) if fs else None}
            checks.append((name, src, (gk, exp), "group"))
        elif t == "block":
            bk = r.choice(["not", "exists", "count", "notin", "sumw"])
            gq, f = gen_expr(r, "$j")
            if bk == "not":
                src = f"query {name}() {{ match {{ $i: Item\n not {{ $i rel $j\n {gq} }} }} return {{ $i.slug }} }}"
                exp = sorted(it["slug"] for it in items
                             if not any(e["from"] == it["slug"] and f(byslug[e["to"]]) is True for e in edges))
            elif bk == "notin":
                src = f"query {name}() {{ match {{ $i: Item\n not {{ $j rel $i\n {gq} }} }} return {{ $i.slug }} }}"
                exp = sorted(it["slug"] for it in items
                             if not any(e["to"] == it["slug"] and f(byslug[e["from"]]) is True for e in edges))
            elif bk == "exists":
                src = f"query {name}() {{ match {{ $i: Item\n exists {{ $i rel $j\n {gq} }} }} return {{ $i.slug }} }}"
                exp = sorted(it["slug"] for it in items
                             if any(e["from"] == it["slug"] and f(byslug[e["to"]]) is True for e in edges))
            elif bk == "count":
                k = r.randint(0, 3)
                op = r.choice([">=", ">", "=", "<"])
                src = f"query {name}() {{ match {{ $i: Item\n count {{ $i rel $j\n {gq} }} {op} {k} }} return {{ $i.slug }} }}"
                # count counts matches (rows); parallel edges count separately? use distinct j semantics check both
                def cnt(it):
                    return sum(1 for e in edges if e["from"] == it["slug"] and f(byslug[e["to"]]) is True)
                def cntd(it):
                    return len({e["to"] for e in edges if e["from"] == it["slug"] and f(byslug[e["to"]]) is True})
                exp = sorted(it["slug"] for it in items if cmp(op, cntd(it), k))
                exp_alt = sorted(it["slug"] for it in items if cmp(op, cnt(it), k))
                checks.append((name, src, (exp, exp_alt), "set_slug_alt"))
                continue
            else:  # sum over edge weight via bound edge
                k = r.randint(0, 4)
                op = r.choice([">=", ">", "<"])
                src = f"query {name}() {{ match {{ $i: Item\n sum($e.w) {{ $i $e:rel $j }} {op} {k} }} return {{ $i.slug }} }}"
                def sm(it):
                    ws = [e["w"] for e in edges if e["from"] == it["slug"] and e["w"] is not None]
                    return float(sum(ws)) if ws else None
                exp = sorted(it["slug"] for it in items if cmp(op, sm(it), k) is True)
            checks.append((name, src, exp, "set_slug"))
        elif t == "distinct":
            fld = r.choice(["s", "b", "n"])
            src = f"query {name}() {{ match {{ $i: Item\n $i rel $j }} return distinct {{ $j.{fld} }} }}"
            vals = {json.dumps(byslug[e["to"]][fld]) for e in edges}
            checks.append((name, src, (fld, sorted(vals)), "distinct"))
        elif t == "edgefilter":
            gq, f = gen_expr(r, "$j")
            gq2, f2 = gen_expr(r, "$i")
            src = f"query {name}() {{ match {{ $i: Item\n $i rel $j\n {gq}\n {gq2} }} return {{ $i.slug, $j.slug as js }} }}"
            pairs = sorted({(e["from"], e["to"]) for e in edges if f(byslug[e["to"]]) is True and f2(byslug[e["from"]]) is True})
            pairs_multi = sorted((e["from"], e["to"]) for e in edges if f(byslug[e["to"]]) is True and f2(byslug[e["from"]]) is True)
            checks.append((name, src, (pairs, pairs_multi), "pairs"))

    qfile = os.path.join(d, "q.gq")
    open(qfile, "w").write("\n\n".join(c[1] for c in checks) + "\n")
    lint = run(["lint", "--schema", os.path.join(d, "schema.pg"), "--query", qfile], check=False)
    if lint.returncode != 0:
        raise Mismatch(f"lint failed for generated queries:\n{lint.stdout[-3000:]}{lint.stderr[-2000:]}")
    for name, src, exp, kind in checks:
        p = run(["query", name, "--query", qfile, "--json", "--target", g], check=False)
        if p.returncode != 0:
            raise Mismatch(f"{name} failed rc={p.returncode}\n{src}\n{p.stderr[-2000:]}{p.stdout[-2000:]}")
        rows = json.loads(p.stdout)["rows"]
        ok, detail = compare(kind, exp, rows)
        if not ok:
            raise Mismatch(f"{name} mismatch ({kind})\n{src}\nexpected={json.dumps(exp, default=str)}\nactual={json.dumps(rows)}\n{detail}")

    # ---------------- mutation phase ----------------
    model = {it["slug"]: dict(it) for it in items}
    medges = [dict(e) for e in edges]
    for mi in range(r.randint(1, 4)):
        gq, f = gen_expr(r, "$i")
        where = gq.replace("$i.", "")  # mutation where uses bare property names
        mname = f"m{mi}"
        if r.random() < 0.6:
            newv = r.choice([0, 1, 2, 3, 5, 10])
            src = f"query {mname}() {{ update Item set {{ m: {newv} }} where {where} }}"
            p = run(["mutate", "-e", src, "--json", "--target", g], check=False)
            if p.returncode != 0:
                raise Mismatch(f"mutation failed\n{src}\n{p.stderr[-2000:]}")
            hit = [s for s, it in model.items() if f(it) is True]
            for s in hit:
                model[s]["m"] = newv
            got = json.loads(p.stdout).get("affected_nodes")
            if got != len(hit):
                raise Mismatch(f"update affected {got}, expected {len(hit)}\n{src}\nhit={hit}")
        else:
            src = f"query {mname}() {{ delete Item where {where} }}"
            p = run(["mutate", "-e", src, "--json", "--target", g], check=False)
            if p.returncode != 0:
                raise Mismatch(f"mutation failed\n{src}\n{p.stderr[-2000:]}")
            hit = [s for s, it in model.items() if f(it) is True]
            for s in hit:
                del model[s]
            before = len(medges)
            medges = [e for e in medges if e["from"] in model and e["to"] in model]
            got = json.loads(p.stdout)
            if got.get("affected_nodes") != len(hit):
                raise Mismatch(f"delete affected {got.get('affected_nodes')}, expected {len(hit)}\n{src}\nhit={hit}")
        # verify state
        src = "query all() { match { $i: Item } return { $i } }\nquery alle() { match { $a: Item\n $a $e:rel $b } return { $a.slug, $b.slug as bs, $e.w } }"
        open(os.path.join(d, "all.gq"), "w").write(src)
        p = run(["query", "all", "--query", os.path.join(d, "all.gq"), "--json", "--target", g])
        got = {row["i"]["slug"]: row["i"] for row in json.loads(p.stdout)["rows"]}
        if set(got) != set(model):
            raise Mismatch(f"after {mname}: node set differs\nexpected={sorted(model)}\nactual={sorted(got)}")
        for s, it in model.items():
            for k, v in it.items():
                gv = got[s].get(k)
                if k == "tags":
                    if (v or None) != (gv or None) and not (v == [] and gv == []):
                        if not (v == gv):
                            raise Mismatch(f"after {mname}: {s}.{k} expected {v!r} got {gv!r}")
                elif not num_eq(v, gv):
                    raise Mismatch(f"after {mname}: {s}.{k} expected {v!r} got {gv!r}")
        p = run(["query", "alle", "--query", os.path.join(d, "all.gq"), "--json", "--target", g])
        gotE = sorted(((row["a.slug"], row["bs"], row.get("e.w")) for row in json.loads(p.stdout)["rows"]), key=repr)
        expE = sorted(((e["from"], e["to"], e["w"]) for e in medges), key=repr)
        if gotE != expE:
            raise Mismatch(f"after {mname}: edges differ\nexpected={expE}\nactual={gotE}")
    shutil.rmtree(d, ignore_errors=True)


def compare(kind, exp, rows):
    if kind == "set_slug":
        got = sorted(r["i.slug"] for r in rows)
        return got == exp, f"missing={sorted(set(exp)-set(got))} extra={sorted(set(got)-set(exp))} dup={len(got)!=len(set(got))}"
    if kind == "set_slug_alt":
        got = sorted(r["i.slug"] for r in rows)
        return got in (exp[0], exp[1]), ""
    if kind == "order":
        exps, fld, vals = exp
        got = [r["i.slug"] for r in rows]
        if got == exps:
            return True, ""
        gotvals = [r.get(f"i.{fld}") for r in rows]
        return False, f"gotvals={gotvals} expvals={vals}"
    if kind == "agg":
        if len(rows) != 1:
            return False, f"rows={len(rows)}"
        row = rows[0]
        bad = {k: (v, row.get(k)) for k, v in exp.items() if not num_eq(v, row.get(k))}
        return not bad, f"bad={bad}"
    if kind == "group":
        gk, e = exp
        got = {}
        for row in rows:
            k = json.dumps(row.get(f"i.{gk}"))
            if k in got:
                return False, f"duplicate group {k}"
            got[k] = {"c": row.get("c"), "sn": row.get("sn"), "mf": row.get("mf")}
        if set(got) != set(e):
            return False, f"groups differ exp={sorted(e)} got={sorted(got)}"
        bad = {k: (e[k], got[k]) for k in e if any(not num_eq(e[k][f], got[k][f]) for f in e[k])}
        return not bad, f"bad={bad}"
    if kind == "distinct":
        fld, vals = exp
        got = sorted(json.dumps(r.get(f"j.{fld}")) for r in rows)
        return got == vals, ""
    if kind == "pairs":
        got = sorted((r["i.slug"], r["js"]) for r in rows)
        return got in (exp[0], exp[1]), f"got={got}"
    raise AssertionError(kind)


def main():
    start, count = int(sys.argv[1]), int(sys.argv[2])
    os.makedirs(ROOT, exist_ok=True)
    os.makedirs(FAIL, exist_ok=True)
    fails = 0
    for seed in range(start, start + count):
        try:
            case(seed)
            print(f"seed {seed} ok", flush=True)
        except Mismatch as m:
            fails += 1
            msg = str(m)
            print(f"seed {seed} MISMATCH\n{msg}\n", flush=True)
            open(os.path.join(FAIL, f"{seed}.txt"), "w").write(msg)
        except Exception as ex:  # harness/tool error
            fails += 1
            print(f"seed {seed} ERROR {ex}", flush=True)
            open(os.path.join(FAIL, f"{seed}.err"), "w").write(str(ex))
    print(f"done fails={fails}")


if __name__ == "__main__":
    main()
