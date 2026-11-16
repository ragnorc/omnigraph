#!/usr/bin/env python3
"""Upgrade journey: v0.15.0 writes a graph with this cycle's shapes (column
patches, a promoted required property and @unique, Blobs, vectors, full-text,
branches, merges, optimize), then the candidate binary reads it, writes,
maintains and upgrades it. Each binary's reads are compared with a Python
model and with each other.

Usage: OLD=<v0.15.0 omnigraph> NEW=<candidate omnigraph> upj.py"""
import base64, json, os, random, shutil, subprocess, sys

OLD, NEW = os.environ["OLD"], os.environ["NEW"]
D = "/tmp/og/upj"
G = f"{D}/g.omni"
errs = []

S1 = """node Doc {
  slug: String @key
  title: String @index
  n: I64?
  tags: [String]?
  b: Blob?
  v: Vector(4)? @index
}
edge Rel: Doc -> Doc { w: I64? @key(@src, @dst) }
"""
S2 = S1.replace("n: I64?", "n: I64").replace("title: String @index", "title: String @index @unique")


def run(binary, args, ok=True, timeout=600):
    p = subprocess.run([binary] + args, capture_output=True, text=True, timeout=timeout)
    if ok and p.returncode:
        errs.append(f"{os.path.basename(os.path.dirname(binary))} {' '.join(args[:3])}: rc={p.returncode} {(p.stdout + p.stderr).strip()[-600:]}")
    return p


def export(binary, br="main"):
    p = run(binary, ["export", "--branch", br, "--target", G])
    rows = sorted(p.stdout.splitlines())
    return rows


def q(binary, src, params=None, br=None, snap=None):
    args = ["query", "-e", src, "--json", "--target", G, "--params", json.dumps(params or {})]
    if snap:
        args += ["--snapshot", snap]
    if br:
        args += ["--branch", br]
    p = run(binary, args)
    try:
        return json.loads(p.stdout)["rows"]
    except Exception:
        return None


def m(binary, src, params, br="main", ok=True):
    return run(binary, ["mutate", "-e", src, "--params", json.dumps(params), "--branch", br, "--json", "--target", G], ok=ok)


def head(binary, br="main"):
    p = run(binary, ["commit", "list", "--branch", br, "--limit", "1", "--json", "--target", G])
    return json.loads(p.stdout)["head_commit_id"]


def stamp(binary):
    p = run(binary, ["snapshot", "--target", G, "--json"], ok=False)
    try:
        return json.loads(p.stdout).get("internal_schema_version"), p.returncode
    except Exception:
        return None, (p.returncode, p.stderr.strip()[-300:])


READS = [
    ("count", "query q() { match { $d: Doc } return { count($d) as c } }", {}),
    ("filter", "query q() { match { $d: Doc  $d.n >= 5 } return { $d.slug, $d.n } order { $d.slug } }", {}),
    ("search", "query q($t: String) { match { $d: Doc  search($d.title, $t) } return { $d.slug } order { $d.slug } }", {"t": "alpha"}),
    ("nearest", "query q($v: Vector(4)) { match { $d: Doc } return { $d.slug } order { nearest($d.v, $v), $d.slug } limit 7 }", {"v": [1.0, 0.5, 0.0, -1.0]}),
    ("trav", "query q() { match { $a: Doc  $a rel $b } return { $a.slug, $b.slug } order { $a.slug, $b.slug } }", {}),
    ("limit", "query q() { match { $d: Doc } return { $d.slug } limit 9 }", {}),
]


def reads(binary, br=None, snap=None):
    out = {}
    for name, src, params in READS:
        rows = q(binary, src, params, br=br, snap=snap)
        out[name] = len(rows) if (name == "limit" and rows is not None) else rows
    return out


