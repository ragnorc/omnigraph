#!/usr/bin/env python3
"""Traversal answers on one long-lived server while the cached topology is
kept across writes (#1363): edge inserts and deletes of one edge type next to
reads of another, node deletes that cascade to edges, branch create, delete
and recreate under the same name, merges, and recorded optimize, which
persists the CSR artifact. Every read is compared with a Python model.

The graph has 1,200 P nodes, so an unanchored hop has an input frontier over
the 1,024 ceiling and plans on the CSR, and anchored reads use {1,7} bounds,
above the 6-hop ceiling, which also selects the CSR.

A merge the server refuses (409) leaves the model unchanged. A merge it
accepts must give the three-way set union the model computes.

Usage: tfuzz.py <seed> <steps>    (OG, OGS: CLI and server binaries)"""
import json, os, random, shutil, subprocess, sys, time, urllib.request, urllib.error

OG = os.environ.get("OG", "omnigraph")
OGS = os.environ.get("OGS", "omnigraph-server")
SEED, STEPS = int(sys.argv[1]), int(sys.argv[2])
NP, NC = int(os.environ.get("TF_NP", "1200")), 30
D = f"/tmp/og/tfuzz-{SEED}"
ROOT = f"{D}/root"
PORT = 18200 + SEED % 500
BASE = f"http://127.0.0.1:{PORT}"
G = "g"
TOK = "tok-a"
ETYPES = {"Knows": ("P", "P"), "Likes": ("P", "P"), "WorksAt": ("P", "C")}
TRAV = {"Knows": "knows", "Likes": "likes", "WorksAt": "worksAt"}

SCHEMA = """node P {
  slug: String @key
}
node C {
  slug: String @key
}
edge Knows: P -> P { @key(@src, @dst) }
edge Likes: P -> P { @key(@src, @dst) }
edge WorksAt: P -> C { @key(@src, @dst) }
"""
POLICY = """version: 1
groups:
  ops: [act-a]
rules:
  - id: branch-actions
    allow:
      actors: { group: ops }
      actions: [read, export, change, maintain]
      branch_scope: any
  - id: graph-actions
    allow:
      actors: { group: ops }
      actions: [reclaim, invoke_query]
  - id: branch-to-branch
    allow:
      actors: { group: ops }
      actions: [branch_create, branch_merge, branch_delete]
      target_branch_scope: any
"""

failures = []


def fail(*a):
    failures.append(a)
    print("FAIL", *a, flush=True)


def http(method, path, body=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + TOK)
    req.add_header("Omnigraph-Http-Api", "0.14")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            b = resp.read()
            return resp.status, json.loads(b or b"null")
    except urllib.error.HTTPError as e:
        b = e.read()
        try:
            return e.code, json.loads(b)
        except Exception:
            return e.code, b.decode()[:500]


def q(src, params, branch):
    st, out = http("POST", f"/graphs/{G}/query", {"query": src, "params": params, "branch": branch})
    if st != 200:
        return None, (st, out)
    return out["rows"], None


def m(src, params, branch):
    return http("POST", f"/graphs/{G}/mutate", {"query": src, "params": params, "branch": branch})


def ulid():
    import secrets
    v = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    a = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    s = ""
    for _ in range(26):
        s = a[v & 31] + s
        v >>= 5
    return s


# ---------------- model ----------------

