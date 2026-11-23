#!/usr/bin/env python3
"""Limit fuzzer. An unfiltered `limit` now reaches Lance as Scanner::limit
(#1342), which plans fragments from manifest row counts. This fuzzer builds
many small fragments, then deletes, updates, merge loads, optimizes, branches
and merges at random. After every step it reads `return { ... } limit K` with
no filter on the branch, on a branch that forked earlier, and at an earlier
commit, for several K. Each answer must hold min(K, live) distinct rows, all
of them live, with the values the model holds."""
import json, os, random, shutil, subprocess, sys

OG = os.environ.get("OG", "omnigraph")
ROOT = "/tmp/og/lfuzzrun"
FAIL = "/tmp/og/lfuzzfail"
SCHEMA = """node P {
  slug: String @key
  n: I64?
  s: String?
}
node U {
  name: String
  n: I64?
}
"""


class Mismatch(Exception):
    pass


def run(args, timeout=300):
    return subprocess.run([OG] + args, capture_output=True, text=True, timeout=timeout)


def ok(p, what):
    if p.returncode:
        raise Exception(f"{what} {p.args[1:4]}: rc={p.returncode} {(p.stdout + p.stderr)[-1500:]}")
    return p


def head(g, br):
    p = ok(run(["commit", "list", "--branch", br, "--limit", "1", "--json", "--target", g]), "commit list")
    return json.loads(p.stdout)["head_commit_id"]


EVOLVED = set()


def read_limit(g, k, br=None, snap=None, ty="P"):
    if ty == "P" and g in EVOLVED and not snap:
        src = f"query q() {{ match {{ $p: P }} return {{ $p.slug, $p.n, $p.z }} limit {k} }}"
    elif ty == "P":
        src = f"query q() {{ match {{ $p: P }} return {{ $p.slug, $p.n }} limit {k} }}"
    else:
        src = f"query q() {{ match {{ $p: U }} return {{ $p.@id, $p.name, $p.n }} limit {k} }}"
    args = ["query", "-e", src, "--json", "--target", g]
    if snap:
        args += ["--snapshot", snap]
    else:
        args += ["--branch", br]
    p = ok(run(args), "query")
    return json.loads(p.stdout)["rows"]


def check(g, model, k, br=None, snap=None, ty="P", label=""):
    rows = read_limit(g, k, br, snap, ty)
    want = min(k, len(model))
    keyf = "p.slug" if ty == "P" else "p.@id"
    rows = [{"p.slug": r["p"]["slug"], "p.n": r["p"].get("n")} if "p" in r else r for r in rows]
    keys = [r[keyf] for r in rows]
    errs = []
    if len(rows) != want:
        errs.append(f"{label} {ty} limit {k}: got {len(rows)} rows, want {want} (live {len(model)})")
    if len(set(keys)) != len(keys):
        errs.append(f"{label} {ty} limit {k}: duplicate keys {sorted(keys)}")
    for r in rows:
        key = r[keyf]
        if key not in model:
            errs.append(f"{label} {ty} limit {k}: row {key} is not live")
        elif r.get("p.n") != model[key]:
            errs.append(f"{label} {ty} limit {k}: row {key} n={r.get('p.n')} want {model[key]}")
    if errs:
        raise Mismatch("\n".join(errs))


def mut(g, br, src, params):
    return run(["mutate", "-e", src, "--params", json.dumps(params), "--branch", br, "--json", "--target", g])


def step(r, g, d, br, st, ust, log):
    op = r.choices(["ins", "load", "del", "delrange", "upd", "uins", "udel", "opt"], [5, 2, 3, 1, 2, 2, 1, 1])[0]
    if op == "ins":
        k = f"k{r.randint(0, 60)}"
        n = r.randint(0, 9)
        p = mut(g, br, "query q($s: String, $n: I64) { insert P { slug: $s, n: $n } }", {"s": k, "n": n})
        if p.returncode == 0:
            st[k] = n
    elif op == "load":
        rows = {f"k{r.randint(0, 60)}": r.randint(0, 9) for _ in range(r.randint(1, 20))}
        with open(f"{d}/l.jsonl", "w") as f:
            for k, n in rows.items():
                f.write(json.dumps({"type": "P", "data": {"slug": k, "n": n, "s": "x"}}) + "\n")
        p = run(["load", "--data", f"{d}/l.jsonl", "--mode", "merge", "--branch", br, "--target", g])
        if p.returncode == 0:
            st.update(rows)
    elif op == "del":
        n = r.randint(0, 9)
        p = mut(g, br, "query q($n: I64) { delete P where n = $n }", {"n": n})
        if p.returncode == 0:
            for k in [k for k, v in st.items() if v == n]:
                del st[k]
    elif op == "delrange":
        lo = r.randint(0, 9)
        p = mut(g, br, "query q($n: I64) { delete P where n >= $n }", {"n": lo})
        if p.returncode == 0:
            for k in [k for k, v in st.items() if v is not None and v >= lo]:
                del st[k]
    elif op == "upd":
        a, b = r.randint(0, 9), r.randint(0, 9)
        p = mut(g, br, "query q($a: I64, $b: I64) { update P set { n: $b } where n = $a }", {"a": a, "b": b})
        if p.returncode == 0:
            for k in st:
                if st[k] == a:
                    st[k] = b
    elif op == "uins":
        n = r.randint(0, 9)
        # U is unkeyed; its live rows are read back by export in case()
        p = mut(g, br, "query q($s: String, $n: I64) { insert U { name: $s, n: $n } }", {"s": f"u{r.randint(0, 999)}", "n": n})
    elif op == "udel":
        n = r.randint(0, 9)
        p = mut(g, br, "query q($n: I64) { delete U where n = $n }", {"n": n})
    else:
        p = run(["optimize", "--target", g], timeout=600)
    log.append(f"{br} {op} rc={p.returncode} {p.stderr.strip()[-300:] if p.returncode else ''}")
    return op


