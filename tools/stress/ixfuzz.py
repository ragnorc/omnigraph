#!/usr/bin/env python3
"""Index-served reads after column-patch updates.

ufuzz2.py patches indexed columns but judges only `export`, which reads no
index. This fuzzer judges the reads an index serves: full-text `search` on a
free-text `@index` String, and equality and range filters on a BTREE `@index`
I32, after updates that patch exactly those columns (the type keeps a vector
and a Blob unassigned, so the column-patch route is open), merge loads,
deletes, appends, `index build` and `optimize`, on main and on a branch that
is merged back. Each answer must equal the model's row set.

The words are made-up lower-case tokens that the analyzer's stemmer leaves
whole, so one query word matches exactly the rows whose body holds it.

Usage: OG=target/debug/omnigraph python3 tools/stress/ixfuzz.py <seed> <count>
IX_MIN and IX_MAX set the row count range (default 200..3000).
"""
import json, os, random, shutil, subprocess, sys, time

OG = os.environ.get("OG", "omnigraph")
ROOT = os.environ.get("FUZZ_ROOT", "/tmp/og/ixfuzz")
FAIL = os.environ.get("FUZZ_FAIL", "/tmp/og/ixfuzzfail")
NMIN = int(os.environ.get("IX_MIN", "200"))
NMAX = int(os.environ.get("IX_MAX", "3000"))

SCHEMA = """node D {
  slug: String @key
  body: String? @index
  k: I32? @index
  t: I32?
  v: Vector(4)?
  b: Blob?
}
"""
WORDS = ["kqzt", "mvrb", "plox", "zund", "frib", "quav", "drex", "wolm", "jent", "xarp", "blik", "stov"]

QUERIES = """query s($q: String) { match { $d: D
 search($d.body, $q) } return { $d.slug } }
query keq($x: I32) { match { $d: D { k: $x } } return { $d.slug } }
query klt($x: I32) { match { $d: D
 $d.k < $x } return { $d.slug } }
query kge($x: I32) { match { $d: D
 $d.k >= $x } return { $d.slug } }
query knull() { match { $d: D
 $d.k is null } return { $d.slug } }
query both($q: String, $x: I32) { match { $d: D
 search($d.body, $q)
 $d.k < $x } return { $d.slug } }
"""


class Mismatch(Exception):
    pass


def run(args, timeout=600):
    return subprocess.run([OG] + args, capture_output=True, text=True, timeout=timeout)


def body(r):
    if r.random() < 0.1:
        return None
    return " ".join(r.choice(WORDS) for _ in range(r.randint(1, 5)))


