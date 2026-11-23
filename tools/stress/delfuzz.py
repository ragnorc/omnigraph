#!/usr/bin/env python3
"""Node removal fuzzer for the bounded removal edge reads of #1444.

Each seed loads thousands of nodes and edges in several appends, with
`index build` between some of them, so an edge table holds fragments that a
BTREE on the endpoints covers and fragments it does not. It then removes
nodes in ways that reach the retained-edge check and the delete cascade:

- delete queries of one to three overlapping statements over two node types
  and two edge types, often removing more than one 8,192-id chunk;
- overwrite loads of the node type that omit some nodes, with and without the
  edge types in the batch;
- branch merges where the branch deletes nodes and main adds edges.

The model predicts the affected counts, the cascade, `@card` refusals and
orphan refusals. After every step a full `export` must equal the model.

Usage: OG=target/debug/omnigraph python3 tools/stress/delfuzz.py <seed> <count>
DF_MIN and DF_MAX set the node count range (default 9000..24000).
"""
import json, os, random, shutil, subprocess, sys, time

OG = os.environ.get("OG", "omnigraph")
ROOT = os.environ.get("FUZZ_ROOT", "/tmp/og/delfuzz")
FAIL = os.environ.get("FUZZ_FAIL", "/tmp/og/delfuzzfail")
NMIN = int(os.environ.get("DF_MIN", "9000"))
NMAX = int(os.environ.get("DF_MAX", "24000"))

SCHEMA = """node P {{
  slug: String @key
  g: I32
  n: I32?
}}

node C {{
  slug: String @key
}}

edge K: P -> P {{
  w: I32
}}

edge L: P -> C @card({lo}..3) {{
  @key(@src, @dst)
}}
"""


class Mismatch(Exception):
    pass


def run(args, timeout=900):
    return subprocess.run([OG] + args, capture_output=True, text=True, timeout=timeout)


class Model:
    def __init__(self):
        self.P = {}  # slug -> (g, n)
        self.C = set()
        self.K = {}  # w -> (src, dst)
        self.L = set()  # (src, dst)

    def copy(self):
        m = Model()
        m.P, m.C, m.K, m.L = dict(self.P), set(self.C), dict(self.K), set(self.L)
        return m

    def snapshot(self):
        return (dict(self.P), set(self.C), dict(self.K), set(self.L))


def read_export(g, branch):
    p = run(["export", "--target", g, "--branch", branch])
    if p.returncode != 0:
        raise Mismatch(f"export failed on {branch}: {p.stderr[-1500:]}")
    m = Model()
    seen_k = set()
    for line in p.stdout.splitlines():
        if not line.strip():
            continue
        o = json.loads(line)
        if o.get("type") == "P":
            d = o["data"]
            if d["slug"] in m.P:
                raise Mismatch(f"duplicate P {d['slug']} on {branch}")
            m.P[d["slug"]] = (d["g"], d.get("n"))
        elif o.get("type") == "C":
            m.C.add(o["data"]["slug"])
        elif o.get("edge") == "K":
            w = o["data"]["w"]
            if w in seen_k:
                raise Mismatch(f"duplicate K w={w} on {branch}")
            seen_k.add(w)
            m.K[w] = (o["from"], o["to"])
        elif o.get("edge") == "L":
            k = (o["from"], o["to"])
            if k in m.L:
                raise Mismatch(f"duplicate L {k} on {branch}")
            m.L.add(k)
    return m


def diff(model, actual):
    out = {}
    for name, a, b in (("P", model.P, actual.P), ("K", model.K, actual.K)):
        miss = sorted(set(a) - set(b))
        extra = sorted(set(b) - set(a))
        chg = sorted(k for k in set(a) & set(b) if a[k] != b[k])
        if miss or extra or chg:
            out[name] = {"missing": len(miss), "extra": len(extra), "changed": len(chg),
                         "missing_sample": miss[:8], "extra_sample": extra[:8],
                         "changed_sample": [(k, a[k], b[k]) for k in chg[:5]]}
    for name, a, b in (("C", model.C, actual.C), ("L", model.L, actual.L)):
        miss, extra = sorted(a - b), sorted(b - a)
        if miss or extra:
            out[name] = {"missing": len(miss), "extra": len(extra),
                         "missing_sample": miss[:8], "extra_sample": extra[:8]}
    return out


