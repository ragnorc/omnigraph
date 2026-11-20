#!/usr/bin/env python3
"""Reads of branch-held commits on one long-lived server (#1184). A commit
that only a branch's `__manifest` buffers is now looked up through a
per-handle hint naming the branch that held its history block. This script
writes commits on several branches, reads each commit by id (snapshot query,
commit changes, diff), deletes and recreates branches under the same names,
merges some into main, and reads every recorded commit again. Every answer
must equal the model at that commit, or be a typed refusal for a commit that
is no longer retained; a commit read once successfully must not later return
different rows.

Usage: hfuzz.py <seed> <steps>    (OG, OGS: CLI and server binaries)"""
import json, os, random, shutil, subprocess, sys, time, urllib.request, urllib.error

OG = os.environ.get("OG", "omnigraph")
OGS = os.environ.get("OGS", "omnigraph-server")
SEED, STEPS = int(sys.argv[1]), int(sys.argv[2])
D = f"/tmp/og/hfuzz-{SEED}"
ROOT = f"{D}/root"
PORT = 18700 + SEED % 200
BASE = f"http://127.0.0.1:{PORT}"
G, TOK = "g", "tok-a"
SCHEMA = "node P {\n  slug: String @key\n  n: I64\n}\n"
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
fails = []


def fail(*a):
    fails.append(a)
    print("FAIL", *a, flush=True)


def http(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + TOK)
    req.add_header("Omnigraph-Http-Api", "0.14")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:
        b = e.read()
        try:
            return e.code, json.loads(b)
        except Exception:
            return e.code, b.decode()[:400]


def lock_id():
    out = subprocess.run([OG, "cluster", "status", "--target", ROOT, "--json"], capture_output=True, text=True).stdout
    import re
    m = re.findall(r'"lock_id": "([^"]+)"', out)
    return m[0] if m else None


def setup():
    shutil.rmtree(D, ignore_errors=True)
    os.makedirs(ROOT)
    open(f"{D}/g.pg", "w").write(SCHEMA)
    open(f"{D}/policy.yaml", "w").write(POLICY)
    open(f"{D}/cluster.yaml", "w").write("version: 1\nmetadata:\n  name: hfuzz\ngraphs:\n  g:\n    schema: g.pg\n"
                                         "policies:\n  gp:\n    file: policy.yaml\n    applies_to: [g]\n")
    p = subprocess.run([OG, "cluster", "apply", "--target", ROOT, "--config", D, "--json"], capture_output=True, text=True)
    if p.returncode:
        sys.exit("apply " + p.stdout[-800:])
    lid = lock_id()
    if lid:
        subprocess.run([OG, "cluster", "force-unlock", lid, "--target", ROOT, "--yes"], capture_output=True)
    e = dict(os.environ)
    e["OMNIGRAPH_SERVER_BEARER_TOKENS_JSON"] = json.dumps({"act-a": TOK})
    srv = subprocess.Popen([OGS, "--cluster", ROOT, "--bind", f"127.0.0.1:{PORT}"],
                           stdout=open(f"{D}/server.log", "w"), stderr=subprocess.STDOUT, env=e)
    for _ in range(120):
        try:
            with urllib.request.urlopen(BASE + "/readyz", timeout=5) as r:
                if r.status == 200:
                    return srv
        except Exception:
            time.sleep(0.5)
    sys.exit("server not ready")


READ = "query q() { match { $p: P } return { $p.slug, $p.n } }"


def read_at(commit):
    st, out = http("POST", f"/graphs/{G}/query", {"query": READ, "params": {}, "snapshot": commit})
    if st != 200:
        return None, (st, out)
    return {r["p.slug"]: r["p.n"] for r in out["rows"]}, None


