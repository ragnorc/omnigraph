#!/usr/bin/env python3
"""Column-patch updates beside optimize, checked against a model (served).

New in the merged main: an `update` that assigns no Blob and leaves a Blob or
vector column unassigned may stage a column patch (Lance RewriteColumns): a
data file per touched fragment, no deletion file, and the touched fragments
withdrawn from any index on the assigned column until a whole rebuild. Lance
compaction refuses a rewrite group that mixes fragments an index covers with
ones it does not (`recalculate_fragment_bitmap`), so optimize meets exactly
that split on `Doc.title @index`.

Each round: random `update Doc set { title } where slug = …` on a few rows
(large Blob payloads make the patch the cheaper plan), sometimes new loads
(new fragments), sometimes an optimize (served, recorded); after every step
the harness checks, for a sample of titles old and new, that
`match Doc where title = $t` returns exactly the model's slugs, and that a
full scan returns every slug with the model's title. It records whether each
update was a patch (no new deletion file) or a row rewrite, and each
optimize's per-table outcome.
Usage: c9_column_patch_optimize.py OUTDIR [ROUNDS] [SEED]
"""
import base64, json, os, random, shutil, signal, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import og

out = Path(sys.argv[1]); shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 40
random.seed(int(sys.argv[3]) if len(sys.argv) > 3 else 9)
BLOB = int(os.environ.get("STRESS_BLOB_BYTES", "32768"))
log = og.Log(str(out / "log.txt"))
root = og.make_root(out, "patch"); og.apply_cluster(root)
srv = og.Server(root, int(os.environ.get("STRESS_PORT", "13990")), str(out / "server.log"),
                {"OMNIGRAPH_OPTIMIZE_GATE_HOLD_SECONDS": "5"}).start(timeout=90)
h = srv.http
model = {}          # slug -> title
violations, updates, optimizes = [], [], []
n_docs = 0


def violate(msg):
    violations.append(msg)
    log("VIOLATION", msg)


def deletion_files():
    if root.startswith(("s3://", "az://")):
        return None
    return sum(1 for p in Path(root).rglob("_deletions/*") if p.is_file())


def load_docs(k):
    global n_docs
    rows = []
    for _ in range(k):
        n_docs += 1
        slug = f"d{n_docs}"
        title = f"t{n_docs}"
        payload = base64.b64encode(os.urandom(BLOB)).decode()
        rows.append({"type": "Doc", "data": {"slug": slug, "title": title, "content": f"base64:{payload}",
                                              "embedding": [random.random() for _ in range(8)]}})
    s, b, _ = h.load("kg", "\n".join(json.dumps(r) for r in rows))
    if s != 200:
        violate(f"load {s} {str(b)[:200]}")
        return
    for r in rows:
        model[r["data"]["slug"]] = r["data"]["title"]


Q_BY_TITLE = "query q($t: String) { match { $d: Doc  $d.title = $t } return { $d.slug as s } }"
Q_ALL = "query q() { match { $d: Doc } return { $d.slug as s, $d.title as t } }"


def rows_of(body):
    return body.get("rows") or body.get("data") or []


def check(where):
    s, b, _ = h.query("kg", Q_ALL, {})
    if s != 200:
        violate(f"{where}: full scan {s} {str(b)[:200]}")
        return
    seen = {}
    for r in rows_of(b):
        seen[r["s"]] = r["t"]
    if seen != model:
        diff = [(k, model.get(k), seen.get(k)) for k in set(model) | set(seen) if model.get(k) != seen.get(k)]
        violate(f"{where}: full scan differs from model at {len(diff)} slugs, e.g. {diff[:3]}")
    titles = set(model.values())
    sample = random.sample(sorted(titles), min(12, len(titles)))
    # old titles nobody holds any more
    sample += [f"t{random.randint(1, n_docs)}" for _ in range(6)]
    for t in sample:
        s, b, _ = h.query("kg", Q_BY_TITLE, {"t": t})
        if s != 200:
            violate(f"{where}: title query {s} {str(b)[:200]}")
            continue
        got = sorted(r["s"] for r in rows_of(b))
        want = sorted(k for k, v in model.items() if v == t)
        if got != want:
            violate(f"{where}: title={t!r} returned {got} expected {want}")


def optimize(tag):
    oid = og.ulid()
    s, b, _ = h.submit("kg", oid, hold_ms=5000)
    if s not in (200, 202):
        violate(f"optimize submit {s} {str(b)[:200]}")
        return
    op = h.wait_terminal("kg", oid)
    tables = {t["type_key"]: (t["outcome"], t.get("reason") or (t.get("error") or {}).get("message") if isinstance(t.get("error"), dict) else t.get("error")) for t in op["outcome"].get("tables", [])}
    optimizes.append({"tag": tag, "state": op["state"], "doc": tables.get("node:Doc"), "tables": tables})
    log("optimize", tag, op["state"], "Doc:", tables.get("node:Doc"))
    if op["state"] != "succeeded":
        violate(f"optimize {tag} ended {op['state']} {op.get('reason')}")
    for k, v in tables.items():
        if v[0] == "failed":
            violate(f"optimize {tag}: {k} failed: {v[1]}")


try:
    for _ in range(4):
        load_docs(12)       # several fragments
    optimize("initial")     # compacts and builds the title index over everything
    check("after initial optimize")
    for rnd in range(rounds):
        r = random.random()
        if r < 0.55:
            slugs = random.sample(sorted(model), random.randint(1, 4))
            before = deletion_files()
            for slug in slugs:
                new = f"u{rnd}-{slug}-{random.randint(0, 9)}"
                s, b, _ = h.mutate("kg", "query u($s: String, $t: String) { update Doc set { title: $t } where slug = $s }",
                                   {"s": slug, "t": new})
                if s != 200:
                    violate(f"update {slug} {s} {str(b)[:200]}")
                    continue
                model[slug] = new
            after = deletion_files()
            mode = None if before is None else ("patch" if after == before else "rewrite")
            updates.append({"round": rnd, "rows": len(slugs), "mode": mode})
            check(f"round {rnd} after update ({mode})")
        elif r < 0.75:
            load_docs(random.randint(3, 10))
            check(f"round {rnd} after load")
        else:
            optimize(f"round {rnd}")
            check(f"round {rnd} after optimize")
    optimize("final")
    check("after final optimize")
    srv.kill(signal.SIGTERM)
    srv = og.Server(root, srv.port + 1, str(out / "server2.log")).start(timeout=90)
    h = srv.http
    check("after restart")
finally:
    srv.kill(signal.SIGTERM)

summary = {"rounds": rounds, "docs": len(model), "updates": len(updates),
           "patch_updates": sum(1 for u in updates if u["mode"] == "patch"),
           "rewrite_updates": sum(1 for u in updates if u["mode"] == "rewrite"),
           "optimizes": [(o["tag"], o["state"], o["doc"]) for o in optimizes],
           "violations": violations}
log("SUMMARY", json.dumps(summary)[:3000])
(out / "summary.json").write_text(json.dumps(summary, indent=1))
