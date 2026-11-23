#!/usr/bin/env python3
"""Change-feed fuzzer: baseline main, then random writes on main and merges
from a feature branch; poll the feed from the stored cursor at random points
and apply each block to a replica. The replica must equal main after every poll."""
import json, os, random, shutil, subprocess, sys
sys.path.insert(0, os.path.dirname(__file__))
from mfuzz import SCHEMA, Commit, lca, three_way, mutate_model, write_state_delta, Mismatch, read_state

OG = os.environ.get("OG", "omnigraph")
ROOT = "/tmp/og/cfuzzrun"
FAIL = "/tmp/og/cfuzzfail"


def apply_blocks(rep, blocks):
    nodes, edges = rep
    for b in blocks:
        for c in b.get("changes", []):
            if c["kind"] == "node":
                key = c["id"]
                tgt = nodes
                img = (lambda p: {"a": p.get("a"), "s": p.get("s")})
            else:
                ep = (c.get("after") or c.get("before"))["endpoints"]
                key = ep["from"] + ">" + ep["to"]
                tgt = edges
                img = (lambda p: {"w": p.get("w")})
            if c["op"] == "delete":
                if key not in tgt:
                    raise Mismatch(f"feed deletes absent {c['kind']} {key}")
                del tgt[key]
            elif c["op"] == "insert":
                if key in tgt:
                    raise Mismatch(f"feed inserts present {c['kind']} {key}")
                tgt[key] = img(c["after"]["properties"])
            else:
                if key not in tgt:
                    raise Mismatch(f"feed updates absent {c['kind']} {key}")
                tgt[key] = img(c["after"]["properties"])


def case(seed):
    r = random.Random(seed)
    d = f"{ROOT}/{seed}"
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    open(f"{d}/schema.pg", "w").write(SCHEMA)
    g = f"{d}/g.omni"
    log = []

    def sh(args, ok=True):
        p = subprocess.run([OG] + (args + ["--target", g] if args[0] != "init" else args), capture_output=True, text=True)
        log.append(" ".join(args)[:200] + f" -> {p.returncode}")
        if ok and p.returncode != 0:
            raise Mismatch(f"cmd failed {args}: {p.stderr[-1500:]}\nLOG:\n" + "\n".join(log))
        return p

    sh(["init", "--schema", f"{d}/schema.pg", g])
    heads = {"main": Commit([], ({}, {}))}
    st = mutate_model(r, heads["main"].state)
    write_state_delta(sh, d, "main", heads["main"].state, st, log)
    heads["main"] = Commit([heads["main"]], st)
    sh(["branch", "create", "feat", "--from", "main"])
    heads["feat"] = heads["main"]
    p = sh(["changes", "baseline", "--out", f"{d}/base.jsonl", "--json"])
    cursor = json.loads(p.stdout)["resume_cursor"]
    rep = ({}, {})
    for line in open(f"{d}/base.jsonl"):
        o = json.loads(line)
        if "type" in o:
            rep[0][o["id"]] = {"a": o["data"].get("a"), "s": o["data"].get("s")}
        else:
            rep[1][o["from"] + ">" + o["to"]] = {"w": o.get("data", {}).get("w")}
    if rep != read_state(g, "main"):
        raise Mismatch(f"baseline differs from main: {rep} vs {read_state(g, 'main')}")
    for step in range(r.randint(4, 10)):
        x = r.random()
        if x < 0.25:
            base, _ = lca(heads["feat"], heads["main"])
            nodes, nc = three_way(base.state[0], heads["main"].state[0], heads["feat"].state[0])
            edges, ec = three_way(base.state[1], heads["main"].state[1], heads["feat"].state[1])
            orph = [k for k in edges if k.split(">")[0] not in nodes or k.split(">")[1] not in nodes]
            if not (nc or ec or orph):
                sh(["branch", "merge", "feat", "--into", "main"])
                if heads["feat"].state != base.state:
                    heads["main"] = Commit([heads["main"], heads["feat"]], (nodes, edges))
        elif x < 0.5:
            old = heads["feat"].state
            new = mutate_model(r, old)
            if new != old:
                write_state_delta(sh, d, "feat", old, new, log)
                heads["feat"] = Commit([heads["feat"]], new)
        else:
            old = heads["main"].state
            new = mutate_model(r, old)
            if new != old:
                write_state_delta(sh, d, "main", old, new, log)
                heads["main"] = Commit([heads["main"]], new)
        if r.random() < 0.5:
            lim = r.choice([None, 1, 2, 50])
            args = ["changes", "poll", "--cursor", cursor, "--json"]
            if lim:
                args += ["--limit", str(lim)]
            p = sh(args)
            out = json.loads(p.stdout)
            apply_blocks(rep, out["blocks"])
            cursor = out["cursor"]
            if out.get("caught_up"):
                actual = read_state(g, "main")
                if rep != actual:
                    raise Mismatch(f"replica differs after poll (limit={lim}): replica={rep}\nmain={actual}\nLOG:\n" + "\n".join(log))
    # drain
    for _ in range(50):
        out = json.loads(sh(["changes", "poll", "--cursor", cursor, "--json"]).stdout)
        apply_blocks(rep, out["blocks"])
        cursor = out["cursor"]
        if out.get("caught_up"):
            break
    actual = read_state(g, "main")
    if rep != actual:
        raise Mismatch(f"final replica differs: replica={rep}\nmain={actual}\nLOG:\n" + "\n".join(log))
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
