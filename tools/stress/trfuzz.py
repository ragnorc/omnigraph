#!/usr/bin/env python3
"""Traversal answers at scale, for the streamed edge probe of #1460.

Each seed loads thousands of nodes and up to about 150,000 edges over many
commits, with `index build` between some of them and sometimes edge deletes
and `optimize` afterwards, so a hop's probe meets indexed and unindexed
fragments and spans many probe batches. Uniform or hub-skewed endpoints.
Queries: one hop out and in from a bound node, bounded hops `{lo,hi}` to a
bound target counted, and one hop from a filtered set of many starts. Every
answer is judged against a BFS model with shortest-distance semantics
(docs/user/queries/traversal.md).

Usage: OG=target/release/omnigraph python3 tools/stress/trfuzz.py <seed> <count>
"""
import json, os, random, shutil, subprocess, sys, time
from collections import deque

OG = os.environ.get("OG", "omnigraph")
ROOT = os.environ.get("FUZZ_ROOT", "/tmp/og/trfuzz")
FAIL = os.environ.get("FUZZ_FAIL", "/tmp/og/trfuzzfail")
NMIN = int(os.environ.get("TR_MIN", "2000"))
NMAX = int(os.environ.get("TR_MAX", "16000"))

SCHEMA = """node P {
  slug: String @key
  g: I32
}

edge K: P -> P
"""
QUERIES = """query out($s: String) { match { $a: P { slug: $s }
 $a k $b } return { $b.slug } }
query inn($s: String) { match { $a: P { slug: $s }
 $b k $a } return { $b.slug } }
query bnd1($s: String) { match { $t: P { slug: $s }
 $c k{1,2} $t } return { count($c) as n } }
query bnd2($s: String) { match { $t: P { slug: $s }
 $c k{1,3} $t } return { count($c) as n } }
query bnd3($s: String) { match { $t: P { slug: $s }
 $c k{2,3} $t } return { count($c) as n } }
query bnd4($s: String) { match { $t: P { slug: $s }
 $t k{1,4} $c } return { count($c) as n } }
query many($x: I32) { match { $a: P
 $a.g = $x
 $a k $b } return { $a.slug, $b.slug } }
query manyc($x: I32) { match { $a: P
 $a.g < $x
 $a k $b } return { count($b) as n } }
"""
BOUNDS = {"bnd1": (1, 2, "in"), "bnd2": (1, 3, "in"), "bnd3": (2, 3, "in"), "bnd4": (1, 4, "out")}


class Mismatch(Exception):
    pass


def run(args, timeout=900):
    return subprocess.run([OG] + args, capture_output=True, text=True, timeout=timeout)


def reach(adj, start, lo, hi):
    d = {start: 0}
    q = deque([start])
    while q:
        x = q.popleft()
        if d[x] >= hi:
            continue
        for y in adj.get(x, ()):
            if y not in d:
                d[y] = d[x] + 1
                q.append(y)
    out = {y for y, k in d.items() if lo <= k <= hi and y != start}
    if lo <= 1 <= hi and start in adj.get(start, ()):
        out.add(start)
    return out


