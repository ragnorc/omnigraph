#!/usr/bin/env python3
"""Constraint fuzzer: @unique, @range, @check, @card under merge-loads,
mutations and branch merges. A write is accepted iff the resulting state
satisfies every constraint; a refused write changes nothing."""
import json, os, random, re, shutil, subprocess, sys

OG = os.environ.get("OG", "omnigraph")
ROOT = "/tmp/og/kfuzzrun"
FAIL = "/tmp/og/kfuzzfail"
SCHEMA = """node P {
  slug: String @key
  email: String?
  age: I64?
  code: String?
  @unique(email)
  @range(age, 0..10)
  @check(code, "^[a-z]{1,3}$")
}
node Q {
  slug: String @key
}
edge Owns: P -> Q @card(0..2) {
  @key(@src, @dst)
}
"""
PK = [f"p{i}" for i in range(6)]
QK = [f"q{i}" for i in range(4)]
EMAILS = [None, "a@x", "b@x", "c@x"]
AGES = [None, 0, 5, 10, 11, -1]
CODES = [None, "ab", "abc", "abcd", "A", "", "x1"]


class Mismatch(Exception):
    pass


def valid(st):
    ps, es = st
    emails = [p["email"] for p in ps.values() if p.get("email") is not None]
    if len(emails) != len(set(emails)):
        return "unique"
    for p in ps.values():
        if p.get("age") is not None and not (0 <= p["age"] <= 10):
            return "range"
        if p.get("code") is not None and not re.fullmatch(r"[a-z]{1,3}", p["code"]):
            return "check"
    cnt = {}
    for (a, b) in es:
        cnt[a] = cnt.get(a, 0) + 1
    if any(c > 2 for c in cnt.values()):
        return "card"
    return None


def run(args):
    return subprocess.run([OG] + args, capture_output=True, text=True, timeout=300)


def read(g, br):
    qf = os.path.join(os.path.dirname(g), "r.gq")
    open(qf, "w").write("query p() { match { $p: P } return { $p } }\nquery e() { match { $a: P  $a owns $b } return { $a.slug, $b.slug as b } }\n")
    p = run(["query", "p", "--query", qf, "--branch", br, "--json", "--target", g])
    ps = {}
    for row in json.loads(p.stdout)["rows"]:
        x = row["p"]
        ps[x["slug"]] = {k: x.get(k) for k in ("email", "age", "code")}
    p = run(["query", "e", "--query", qf, "--branch", br, "--json", "--target", g])
    es = {(row["a.slug"], row["b"]) for row in json.loads(p.stdout)["rows"]}
    return ps, es


def apply_write(r, g, d, br, st):
    """Pick a random write, return (new_state, description, completed_process)."""
    ps = {k: dict(v) for k, v in st[0].items()}
    es = set(st[1])
    kind = r.choice(["load", "load", "update", "insert", "edges", "delnode"])
    if kind == "load":
        lines = []
        for k in r.sample(PK, r.randint(1, 3)):
            # merge-load cannot null a property: only supply non-null fields for existing rows
            rec = {"email": r.choice(EMAILS), "age": r.choice(AGES), "code": r.choice(CODES)}
            if k in ps:
                rec = {f: v for f, v in rec.items() if v is not None}
                ps[k] = {**ps[k], **rec}
            else:
                ps[k] = {f: rec.get(f) for f in ("email", "age", "code")}
            data = {"slug": k, **{f: v for f, v in rec.items() if v is not None}}
            lines.append(json.dumps({"type": "P", "data": data}))
        open(f"{d}/w.jsonl", "w").write("\n".join(lines) + "\n")
        p = run(["load", "--data", f"{d}/w.jsonl", "--mode", "merge", "--branch", br, "--target", g])
        return (ps, es), "load " + " | ".join(lines), p
    if kind == "update" and ps:
        k = r.choice(sorted(ps))
        f = r.choice(["email", "age", "code"])
        v = r.choice([x for x in {"email": EMAILS, "age": AGES, "code": CODES}[f] if x is not None])
        if f == "age" and v < 0:
            v = 11  # negative literals do not parse (separate finding)
        ps[k][f] = v
        lit = json.dumps(v) if isinstance(v, str) else str(v)
        src = f'query m() {{ update P set {{ {f}: {lit} }} where slug = "{k}" }}'
        return (ps, es), src, run(["mutate", "-e", src, "--branch", br, "--target", g])
    if kind == "insert":
        k = r.choice(PK)
        rec = {"email": r.choice(EMAILS), "age": r.choice([a for a in AGES if a is None or a >= 0]), "code": r.choice(CODES)}
        ps[k] = rec  # keyed insert upserts the whole row
        fields = ", ".join([f'slug: "{k}"'] + [f"{f}: {json.dumps(v) if isinstance(v, str) else v}" for f, v in rec.items() if v is not None])
        src = f"query m() {{ insert P {{ {fields} }} }}"
        p = run(["mutate", "-e", src, "--branch", br, "--target", g])
        return (ps, es), src, p
    if kind == "edges":
        lines = []
        for _ in range(r.randint(1, 3)):
            a = r.choice(sorted(ps)) if ps else None
            if a is None:
                break
            b = r.choice(QK)
            if (a, b) in [tuple(json.loads(l)[x] for x in ("from", "to")) for l in lines]:
                continue
            es.add((a, b))
            lines.append(json.dumps({"edge": "Owns", "from": a, "to": b, "data": {}}))
        if not lines:
            return st, "noop", None
        open(f"{d}/w.jsonl", "w").write("\n".join(lines) + "\n")
        return (ps, es), "edges " + " | ".join(lines), run(["load", "--data", f"{d}/w.jsonl", "--mode", "merge", "--branch", br, "--target", g])
    if kind == "delnode" and ps:
        k = r.choice(sorted(ps))
        del ps[k]
        es = {e for e in es if e[0] != k}
        src = f'query m() {{ delete P where slug = "{k}" }}'
        return (ps, es), src, run(["mutate", "-e", src, "--branch", br, "--target", g])
    return st, "noop", None


