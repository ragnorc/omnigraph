#!/usr/bin/env python3
"""Diff fuzzer: random writes on two branches with clean merges; after each
step record (commit id, model state). Then `omnigraph diff --from A --to B`
must equal the net change between the two model states."""
import json, os, random, shutil, subprocess, sys
sys.path.insert(0, os.path.dirname(__file__))
import mfuzz
from mfuzz import SCHEMA, Commit, lca, three_way, mutate_model, write_state_delta, copy_state, Mismatch

OG = mfuzz.OG
ROOT = "/tmp/og/dfuzzrun"
FAIL = "/tmp/og/dfuzzfail"


def head(g, br):
    p = subprocess.run([OG, "commit", "list", "--target", g, "--branch", br, "--json"], capture_output=True, text=True)
    j = json.loads(p.stdout)
    lst = j["commits"] if isinstance(j, dict) else j
    return lst[0]["graph_commit_id"]


def net(a, b):
    out = {}
    an, ae = a
    bn, be = b
    for kind, A, B in (("node", an, bn), ("edge", ae, be)):
        for k in set(A) | set(B):
            x, y = A.get(k), B.get(k)
            if x == y:
                continue
            ident = k if kind == "node" else json.dumps(k.split(">"), separators=(",", ":"))
            op = "insert" if x is None else "delete" if y is None else "update"
            out[(kind, ident)] = (op, x, y)
    return out


def actual(g, a, b):
    p = subprocess.run([OG, "diff", "--from", a, "--to", b, "--target", g, "--json"], capture_output=True, text=True)
    if p.returncode != 0:
        return None, p.stderr[-1500:]
    out = {}
    for c in json.loads(p.stdout)["changes"]:
        def img(side):
            if side not in c:
                return None
            pr = c[side]["properties"]
            if c["kind"] == "node":
                return {"a": pr.get("a"), "s": pr.get("s")}
            return {"w": pr.get("w")}
        key = (c["kind"], c["id"])
        if key in out:
            return None, f"duplicate change {key}"
        out[key] = (c["op"], img("before"), img("after"))
    return out, None


def case(seed):
    r = random.Random(seed)
    d = os.path.join(ROOT, str(seed))
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    open(f"{d}/schema.pg", "w").write(SCHEMA)
    g = f"{d}/g.omni"
    log = []

    def sh(args, ok=True):
        p = subprocess.run([OG] + (args + ["--target", g] if args[0] != "init" else args), capture_output=True, text=True)
        log.append(" ".join(args) + f" -> {p.returncode}")
        if ok and p.returncode != 0:
            raise Mismatch(f"cmd failed {args}: {p.stderr[-1500:]}")
        return p

    sh(["init", "--schema", f"{d}/schema.pg", g])
    heads = {"main": Commit([], ({}, {}))}
    st = mutate_model(r, heads["main"].state)
    write_state_delta(sh, d, "main", heads["main"].state, st, log)
    heads["main"] = Commit([heads["main"]], st)
    sh(["branch", "create", "feat", "--from", "main"])
    heads["feat"] = heads["main"]
    points = [(head(g, "main"), heads["main"].state)]
    for step in range(r.randint(3, 8)):
        if r.random() < 0.3:
            src, dst = r.choice([("feat", "main"), ("main", "feat")])
            base, _ = lca(heads[src], heads[dst])
            nodes, nc = three_way(base.state[0], heads[dst].state[0], heads[src].state[0])
            edges, ec = three_way(base.state[1], heads[dst].state[1], heads[src].state[1])
            orph = [k for k in edges if k.split(">")[0] not in nodes or k.split(">")[1] not in nodes]
            if nc or ec or orph:
                continue
            sh(["branch", "merge", src, "--into", dst])
            if heads[src].state != base.state:
                heads[dst] = Commit([heads[dst], heads[src]], (nodes, edges))
            points.append((head(g, dst), heads[dst].state))
        else:
            br = r.choice(["main", "feat"])
            old = heads[br].state
            new = mutate_model(r, old)
            if new == old:
                continue
            write_state_delta(sh, d, br, old, new, log)
            heads[br] = Commit([heads[br]], new)
            points.append((head(g, br), new))
    for _ in range(8):
        (ca, sa), (cb, sb) = r.choice(points), r.choice(points)
        exp = net(sa, sb)
        got, err = actual(g, ca, cb)
        if err:
            if "schema_boundary" in err:
                continue
            raise Mismatch(f"diff {ca}..{cb} failed: {err}\nLOG:\n" + "\n".join(log))
        if got != exp:
            diff = {"missing": {str(k): v for k, v in exp.items() if k not in got},
                    "extra": {str(k): v for k, v in got.items() if k not in exp},
                    "wrong": {str(k): (exp[k], got[k]) for k in exp if k in got and exp[k] != got[k]}}
            raise Mismatch(f"diff {ca}..{cb} mismatch {json.dumps(diff)}\nLOG:\n" + "\n".join(log))
    shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
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
