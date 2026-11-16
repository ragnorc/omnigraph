#!/usr/bin/env python3
"""Read-your-writes against one long-lived server (#1276 handle eviction,
#1272 column patches, #1229 merges). Talks HTTP directly to a local
`omnigraph-server` (plain HTTP on localhost, bearer tokens).

Writers own disjoint keys and update scalar, vector and Blob properties on main;
after every acknowledged write they read the row back through the server and
must see exactly what they wrote. A branch worker repeatedly creates a branch,
updates its own keys there, checks the branch, merges into main, checks main
and deletes the branch. Readers compare, at one pinned commit, indexed equality
filters with the same filter computed from a full read. At the end the export
must equal the model.

Usage: sserve.py <base_url> <graph> <seconds> <writers>"""
import base64, json, random, sys, threading, time, urllib.request, urllib.error

BASE, G, SECS, NW = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
TOK = ["ta", "tb"]
NK = 240
events = []
lock = threading.Lock()


def call(method, path, body=None, tok="ta", raw=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + tok)
    req.add_header("Omnigraph-Http-Api", "0.14")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            b = resp.read()
            return resp.status, (b.decode() if raw else json.loads(b or b"null"))
    except urllib.error.HTTPError as e:
        b = e.read()
        try:
            return e.code, json.loads(b)
        except Exception:
            return e.code, b.decode()[:500]


def ev(*a):
    with lock:
        events.append(a)
    print("EVENT", *a, flush=True)


READ_ROW = "query r($k: String) { match { $p: P { slug: $k } } return { $p.slug, $p.n, $p.s, $p.v } }"


def read_row(k, branch="main", tok="ta"):
    st, out = call("POST", f"/graphs/{G}/query", {"query": READ_ROW, "params": {"k": k}, "branch": branch}, tok)
    if st != 200:
        return None, (st, out)
    rows = out["rows"]
    if not rows:
        return {}, None
    r = rows[0]
    return {"n": r.get("p.n"), "s": r.get("p.s"), "v": r.get("p.v")}, None


def blob_of(k, branch="main"):
    st, out = call("POST", f"/graphs/{G}/export", {"branch": branch, "type_names": ["P"]}, raw=True)
    if st != 200 or not isinstance(out, str):
        ev("export", st, str(out)[:300])
        return None
    for line in out.splitlines():
        if line.strip():
            d = json.loads(line)["data"]
            if d["slug"] == k:
                return d.get("b")
    return "MISSING"


def rand_vec(r):
    return [float(r.randint(-4, 4)) / 2 for _ in range(4)]


def update(k_list, sets, params, branch, tok):
    sig = ", ".join(["$ids: [String]"] + [f"${f}: {t}" for f, t in sets])
    body = ", ".join(f"{f}: ${f}" for f, _ in sets)
    src = f"query m({sig}) {{ update P set {{ {body} }} where slug in $ids }}"
    return call("POST", f"/graphs/{G}/mutate", {"query": src, "params": {**params, "ids": k_list}, "branch": branch}, tok)


TYPES = {"n": "I64?", "s": "String?", "v": "Vector(4)?", "b": "Blob?"}


def writer(wid, keys, model, deadline, stats):
    r = random.Random(wid)
    tok = TOK[wid % 2]
    while time.time() < deadline:
        ks = r.sample(keys, r.randint(1, 3))
        fs = r.sample(["n", "s", "v", "b"] if r.random() < 0.2 else ["n", "s", "v"], r.randint(1, 2))
        vals = {}
        for f in fs:
            vals[f] = {"n": r.randint(0, 15), "s": r.choice(["a", "b", "c"]), "v": rand_vec(r),
                       "b": "base64:" + base64.b64encode(bytes(r.getrandbits(8) for _ in range(r.choice([5, 3000])))).decode()}[f]
        st, out = update(ks, [(f, TYPES[f]) for f in fs], vals, "main", tok)
        if st == 200:
            stats["acked"] += 1
            for k in ks:
                model[k].update(vals)
            for k in ks:
                got, err = read_row(k, tok=tok)
                if err:
                    ev("readerr", wid, k, err)
                    continue
                want = {f: model[k][f] for f in ("n", "s", "v")}
                if got != want:
                    ev("STALE_READ", wid, k, "want", want, "got", got, "after", fs, out.get("commit", {}).get("graph_commit_id"))
                    stats["stale"] += 1
            if "b" in fs and r.random() < 0.3:
                b = blob_of(ks[0])
                if b is not None and b != model[ks[0]]["b"]:
                    ev("STALE_BLOB", wid, ks[0])
                    stats["stale"] += 1
        elif st in (409, 429, 503):
            stats["refused"] += 1
        else:
            stats["other"] += 1
            ev("write", wid, st, str(out)[:300])