def kval(r):
    return None if r.random() < 0.1 else r.randint(0, 40)


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
        log.append(" ".join(a if len(a) < 160 else a[:160] + "..." for a in args)
                   + f"  -> rc={p.returncode} {time.time() - t0:.1f}s")
        if ok and p.returncode != 0:
            raise Mismatch(f"command failed: {' '.join(args)[:300]}\n{p.stderr[-1500:]}{p.stdout[-1500:]}\nLOG:\n" + "\n".join(log))
        return p

    sh(["init", "--schema", os.path.join(d, "schema.pg"), g])
    n = r.randint(NMIN, NMAX)
    model = {"main": {}}
    nextid = [0]

    def new_rows(cnt):
        rows = {}
        for _ in range(cnt):
            s = f"d{nextid[0]:06d}"
            nextid[0] += 1
            rows[s] = {"body": body(r), "k": kval(r), "t": r.randint(0, 5)}
        return rows

    def load(br, rows, mode):
        lines = []
        for s, v in sorted(rows.items()):
            data = {"slug": s, "t": v["t"], "v": [0.5, 1.0, 1.5, 2.0], "b": "base64:AAEC"}
            if v["body"] is not None:
                data["body"] = v["body"]
            if v["k"] is not None:
                data["k"] = v["k"]
            lines.append(json.dumps({"type": "D", "data": data}))
        f = os.path.join(d, "batch.jsonl")
        open(f, "w").write("\n".join(lines) + "\n")
        sh(["load", "--data", f, "--mode", mode, "--branch", br])

    # initial appends with index builds between some of them
    left = n
    while left > 0:
        cnt = min(left, r.randint(50, max(51, n // 2)))
        rows = new_rows(cnt)
        load("main", rows, "append")
        model["main"].update(rows)
        left -= cnt
        if r.random() < 0.5:
            sh(["index", "build"])
    check(sh, qf, "main", model["main"], r, log, "after load")

    branch = None
    for step in range(r.randint(4, 10)):
        br = branch if branch and r.random() < 0.5 else "main"
        m = model[br]
        op = r.choices(["upd_body", "upd_k", "upd_both", "merge", "delete", "append", "index", "optimize", "branch", "mergeback"],
                       [4, 4, 1, 2, 1, 1, 2, 1, 1, 1])[0]
        if op in ("upd_body", "upd_k", "upd_both") and m:
            # select by t (unindexed) or by k (indexed) or by slug list
            sel = r.choice(["t", "k", "slugs"])
            if sel == "t":
                x = r.randint(0, 5)
                where, match = f"t = {x}", [s for s, v in m.items() if v["t"] == x]
            elif sel == "k":
                x = r.randint(0, 40)
                where, match = f"k < {x}", [s for s, v in m.items() if v["k"] is not None and v["k"] < x]
            else:
                ss = r.sample(sorted(m), min(len(m), r.randint(1, 30)))
                where = "slug in [" + ", ".join(f'"{s}"' for s in ss) + "]"
                match = ss
            sets = {}
            if op in ("upd_body", "upd_both"):
                sets["body"] = r.choice(WORDS) + " " + r.choice(WORDS)
            if op in ("upd_k", "upd_both"):
                sets["k"] = r.randint(0, 40)
            assign = ", ".join(f'{f}: "{v}"' if isinstance(v, str) else f"{f}: {v}" for f, v in sets.items())
            sh(["mutate", "-e", f"query u() {{ update D set {{ {assign} }} where {where} }}", "--branch", br])
            for s in match:
                m[s] = dict(m[s], **sets)
        elif op == "merge" and m:
            rows = {}
            for s in r.sample(sorted(m), min(len(m), r.randint(1, 200))):
                rows[s] = {"body": body(r), "k": kval(r), "t": m[s]["t"]}
            rows.update(new_rows(r.randint(0, 50)))
            load(br, rows, "merge")
            m.update(rows)
        elif op == "delete" and m:
            x = r.randint(0, 40)
            sh(["mutate", "-e", f"query x() {{ delete D where k = {x} }}", "--branch", br])
            for s in [s for s, v in m.items() if v["k"] == x]:
                del m[s]
        elif op == "append":
            rows = new_rows(r.randint(1, 300))
            load(br, rows, "append")
            m.update(rows)
        elif op == "index":
            sh(["index", "build", "--branch", br])
        elif op == "optimize":
            sh(["optimize"])
        elif op == "branch" and branch is None:
            branch = f"feat{step}"
            sh(["branch", "create", branch, "--from", "main"])
            model[branch] = {s: dict(v) for s, v in model["main"].items()}
            model["base"] = {s: dict(v) for s, v in model["main"].items()}
            continue
        elif op == "mergeback" and branch is not None:
            # three-way at row level (entity-level conflicts)
            base, ours, theirs = model["base"], model["main"], model[branch]
            keys = set(base) | set(ours) | set(theirs)
            res, conflict = {}, False
            for k in keys:
                b, o, t = base.get(k), ours.get(k), theirs.get(k)
                if o == t:
                    v = o
                elif o == b:
                    v = t
                elif t == b:
                    v = o
                else:
                    # entity-level: both sides changed the same row differently (docs/user/branching/merge.md)
                    conflict = True
                    break
                if v is not None:
                    res[k] = v
            p = sh(["branch", "merge", branch, "--into", "main", "--json"], ok=False)
            if p.returncode == 0:
                if conflict:
                    raise Mismatch("merge succeeded where both sides changed one row differently\nLOG:\n" + "\n".join(log))
                model["main"] = res
                model.pop(branch)
                model.pop("base")
                branch = None
                check(sh, qf, "main", model["main"], r, log, f"after mergeback step {step}")
            else:
                if not conflict:
                    raise Mismatch(f"merge refused where the model saw no conflict:\n{p.stdout[-1500:]}\nLOG:\n" + "\n".join(log))
                return finish(d)
            continue
        else:
            continue
        check(sh, qf, br, m, r, log, f"after {op} step {step} on {br}")
    for br in list(model):
        if br != "base":
            check(sh, qf, br, model[br], r, log, "final", thorough=True)
    finish(d)


def finish(d):
    shutil.rmtree(d, ignore_errors=True)


def export_rows(sh, br):
    p = sh(["export", "--branch", br])
    out = {}
    for line in p.stdout.splitlines():
        if line.strip():
            o = json.loads(line)
            dd = o["data"]
            out[dd["slug"]] = {"body": dd.get("body"), "k": dd.get("k"), "t": dd.get("t")}
    return out


def q(sh, qf, br, name, params):
    p = sh(["query", name, "--query", qf, "--branch", br, "--params", json.dumps(params), "--json"])
    return sorted(row["d.slug"] for row in json.loads(p.stdout)["rows"])


def check(sh, qf, br, m, r, log, where, thorough=False):
    exp_rows = export_rows(sh, br)
    if exp_rows != m:
        miss = sorted(set(m) - set(exp_rows))
        extra = sorted(set(exp_rows) - set(m))
        chg = [(k, m[k], exp_rows[k]) for k in sorted(set(m) & set(exp_rows)) if m[k] != exp_rows[k]]
        raise Mismatch(f"{where}: export differs: missing {miss[:5]} extra {extra[:5]} changed {chg[:5]}\nLOG:\n" + "\n".join(log))
    words = WORDS if thorough else r.sample(WORDS, 3)
    for w in words:
        exp = sorted(s for s, v in m.items() if v["body"] is not None and w in v["body"].split())
        got = q(sh, qf, br, "s", {"q": w})
        if got != exp:
            raise Mismatch(f"{where}: search({w}) on {br}: missing {sorted(set(exp) - set(got))[:10]} extra {sorted(set(got) - set(exp))[:10]} "
                           f"(expected {len(exp)}, got {len(got)}); model of first missing/extra: "
                           f"{[(s, m.get(s)) for s in (sorted(set(exp) ^ set(got))[:3])]}\nLOG:\n" + "\n".join(log))
    xs = range(0, 41) if thorough else r.sample(range(0, 41), 3)
    for x in xs:
        for name, f in (("keq", lambda v: v == x), ("klt", lambda v: v < x), ("kge", lambda v: v >= x)):
            exp = sorted(s for s, v in m.items() if v["k"] is not None and f(v["k"]))
            got = q(sh, qf, br, name, {"x": x})
            if got != exp:
                raise Mismatch(f"{where}: {name}({x}) on {br}: missing {sorted(set(exp) - set(got))[:10]} extra {sorted(set(got) - set(exp))[:10]} "
                               f"(expected {len(exp)}, got {len(got)})\nLOG:\n" + "\n".join(log))
        if not thorough:
            break
    exp = sorted(s for s, v in m.items() if v["k"] is None)
    got = q(sh, qf, br, "knull", {})
    if got != exp:
        raise Mismatch(f"{where}: knull on {br}: expected {len(exp)} got {len(got)}\nLOG:\n" + "\n".join(log))
    w, x = r.choice(WORDS), r.randint(0, 40)
    exp = sorted(s for s, v in m.items() if v["body"] is not None and w in v["body"].split() and v["k"] is not None and v["k"] < x)
    got = q(sh, qf, br, "both", {"q": w, "x": x})
    if got != exp:
        raise Mismatch(f"{where}: both({w},{x}) on {br}: missing {sorted(set(exp) - set(got))[:10]} extra {sorted(set(got) - set(exp))[:10]}\nLOG:\n" + "\n".join(log))


def main():
    start, count = int(sys.argv[1]), int(sys.argv[2])
    os.makedirs(ROOT, exist_ok=True)
    os.makedirs(FAIL, exist_ok=True)
    fails = 0
    for seed in range(start, start + count):
        t0 = time.time()
        try:
            case(seed)
            print(f"seed {seed} ok {time.time() - t0:.0f}s", flush=True)
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