def main():
    r = random.Random(SEED)
    srv = setup()
    states = {"main": {}}
    commits = {}       # commit id -> (model rows, previous commit on its branch or None)
    seen_ok = {}       # commit id -> rows a successful read returned
    stats = {}
    heads = {}
    bases = {}
    origin, inc, merged_into_main = {}, {}, set()
    for step in range(STEPS):
        live = [b for b in states if b != "main"]
        op = r.choices(["write", "create", "delete", "merge", "readall"],
                       [55, 10 if len(live) < 4 else 0, 7 if live else 0, 8 if live else 0, 6])[0]
        stats[op] = stats.get(op, 0) + 1
        if op == "write":
            b = r.choice(list(states))
            k = f"k{r.randint(0, 30):02d}"
            n = r.randint(0, 99)
            st, out = http("POST", f"/graphs/{G}/mutate", {"query": "query m($k: String, $n: I64) { insert P { slug: $k, n: $n } }",
                                                          "params": {"k": k, "n": n}, "branch": b})
            if st != 200:
                fail(step, "write", b, st, str(out)[:300])
                continue
            prev = heads.get(b)
            states[b][k] = n
            c = out["commit"]["graph_commit_id"]
            commits[c] = (dict(states[b]), prev)
            heads[b] = c
            origin[c] = (b, inc.get(b, 0))
        elif op == "create":
            name = r.choice(["b1", "b2", "b3", "b4"])
            if name in states:
                continue
            src = "main"
            st, out = http("POST", f"/graphs/{G}/branches", {"name": name, "from": src})
            if st not in (200, 201):
                fail(step, "create", name, st, str(out)[:300])
                continue
            states[name] = dict(states[src])
            heads[name] = heads.get(src)
            bases[name] = dict(states[src])
        elif op == "delete":
            name = r.choice(live)
            st, out = http("DELETE", f"/graphs/{G}/branches/{name}")
            if st not in (200, 204):
                fail(step, "delete", name, st, str(out)[:300])
                continue
            del states[name]
            inc[name] = inc.get(name, 0) + 1
            heads.pop(name, None)
            bases.pop(name, None)
        elif op == "merge":
            name = r.choice(live)
            st, out = http("POST", f"/graphs/{G}/branches/merge", {"source": name, "target": "main"})
            if st == 200:
                # three-way merge of upserted keys against the merge base
                base, src_s, tgt = bases[name], states[name], states["main"]
                merged = dict(tgt)
                conflict = False
                for k in set(src_s) | set(tgt):
                    if src_s.get(k) != base.get(k):
                        if tgt.get(k) != base.get(k) and tgt.get(k) != src_s.get(k):
                            conflict = True
                        merged[k] = src_s[k]
                if conflict:
                    fail(step, "merge accepted though the model has a conflicting key", name)
                states["main"] = merged
                bases[name] = dict(src_s)
                for cc, (bn, ii) in origin.items():
                    if bn == name and ii == inc.get(name, 0):
                        merged_into_main.add(cc)
                c = (out.get("commit") or {}).get("graph_commit_id") if isinstance(out, dict) else None
                if c:
                    commits[c] = (dict(merged), heads.get("main"))
                    heads["main"] = c
            elif st == 409:
                base, src_s, tgt = bases[name], states[name], states["main"]
                if not any(src_s.get(k) != base.get(k) and tgt.get(k) != base.get(k) and tgt.get(k) != src_s.get(k) for k in set(src_s) | set(tgt)):
                    fail(step, "merge refused without a conflicting key", name, str(out)[:300])
                stats["merge409"] = stats.get("merge409", 0) + 1
            else:
                fail(step, "merge", name, st, str(out)[:300])
        # reads: the newest commits always, every commit on readall
        sample = list(commits) if op == "readall" else r.sample(list(commits), min(6, len(commits)))
        for c in sample:
            want, prev = commits[c]
            got, err = read_at(c)
            if err:
                bn, ii = origin.get(c, ("main", 0))
                branch_only_gone = bn != "main" and (bn not in states or ii != inc.get(bn, 0)) and c not in merged_into_main
                if c in seen_ok and not branch_only_gone:
                    fail(step, "commit read earlier now refused", c, "branch", bn, "merged", c in merged_into_main, err)
                elif branch_only_gone:
                    stats["refused_branch_only_gone"] = stats.get("refused_branch_only_gone", 0) + 1
                else:
                    stats["refused_reads"] = stats.get("refused_reads", 0) + 1
                continue
            if got != want:
                fail(step, "snapshot read differs", c, "want", sorted(want.items())[:6], "got", sorted(got.items())[:6])
            seen_ok[c] = got
            if r.random() < 0.3:
                st, out = http("GET", f"/graphs/{G}/commits/{c}/changes")
                if st != 200:
                    fail(step, "commit changes", c, st, str(out)[:300])
            if prev and r.random() < 0.3:
                st, out = http("GET", f"/graphs/{G}/diff?from={prev}&to={c}")
                if st == 200:
                    ids = {ch.get("id") for ch in (out.get("changes") or [])}
                    pw = commits[prev][0]
                    want_ids = {k for k in set(pw) | set(want) if pw.get(k) != want.get(k)}
                    if ids != want_ids:
                        fail(step, "diff ids", prev, c, "want", sorted(want_ids), "got", sorted(ids))
                elif st not in (409, 410):
                    fail(step, "diff", prev, c, st, str(out)[:300])
        if len(fails) > 15:
            break
    srv.terminate()
    srv.wait(60)
    print(json.dumps({"seed": SEED, "commits": len(commits), "stats": stats, "failures": len(fails)}))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
