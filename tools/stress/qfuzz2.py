#!/usr/bin/env python3
"""Round 2: numeric types (I32, U64, F32), DateTime, bounded multi-hop and
undirected traversal (shortest-path semantics), count(distinct), multi-key order."""
import json, os, random, shutil, subprocess, sys, struct, math
from collections import deque

OG = os.environ.get("OG", "omnigraph")
ROOT = os.environ.get("FUZZ_ROOT", "/tmp/og/fr2")
FAIL = os.environ.get("FUZZ_FAIL", "/tmp/og/ff2")

SCHEMA = """node N {
  slug: String @key
  i: I32?
  u: U64?
  f: F32?
  t: DateTime?
  g: String?
}

edge E: N -> N
"""
U64S = [0, 1, 7, 9223372036854775807, 9223372036854775808, 18446744073709551615]
F32S = [0.1, 0.5, 1.0, 2.5, 0.2, 100.0]
TS = ["2026-01-01T00:00:00Z", "2026-01-01T00:00:00.001Z", "2026-06-15T12:30:00Z", "1999-12-31T23:59:59.999Z"]


def f32(x):
    return struct.unpack("f", struct.pack("f", x))[0]


def run(args):
    return subprocess.run([OG] + args, capture_output=True, text=True, timeout=300)


class Mismatch(Exception):
    pass


