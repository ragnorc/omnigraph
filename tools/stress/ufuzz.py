#!/usr/bin/env python3
"""Update-patch fuzzer. Random merge loads (every property supplied), partial
`update`s of scalar, list, Blob and vector properties, deletes, `optimize` and
`index build`, on main and on a branch that is merged back. Each round the
branch and main touch disjoint keys, so the merged state is the union. After
every write the full `export` of the branch must equal the model, Blob bytes
and vectors included. Targets the column-patching update path (#1272), the
update admission charge (#1304) and fragment-scoped merges (#1229)."""
import base64, json, os, random, shutil, subprocess, sys

OG = os.environ.get("OG", "omnigraph")
ROOT = "/tmp/og/ufuzzrun"
FAIL = "/tmp/og/ufuzzfail"
SCHEMA = """node D {
  slug: String @key
  n: I64?
  s: String?
  f: F64?
  tags: [String]?
  b: Blob?
  c: Blob?
  v: Vector(4)? @index
}
"""
FIELDS = ["n", "s", "f", "tags", "b", "c", "v"]
GQTYPE = {"n": "I64", "s": "String", "f": "F64", "tags": "[String]", "b": "Blob", "c": "Blob", "v": "Vector(4)"}


class Mismatch(Exception):
    pass


def run(args, timeout=300):
    return subprocess.run([OG] + args, capture_output=True, text=True, timeout=timeout)


def rand_val(r, f, big=False):
    if r.random() < 0.15:
        return None
    if f == "n":
        return r.randint(0, 9)
    if f == "s":
        return r.choice(["a", "b", "c", "dd", "e"])
    if f == "f":
        return r.choice([0.5, 1.25, 2.0, 3.75])
    if f == "tags":
        return r.sample(["x", "y", "z", "w"], r.randint(0, 3))
    if f in ("b", "c"):
        size = r.choice([0, 3, 100, 5000] + ([300000] if big else []))
        return "base64:" + base64.b64encode(bytes(r.getrandbits(8) for _ in range(size))).decode()
    if f == "v":
        return [float(r.randint(-4, 4)) / 2 for _ in range(4)]


def export(g, br):
    p = run(["export", "--branch", br, "--target", g], timeout=600)
    if p.returncode != 0:
        raise Mismatch(f"export failed on {br}: {p.stderr[-2000:]}")
    out = {}
    for line in p.stdout.splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("type") != "D":
            continue
        d = rec["data"]
        out[d["slug"]] = {f: d.get(f) for f in FIELDS}
    return out


def norm(st):
    res = {}
    for k, row in st.items():
        r2 = {}
        for f in FIELDS:
            v = row.get(f)
            if f == "tags" and v == []:
                v = v  # an empty list is a value, keep it
            if f == "v" and v is not None:
                v = [float(x) for x in v]
            r2[f] = v
        res[k] = r2
    return res


def diff(model, got):
    lines = []
    for k in sorted(set(model) | set(got)):
        if k not in got:
            lines.append(f"missing {k}")
        elif k not in model:
            lines.append(f"extra {k}: {str(got[k])[:200]}")
        else:
            for f in FIELDS:
                if model[k][f] != got[k][f]:
                    lines.append(f"{k}.{f}: model={str(model[k][f])[:80]} got={str(got[k][f])[:80]}")
    return lines


def write(r, g, d, br, st, keys, log, big):
    st = {k: dict(v) for k, v in st.items()}
    kind = r.choice(["load", "update", "update", "update", "update_where", "delete"])
    if kind == "load" or not any(k in st for k in keys):
        lines = []
        for k in r.sample(keys, r.randint(1, min(4, len(keys)))):
            row = {f: rand_val(r, f, big) for f in FIELDS}
            st[k] = row
            data = {"slug": k, **row}
            lines.append(json.dumps({"type": "D", "data": data}))
        open(f"{d}/w.jsonl", "w").write("\n".join(lines) + "\n")
        p = run(["load", "--data", f"{d}/w.jsonl", "--mode", "merge", "--branch", br, "--target", g])
        desc = f"load {br} keys=" + ",".join(json.loads(l)["data"]["slug"] for l in lines)
        return st, desc, p, f"{d}/w.jsonl"
    live = [k for k in keys if k in st]
    if kind in ("update", "update_where"):
        fs = r.sample(FIELDS, r.randint(1, 3))
        vals = {f: rand_val(r, f, big) for f in fs}
        if kind == "update":
            ids = r.sample(live, r.randint(1, min(3, len(live))))
            match = ids
            pred = "slug in $ids"
        else:
            lim = r.randint(0, 9)
            match = [k for k in live if st[k]["n"] is not None and st[k]["n"] >= lim]
            pred = "n >= $lim and slug in $ids"
            ids = live
        for k in match:
            for f in fs:
                st[k][f] = vals[f]
        sig = ", ".join([f"${f}: {GQTYPE[f]}?" for f in fs] + ["$ids: [String]"] + (["$lim: I64"] if kind == "update_where" else []))
        sets = ", ".join(f"{f}: ${f}" for f in fs)
        src = f"query m({sig}) {{ update D set {{ {sets} }} where {pred} }}"
        params = {**vals, "ids": ids}
        if kind == "update_where":
            params["lim"] = lim
        open(f"{d}/p.json", "w").write(json.dumps(params))
        p = run(["mutate", "-e", src, "--params-file", f"{d}/p.json", "--branch", br, "--target", g])
        return st, f"{kind} {br} set {fs} ids={ids if kind == 'update' else match} :: {src}", p, f"{d}/p.json"
    ids = r.sample(live, r.randint(1, min(2, len(live))))
    for k in ids:
        del st[k]
    open(f"{d}/p.json", "w").write(json.dumps({"ids": ids}))
    src = "query m($ids: [String]) { delete D where slug in $ids }"
    p = run(["mutate", "-e", src, "--params-file", f"{d}/p.json", "--branch", br, "--target", g])
    return st, f"delete {br} {ids}", p, None


