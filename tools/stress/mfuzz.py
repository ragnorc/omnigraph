#!/usr/bin/env python3
"""Branch/merge fuzzer: random rounds of writes on two branches with merges in
both directions, checked against a three-way entity-level merge oracle."""
import json, os, random, shutil, subprocess, sys

OG = os.environ.get("OG", "omnigraph")
ROOT = os.environ.get("FUZZ_ROOT", "/tmp/og/mfuzzrun")
FAIL = os.environ.get("FUZZ_FAIL", "/tmp/og/mfuzzfail")

SCHEMA = """node Item {
  slug: String @key
  a: I64?
  s: String?
}

edge Rel: Item -> Item {
  w: I64?
  @key(@src, @dst)
}
"""
KEYS = ["k%d" % i for i in range(7)]


class Mismatch(Exception):
    pass


def run(args):
    return subprocess.run([OG] + args, capture_output=True, text=True, timeout=300)


class Commit:
    _n = 0

    def __init__(self, parents, state):
        Commit._n += 1
        self.id = Commit._n
        self.parents = parents
        self.state = state  # (nodes dict, edges dict)

    def ancestors(self):
        seen, stack = {}, [(self, 0)]
        while stack:
            c, dist = stack.pop()
            if c.id in seen and seen[c.id][1] <= dist:
                continue
            seen[c.id] = (c, dist)
            for p in c.parents:
                stack.append((p, dist + 1))
        return seen


def lca(a, b):
    aa, bb = a.ancestors(), b.ancestors()
    common = set(aa) & set(bb)
    # keep only commits not ancestor of another common commit
    best = []
    for cid in common:
        c = aa[cid][0]
        if not any(cid != o and cid in aa[o][0].ancestors() for o in common):
            best.append(c)
    best.sort(key=lambda c: (aa[c.id][1] + bb[c.id][1], -c.id))
    return best[0], len(best)


def copy_state(st):
    return ({k: dict(v) for k, v in st[0].items()}, {k: dict(v) for k, v in st[1].items()})


def three_way(base, ours, theirs):
    """Return (result_dict, conflicts) for one entity map."""
    out, conflicts = {}, []
    for k in set(base) | set(ours) | set(theirs):
        b, o, t = base.get(k), ours.get(k), theirs.get(k)
        if o == t:
            r = o
        elif o == b:
            r = t
        elif t == b:
            r = o
        else:
            if b is None:
                conflicts.append(("divergent_insert", k))
            elif o is None or t is None:
                conflicts.append(("delete_vs_update", k))
            else:
                conflicts.append(("divergent_update", k))
            continue
        if r is not None:
            out[k] = r
    return out, conflicts


def read_state(g, branch):
    q = ("query n() { match { $i: Item } return { $i } }\n"
         "query e() { match { $a: Item\n $a $r:rel $b } return { $a.slug, $b.slug as bs, $r.w } }\n")
    qf = os.path.join(os.path.dirname(g), "read.gq")
    open(qf, "w").write(q)
    p = run(["query", "n", "--query", qf, "--branch", branch, "--json", "--target", g])
    if p.returncode != 0:
        raise Mismatch(f"read n failed on {branch}: {p.stderr[-1500:]}")
    nodes = {}
    for row in json.loads(p.stdout)["rows"]:
        i = row["i"]
        nodes[i["slug"]] = {"a": i.get("a"), "s": i.get("s")}
    p = run(["query", "e", "--query", qf, "--branch", branch, "--json", "--target", g])
    if p.returncode != 0:
        raise Mismatch(f"read e failed on {branch}: {p.stderr[-1500:]}")
    edges = {}
    for row in json.loads(p.stdout)["rows"]:
        k = row["a.slug"] + ">" + row["bs"]
        if k in edges:
            raise Mismatch(f"duplicate keyed edge {k} on {branch}")
        edges[k] = {"w": row.get("r.w")}
    return nodes, edges


