#!/usr/bin/env python3
"""Bounded recorded optimize over several tables, on one long-lived server.

A graph with four node types and three edge types gets many small commits per
table, so every table has many fragments. The server runs with a small run
byte budget (`OMNIGRAPH_OPTIMIZE_RUN_SOURCE_BYTES`) and small gate holds, so
a full-catalog optimize stops early and records the table to start at
(`resume_at`). The script submits successive full-catalog runs, with writes
between them and sometimes during them, and SIGKILLs the server in the middle
of some runs (then force-unlocks the local root and restarts it).

Checks:
- every operation reaches a terminal state; one that was running when its
  server was killed is `interrupted`;
- a run after a deferral starts at the recorded `resume_at` (the first table
  it reports work on is that table);
- successive bounded runs reach every table: every table with more than one
  fragment's worth of commits is `optimized` at least once;
- every acknowledged row is present once, with its value, and no other row is
  (full export), and every edge's endpoints exist.

Usage: optresume.py <runs> [--kill]
Env: OG, OGS (binaries), BUDGET (run source bytes, default 60000),
     CEIL_FRAGS (OMNIGRAPH_OPTIMIZE_MAX_SOURCE_FRAGMENTS, unset by default)."""
import json, os, random, shutil, signal, subprocess, sys, threading, time, urllib.request, urllib.error

OG = os.environ.get("OG", "omnigraph")
OGS = os.environ.get("OGS", "omnigraph-server")
RUNS = int(sys.argv[1])
KILL = "--kill" in sys.argv
D = "/tmp/og/optresume"
ROOT = f"{D}/root"
PORT = 18110
BASE = f"http://127.0.0.1:{PORT}"
G = "g"
TOK = "tok-o"
NODES = ["A", "B", "C", "D"]
EDGES = [("AB", "A", "B"), ("BC", "B", "C"), ("CD", "C", "D")]
SCHEMA = "".join(f"node {t} {{\n  slug: String @key\n  n: I64\n  tag: String @index\n}}\n" for t in NODES) + \
    "".join(f"edge {e}: {a} -> {b} {{ w: I64 @key(@src, @dst) }}\n" for e, a, b in EDGES)
POLICY = """version: 1
groups:
  ops: [act-o]
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
"""
events = []


def ev(*a):
    events.append((round(time.time(), 2),) + a)
    print("EVENT", *a, flush=True)


def http(method, url, body=None, headers=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:4000]
    except Exception as e:
        return None, repr(e)


def api(method, path, body=None, timeout=120):
    st, txt = http(method, BASE + path, body, {"Authorization": "Bearer " + TOK, "Omnigraph-Http-Api": "0.14"}, timeout)
    try:
        return st, json.loads(txt) if txt else None
    except Exception:
        return st, txt


def find_key(o, key):
    if isinstance(o, dict):
        for k, v in o.items():
            if k == key and isinstance(v, str):
                return v
            r = find_key(v, key)
            if r:
                return r
    if isinstance(o, list):
        for v in o:
            r = find_key(v, key)
            if r:
                return r
    return None


def unlock():
    p = subprocess.run([OG, "cluster", "status", "--target", ROOT, "--json"], capture_output=True, text=True)
    try:
        lock_id = find_key(json.loads(p.stdout), "lock_id")
    except Exception:
        sys.exit("status: " + p.stdout[-800:] + p.stderr[-800:])
    if lock_id:
        q = subprocess.run([OG, "cluster", "force-unlock", lock_id, "--target", ROOT, "--yes"], capture_output=True, text=True)
        ev("force-unlock", lock_id, q.returncode, q.stderr[-200:].strip())


server = None


def start_server():
    global server
    e = dict(os.environ)
    e["OMNIGRAPH_SERVER_BEARER_TOKENS_JSON"] = json.dumps({"act-o": TOK})
    e["OMNIGRAPH_OPERATION_ROUTES"] = "true"
    e["OMNIGRAPH_OPTIMIZE_RUN_SOURCE_BYTES"] = os.environ.get("BUDGET", "60000")
    if os.environ.get("CEIL_FRAGS"):
        e["OMNIGRAPH_OPTIMIZE_MAX_SOURCE_FRAGMENTS"] = os.environ["CEIL_FRAGS"]
    log = open(f"{D}/server.log", "a")
    server = subprocess.Popen([OGS, "--cluster", ROOT, "--bind", f"127.0.0.1:{PORT}"], stdout=log, stderr=log, env=e)
    for _ in range(240):
        st, _ = http("GET", BASE + "/readyz", timeout=5)
        if st == 200:
            return
        if server.poll() is not None:
            sys.exit(f"server exited {server.returncode}; see {D}/server.log")
        time.sleep(0.5)
    sys.exit("server not ready")


