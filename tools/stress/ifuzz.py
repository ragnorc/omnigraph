#!/usr/bin/env python3
"""Keyed writes through the id index (#1318). A keyed upsert whose rows are
all new now stages through the absence-proven insert writer, one whose rows
are all present as a known-present update, and an update whose `where` holds
`<key> = v` adds the derived id conjunct. The choice depends on the id
BTREE and how many fragments it leaves uncovered.

Each case builds a keyed node table and a keyed edge table, then runs random
merge loads (all new, all present, mixed, and ids deleted earlier coming
back), append loads, keyed insert mutations, updates by key alone and by key
with another condition, deletes, `index build` and `optimize`, so the id
index is sometimes complete, sometimes behind by one fragment and sometimes
by many. After every step a full export and point reads by key must equal
the model.

Usage: ifuzz.py <first seed> <count>    (OG: CLI binary)"""
import json, os, random, shutil, subprocess, sys

OG = os.environ.get("OG", "omnigraph")
FIRST, COUNT = int(sys.argv[1]), int(sys.argv[2])
ROOT = "/tmp/og/ifuzz"

SCHEMA = """node P {
  slug: String @key
  n: I64?
  s: String?
}
edge E: P -> P {
  w: I64?
  @key(@src, @dst)
}
"""


def run(args, g, inp=None):
    p = subprocess.run([OG] + args + ["--target", g], capture_output=True, text=True, input=inp)
    return p.returncode, p.stdout, p.stderr


def export(g):
    rc, out, err = run(["export", "--branch", "main"], g)
    if rc:
        raise RuntimeError("export: " + err[-500:])
    ps, es = {}, {}
    for line in out.splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        if d.get("type") == "P":
            x = d["data"]
            if x["slug"] in ps:
                ps[x["slug"]] = ("DUP",)
            else:
                ps[x["slug"]] = (x.get("n"), x.get("s"))
        elif d.get("edge") == "E":
            k = (d["from"], d["to"])
            es[k] = ("DUP",) if k in es else d["data"].get("w")
    return ps, es


