#!/usr/bin/env python3
"""Concurrent column-patch updates, merges and maintenance on one local graph.

Writers update indexed scalar properties of their own disjoint keys (the
column-patch path, #1272) on main, a branch writer updates its keys on a branch
and merges it into main, a maintenance process loops `optimize` and
`index build`, and readers compare, at one pinned commit, an equality filter on
an indexed property with the same filter computed from a full read (index
coverage, #1300). At the end every key must hold its last acknowledged value
and every Blob its original bytes.

Usage: cpatch.py <seed> <seconds> [writers]"""
import base64, json, multiprocessing as mp, os, random, shutil, subprocess, sys, time

OG = os.environ.get("OG", "omnigraph")
D = "/tmp/og/cpatch"
SCHEMA = """node P {
  slug: String @key
  n: I64? @index
  s: String? @index
  b: Blob?
}
"""
NKEYS = 300


def run(args, timeout=600):
    return subprocess.run([OG] + args, capture_output=True, text=True, timeout=timeout)


def writer(wid, keys, branch, deadline, q):
    r = random.Random(wid)
    acked, unknown, refused = {}, set(), 0
    pf = f"{D}/p{wid}.json"
    while time.time() < deadline:
        ks = r.sample(keys, r.randint(1, 3))
        v = r.randint(0, 20)
        sv = r.choice(["a", "b", "c"])
        both = r.random() < 0.3
        params = {"ids": ks, "v": v, "sv": sv}
        sets = "n: $v, s: $sv" if both else "n: $v"
        src = f"query m($ids: [String], $v: I64, $sv: String) {{ update P set {{ {sets} }} where slug in $ids }}"
        open(pf, "w").write(json.dumps(params))
        p = run(["mutate", "-e", src, "--params-file", pf, "--branch", branch, "--target", f"{D}/g.omni", "--json"])
        if p.returncode == 0:
            for k in ks:
                old = acked.get(k, {})
                acked[k] = {"n": v, "s": sv if both else old.get("s", "SEED")}
                unknown.discard(k)
        elif p.returncode == 2:
            unknown.update(ks)
        else:
            refused += 1
            if "conflict" not in p.stdout + p.stderr and "busy" not in p.stdout + p.stderr:
                q.put(("err", wid, (p.stdout + p.stderr)[-400:]))
    q.put(("writer", wid, branch, acked, sorted(unknown), refused))


def maint(deadline, q):
    r = random.Random(99)
    res = {"optimize": [0, 0], "index": [0, 0]}
    while time.time() < deadline:
        op = r.choice(["optimize", "index"])
        p = run(["optimize", "--target", f"{D}/g.omni"] if op == "optimize" else ["index", "build", "--target", f"{D}/g.omni"])
        res[op][0 if p.returncode == 0 else 1] += 1
        if p.returncode and "conflict" not in p.stdout + p.stderr:
            q.put(("err", "maint", (p.stdout + p.stderr)[-400:]))
        time.sleep(r.random())
    q.put(("maint", res))


def reader(rid, deadline, q):
    r = random.Random(1000 + rid)
    checks = bad = 0
    g = f"{D}/g.omni"
    while time.time() < deadline:
        p = run(["commit", "list", "--target", g, "--branch", "main", "--json"])
        if p.returncode:
            continue
        head = json.loads(p.stdout)["commits"][0]["graph_commit_id"]
        a = run(["query", "-e", "query q() { match { $p: P } return { $p.slug, $p.n, $p.s } }", "--snapshot", head, "--target", g, "--json"])
        v = r.randint(0, 20)
        sv = r.choice(["a", "b", "c", "SEED"])
        b = run(["query", "-e", "query q($v: I64) { match { $p: P { n: $v } } return { $p.slug } }", "--params", json.dumps({"v": v}), "--snapshot", head, "--target", g, "--json"])
        c = run(["query", "-e", "query q($s: String) { match { $p: P { s: $s } } return { $p.slug } }", "--params", json.dumps({"s": sv}), "--snapshot", head, "--target", g, "--json"])
        if a.returncode or b.returncode or c.returncode:
            q.put(("err", f"reader{rid}", (a.stderr + b.stderr + c.stderr)[-400:]))
            continue
        rows = json.loads(a.stdout)["rows"]
        want_n = {x["p.slug"] for x in rows if x.get("p.n") == v}
        want_s = {x["p.slug"] for x in rows if x.get("p.s") == sv}
        got_n = {x["p.slug"] for x in json.loads(b.stdout)["rows"]}
        got_s = {x["p.slug"] for x in json.loads(c.stdout)["rows"]}
        checks += 1
        if got_n != want_n or got_s != want_s or len(rows) != NKEYS:
            bad += 1
            q.put(("bad", rid, head, v, sv, len(rows), sorted(want_n ^ got_n)[:10], sorted(want_s ^ got_s)[:10]))
    q.put(("reader", rid, checks, bad))


