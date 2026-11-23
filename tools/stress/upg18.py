#!/usr/bin/env python3
"""Branch set across the format 18 storage upgrade (#1504).

The previous release (OLD) writes a standalone graph with many branch shapes:
nested names, branches of branches, a branch started at a past commit, merged
branches, deleted branches, names deleted and created again, a deleted parent
whose child stays live, and names with characters a path or an object key must
escape. The candidate (NEW) then reads every branch, its commit list and
older commits by id, before and after `omnigraph upgrade`, and the answers
must be equal. Then NEW writes, merges, creates and deletes on the upgraded
graph. KILLS=N copies the format-17 graph N times and SIGKILLs the upgrade
after a random delay; the rerun must complete, and the reads must be equal.

Usage: OLD=... NEW=... upg18.py <seed>"""
import json, os, random, shutil, signal, subprocess, sys, time

OLD, NEW = os.environ["OLD"], os.environ["NEW"]
SEED = int(sys.argv[1]) if len(sys.argv) > 1 else 1
KILLS = int(os.environ.get("KILLS", "0"))
NB = int(os.environ.get("UB_N", "24"))
D = f"/tmp/og/upg18-{SEED}"
G = f"{D}/g.omni"
r = random.Random(SEED)
errs = []

SCHEMA = """node Item {
  slug: String @key
  v: I64?
}
edge Rel: Item -> Item { @key(@src, @dst) }
"""
ODD = ["x^y", "dots.v1.2", "uni-é", "Up.Case-1", "_u_", "9digit", "a.b.c", "x--y", "x^^y", "ü.ß"]


def run(binary, args, ok=True, timeout=600, g=None):
    p = subprocess.run([binary] + args + ["--target", g or G], capture_output=True, text=True, timeout=timeout)
    if ok and p.returncode:
        errs.append(f"{os.path.basename(binary)} {' '.join(args[:4])}: rc={p.returncode} {(p.stdout + p.stderr).strip()[-500:]}")
        print("ERR", errs[-1], flush=True)
    return p


def j(p):
    try:
        return json.loads(p.stdout)
    except Exception:
        return None


def load(binary, br, rows, mode="merge", g=None):
    path = f"{D}/l.jsonl"
    with open(path, "w") as f:
        for x in rows:
            f.write(json.dumps(x) + "\n")
    return run(binary, ["load", "--data", path, "--mode", mode, "--branch", br, "--json"], g=g)


def items(br, n0, n1, tag):
    return [{"type": "Item", "data": {"slug": f"{tag}{i}", "v": i}} for i in range(n0, n1)]


def snapshot(binary, g=None, deep=True):
    """Everything a reader can see: branch list, per branch export and commit ids, older commits by id."""
    out = {}
    p = run(binary, ["branch", "list", "--json"], g=g)
    bl = j(p) or {}
    names = sorted(bl.get("branches") or [])
    out["branches"] = names
    for b in names:
        e = run(binary, ["export", "--branch", b], g=g)
        out[f"export:{b}"] = sorted(e.stdout.splitlines()) if e.returncode == 0 else f"rc={e.returncode} {e.stderr[-200:]}"
        c = run(binary, ["commit", "list", "--branch", b, "--limit", "1000", "--json"], g=g)
        cj = j(c) or {}
        ids = [x.get("graph_commit_id") for x in cj.get("commits") or []]
        out[f"commits:{b}"] = ids
        if deep:
            for cid in ids[: min(len(ids), 6)]:
                q = run(binary, ["query", "-e", "query a() { match { $i: Item } return { $i.slug } order { $i.slug asc } }",
                                 "--snapshot", cid, "--json"], g=g, ok=False)
                qj = j(q)
                out[f"at:{b}:{cid}"] = [list(x.values())[0] for x in qj["rows"]] if qj and "rows" in qj else f"rc={q.returncode} {(q.stdout+q.stderr)[-200:]}"
    return out


def compare(a, b, what):
    bad = 0
    for k in sorted(set(a) | set(b)):
        if a.get(k) != b.get(k):
            bad += 1
            if bad <= 6:
                x, y = a.get(k), b.get(k)
                if isinstance(x, list) and isinstance(y, list):
                    x, y = (sorted(set(x) - set(y))[:4], sorted(set(y) - set(x))[:4])
                errs.append(f"{what}: {k}: {str(x)[:300]} != {str(y)[:300]}")
                print("ERR", errs[-1], flush=True)
    return bad