def export_u(g, br=None, snap=None):
    args = ["export", "--type", "U", "--target", g]
    args += ["--snapshot", snap] if snap else ["--branch", br]
    p = run(args)
    if p.returncode:
        # export may not take --snapshot; fall back to a query
        raise Exception("export U " + p.stderr[-500:])
    out = {}
    for line in p.stdout.splitlines():
        if line.strip():
            rec = json.loads(line)
            if rec.get("type") == "U":
                out[rec["id"]] = rec["data"].get("n")
    return out


def case(seed):
    r = random.Random(seed)
    d = os.path.join(ROOT, str(seed))
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    g = os.path.join(d, "g.omni")
    open(f"{d}/s.pg", "w").write(SCHEMA)
    ok(run(["init", "--schema", f"{d}/s.pg", g]), "init")
    log = []
    st, ust = {}, {}
    hist = []  # (commit, model) on main
    states = {"main": st}
    forks = {}
    for i in range(r.randint(25, 60)):
        br = "main"
        if forks and r.random() < 0.35:
            br = r.choice(list(forks))
        if r.random() < 0.06 and len(forks) < 3:
            name = f"b{i}"
            ok(run(["branch", "create", "--from", "main", name, "--target", g]), "branch create")
            forks[name] = dict(states["main"])
            states[name] = forks[name]
            log.append(f"branch create {name}")
            continue
        if forks and r.random() < 0.05:
            name = r.choice(list(forks))
            # only merge when the branch and main touched disjoint keys is hard to
            # guarantee; merge and accept the engine's result only when it reports
            # no conflict, then re-derive main from an export.
            p = run(["branch", "merge", name, "--into", "main", "--json", "--target", g], timeout=600)
            log.append(f"merge {name} rc={p.returncode} {(p.stdout + p.stderr).strip()[-300:]}")
            if p.returncode == 0:
                ex = run(["export", "--type", "P", "--branch", "main", "--target", g])
                m = {}
                for line in ex.stdout.splitlines():
                    if line.strip():
                        rec = json.loads(line)
                        m[rec["data"]["slug"]] = rec["data"].get("n")
                states["main"].clear()
                states["main"].update(m)
            continue
        if g not in EVOLVED and not forks and r.random() < 0.05:
            open(f"{d}/s2.pg", "w").write(SCHEMA.replace("  s: String?\n}", "  s: String?\n  z: String?\n}", 1))
            p = run(["schema", "apply", "--schema", f"{d}/s2.pg", "--target", g, "--json"])
            log.append(f"schema apply add z rc={p.returncode} {p.stderr.strip()[-300:] if p.returncode else ''}")
            if p.returncode == 0:
                EVOLVED.add(g)
            continue
        step(r, g, d, br, states[br], ust, log)
        if br == "main" and r.random() < 0.3:
            hist.append((head(g, "main"), dict(states["main"])))
        try:
            for name, model in states.items():
                for k in sorted({1, 2, 3, r.randint(1, 70), len(model), len(model) + 1} - {0}):
                    check(g, model, k, br=name, label=name)
            if hist and r.random() < 0.5:
                c, model = r.choice(hist)
                for k in sorted({1, r.randint(1, 70), len(model) + 1} - {0}):
                    check(g, model, k, snap=c, label=f"snapshot {c[:16]}")
            um = export_u(g, br="main")
            for k in sorted({1, r.randint(1, 10), len(um) + 1} - {0}):
                check(g, um, k, br="main", ty="U", label="main")
        except Mismatch as e:
            raise Mismatch("\n".join(log) + "\n" + str(e))
    shutil.rmtree(d, ignore_errors=True)


def main():
    start, count = int(sys.argv[1]), int(sys.argv[2])
    os.makedirs(FAIL, exist_ok=True)
    bad = 0
    for seed in range(start, start + count):
        try:
            case(seed)
            print(f"seed {seed} ok", flush=True)
        except Mismatch as e:
            bad += 1
            open(f"{FAIL}/{seed}.txt", "w").write(str(e))
            print(f"seed {seed} MISMATCH\n{str(e)[-1500:]}", flush=True)
        except Exception as e:
            print(f"seed {seed} ERROR {str(e)[-800:]}", flush=True)
    print(f"done {count} seeds, {bad} mismatches")


if __name__ == "__main__":
    main()
