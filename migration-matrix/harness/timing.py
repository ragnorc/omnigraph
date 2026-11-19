"""Search timings on a larger corpus: OLD's edition-2 text against NEW's
respelling on the same OLD-built graph.

For each corpus size: OLD `init`, `load`, `index build` (edition 2's
full-text, vector and scalar indexes). Then every search query runs `REPS`
times per side, sides interleaved so drift hits both, each invocation timed
by wall clock and by `--stats` (elapsed, object-store calls, peak RSS). Rows
are compared by p22's `differences`. A second NEW pass runs after NEW's own
`index build` on a copy (NEW's index kinds), to show whether NEW's index
pass changes its cost.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

import catalog
from gen import SEARCH_PG, search_corpus
from lib import WORK, og, must, arrow_observation, p22, copy_graph, append_jsonl

OUT = WORK / "results" / "timing.jsonl"
SEARCH_NAMES = ["s_search_title", "s_match_text_body", "s_fuzzy_default", "s_bm25_title",
                "s_bm25_filtered", "s_nearest", "s_rrf"]
REPS = int(__import__("os").environ.get("MM_REPS", "5"))


def run_q(side, g, qfile, name, params, statsfile):
    args = ["query", name, "--query", qfile, "--target", g, "--format", "arrow", "--stats", statsfile]
    if params:
        args += ["--params", json.dumps(params)]
    r = og(side, *args, binary=True, home=WORK / "home" / f"timing-{side}", timeout=900)
    stats = None
    try:
        stats = json.loads(Path(statsfile).read_text())
    except Exception:
        pass
    return r, stats


def main(sizes):
    qs = {q.name: q for q in catalog.SEARCHES}
    for n in sizes:
        root = WORK / "timing" / f"n{n}"
        if root.exists():
            import shutil
            shutil.rmtree(root)
        root.mkdir(parents=True)
        (root / "schema.pg").write_text(SEARCH_PG)
        search_corpus(root / "docs.jsonl", n)
        (root / "e2.gq").write_text("\n\n".join(qs[x].e2 for x in SEARCH_NAMES if x != "s_rrf" or True))
        (root / "e3.gq").write_text("\n\n".join(qs[x].e3 for x in SEARCH_NAMES))
        g = root / "graph"
        h = WORK / "home" / "timing-build"
        must(og("old", "init", "--schema", root / "schema.pg", g, home=h), "init")
        # a keyed merge load takes at most 8,192 entities: load in chunks
        lines = (root / "docs.jsonl").read_text().splitlines()
        load_secs = 0.0
        for k in range(0, len(lines), 8000):
            part = root / f"docs.{k}.jsonl"
            part.write_text("\n".join(lines[k:k + 8000]) + "\n")
            r = must(og("old", "load", "--data", part, "--mode", "merge", "--target", g, "--json", home=h, timeout=1800), "load")
            load_secs += r.secs
        r.secs = load_secs
        b = must(og("old", "index", "build", "--target", g, "--json", home=h, timeout=1800), "index build")
        append_jsonl(OUT, {"kind": "corpus", "n": n, "load_secs": r.secs, "index_secs": b.secs})
        gn = copy_graph(g, root / "graph_new_indexed")
        nb = og("new", "index", "build", "--target", gn, "--json", home=h, timeout=1800)
        append_jsonl(OUT, {"kind": "new_index_build", "n": n, "secs": nb.secs, "ok": nb.ok,
                           "msg": None if nb.ok else nb.message()[-800:], "out": nb.out[-3000:]})
        for name in SEARCH_NAMES:
            q = qs[name]
            sides = {"old": [], "new": [], "new_after_new_index": []}
            obs = {}
            for i in range(REPS):
                for side, graph, f, s in (("old", g, root / "e2.gq", "old"), ("new", g, root / "e3.gq", "new"),
                                          ("new_after_new_index", gn, root / "e3.gq", "new")):
                    r, st = run_q(s, graph, f, name, q.params, root / f"stats-{side}.json")
                    sides[side].append({"secs": r.secs, "ok": r.ok, "stats": st,
                                        "msg": None if r.ok else r.message()[-400:]})
                    if i == 0:
                        obs[side] = arrow_observation(r, "order {" in q.e2)
            summary = {}
            for side, runs in sides.items():
                secs = [x["secs"] for x in runs if x["ok"]]
                st = [x["stats"] for x in runs if x["ok"] and x["stats"]]
                summary[side] = {
                    "ok": all(x["ok"] for x in runs), "median_secs": statistics.median(secs) if secs else None,
                    "min_secs": min(secs) if secs else None, "max_secs": max(secs) if secs else None,
                    "stats_sample": st[0] if st else None, "msg": runs[0]["msg"],
                }
            diffs = {side: p22().differences(obs["old"], obs[side]) for side in ("new", "new_after_new_index")}
            rows = {side: (obs[side].rows or [])[:10] for side in obs}
            append_jsonl(OUT, {"kind": "search_timing", "n": n, "query": name, "params": q.params,
                               "summary": summary, "diffs": diffs, "rows": rows,
                               "row_counts": {s: len(o.rows or []) for s, o in obs.items()}})
            print(n, name, {s: (v["median_secs"], v["ok"]) for s, v in summary.items()}, flush=True)


if __name__ == "__main__":
    main([int(x) for x in sys.argv[1:]] or [20000, 100000])