def card_ok(model, lo, touched):
    """Every surviving source node in `touched` keeps between lo and 3 L edges."""
    cnt = {}
    for s, _ in model.L:
        cnt[s] = cnt.get(s, 0) + 1
    for s in touched:
        if s in model.P and not (lo <= cnt.get(s, 0) <= 3):
            return False
    return True


def case(seed):
    r = random.Random(seed)
    d = os.path.join(ROOT, str(seed))
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    lo = 1 if r.random() < 0.4 else 0
    open(os.path.join(d, "schema.pg"), "w").write(SCHEMA.format(lo=lo))
    g = os.path.join(d, "g.omni")
    log = [f"seed {seed} lo={lo}"]

    def sh(args, ok=True, timeout=900):
        full = args + ["--target", g] if args[0] != "init" else args
        t0 = time.time()
        p = run(full, timeout)
        log.append(" ".join(a if len(a) < 200 else a[:200] + "..." for a in args)
                   + f"  -> rc={p.returncode} {time.time() - t0:.1f}s")
        if ok and p.returncode != 0:
            raise Mismatch(f"command failed: {' '.join(args)[:400]}\n{p.stderr[-2000:]}\nLOG:\n" + "\n".join(log))
        return p

    sh(["init", "--schema", os.path.join(d, "schema.pg"), g])
    n = r.randint(NMIN, NMAX)
    nc = r.randint(4, 40)
    m = Model()
    m.C = {f"c{i}" for i in range(nc)}
    slugs = [f"p{i:06d}" for i in range(n)]
    for s in slugs:
        m.P[s] = (r.randint(0, 63), None if r.random() < 0.2 else r.randint(0, 999))
    for s in slugs:
        k = r.randint(lo, 3) if r.random() < 0.9 else lo
        for c in r.sample(sorted(m.C), min(k, nc)):
            m.L.add((s, c))
    ne = int(n * r.uniform(0.5, 2.5))
    wnext = 0
    for _ in range(ne):
        a = r.choice(slugs)
        b = a if r.random() < 0.03 else r.choice(slugs)
        m.K[wnext] = (a, b)
        wnext += 1

    # load in several appends: C first, then chunks of P with their L, then K chunks,
    # with index builds between some of them
    def write_jsonl(path, lines):
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")

    write_jsonl(os.path.join(d, "c.jsonl"), [json.dumps({"type": "C", "data": {"slug": c}}) for c in sorted(m.C)])
    sh(["load", "--data", os.path.join(d, "c.jsonl"), "--mode", "append"])
    lby = {}
    for s, c in m.L:
        lby.setdefault(s, []).append(c)
    # a load holds at most 8,192 entities per type (documented), and a P brings up to 3 L
    def split(items, cap):
        out, i = [], 0
        while i < len(items):
            j = i + r.randint(max(1, cap // 4), cap)
            out.append(items[i:j])
            i = j
        return out

    parts = split(slugs, 2600)
    kitems = sorted(m.K.items())
    kparts = split(kitems, 8000)
    loaded = set()
    for i, part in enumerate(parts):
        lines = []
        for s in part:
            gv, nv = m.P[s]
            data = {"slug": s, "g": gv}
            if nv is not None:
                data["n"] = nv
            lines.append(json.dumps({"type": "P", "data": data}))
            for c in lby.get(s, []):
                lines.append(json.dumps({"edge": "L", "from": s, "to": c, "data": {}}))
        loaded.update(part)
        # K edges whose endpoints are both loaded already, from the next K part
        if kparts and r.random() < 0.6:
            kp = kparts[0]
            ready = [(w, e) for w, e in kp if e[0] in loaded and e[1] in loaded]
            if len(ready) == len(kp):
                kparts.pop(0)
                for w, (a, b) in kp:
                    lines.append(json.dumps({"edge": "K", "from": a, "to": b, "data": {"w": w}}))
        f = os.path.join(d, f"p{i}.jsonl")
        write_jsonl(f, lines)
        sh(["load", "--data", f, "--mode", "append"])
        if r.random() < 0.35:
            sh(["index", "build"])
    for i, kp in enumerate(kparts):
        f = os.path.join(d, f"k{i}.jsonl")
        write_jsonl(f, [json.dumps({"edge": "K", "from": a, "to": b, "data": {"w": w}}) for w, (a, b) in kp])
        sh(["load", "--data", f, "--mode", "append"])
        if r.random() < 0.35:
            sh(["index", "build"])
    log.append(f"loaded P={len(m.P)} C={len(m.C)} K={len(m.K)} L={len(m.L)} pchunks={len(parts)}")
    check(g, "main", m, log, "after load")

    heads = {"main": m}
    history = []  # (graph_commit_id, model) on main after each published write

    def remember(p):
        try:
            cid = (json.loads(p.stdout).get("commit") or {}).get("graph_commit_id")
        except Exception:
            cid = None
        if cid:
            history.append((cid, heads["main"]))

    def counts_at(cid):
        qf = os.path.join(d, "counts.gq")
        open(qf, "w").write(
            "query np() { match { $p: P } return { count($p) as n } }\n"
            "query nk() { match { $a: P\n $a $e:k $b } return { count($a) as n } }\n"
            "query nl() { match { $a: P\n $a $e:l $c } return { count($a) as n } }\n"
            "query ws() { match { $a: P\n $a $e:k $b } return { $e.w } }\n")
        out = []
        for name in ("np", "nk", "nl"):
            p = sh(["query", name, "--query", qf, "--snapshot", cid, "--json"])
            rows = json.loads(p.stdout)["rows"]
            out.append(rows[0]["n"] if rows else 0)
        p = sh(["query", "ws", "--query", qf, "--snapshot", cid, "--json"])
        out.append(sorted(row["e.w"] for row in json.loads(p.stdout)["rows"]))
        return out

    def gen_delete(model):
        """Return (gq text, new model, expected affected (nodes, edges), refused?)."""
        stmts, rp, rc, rk, rl = [], set(), set(), set(), set()
        for _ in range(r.randint(1, 3)):
            kind = r.choices(["pg", "pin", "pnull", "pn", "pslug", "c", "kw", "ksrc", "ldst"],
                             [4, 2, 1, 2, 1, 1, 2, 1, 1])[0]
            if kind == "pg":
                x = r.randint(1, 50)
                op = r.choice(["<", "<=", ">", ">=", "="])
                stmts.append(f"delete P where g {op} {x}")
                f = {"<": lambda v: v < x, "<=": lambda v: v <= x, ">": lambda v: v > x,
                     ">=": lambda v: v >= x, "=": lambda v: v == x}[op]
                rp |= {s for s, (gv, _) in model.P.items() if f(gv)}
            elif kind == "pin":
                xs = sorted(r.sample(range(64), r.randint(1, 20)))
                stmts.append(f"delete P where g in [{', '.join(map(str, xs))}]")
                rp |= {s for s, (gv, _) in model.P.items() if gv in xs}
            elif kind == "pnull":
                x = r.randint(0, 63)
                stmts.append(f"delete P where n is null and g < {x}")
                rp |= {s for s, (gv, nv) in model.P.items() if nv is None and gv < x}
            elif kind == "pn":
                x = r.randint(0, 999)
                if r.random() < 0.5:
                    stmts.append(f"delete P where n > {x}")
                    rp |= {s for s, (_, nv) in model.P.items() if nv is not None and nv > x}
                else:
                    stmts.append(f"delete P where not (n > {x})")
                    rp |= {s for s, (_, nv) in model.P.items() if nv is not None and not nv > x}
            elif kind == "pslug":
                pref = f"p{r.randint(0, n // 1000):03d}"
                stmts.append(f'delete P where slug starts_with "{pref}"')
                rp |= {s for s in model.P if s.startswith(pref)}
            elif kind == "c":
                cs = r.sample(sorted(model.C), min(len(model.C), r.randint(1, 3))) if model.C else ["cnone"]
                stmts.append("delete C where slug in [" + ", ".join(f'"{c}"' for c in cs) + "]")
                rc |= set(cs) & model.C
            elif kind == "kw":
                x = r.randint(0, max(1, wnext))
                stmts.append(f"delete K where w < {x}")
                rk |= {w for w in model.K if w < x}
            elif kind == "ksrc":
                s = r.choice(sorted(model.P)) if model.P else "pnone"
                stmts.append(f'delete K where @src = "{s}"')
                rk |= {w for w, (a, _) in model.K.items() if a == s}
            elif kind == "ldst":
                c = r.choice(sorted(model.C)) if model.C else "cnone"
                stmts.append(f'delete L where @dst = "{c}"')
                rl |= {e for e in model.L if e[1] == c}
        new = model.copy()
        for s in rp:
            del new.P[s]
        new.C -= rc
        rk |= {w for w, (a, b) in model.K.items() if a in rp or b in rp}
        rl |= {e for e in model.L if e[0] in rp or e[1] in rc}
        for w in rk:
            del new.K[w]
        new.L -= rl
        touched = {e[0] for e in rl}
        refused = lo > 0 and not card_ok(new, lo, touched)
        text = "query m() {\n  " + "\n  ".join(stmts) + "\n}"
        return text, new, (len(rp) + len(rc), len(rk) + len(rl)), refused

    for step in range(r.randint(2, 4)):
        op = r.choices(["delete", "overwrite", "merge", "optimize", "index"], [5, 2, 2, 1, 1])[0]
        cur = heads["main"]
        if op == "delete":
            text, new, (an, ae), refused = gen_delete(cur)
            open(os.path.join(d, f"del{step}.gq"), "w").write(text)
            log.append("  " + text.replace("\n", " "))
            p = sh(["mutate", "--query", os.path.join(d, f"del{step}.gq"), "m", "--json"], ok=False)
            if refused:
                if p.returncode == 0:
                    raise Mismatch(f"step {step}: delete succeeded but @card({lo}..3) should refuse it\n{p.stdout[-800:]}\nLOG:\n" + "\n".join(log))
                check(g, "main", cur, log, f"after refused delete step {step}")
            else:
                if p.returncode != 0:
                    raise Mismatch(f"step {step}: delete failed but the model expected success\n{p.stderr[-1000:]}{p.stdout[-1500:]}\nLOG:\n" + "\n".join(log))
                out = json.loads(p.stdout)
                got = (out.get("affected_nodes"), out.get("affected_edges"))
                log.append(f"  affected {got} expected {(an, ae)}")
                if got != (an, ae):
                    actual = read_export(g, "main")
                    raise Mismatch(f"step {step}: affected {got} != expected {(an, ae)}; state diff {json.dumps(diff(new, actual))}\nLOG:\n" + "\n".join(log))
                heads["main"] = new
                remember(p)
                check(g, "main", new, log, f"after delete step {step}")
        elif op == "overwrite":
            if not cur.P:
                continue
            frac = r.choice([0.01, 0.1, 0.4, 0.7])
            removed = set(r.sample(sorted(cur.P), int(len(cur.P) * frac)))
            with_k = r.random() < 0.4
            with_l = r.random() < 0.3
            # a type whose batch part is empty is absent from the batch and stays unchanged
            if with_k and not any(e[0] not in removed and e[1] not in removed for e in cur.K.values()):
                with_k = False
            if with_l and not any(e[0] not in removed for e in cur.L):
                with_l = False
            new = cur.copy()
            for s in removed:
                del new.P[s]
            lines = []
            for s, (gv, nv) in sorted(new.P.items()):
                data = {"slug": s, "g": gv}
                if nv is not None:
                    data["n"] = nv
                lines.append(json.dumps({"type": "P", "data": data}))
            orphan = False
            if with_k:
                new.K = {w: e for w, e in cur.K.items() if e[0] not in removed and e[1] not in removed}
                for w, (a, b) in sorted(new.K.items()):
                    lines.append(json.dumps({"edge": "K", "from": a, "to": b, "data": {"w": w}}))
            else:
                orphan |= any(a in removed or b in removed for a, b in cur.K.values())
            if with_l:
                new.L = {e for e in cur.L if e[0] not in removed}
                for a, b in sorted(new.L):
                    lines.append(json.dumps({"edge": "L", "from": a, "to": b, "data": {}}))
            else:
                orphan |= any(a in removed for a, _ in cur.L)
            f = os.path.join(d, f"ow{step}.jsonl")
            write_jsonl(f, lines)
            log.append(f"  overwrite removes {len(removed)} P, with_k={with_k} with_l={with_l}, orphan expected={orphan}")
            p = sh(["load", "--data", f, "--mode", "overwrite", "--json"], ok=False)
            if orphan:
                if p.returncode == 0:
                    actual = read_export(g, "main")
                    raise Mismatch(f"step {step}: overwrite that orphans retained edges succeeded; state diff vs pre {json.dumps(diff(cur, actual))}\nLOG:\n" + "\n".join(log))
                log.append("  refused: " + (p.stdout + p.stderr).strip().splitlines()[-1][:300] if (p.stdout + p.stderr).strip() else "")
                check(g, "main", cur, log, f"after refused overwrite step {step}")
            else:
                if p.returncode != 0:
                    raise Mismatch(f"step {step}: overwrite failed but the model expected success\n{p.stderr[-2000:]}{p.stdout[-1500:]}\nLOG:\n" + "\n".join(log))
                heads["main"] = new
                remember(p)
                check(g, "main", new, log, f"after overwrite step {step}")
        elif op == "merge":
            b = f"b{step}"
            sh(["branch", "create", b, "--from", "main"])
            text, bnew, _, refused = gen_delete(cur)
            open(os.path.join(d, f"bdel{step}.gq"), "w").write(text)
            log.append("  on branch: " + text.replace("\n", " "))
            p = sh(["mutate", "--query", os.path.join(d, f"bdel{step}.gq"), "m", "--branch", b, "--json"], ok=False)
            if refused or p.returncode != 0:
                if not refused:
                    raise Mismatch(f"step {step}: branch delete failed but the model expected success\n{p.stderr[-1000:]}{p.stdout[-1500:]}\nLOG:\n" + "\n".join(log))
                if p.returncode == 0:
                    raise Mismatch(f"step {step}: branch delete succeeded but @card should refuse it\nLOG:\n" + "\n".join(log))
                bnew = cur
            # main adds K edges, some to nodes the branch deleted
            mnew = cur.copy()
            adds = []
            ps = sorted(cur.P)
            for _ in range(r.randint(0, 30) if ps else 0):
                a, bb = r.choice(ps), r.choice(ps)
                mnew.K[wnext] = (a, bb)
                adds.append(json.dumps({"edge": "K", "from": a, "to": bb, "data": {"w": wnext}}))
                wnext += 1
            if adds:
                f = os.path.join(d, f"madd{step}.jsonl")
                write_jsonl(f, adds)
                sh(["load", "--data", f, "--mode", "append"])
            heads["main"] = mnew
            check(g, "main", mnew, log, f"main before merge step {step}")
            # three-way: base=cur, ours=mnew (only K inserts), theirs=bnew (deletes)
            res = bnew.copy()
            for w in set(mnew.K) - set(cur.K):
                res.K[w] = mnew.K[w]
            orph = any(a not in res.P or bb not in res.P for a, bb in res.K.values())
            if r.random() < 0.3:
                po = sh(["optimize"], ok=False)
                log.append(f"  optimize rc={po.returncode}")
            p = sh(["branch", "merge", b, "--into", "main", "--json"], ok=False)
            if orph:
                if p.returncode == 0:
                    actual = read_export(g, "main")
                    raise Mismatch(f"step {step}: merge with orphan edges succeeded; diff vs model-without-check {json.dumps(diff(res, actual))}\nLOG:\n" + "\n".join(log))
                check(g, "main", mnew, log, f"after refused merge step {step}")
            else:
                if p.returncode != 0:
                    raise Mismatch(f"step {step}: merge failed but the model expected success\n{p.stderr[-2000:]}{p.stdout[-1000:]}\nLOG:\n" + "\n".join(log))
                heads["main"] = res
                remember(p)
                log.append(f"  merged: branch removed {len(cur.P) - len(bnew.P)} P")
                check(g, "main", res, log, f"after merge step {step}")
        elif op == "optimize":
            sh(["optimize"])
            check(g, "main", cur, log, f"after optimize step {step}")
        elif op == "index":
            sh(["index", "build"])
    # historical reads: each remembered commit still answers its own state
    for cid, hm in history:
        got = counts_at(cid)
        exp = [len(hm.P), len(hm.K), len(hm.L), sorted(hm.K)]
        if got != exp:
            raise Mismatch(f"snapshot {cid}: counts P,K,L {got[:3]} != model {exp[:3]}; K w differ: missing {sorted(set(exp[3]) - set(got[3]))[:5]} extra {sorted(set(got[3]) - set(exp[3]))[:5]}\nLOG:\n" + "\n".join(log))
    log.append(f"history {len(history)} snapshots")
    shutil.rmtree(d, ignore_errors=True)
    return [x.strip() for x in log if x.strip().startswith(("affected", "overwrite", "merged", "history"))]


def check(g, br, model, log, where):
    actual = read_export(g, br)
    dd = diff(model, actual)
    if dd:
        raise Mismatch(f"{where}: branch {br} differs from the model: {json.dumps(dd)}\nLOG:\n" + "\n".join(log))


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