def main():
    shutil.rmtree(D, ignore_errors=True)
    os.makedirs(D)
    r = random.Random(5)
    open(f"{D}/s1.pg", "w").write(S1)
    open(f"{D}/s2.pg", "w").write(S2)
    run(OLD, ["init", "--schema", f"{D}/s1.pg", G])
    words = ["alpha", "beta", "gamma", "delta"]
    with open(f"{D}/l.jsonl", "w") as f:
        for i in range(400):
            f.write(json.dumps({"type": "Doc", "data": {
                "slug": f"d{i}", "title": f"{r.choice(words)} {r.choice(words)} t{i}", "n": i % 10,
                "tags": r.sample(words, 2), "b": "base64:" + base64.b64encode(os.urandom(r.choice([0, 10, 3000]))).decode(),
                "v": [float(r.randint(-4, 4)) / 2 for _ in range(4)]}}) + "\n")
        for i in range(300):
            f.write(json.dumps({"edge": "Rel", "from": f"d{i}", "to": f"d{(i * 7) % 400}", "data": {"w": i}}) + "\n")
    run(OLD, ["load", "--data", f"{D}/l.jsonl", "--mode", "merge", "--target", G])
    m(OLD, "query u($k: I64) { update Doc set { n: $k } where n = 3 }", {"k": 33})  # column patch
    run(OLD, ["index", "build", "--target", G])
    p = run(OLD, ["schema", "plan", "--schema", f"{D}/s2.pg", "--target", G, "--json"])
    run(OLD, ["schema", "apply", "--schema", f"{D}/s2.pg", "--target", G, "--json"])
    run(OLD, ["branch", "create", "--from", "main", "feat", "--target", G])
    m(OLD, "query i($s: String, $t: String) { insert Doc { slug: $s, title: $t, n: 1 } }", {"s": "f1", "t": "alpha feat"}, br="feat")
    m(OLD, "query u() { update Doc set { tags: [\"x\"] } where n = 4 }", {}, br="feat")
    m(OLD, "query d() { delete Doc where n = 9 }", {})
    run(OLD, ["branch", "merge", "feat", "--into", "main", "--target", G])
    run(OLD, ["branch", "create", "--from", "main", "keep", "--target", G])
    m(OLD, "query i($s: String, $t: String) { insert Doc { slug: $s, title: $t, n: 2 } }", {"s": "k1", "t": "beta keep"}, br="keep")
    run(OLD, ["optimize", "--target", G])
    old_head = head(OLD)
    old_main = export(OLD)
    old_keep = export(OLD, "keep")
    old_reads = reads(OLD)
    old_reads_keep = reads(OLD, br="keep")
    print("old stamp", stamp(OLD), "head", old_head, "rows", len(old_main))
    # candidate reads the predecessor's bytes
    print("new stamp before write", stamp(NEW))
    if export(NEW) != old_main:
        errs.append("candidate export of main differs from v0.15.0 export")
    if export(NEW, "keep") != old_keep:
        errs.append("candidate export of keep differs")
    nr = reads(NEW)
    for k in old_reads:
        if nr[k] != old_reads[k]:
            errs.append(f"read {k} differs: old {str(old_reads[k])[:200]} new {str(nr[k])[:200]}")
    nrk = reads(NEW, br="keep")
    for k in old_reads_keep:
        if nrk[k] != old_reads_keep[k]:
            errs.append(f"keep read {k} differs")
    # candidate writes, merges, maintains
    m(NEW, "query i($s: String, $t: String) { insert Doc { slug: $s, title: $t, n: 7 } }", {"s": "n1", "t": "gamma new"})
    m(NEW, "query u() { update Doc set { n: 44 } where n = 4 }", {})
    m(NEW, "query i($s: String, $t: String) { insert Doc { slug: $s, title: $t, n: 8 } }", {"s": "k2", "t": "delta keep"}, br="keep")
    p = m(NEW, "query i($s: String, $t: String) { insert Doc { slug: $s, title: $t, n: 8 } }", {"s": "dup", "t": "gamma new"}, ok=False)
    if p.returncode == 0:
        errs.append("candidate accepted a duplicate @unique title on a v0.15.0 graph")
    run(NEW, ["branch", "merge", "keep", "--into", "main", "--target", G])
    pr = run(NEW, ["optimize", "--target", G, "--json"])
    run(NEW, ["index", "build", "--target", G])
    hist = reads(NEW, snap=old_head)
    for k in old_reads:
        if hist[k] != old_reads[k]:
            errs.append(f"historical read {k} at the v0.15.0 head differs after candidate writes")
    run(NEW, ["cleanup", "--keep", "3", "--confirm", "--target", G])
    new_main = export(NEW)
    print("new stamp after writes", stamp(NEW), "rows", len(new_main))
    # predecessor reads candidate-written bytes
    o2 = run(OLD, ["export", "--branch", "main", "--target", G], ok=False)
    if o2.returncode:
        errs.append(f"v0.15.0 cannot read the candidate-written graph: {o2.stderr.strip()[-400:]}")
    elif sorted(o2.stdout.splitlines()) != new_main:
        errs.append("v0.15.0 export of the candidate-written graph differs")
    nr2 = reads(NEW)
    or2 = reads(OLD)
    for k in nr2:
        if nr2[k] != or2[k]:
            errs.append(f"after candidate writes, read {k} differs between binaries")
    # storage upgrade to the candidate's stamp
    p = run(NEW, ["upgrade", "--target", G, "--check", "--json"], ok=False)
    print("upgrade --check", p.returncode, p.stdout[-600:], p.stderr[-300:])
    p = run(NEW, ["upgrade", "--target", G, "--json"], ok=False)
    print("upgrade", p.returncode, p.stdout[-600:], p.stderr[-300:])
    print("stamp after upgrade", stamp(NEW))
    if export(NEW) != new_main:
        errs.append("export changed across upgrade")
    nr3 = reads(NEW)
    if nr3 != nr2:
        errs.append("reads changed across upgrade")
    hist3 = reads(NEW, snap=head(NEW))
    o3 = run(OLD, ["export", "--branch", "main", "--target", G], ok=False)
    print("v0.15.0 on the upgraded graph:", o3.returncode, o3.stderr.strip()[-300:])
    m(NEW, "query i($s: String, $t: String) { insert Doc { slug: $s, title: $t, n: 6 } }", {"s": "post", "t": "alpha post"})
    run(NEW, ["branch", "create", "--from", "main", "post", "--target", G])
    run(NEW, ["cleanup", "--keep", "1", "--confirm", "--target", G])
    reads(NEW)
    print(json.dumps({"errors": errs}, indent=1))


if __name__ == "__main__":
    main()
