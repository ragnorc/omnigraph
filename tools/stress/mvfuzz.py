#!/usr/bin/env python3
"""Merge validation at scale (#1433): two branches each write large deltas
(thousands of rows, several validation batches, some wide rows), with
conflicts planted at random positions: the same new @unique value on both
sides, @card(0..2) and @card(1..1) overflow from edges added on both sides,
orphan edges to nodes the other side deleted, divergent inserts and
delete-vs-update. The merge (and its dry run) must succeed exactly when the
three-way, entity-level result is valid, and main must then equal the model
row for row (full export). MV_LINEAGE=verify|off|on sets merge_lineage for
the merge."""
import json, os, random, shutil, subprocess, sys

OG = os.environ.get("OG", "omnigraph")
LINEAGE = os.environ.get("MV_LINEAGE", "verify")
ROOT = "/tmp/og/mvrun"
FAIL = "/tmp/og/mvfail"
SCHEMA = """node P {
  slug: String @key
  email: String?
  age: I64?
  bio: String?
  @unique(email)
  @range(age, 0..10)
}
node Q {
  slug: String @key
}
edge Owns: P -> Q @card(0..2) {
  @key(@src, @dst)
}
edge Home: P -> Q @card(1..1) {
  @key(@src, @dst)
}
"""
QK = [f"q{i}" for i in range(20)]
HOMEQ = QK[:12]          # homes only point here
FREEQ = QK[12:]          # may be deleted when nothing points at them
CHUNK = 3000


def _par(k):
    import zlib
    return zlib.crc32(k.encode()) % 2


class Mismatch(Exception):
    pass


def run(args, env=None, timeout=900):
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run([OG] + args, capture_output=True, text=True, timeout=timeout, env=e)


def tail(p):
    return (p.stdout[-600:] + " | " + p.stderr[-900:]).replace("\n", " ")


class St:
    def __init__(self):
        self.p = {}      # slug -> (email, age, bio)
        self.q = set()
        self.owns = set()
        self.home = set()

    def copy(self):
        s = St()
        s.p = dict(self.p)
        s.q = set(self.q)
        s.owns = set(self.owns)
        s.home = set(self.home)
        return s

    def key(self):
        return (self.p, self.q, self.owns, self.home)


def violations(s):
    out = []
    emails = [v[0] for v in s.p.values() if v[0] is not None]
    if len(emails) != len(set(emails)):
        out.append("unique")
    cnt = {}
    for a, b in s.owns:
        cnt[a] = cnt.get(a, 0) + 1
    if any(c > 2 for c in cnt.values()):
        out.append("card_owns")
    hc = {}
    for a, b in s.home:
        hc[a] = hc.get(a, 0) + 1
    if any(hc.get(k, 0) != 1 for k in s.p):
        out.append("card_home")
    for a, b in s.owns | s.home:
        if a not in s.p or b not in s.q:
            out.append("orphan")
            break
    return out


def tw(b, o, t):
    """three-way entity merge of dicts; None on conflict"""
    out = {}
    conflicts = []
    for k in set(b) | set(o) | set(t):
        bv, ov, tv = b.get(k), o.get(k), t.get(k)
        if ov == tv:
            rv = ov
        elif ov == bv:
            rv = tv
        elif tv == bv:
            rv = ov
        else:
            conflicts.append(k)
            continue
        if rv is not None:
            out[k] = rv
    return out, conflicts


def merge_model(base, ours, theirs):
    res = St()
    res.p, c1 = tw(base.p, ours.p, theirs.p)
    qd, c2 = tw({k: 1 for k in base.q}, {k: 1 for k in ours.q}, {k: 1 for k in theirs.q})
    res.q = set(qd)
    od, c3 = tw({k: 1 for k in base.owns}, {k: 1 for k in ours.owns}, {k: 1 for k in theirs.owns})
    res.owns = set(od)
    hd, c4 = tw({k: 1 for k in base.home}, {k: 1 for k in ours.home}, {k: 1 for k in theirs.home})
    res.home = set(hd)
    return res, c1 + c2 + c3 + c4


def export(g, br, d):
    out = f"{d}/exp.jsonl"
    p = run(["export", "--branch", br, "--target", g], timeout=900)
    if p.returncode:
        raise Mismatch(f"export failed: {tail(p)}")
    s = St()
    for line in p.stdout.splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("type") == "P":
            x = r["data"]
            s.p[x["slug"]] = (x.get("email"), x.get("age"), x.get("bio"))
        elif r.get("type") == "Q":
            s.q.add(r["data"]["slug"])
        elif r.get("edge") == "Owns":
            s.owns.add((r["from"], r["to"]))
        elif r.get("edge") == "Home":
            s.home.add((r["from"], r["to"]))
    return s