def case(seed):
    r = random.Random(seed)
    d = os.path.join(ROOT, str(seed))
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    open(os.path.join(d, "schema.pg"), "w").write(SCHEMA)
    g = os.path.join(d, "g.omni")
    log = []

    def sh(args, ok=True):
        p = run(args + ["--target", g] if args[0] != "init" else args)
        log.append(" ".join(args) + f"  -> rc={p.returncode}")
        if ok and p.returncode != 0:
            raise Mismatch(f"command failed: {' '.join(args)}\n{p.stderr[-2000:]}\nLOG:\n" + "\n".join(log))
        return p

    sh(["init", "--schema", os.path.join(d, "schema.pg"), g])
    heads = {"main": Commit([], ({}, {}))}
    # seed main
    st = copy_state(heads["main"].state)
    lines = []
    for k in r.sample(KEYS, 4):
        st[0][k] = {"a": r.randint(0, 3), "s": r.choice([None, "x", "y"])}
    for _ in range(3):
        a, b = r.choice(list(st[0])), r.choice(list(st[0]))
        st[1][a + ">" + b] = {"w": r.choice([None, 1, 2])}
    write_state_delta(sh, d, "main", heads["main"].state, st, log)
    heads["main"] = Commit([heads["main"]], st)
    sh(["branch", "create", "feat", "--from", "main"])
    heads["feat"] = heads["main"]

    for rnd in range(r.randint(2, 5)):
        for br in ("main", "feat"):
            if r.random() < 0.25:
                continue
            old = heads[br].state
            new = mutate_model(r, old)
            if new != old:
                write_state_delta(sh, d, br, old, new, log)
                heads[br] = Commit([heads[br]], new)
            check(g, br, heads[br].state, log, f"after write r{rnd}")
        src, dst = ("feat", "main") if r.random() < 0.6 else ("main", "feat")
        base, nbases = lca(heads[src], heads[dst])
        nodes, nc = three_way(base.state[0], heads[dst].state[0], heads[src].state[0])
        edges, ec = three_way(base.state[1], heads[dst].state[1], heads[src].state[1])
        conflicts = nc + ec
        orphans = [k for k in edges if k.split(">")[0] not in nodes or k.split(">")[1] not in nodes]
        p = sh(["branch", "merge", src, "--into", dst, "--json"], ok=False)
        if p.returncode == 0:
            out = json.loads(p.stdout)
            if conflicts or orphans:
                # engine may merge per property; accept only if result equals a property-level merge
                actual = read_state(g, dst)
                raise Mismatch(f"merge {src}->{dst} succeeded but oracle expected conflicts {conflicts} orphans {orphans}\n"
                               f"base={base.state}\nours={heads[dst].state}\ntheirs={heads[src].state}\nactual={actual}\nout={out}\nLOG:\n" + "\n".join(log))
            if heads[src].state == base.state or heads[src] is heads[dst]:
                pass  # already up to date
            if heads[src].state != base.state:
                heads[dst] = Commit([heads[dst], heads[src]], (nodes, edges))
            check(g, dst, heads[dst].state, log, f"after merge {src}->{dst} r{rnd} outcome={out.get('outcome') or out.get('merge') or ''}")
            check(g, src, heads[src].state, log, f"source after merge {src}->{dst}")
        else:
            if not conflicts and not orphans:
                raise Mismatch(f"merge {src}->{dst} failed but oracle expected success (nbases={nbases})\n{p.stderr[-2500:]}{p.stdout[-2500:]}\n"
                               f"base={base.state}\nours={heads[dst].state}\ntheirs={heads[src].state}\nLOG:\n" + "\n".join(log))
            check(g, dst, heads[dst].state, log, f"after refused merge {src}->{dst}")
            # resolve: make feat match main to continue cleanly
            return
    shutil.rmtree(d, ignore_errors=True)