def build():
    shutil.rmtree(D, ignore_errors=True)
    os.makedirs(D)
    open(f"{D}/s.pg", "w").write(SCHEMA)
    run(OLD, ["init", "--schema", f"{D}/s.pg"])
    load(OLD, "main", items("main", 0, 50, "m"))
    live = ["main"]
    deleted = []
    n = 0
    commits = []
    for k in range(NB):
        n += 1
        x = r.random()
        if k < len(ODD):
            name = f"odd/{ODD[k]}"
        elif x < 0.3 and len(live) > 1:
            par = r.choice([b for b in live if b != "main"])
            name = f"kid/{par.replace('/', '_')}-c{k}"
        else:
            name = f"f{k % 7}/b{k}"
        # names must not be path prefixes of live ones
        if any(b == name or b.startswith(name + "/") or name.startswith(b + "/") for b in live if b != "main"):
            name = f"z{k}"
        args = ["branch", "create", name, "--json"]
        if name.startswith("kid/") and x < 0.3:
            args += ["--from", par]
        elif commits and r.random() < 0.2:
            args += ["--from-commit", r.choice(commits)]
        p = run(OLD, args, ok=False)
        if p.returncode:
            print("create refused", name, (p.stdout + p.stderr)[-200:])
            continue
        live.append(name)
        load(OLD, name, items(name, 0, r.randrange(1, 30), f"{name}#"))
        c = run(OLD, ["commit", "list", "--branch", "main", "--limit", "5", "--json"])
        commits += [x["graph_commit_id"] for x in (j(c) or {}).get("commits") or []]
        y = r.random()
        if y < 0.25:
            run(OLD, ["branch", "merge", name, "--into", "main", "--json"])
        if y < 0.12 or 0.25 <= y < 0.4:
            # delete; children (if any) stay live
            run(OLD, ["branch", "delete", name, "--json"])
            live.remove(name)
            deleted.append(name)
        if r.random() < 0.3:
            load(OLD, "main", items("main", 100 + k * 10, 105 + k * 10, "m"))
    # recreate some deleted names
    for name in deleted[:4]:
        if any(b.startswith(name + "/") for b in live):
            continue
        if run(OLD, ["branch", "create", name, "--json"], ok=False).returncode == 0:
            live.append(name)
            load(OLD, name, items(name, 0, 3, f"{name}#re"))
    print("live", len(live), "deleted", len(deleted))


def stamp(binary, g=None):
    p = run(binary, ["upgrade", "--check", "--json"], ok=False, g=g)
    return (p.returncode, (p.stdout + p.stderr).strip()[-400:])


def post_upgrade_writes(g):
    """NEW writes on the upgraded graph: per-branch load, merge, delete, create of a fresh and of a
    retired name, cleanup; then all branches readable."""
    bl = (j(run(NEW, ["branch", "list", "--json"], g=g)) or {}).get("branches") or []
    for b in bl[:8]:
        load(NEW, b, items(b, 500, 503, f"{b}#post"), g=g)
    others = [b for b in bl if b != "main"]
    for b in others[:3]:
        run(NEW, ["branch", "merge", b, "--into", "main", "--json"], g=g)
    for b in others[3:6]:
        run(NEW, ["branch", "delete", b, "--json"], g=g)
        run(NEW, ["branch", "create", b, "--json"], g=g)
        load(NEW, b, items(b, 900, 901, f"{b}#again"), g=g)
    run(NEW, ["branch", "create", "post/new", "--json"], g=g)
    run(NEW, ["cleanup", "--keep", "1", "--confirm", "--yes", "--json"], g=g, ok=False)
    after = snapshot(NEW, g=g, deep=False)
    for k, v in after.items():
        if isinstance(v, str) and v.startswith("rc="):
            errs.append(f"after writes and cleanup: {k}: {v}")
            print("ERR", errs[-1], flush=True)
    return after


def main():
    build()
    print("stamp before", stamp(NEW))
    s_old = snapshot(OLD)
    s_new17 = snapshot(NEW)
    compare(s_old, s_new17, "OLD vs NEW on format 17")
    base = f"{D}/base17"
    shutil.copytree(G, base)
    p = run(NEW, ["upgrade", "--json"], ok=True)
    print("upgrade", p.returncode, p.stdout[-800:])
    s_new18 = snapshot(NEW)
    compare(s_new17, s_new18, "NEW before vs after upgrade")
    if os.path.isdir(f"{G}/_refs/branches"):
        left = []
        for root, _, files in os.walk(f"{G}/_refs/branches"):
            left += files
        if left:
            errs.append(f"ref files left after upgrade: {left[:5]}")
    o = run(OLD, ["branch", "list", "--json"], ok=False)
    if o.returncode == 0:
        errs.append("OLD reads the upgraded graph: " + o.stdout[-200:])
    post_upgrade_writes(G)
    for t in range(KILLS):
        g = f"{D}/k{t}.omni"
        shutil.rmtree(g, ignore_errors=True)
        shutil.copytree(base, g)
        delay = r.uniform(0.01, float(os.environ.get("KMAX", "2.5")))
        pr = subprocess.Popen([NEW, "upgrade", "--json", "--target", g], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        time.sleep(delay)
        killed = pr.poll() is None
        if killed:
            pr.send_signal(signal.SIGKILL)
        pr.wait()
        mid = snapshot(NEW, g=g, deep=False) if killed else None
        p = run(NEW, ["upgrade", "--json"], g=g, ok=False)
        if p.returncode:
            errs.append(f"kill {t} after {delay:.2f}s: rerun of upgrade rc={p.returncode} {(p.stdout+p.stderr)[-400:]}")
            print("ERR", errs[-1], flush=True)
            continue
        s = snapshot(NEW, g=g)
        bad = compare(s_new18, s, f"kill {t} after {delay:.2f}s (killed={killed})")
        if mid is not None:
            # reads in the middle of a stopped upgrade: either the old answer or a clean refusal
            for k, v in mid.items():
                if isinstance(v, str) and v.startswith("rc=") and "upgrade" not in v.lower() and "format" not in v.lower():
                    print("mid-upgrade read", k, v[:200])
        post_upgrade_writes(g)
        print("kill", t, f"{delay:.2f}", "killed" if killed else "finished", "diffs", bad, flush=True)
    print(json.dumps({"seed": SEED, "errors": len(errs)}))
    sys.exit(1 if errs else 0)


if __name__ == "__main__":
    main()
