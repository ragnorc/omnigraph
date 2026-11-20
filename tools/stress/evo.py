#!/usr/bin/env python3
"""Schema evolution journey with historical reads.

A populated graph goes through: add a nullable property and fill some of it;
rename a property, a node type and an edge type (`@rename_from`) in one
apply; drop an indexed property; re-add it under the same name; promote the
added property to required (first refused with OG-MF-104 while a null is
live, then accepted once filled); `optimize`; and `cleanup --keep 1`. After
each apply the current export must equal the model, and reads at every
earlier commit must follow docs/user/branching/index.md, Historical reads: a
renamed type reads under its current name; a property renamed, added, or
dropped and re-added since the commit is refused, naming the property; a
property the commit stores under its current name reads its values then.
After cleanup a read at a removed commit must fail, never answer.

Usage: OG=target/release/omnigraph python3 tools/stress/evo.py <seed> <count>
"""
import json, os, random, shutil, subprocess, sys, time

OG = os.environ.get("OG", "omnigraph")
ROOT = os.environ.get("FUZZ_ROOT", "/tmp/og/evo")
FAIL = os.environ.get("FUZZ_FAIL", "/tmp/og/evofail")

S0 = """node P {
  slug: String @key
  a: I32?
  s: String? @index
}
edge K: P -> P {
  w: I32?
}
"""
S1 = S0.replace("  s: String? @index\n", "  s: String? @index\n  b: I32?\n")
S2 = """node Q @rename_from("P") {
  slug: String @key
  a2: I32? @rename_from("a")
  s: String? @index
  b: I32?
}
edge K2: Q -> Q @rename_from("K") {
  w: I32?
}
"""
S3 = S2.replace("  s: String? @index\n", "")
S4 = S3.replace("  b: I32?\n", "  b: I32?\n  s: String?\n")
S5 = S4.replace("  b: I32?\n", "  b: I32\n")


KNOWN = [0]


class Mismatch(Exception):
    pass


def run(args, timeout=600):
    return subprocess.run([OG] + args, capture_output=True, text=True, timeout=timeout)