def case(seed):
    r = random.Random(seed)
    d = os.path.join(ROOT, str(seed))
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    open(os.path.join(d, "schema.pg"), "w").write(SCHEMA)
    qf = os.path.join(d, "q.gq")
    open(qf, "w").write(QUERIES)
    g = os.path.join(d, "g.omni")
    log = [f"seed {seed}"]

    def sh(args, ok=True):
        full = args + ["--target", g] if args[0] != "init" else args
        t0 = time.time()
        p = run(full)
        log.append(" ".join(args)[:200] + f"  -> rc={p.returncode} {time.time() - t0:.1f}s")
        if ok and p.returncode != 0:
            raise Mismatch(f"command failed: {' '.join(args)[:300]}\n{p.stderr[-1500:]}{p.stdout[-1500:]}\nLOG:\n" + "\n".join(log))
        return p

    sh(["init", "--schema", os.path.join(d, "schema.pg"), g])
    n = r.randint(NMIN, NMAX)
    slugs = [f"p{i:05d}" for i in range(n)]
    gs = {s: r.randint(0, 99) for s in slugs}
    hubs = r.sample(slugs, min(n, r.randint(1, 20)))
    skew = r.random() < 0.5
    e = min(150000, int(n * r.uniform(1, 9)))

    def endpoint():
        if skew and r.random() < 0.3:
            return r.choice(hubs)
        return r.choice(slugs)

    edges = [(endpoint(), endpoint()) for _ in range(e)]
    for i in range(0, n, 8000):
        f = os.path.join(d, "n.jsonl")
        open(f, "w").write("\n".join(json.dumps({"type": "P", "data": {"slug": s, "g": gs[s]}}) for s in slugs[i:i + 8000]) + "\n")
        sh(["load", "--data", f, "--mode", "append"])
    i = 0
    while i < e:
        j = i + r.randint(500, 8000)
        f = os.path.join(d, "e.jsonl")
        open(f, "w").write("\n".join(json.dumps({"edge": "K", "from": a, "to": b}) for a, b in edges[i:j]) + "\n")
        sh(["load", "--data", f, "--mode", "append"])
        if r.random() < 0.15:
            sh(["index", "build"])
        i = j
    log.append(f"n={n} e={e} skew={skew}")
    # sometimes delete the edges of a few nodes, then optimize
    if r.random() < 0.4:
        victims = r.sample(slugs, 5)
        for v in victims:
            sh(["mutate", "-e", f'query m() {{ delete K where @src = "{v}" }}'])
        vs = set(victims)
        edges = [x for x in edges if x[0] not in vs]
        if r.random() < 0.5:
            sh(["optimize"])
        if r.random() < 0.5:
            sh(["index", "build"])
    adj, radj = {}, {}
    for a, b in edges:
        adj.setdefault(a, set()).add(b)
        radj.setdefault(b, set()).add(a)

    def q(name, params):
        p = sh(["query", name, "--query", qf, "--params", json.dumps(params), "--json"])
        return json.loads(p.stdout)["rows"]

    probes = r.sample(slugs, 4) + r.sample(hubs, min(2, len(hubs)))
    for s in probes:
        got = sorted(x["b.slug"] for x in q("out", {"s": s}))
        exp = sorted(adj.get(s, ()))
        if got != exp:
            raise Mismatch(f"out({s}): expected {len(exp)} got {len(got)}; missing {sorted(set(exp) - set(got))[:5]} extra {sorted(set(got) - set(exp))[:5]} dups {len(got) - len(set(got))}\nLOG:\n" + "\n".join(log))
        got = sorted(x["b.slug"] for x in q("inn", {"s": s}))
        exp = sorted(radj.get(s, ()))
        if got != exp:
            raise Mismatch(f"inn({s}): expected {len(exp)} got {len(got)}; missing {sorted(set(exp) - set(got))[:5]} extra {sorted(set(got) - set(exp))[:5]}\nLOG:\n" + "\n".join(log))
    for s in probes[:3]:
        for name, (lo, hi, direction) in BOUNDS.items():
            if hi >= 3 and e > 60000 and name != "bnd3":
                continue
            exp = len(reach(radj if direction == "in" else adj, s, lo, hi))
            rows = q(name, {"s": s})
            got = rows[0]["n"] if rows else 0
            if got != exp:
                raise Mismatch(f"{name}({s}) {direction} {{{lo},{hi}}}: expected {exp} got {got}\nLOG:\n" + "\n".join(log))
    for _ in range(2):
        x = r.randint(0, 99)
        got = sorted((row["a.slug"], row["b.slug"]) for row in q("many", {"x": x}))
        exp = sorted({(a, b) for a, b in edges if gs[a] == x})
        if got != exp:
            raise Mismatch(f"many({x}): expected {len(exp)} got {len(got)}; missing {sorted(set(exp) - set(got))[:5]} extra {sorted(set(got) - set(exp))[:5]} dups {len(got) - len(set(got))}\nLOG:\n" + "\n".join(log))
    x = r.randint(1, 99)
    rows = q("manyc", {"x": x})
    got = rows[0]["n"] if rows else 0
    exp = len({(a, b) for a, b in edges if gs[a] < x})
    if got != exp:
        raise Mismatch(f"manyc({x}): expected {exp} got {got}\nLOG:\n" + "\n".join(log))
    shutil.rmtree(d, ignore_errors=True)
    return f"n={n} e={len(edges)} skew={skew} cmds={len(log)}"


def main():
    start, count = int(sys.argv[1]), int(sys.argv[2])
    os.makedirs(ROOT, exist_ok=True)
    os.makedirs(FAIL, exist_ok=True)
    fails = 0
    for seed in range(start, start + count):
        t0 = time.time()
        try:
            summary = case(seed)
            print(f"seed {seed} ok {time.time() - t0:.0f}s {summary}", flush=True)
        except Mismatch as e:
            fails += 1
            print(f"seed {seed} MISMATCH\n{e}\n", flush=True)
            open(os.path.join(FAIL, f"{seed}.txt"), "w").write(str(e))
        except Exception as ex:
            fails += 1
            import traceback
            print(f"seed {seed} ERROR {ex}\n{traceback.format_exc()}", flush=True)
    print(f"done fails={fails}")


if __name__ == "__main__":
    main()
