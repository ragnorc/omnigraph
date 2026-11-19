"""Aggregate the per-graph records into the report's tables.

Reads `<work>/results/*.jsonl` (ops.py), `serve.jsonl` (serve.py) and
`timing.jsonl` (timing.py); writes `migration-matrix/results/`:

* `coverage.md`: every combination, built or not, run or not;
* `differences.md` and `differences.json`: every OLD-vs-NEW difference,
  grouped by operation and signature, with the graphs it occurred on, its
  class and RFC bullet (rules below), OLD's and NEW's result;
* `rollback.md`: for every NEW operation, which OLD probes still work
  afterwards and which refuse, over every graph it ran on;
* `timing.md`: the search timings.

Classification: `RULES` maps a difference signature to a class
(`a` listed in the RFC's Compatibility section, `b` intended by the RFC but
not listed there, `c` a defect) and the bullet or reason. A signature no
rule names is printed as `unclassified`, to be read by hand.
"""

from __future__ import annotations

import itertools
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from build import SCHEMAS, INDEX_STATES, HISTORIES, combo_name
from lib import WORK

OUTDIR = Path(__file__).resolve().parents[1] / "results"
RES = WORK / "results"

BULLETS = {
    "language.diagnostic": "Language: no edition-2 text compiles after the break; it fails with its diagnostic, whose printed fix applies",
    "language.accepted-lexical-under-not-or": "Language: a lexical predicate under `not` or `or` (T38) is accepted",
    "writes.keyed-insert": "Writes and loads: a keyed `insert` on an existing key fails with `key_conflict` where edition 2 replaced the row; a stored write that relied on replacement still compiles and fails at run time with `upsert` and `replace` named",
    "writes.from-to": "Writes and loads: `from:` and `to:` in an edge insert are refused with `@src:` and `@dst:`",
    "writes.load-modes": "Writes and loads: the load modes `append` and `merge` are refused with `insert` and `replace` as fixes",
    "writes.new-statements": "Writes and loads: `upsert`, `--mode upsert` and an edge `update` are new",
    "results.integer-float-comparison": "Results: an integer property compared with a whole-number float literal compares as F64",
    "results.hop-range-self-loop": "Results: a hop range other than the single hop no longer returns its start node through a self-loop",
    "results.avg-exact-integer": "Results: avg over integers is computed exactly and rounded once to F64",
    "results.limit-tie-rule": "Results: a read with `limit` and no `order` returns the first rows under the tie rule",
    "results.bm25-f64": "Results: bm25 scores are bm25_v1's F64 on every field, where edition 2 gave Lance's F32",
    "results.full-text-analysis": "Results: rows change on every full-text field where edition 2's analysis differs from its profile (english_v1)",
    "results.distance-rename": "Results (fixed map): an unaliased nearest column `d._distance` becomes `_distance`",
    "results.bm25-score-rename": "Results: a full-text score edition 2 returned as `<binding>._score` is the `_score` column",
    "results.fusion-window": "Results: fusion rows change where an arm's window differs; a fusion result loses fan-out rows",
    "reverting.fts-index": "Reverting: an older binary refuses a full-text index the new binary built or rebuilt",
    "reverting.one-way": "Reverting: stage B's one-way changes (language, analyzer default, index kinds, vector distances, boot behavior) revert only together",
    "results.index-state": "Results: rows change where edition 2's answer depended on index state",
}


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.open() if l.strip()]


def dims(graph: str) -> tuple[str, str, str]:
    s, i, h = graph.split("__")
    return s, i, h


def short(s, n=160):
    s = (s or "").replace("\n", " ").replace("|", "\\|")
    s = re.sub(r"\x1b\[[0-9;]*m", "", s)
    s = re.sub(r"\s+", " ", s)
    return s[:n] + ("…" if len(s) > n else "")


# ---------------------------------------------------------------- reads

