#!/usr/bin/env python3
"""Deep-history and many-branch journey on one local graph.

1. N single-row commits on main (inserts, column-patch updates, deletes), with
   `optimize` every 250 commits. The receipt of each commit is kept with the
   model state after it.
2. Write latency at the start and end of the history, to check that a commit's
   cost does not grow with the history (invariant 11).
3. Reads at a sample of historical commits (`--snapshot`) against the model.
4. `commit list` length and `diff` between two old commits against the model.
5. B branches created from random historical commits (GQ `branch create ...
   from commit`), one write each, several merged into main.
6. `cleanup --keep K --confirm`, then reads of retained commits must be
   unchanged and reads of reclaimed ones must fail with a typed error.

Usage: hscale.py <seed> <commits> <branches>"""
import json, os, random, shutil, subprocess, sys, time

OG = os.environ.get("OG", "omnigraph")
D = "/tmp/og/hscale"
SCHEMA = """node P {
  slug: String @key
  n: I64?
  s: String?
}
"""


def run(args, timeout=600):
    t = time.time()
    p = subprocess.run([OG] + args, capture_output=True, text=True, timeout=timeout)
    p.elapsed = time.time() - t
    return p


def fail(msg):
    print("FAIL " + msg, flush=True)
    open(f"{D}/FAIL.txt", "a").write(msg + "\n")


def read_at(g, snap=None, branch=None):
    args = ["query", "-e", "query q() { match { $p: P } return { $p.slug, $p.n, $p.s } }", "--json", "--target", g]
    if snap:
        args += ["--snapshot", snap]
    if branch:
        args += ["--branch", branch]
    p = run(args)
    if p.returncode:
        return None, p
    rows = json.loads(p.stdout)["rows"]
    return {r["p.slug"]: (r.get("p.n"), r.get("p.s")) for r in rows}, p