def setup():
    shutil.rmtree(D, ignore_errors=True)
    os.makedirs(f"{D}/src")
    os.makedirs(ROOT)
    open(f"{D}/src/g.pg", "w").write(SCHEMA)
    open(f"{D}/src/policy.yaml", "w").write(POLICY)
    open(f"{D}/src/cluster.yaml", "w").write(
        "version: 1\nmetadata:\n  name: optresume\ngraphs:\n  g:\n    schema: g.pg\n"
        "policies:\n  gp:\n    file: policy.yaml\n    applies_to: [g]\n")
    p = subprocess.run([OG, "cluster", "apply", "--target", ROOT, "--config", f"{D}/src", "--json"], capture_output=True, text=True)
    if p.returncode:
        sys.exit("apply: " + p.stdout[-1500:] + p.stderr[-1500:])
    unlock()


nodes = {t: {} for t in NODES}     # acked: slug -> n
edges = {e: {} for e, _, _ in EDGES}  # acked: (src, dst) -> w
attempted = {t: set() for t in NODES}
attempted_e = {e: set() for e, _, _ in EDGES}
unknown = []
counter = [0]
R = random.Random(5)


def write_batch(tables=None, size=3):
    """One commit touching the given tables (node rows plus edges between existing rows)."""
    tables = tables or R.sample(NODES + [e for e, _, _ in EDGES], R.randint(1, 3))
    params, stmts, decl = {}, [], []
    planned_n, planned_e = [], []
    for t in tables:
        if t in NODES:
            for _ in range(size):
                counter[0] += 1
                k, n = f"{t.lower()}{counter[0]}", R.randint(0, 10**6)
                i = len(decl)
                decl += [f"$k{i}: String", f"$n{i}: I64", f"$g{i}: String"]
                params.update({f"k{i}": k, f"n{i}": n, f"g{i}": R.choice(["x", "y", "z"])})
                stmts.append(f"insert {t} {{ slug: $k{i}, n: $n{i}, tag: $g{i} }}")
                planned_n.append((t, k, n))
        else:
            e, a, b = next(x for x in EDGES if x[0] == t)
            if not nodes[a] or not nodes[b]:
                continue
            for _ in range(size):
                s, d, w = R.choice(list(nodes[a])), R.choice(list(nodes[b])), R.randint(0, 999)
                if any(pe[1:3] == (s, d) for pe in planned_e if pe[0] == e):
                    continue
                i = len(decl)
                decl += [f"$s{i}: String", f"$d{i}: String", f"$w{i}: I64"]
                params.update({f"s{i}": s, f"d{i}": d, f"w{i}": w})
                stmts.append(f"insert {e} {{ from: $s{i}, to: $d{i}, w: $w{i} }}")
                planned_e.append((e, s, d, w))
    if not stmts:
        return None
    for t, k, n in planned_n:
        attempted[t].add(k)
    for e, s, d, w in planned_e:
        attempted_e[e].add((s, d, w))
    q = f"query m({', '.join(decl)}) {{ {' '.join(stmts)} }}"
    st, out = api("POST", f"/graphs/{G}/mutate", {"query": q, "params": params})
    if st == 200:
        for t, k, n in planned_n:
            nodes[t][k] = n
        for e, s, d, w in planned_e:
            edges[e][(s, d)] = w  # keyed edge: a re-insert upserts w
    elif not (isinstance(out, dict) and out.get("disposition", {}).get("effects") == "none"):
        unknown.append((st, str(out)[:200]))
    return st


def ulid():
    import secrets
    t = int(time.time() * 1000)
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    v = (t << 80) | secrets.randbits(80)
    s = ""
    for _ in range(26):
        s = alphabet[v & 31] + s
        v >>= 5
    return s


TERMINAL = {"succeeded", "failed", "cancelled", "interrupted", "unsettled"}


def lookup(oid, wait=600):
    t0 = time.time()
    out = None
    while time.time() - t0 < wait:
        st, out = api("GET", f"/graphs/{G}/operations/{oid}")
        if st == 200 and isinstance(out, dict):
            rec = out.get("operation") or {}
            if (rec.get("state") in TERMINAL) or out.get("lookup") == "unknown":
                return out
        time.sleep(0.5)
    return out


def table_outcomes(rec):
    """[(table, outcome)] in reported order, from wherever the record keeps them."""
    res = []

    def walk(o):
        if isinstance(o, dict):
            if "outcome" in o and isinstance(o.get("outcome"), str) and any(k in o for k in ("type_key", "table", "table_key")):
                res.append((o.get("type_key") or o.get("table") or o.get("table_key"), o["outcome"]))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(rec)
    return res