def case(seed):
    r = random.Random(seed)
    d = os.path.join(ROOT, str(seed))
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    g = os.path.join(d, "g.omni")
    open(f"{d}/s.pg", "w").write(SCHEMA)
    rc, out, err = run(["init", "--schema", f"{d}/s.pg"], g)
    if rc:
        # init takes its address positionally
        p = subprocess.run([OG, "init", "--schema", f"{d}/s.pg", g], capture_output=True, text=True)
        if p.returncode:
            return [f"init: {p.stderr[-400:]}"]
    keys = [f"k{i:03d}" for i in range(r.choice([20, 60, 200]))]
    P, E = {}, {}
    log = []
    fails = []

    def val():
        return (None if r.random() < 0.15 else r.randint(-3, 9), None if r.random() < 0.15 else r.choice(["a", "b", "c"]))

    def load(rows, edges, mode):
        path = f"{d}/batch.jsonl"
        with open(path, "w") as f:
            for k, (n, s) in rows:
                data = {"slug": k, "n": n, "s": s}
                f.write(json.dumps({"type": "P", "data": data}) + "\n")
            for (a, b), w in edges:
                f.write(json.dumps({"edge": "E", "from": a, "to": b, "data": {"w": w}}) + "\n")
        return run(["load", "--data", path, "--mode", mode, "--json"], g)

    def mutate(src, params):
        return run(["mutate", "-e", src, "--params", json.dumps(params), "--json"], g)

    for step in range(r.randint(15, 40)):
        live = sorted(P)
        dead = sorted(set(keys) - set(P))
        op = r.choices(["merge", "append", "ins", "upd", "upd2", "updin", "del", "delw", "eins", "edel", "index", "opt"],
                       [24, 6, 8, 10, 6, 5, 7, 3, 8, 3, 8, 6])[0]
        desc = op
        if op in ("merge", "append"):
            if op == "append":
                comp = "new"
            else:
                comp = r.choice(["new", "present", "mixed", "revive"])
            n = r.randint(1, 12)
            if comp == "new" or (comp == "revive" and dead):
                pool = dead
            elif comp == "present":
                pool = live
            else:
                pool = keys
            if not pool:
                continue
            ks = r.sample(pool, min(n, len(pool)))
            rows = [(k, val()) for k in ks]
            edges = []
            if op == "merge" and r.random() < 0.4:
                ends = sorted(set(live) | set(ks))
                for _ in range(r.randint(1, 4)):
                    e = (r.choice(ends), r.choice(ends))
                    if e not in [x for x, _ in edges]:
                        edges.append((e, r.randint(0, 5)))
            desc = f"{op}:{comp}:{ks}:{edges}"
            rc, out, err = load(rows, edges, op)
            if rc == 0:
                for k, v in rows:
                    P[k] = v
                for e, w in edges:
                    E[e] = w
            elif op == "append" and "key_conflict" in (out + err):
                pass
            else:
                fails.append(f"step {step} {desc}: refused rc={rc} {(out + err)[-400:]}")
        elif op == "ins":
            k = r.choice(keys)
            n, s = val()
            desc = f"ins:{k}:{'present' if k in P else 'new'}"
            rc, out, err = mutate("query m($k: String, $n: I64?, $s: String?) { insert P { slug: $k, n: $n, s: $s } }", {"k": k, "n": n, "s": s})
            if rc == 0:
                P[k] = (n, s)
            else:
                fails.append(f"step {step} {desc}: refused {(out + err)[-400:]}")
        elif op == "upd":
            k = r.choice(keys)
            n = r.randint(-3, 9)
            desc = f"upd:{k}:{'present' if k in P else 'absent'}"
            rc, out, err = mutate("query m($k: String, $n: I64) { update P set { n: $n } where slug = $k }", {"k": k, "n": n})
            if rc == 0:
                if k in P:
                    P[k] = (n, P[k][1])
            else:
                fails.append(f"step {step} {desc}: refused {(out + err)[-400:]}")
        elif op == "upd2":
            k = r.choice(keys)
            x = r.choice(["a", "b", "c"])
            n = r.randint(-3, 9)
            desc = f"upd2:{k}:s={x}"
            rc, out, err = mutate("query m($k: String, $x: String, $n: I64) { update P set { n: $n } where slug = $k and s = $x }", {"k": k, "x": x, "n": n})
            if rc == 0:
                if k in P and P[k][1] == x:
                    P[k] = (n, P[k][1])
            else:
                fails.append(f"step {step} {desc}: refused {(out + err)[-400:]}")
        elif op == "updin":
            ks = r.sample(keys, r.randint(1, 5))
            sv = r.choice(["a", "b", "c", None])
            desc = f"updin:{ks}:{sv}"
            rc, out, err = mutate("query m($ks: [String], $s: String?) { update P set { s: $s } where slug in $ks }", {"ks": ks, "s": sv})
            if rc == 0:
                for k in ks:
                    if k in P:
                        P[k] = (P[k][0], sv)
            else:
                fails.append(f"step {step} {desc}: refused {(out + err)[-400:]}")
        elif op == "del":
            k = r.choice(keys)
            desc = f"del:{k}"
            rc, out, err = mutate("query m($k: String) { delete P where slug = $k }", {"k": k})
            if rc == 0:
                P.pop(k, None)
                for e in [e for e in E if k in e]:
                    del E[e]
            else:
                fails.append(f"step {step} {desc}: refused {(out + err)[-400:]}")
        elif op == "delw":
            x = r.randint(-3, 9)
            desc = f"delw:n={x}"
            rc, out, err = mutate("query m($x: I64) { delete P where n = $x }", {"x": x})
            if rc == 0:
                gone = [k for k, v in P.items() if v[0] == x]
                for k in gone:
                    del P[k]
                    for e in [e for e in E if k in e]:
                        del E[e]
            else:
                fails.append(f"step {step} {desc}: refused {(out + err)[-400:]}")
        elif op == "eins":
            if not live:
                continue
            a, b = r.choice(live), r.choice(live)
            w = r.randint(0, 5)
            desc = f"eins:{a}->{b}:{'present' if (a, b) in E else 'new'}"
            rc, out, err = mutate("query m($a: String, $b: String, $w: I64) { insert E { from: $a, to: $b, w: $w } }", {"a": a, "b": b, "w": w})
            if rc == 0:
                E[(a, b)] = w
            else:
                fails.append(f"step {step} {desc}: refused {(out + err)[-400:]}")
        elif op == "edel":
            if not E:
                continue
            a, b = r.choice(sorted(E))
            desc = f"edel:{a}->{b}"
            rc, out, err = mutate("query m($a: String, $b: String) { delete E where @src = $a and @dst = $b }", {"a": a, "b": b})
            if rc == 0:
                del E[(a, b)]
            else:
                fails.append(f"step {step} {desc}: refused {(out + err)[-400:]}")
        elif op == "index":
            rc, out, err = run(["index", "build", "--json"], g)
            if rc:
                fails.append(f"step {step} index build: {(out + err)[-300:]}")
        elif op == "opt":
            rc, out, err = run(["optimize", "--json"], g)
            if rc:
                fails.append(f"step {step} optimize: {(out + err)[-300:]}")
        log.append(desc)
        try:
            ps, es = export(g)
        except Exception as e:
            fails.append(f"step {step} {desc}: {e}")
            break
        if ps != P:
            diff = {k: (P.get(k), ps.get(k)) for k in set(P) | set(ps) if P.get(k) != ps.get(k)}
            fails.append(f"step {step} {desc}: nodes differ (want, got) {dict(list(diff.items())[:6])}")
        if es != E:
            diff = {f"{k}": (E.get(k), es.get(k)) for k in set(E) | set(es) if E.get(k) != es.get(k)}
            fails.append(f"step {step} {desc}: edges differ (want, got) {dict(list(diff.items())[:6])}")
        # point reads by key, through the id index when it exists
        for k in r.sample(keys, min(4, len(keys))):
            rc, out, err = run(["query", "-e", "query q($k: String) { match { $p: P { slug: $k } } return { $p.slug, $p.n, $p.s } }",
                                "--params", json.dumps({"k": k}), "--json"], g)
            if rc:
                fails.append(f"step {step} read {k}: {err[-300:]}")
                continue
            rows = json.loads(out)["rows"]
            got = [(x.get("p.n"), x.get("p.s")) for x in rows]
            want = [P[k]] if k in P else []
            if got != want:
                fails.append(f"step {step} {desc}: point read {k} want {want} got {got}")
        if fails:
            break
    if fails:
        json.dump({"seed": seed, "log": log, "fails": fails}, open(f"{d}/fail.json", "w"), indent=1)
    else:
        shutil.rmtree(d, ignore_errors=True)
    return fails


def main():
    bad = 0
    for seed in range(FIRST, FIRST + COUNT):
        f = case(seed)
        if f:
            bad += 1
            print("FAIL seed", seed, f[:3], flush=True)
        else:
            print("ok", seed, flush=True)
    print(json.dumps({"first": FIRST, "count": COUNT, "failed": bad}))


if __name__ == "__main__":
    main()