def maint(r, g, log):
    op = r.choice(["optimize", "index"])
    if op == "optimize":
        p = run(["optimize", "--target", g], timeout=600)
    else:
        p = run(["index", "build", "--target", g], timeout=600)
    log.append(f"{op} rc={p.returncode} {p.stderr.strip()[-300:] if p.returncode else ''}")
    return p


def case(seed):
    r = random.Random(seed)
    d = os.path.join(ROOT, str(seed))
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    g = os.path.join(d, "g.omni")
    open(f"{d}/s.pg", "w").write(SCHEMA)
    p = run(["init", "--schema", f"{d}/s.pg", g])
    if p.returncode:
        raise Exception("init " + p.stderr)
    keys = [f"k{i}" for i in range(r.randint(4, 14))]
    big = r.random() < 0.2
    main = {}
    log = []
    for rnd in range(r.randint(2, 4)):
        use_branch = r.random() < 0.7
        if use_branch:
            br = f"b{rnd}"
            p = run(["branch", "create", "--from", "main", br, "--target", g])
            if p.returncode:
                raise Exception("branch create " + p.stderr)
            log.append(f"branch create {br}")
            r.shuffle(keys)
            half = len(keys) // 2
            bkeys, mkeys = keys[:half], keys[half:]
            bst = dict(main)
            for _ in range(r.randint(2, 6)):
                side = r.choice(["b", "m"])
                if side == "b":
                    new, desc, p, _ = write(r, g, d, br, bst, bkeys, log, big)
                else:
                    new, desc, p, _ = write(r, g, d, "main", main, mkeys, log, big)
                log.append(f"{desc} rc={p.returncode} {p.stderr.strip()[-300:] if p.returncode else ''}")
                if p.returncode == 0:
                    if side == "b":
                        bst = new
                    else:
                        main = new
                if r.random() < 0.15:
                    maint(r, g, log)
                chk_br, chk_st = (br, bst) if side == "b" else ("main", main)
                got = export(g, chk_br)
                dd = diff(norm(chk_st), norm(got))
                if dd:
                    raise Mismatch("\n".join(log) + "\nDIFF on " + chk_br + "\n" + "\n".join(dd[:30]))
            # merged state: main keys from main, branch keys from branch
            merged = {k: v for k, v in main.items() if k in mkeys}
            merged.update({k: v for k, v in bst.items() if k in bkeys})
            p = run(["branch", "merge", br, "--into", "main", "--json", "--target", g], timeout=600)
            log.append(f"merge {br} rc={p.returncode} {(p.stdout + p.stderr).strip()[-400:]}")
            if p.returncode:
                raise Mismatch("\n".join(log) + "\nmerge of disjoint keys refused")
            main = merged
            got = export(g, "main")
            dd = diff(norm(main), norm(got))
            if dd:
                raise Mismatch("\n".join(log) + "\nDIFF after merge\n" + "\n".join(dd[:30]))
        else:
            for _ in range(r.randint(2, 5)):
                new, desc, p, _ = write(r, g, d, "main", main, keys, log, big)
                log.append(f"{desc} rc={p.returncode} {p.stderr.strip()[-300:] if p.returncode else ''}")
                if p.returncode == 0:
                    main = new
                if r.random() < 0.2:
                    maint(r, g, log)
                got = export(g, "main")
                dd = diff(norm(main), norm(got))
                if dd:
                    raise Mismatch("\n".join(log) + "\nDIFF on main\n" + "\n".join(dd[:30]))
    rejected = [l for l in log if " rc=1" in l or " rc=2" in l]
    shutil.rmtree(d, ignore_errors=True)
    return rejected


def main_():
    start, count = int(sys.argv[1]), int(sys.argv[2])
    os.makedirs(ROOT, exist_ok=True)
    os.makedirs(FAIL, exist_ok=True)
    fails = 0
    for seed in range(start, start + count):
        try:
            rej = case(seed)
            print(f"seed {seed} ok" + (f" rejected={len(rej)}: {rej[0][:300]}" if rej else ""), flush=True)
        except Mismatch as m:
            fails += 1
            print(f"seed {seed} MISMATCH\n{m}\n", flush=True)
            open(os.path.join(FAIL, f"{seed}.txt"), "w").write(str(m))
        except Exception as ex:
            fails += 1
            import traceback
            print(f"seed {seed} ERROR {ex}\n{traceback.format_exc()}", flush=True)
    print(f"done fails={fails}")


if __name__ == "__main__":
    main_()
