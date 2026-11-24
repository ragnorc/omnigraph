"""Shrink a failing case to the smallest schema, data and script that still fails.

    python ufshrink.py <seed> <finding-kind> [<sig-prefix>] [--out repro/NAME.json] [--alternate]

The predicate is "the replayed case reports a finding of this kind whose
signature starts with the prefix". Passes, each kept only while the
predicate holds: drop NEW steps, drop GQ 2 operations (delta debugging,
halves first, then one at a time), drop node and edge types the finding
does not name, drop properties, halve the row counts. Every trial is a full
replay of `ufcase.Case` from the case's own seed with the reduced
configuration and explicit script, run in parallel. GQ 2's probes are
skipped unless the finding's oracle needs them (3 and 4). The result is a
JSON case `ufreplay.py` runs.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import shutil
import sys
from pathlib import Path

import ufcommon as C
import ufgen as G
import ufcase as U

JOBS = int(os.environ.get("UF_JOBS", "12"))


def trial(args):
    seed, cfg_json, script, kind, prefix, tag, alternate = args
    cfg = G.Config.from_json(cfg_json)
    root = C.WORK / "shrink" / f"s{seed}-{tag}"
    cls = U.Case
    if alternate:
        import ufmixed
        cls = ufmixed.Alternate
    rec = cls(seed, cfg=cfg, script=json.loads(json.dumps(script)), root=root).run()
    hit = any(f["kind"] == kind and f["sig"].startswith(prefix) for f in rec["findings"])
    shutil.rmtree(root, ignore_errors=True)
    return hit, rec


class Shrinker:
    def __init__(self, seed, kind, prefix, alternate=False):
        self.seed, self.kind, self.prefix, self.alternate = seed, kind, prefix, alternate
        self.n = 0
        self.log = []

    def test_many(self, candidates):
        """The first candidate (in order) that still fails, or None."""
        args = []
        for cfg, script in candidates:
            self.n += 1
            args.append((self.seed, cfg.to_json(), script, self.kind, self.prefix, f"t{self.n}", self.alternate))
        with cf.ProcessPoolExecutor(max_workers=JOBS) as ex:
            res = list(ex.map(trial, args))
        for (cfg, script), (hit, rec) in zip(candidates, res):
            if hit:
                return cfg, script, rec
        return None

    def ddmin(self, cfg, script, key):
        items = list(script.get(key) or [])
        n = 2
        while len(items) >= 1:
            chunk = max(1, len(items) // n)
            cands = []
            for i in range(0, len(items), chunk):
                rest = items[:i] + items[i + chunk:]
                cands.append((cfg, {**script, key: rest}))
            got = self.test_many(cands)
            if got:
                cfg, script, _ = got
                items = list(script[key])
                n = max(n - 1, 2)
            elif chunk == 1:
                break
            else:
                n = min(len(items), n * 2)
        return cfg, script

    def shrink_cfg(self, cfg, script, keep_types):
        # drop node types and edges not named
        changed = True
        while changed:
            changed = False
            cands = []
            for e in list(cfg.edges):
                c = G.clone(cfg)
                c.edges = [x for x in c.edges if x.name != e.name]
                cands.append((c, script))
            for n in list(cfg.nodes):
                if n.name in keep_types or any(e.src == n.name or e.dst == n.name for e in cfg.edges):
                    continue
                c = G.clone(cfg)
                c.nodes = [x for x in c.nodes if x.name != n.name]
                c.sizes.pop(n.name, None)
                cands.append((c, script))
            for n in cfg.nodes:
                for p in n.props:
                    if p.key or p.name in n.compound:
                        continue
                    c = G.clone(cfg)
                    t = c.node(n.name)
                    t.props = [q for q in t.props if q.name != p.name]
                    cands.append((c, script))
            for n in cfg.nodes:
                if cfg.sizes.get(n.name, 0) > 6:
                    c = G.clone(cfg)
                    c.sizes[n.name] = max(3, cfg.sizes[n.name] // 2)
                    cands.append((c, script))
            for i in range(0, len(cands), JOBS):
                got = self.test_many(cands[i:i + JOBS])
                if got:
                    cfg, script, _ = got
                    changed = True
                    break
        return cfg, script


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("seed", type=int)
    ap.add_argument("kind")
    ap.add_argument("prefix", nargs="?", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--alternate", action="store_true")
    ap.add_argument("--records", default=str(C.WORK / "results" / "cases.jsonl"))
    ap.add_argument("--phase", default="all")
    a = ap.parse_args()
    if not os.environ.get("UF_NO_PROBE") and a.kind in ("index_state_changed_answer", "fresh_graph_differs",
                                                          "profile_reference_mismatch", "one_way_listing_noop",
                                                          "one_way_listing", "new_cannot_open"):
        os.environ["UF_NO_PROBE"] = "1"
    out = Path(a.out)
    if out.exists():
        saved = json.loads(out.read_text())
        cfg, script = G.Config.from_json(saved["cfg"]), saved["script"]
    else:
        rec = None
        for line in open(a.records):
            r = json.loads(line)
            if r["seed"] == a.seed:
                rec = r
        cfg = G.draw(a.seed, stress=rec.get("stress", False))
        cfg.builder = rec["builder"]
        script = rec["script"]
    sh = Shrinker(a.seed, a.kind, a.prefix, a.alternate)
    ok = sh.test_many([(cfg, script)])
    if not ok:
        print("the case does not fail on replay"); sys.exit(1)
    keep = {f["detail"].get("read", "").split("_")[-2] if "_" in f["detail"].get("read", "") else ""
            for f in ok[2]["findings"] if f["kind"] == a.kind}
    keep = {k for k in keep if k}
    for key in ("new", "gq2") if a.phase in ("all", "script") else ():
        cfg, script = sh.ddmin(cfg, script, key)
        save(out, a, cfg, script, sh)
    if a.phase in ("all", "cfg"):
        cfg, script = sh.shrink_cfg(cfg, script, keep)
        cfg, script = sh.ddmin(cfg, script, "new")
    save(out, a, cfg, script, sh)
    print(json.dumps({"trials": sh.n, "types": [n.name for n in cfg.nodes], "sizes": cfg.sizes,
                      "new": script.get("new"), "gq2": script.get("gq2")}, default=str)[:3000])


def save(out, a, cfg, script, sh):
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"seed": a.seed, "kind": a.kind, "sig_prefix": a.prefix, "alternate": a.alternate,
                               "trials": sh.n, "schema": G.render(cfg), "cfg": cfg.to_json(), "script": script},
                              indent=1, default=str, ensure_ascii=False))


if __name__ == "__main__":
    main()