def check(g, br, model, log, where):
    actual = read_state(g, br)
    if actual != (model[0], model[1]):
        mn, me = model
        an, ae = actual
        diff = {
            "nodes_missing": sorted(set(mn) - set(an)), "nodes_extra": sorted(set(an) - set(mn)),
            "nodes_changed": {k: (mn[k], an[k]) for k in set(mn) & set(an) if mn[k] != an[k]},
            "edges_missing": sorted(set(me) - set(ae)), "edges_extra": sorted(set(ae) - set(me)),
            "edges_changed": {k: (me[k], ae[k]) for k in set(me) & set(ae) if me[k] != ae[k]},
        }
        raise Mismatch(f"{where}: branch {br} state differs: {json.dumps(diff)}\nLOG:\n" + "\n".join(log))


def mutate_model(r, old):
    nodes, edges = copy_state(old)
    for _ in range(r.randint(1, 4)):
        op = r.choice(["ins", "upd", "upd", "del", "eins", "edel"])
        if op == "ins":
            k = r.choice(KEYS)
            if k in nodes:
                continue
            nodes[k] = {"a": r.randint(0, 3), "s": r.choice([None, "x", "y", "z"])}
        elif op == "upd" and nodes:
            k = r.choice(sorted(nodes))
            fld = r.choice(["a", "s"])
            nodes[k] = dict(nodes[k])
            nodes[k][fld] = r.randint(0, 3) if fld == "a" else r.choice(["x", "y", "z"])
        elif op == "del" and nodes:
            k = r.choice(sorted(nodes))
            del nodes[k]
            for ek in [e for e in edges if k in e.split(">")]:
                del edges[ek]
        elif op == "eins" and nodes:
            a, b = r.choice(sorted(nodes)), r.choice(sorted(nodes))
            ek = a + ">" + b
            edges[ek] = {"w": r.choice([1, 2, 3] if ek in edges else [None, 1, 2, 3])}
        elif op == "edel" and edges:
            del edges[r.choice(sorted(edges))]
    return nodes, edges


def write_state_delta(sh, d, br, old, new, log):
    on, oe = old
    nn, ne = new
    # deletes first: edges then nodes
    for k in sorted(set(oe) - set(ne)):
        a, b = k.split(">")
        if a in nn and b in nn:
            sh(["mutate", "-e", f'query m() {{ delete Rel where @src = "{a}" and @dst = "{b}" }}', "--branch", br])
    for k in sorted(set(on) - set(nn)):
        sh(["mutate", "-e", f'query m() {{ delete Item where slug = "{k}" }}', "--branch", br])
    lines = []
    for k, v in sorted(nn.items()):
        if on.get(k) != v:
            data = {"slug": k}
            data.update({f: x for f, x in v.items() if x is not None})
            if on.get(k) is not None and any(v[f] is None and on[k].get(f) is not None for f in v):
                # merge-load cannot null a property; use a mutation per nulled field
                pass
            lines.append(json.dumps({"type": "Item", "data": data}))
    for k, v in sorted(ne.items()):
        if oe.get(k) != v:
            a, b = k.split(">")
            lines.append(json.dumps({"edge": "Rel", "from": a, "to": b, "data": {} if v["w"] is None else {"w": v["w"]}}))
    if lines:
        f = os.path.join(d, "delta.jsonl")
        open(f, "w").write("\n".join(lines) + "\n")
        log.append("  delta: " + " | ".join(lines))
        sh(["load", "--data", f, "--mode", "merge", "--branch", br])


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
            print(f"seed {seed} MISMATCH\n{m}\n", flush=True)
            open(os.path.join(FAIL, f"{seed}.txt"), "w").write(str(m))
        except Exception as ex:
            fails += 1
            import traceback
            print(f"seed {seed} ERROR {ex}\n{traceback.format_exc()}", flush=True)
    print(f"done fails={fails}")


if __name__ == "__main__":
    main()
