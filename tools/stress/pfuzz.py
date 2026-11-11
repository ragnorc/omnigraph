#!/usr/bin/env python3
"""Schema promotion fuzzer (#721, #1271). Builds a random history on a node
type with nullable properties (keyed upserts, column-patch updates, deletes,
re-inserts, optimize, branches merged and deleted), then applies a schema that
makes a property required and/or adds a @unique. The apply must refuse with
OG-MF-104 iff a live row holds a null in a promoted property, with OG-MF-108
iff two live rows share a complete non-null unique key, and succeed otherwise.
Deleted rows and earlier versions of updated rows must not count. After the
apply, the rows must be unchanged, later writes must be checked against the new
rule, and a read at the first commit must still work."""
import json, os, random, shutil, subprocess, sys

OG = os.environ.get("OG", "omnigraph")
ROOT = "/tmp/og/pfuzzrun"
FAIL = "/tmp/og/pfuzzfail"
VALS_E = ["a", "b", "c", None]
VALS_N = [1, 2, 3, None]


class Mismatch(Exception):
    pass


def run(args, timeout=300):
    return subprocess.run([OG] + args, capture_output=True, text=True, timeout=timeout)


def schema(req_e=False, req_n=False, uniq=None, rename=False):
    e = "e2" if rename else "e"
    lines = ["node P {", "  slug: String @key"]
    lines.append(f"  {e}: String{'' if req_e else '?'}" + (' @rename_from("e")' if rename else ""))
    lines.append(f"  n: I64{'' if req_n else '?'}")
    lines.append("  m: String?")
    if uniq == "e":
        lines.append(f"  @unique({e})")
    elif uniq == "en":
        lines.append(f"  @unique({e}, n)")
    elif uniq == "n":
        lines.append("  @unique(n)")
    lines.append("}")
    return "\n".join(lines) + "\n"


def export(g):
    p = run(["export", "--target", g])
    if p.returncode:
        raise Mismatch("export failed " + p.stderr[-1000:])
    out = {}
    for line in p.stdout.splitlines():
        if line.strip():
            rec = json.loads(line)
            d = rec["data"]
            e = d.get("e", d.get("e2"))
            out[d["slug"]] = {"e": e, "n": d.get("n"), "m": d.get("m")}
    return out


def write(r, g, d, br, st, keys):
    st = {k: dict(v) for k, v in st.items()}
    kind = r.choice(["insert", "insert", "upd_e", "upd_e", "upd_n", "upd_m", "delete"])
    live = sorted(st)
    if kind == "insert" or not live:
        k = r.choice(keys)
        row = {"e": r.choice(VALS_E), "n": r.choice(VALS_N), "m": r.choice(["x", None])}
        st[k] = row
        params = {"slug": k, **row}
        src = "query m($slug: String, $e: String?, $n: I64?, $m: String?) { insert P { slug: $slug, e: $e, n: $n, m: $m } }"
    elif kind.startswith("upd"):
        f = kind[4:]
        ids = r.sample(live, r.randint(1, min(2, len(live))))
        v = r.choice({"e": VALS_E, "n": VALS_N, "m": ["x", "y", None]}[f])
        for k in ids:
            st[k][f] = v
        t = {"e": "String?", "n": "I64?", "m": "String?"}[f]
        params = {"v": v, "ids": ids}
        src = f"query m($v: {t}, $ids: [String]) {{ update P set {{ {f}: $v }} where slug in $ids }}"
    else:
        ids = r.sample(live, 1)
        for k in ids:
            del st[k]
        params = {"ids": ids}
        src = "query m($ids: [String]) { delete P where slug in $ids }"
    open(f"{d}/p.json", "w").write(json.dumps(params))
    p = run(["mutate", "-e", src, "--params-file", f"{d}/p.json", "--branch", br, "--target", g])
    return st, f"{kind} {br} {json.dumps(params)}", p


def expected(st, req_e, req_n, uniq):
    if req_e and any(v["e"] is None for v in st.values()):
        return "OG-MF-104"
    if req_n and any(v["n"] is None for v in st.values()):
        return "OG-MF-104"
    if uniq:
        cols = {"e": ["e"], "en": ["e", "n"], "n": ["n"]}[uniq]
        seen = set()
        for v in st.values():
            key = tuple(v[c] for c in cols)
            if any(x is None for x in key):
                continue
            if key in seen:
                return "OG-MF-108"
            seen.add(key)
    return None


