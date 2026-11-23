#!/usr/bin/env python3
"""SIGKILL during `index build` and `optimize` while they build several BTREE
indexes concurrently (#1395), then recovery.

A type with six BTREE `@index` I32 properties and a full-text String gets rows
in rounds of appends, merge loads and column-patch updates. Each round starts
`index build` (or `optimize`) and kills it after a random delay. Then:
- a read and every equality filter on each indexed property must equal the
  model (no partial commit visible, no lost or duplicated row);
- the next write, `index build` and `optimize` must succeed;
- the reads are repeated after them.

Usage: OG=target/release/omnigraph python3 tools/stress/ixkill.py <seed> <count>
"""
import json, os, random, shutil, signal, subprocess, sys, time

OG = os.environ.get("OG", "omnigraph")
ROOT = os.environ.get("FUZZ_ROOT", "/tmp/og/ixkill")
FAIL = os.environ.get("FUZZ_FAIL", "/tmp/og/ixkillfail")
PROPS = [f"k{i}" for i in range(1, 7)]
SCHEMA = "node D {\n  slug: String @key\n" + "".join(f"  {p}: I32? @index\n" for p in PROPS) + \
    "  body: String? @index\n  v: Vector(4)?\n}\n"
QUERIES = "".join(f"query q{p}($x: I32) {{ match {{ $d: D {{ {p}: $x }} }} return {{ $d.slug }} }}\n" for p in PROPS) + \
    "query qs($q: String) { match { $d: D\n search($d.body, $q) } return { $d.slug } }\n" + \
    "query qc() { match { $d: D } return { count($d) as n } }\n"
WORDS = ["kqzt", "mvrb", "plox", "zund", "frib", "quav"]


class Mismatch(Exception):
    pass


def run(args, timeout=900):
    return subprocess.run([OG] + args, capture_output=True, text=True, timeout=timeout)


def case(seed):
    r = random.Random(seed)
    d = os.path.join(ROOT, str(seed))
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    open(os.path.join(d, "s.pg"), "w").write(SCHEMA)
    qf = os.path.join(d, "q.gq")
    open(qf, "w").write(QUERIES)
    g = os.path.join(d, "g.omni")
    log = [f"seed {seed}"]
    kills = 0

    def sh(args, ok=True):
        full = args + ["--target", g] if args[0] != "init" else args
        p = run(full)
        log.append(" ".join(args)[:160] + f"  -> rc={p.returncode}")
        if ok and p.returncode != 0:
            raise Mismatch(f"command failed: {' '.join(args)[:300]}\n{p.stderr[-1500:]}{p.stdout[-1500:]}\nLOG:\n" + "\n".join(log))
        return p

    sh(["init", "--schema", os.path.join(d, "s.pg"), g])
    model = {}
    nid = [0]

    def rowval():
        v = {p: (None if r.random() < 0.1 else r.randint(0, 30)) for p in PROPS}
        v["body"] = " ".join(r.choice(WORDS) for _ in range(r.randint(1, 3)))
        return v

    def load(rows, mode):
        lines = []
        for s, v in rows.items():
            data = {"slug": s, "body": v["body"], "v": [1.0, 2.0, 3.0, 4.0]}
            data.update({p: v[p] for p in PROPS if v[p] is not None})
            lines.append(json.dumps({"type": "D", "data": data}))
        f = os.path.join(d, "b.jsonl")
        open(f, "w").write("\n".join(lines) + "\n")
        sh(["load", "--data", f, "--mode", mode])

    def check(where):
        rows = json.loads(sh(["query", "qc", "--query", qf, "--json"]).stdout)["rows"]
        n = rows[0]["n"] if rows else 0
        if n != len(model):
            raise Mismatch(f"{where}: count {n} != model {len(model)}\nLOG:\n" + "\n".join(log))
        for p in r.sample(PROPS, 3):
            x = r.randint(0, 30)
            got = sorted(row["d.slug"] for row in json.loads(sh(["query", f"q{p}", "--query", qf, "--params", json.dumps({"x": x}), "--json"]).stdout)["rows"])
            exp = sorted(s for s, v in model.items() if v[p] == x)
            if got != exp:
                raise Mismatch(f"{where}: {p}={x}: expected {len(exp)} got {len(got)} missing {sorted(set(exp) - set(got))[:5]} extra {sorted(set(got) - set(exp))[:5]}\nLOG:\n" + "\n".join(log))
        w = r.choice(WORDS)
        got = sorted(row["d.slug"] for row in json.loads(sh(["query", "qs", "--query", qf, "--params", json.dumps({"q": w}), "--json"]).stdout)["rows"])
        exp = sorted(s for s, v in model.items() if w in v["body"].split())
        if got != exp:
            raise Mismatch(f"{where}: search {w}: expected {len(exp)} got {len(got)}\nLOG:\n" + "\n".join(log))

    for rnd in range(r.randint(4, 8)):
        op = r.choice(["append", "append", "merge", "patch"])
        if op == "append" or not model:
            rows = {}
            for _ in range(r.randint(2000, 8000)):
                rows[f"d{nid[0]:07d}"] = rowval()
                nid[0] += 1
            load(rows, "append")
            model.update(rows)
        elif op == "merge":
            rows = {s: rowval() for s in r.sample(sorted(model), min(len(model), r.randint(100, 4000)))}
            load(rows, "merge")
            model.update(rows)
        else:
            p = r.choice(PROPS)
            x, y = r.randint(0, 30), r.randint(0, 30)
            sh(["mutate", "-e", f"query u() {{ update D set {{ {p}: {y} }} where {p} = {x} }}"])
            for s, v in model.items():
                if v[p] == x:
                    v[p] = y
        # the maintenance command to kill
        cmd = ["index", "build"] if r.random() < 0.7 else ["optimize"]
        delay = r.uniform(0.01, 0.3) if r.random() < 0.6 else r.uniform(0.3, 1.5)
        t0 = time.time()
        proc = subprocess.Popen([OG] + cmd + ["--target", g], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            proc.wait(timeout=delay)
            log.append(f"{' '.join(cmd)} finished before kill in {time.time() - t0:.2f}s rc={proc.returncode}")
            if proc.returncode != 0:
                raise Mismatch(f"{' '.join(cmd)} failed without a kill: {proc.stderr.read().decode()[-1500:]}\nLOG:\n" + "\n".join(log))
        except subprocess.TimeoutExpired:
            proc.send_signal(signal.SIGKILL)
            proc.wait()
            kills += 1
            log.append(f"{' '.join(cmd)} SIGKILL after {delay:.2f}s")
        check(f"round {rnd} after kill")
    # recovery: a write, index build and optimize must succeed, reads stay exact
    rows = {f"d{nid[0] + i:07d}": rowval() for i in range(50)}
    load(rows, "append")
    model.update(rows)
    sh(["index", "build"])
    check("after index build")
    sh(["optimize"])
    check("after optimize")
    sh(["index", "build"])
    check("final")
    shutil.rmtree(d, ignore_errors=True)
    return f"rows={len(model)} kills={kills}"


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