def write_jsonl(path, lines):
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def prow(slug, v):
    return json.dumps({"type": "P", "data": {"slug": slug, "email": v[0], "age": v[1], "bio": v[2]}})


def bio(r):
    x = r.random()
    if x < 0.5:
        return None
    if x < 0.97:
        return "b" * r.randint(1, 40)
    return "W" * r.randint(20000, 60000)  # wide row


class Case:
    def __init__(self, seed):
        self.r = random.Random(seed)
        self.seed = seed
        self.d = f"{ROOT}/{seed}"
        shutil.rmtree(self.d, ignore_errors=True)
        os.makedirs(self.d)
        open(f"{self.d}/s.pg", "w").write(SCHEMA)
        open(f"{self.d}/del.gq", "w").write(
            "query dp($s: [String]) { delete P where slug in $s }\n"
            "query dq($s: String) { delete Q where slug = $s }\n")
        self.g = f"{self.d}/g.omni"
        self.log = []
        self.n = 0

    def L(self, m):
        self.log.append(m)

    def fail(self, m):
        raise Mismatch(m + "\nLOG:\n" + "\n".join(self.log[-60:]))

    def load(self, br, lines, expect_ok, what):
        # chunk; every chunk is a separate commit, so only the last can be the invalid one
        f = f"{self.d}/w.jsonl"
        for i in range(0, len(lines), CHUNK):
            write_jsonl(f, lines[i:i + CHUNK])
            p = run(["load", "--data", f, "--mode", "merge", "--branch", br, "--json", "--target", self.g])
            if p.returncode != 0:
                self.fail(f"[{br}] {what}: load refused (chunk {i}): {tail(p)}")
        self.L(f"[{br}] {what}: {len(lines)} lines ok")

    def fresh_slug(self, side):
        self.n += 1
        return f"{side}{self.n}"

    def new_email(self, side):
        self.n += 1
        return f"{side}e{self.n}"

    def side_ops(self, br, st, other_tag, plants):
        """bulk valid writes on one branch; returns the new state"""
        r = self.r
        st = st.copy()
        big = r.random() < 0.5
        # 1. bulk insert of new P (each with Home), maybe Owns
        n = r.randint(2000, 9000) if big else r.randint(1, 60)
        lines = []
        rows = []
        for _ in range(n):
            k = self.fresh_slug(br[0])
            v = (self.new_email(br[0]) if r.random() < 0.7 else None, r.choice([None, 0, 3, 10]), bio(r))
            h = r.choice(HOMEQ)
            rows.append((k, v, h))
        # plants that need a position inside the delta
        for kind, payload in plants:
            if kind == "same_new_email":
                pos = r.randrange(len(rows) + 1)
                rows.insert(pos, (self.fresh_slug(br[0]), (payload, 1, None), r.choice(HOMEQ)))
            if kind == "same_new_node":
                slug, v, h = payload
                pos = r.randrange(len(rows) + 1)
                rows.insert(pos, (slug, v, h))
        for k, v, h in rows:
            st.p[k] = v
            st.home.add((k, h))
            lines.append(prow(k, v))
            lines.append(json.dumps({"edge": "Home", "from": k, "to": h, "data": {}}))
        # node and its Home edge must be in the same load (card min): keep pairs in one chunk
        self.load(br, lines, True, f"insert {len(rows)} P")
        # 2. updates of existing P (whole rows, all properties supplied)
        par = 0 if br == "main" else 1
        olds = [k for k in st.p if not k.startswith(br[0]) and _par(k) == par]
        if olds:
            ups = r.sample(olds, min(len(olds), r.randint(1, 400 if big else 20)))
            lines = []
            for k in ups:
                e, a, b = st.p[k]
                ch = r.choice(["email", "age", "bio"])
                if ch == "email":
                    e = None if r.random() < 0.2 else self.new_email(br[0])
                elif ch == "age":
                    a = r.choice([None, 1, 2, 9])
                else:
                    b = bio(r)
                st.p[k] = (e, a, b)
                lines.append(prow(k, st.p[k]))
            for kind, payload in plants:
                if kind == "update_email":
                    k, e = payload
                    if k in st.p:
                        v = st.p[k]
                        st.p[k] = (e, v[1], v[2])
                        lines.insert(r.randrange(len(lines) + 1), prow(k, st.p[k]))
            # a key may appear twice in one load: dedupe keeping the last state
            seen = {}
            for l in lines:
                seen[json.loads(l)["data"]["slug"]] = prow(json.loads(l)["data"]["slug"], st.p[json.loads(l)["data"]["slug"]])
            self.load(br, list(seen.values()), True, f"update {len(seen)} P")
        # 3. Owns edges
        cnt = {}
        for a, b in st.owns:
            cnt[a] = cnt.get(a, 0) + 1
        lines = []
        cand = [k for k in st.p if _par(k) == par or k.startswith(br[0])] or list(st.p)
        for _ in range(r.randint(0, 300 if big else 15)):
            a = r.choice(cand)
            b = r.choice(QK)
            if b not in st.q or (a, b) in st.owns or cnt.get(a, 0) >= 2:
                continue
            st.owns.add((a, b))
            cnt[a] = cnt.get(a, 0) + 1
            lines.append(json.dumps({"edge": "Owns", "from": a, "to": b, "data": {}}))
        for kind, payload in plants:
            if kind == "owns_add":
                a, b = payload
                if a in st.p and b in st.q and (a, b) not in st.owns and cnt.get(a, 0) < 2:
                    st.owns.add((a, b))
                    cnt[a] = cnt.get(a, 0) + 1
                    lines.insert(r.randrange(len(lines) + 1), json.dumps({"edge": "Owns", "from": a, "to": b, "data": {}}))
        if lines:
            self.load(br, lines, True, f"owns {len(lines)}")
        # 4. deletes of P (cascade edges)
        dels = []
        for kind, payload in plants:
            if kind == "del_p" and payload in st.p:
                dels.append(payload)
        if r.random() < 0.5 and olds:
            dels += r.sample(olds, min(len(olds), r.randint(1, 30)))
        dels = sorted(set(d for d in dels if d in st.p))
        if dels:
            p = run(["mutate", "dp", "--query", f"{self.d}/del.gq", "--params", json.dumps({"s": dels}), "--branch", br, "--json", "--target", self.g])
            if p.returncode:
                self.fail(f"[{br}] delete {len(dels)} P refused: {tail(p)}")
            for k in dels:
                del st.p[k]
            st.owns = {e for e in st.owns if e[0] in st.p}
            st.home = {e for e in st.home if e[0] in st.p}
            self.L(f"[{br}] delete {len(dels)} P ok")
        # 5. delete a free Q (no Home points there; cascades Owns)
        for kind, payload in plants:
            if kind == "del_q" and payload in st.q:
                p = run(["mutate", "dq", "--query", f"{self.d}/del.gq", "--params", json.dumps({"s": payload}), "--branch", br, "--json", "--target", self.g])
                if p.returncode:
                    self.fail(f"[{br}] delete Q {payload} refused: {tail(p)}")
                st.q.discard(payload)
                st.owns = {e for e in st.owns if e[1] != payload}
                self.L(f"[{br}] delete Q {payload} ok")
        return st

    def run(self):
        r = self.r
        if run(["init", "--schema", f"{self.d}/s.pg", self.g]).returncode:
            self.fail("init failed")
        base = St()
        write_jsonl(f"{self.d}/q.jsonl", [json.dumps({"type": "Q", "data": {"slug": q}}) for q in QK])
        self.load("main", open(f"{self.d}/q.jsonl").read().splitlines(), True, "Q")
        base.q = set(QK)
        nb = r.choice([30, 300, 3000])
        lines = []
        for i in range(nb):
            k = f"b{i}"
            v = (f"be{i}" if r.random() < 0.8 else None, r.choice([None, 0, 5]), bio(r))
            h = r.choice(HOMEQ)
            base.p[k] = v
            base.home.add((k, h))
            lines += [prow(k, v), json.dumps({"edge": "Home", "from": k, "to": h, "data": {}})]
        self.load("main", lines, True, f"base {nb} P")
        lines = []
        for k in list(base.p):
            if r.random() < 0.3:
                b = r.choice(QK)
                base.owns.add((k, b))
                lines.append(json.dumps({"edge": "Owns", "from": k, "to": b, "data": {}}))
        if lines:
            self.load("main", lines, True, f"base owns {len(lines)}")
        rounds = r.randint(1, 3)
        main = base
        for rd in range(rounds):
            p = run(["branch", "create", "feat", "--from", "main", "--target", self.g])
            if p.returncode:
                self.fail(f"branch create: {tail(p)}")
            mb = main.copy()
            # choose plants
            plants_m, plants_f = [], []
            olds = sorted(mb.p)
            expect = []
            if r.random() < 0.35:
                e = f"shared{rd}"
                plants_m.append(("same_new_email", e))
                plants_f.append(("same_new_email", e))
                expect.append("unique")
            if r.random() < 0.25 and len(olds) >= 2:
                a, b = r.sample(olds, 2)
                e = f"shared_up{rd}"
                plants_m.append(("update_email", (a, e)))
                plants_f.append(("update_email", (b, e)))
                expect.append("unique")
            if r.random() < 0.3 and olds:
                a = r.choice(olds)
                have = {b for (x, b) in mb.owns if x == a}
                free = [q for q in QK if q not in have]
                if len(have) <= 1 and len(free) >= 2:
                    q1, q2 = r.sample(free, 2)
                    plants_m.append(("owns_add", (a, q1)))
                    plants_f.append(("owns_add", (a, q2)))
                    if len(have) == 1:
                        expect.append("card_owns")
            if r.random() < 0.25:
                slug = f"twin{rd}"
                v = (None, 2, None)
                h1, h2 = r.sample(HOMEQ, 2) if r.random() < 0.7 else [HOMEQ[0]] * 2
                v2 = v if r.random() < 0.7 else (None, 3, None)
                plants_m.append(("same_new_node", (slug, v, h1)))
                plants_f.append(("same_new_node", (slug, v2, h2)))
                expect.append("twin")
            if r.random() < 0.25:
                q = r.choice([q for q in FREEQ if q in mb.q] or [None])
                if q and olds:
                    a = r.choice(olds)
                    plants_m.append(("del_q", q))
                    plants_f.append(("owns_add", (a, q)))
                    expect.append("orphan_q")
            if r.random() < 0.2 and len(olds) >= 1:
                a = r.choice(olds)
                plants_m.append(("del_p", a))
                if r.random() < 0.5:
                    plants_f.append(("update_email", (a, f"dvu{rd}")))
                else:
                    plants_f.append(("owns_add", (a, r.choice(FREEQ))))
                expect.append("del_vs")
            # random side order; sometimes main does not write at all (fast-forward)
            ff = r.random() < 0.2
            fst = self.side_ops("feat", mb, "m", plants_f)
            mst = mb if ff else self.side_ops("main", mb, "f", plants_m)
            model, conflicts = merge_model(mb, mst, fst)
            viol = violations(model)
            ok = not conflicts and not viol
            self.L(f"round {rd}: ff={ff} plants={expect} conflicts={conflicts[:4]} viol={viol} expect_ok={ok} |P|={len(model.p)}")
            env = {"OMNIGRAPH_MERGE_LINEAGE": LINEAGE}
            dp = run(["branch", "merge", "feat", "--into", "main", "--dry-run", "--json", "--target", self.g], env=env)
            p = run(["branch", "merge", "feat", "--into", "main", "--json", "--target", self.g], env=env)
            self.L(f"dry rc={dp.returncode} merge rc={p.returncode} {tail(p)[:400] if p.returncode else json.loads(p.stdout).get('outcome', {})}")
            if (dp.returncode == 0) != (p.returncode == 0):
                self.fail(f"dry run and merge disagree: dry={tail(dp)} merge={tail(p)}")
            if "divergence" in (p.stdout + p.stderr).lower() and "lineage" in (p.stdout + p.stderr).lower():
                self.fail(f"merge_lineage verify divergence: {tail(p)}")
            if ok and p.returncode != 0:
                self.fail(f"valid merge refused: {tail(p)}")
            if not ok and p.returncode == 0:
                self.fail(f"invalid merge accepted (conflicts={conflicts[:5]} viol={viol})")
            main = model if p.returncode == 0 else mst
            act = export(self.g, "main", self.d)
            if act.key() != main.key():
                dp_ = {k for k in set(act.p) | set(main.p) if act.p.get(k) != main.p.get(k)}
                self.fail(f"main differs after merge: P diff {sorted(dp_)[:10]} (act {len(act.p)} model {len(main.p)}); "
                          f"owns +{sorted(act.owns - main.owns)[:5]} -{sorted(main.owns - act.owns)[:5]}; "
                          f"home +{sorted(act.home - main.home)[:5]} -{sorted(main.home - act.home)[:5]}; q {act.q ^ main.q}")
            run(["branch", "delete", "feat", "--target", self.g])
        shutil.rmtree(self.d, ignore_errors=True)


if __name__ == "__main__":
    a, c = int(sys.argv[1]), int(sys.argv[2])
    os.makedirs(ROOT, exist_ok=True)
    os.makedirs(FAIL, exist_ok=True)
    fails = 0
    for s in range(a, a + c):
        try:
            Case(s).run()
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
