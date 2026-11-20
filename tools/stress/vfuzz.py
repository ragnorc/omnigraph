#!/usr/bin/env python3
"""Vector search differential: nearest() with filters and traversal vs exact
squared-L2 brute force; before and after index build + optimize. A result that
claims `exhaustive` must contain no row nearer than a returned one."""
import json, os, random, shutil, subprocess, sys, struct

OG = os.environ.get("OG", "omnigraph")
ROOT = "/tmp/og/vfuzzrun"
FAIL = "/tmp/og/vfuzzfail"
DIM = 4
SCHEMA = f"""node D {{
  slug: String @key
  n: I64?
  g: String?
  v: Vector({DIM})? @index
}}
node T {{
  slug: String @key
}}
edge Has: T -> D
"""


def f32(x):
    return struct.unpack("f", struct.pack("f", x))[0]


def d2(a, b):
    return sum((f32(x) - f32(y)) ** 2 for x, y in zip(a, b))


class Mismatch(Exception):
    pass


def run(args):
    return subprocess.run([OG] + args, capture_output=True, text=True, timeout=600)


def case(seed):
    r = random.Random(seed)
    n = r.choice([5, 40, 300, 1200])
    docs = []
    for i in range(n):
        v = None if r.random() < 0.15 else [round(r.uniform(-3, 3), r.choice([0, 1, 3])) for _ in range(DIM)]
        docs.append({"slug": f"d{i:04d}", "n": None if r.random() < .2 else r.randint(0, 9), "g": r.choice([None, "x", "y"]), "v": v})
    tags = [f"t{j}" for j in range(4)]
    has = sorted({(r.choice(tags), r.choice(docs)["slug"]) for _ in range(n)})
    d = f"{ROOT}/{seed}"
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    open(f"{d}/s.pg", "w").write(SCHEMA)
    lines = [json.dumps({"type": "D", "data": {k: v for k, v in x.items() if v is not None}}) for x in docs]
    lines += [json.dumps({"type": "T", "data": {"slug": t}}) for t in tags]
    lines += [json.dumps({"edge": "Has", "from": t, "to": s}) for t, s in has]
    open(f"{d}/d.jsonl", "w").write("\n".join(lines) + "\n")
    g = f"{d}/g.omni"
    for a in (["init", "--schema", f"{d}/s.pg", g], ["load", "--data", f"{d}/d.jsonl", "--mode", "overwrite", "--target", g]):
        p = run(a)
        if p.returncode:
            raise Mismatch(f"setup {a}: {p.stderr[-800:]}")
    by = {x["slug"]: x for x in docs}
    qs = []
    for qi in range(6):
        k = r.choice([1, 3, 10, 50])
        qv = [round(r.uniform(-3, 3), 2) for _ in range(DIM)]
        kind = r.choice(["plain", "filter", "trav"])
        if kind == "plain":
            src = f"query q{qi}($q: Vector({DIM})) {{ match {{ $d: D }} return {{ $d.slug, nearest($d.v, $q) as dist }} order {{ nearest($d.v, $q) }} limit {k} }}"
            cand = [x for x in docs if x["v"] is not None]
        elif kind == "filter":
            c = r.randint(0, 9)
            src = f"query q{qi}($q: Vector({DIM})) {{ match {{ $d: D\n $d.n >= {c} }} return {{ $d.slug, nearest($d.v, $q) as dist }} order {{ nearest($d.v, $q) }} limit {k} }}"
            cand = [x for x in docs if x["v"] is not None and x["n"] is not None and x["n"] >= c]
        else:
            t = r.choice(tags)
            src = f"query q{qi}($q: Vector({DIM})) {{ match {{ $t: T {{ slug: \"{t}\" }}\n $t has $d }} return {{ $d.slug, nearest($d.v, $q) as dist }} order {{ nearest($d.v, $q) }} limit {k} }}"
            members = {s for tt, s in has if tt == t}
            cand = [by[s] for s in sorted(members) if by[s]["v"] is not None]
        ranked = sorted(cand, key=lambda x: (d2(x["v"], qv), x["slug"]))
        qs.append((f"q{qi}", src, qv, k, ranked))
    qf = f"{d}/q.gq"
    open(qf, "w").write("\n".join(q[1] for q in qs) + "\n")

    def check(phase):
        for name, src, qv, k, ranked in qs:
            p = run(["query", name, "--query", qf, "--params", json.dumps({"q": qv}), "--json", "--target", g])
            if p.returncode:
                raise Mismatch(f"{phase} {name} failed\n{src}\n{p.stderr[-1200:]}")
            out = json.loads(p.stdout)
            rows = out["rows"]
            exhaustive = (out.get("ranking") or {}).get("exhaustive")
            got = [x["d.slug"] for x in rows]
            exp_d = [d2(x["v"], qv) for x in ranked[:k]]
            got_d = [d2(by[s]["v"], qv) for s in got]
            if len(got) != len(set(got)):
                raise Mismatch(f"{phase} {name}: duplicate rows {got}")
            if len(got) != min(k, len(ranked)):
                raise Mismatch(f"{phase} {name}: got {len(got)} rows, expected {min(k, len(ranked))} (exhaustive={exhaustive})\n{src}")
            if any(abs(a - b) > 1e-4 * max(1, abs(a)) for a, b in zip(sorted(got_d), exp_d)):
                if exhaustive is False:
                    continue
                raise Mismatch(f"{phase} {name}: distances differ (exhaustive={exhaustive})\n{src}\nq={qv}\nexp={list(zip([x['slug'] for x in ranked[:k]], exp_d))}\ngot={list(zip(got, got_d))}")
            if got_d != sorted(got_d):
                raise Mismatch(f"{phase} {name}: rows not in ascending distance {list(zip(got, got_d))}")
            # reported distance equals squared L2
            for x in rows:
                if abs(x["dist"] - d2(by[x["d.slug"]]["v"], qv)) > 1e-3 * max(1, x["dist"]):
                    raise Mismatch(f"{phase} {name}: reported dist {x['dist']} != {d2(by[x['d.slug']]['v'], qv)} for {x['d.slug']}")

    check("unindexed")
    for a in (["index", "build", "--target", g], ["optimize", "--target", g]):
        p = run(a)
        if p.returncode:
            raise Mismatch(f"{a} failed: {p.stderr[-800:]}")
    check("indexed")
    # write after index: new rows outside coverage
    extra = [{"slug": f"z{i}", "n": 5, "g": "x", "v": [round(r.uniform(-3, 3), 2) for _ in range(DIM)]} for i in range(20)]
    open(f"{d}/e.jsonl", "w").write("\n".join(json.dumps({"type": "D", "data": x}) for x in extra) + "\n")
    p = run(["load", "--data", f"{d}/e.jsonl", "--mode", "merge", "--target", g])
    if p.returncode:
        raise Mismatch(f"extra load: {p.stderr[-500:]}")
    for x in extra:
        by[x["slug"]] = x
    new = []
    for name, src, qv, k, ranked in qs:
        if "$d.n >=" in src:
            c = int(src.split("$d.n >= ")[1].split()[0])
            add = [x for x in extra if x["n"] >= c]
        elif "$t has" in src:
            add = []
        else:
            add = extra
        new.append((name, src, qv, k, sorted(ranked + add, key=lambda x: (d2(x["v"], qv), x["slug"]))))
    qs[:] = new
    check("uncovered-tail")
    shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    a, c = int(sys.argv[1]), int(sys.argv[2])
    os.makedirs(ROOT, exist_ok=True)
    os.makedirs(FAIL, exist_ok=True)
    fails = 0
    for s in range(a, a + c):
        try:
            case(s)
            print(f"seed {s} ok", flush=True)
        except Mismatch as m:
            fails += 1
            print(f"seed {s} MISMATCH\n{m}\n", flush=True)
            open(f"{FAIL}/{s}.txt", "w").write(str(m))
        except Exception as e:
            import traceback
            fails += 1
            print(f"seed {s} ERROR {e}\n{traceback.format_exc()}", flush=True)
    print(f"done fails={fails}")