def main():
    seed, secs = int(sys.argv[1]), int(sys.argv[2])
    nw = int(sys.argv[3]) if len(sys.argv) > 3 else 6
    r = random.Random(seed)
    shutil.rmtree(D, ignore_errors=True)
    os.makedirs(D)
    g = f"{D}/g.omni"
    open(f"{D}/s.pg", "w").write(SCHEMA)
    run(["init", "--schema", f"{D}/s.pg", g])
    blobs = {}
    lines = []
    for i in range(NKEYS):
        k = f"k{i:03d}"
        raw = bytes(r.getrandbits(8) for _ in range(r.choice([10, 2000, 40000])))
        blobs[k] = "base64:" + base64.b64encode(raw).decode()
        lines.append(json.dumps({"type": "P", "data": {"slug": k, "n": 0, "s": "SEED", "b": blobs[k]}}))
    open(f"{D}/seed.jsonl", "w").write("\n".join(lines) + "\n")
    p = run(["load", "--data", f"{D}/seed.jsonl", "--mode", "overwrite", "--target", g])
    assert p.returncode == 0, p.stderr
    run(["index", "build", "--target", g])
    keys = [f"k{i:03d}" for i in range(NKEYS)]
    r.shuffle(keys)
    parts = [keys[i::nw + 1] for i in range(nw + 1)]
    deadline = time.time() + secs
    q = mp.Queue()
    procs = [mp.Process(target=writer, args=(i, parts[i], "main", deadline, q)) for i in range(nw)]
    # branch writer works on the last partition on branch "side", merged at the end
    run(["branch", "create", "--from", "main", "side", "--target", g])
    procs.append(mp.Process(target=writer, args=(nw, parts[nw], "side", deadline, q)))
    procs.append(mp.Process(target=maint, args=(deadline, q)))
    procs += [mp.Process(target=reader, args=(i, deadline, q)) for i in range(2)]
    for p_ in procs:
        p_.start()
    results = []
    alive = len(procs)
    while alive:
        msg = q.get()
        results.append(msg)
        if msg[0] in ("writer", "maint", "reader"):
            alive -= 1
        elif msg[0] in ("err", "bad"):
            print("EVENT", msg, flush=True)
    for p_ in procs:
        p_.join()
    m = run(["branch", "merge", "side", "--into", "main", "--target", g, "--json"])
    print(f"merge side rc={m.returncode} {(m.stdout + m.stderr)[-300:] if m.returncode else ''}", flush=True)
    # final check
    p = run(["export", "--target", g])
    final = {}
    for line in p.stdout.splitlines():
        if line.strip():
            d = json.loads(line)["data"]
            final[d["slug"]] = d
    lost = blob_bad = 0
    total_acked = 0
    for msg in results:
        if msg[0] == "writer":
            _, wid, br, acked, unknown, refused = msg
            total_acked += len(acked)
            print(f"writer {wid} on {br}: keys acked {len(acked)}, unknown {len(unknown)}, refused {refused}", flush=True)
            if br == "side" and m.returncode:
                continue
            for k, val in acked.items():
                if k in unknown:
                    continue
                if final[k].get("n") != val["n"] or final[k].get("s") != val["s"]:
                    lost += 1
                    print(f"LOST {k}: acked {val} final n={final[k].get('n')} s={final[k].get('s')}", flush=True)
        elif msg[0] in ("maint", "reader"):
            print(msg, flush=True)
    for k in keys:
        if final[k].get("b") != blobs[k]:
            blob_bad += 1
    print(f"done rows={len(final)} lost={lost} blob_bad={blob_bad}", flush=True)


if __name__ == "__main__":
    main()