def _close(a, b) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        try:
            a, b = float(a), float(b)
        except (TypeError, ValueError):
            return False
        return a == b or abs(a - b) <= 2e-7 * max(abs(a), abs(b))
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_close(x, y) for x, y in zip(a, b))
    return a == b


def values_close(old_rows, new_rows, ordered: bool) -> bool | None:
    """Rows equal by position, floats within F32 precision; None when the
    stored rows were truncated."""
    if not isinstance(old_rows, list) or not isinstance(new_rows, list):
        return None
    if any(isinstance(x, str) for x in old_rows + new_rows):
        return None  # a truncation marker
    if len(old_rows) != len(new_rows):
        return False
    a = [list(r.values()) for r in old_rows]
    b = [list(r.values()) for r in new_rows]
    if not ordered:
        key = lambda r: json.dumps(r, sort_keys=True, default=str)
        a, b = sorted(a, key=key), sorted(b, key=key)
    return all(_close(x, y) for x, y in zip(a, b))


def read_signature(r: dict, tag: str) -> tuple | None:
    res = r["results"].get(tag)
    if not res:
        return None
    if not res["diffs"]:
        return None
    aspects = tuple(sorted({d[0] for d in res["diffs"]}))
    ref = dict(res.get("refined") or {})
    close = values_close(r["old"].get("rows"), res["new"].get("rows"), bool(r["old"].get("ordered")))
    if close is not None:
        ref["values_equal"] = close
    extra = []
    if "rows" in aspects:
        if ref.get("values_equal"):
            extra.append("values-equal-within-f32")
        elif ref.get("keys_equal"):
            extra.append("same-identities-same-order")
        elif ref.get("keys_equal_unordered"):
            extra.append("same-identities-other-order")
        else:
            extra.append("different-identities")
    err = None
    if "error" in aspects:
        old_err = r["old"].get("error")
        new_err = res["new"].get("error")
        code = lambda e: (re.search(r"\b([QTPE]\d{1,3})\b", e or "") or [None, None])[1]
        err = f"old:{'refused ' + (code(old_err) or '') if old_err else 'ok'} new:{'refused ' + (code(new_err) or '') if new_err else 'ok'}"
    return aspects + tuple(extra) + ((err,) if err else ())


def classify_read(query: str, sig: tuple, label: str) -> tuple[str, str]:
    a = set(sig)
    if query == "q_int_float_literal":
        return "a", "results.integer-float-comparison"
    if query == "q_hops":
        return "a", "results.hop-range-self-loop"
    if query == "q_avg_int_extremes":
        return "a", "results.avg-exact-integer"
    if query == "q_limit_no_order":
        return "a", "results.limit-tie-rule"
    if query == "s_not_search" and any(str(x).startswith("old:refused T38 new:ok") for x in sig):
        return "a", "language.accepted-lexical-under-not-or"
    if query == "s_stopword" and any("new:refused T20" in str(x) for x in sig):
        return "b", "section 4 'Empty text: a query with no searchable terms is a type error'; no Compatibility bullet names a refusal where edition 2 answered"
    if query == "s_nearest_unaliased" and a <= {"columns", "rows", "values-equal-within-f32"}:
        return "a", "results.distance-rename"
    if query == "s_bm25_unaliased" and "columns" in a:
        if "values-equal-within-f32" in a:
            return "a", "results.bm25-score-rename + results.bm25-f64"
        return "a?", "results.bm25-score-rename + results.bm25-f64, scores moved (read by hand)"
    if query.startswith("s_bm25") and "types" in a and a <= {"types", "rows", "values-equal-within-f32"}:
        return "a", "results.bm25-f64"
    if query.startswith("s_bm25") and "types" in a and "same-identities-same-order" in a:
        return "a", "results.bm25-f64 + results.index-state (edition 2's index statistics still count deleted or superseded rows)"
    if query.startswith("s_bm25") or query == "s_rrf":
        return "a?", "results.full-text-analysis / results.bm25-f64 (ranking moved; read by hand)"
    if query.startswith("s_") and "rows" in a:
        return "a?", "results.full-text-analysis (rows moved; read by hand)"
    return "unclassified", ""