def case(seed):
    r = random.Random(seed)
    d = os.path.join(ROOT, str(seed))
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    g = os.path.join(d, "g.omni")
    open(f"{d}/s0.pg", "w").write(schema())
    p = run(["init", "--schema", f"{d}/s0.pg", g])
    if p.returncode:
        raise Exception("init " + p.stderr)
    keys = [f"k{i}" for i in range(r.randint(2, 7))]
    st = {}
    log = []
    first_commit = None
    for step in range(r.randint(3, 14)):
        if r.random() < 0.15:
            br = f"b{step}"
            run(["branch", "create", "--from", "main", br, "--target", g])
            bst = st
            for _ in range(r.randint(1, 3)):
                new, desc, p = write(r, g, d, br, bst, keys)
                log.append(f"{desc} rc={p.returncode}")
                if p.returncode == 0:
                    bst = new
            p = run(["branch", "merge", br, "--into", "main", "--target", g])
            log.append(f"merge {br} rc={p.returncode} {p.stderr.strip()[-200:] if p.returncode else ''}")
            if p.returncode == 0:
                st = bst
            p = run(["branch", "delete", br, "--target", g, "--yes"])
            log.append(f"branch delete {br} rc={p.returncode} {p.stderr.strip()[-200:] if p.returncode else ''}")
            if p.returncode:
                raise Exception("branch delete failed " + p.stderr)
        else:
            new, desc, p = write(r, g, d, "main", st, keys)
            log.append(f"{desc} rc={p.returncode} {p.stderr.strip()[-200:] if p.returncode else ''}")
            if p.returncode == 0:
                st = new
        if first_commit is None:
            c = run(["commit", "list", "--target", g, "--branch", "main", "--json"])
            try:
                cl = json.loads(c.stdout)
                items = cl if isinstance(cl, list) else cl.get("commits", [])
                if items:
                    first_commit = items[0].get("graph_commit_id") or items[0].get("id")
            except Exception:
                pass
        if r.random() < 0.15:
            p = run(["optimize", "--target", g], timeout=600)
            log.append(f"optimize rc={p.returncode}")
    got = export(g)
    if got != st:
        raise Mismatch("\n".join(log) + f"\npre-apply state differs\nmodel={st}\ngot={got}")
    req_e = r.random() < 0.5
    req_n = r.random() < 0.25
    uniq = r.choice([None, None, "e", "en", "n"])
    if not (req_e or req_n or uniq):
        req_e = True
    rename = r.random() < 0.2
    open(f"{d}/s1.pg", "w").write(schema(req_e, req_n, uniq, rename))
    want = expected(st, req_e, req_n, uniq)
    p = run(["schema", "apply", "--schema", f"{d}/s1.pg", "--target", g, "--json"], timeout=600)
    out = p.stdout + p.stderr
    desc = f"apply req_e={req_e} req_n={req_n} uniq={uniq} rename={rename} rc={p.returncode}"
    log.append(desc + " " + out.strip()[-500:])
    ctx = "\n".join(log) + f"\nstate={st}"
    if want is None and p.returncode != 0:
        raise Mismatch(ctx + f"\napply refused but no live row breaks the rule")
    if want is not None:
        if p.returncode == 0:
            raise Mismatch(ctx + f"\napply accepted but expected {want}")
        if want not in out and not (want == "OG-MF-104" and "OG-MF-108" in out) and not (want == "OG-MF-108" and "OG-MF-104" in out):
            raise Mismatch(ctx + f"\napply refused without {want}")
    got = export(g)
    if got != st:
        raise Mismatch(ctx + f"\npost-apply rows differ\ngot={got}")
    if p.returncode == 0:
        e = "e2" if rename else "e"
        # later writes are checked against the new rule
        if req_e:
            k = "znew"
            src = f'query m() {{ insert P {{ slug: "{k}", n: 1 }} }}'
            q = run(["mutate", "-e", src, "--target", g])
            if q.returncode == 0:
                raise Mismatch(ctx + "\ninsert without the required property accepted after promotion")
        if uniq in ("e", "en") and st:
            live = [v for v in st.values() if v["e"] is not None and (uniq == "e" or v["n"] is not None)]
            if live:
                v = live[0]
                nfield = f", n: {v['n']}" if v["n"] is not None else (", n: 1" if req_n else "")
                src = f'query m() {{ insert P {{ slug: "zdup", {e}: "{v["e"]}"{nfield} }} }}'
                q = run(["mutate", "-e", src, "--target", g])
                if q.returncode == 0:
                    raise Mismatch(ctx + "\nduplicate insert accepted after adding @unique\n" + src)
        if first_commit:
            q = run(["query", "-e", "query q() { match { $p: P } return { $p.slug } }", "--snapshot", first_commit, "--target", g, "--json"])
            if q.returncode:
                raise Mismatch(ctx + "\nread at first commit failed after promotion: " + q.stderr[-500:])
        # optimize and read again
        q = run(["optimize", "--target", g], timeout=600)
        if q.returncode:
            raise Mismatch(ctx + "\noptimize after promotion failed: " + q.stderr[-500:])
        got2 = export(g)
        if {k: v for k, v in got2.items() if not k.startswith("z")} != st:
            raise Mismatch(ctx + f"\nrows differ after optimize\ngot={got2}")
    shutil.rmtree(d, ignore_errors=True)
    return want


def main():
    start, count = int(sys.argv[1]), int(sys.argv[2])
    os.makedirs(ROOT, exist_ok=True)
    os.makedirs(FAIL, exist_ok=True)
    fails = 0
    tally = {}
    for seed in range(start, start + count):
        try:
            w = case(seed)
            tally[w] = tally.get(w, 0) + 1
            print(f"seed {seed} ok {w}", flush=True)
        except Mismatch as m:
            fails += 1
            print(f"seed {seed} MISMATCH\n{m}\n", flush=True)
            open(os.path.join(FAIL, f"{seed}.txt"), "w").write(str(m))
        except Exception as ex:
            fails += 1
            import traceback
            print(f"seed {seed} ERROR {ex}\n{traceback.format_exc()}", flush=True)
    print(f"done fails={fails} outcomes={tally}")


if __name__ == "__main__":
    main()