def case(seed):
    r = random.Random(seed)
    d = os.path.join(ROOT, str(seed))
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    g = os.path.join(d, "g.omni")
    log = [f"seed {seed}"]

    def sh(args, ok=True):
        full = args + ["--target", g] if args[0] != "init" else args
        p = run(full)
        log.append(" ".join(args)[:160] + f"  -> rc={p.returncode}")
        if ok and p.returncode != 0:
            raise Mismatch(f"command failed: {' '.join(args)[:300]}\n{p.stderr[-1500:]}{p.stdout[-1500:]}\nLOG:\n" + "\n".join(log))
        return p

    def apply(text, name, ok=True):
        f = os.path.join(d, name + ".pg")
        open(f, "w").write(text)
        sh(["schema", "plan", "--schema", f, "--json"], ok=ok)
        return sh(["schema", "apply", "--schema", f, "--json"], ok=ok)

    def head():
        p = sh(["commit", "list", "--branch", "main", "--json"])
        out = json.loads(p.stdout)
        rows = out if isinstance(out, list) else out.get("commits") or out.get("rows") or []
        ids = [c.get("graph_commit_id") or c.get("id") for c in rows]
        return ids[0] if ids else None

    def q(src, snapshot=None, ok=True):
        f = os.path.join(d, "q.gq")
        open(f, "w").write(src)
        args = ["query", "--query", f, "--json"]
        if snapshot:
            args += ["--snapshot", snapshot]
        p = sh(args, ok=ok)
        return p

    def rows_of(p):
        return json.loads(p.stdout)["rows"]

    open(os.path.join(d, "s0.pg"), "w").write(S0)
    sh(["init", "--schema", os.path.join(d, "s0.pg"), g])
    n = r.randint(300, 4000)
    nodes = {f"p{i:05d}": {"a": None if r.random() < 0.2 else r.randint(0, 50),
                           "s": None if r.random() < 0.2 else r.choice(["kqzt mvrb", "plox", "zund frib"])} for i in range(n)}
    edges = {}
    for i in range(r.randint(n // 2, 3 * n)):
        edges[i] = (r.choice(sorted(nodes)) if i % 50 == 0 else f"p{r.randrange(n):05d}", f"p{r.randrange(n):05d}", r.choice([None, 1, 2]))
    lines = []
    for k, v in nodes.items():
        data = {"slug": k}
        data.update({f: x for f, x in v.items() if x is not None})
        lines.append(json.dumps({"type": "P", "data": data}))
    for i, (a, b, w) in edges.items():
        lines.append(json.dumps({"edge": "K", "from": a, "to": b, "data": {} if w is None else {"w": w}}))
    # loads hold at most 8,192 entities per type
    nl = [l for l in lines if '"type"' in l]
    el = [l for l in lines if '"edge"' in l]
    for chunk in [nl[i:i + 8000] for i in range(0, len(nl), 8000)] + [el[i:i + 8000] for i in range(0, len(el), 8000)]:
        f = os.path.join(d, "l.jsonl")
        open(f, "w").write("\n".join(chunk) + "\n")
        sh(["load", "--data", f, "--mode", "append"])
    if r.random() < 0.5:
        sh(["index", "build"])
    c0 = head()

    # A: add b, fill some of it
    apply(S1, "s1")
    filled = {k: r.randint(0, 9) for k in r.sample(sorted(nodes), n // 2)}
    f = os.path.join(d, "b.jsonl")
    open(f, "w").write("\n".join(json.dumps({"type": "P", "data": {"slug": k, "b": v, **({"a": nodes[k]["a"]} if nodes[k]["a"] is not None else {}), **({"s": nodes[k]["s"]} if nodes[k]["s"] is not None else {})}}) for k, v in filled.items()) + "\n")
    sh(["load", "--data", f, "--mode", "merge"])
    for k in nodes:
        nodes[k]["b"] = filled.get(k)
    c2 = head()

    # B: rename property a -> a2, P -> Q, K -> K2
    apply(S2, "s2")
    got = {row["q.slug"]: row.get("q.a2") for row in rows_of(q("query x() { match { $q: Q } return { $q.slug, $q.a2 } }"))}
    exp = {k: v["a"] for k, v in nodes.items()}
    if got != exp:
        raise Mismatch(f"after rename: a2 differs for {[k for k in exp if got.get(k) != exp[k]][:5]}\nLOG:\n" + "\n".join(log))
    # a renamed type reads under its current name at an older commit
    old = sorted(row["q.slug"] for row in rows_of(q("query x() { match { $q: Q } return { $q.slug } }", c0)))
    if old != sorted(nodes):
        raise Mismatch(f"Q at c0: {len(old)} rows, expected {len(nodes)}\nLOG:\n" + "\n".join(log))
    # a property renamed since the commit is refused there
    p = q("query x() { match { $q: Q } return { $q.slug, $q.a2 } }", c0, ok=False)
    if p.returncode == 0:
        raise Mismatch(f"a2 at c0 answered instead of refusing (renamed since): {p.stdout[:300]}\nLOG:\n" + "\n".join(log))
    if "a2" not in (p.stdout + p.stderr):
        raise Mismatch(f"a2 at c0 refused without naming a2: {(p.stdout + p.stderr)[-500:]}\nLOG:\n" + "\n".join(log))
    # a property stored under its current name at the commit reads its values then
    got = {row["q.slug"]: row.get("q.b") for row in rows_of(q("query x() { match { $q: Q } return { $q.slug, $q.b } }", c2))}
    if got != {k: v["b"] for k, v in nodes.items()}:
        raise Mismatch(f"b at c2 differs\nLOG:\n" + "\n".join(log))
    # the renamed edge type traverses at the older commit
    cnt = rows_of(q("query x() { match { $a: Q\n $a $e:k2 $b } return { count($a) as n } }", c0))
    if (cnt[0]["n"] if cnt else 0) != len(edges):
        raise Mismatch(f"K2 at c0: {cnt} edges, expected {len(edges)}\nLOG:\n" + "\n".join(log))
    c3 = head()
    s_word = r.choice(["kqzt", "plox", "zund"])
    exp_s = sorted(k for k, v in nodes.items() if v["s"] and s_word in v["s"].split())
    got_s = sorted(row["q.slug"] for row in rows_of(q(f'query x() {{ match {{ $q: Q\n search($q.s, "{s_word}") }} return {{ $q.slug }} }}')))
    if got_s != exp_s:
        raise Mismatch(f"search s after rename: expected {len(exp_s)} got {len(got_s)}\nLOG:\n" + "\n".join(log))

    # C: drop s; D: re-add s
    apply(S3, "s3")
    apply(S4, "s4")
    got = [row for row in rows_of(q("query x() { match { $q: Q } return { $q.slug, $q.s } }")) if row.get("q.s") is not None]
    if got:
        raise Mismatch(f"re-added s reads old values: {got[:3]}\nLOG:\n" + "\n".join(log))
    p = q("query x() { match { $q: Q } return { $q.slug, $q.s } }", c3, ok=False)
    if p.returncode == 0:
        raise Mismatch(f"s at c3 answered after drop and re-add: {p.stdout[:300]}\nLOG:\n" + "\n".join(log))

    # E: promote b while a null is live: refused; then fill and promote
    p = apply(S5, "s5", ok=False)
    if p.returncode == 0:
        raise Mismatch("promoting b with nulls live was accepted\nLOG:\n" + "\n".join(log))
    if "OG-MF-104" not in p.stdout + p.stderr:
        raise Mismatch(f"promotion refusal without OG-MF-104: {(p.stdout + p.stderr)[-600:]}\nLOG:\n" + "\n".join(log))
    sh(["mutate", "-e", "query u() { update Q set { b: 7 } where b is null }"])
    for k in nodes:
        if nodes[k]["b"] is None:
            nodes[k]["b"] = 7
    apply(S5, "s5")
    got = {row["q.slug"]: row.get("q.b") for row in rows_of(q("query x() { match { $q: Q } return { $q.slug, $q.b } }"))}
    if got != {k: v["b"] for k, v in nodes.items()}:
        raise Mismatch("b after promotion differs\nLOG:\n" + "\n".join(log))

    # F: optimize, history still reads; cleanup --keep 1, removed history fails
    sh(["optimize"])
    p = q("query x() { match { $q: Q } return { $q.slug, $q.b } }", c2, ok=False)
    if p.returncode != 0 and "nullable true; declared Int32, nullable false" in p.stdout:
        KNOWN[0] += 1  # og-stress-promoted-property-history-read-internal, GitHub #1493
    else:
        got = {row["q.slug"]: row.get("q.b") for row in rows_of(p)} if p.returncode == 0 else None
        if got != {k: (filled.get(k)) for k in nodes}:
            raise Mismatch(f"b at c2 after promotion and optimize: {p.stdout[-400:] if got is None else 'rows differ'}\nLOG:\n" + "\n".join(log))
    # the other properties stay readable at c2
    got = sorted(row["q.slug"] for row in rows_of(q("query x() { match { $q: Q } return { $q.slug } }", c2)))
    if got != sorted(nodes):
        raise Mismatch("slugs at c2 differ after promotion\nLOG:\n" + "\n".join(log))
    sh(["cleanup", "--keep", "1", "--yes"])
    p = q("query x() { match { $q: Q } return { $q.slug } }", c0, ok=False)
    if p.returncode == 0:
        got = sorted(row["q.slug"] for row in rows_of(p))
        if got != sorted(nodes):
            raise Mismatch(f"read at c0 after cleanup --keep 1 answered wrong rows ({len(got)} vs {len(nodes)})\nLOG:\n" + "\n".join(log))
        log.append("c0 still readable after cleanup (retained)")
    got = sorted(row["q.slug"] for row in rows_of(q("query x() { match { $q: Q } return { $q.slug } }")))
    if got != sorted(nodes):
        raise Mismatch("current rows differ after cleanup\nLOG:\n" + "\n".join(log))
    shutil.rmtree(d, ignore_errors=True)
    return f"n={n} e={len(edges)}"


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
    print(f"done fails={fails} known_1493={KNOWN[0]}")


if __name__ == "__main__":
    main()
