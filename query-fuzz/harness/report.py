"""Summarize a campaign: counts, acceptance, coverage per form, form pairs never drawn
together, and the findings grouped by signature with their class and oracle.

    report.py --data DIR --checks FILE --corpus FILE --out DIR
"""

from __future__ import annotations

import argparse
import glob
import itertools
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from classify import SANCTIONS  # noqa: E402

# The edition-2 forms the brief lists, as generator form tags (a form may need
# several tags; `any` of them counts).
FORMS = {
    "filter inside match": ["filter_in_match"],
    "filter in property braces": ["prop_braces"],
    "whitespace-separated list": ["ws_list"],
    "not { } over a pattern": ["not_pattern"],
    "not { } over filters only": ["not_filter"],
    "count block": ["count_block"],
    "exists block": ["exists_block"],
    "aggregate block": ["agg_block"],
    "contains over String": ["contains_str"],
    "contains over a list": ["contains_list"],
    "starts_with": ["starts_with"],
    "search": ["search"],
    "match_text": ["match_text"],
    "fuzzy, 2 arguments": ["fuzzy2"],
    "fuzzy, 3 arguments": ["fuzzy3"],
    "nearest in order": ["nearest_order"],
    "bm25 in order": ["bm25_order"],
    "rrf": ["rrf"],
    "ranking in return, aliased": ["rank_return_aliased"],
    "ranking in return, unaliased": ["rank_return_unaliased"],
    "ranking under an aggregate": ["rank_under_aggregate"],
    "ranking under distinct": ["rank_under_distinct"],
    "limit": ["limit"],
    "limit without order": ["limit_no_order"],
    "multi-hop": ["multi_hop"],
    "hop range": ["hop_range"],
    "reversed triple": ["reversed_triple"],
    "disconnected components": ["disconnected"],
    "triple naming a new variable": ["triple_new_var"],
    "triple naming a new variable inside a block": ["block_triple_new_var"],
    "order keys over grouped rows": ["order_grouped"],
    "grouped order key on a meta-field": ["grouped_order_meta"],
    "read named after a function": ["read_named_function"],
    "mixed keyword case": ["refuse:mixed_case_keyword"],
    "unused parameter declaration": ["unused_param"],
    "integer/float comparison": ["int_float_cmp"],
    "avg over integers": ["avg_int"],
    "integers beyond 2^53": ["big_int"],
    "aggregate in return": ["aggregate"],
    "return distinct": ["distinct"],
    "edge binding": ["edge_binding"],
    "undirected edge": ["undirected"],
    "edge alternation": ["alternation"],
    "is null": ["is_null"],
    "in over a literal list": ["in_literal_list"],
    "write: several statements": ["multi_statement"],
    "write: keyed insert": ["insert_keyed"],
    "write: keyed insert of an existing key": ["insert_keyed_existing"],
    "write: unkeyed insert": ["insert_unkeyed"],
    "write: update … set … where": ["update"],
    "write: delete … where": ["delete", "delete_edge"],
    "write: edge insert with from:/to:": ["edge_insert"],
    "write: Blob assignment": ["blob_assign"],
    "write: `mutation` keyword (edition 2 refuses it)": ["refuse:mutation_keyword"],
    "`optional` block (edition 2 refuses it)": ["refuse:optional_block"],
}
PARAM_TYPES = ["String", "Bool", "I32", "I64", "U32", "U64", "F32", "F64", "Date", "DateTime", "Vector", "Blob"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--checks", required=True)
    ap.add_argument("--corpus")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    seeds = []
    cases = 0
    accepted = 0
    by_intent = Counter()
    acc_by_intent = Counter()
    kinds = Counter()
    form_count = Counter()
    form_acc = Counter()
    pair = Counter()
    new_unchanged_ok = Counter()
    gen_versions = Counter()
    worlds_failed = 0
    tags = {name: set(t) for name, t in FORMS.items()}
    for f in sorted(glob.glob(str(Path(args.data) / "raw" / "*.jsonl")), key=lambda p: int(Path(p).stem)):
        for line in open(f):
            r = json.loads(line)
            if r["record"] == "world":
                seeds.append(r["seed"])
                gen_versions[r.get("gen_version", 1)] += 1
                if not (r["e2_setup"]["ok"] and r["new_setup"]["ok"]):
                    worlds_failed += 1
                continue
            cases += 1
            ok = (r.get("e2") or {}).get("status") == "ok"
            accepted += ok
            by_intent[r["intent"]] += 1
            acc_by_intent[r["intent"]] += ok
            kinds[r["kind"]] += 1
            if ok and (r.get("new") or {}).get("status") == "ok":
                new_unchanged_ok[r["kind"]] += 1
            fs = set(r["forms"])
            present = [name for name, t in tags.items() if fs & t]
            for name in present:
                form_count[name] += 1
                form_acc[name] += ok
            for p in fs:
                if p.startswith("param:"):
                    form_count["parameter " + p[6:]] += 1
                    form_acc["parameter " + p[6:]] += ok
            if ok:
                for a, b in itertools.combinations(sorted(present), 2):
                    pair[(a, b)] += 1

    # findings by signature
    groups = defaultdict(lambda: {"count": 0, "cases": set(), "example": None, "oracles": set()})
    for line in open(args.checks):
        c = json.loads(line)
        if c["sanction"] == "e2-refused-too":
            key = ("e2-refused-too", c["signature"].split(":")[0])
        else:
            key = (c["sanction"] or "UNATTRIBUTED", c["signature"])
        g = groups[key]
        g["count"] += 1
        g["cases"].add((c["seed"], c["qi"]))
        g["oracles"].add(c["oracle"])
        if g["example"] is None:
            g["example"] = (c["seed"], c["qi"], c["detail"][:200])
    by_sanction = defaultdict(lambda: {"count": 0, "cases": set(), "signatures": Counter(), "oracles": set(), "example": None})
    for (sanction, sig), g in groups.items():
        b = by_sanction[sanction]
        b["count"] += g["count"]
        b["cases"] |= g["cases"]
        b["signatures"][sig] += g["count"]
        b["oracles"] |= g["oracles"]
        b["example"] = b["example"] or g["example"]

    lines = ["| class | sanction or cause | oracle | checks | cases | signatures | example (seed, case) |", "|---|---|---|---|---|---|---|"]
    order = {"c": 0, "b": 1, "a": 2}
    rows = []
    for sanction, b in by_sanction.items():
        cls = SANCTIONS.get(sanction, ("?", ""))[0] if sanction not in ("e2-refused-too", "UNATTRIBUTED") else ("-" if sanction == "e2-refused-too" else "?")
        sigs = "; ".join(f"{s} ({n})" for s, n in b["signatures"].most_common(4))
        rows.append((order.get(cls, 3), -len(b["cases"]), f"| {cls} | `{sanction}` | {','.join(map(str, sorted(b['oracles'])))} | {b['count']} | {len(b['cases'])} | {sigs} | {b['example'][0]}, {b['example'][1]} |"))
    lines += [r[2] for r in sorted(rows)]
    (out / "findings-by-cause.md").write_text("\n".join(lines) + "\n")

    # coverage
    cov = ["| form | cases | EDITION-2 accepted |", "|---|---|---|"]
    for name in list(FORMS) + ["parameter " + t for t in PARAM_TYPES]:
        cov.append(f"| {name} | {form_count[name]} | {form_acc[name]} |")
    never = []
    names = sorted(n for n in FORMS if not n.startswith(("write:", "`optional`", "mixed keyword")))
    read_names = [n for n in names]
    for a, b in itertools.combinations(sorted(read_names), 2):
        if pair[(a, b)] == 0:
            never.append(f"{a} + {b}")
    cov.append("")
    cov.append(f"Pairs of forms never drawn together in a case EDITION-2 accepted ({len(never)} of {len(read_names) * (len(read_names) - 1) // 2}):")
    cov.append("")
    cov += [f"- {p}" for p in never]
    (out / "coverage.md").write_text("\n".join(cov) + "\n")

    summary = {
        "worlds": len(seeds), "worlds_not_set_up": worlds_failed,
        "seed_range": [min(seeds), max(seeds)] if seeds else None,
        "seed_ranges": ranges(seeds),
        "cases": cases, "edition_2_accepted": accepted,
        "by_intent": dict(by_intent), "edition_2_accepted_by_intent": dict(acc_by_intent),
        "kinds": dict(kinds), "new_compiles_unchanged_and_runs": dict(new_unchanged_ok),
        "gen_versions": dict(gen_versions), "pairs_never_drawn": len(never),
    }
    if args.corpus:
        summary["corpus"] = corpus_summary(args.corpus)
    (out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps(summary, indent=1))


def ranges(seeds):
    seeds = sorted(seeds)
    out = []
    for s in seeds:
        if out and s == out[-1][1] + 1:
            out[-1][1] = s
        else:
            out.append([s, s])
    return [f"{a}..={b}" for a, b in out]


def corpus_summary(path):
    c = Counter()
    unfixed = Counter()
    for line in open(path):
        r = json.loads(line)
        e2ok = r["e2"]["ok"]
        c["steps"] += 1
        c["edition_2_accepts"] += e2ok
        if e2ok:
            c["new_compiles_unchanged"] += r["new"]["ok"]
            for d in r["new"]["diagnostics"]:
                if not d["position"]:
                    c["refusal_without_position"] += 1
                if not d["fix"] and not d["edits"]:
                    c[f"refusal_without_fix:{d['code']}"] += 1
            st = r["fix"]["status"]
            c[f"fix:{st}"] += 1
            if st == "unfixed":
                rv = (r.get("fix_review") or {}).get("status")
                codes = ",".join(sorted({u.get("code") for u in r["fix"]["unfixed"] or []}))
                unfixed[f"{codes}{' (fixed with --review-fixes)' if rv == 'fixed' else ''}"] += 1
            if r.get("closure"):
                c["closure_" + ("changed" if r["closure"]["changed"] else r["closure"]["status"])] += 1
            if r.get("fixed_lint") and not r["fixed_lint"]["ok"]:
                c["fix_writes_refused_source"] += 1
            if r["fix"].get("panic"):
                c["lint_panic"] += 1
    d = dict(c)
    d["unfixed_codes"] = dict(unfixed)
    return d


if __name__ == "__main__":
    main()