def new_state(r):
    ps = {f"p{i:04d}" for i in range(NP)}
    cs = {f"c{i:02d}" for i in range(NC)}
    pl = sorted(ps)
    e = {
        "Knows": {(r.choice(pl), r.choice(pl)) for _ in range(NP * 2)},
        "Likes": {(r.choice(pl), r.choice(pl)) for _ in range(NP)},
        "WorksAt": {(r.choice(pl), r.choice(sorted(cs))) for _ in range(NP * 2 // 3)},
    }
    return {"P": ps, "C": cs, "E": e}


def copy_state(s):
    return {"P": set(s["P"]), "C": set(s["C"]), "E": {k: set(v) for k, v in s["E"].items()}}


def merge3(base, src, tgt):
    out = {}
    for k in ("P", "C"):
        out[k] = (base[k] | (src[k] - base[k]) | (tgt[k] - base[k])) - (base[k] - src[k]) - (base[k] - tgt[k])
    out["E"] = {}
    for t, b in base["E"].items():
        s, g = src["E"][t], tgt["E"][t]
        out["E"][t] = (b | (s - b) | (g - b)) - (b - s) - (b - g)
    orphan = any(a not in out["P"] or (b not in out[ETYPES[t][1]]) for t, es in out["E"].items() for a, b in es)
    return out, orphan


def adj(state, types, undirected=False):
    a = {}
    for t in types:
        for x, y in state["E"][t]:
            a.setdefault(x, set()).add(y)
            if undirected:
                a.setdefault(y, set()).add(x)
    return a


def reach(a, start, lo, hi):
    d = {start: 0}
    fr = [start]
    while fr:
        nx = []
        for x in fr:
            if d[x] >= hi:
                continue
            for y in a.get(x, ()):
                if y not in d:
                    d[y] = d[x] + 1
                    nx.append(y)
        fr = nx
    out = {y for y, k in d.items() if lo <= k <= hi and y != start}
    if lo <= 1 <= hi and start in a.get(start, ()):
        out.add(start)
    return out


# ---------------- checks ----------------

def check(state, branch, r, tag):
    for t in ETYPES:
        rows, err = q(f"query a() {{ match {{ $a: {ETYPES[t][0]} $a {TRAV[t]} $b }} return {{ $a.slug, $b.slug }} }}", {}, branch)
        if err:
            fail(tag, branch, "pairs", t, err)
            continue
        got = sorted((x["a.slug"], x["b.slug"]) for x in rows)
        want = sorted(state["E"][t])
        if got != want:
            gs, ws = set(got), set(want)
            fail(tag, branch, "pairs", t, "missing", sorted(ws - gs)[:5], "extra", sorted(gs - ws)[:5], "dups", len(got) - len(gs))
    pl = sorted(state["P"])
    for _ in range(3):
        s = r.choice(pl)
        kind = r.choice(["knows", "sel", "und", "two"])
        if kind == "knows":
            src = "query a($s: String) { match { $a: P { slug: $s } $a knows{1,7} $b } return distinct { $b.slug } }"
            want = reach(adj(state, ["Knows"]), s, 1, 7)
        elif kind == "sel":
            src = "query a($s: String) { match { $a: P { slug: $s } $a (knows | likes){1,7} $b } return distinct { $b.slug } }"
            want = reach(adj(state, ["Knows", "Likes"]), s, 1, 7)
        elif kind == "und":
            src = "query a($s: String) { match { $a: P { slug: $s } $a <likes>{1,7} $b } return distinct { $b.slug } }"
            want = reach(adj(state, ["Likes"], True), s, 1, 7)
        else:
            src = "query a($s: String) { match { $a: P { slug: $s } $a knows $b $b worksAt $c } return distinct { $c.slug } }"
            k = adj(state, ["Knows"])
            w = adj(state, ["WorksAt"])
            want = {c for b in k.get(s, ()) for c in w.get(b, ())}
        rows, err = q(src, {"s": s}, branch)
        if err:
            fail(tag, branch, kind, s, err)
            continue
        got = {list(x.values())[0] for x in rows}
        if len(rows) != len(got):
            fail(tag, branch, kind, s, "duplicate rows", len(rows), len(got))
        if got != want:
            fail(tag, branch, kind, s, "missing", sorted(want - got)[:5], "extra", sorted(got - want)[:5])


# ---------------- setup ----------------

def unlock():
    st = json.loads(subprocess.run([OG, "cluster", "status", "--target", ROOT, "--json"], capture_output=True, text=True).stdout)

    def find(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "lock_id" and isinstance(v, str):
                    return v
                x = find(v)
                if x:
                    return x
        if isinstance(o, list):
            for v in o:
                x = find(v)
                if x:
                    return x
    lid = find(st)
    if lid:
        subprocess.run([OG, "cluster", "force-unlock", lid, "--target", ROOT, "--yes"], capture_output=True, text=True)



def setup(state):
    shutil.rmtree(D, ignore_errors=True)
    os.makedirs(ROOT)
    open(f"{D}/g.pg", "w").write(SCHEMA)
    open(f"{D}/policy.yaml", "w").write(POLICY)
    open(f"{D}/cluster.yaml", "w").write(
        "version: 1\nmetadata:\n  name: tfuzz\ngraphs:\n  g:\n    schema: g.pg\n"
        "policies:\n  gp:\n    file: policy.yaml\n    applies_to: [g]\n")
    p = subprocess.run([OG, "cluster", "apply", "--target", ROOT, "--config", D, "--json"], capture_output=True, text=True)
    if p.returncode:
        sys.exit("apply: " + p.stdout[-1500:] + p.stderr[-1500:])
    unlock()
    with open(f"{D}/seed.jsonl", "w") as f:
        for s in sorted(state["P"]):
            f.write(json.dumps({"type": "P", "data": {"slug": s}}) + "\n")
        for s in sorted(state["C"]):
            f.write(json.dumps({"type": "C", "data": {"slug": s}}) + "\n")
        for t, es in state["E"].items():
            for a, b in sorted(es):
                f.write(json.dumps({"edge": t, "from": a, "to": b, "data": {}}) + "\n")
    p = subprocess.run([OG, "load", "--target", ROOT, "--graph", G, "--data", f"{D}/seed.jsonl", "--mode", "merge", "--json"], capture_output=True, text=True)
    if p.returncode:
        sys.exit("load: " + p.stdout[-1500:] + p.stderr[-1500:])
    unlock()


server = None


def start():
    global server
    e = dict(os.environ)
    e["OMNIGRAPH_SERVER_BEARER_TOKENS_JSON"] = json.dumps({"act-a": TOK})
    e["OMNIGRAPH_OPERATION_ROUTES"] = "true"
    server = subprocess.Popen([OGS, "--cluster", ROOT, "--bind", f"127.0.0.1:{PORT}"],
                              stdout=open(f"{D}/server.log", "a"), stderr=subprocess.STDOUT, env=e)
    for _ in range(240):
        try:
            with urllib.request.urlopen(BASE + "/readyz", timeout=5) as resp:
                if resp.status == 200:
                    return
        except Exception:
            pass
        if server.poll() is not None:
            sys.exit("server exited: " + open(f"{D}/server.log").read()[-2000:])
        time.sleep(0.5)
    sys.exit("server not ready")


def stop():
    server.terminate()
    try:
        server.wait(60)
    except Exception:
        server.kill()


def optimize():
    oid = ulid()
    st, out = http("POST", f"/graphs/{G}/operations/optimize", {"operation_id": oid, "max_gate_hold_ms": 5000})
    if st not in (200, 202):
        return f"submit {st} {str(out)[:200]}"
    for _ in range(240):
        st, out = http("GET", f"/graphs/{G}/operations/{oid}")
        rec = (out or {}).get("operation") or {} if isinstance(out, dict) else {}
        state = rec.get("state") or rec.get("status")
        if state in ("succeeded", "failed", "cancelled"):
            return state
        time.sleep(0.5)
    return "not terminal"



# ---------------- concurrent readers ----------------
# Readers run beside the writer. Each read records the graph_commit_id the
# server pinned; the writer maps every head it publishes to the model state,
# and the reads are judged at the end against the state of their own commit.

import threading
snapshots = {}          # graph_commit_id -> model state
reads = []              # (branch, commit, query kind, param, rows)
live_branches = ["main"]
stop_readers = threading.Event()
HEAD = "query h() { match { $c: C } return { count($c) as n } }"


def head_of(branch):
    st, out = http("POST", f"/graphs/{G}/query", {"query": HEAD, "params": {}, "branch": branch})
    return out.get("graph_commit_id") if st == 200 else None


def record_heads(states):
    for b, s in states.items():
        h = head_of(b)
        if h:
            snapshots[h] = copy_state(s)


def reader(rid, pl):
    rr = random.Random(1000 + rid)
    while not stop_readers.is_set():
        b = rr.choice(list(live_branches))
        if rr.random() < 0.5:
            t = rr.choice(list(ETYPES))
            src = f"query a() {{ match {{ $a: {ETYPES[t][0]} $a {TRAV[t]} $b }} return {{ $a.slug, $b.slug }} }}"
            st, out = http("POST", f"/graphs/{G}/query", {"query": src, "params": {}, "branch": b})
            if st == 200:
                reads.append((b, out.get("graph_commit_id"), "pairs", t, [(x["a.slug"], x["b.slug"]) for x in out["rows"]]))
        else:
            a = rr.choice(pl)
            src = "query a($s: String) { match { $a: P { slug: $s } $a (knows | likes){1,7} $b } return distinct { $b.slug } }"
            st, out = http("POST", f"/graphs/{G}/query", {"query": src, "params": {"s": a}, "branch": b})
            if st == 200:
                reads.append((b, out.get("graph_commit_id"), "sel", a, [x["b.slug"] for x in out["rows"]]))


def judge_reads():
    judged = unmapped = 0
    for b, c, kind, prm, rows in reads:
        s = snapshots.get(c)
        if s is None:
            unmapped += 1
            continue
        judged += 1
        if kind == "pairs":
            if sorted(rows) != sorted(s["E"][prm]):
                gs, ws = set(rows), s["E"][prm]
                fail("concurrent", b, c, "pairs", prm, "missing", sorted(ws - gs)[:5], "extra", sorted(gs - ws)[:5], "dups", len(rows) - len(gs))
        else:
            want = reach(adj(s, ["Knows", "Likes"]), prm, 1, 7) if prm in s["P"] else set()
            if set(rows) != want or len(rows) != len(set(rows)):
                fail("concurrent", b, c, "sel", prm, "missing", sorted(want - set(rows))[:5], "extra", sorted(set(rows) - want)[:5])
    return judged, unmapped


def main():
    r = random.Random(SEED)
    main_s = new_state(r)
    setup(main_s)
    start()
    states = {"main": main_s}
    base = None
    stats = {}
    check(main_s, "main", r, "init")
    NR = int(os.environ.get("TF_READERS", "0"))
    record_heads(states)
    threads = [threading.Thread(target=reader, args=(i, sorted(main_s["P"])), daemon=True) for i in range(NR)]
    for t in threads:
        t.start()
    for step in range(STEPS):
        live = [b for b in states if b != "main"]
        op = r.choices(["ins", "del", "ndel", "nins", "bcreate", "merge", "bdel", "opt"],
                       [30, 20, 6, 6, 8 if not live else 0, 8 if live else 0, 5 if live else 0, 3])[0]
        br = r.choice(list(states))
        s = states[br]
        tag = f"step{step}:{op}@{br}"
        stats[op] = stats.get(op, 0) + 1
        if op == "ins":
            t = r.choice(list(ETYPES))
            n = r.randint(1, 3)
            pl = sorted(s["P"])
            dst = pl if ETYPES[t][1] == "P" else sorted(s["C"])
            pairs = [(r.choice(pl), r.choice(dst)) for _ in range(n)]
            src = "query m(" + ", ".join(f"$a{i}: String, $b{i}: String" for i in range(n)) + ") { " + \
                  " ".join(f"insert {t} {{ from: $a{i}, to: $b{i} }}" for i in range(n)) + " }"
            params = {}
            for i, (a, b) in enumerate(pairs):
                params[f"a{i}"], params[f"b{i}"] = a, b
            st, out = m(src, params, br)
            if st == 200:
                s["E"][t].update(pairs)
            else:
                fail(tag, "insert refused", st, str(out)[:300])
        elif op == "del":
            t = r.choice(list(ETYPES))
            if not s["E"][t]:
                continue
            a, b = r.choice(sorted(s["E"][t]))
            st, out = m(f"query m($a: String, $b: String) {{ delete {t} where @src = $a and @dst = $b }}", {"a": a, "b": b}, br)
            if st == 200:
                s["E"][t].discard((a, b))
            else:
                fail(tag, "edge delete refused", st, str(out)[:300])
        elif op == "ndel":
            p = r.choice(sorted(s["P"]))
            st, out = m("query m($s: String) { delete P where slug = $s }", {"s": p}, br)
            if st == 200:
                s["P"].discard(p)
                for t in ETYPES:
                    s["E"][t] = {(a, b) for a, b in s["E"][t] if a != p and b != p}
            else:
                fail(tag, "node delete refused", st, str(out)[:300])
        elif op == "nins":
            gone = sorted({f"p{i:04d}" for i in range(NP)} - s["P"])
            if not gone:
                continue
            p = r.choice(gone)
            st, out = m("query m($s: String) { insert P { slug: $s } }", {"s": p}, br)
            if st == 200:
                s["P"].add(p)
            else:
                fail(tag, "node insert refused", st, str(out)[:300])
        elif op == "bcreate":
            st, out = http("POST", f"/graphs/{G}/branches", {"name": "x", "from": "main"})
            if st in (200, 201):
                states["x"] = copy_state(states["main"])
                base = copy_state(states["main"])
            else:
                fail(tag, "branch create", st, str(out)[:300])
        elif op == "merge":
            st, out = http("POST", f"/graphs/{G}/branches/merge", {"source": "x", "target": "main"})
            want, orphan = merge3(base, states["x"], states["main"])
            if st == 200:
                if orphan:
                    fail(tag, "merge accepted though the model has an orphan edge")
                states["main"] = want
                # main now holds the source head, so the next merge base is
                # the source branch as it was at this merge
                base = copy_state(states["x"])
            elif st == 409:
                stats["merge409"] = stats.get("merge409", 0) + 1
                if not orphan:
                    print("NOTE", tag, "merge 409 without a model orphan", str(out)[:300], flush=True)
            else:
                fail(tag, "merge", st, str(out)[:300])
        elif op == "bdel":
            st, out = http("DELETE", f"/graphs/{G}/branches/x")
            if st in (200, 204):
                del states["x"]
                base = None
            else:
                fail(tag, "branch delete", st, str(out)[:300])
        elif op == "opt":
            res = optimize()
            stats["opt_" + str(res)] = stats.get("opt_" + str(res), 0) + 1
        record_heads(states)
        live_branches[:] = list(states)
        for b in list(states):
            check(states[b], b, r, tag)
        if len(failures) > 20:
            break
    stop_readers.set()
    for t in threads:
        t.join(120)
    judged, unmapped = judge_reads()
    stats["concurrent_reads_judged"], stats["concurrent_reads_unmapped"] = judged, unmapped
    stop()
    start()
    for b in list(states):
        check(states[b], b, r, "after-restart")
    stop()
    print(json.dumps({"seed": SEED, "steps": STEPS, "stats": stats, "failures": len(failures)}))
    if failures:
        json.dump([list(map(str, f)) for f in failures], open(f"{D}/failures.json", "w"), indent=1)
        sys.exit(1)


if __name__ == "__main__":
    main()