def main():
    seed, n_commits, n_branches = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
    r = random.Random(seed)
    shutil.rmtree(D, ignore_errors=True)
    os.makedirs(D)
    g = f"{D}/g.omni"
    open(f"{D}/s.pg", "w").write(SCHEMA)
    run(["init", "--schema", f"{D}/s.pg", g])
    st = {}
    hist = []  # (commit_id, state)
    lat = []
    keys = [f"k{i}" for i in range(300)]
    for i in range(n_commits):
        live = sorted(st)
        op = r.random()
        if op < 0.5 or len(live) < 5:
            k = r.choice(keys)
            row = (r.randint(0, 99), r.choice(["a", "b", None]))
            params = {"k": k, "n": row[0], "s": row[1]}
            src = "query m($k: String, $n: I64?, $s: String?) { insert P { slug: $k, n: $n, s: $s } }"
            new = dict(st); new[k] = row
        elif op < 0.9:
            k = r.choice(live)
            v = r.randint(0, 99)
            params = {"k": k, "v": v}
            src = "query m($k: String, $v: I64?) { update P set { n: $v } where slug = $k }"
            new = dict(st); new[k] = (v, st[k][1])
        else:
            k = r.choice(live)
            params = {"k": k}
            src = "query m($k: String) { delete P where slug = $k }"
            new = dict(st); del new[k]
        open(f"{D}/p.json", "w").write(json.dumps(params))
        p = run(["mutate", "-e", src, "--params-file", f"{D}/p.json", "--target", g, "--json"])
        if p.returncode:
            fail(f"commit {i} failed: {p.stderr[-500:]}")
            continue
        rec = json.loads(p.stdout)
        cid = rec["commit"]["graph_commit_id"]
        st = new
        hist.append((cid, dict(st)))
        lat.append(p.elapsed)
        if (i + 1) % 250 == 0:
            q = run(["optimize", "--target", g])
            print(f"commit {i+1}: write p50 last 50 = {sorted(lat[-50:])[25]:.3f}s optimize rc={q.returncode} {q.elapsed:.1f}s", flush=True)
            if q.returncode:
                fail("optimize: " + q.stderr[-500:])
    print(f"write latency first50 p50={sorted(lat[:50])[25]:.3f}s last50 p50={sorted(lat[-50:])[25]:.3f}s", flush=True)

    # reads at history
    t_reads = []
    for cid, s in r.sample(hist, min(60, len(hist))):
        got, p = read_at(g, snap=cid)
        t_reads.append(p.elapsed)
        if got is None:
            fail(f"read at {cid} failed: {p.stderr[-300:]}")
        elif got != s:
            fail(f"read at {cid} differs: model {len(s)} rows got {len(got)}; diff {[k for k in set(s)|set(got) if s.get(k)!=got.get(k)][:5]}")
    print(f"historical reads ok-checked {len(t_reads)} p50={sorted(t_reads)[len(t_reads)//2]:.3f}s", flush=True)
    got, p = read_at(g)
    print(f"head read {p.elapsed:.3f}s rows={len(got) if got else None}", flush=True)
    if got != st:
        fail("head read differs from model")

    p = run(["commit", "list", "--target", g, "--branch", "main", "--json"])
    cl = json.loads(p.stdout)["commits"]
    print(f"commit list {len(cl)} commits in {p.elapsed:.2f}s (expected >= {len(hist)+1})", flush=True)
    ids = {c["graph_commit_id"] for c in cl}
    missing = [c for c, _ in hist if c not in ids]
    if missing:
        fail(f"commit list missing {len(missing)} of our commits, e.g. {missing[:2]}")

    # diff between two old commits
    a, b = sorted(r.sample(range(len(hist)), 2))
    p = run(["diff", "--from", hist[a][0], "--to", hist[b][0], "--target", g, "--json"])
    print(f"diff {a}->{b} rc={p.returncode} {p.elapsed:.2f}s", flush=True)
    if p.returncode:
        fail("diff failed: " + p.stderr[-500:])
    else:
        sa, sb = hist[a][1], hist[b][1]
        exp_changed = {k for k in set(sa) | set(sb) if sa.get(k) != sb.get(k)}
        open(f"{D}/diff.json", "w").write(p.stdout)
        txt = p.stdout
        notfound = [k for k in exp_changed if f'"{k}"' not in txt]
        if notfound:
            fail(f"diff {a}->{b} does not mention changed keys {notfound[:5]} ({len(notfound)} of {len(exp_changed)})")

    # branches from history
    merged = 0
    for j in range(n_branches):
        idx = r.randrange(len(hist))
        cid, s = hist[idx]
        name = f"h{j}"
        p = run(["mutate", "-e", f'branch create "{name}" from commit "{cid}"', "--target", g, "--json"])
        if p.returncode:
            fail(f"branch create from {cid}: {p.stderr[-300:]}")
            continue
        got, q = read_at(g, branch=name)
        if got != s:
            fail(f"branch {name} from commit {idx} does not read that commit's state")
        k = f"br{j}"
        src = f'query m() {{ insert P {{ slug: "{k}", n: {j} }} }}'
        q = run(["mutate", "-e", src, "--branch", name, "--target", g])
        if q.returncode:
            fail(f"write on {name}: {q.stderr[-300:]}")
            continue
        if r.random() < 0.3:
            m = run(["branch", "merge", name, "--into", "main", "--target", g, "--json"])
            if m.returncode == 0:
                merged += 1
                st[k] = (j, None)
                # rows deleted on main since the fork stay deleted, rows changed stay changed
            else:
                print(f"merge {name} (from commit {idx}) refused: {(m.stdout+m.stderr)[-300:]}", flush=True)
    print(f"branches {n_branches}, merged {merged}", flush=True)
    got, p = read_at(g)
    if got != st:
        dd = [k for k in set(st) | set(got or {}) if st.get(k) != (got or {}).get(k)]
        fail(f"main after merges differs on {len(dd)} keys, e.g. {[(k, st.get(k), (got or {}).get(k)) for k in dd[:5]]}")
    p = run(["branch", "list", "--target", g, "--json"])
    print(f"branch list {p.elapsed:.2f}s", flush=True)

    # cleanup
    K = 100
    p = run(["cleanup", "--keep", str(K), "--confirm", "--target", g, "--json"], timeout=1800)
    print(f"cleanup rc={p.returncode} {p.elapsed:.1f}s {p.stdout[-300:]}", flush=True)
    if p.returncode:
        fail("cleanup: " + p.stderr[-500:])
    got, q = read_at(g)
    if got != st:
        fail("head differs after cleanup")
    ok_old = refused = wrong = 0
    for idx in r.sample(range(len(hist)), min(40, len(hist))):
        cid, s = hist[idx]
        got, q = read_at(g, snap=cid)
        if got is None:
            refused += 1
            if idx >= len(hist) - K + 5:
                fail(f"retained commit {idx} unreadable after cleanup: {q.stderr[-300:]}")
        elif got != s:
            wrong += 1
            fail(f"commit {idx} reads wrong rows after cleanup")
        else:
            ok_old += 1
    print(f"after cleanup: {ok_old} historical reads ok, {refused} refused, {wrong} wrong", flush=True)
    for name in [f"h{j}" for j in range(min(5, n_branches))]:
        got, q = read_at(g, branch=name)
        if got is None:
            fail(f"branch {name} unreadable after cleanup: {q.stderr[-300:]}")
    print("done", flush=True)


if __name__ == "__main__":
    main()