def brancher(keys, model, deadline, stats):
    r = random.Random(777)
    i = 0
    while time.time() < deadline:
        name = f"br{i}"
        i += 1
        st, out = call("POST", f"/graphs/{G}/branches", {"name": name, "from": "main"})
        if st not in (200, 201):
            ev("branch create", st, str(out)[:300])
            time.sleep(0.5)
            continue
        local = {k: dict(model[k]) for k in keys}
        for _ in range(r.randint(1, 4)):
            ks = r.sample(keys, 2)
            v = r.randint(100, 200)
            st, out = update(ks, [("n", "I64?")], {"n": v}, name, "tb")
            if st == 200:
                for k in ks:
                    local[k]["n"] = v
                got, err = read_row(ks[0], branch=name, tok="tb")
                if got is None or got["n"] != v:
                    ev("STALE_BRANCH_READ", name, ks[0], v, got, err)
                    stats["stale"] += 1
            else:
                ev("branch write", st, str(out)[:300])
        st, out = call("POST", f"/graphs/{G}/branches/merge", {"source": name, "target": "main"})
        if st == 200:
            stats["merged"] += 1
            for k in keys:
                model[k].update(local[k])
            for k in r.sample(keys, 3):
                got, err = read_row(k)
                want = {f: model[k][f] for f in ("n", "s", "v")}
                if got != want:
                    ev("STALE_AFTER_MERGE", name, k, want, got)
                    stats["stale"] += 1
        else:
            ev("merge", name, st, str(out)[:300])
        st, out = call("DELETE", f"/graphs/{G}/branches/{name}")
        if st not in (200, 204):
            ev("branch delete", name, st, str(out)[:200])


def reader(rid, deadline, stats):
    r = random.Random(5000 + rid)
    while time.time() < deadline:
        st, a = call("POST", f"/graphs/{G}/query", {"query": "query q() { match { $p: P } return { $p.slug, $p.n, $p.s } }"})
        if st != 200:
            ev("readall", st, str(a)[:200])
            continue
        snap = a["graph_commit_id"]
        v = r.randint(0, 15)
        s = r.choice(["a", "b", "c", "S"])
        st1, b = call("POST", f"/graphs/{G}/query", {"query": "query q($v: I64) { match { $p: P { n: $v } } return { $p.slug } }", "params": {"v": v}, "snapshot": snap})
        st2, c = call("POST", f"/graphs/{G}/query", {"query": "query q($s: String) { match { $p: P { s: $s } } return { $p.slug } }", "params": {"s": s}, "snapshot": snap})
        if st1 != 200 or st2 != 200:
            ev("snapread", st1, st2, str(b)[:200], str(c)[:200])
            continue
        rows = [x for x in a["rows"] if x["p.slug"].startswith("k")]  # other clients may add rows
        wn = {x["p.slug"] for x in rows if x.get("p.n") == v}
        ws = {x["p.slug"] for x in rows if x.get("p.s") == s}
        gn = {x["p.slug"] for x in b["rows"] if x["p.slug"].startswith("k")}
        gs = {x["p.slug"] for x in c["rows"] if x["p.slug"].startswith("k")}
        stats["checks"] += 1
        if wn != gn or ws != gs or len(rows) != NK:
            stats["bad"] += 1
            ev("INDEX_MISMATCH", snap, v, s, len(rows), sorted(wn ^ gn)[:6], sorted(ws ^ gs)[:6])


def main():
    r = random.Random(1)
    model = {}
    lines = []
    for i in range(NK):
        k = f"k{i:03d}"
        model[k] = {"n": 0, "s": "S", "v": [0.5, 0.0, 0.0, 0.0],
                    "b": "base64:" + base64.b64encode(bytes(r.getrandbits(8) for _ in range(r.choice([5, 3000])))).decode()}
    # seed through mutate inserts, 40 rows per request
    keys = sorted(model)
    for j in range(0, NK, 40):
        chunk = keys[j:j + 40]
        sig = ", ".join(f"$b{i}: Blob" for i in range(len(chunk)))
        body = " ".join(f'insert P {{ slug: "{k}", n: 0, s: "S", v: [0.5, 0.0, 0.0, 0.0], b: $b{i} }}' for i, k in enumerate(chunk))
        st, out = call("POST", f"/graphs/{G}/mutate", {"query": f"query m({sig}) {{ {body} }}", "params": {f"b{i}": model[k]["b"] for i, k in enumerate(chunk)}})
        assert st == 200, (st, out)
    r.shuffle(keys)
    parts = [keys[i::NW + 1] for i in range(NW + 1)]
    deadline = time.time() + SECS
    stats = [{"acked": 0, "stale": 0, "refused": 0, "other": 0} for _ in range(NW)]
    bstats = {"merged": 0, "stale": 0}
    rstats = [{"checks": 0, "bad": 0} for _ in range(2)]
    ts = [threading.Thread(target=writer, args=(i, parts[i], model, deadline, stats[i])) for i in range(NW)]
    ts.append(threading.Thread(target=brancher, args=(parts[NW], model, deadline, bstats)))
    ts += [threading.Thread(target=reader, args=(i, deadline, rstats[i])) for i in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    st, out = call("POST", f"/graphs/{G}/export", {"branch": "main", "type_names": ["P"]}, raw=True)
    final = {}
    for line in out.splitlines():
        if line.strip():
            d = json.loads(line)["data"]
            final[d["slug"]] = d
    diff = [k for k in keys if any(final[k].get(f) != model[k][f] for f in ("n", "s", "v", "b"))]
    print("writers", stats, "\nbrancher", bstats, "\nreaders", rstats)
    print(f"final rows={len(final)} differing={len(diff)} {diff[:5]}")
    for k in diff[:3]:
        print(k, {f: (str(model[k][f])[:40], str(final[k].get(f))[:40]) for f in ("n", "s", "v", "b")})


if __name__ == "__main__":
    main()
