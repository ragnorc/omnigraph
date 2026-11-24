"""Part 3: GQ 2 and NEW binaries on one graph (documented as unsupported).

The question is whether they fail loudly or corrupt. Three modes:

* `alternate <seed>`: a case whose steps alternate a GQ 2 writer
  (GQ2-REL or GQ2-MAIN) and NEW on the same graph: writes, loads, index
  passes, full-text rebuilds, optimize, cleanup, branches, schema applies.
  After every step both binaries open the graph, read and export it, and the
  case's oracles run as in Part 2 (`ufcase.Case.observe`).
* `concurrent <pair> <secs>`: a 20,000-row graph, one GQ 2 and one NEW
  process at once for SECS seconds: `gq2w-newr` (GQ 2 writes, NEW reads),
  `neww-gq2r`, `both-write`. Every acknowledged insert must be in the final
  export of both binaries, every read either answers or refuses with a code,
  and a reader's count of an insert-only type never goes down.
* `kill <op> <seed>`: NEW is killed with SIGKILL partway through `index
  build`, `index rebuild --kind full-text`, `schema apply` or `optimize` on a
  20,000-row graph; then each binary reopens it, reads it and exports it, NEW
  reruns the operation, and the logical content and NEW's answers must be
  those before the kill.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path

import ufcommon as C
from ufcommon import lib
import ufgen as G
import ufcase as U

OUT = C.WORK / "results"


# ---------------------------------------------------------------- alternate

class Alternate(U.Case):
    """Steps alternate between a GQ 2 binary and NEW on one graph."""

    def part2(self):
        g = self.root / "u"
        lib.copy_graph(self.root / "g", g)
        rng = random.Random(self.seed * 13 + 1)
        gq2_ops = U.draw_gq2_script(self.cur, self.seed + 5)
        # interleave index passes: each side's index build after the other side's writes
        extra = []
        for op in gq2_ops:
            extra.append(op)
            if op["op"] in ("load", "insert", "update") and rng.random() < 0.5:
                extra.append({"op": rng.choice(["index_build", "index_build", "optimize", "fts_rebuild"])})
        gq2_ops = extra
        cfg = G.clone(self.cur)
        cfg.sizes = {t.name: max(self.nkeys.get(t.name, 0), 1) + 5 for t in cfg.nodes}
        new_steps = [s for s in U.draw_new_script(cfg, sorted(self.live), self.cfg0.builder, self.seed + 9)
                     if s.get("step") != "upgrade"]
        # keep one-way schema applies rare and late: after one, GQ 2 can only refuse
        tail = [s for s in new_steps if s.get("step") == "schema" and s["kind"] in U.ONE_WAY_KINDS]
        new_steps = [s for s in new_steps if s not in tail]
        more = []
        for s in new_steps:
            more.append(s)
            if s.get("step") in ("write", "load") and rng.random() < 0.5:
                more.append({"step": rng.choice(["index_build", "index_build", "optimize"])})
        new_steps = more
        script = []
        gi = ni = 0
        sides = ["rel", "main"] if self.cfg0.builder == "rel" else ["main"]
        while gi < len(gq2_ops) or ni < len(new_steps):
            if gi < len(gq2_ops) and (ni >= len(new_steps) or rng.random() < 0.5):
                op = dict(gq2_ops[gi], side=rng.choice(sides))
                script.append({"mixed": "gq2", **op})
                gi += 1
            elif ni < len(new_steps):
                script.append({"mixed": "new", **new_steps[ni]})
                ni += 1
        if tail and rng.random() < 0.3:
            script.append({"mixed": "new", **tail[0]})
        self.script["mixed"] = script
        self.observe(g, {"step": "start"})
        for st in script:
            if time.time() - self.t0 > U.CASE_BUDGET:
                self.truncated = True
                break
            if st["mixed"] == "gq2":
                self.gq2_op(st["side"], g, st)
                self.hist.append({"step": f"gq2:{st['op']}", "kind": st.get("kind", ""), "side": st["side"]})
                self.cov[f"mixed_gq2_step:{st['op']}"] += 1
            else:
                ok = self.new_step(g, st)
                self.hist.append({**st, "ok": ok})
            self.observe(g, st, label=f"{len(self.hist)}:{st['mixed']}:{st.get('op') or st.get('step')}")
        self.final(g)


# ---------------------------------------------------------------- shared

def build_big(root: Path, side: str, n: int = 20000, seed: int = 4242) -> tuple[G.Config, Path]:
    """A 20,000-row graph built by a GQ 2 binary: one keyed type with two
    text fields (one plain `@index`, one `@key`-like slug), a vector and a
    numeric index, plus an insert-only log type the concurrency checks count."""
    cfg = G.Config(seed=seed, nodes=[
        G.NodeT("Doc", [G.Prop("k", "String", key=True, index=True), G.Prop("title", "String", index=True),
                        G.Prop("body", "String", index=True), G.Prop("n", "I64", index=True),
                        G.Prop("v", "vector", dim=4, index=True)], "single"),
        G.NodeT("Log", [G.Prop("k", "String", key=True), G.Prop("who", "String"), G.Prop("i", "I64")], "single"),
    ], edges=[], sizes={"Doc": n, "Log": 0}, builder=side, queries=["graph", "run", "café"],
        vec_query={"Doc.v": [0.5, -0.25, 0.125, 1.0]})
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    (root / "s.pg").write_text(G.render(cfg))
    g = root / "g"
    home = root / f"home-{side}"
    lib.must(lib.og(side, "init", "--schema", root / "s.pg", g, home=home), "init")
    rows = G.base_rows(cfg)
    for i, ch in enumerate(U.chunks(rows, 8000)):
        f = G.write_jsonl(root / f"base-{i}.jsonl", ch)
        lib.must(lib.og(side, "load", "--data", f, "--mode", "merge", "--target", g, "--json", home=home, timeout=500), "load")
    lib.must(lib.og(side, "index", "build", "--target", g, "--json", home=home, timeout=500), "index build")
    return cfg, g


READ_E2 = """query cnt_log() {
  match { $l: Log }
  return { count($l) as n }
}
query cnt_doc() {
  match { $d: Doc }
  return { count($d) as n }
}
query s_title($q: String) {
  match {
    $d: Doc
    search($d.title, $q)
  }
  return { $d.k }
  order { $d.k }
}
"""
READ_E3 = """query cnt_log() {
  match { $l: Log }
  return { count($l) as n }
}
query cnt_doc() {
  match { $d: Doc }
  return { count($d) as n }
}
query s_title($q: String) {
  match { $d: Doc }
  where match_terms($d.title, terms($q, mode: any))
  return { $d.k }
  order { $d.k }
}
"""
WRITE_E2 = 'query w($k: String, $who: String, $i: I64) {\n  insert Log { k: $k, who: $who, i: $i }\n}\n'
UPD_E2 = 'query u($k: String, $t: String) {\n  update Doc set { title: $t } where k = $k\n}\n'
UPD_E3 = 'query u($k: String, $t: String) {\n  match { $d: Doc } where $d.k = $k\n  update $d set { title: $t }\n}\n'


# ---------------------------------------------------------------- concurrent

def concurrent(pair: str, secs: float, gq2: str = "rel", maint: bool = False) -> dict:
    root = C.WORK / "mixed" / f"conc-{pair}-{gq2}{'-maint' if maint else ''}"
    cfg, g = build_big(root, gq2)
    for name, text in (("r2.gq", READ_E2), ("r3.gq", READ_E3), ("w.gq", WRITE_E2), ("u2.gq", UPD_E2), ("u3.gq", UPD_E3)):
        (root / name).write_text(text)
    writers = {"gq2w-newr": [gq2], "neww-gq2r": ["new"], "both-write": [gq2, "new"]}[pair]
    readers = {"gq2w-newr": ["new"], "neww-gq2r": [gq2], "both-write": [gq2, "new"]}[pair]
    stop = time.time() + secs
    log = {"acked": {}, "errors": Counter(), "samples": {}, "reads": Counter(), "writes": Counter(),
           "nonmonotonic": [], "panics": [], "uncoded": []}
    lock = threading.Lock()

    def note(side, kind, r):
        problems = C.integrity(r)
        with lock:
            if "panic" in problems:
                log["panics"].append({"side": side, "kind": kind, "err": C.clean(r.err)[-1500:]})
            if any(p.startswith("uncoded") for p in problems):
                log["uncoded"].append({"side": side, "kind": kind, "err": C.clean(r.err)[-600:], "out": r.out[-300:]})
            if not r.ok:
                key = f"{side}:{kind}:{C.clean(r.message() or '')[:90]}"
                log["errors"][key] += 1
                log["samples"].setdefault(key, C.clean(r.err)[-3000:] + C.clean(r.out)[-1000:])

    def writer(side):
        i = 0
        home = root / f"home-w-{side}"
        while time.time() < stop:
            k = f"{side}-{i:06d}"
            r = lib.og(side, "mutate", "w", "--query", root / "w.gq", "--target", g, "--json",
                       "--params", json.dumps({"k": k, "who": side, "i": i}), home=home, timeout=120)
            with lock:
                log["writes"][f"{side}:{'ok' if r.ok else 'refused'}"] += 1
                if r.ok:
                    log["acked"][k] = side
            note(side, "insert", r)
            if maint and i % 9 == 8:
                op = [["index", "build"], ["index", "rebuild", "--kind", "full-text"], ["optimize"],
                      ["cleanup", "--keep", "3", "--confirm"]][(i // 9) % 4]
                r = lib.og(side, *op, "--target", g, "--json", home=home, timeout=200)
                note(side, " ".join(op[:2]), r)
                with lock:
                    log["writes"][f"{side}:{op[0]}{'-' + op[1] if len(op) > 1 and not op[1].startswith('-') else ''}:{'ok' if r.ok else 'refused'}"] += 1
            if i % 5 == 4:
                u = "u2.gq" if side != "new" else "u3.gq"
                r = lib.og(side, "mutate", "u", "--query", root / u, "--target", g, "--json",
                           "--params", json.dumps({"k": f"doc-{(i * 37) % 20000:06d}", "t": f"conc {side} {i} graph"}),
                           home=home, timeout=120)
                note(side, "update", r)
            i += 1

    def reader(side):
        home = root / f"home-r-{side}"
        last = -1
        qf = root / ("r3.gq" if side == "new" else "r2.gq")
        while time.time() < stop:
            r = lib.og(side, "query", "cnt_log", "--query", qf, "--target", g, "--format", "json", home=home, timeout=120)
            note(side, "count", r)
            with lock:
                log["reads"][f"{side}:{'ok' if r.ok else 'refused'}"] += 1
            if r.ok:
                n = (r.json() or {}).get("rows", [{}])[0].get("n")
                if n is not None and n < last:
                    with lock:
                        log["nonmonotonic"].append({"side": side, "was": last, "now": n})
                last = max(last, n or 0)
            r = lib.og(side, "query", "s_title", "--query", qf, "--target", g, "--format", "json",
                       "--params", json.dumps({"q": "graph"}), home=home, timeout=120)
            note(side, "search", r)
            r = lib.og(side, "query", "cnt_doc", "--query", qf, "--target", g, "--format", "json", home=home, timeout=120)
            note(side, "count_doc", r)
            if r.ok and (r.json() or {}).get("rows", [{}])[0].get("n") != 20000:
                with lock:
                    log["errors"][f"{side}:partial:Doc count {(r.json() or {}).get('rows')}"] += 1

    threads = [threading.Thread(target=writer, args=(s,)) for s in writers]
    threads += [threading.Thread(target=reader, args=(s,)) for s in readers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # the end state, through every binary
    final = {}
    for side in ("new", gq2):
        snap = lib.og(side, "snapshot", "--target", g, "--json", home=root / "home-end")
        ex = lib.og(side, "export", "--target", g, home=root / "home-end", timeout=300)
        rows = C.parse_jsonl(ex.out) if ex.ok else []
        logs = {r["data"]["k"] for r in rows if r.get("type") == "Log"}
        missing = sorted(k for k in log["acked"] if k not in logs)
        final[side] = {"snapshot_ok": snap.ok, "export_ok": ex.ok, "log_rows": len(logs),
                       "acked": len(log["acked"]), "acked_missing": missing[:10], "n_missing": len(missing),
                       "msg": None if ex.ok else ex.message()[-500:],
                       "digest": C.sha("\n".join(sorted(json.dumps(r, sort_keys=True) for r in rows)))}
    rec = {"mode": "concurrent", "pair": pair, "gq2": gq2, "secs": secs, "maint": maint, "writes": dict(log["writes"]),
           "reads": dict(log["reads"]), "errors": dict(log["errors"].most_common(20)), "panics": log["panics"][:5], "samples": log["samples"],
           "uncoded": log["uncoded"][:5], "nonmonotonic": log["nonmonotonic"][:5], "final": final,
           "exports_equal": len({f["digest"] for f in final.values()}) == 1}
    with (OUT / "mixed.jsonl").open("a") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    return rec


# ---------------------------------------------------------------- kill -9

E3_READS = """query s_title($q: String) {
  match { $d: Doc }
  where match_terms($d.title, terms($q, mode: any))
  return { $d.k }
  order { $d.k }
}
query b_body($q: String) {
  match { let $pop = { match { $d: Doc } return { $d } }, from bm25($pop, $d.body, $q) as $s }
  return { $d.k, $s as s }
  order { $s desc }
  limit 20
}
query near($v: Vector(4)) {
  match { let $pop = { match { $d: Doc } return { $d } }, from nearest($pop, $d.v, $v, k: 10) as $x }
  return { $d.k, $x as x }
  order { $x }
  limit 10
}
"""


def new_reads(g: Path, root: Path, home: Path) -> dict:
    (root / "e3.gq").write_text(E3_READS)
    out = {}
    for name, params in (("s_title", {"q": "graph running"}), ("b_body", {"q": "café run"}),
                         ("near", {"v": [0.5, -0.25, 0.125, 1.0]})):
        r = lib.og("new", "query", name, "--query", root / "e3.gq", "--target", g, "--format", "json",
                   "--params", json.dumps(params), home=home, timeout=300)
        out[name] = (r.json() or {}).get("rows") if r.ok else {"error": r.message()[-300:]}
    return out


def kill(op: str, seed: int, gq2: str = "rel") -> dict:
    rng = random.Random(seed)
    base = C.WORK / "mixed" / f"kill-base-{gq2}"
    if not (base / "g").exists():
        build_big(base, gq2)
    root = C.WORK / "mixed" / f"kill-{op}-{seed}"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    g = lib.copy_graph(base / "g", root / "g")
    home = root / "home"
    before_reads = new_reads(g, root, home)
    ex0 = lib.og("new", "export", "--target", g, home=home, timeout=300)
    before = C.sha("\n".join(sorted(ex0.out.splitlines())))
    if op == "index_build":
        # rows after the GQ 2 index pass, so NEW's pass has work
        rows = [G.node_row(random.Random(seed), G.NodeT("Doc", build_cfg_props(), "single"), 30000 + i) for i in range(3000)]
        f = G.write_jsonl(root / "tail.jsonl", rows)
        lib.og("new", "load", "--data", f, "--mode", "insert", "--target", g, "--json", home=home, timeout=300)
        before_reads = new_reads(g, root, home)
        ex0 = lib.og("new", "export", "--target", g, home=home, timeout=300)
        before = C.sha("\n".join(sorted(ex0.out.splitlines())))
    args = {"index_build": ["index", "build"], "fts_rebuild": ["index", "rebuild", "--kind", "full-text"],
            "optimize": ["optimize"], "schema_apply": ["schema", "apply", "--schema", root / "s3.pg"],
            "schema_apply_plain": ["schema", "apply", "--schema", root / "s3p.pg"]}[op]
    (root / "s3.pg").write_text((base / "s.pg").read_text().replace("title: String @index", 'title: String @index(fulltext, analyzer = "standard_v1")'))
    (root / "s3p.pg").write_text((base / "s.pg").read_text() + "\nnode Extra {\n    k: String @key\n}\n")
    env = dict(lib.ENV, OMNIGRAPH_HOME=str(home))
    # the operation run to completion on a copy: its duration, and the answers it leads to
    refg = lib.copy_graph(g, root / "ref")
    t1 = time.time()
    lib.og("new", *args, "--target", refg, "--json", home=home, timeout=500)
    duration = time.time() - t1
    after_reads = new_reads(refg, root, home)
    shutil.rmtree(refg, ignore_errors=True)
    cmd = [str(lib.BIN["new"]), *map(str, args), "--target", str(g), "--json"]
    t0 = time.time()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    delay = rng.uniform(0.02, 0.98) * duration
    time.sleep(delay)
    killed = p.poll() is None
    if killed:
        p.send_signal(signal.SIGKILL)
    out, err = p.communicate()
    rec = {"mode": "kill", "op": op, "seed": seed, "gq2": gq2, "delay": round(delay, 3), "killed": killed,
           "op_rc": p.returncode, "op_secs": round(time.time() - t0, 3)}
    after = {}
    for side in ("new", gq2, "main") if gq2 != "main" else ("new", "main"):
        snap = lib.og(side, "snapshot", "--target", g, "--json", home=home)
        ex = lib.og(side, "export", "--target", g, home=home, timeout=300)
        r2 = None
        if side != "new":
            (root / "e2.gq").write_text(READ_E2)
            r2 = lib.og(side, "query", "s_title", "--query", root / "e2.gq", "--target", g, "--format", "json",
                        "--params", json.dumps({"q": "graph running"}), home=home)
        after[side] = {"snapshot": snap.ok, "snap_msg": None if snap.ok else snap.message()[-400:],
                       "export": ex.ok, "same_content": ex.ok and C.sha("\n".join(sorted(ex.out.splitlines()))) == before,
                       "lexical": None if r2 is None else (r2.ok, None if r2.ok else r2.message()[-300:]),
                       "panic": snap.panicked() or ex.panicked()}
    rec["after_kill"] = after
    rec["op_duration"] = round(duration, 3)
    now = new_reads(g, root, home)
    rec["reads_after_kill"] = "before" if now == before_reads else ("after" if now == after_reads else "neither")
    rec["new_reads_same_after_kill"] = rec["reads_after_kill"] != "neither"
    rerun = lib.og("new", *args, "--target", g, "--json", home=home, timeout=500)
    rec["rerun"] = {"ok": rerun.ok, "msg": None if rerun.ok else rerun.message()[-500:]}
    rec["new_reads_same_after_rerun"] = new_reads(g, root, home) == after_reads
    ex = lib.og("new", "export", "--target", g, home=home, timeout=300)
    rec["content_same_after_rerun"] = C.sha("\n".join(sorted(ex.out.splitlines()))) == before
    # the cleanup collector after a kill
    cl = lib.og("new", "cleanup", "--keep", "1", "--confirm", "--target", g, "--json", home=home, timeout=300)
    rec["cleanup_after"] = {"ok": cl.ok, "msg": None if cl.ok else cl.message()[-300:]}
    rec["new_reads_same_after_cleanup"] = new_reads(g, root, home) == after_reads
    with (OUT / "mixed.jsonl").open("a") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    shutil.rmtree(root, ignore_errors=True) if all(
        [rec["new_reads_same_after_kill"], rec["new_reads_same_after_rerun"], rec["content_same_after_rerun"]]) else None
    return rec


def build_cfg_props():
    return [G.Prop("k", "String", key=True, index=True), G.Prop("title", "String", index=True),
            G.Prop("body", "String", index=True), G.Prop("n", "I64", index=True),
            G.Prop("v", "vector", dim=4, index=True)]


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    mode = sys.argv[1]
    if mode == "alternate":
        import concurrent.futures as cf
        lo, hi = int(sys.argv[2]), int(sys.argv[3])
        out = OUT / "alternate.jsonl"

        def one(s):
            rec = Alternate(s, root=C.WORK / "alt" / f"s{s}").run()
            if not rec["findings"] and rec["status"] == "ok":
                shutil.rmtree(C.WORK / "alt" / f"s{s}", ignore_errors=True)
            return rec
        with cf.ProcessPoolExecutor(max_workers=int(os.environ.get("UF_JOBS", "12"))) as ex:
            for rec in ex.map(one, range(lo, hi)):
                with out.open("a") as f:
                    f.write(json.dumps(rec, default=str) + "\n")
                print(rec["seed"], rec["status"], rec["secs"], len(rec["findings"]), flush=True)
    elif mode == "concurrent":
        print(json.dumps(concurrent(sys.argv[2], float(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else "rel",
                                    len(sys.argv) > 5 and sys.argv[5] == "maint"), default=str)[:3000])
    elif mode == "kill":
        op = sys.argv[2]
        for s in range(int(sys.argv[3]), int(sys.argv[4])):
            rec = kill(op, s, sys.argv[5] if len(sys.argv) > 5 else "rel")
            a = rec["after_kill"]
            print(op, s, "killed" if rec["killed"] else "finished", rec["delay"], "of", rec["op_duration"], "|",
                  {k: (v["snapshot"], v["same_content"], v["lexical"] and v["lexical"][0]) for k, v in a.items()},
                  "| reads:", rec["reads_after_kill"], rec["new_reads_same_after_rerun"], rec["content_same_after_rerun"],
                  "rerun", rec["rerun"]["ok"], "cleanup", rec["cleanup_after"]["ok"], flush=True)