def classify_write(key: tuple) -> tuple[str, str]:
    kind = key[0]
    if kind == "load":
        _, f, mode, old_status, new_status, same = key
        if mode in ("append", "merge") and new_status == "refused":
            return "a", "writes.load-modes"
        if old_status == "no OLD counterpart":
            return "a", "writes.new-statements (`--mode upsert`)"
        return "unclassified", ""
    _, q, variant, old_status, new_status, same = key
    if variant == "from_to" and new_status == "refused":
        return "a", "writes.from-to"
    if variant in ("insert", "fixed") and old_status == "ok" and new_status == "refused":
        return "a", "writes.keyed-insert (an edition-2 keyed insert compiles unchanged and fails at run time with key_conflict)"
    if q == "w_update_edge" and old_status == "refused" and new_status == "ok":
        return "a", "writes.new-statements (edge `update`; edition 2 refused T16)"
    if variant == "upsert" and old_status == "ok" and new_status == "ok":
        return "a", "writes.new-statements (`upsert` keeps omitted properties; edition 2's keyed insert replaced the row)"
    return "unclassified", ""


# ---------------------------------------------------------------- main

def main():
    OUTDIR.mkdir(parents=True, exist_ok=True)
    graphs = sorted(p.stem for p in RES.glob("*__*__*.jsonl"))
    built = {}
    gdir = WORK / "graphs"
    for s, i, h in itertools.product(SCHEMAS, INDEX_STATES, HISTORIES):
        n = combo_name(s, i, h)
        built[n] = (gdir / n / "meta.json").exists() and not (gdir / n / "BUILD_FAILED").exists()
    done = {p.stem for p in RES.glob("*.done")}

    # coverage
    cov = ["| schema | index state | history | built by OLD | NEW run complete | harness errors |", "|---|---|---|---|---|---|"]
    herr = defaultdict(int)
    records = {}
    for g in graphs:
        recs = load(RES / f"{g}.jsonl")
        records[g] = recs
        herr[g] = sum(1 for r in recs if r["kind"] == "harness_error")
    for s, i, h in itertools.product(SCHEMAS, INDEX_STATES, HISTORIES):
        n = combo_name(s, i, h)
        cov.append(f"| {s} | {i} | {h} | {'yes' if built[n] else 'no'} | {'yes' if n in done else 'no'} | {herr.get(n, 0)} |")
    (OUTDIR / "coverage.md").write_text("\n".join(cov) + "\n")

    # differences
    diffs = defaultdict(lambda: {"graphs": [], "examples": []})
    harness_errors = []
    for g, recs in records.items():
        for r in recs:
            k = r["kind"]
            if k == "harness_error":
                harness_errors.append((g, r.get("op"), short(r.get("error"), 400)))
            if k == "compare":
                label = r["op"].split(":")[0]
                label = re.sub(r"\[.*\]", "[…]", label) if label.startswith(("snapshot", "branch")) else label
                for tag in r["results"]:
                    sig = read_signature(r, tag)
                    if sig is None:
                        continue
                    key = ("read", r["query"], tag, label, sig)
                    d = diffs[key]
                    d["graphs"].append(g)
                    if len(d["examples"]) < 2:
                        res = r["results"][tag]
                        d["examples"].append({"graph": g, "op": r["op"], "old_status": r["old_status"],
                                              "old": short(json.dumps(r["old"].get("rows") or r["old"].get("error"), ensure_ascii=False, default=str), 300),
                                              "old_types": r["old"].get("types"), "new_types": res["new"].get("types"),
                                              "new": short(json.dumps(res["new"].get("rows") or res["new"].get("error"), ensure_ascii=False, default=str), 300),
                                              "diffs": res["diffs"], "refined": res.get("refined")})
            elif k == "write_compare":
                same = r["same_state"] and r["old_status"] == r["new_status"]
                if same:
                    continue
                key = ("write", r["query"], r["variant"], r["old_status"], r["new_status"], r["same_state"])
                d = diffs[key]
                d["graphs"].append(g)
                if len(d["examples"]) < 2:
                    d["examples"].append({"graph": g, "old_msg": short(r.get("old_msg"), 300), "new_msg": short(r.get("new_msg"), 300),
                                          "diff": [short(x, 300) for x in r.get("diff", [])[:4]]})
            elif k == "load_compare":
                same = r.get("same_state") and r.get("old_status") == r["new_status"]
                if same or r.get("old_mode") is None and r["new_status"] == "ok":
                    if r.get("old_mode") is None:
                        key = ("load", r["file"], r["mode"], "no OLD counterpart", r["new_status"], None)
                        diffs[key]["graphs"].append(g)
                    continue
                key = ("load", r["file"], r["mode"], r.get("old_status"), r["new_status"], r.get("same_state"))
                d = diffs[key]
                d["graphs"].append(g)
                if len(d["examples"]) < 2:
                    d["examples"].append({"graph": g, "old_msg": short(r.get("old_msg"), 300), "new_msg": short(r.get("new_msg"), 300),
                                          "diff": [short(x, 300) for x in r.get("diff", [])[:4]]})
            elif k == "side":
                pass
            elif k == "exports" and not r["equal"]:
                key = ("export", "old vs new export", r["old"], r["new"], str(r["sample_diff"][:1]))
                diffs[key]["graphs"].append(g)
            elif k == "digest" and not r["unchanged"]:
                key = ("readonly", "read-only phase wrote files", tuple(r["added"][:3]), tuple(r["removed"][:3]))
                diffs[key]["graphs"].append(g)
            elif k == "roundtrip" and not (r["old_eq_new"] and r["old_eq_back"]):
                key = ("roundtrip", json.dumps(r["steps"]), r["old_eq_new"], r["old_eq_back"])
                d = diffs[key]
                d["graphs"].append(g)
                if len(d["examples"]) < 2:
                    d["examples"].append({"graph": g, "diff_old_new": r["diff_old_new"][:3], "diff_old_back": r["diff_old_back"][:3]})
            elif k == "merge_compare" and not r["same_state"]:
                key = ("merge", r["op"].split("[")[0], r["old_status"], tuple(short(x, 200) for x in r["diff"][:2]))
                diffs[key]["graphs"].append(g)
            elif k == "new_op":
                for x in r["runs"]:
                    if isinstance(x, dict) and x.get("panic"):
                        key = ("panic", r["op"], tuple(x["args"][:3]), short(x.get("msg"), 300))
                        diffs[key]["graphs"].append(g)

    # side-by-side CLI surfaces (snapshot, schema show, commit lists)
    side = defaultdict(dict)
    for g, recs in records.items():
        for r in recs:
            if r["kind"] == "side":
                side[(g, r["op"])][r["side"]] = r
    for (g, op), pair in side.items():
        o, n = pair.get("old"), pair.get("new")
        if not o or not n:
            continue
        if o["status"] != n["status"]:
            key = ("surface", op, o["status"], n["status"], short(n.get("msg") or o.get("msg"), 200))
            diffs[key]["graphs"].append(g)
        elif o["status"] == "ok" and o.get("json") != n.get("json"):
            oj, nj = o.get("json"), n.get("json")
            keys = sorted(set(oj or {}) ^ set(nj or {})) if isinstance(oj, dict) and isinstance(nj, dict) else []
            changed = sorted(k for k in (oj or {}) if isinstance(nj, dict) and k in nj and oj[k] != nj[k]) if isinstance(oj, dict) else []
            key = ("surface", re.sub(r":.*", "", op), "json differs", tuple(keys), tuple(changed[:6]))
            d = diffs[key]
            d["graphs"].append(g)
            if len(d["examples"]) < 1:
                d["examples"].append({"graph": g, "old": short(json.dumps(oj)[:2000], 600), "new": short(json.dumps(nj)[:2000], 600)})

    # rollback
    rb = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    rb_msgs = defaultdict(dict)
    rb_changed = defaultdict(lambda: [0, 0])
    newop = defaultdict(lambda: defaultdict(int))
    newop_msgs = defaultdict(dict)
    for g, recs in records.items():
        base = next((r["probes"] for r in recs if r["kind"] == "baseline"), {})
        for r in recs:
            if r["kind"] == "new_op":
                op = re.sub(r"\[.*\]", "[…]", r["op"])
                st = "ok" if all(isinstance(x, dict) and x.get("status") == "ok" for x in r["runs"]) else \
                    "refused:" + ",".join(sorted({" ".join(x["args"][:2]) for x in r["runs"] if isinstance(x, dict) and x.get("status") != "ok"}))
                newop[op][st] += 1
                if st != "ok" and st not in newop_msgs[op]:
                    bad = [x for x in r["runs"] if isinstance(x, dict) and x.get("status") != "ok"]
                    newop_msgs[op][st] = (g, short(bad[0].get("msg"), 300))
            if r["kind"] != "rollback":
                continue
            op = re.sub(r"\[.*\]", "[…]", r["op"])
            rb_changed[op][0 if r["changed"] else 1] += 1
            if not r["changed"]:
                continue
            for probe, v in r["probes"].items():
                b = base.get(probe, {}).get("status")
                state = v["status"] if (b == "ok" or probe.startswith("recover")) else f"{v['status']} (baseline {b})"
                rb[op][probe][state] += 1
                if v["status"] != "ok" and probe not in rb_msgs[op]:
                    rb_msgs[op][probe] = (g, short(v.get("msg"), 260))

    # write the tables
    out = []
    out.append("| # | kind | operation | signature | graphs | class | bullet / reason |")
    out.append("|---|---|---|---|---|---|---|")
    # A search difference that never occurs on a graph whose edition-2
    # indexes cover every row (index states complete and fts_rebuilt) is one
    # where edition 2's answer depended on index state.
    FULL = {"complete", "fts_rebuilt"}
    full_diff = set()
    for key, d in diffs.items():
        if key[0] == "read" and any(dims(g)[1] in FULL for g in d["graphs"]) and \
                not {"values-equal-within-f32"} & set(key[4]) and "rows" in key[4]:
            full_diff.add((key[1], key[2], key[3]))
    rows = []
    for key, d in diffs.items():
        kind = key[0]
        cls, why = "unclassified", ""
        if kind == "read":
            cls, why = classify_read(key[1], key[4], key[3])
            states = {dims(g)[1] for g in d["graphs"]}
            if cls in ("a?", "unclassified") and key[1].startswith("s_") and not states & FULL \
                    and (key[1], key[2], key[3]) not in full_diff:
                cls = "a"
                why = "results.index-state" + (" + results.bm25-f64" if "types" in key[4] else "") + \
                    (" + results.bm25-score-rename" if "columns" in key[4] else "") + \
                    f" (only on index states {', '.join(sorted(states))}; never where edition 2's indexes covered every row)"
        elif kind in ("write", "load"):
            cls, why = classify_write(key)
        rows.append((kind, key, d, cls, why))
    rows.sort(key=lambda x: (x[0], str(x[1])))
    for idx, (kind, key, d, cls, why) in enumerate(rows, 1):
        gs = d["graphs"]
        by = defaultdict(set)
        for g in gs:
            s, i, h = dims(g)
            by["schema"].add(s); by["index"].add(i); by["history"].add(h)
        where = f"{len(gs)} ({'/'.join(sorted(by['schema']))}; idx {','.join(sorted(by['index'])) if len(by['index']) < 7 else 'all'}; hist {','.join(sorted(by['history'])) if len(by['history']) < 8 else 'all'})"
        out.append(f"| {idx} | {kind} | {short(' '.join(map(str, key[1:3])), 80)} | {short(' '.join(map(str, key[3:])), 200)} | {where} | {cls} | {short(why, 200)} |")
    (OUTDIR / "differences.md").write_text("\n".join(out) + "\n")
    js = [{"kind": k, "key": [str(x) for x in key], "graphs": d["graphs"], "examples": d["examples"], "class": c, "why": w}
          for k, key, d, c, w in rows]
    (OUTDIR / "differences.json").write_text(json.dumps(js, indent=1, ensure_ascii=False, default=str))

    rbo = ["| NEW operation | NEW outcome (graphs) | graph changed / unchanged | OLD probes after (graphs per outcome) | first OLD refusal |", "|---|---|---|---|---|"]
    for op in sorted(set(rb_changed) | set(newop)):
        probes_s = "; ".join(f"{p}: " + ", ".join(f"{st}×{n}" for st, n in sorted(v.items())) for p, v in sorted(rb[op].items())
                             if any(not st.startswith("ok") for st in v))
        if not probes_s:
            probes_s = "all probes ok" if rb[op] else "—"
        first = "; ".join(f"{p}: {m[1]} ({m[0]})" for p, m in list(rb_msgs[op].items())[:2])
        no = ", ".join(f"{st}×{n}" for st, n in sorted(newop[op].items()))
        nm = "; ".join(f"{st}: {m[1]}" for st, m in list(newop_msgs[op].items())[:1])
        rbo.append(f"| {op} | {short(no + (' — ' + nm if nm else ''), 260)} | {rb_changed[op][0]} / {rb_changed[op][1]} | {short(probes_s, 500)} | {short(first, 300)} |")
    (OUTDIR / "rollback.md").write_text("\n".join(rbo) + "\n")

    he = ["| graph | operation | error |", "|---|---|---|"] + [f"| {g} | {o} | {e} |" for g, o, e in harness_errors]
    (OUTDIR / "harness_errors.md").write_text("\n".join(he) + "\n")

    # timing
    tm = load(RES / "timing.jsonl")
    t = ["| corpus | query | OLD median s | NEW median s | NEW after NEW index build median s | NEW/OLD | OLD rows | NEW rows | rows differ | OLD store reads | NEW store reads | OLD peak RSS MB | NEW peak RSS MB |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]

    def reads(st):
        """Object-store GETs and HEADs of the invocation (`--stats`), manifest
        plane + table plane."""
        if not st or "storage" not in st:
            return "—"
        tot = sum(plane.get("get", 0) + plane.get("head", 0) for plane in st["storage"].values() if isinstance(plane, dict))
        return str(int(tot))

    def rss(st):
        if not st:
            return "—"
        for k in ("peak_rss_bytes", "peak_rss"):
            if k in st:
                return f"{st[k] / 1e6:.0f}"
        return "—"
    for r in tm:
        if r["kind"] != "search_timing":
            continue
        s = r["summary"]
        o, n, ni = s["old"], s["new"], s["new_after_new_index"]
        ratio = (n["median_secs"] / o["median_secs"]) if o["median_secs"] and n["median_secs"] else None
        t.append(f"| {r['n']} | {r['query']} | {o['median_secs'] and round(o['median_secs'], 3)} | {n['median_secs'] and round(n['median_secs'], 3)} | "
                 f"{ni['median_secs'] and round(ni['median_secs'], 3)} | {ratio and round(ratio, 1)} | {r['row_counts'].get('old')} | {r['row_counts'].get('new')} | "
                 f"{'; '.join(d[0] for d in r['diffs']['new']) or 'no'} | {reads(o['stats_sample'])} | {reads(n['stats_sample'])} | {rss(o['stats_sample'])} | {rss(n['stats_sample'])} |")
    (OUTDIR / "timing.md").write_text("\n".join(t) + "\n")
    print("graphs with results:", len(graphs), "complete:", len(done), "difference rows:", len(rows), "harness errors:", len(harness_errors))


if __name__ == "__main__":
    main()
