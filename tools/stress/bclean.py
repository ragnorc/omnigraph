#!/usr/bin/env python3
"""cleanup after many branch lives at format 18 (#1504): branches created from
main, from other live branches and at past commits, written, merged, deleted
and created again under the same names, with cleanup at random points
(--keep 1 or a generous keep). After every cleanup: the branch list equals
the model, every live branch's export equals the model, every live branch's
commit list still opens, a deleted name can be created again and reads its
source, and no directory of a retired branch's native ref remains under any
table's tree/ after a cleanup with --keep 1.

Usage: OG=... bclean.py <seed> <steps>"""
import json, os, random, re, shutil, subprocess, sys

OG = os.environ.get("OG", "omnigraph")
SEED, STEPS = int(sys.argv[1]), int(sys.argv[2])
D = f"/tmp/og/bclean-{SEED}"
G = f"{D}/g.omni"
r = random.Random(SEED)
errs = []
SCHEMA = """node Item {
  slug: String @key
  v: I64?
}
edge Rel: Item -> Item { @key(@src, @dst) }
"""


def run(args, ok=True):
    p = subprocess.run([OG] + args + ["--target", G], capture_output=True, text=True, timeout=600)
    if ok and p.returncode:
        err("rc", " ".join(args[:4]), p.returncode, (p.stdout + p.stderr)[-400:])
    return p


def err(*a):
    errs.append(a)
    print("ERR", *a, flush=True)


def j(p):
    try:
        return json.loads(p.stdout)
    except Exception:
        return {}


def load(br, rows, mode="merge"):
    with open(f"{D}/l.jsonl", "w") as f:
        for x in rows:
            f.write(json.dumps(x) + "\n")
    return run(["load", "--data", f"{D}/l.jsonl", "--mode", mode, "--branch", br, "--json"])


def export(br):
    p = run(["export", "--branch", br], ok=False)
    if p.returncode:
        return None
    return sorted(p.stdout.splitlines())


def rows_of(lines):
    out = set()
    for ln in lines:
        o = json.loads(ln)
        if o.get("type") == "Item":
            out.add(o["data"]["slug"])
        elif o.get("edge") == "Rel":
            out.add(("E", o["from"], o["to"]))
    return out


def main():
    shutil.rmtree(D, ignore_errors=True)
    os.makedirs(D)
    open(f"{D}/s.pg", "w").write(SCHEMA)
    run(["init", "--schema", f"{D}/s.pg"])
    model = {"main": set()}
    n = 0

    def write(br):
        nonlocal n
        k = r.randrange(1, 6)
        rows, adds = [], set()
        for _ in range(k):
            n += 1
            rows.append({"type": "Item", "data": {"slug": f"{br}#{n}", "v": n}})
            adds.add(f"{br}#{n}")
        cur = sorted(x for x in model[br] if isinstance(x, str))
        if cur and r.random() < 0.5:
            a, b = r.choice(cur), r.choice(sorted(adds))
            rows.append({"edge": "Rel", "from": a, "to": b, "data": {}})
            adds.add(("E", a, b))
        if load(br, rows).returncode == 0:
            model[br] |= adds

    write("main")
    names = [f"t{i}/b" for i in range(6)] + ["solo", "x.y-z"]
    cleanups = 0
    for step in range(STEPS):
        x = r.random()
        live = sorted(b for b in model if b != "main")
        if x < 0.25:
            name = r.choice(names)
            if name in model:
                continue
            src = "main"
            args = ["branch", "create", name, "--json"]
            if live and r.random() < 0.3:
                src = r.choice(live)
                args += ["--from", src]
            p = run(args, ok=False)
            if p.returncode == 0:
                model[name] = set(model[src])
            elif "branch_exists" not in p.stdout and "prefix" not in p.stdout and "shares its physical" not in p.stdout:
                err("create", name, p.stdout[-300:])
        elif x < 0.55:
            write(r.choice(live + ["main"]))
        elif x < 0.68 and live:
            b = r.choice(live)
            p = run(["branch", "merge", b, "--into", "main", "--json"], ok=False)
            if p.returncode == 0:
                model["main"] |= model[b]
            else:
                err("merge", b, p.stdout[-300:])
        elif x < 0.85 and live:
            b = r.choice(live)
            if run(["branch", "delete", b, "--json"]).returncode == 0:
                del model[b]
        else:
            keep = r.choice(["1", "1", "50"])
            p = run(["cleanup", "--keep", keep, "--confirm", "--yes", "--json"], ok=False)
            cleanups += 1
            if p.returncode:
                err("cleanup", keep, p.stdout[-500:], p.stderr[-300:])
            check(model, f"after cleanup {cleanups} keep {keep} step {step}", keep == "1")
    check(model, "end", False)
    p = run(["cleanup", "--keep", "1", "--confirm", "--yes", "--json"], ok=False)
    check(model, "final cleanup", True)
    print(json.dumps({"seed": SEED, "steps": STEPS, "cleanups": cleanups + 1, "live": len(model), "errors": len(errs)}))
    sys.exit(1 if errs else 0)


def check(model, where, swept):
    bl = j(run(["branch", "list", "--json"])).get("branches") or []
    if sorted(bl) != sorted(model):
        err(where, "branch list", sorted(set(bl) ^ set(model)))
    for b in sorted(model):
        e = export(b)
        if e is None:
            err(where, "export refused", b)
            continue
        got = rows_of(e)
        if got != model[b]:
            err(where, "export", b, "missing", sorted(map(str, model[b] - got))[:4], "extra", sorted(map(str, got - model[b]))[:4])
        c = run(["commit", "list", "--branch", b, "--limit", "1000", "--json"], ok=False)
        if c.returncode:
            err(where, "commit list", b, c.stdout[-300:])
    if swept:
        # native refs are <name>.<ULID>; a live one per live name, none for retired names after keep 1
        detail = j(run(["branch", "list", "--detailed", "--json"], ok=False)).get("details") or []
        # a live branch forked from an earlier life keeps that life's tree (docs: cleanup keeps data
        # other branches still need), so judge only names no live branch was forked from
        forked_from = {d.get("from") for d in detail}
        trees = set()
        for root, dirs, _ in os.walk(G):
            if "/tree/" in root + "/" and re.search(r"\.[0-9A-HJKMNP-TV-Z]{26}$", root):
                trees.add(root.split("/tree/", 1)[1])
                dirs[:] = []
        per = {}
        for t in trees:
            m = re.match(r"^(.*)\.([0-9A-HJKMNP-TV-Z]{26})$", t)
            if m:
                per.setdefault(m.group(1), set()).add(m.group(2))
        for name, lives in per.items():
            if len(lives) > 1 and name not in forked_from:
                err(where, "more than one life of a name keeps a tree after cleanup --keep 1", name, sorted(lives))


if __name__ == "__main__":
    main()