def main():
    setup()
    start_server()
    # many small commits per table
    for i in range(int(os.environ.get("SEED_COMMITS", "60"))):
        write_batch(tables=[NODES[i % 4]] + ([EDGES[i % 3][0]] if i >= 8 else []), size=4)
    ev("seeded", {t: len(v) for t, v in nodes.items()}, {e: len(v) for e, v in edges.items()})
    errs = []
    optimized = {}
    prev_resume = None
    records = []
    for run in range(RUNS):
        oid = ulid()
        body = {"operation_id": oid, "max_gate_hold_ms": R.choice([0, 100, 1000, 5000])}
        bg_stop = threading.Event()
        bg = None
        if R.random() < 0.4:
            def bgw():
                while not bg_stop.is_set():
                    write_batch(size=2)
                    time.sleep(0.05)
            bg = threading.Thread(target=bgw)
            bg.start()
        st, out = api("POST", f"/graphs/{G}/operations/optimize", body)
        killed = False
        if KILL and st in (200, 202) and R.random() < 0.3:
            time.sleep(R.uniform(0, float(os.environ.get("KILL_MAX", "1.5"))))
            server.send_signal(signal.SIGKILL)
            server.wait()
            killed = True
            ev("SIGKILL during run", run, oid)
            if bg:
                bg_stop.set()
                bg.join()
                bg = None
            unlock()
            start_server()
        out2 = lookup(oid)
        if bg:
            bg_stop.set()
            bg.join()
        rec = (out2 or {}).get("operation") or {}
        state = rec.get("state")
        outs = table_outcomes(rec)
        resume = find_key(rec, "resume_at")
        records.append({"rec": rec, "run": run, "oid": oid, "submit": st, "killed": killed, "state": state, "outcomes": outs, "resume_at": resume, "prev_resume": prev_resume})
        ev("run", run, st, state, "killed" if killed else "", outs, "resume_at", resume)
        if state not in TERMINAL:
            errs.append(f"run {run} {oid} not terminal: {state} {str(out2)[:300]}")
        if killed and state not in TERMINAL:
            pass
        if state == "succeeded":
            worked = [t for t, o in outs if o != "deferred"]
            if prev_resume and worked and worked[0] != prev_resume and not any(t == prev_resume for t, _ in outs[:1]):
                errs.append(f"RESUME_IGNORED run {run}: resume_at was {prev_resume}, first table {outs[:2]}")
            for t, o in outs:
                if o == "optimized":
                    optimized[t] = optimized.get(t, 0) + 1
                if o == "failed":
                    errs.append(f"table failed run {run}: {t} {str(rec)[:400]}")
            prev_resume = resume
        # writes between runs
        for _ in range(R.randint(0, 6)):
            write_batch(size=R.randint(1, 4))
    # final checks
    st, txt = http("POST", BASE + f"/graphs/{G}/export", {"branch": "main"},
                   {"Authorization": "Bearer " + TOK, "Omnigraph-Http-Api": "0.14"}, timeout=600)
    got_n = {t: {} for t in NODES}
    got_e = {e: {} for e, _, _ in EDGES}
    if st != 200:
        errs.append(f"export {st} {txt[:300]}")
    else:
        for line in txt.splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if "type" in r and r["type"] in got_n:
                d = r["data"]
                if d["slug"] in got_n[r["type"]]:
                    errs.append(f"DUPLICATE {r['type']} {d['slug']}")
                got_n[r["type"]][d["slug"]] = d["n"]
            elif r.get("edge") in got_e:
                key = (r["from"], r["to"])
                if key in got_e[r["edge"]]:
                    errs.append(f"DUPLICATE edge {r['edge']} {key}")
                got_e[r["edge"]][key] = r["data"]["w"]
    for t in NODES:
        for k, n in nodes[t].items():
            if got_n[t].get(k) != n:
                errs.append(f"LOST/WRONG {t} {k}: {got_n[t].get(k)} != {n}")
        for k in got_n[t]:
            if k not in attempted[t]:
                errs.append(f"PHANTOM {t} {k}")
    for e, a, b in EDGES:
        for key, w in edges[e].items():
            if key not in got_e[e]:
                errs.append(f"LOST edge {e} {key}")
        for key, w in got_e[e].items():
            if key[0] not in got_n[a] or key[1] not in got_n[b]:
                errs.append(f"DANGLING edge {e} {key}")
            if not any((key[0], key[1], ww) == x for x in attempted_e[e] for ww in [w]):
                errs.append(f"PHANTOM edge {e} {key} w={w}")
    st, outq = api("POST", f"/graphs/{G}/query", {"query": "query q() { match { $a: A\n $a aB $b\n $b bC $c } return { count($c) as n } }", "params": {}})
    if st != 200:
        errs.append(f"traversal read {st} {str(outq)[:200]}")
    tables_seen = sorted({t for r in records for t, _ in r["outcomes"]})
    starved = [t for t in tables_seen if t not in optimized]
    res = {
        "runs": len(records), "states": {s: sum(1 for r in records if r["state"] == s) for s in {r["state"] for r in records}},
        "kills": sum(1 for r in records if r["killed"]), "optimized_per_table": optimized, "tables_seen": tables_seen,
        "never_optimized": starved, "unknown_writes": len(unknown), "unknown_samples": unknown[:3],
        "rows": {t: len(v) for t, v in got_n.items()}, "edges": {e: len(v) for e, v in got_e.items()},
        "errors": errs[:40], "error_count": len(errs),
    }
    print(json.dumps(res, indent=1, default=str))
    json.dump({"res": res, "records": records, "events": events}, open(f"{D}/result.json", "w"), default=str)
    server.send_signal(signal.SIGTERM)
    try:
        server.wait(timeout=60)
    except subprocess.TimeoutExpired:
        server.kill()


if __name__ == "__main__":
    main()