def case(seed):
    r = random.Random(seed)
    d = f"{ROOT}/{seed}"
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    open(f"{d}/s.pg", "w").write(SCHEMA)
    g = f"{d}/g.omni"
    run(["init", "--schema", f"{d}/s.pg", g])
    open(f"{d}/q.jsonl", "w").write("\n".join(json.dumps({"type": "Q", "data": {"slug": q}}) for q in QK) + "\n")
    run(["load", "--data", f"{d}/q.jsonl", "--mode", "merge", "--target", g])
    states = {"main": ({}, set())}
    log = []
    branched = False
    for step in range(r.randint(6, 14)):
        if not branched and step >= 3 and r.random() < 0.4:
            p = run(["branch", "create", "feat", "--from", "main", "--target", g])
            states["feat"] = (dict(states["main"][0]), set(states["main"][1]))
            base = (dict(states["main"][0]), set(states["main"][1]))
            branched = True
            log.append("branch create feat")
            continue
        if branched and r.random() < 0.2:
            # merge feat into main
            def tw(b, o, t):
                out = {}
                for k in set(b) | set(o) | set(t):
                    bv, ov, tv = b.get(k), o.get(k), t.get(k)
                    if ov == tv:
                        rv = ov
                    elif ov == bv:
                        rv = tv
                    elif tv == bv:
                        rv = ov
                    else:
                        return None
                    if rv is not None:
                        out[k] = rv
                return out
            nodes = tw(base[0], states["main"][0], states["feat"][0])
            eb = {e: True for e in base[1]}
            em = {e: True for e in states["main"][1]}
            ef = {e: True for e in states["feat"][1]}
            edges = tw(eb, em, ef)
            p = run(["branch", "merge", "feat", "--into", "main", "--json", "--target", g])
            log.append(f"merge feat->main rc={p.returncode} {p.stdout[-300:] if p.returncode else ''}")
            if nodes is None or edges is None:
                if p.returncode == 0:
                    raise Mismatch("merge succeeded despite divergent change\nLOG:\n" + "\n".join(log))
            else:
                new = (nodes, set(edges))
                orphan = any(a not in nodes for a, b in new[1])
                v = valid(new) or ("orphan" if orphan else None)
                if v is None and p.returncode != 0:
                    raise Mismatch(f"valid merge refused: {p.stdout[-800:]}{p.stderr[-800:]}\nLOG:\n" + "\n".join(log))
                if v is not None and p.returncode == 0:
                    raise Mismatch(f"merge produced invalid state ({v}): {new}\nLOG:\n" + "\n".join(log))
                if p.returncode == 0:
                    states["main"] = new
            act = read(g, "main")
            if act != states["main"]:
                raise Mismatch(f"after merge main differs: model={states['main']} actual={act}\nLOG:\n" + "\n".join(log))
            run(["branch", "delete", "feat", "--target", g])
            branched = False
            del states["feat"]
            continue
        br = r.choice(["main", "feat"] if branched else ["main"])
        new, desc, p = apply_write(r, g, d, br, states[br])
        if p is None:
            continue
        v = valid(new)
        log.append(f"[{br}] {desc} -> rc={p.returncode} expect={'refuse:' + v if v else 'accept'} {p.stderr.strip().splitlines()[-2][:160] if p.returncode and p.stderr.strip() else ''}")
        if v is None and p.returncode != 0:
            raise Mismatch(f"valid write refused\nLOG:\n" + "\n".join(log) + "\n" + p.stderr[-1000:])
        if v is not None and p.returncode == 0:
            raise Mismatch(f"invalid write accepted ({v})\nLOG:\n" + "\n".join(log))
        if p.returncode == 0:
            states[br] = new
        act = read(g, br)
        if act != states[br]:
            raise Mismatch(f"state differs on {br}: model={states[br]} actual={act}\nLOG:\n" + "\n".join(log))
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