def cmp(op, a, b):
    if a is None or b is None:
        return None
    return {"=": a == b, "!=": a != b, "<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]


OPS = ["=", "!=", "<", "<=", ">", ">="]


def ts_key(s):
    # normalize to (date, time, millis)
    base, _, frac = s.rstrip("Z").partition(".")
    return base + "." + (frac or "0").ljust(3, "0")


def atom(r):
    k = r.choice(["i", "u", "ubig", "f", "f01", "t", "g"])
    if k == "i":
        op, c = r.choice(OPS), r.choice([0, 1, 5, 2147483647])
        return f"$n.i {op} {c}", lambda it: cmp(op, it["i"], c)
    if k == "u":
        op, c = r.choice(OPS), r.choice([0, 1, 7])
        return f"$n.u {op} {c}", lambda it: cmp(op, it["u"], c)
    if k == "ubig":
        op, c = r.choice(OPS), r.choice([9223372036854775807, 9223372036854775806])
        return f"$n.u {op} {c}", lambda it: cmp(op, it["u"], c)
    if k == "f":
        op, c = r.choice(OPS), r.choice([0.5, 1.0, 2.5, 100.0, 1])
        return f"$n.f {op} {c}", lambda it: cmp(op, None if it["f"] is None else f32(it["f"]), float(c))
    if k == "f01":
        # 0.1 and 0.2 are not exactly representable in F32: compared in F64 per docs
        op, c = r.choice(OPS), r.choice([0.1, 0.2])
        return f"$n.f {op} {c}", lambda it: cmp(op, None if it["f"] is None else f32(it["f"]), c)
    if k == "t":
        op, c = r.choice(OPS), r.choice(TS)
        return f'$n.t {op} datetime("{c}")', lambda it: cmp(op, None if it["t"] is None else ts_key(it["t"]), ts_key(c))
    op, c = r.choice(OPS), r.choice(["a", "b", "c"])
    return f'$n.g {op} "{c}"', lambda it: cmp(op, it["g"], c)


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


def expr(r, d=0):
    if d >= 2 or r.random() < .5:
        return atom(r)
    t1, f1 = expr(r, d + 1)
    t2, f2 = expr(r, d + 1)
    if r.random() < .5:
        return f"({t1}) and ({t2})", lambda it: and3(f1(it), f2(it))
    return f"({t1}) or ({t2})", lambda it: or3(f1(it), f2(it))


def dists(adj, start, maxh):
    d = {start: 0}
    q = deque([start])
    while q:
        x = q.popleft()
        if d[x] >= maxh:
            continue
        for y in adj.get(x, ()):
            if y not in d:
                d[y] = d[x] + 1
                q.append(y)
    return d


def reach(adj, start, lo, hi):
    d = dists(adj, start, hi)
    out = {y for y, k in d.items() if lo <= k <= hi and y != start}
    if lo <= 1 <= hi and start in adj.get(start, ()):
        out.add(start)
    return out


def case(seed):
    r = random.Random(seed)
    n = r.randint(1, 14)
    items = []
    for k in range(n):
        it = {"slug": f"n{k:02d}",
              "i": None if r.random() < .25 else r.choice([-5, 0, 1, 5, 2147483647, -2147483648]),
              "u": None if r.random() < .25 else r.choice(U64S),
              "f": None if r.random() < .25 else r.choice(F32S),
              "t": None if r.random() < .25 else r.choice(TS),
              "g": None if r.random() < .25 else r.choice(["a", "b", "c"])}
        items.append(it)
    edges = [(r.choice(items)["slug"], r.choice(items)["slug"]) for _ in range(r.randint(0, 2 * n))]
    d = os.path.join(ROOT, str(seed))
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    open(f"{d}/s.pg", "w").write(SCHEMA)
    lines = [json.dumps({"type": "N", "data": {k: v for k, v in it.items() if v is not None}}) for it in items]
    lines += [json.dumps({"edge": "E", "from": a, "to": b}) for a, b in edges]
    open(f"{d}/d.jsonl", "w").write("\n".join(lines) + "\n")
    g = f"{d}/g.omni"
    for args in (["init", "--schema", f"{d}/s.pg", g], ["load", "--data", f"{d}/d.jsonl", "--mode", "overwrite", "--target", g]):
        p = run(args)
        if p.returncode != 0:
            raise Mismatch(f"setup failed {args}: {p.stderr[-1500:]}")
    by = {it["slug"]: it for it in items}
    fwd, und = {}, {}
    for a, b in edges:
        fwd.setdefault(a, set()).add(b)
        und.setdefault(a, set()).add(b)
        und.setdefault(b, set()).add(a)
    checks = []
    for qi in range(10):
        t = r.choice(["filter", "filter", "hop", "uhop", "hopfilter", "cdist", "order2"])
        name = f"q{qi}"
        if t == "filter":
            gq, f = expr(r)
            src = f"query {name}() {{ match {{ $n: N\n {gq} }} return {{ $n.slug }} }}"
            exp = sorted(x["slug"] for x in items if f(x) is True)
            checks.append((name, src, exp, lambda rows: sorted(x["n.slug"] for x in rows)))
        elif t in ("hop", "uhop", "hopfilter"):
            lo = r.randint(1, 2)
            hi = r.randint(lo, 3)
            s = r.choice(items)["slug"]
            if t == "uhop":
                src = f'query {name}() {{ match {{ $a: N {{ slug: "{s}" }}\n $a <e>{{{lo},{hi}}} $b }} return {{ $b.slug }} }}'
                exp = sorted(reach(und, s, lo, hi))
            elif t == "hop":
                src = f'query {name}() {{ match {{ $a: N {{ slug: "{s}" }}\n $a e{{{lo},{hi}}} $b }} return {{ $b.slug }} }}'
                exp = sorted(reach(fwd, s, lo, hi))
            else:
                gq, f = expr(r)
                gq = gq.replace("$n.", "$b.")
                src = f'query {name}() {{ match {{ $a: N {{ slug: "{s}" }}\n $a e{{{lo},{hi}}} $b\n {gq} }} return {{ $b.slug }} }}'
                exp = sorted(x for x in reach(fwd, s, lo, hi) if f(by[x]) is True)
            checks.append((name, src, exp, lambda rows: sorted(x["b.slug"] for x in rows)))
        elif t == "cdist":
            fld = r.choice(["g", "i", "u", "t"])
            src = f"query {name}() {{ match {{ $a: N\n $a e $b }} return {{ count(distinct $b.{fld}) as c, count(distinct $b) as cn, count($b) as rows }} }}"
            pairs = set(edges)
            exp = {"c": len({by[b][fld] for a, b in pairs if by[b][fld] is not None}),
                   "cn": len({b for a, b in pairs}), "rows": len(pairs)}
            checks.append((name, src, exp, lambda rows: {k: rows[0].get(k) for k in ("c", "cn", "rows")} if rows else None))
        elif t == "order2":
            k1, k2 = r.sample(["g", "i", "u", "f", "t"], 2)
            d1, d2 = r.choice(["asc", "desc"]), r.choice(["asc", "desc"])
            lim = r.randint(1, 10)
            src = f"query {name}() {{ match {{ $n: N }} return {{ $n.slug }} order {{ $n.{k1} {d1}, $n.{k2} {d2} }} limit {lim} }}"

            def keyf(v, fld, desc):
                if fld == "t" and v is not None:
                    v = ts_key(v)
                if fld == "f" and v is not None:
                    v = f32(v)
                # asc: nulls first; desc: nulls last
                return (v is not None, v) if v is not None else (False, 0)

            rows = sorted(items, key=lambda x: x["slug"])
            # stable sorts from least significant: slug asc, k2, k1
            def sort_by(rows, fld, dirn):
                nn = [x for x in rows if x[fld] is not None]
                nl = [x for x in rows if x[fld] is None]
                nn = sorted(nn, key=lambda x: keyf(x[fld], fld, dirn)[1], reverse=(dirn == "desc"))
                return (nl + nn) if dirn == "asc" else (nn + nl)
            rows = sort_by(rows, k2, d2)
            rows = sort_by(rows, k1, d1)
            exp = [x["slug"] for x in rows[:lim]]
            checks.append((name, src, exp, lambda rows: [x["n.slug"] for x in rows]))
    qf = f"{d}/q.gq"
    open(qf, "w").write("\n\n".join(c[1] for c in checks) + "\n")
    for name, src, exp, proj in checks:
        p = run(["query", name, "--query", qf, "--json", "--target", g])
        if p.returncode != 0:
            raise Mismatch(f"{name} failed\n{src}\n{p.stderr[-1500:]}")
        got = proj(json.loads(p.stdout)["rows"])
        if got != exp:
            raise Mismatch(f"{name} mismatch\n{src}\nexpected={exp}\nactual={got}")
    shutil.rmtree(d, ignore_errors=True)


def main():
    a, c = int(sys.argv[1]), int(sys.argv[2])
    os.makedirs(ROOT, exist_ok=True)
    os.makedirs(FAIL, exist_ok=True)
    fails = 0
    for s in range(a, a + c):
        try:
            case(s)
            print(f"seed {s} ok", flush=True)
        except Mismatch as m:
            fails += 1
            print(f"seed {s} MISMATCH\n{m}\n", flush=True)
            open(f"{FAIL}/{s}.txt", "w").write(str(m))
        except Exception as e:
            import traceback
            fails += 1
            print(f"seed {s} ERROR {e}\n{traceback.format_exc()}", flush=True)
    print(f"done fails={fails}")


if __name__ == "__main__":
    main()
