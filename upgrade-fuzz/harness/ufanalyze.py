"""Aggregate the fuzz records into upgrade-fuzz/results/.

Reads `$UF_WORK/results/cases.jsonl` (Part 1 and 2), `alternate.jsonl`
(Part 3, alternating writers) and `mixed.jsonl` (Part 3, concurrent
processes and kill -9); writes:

* `summary.md`: cases per part, seeds, statuses, time, invocations;
* `coverage.md`: the schema index mix, the index states, the NEW step kinds
  and schema sub-kinds and their outcomes, oracle counts, and the
  combinations never drawn;
* `findings.md` and `findings.json`: every finding signature with its
  oracle, cases and an example;
* `rollback.md`: for each NEW step kind and GQ 2 binary, what the binary
  did on the graph afterwards;
* `mixed.md`: the concurrent and kill -9 runs.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import ufcommon as C

OUT = Path(__file__).resolve().parents[1] / "results"
RES = C.WORK / "results"


def load(name):
    p = RES / name
    if not p.exists():
        return []
    out = []
    for line in p.open():
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def dedupe(recs):
    by = {}
    for r in recs:
        by[r["seed"]] = r
    return [by[k] for k in sorted(by)]


def schema_mix(schema: str) -> Counter:
    """Count the index-mix features of one edition-2 schema text."""
    c = Counter()
    types = re.findall(r"node (\w+) \{(.*?)\n\}", schema, re.S)
    edges = re.findall(r"edge (\w+): .*?\{(.*?)\n\}", schema, re.S)
    c["node types"] += len(types)
    c["edge types"] += len(edges)
    c[f"cases with {len(types)} node types"] += 1
    c[f"cases with {len(edges)} edge types"] += 1
    for name, body in types:
        lines = [l.strip() for l in body.splitlines() if l.strip()]
        idx = 0
        text_idx = 0
        if any(l.startswith("@key(") for l in lines):
            c["type: compound @key"] += 1
        elif any("@key" in l for l in lines):
            c["type: single @key"] += 1
        else:
            c["type: no @key"] += 1
        for l in lines:
            if l.startswith("@index("):
                c["body-level @index(list)"] += 1
                idx += 1
                continue
            if l.startswith("@range"):
                c["@range"] += 1
                continue
            if l.startswith("@"):
                continue
            m = re.match(r"(\w+): (\S+(?:\(.*?\))?\??)(.*)", l)
            if not m:
                continue
            prop, ty, ann = m.groups()
            base = ty.rstrip("?")
            nullable = ty.endswith("?")
            kind = ("String" if base == "String" else "enum" if base.startswith("enum") else
                    "list" if base.startswith("[") else "Vector" if base.startswith("Vector") else base)
            c[f"property {kind}{'?' if nullable else ''}"] += 1
            if "@unique" in ann:
                c["@unique"] += 1
            if base == "String" and "@key" in ann:
                c["@key String with @index" if "@index" in ann else "@key String without @index"] += 1
            if "@index" in ann:
                idx += 1
                if base == "String" and "@key" not in ann:
                    if "fulltext" in ann:
                        c["@index(fulltext) (GQ 2 refuses)"] += 1
                    else:
                        c["plain @index on a String (edition 2 full text)"] += 1
                        text_idx += 1
                elif base in ("I32", "I64", "U32", "U64", "F32", "F64"):
                    c["@index on a numeric"] += 1
                elif base.startswith("enum"):
                    c["@index on an enum"] += 1
                elif base == "Bool":
                    c["@index on a Bool"] += 1
                elif base in ("Date", "DateTime"):
                    c["@index on a Date/DateTime"] += 1
                elif base.startswith("["):
                    c["property-level @index on a list (GQ 2 refuses)"] += 1
                elif base.startswith("Vector"):
                    c["@index on a Vector"] += 1
            if base.startswith("Vector"):
                c["Vector with a distance (GQ 2 refuses)" if "distance" in base else "Vector without a distance"] += 1
            if base == "Blob":
                c["managed Blob" + ("?" if nullable else "")] += 1
        if text_idx >= 2:
            c["type with several full-text fields"] += 1
        if idx == 0:
            c["type with no index"] += 1
    for name, body in edges:
        if "@key(@src, @dst)" in body:
            c["edge @key(@src, @dst)"] += 1
        if "@unique(@src, @dst)" in body:
            c["edge @unique(@src, @dst)"] += 1
    c["edge @card"] += len(re.findall(r"@card\(", schema))
    return c


def table(rows, head):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in rows:
        out.append("| " + " | ".join(str(x).replace("|", "\\|").replace("\n", " ") for x in r) + " |")
    return "\n".join(out)


# Final classification of every finding signature, read by hand (README, Findings).
# (kind, signature regex) -> (finding id, class, note)
CLASS = [
    ("index_state_changed_answer", r"^(near|rrf)", "F1", "c (pre-existing)", "nearest returns fewer than k rows after a vector index build, a delete and optimize"),
    ("fresh_graph_differs", r"^(near|rrf)", "F1", "c (pre-existing)", "the same, against a graph rebuilt from the export"),
    ("fresh_graph_differs", r"^rng", "F6", "c (pre-existing, minor)", "near query_memory_bytes, an index build or optimize turns an answer into a resource refusal"),
    ("one_way_listing_noop", r"", "F5", "c (doc, minor)", "a no-step schema plan lists none of the stored one-way constructs"),
    ("integrity", r"uncoded", "F3", "c (pre-existing)", "maintenance commands refuse in plain text under --json (here GQ 2's index build, optimize, cleanup)"),
    ("gq2_new_difference", r"^nearest rows different-identities", "—", "a", "results.index-state: GQ 2's nearest returned fewer than k rows (an exact prefix of NEW's)"),
    ("gq2_new_difference", r"^nearest rows same-identities-same-order", "—", "a", "results.limit-tie-rule: a distance tie at the limit boundary"),
    ("gq2_write_refused", r"", "—", "harness", "the probe drew an empty insert (fixed in the harness at seed ~1460)"),
]


def classify(kind, sig):
    for k, rx, fid, cls, note in CLASS:
        if k == kind and re.search(rx, sig):
            return fid, cls, note
    return "?", "unclassified", ""


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    cases = dedupe(load("cases.jsonl"))
    alt = dedupe(load("alternate.jsonl"))
    stress = dedupe(load("stress.jsonl"))
    mixed = load("mixed.jsonl")
    parts = {"Part 1+2 (random configurations)": cases, "Part 1+2 stress (100,000 rows)": stress,
             "Part 3 alternating writers": alt}

    # ---- summary
    lines = ["# Summary", ""]
    rows = []
    for name, rs in parts.items():
        if not rs:
            continue
        st = Counter(r["status"] for r in rs)
        seeds = sorted(r["seed"] for r in rs)
        inv = Counter()
        for r in rs:
            inv.update(r.get("invocations") or {})
        rows.append([name, len(rs), f"{seeds[0]}–{seeds[-1]}", dict(st), round(sum(r.get("secs") or 0 for r in rs) / 3600, 2),
                     sum(1 for r in rs if r.get("truncated")), dict(inv),
                     Counter(r.get("builder") for r in rs).most_common()])
    lines.append(table(rows, ["part", "cases", "seeds", "status", "case-hours", "truncated", "invocations", "builder"]))
    lines.append("")
    lines.append(f"Part 3 concurrent runs: {sum(1 for m in mixed if m['mode'] == 'concurrent')}; kill -9 runs: "
                 f"{sum(1 for m in mixed if m['mode'] == 'kill')}.")
    (OUT / "summary.md").write_text("\n".join(lines) + "\n")

    # ---- coverage
    cov = Counter()
    mix = Counter()
    for r in cases + stress:
        cov.update(r.get("coverage") or {})
        mix.update(schema_mix(r.get("schema") or ""))
    altcov = Counter()
    for r in alt:
        altcov.update(r.get("coverage") or {})
    lines = ["# Coverage", "", "## Schema index mix (Part 1 draws)", "",
             table(sorted(mix.items()), ["feature", "count (types or properties)"]), ""]
    lines += ["## Index states", "",
              "Per lexical field type, the state of GQ 2's full-text index at the start of Part 2 (`start_state`) and at each "
              "observation (`obs_state`): `never_built`, `complete:<side>` (GQ 2's first index pass, nothing written after), "
              "`rebuilt:<side>` (GQ 2's `index rebuild --kind full-text`), `uncovered_tail` (rows appended after), `lagging` "
              "(rows updated after), `deletions`, `rebuilt_by_new` (NEW's full-text rebuild, which GQ 2 refuses). NEW's own "
              "`index build` never refreshes GQ 2's full-text index, so it does not change the label.", "",
              table(sorted((k, v) for k, v in cov.items() if k.startswith(("start_state", "obs_state"))), ["state", "count"]), ""]
    lines += ["## NEW step kinds", "", table(sorted((k, v) for k, v in cov.items() if k.startswith(("new_step:", "new_schema"))),
                                                ["step", "count"]), ""]
    lines += ["## GQ 2 operations in Part 1", "", table(sorted((k, v) for k, v in cov.items() if k.startswith("gq2_op")),
                                                         ["operation:outcome", "count"]), ""]
    lines += ["## Oracles", "", table(sorted((k, v) for k, v in cov.items() if k.startswith(
        ("oracle", "rollback", "probe", "mirror", "index_event", "fixed_left", "one_way_unlisted", "index_build_advised"))),
        ["check", "count"]), ""]
    lines += ["## Part 3 alternating writers", "", table(sorted((k, v) for k, v in altcov.items() if k.startswith(
        ("mixed_gq2_step", "new_step:", "oracle", "rollback"))), ["check", "count"]), ""]
    # combinations never drawn: NEW step kind x starting index state
    combo = Counter()
    for r in cases:
        starts = {k.split(":", 1)[1].split(":")[0] for k in (r.get("coverage") or {}) if k.startswith("start_state:")}
        kinds = {k.split(":", 1)[1] for k in (r.get("coverage") or {}) if k.startswith("new_step:")}
        subs = {k.split(":", 1)[1] for k in (r.get("coverage") or {}) if k.startswith("new_schema:")}
        for s in starts:
            for k in kinds | {f"schema:{x}" for x in subs}:
                combo[(k, s)] += 1
    states = sorted({s for _, s in combo})
    kinds = sorted({k for k, _ in combo})
    lines += ["## NEW step kind × starting index state (cases)", "",
              table([[k] + [combo.get((k, s), 0) for s in states] for k in kinds], ["step"] + states), ""]
    never = [f"{k} × {s}" for k in kinds for s in states if not combo.get((k, s))]
    lines += ["Never drawn: " + (", ".join(never) if never else "none") + ".", ""]
    (OUT / "coverage.md").write_text("\n".join(lines) + "\n")

    # ---- findings
    groups = defaultdict(lambda: {"cases": set(), "n": 0, "example": None, "oracle": None})
    for part, rs in parts.items():
        for r in rs:
            for f in r.get("findings") or []:
                sig = re.sub(r"seed \d+", "", f["sig"])
                key = (part, f["kind"], sig[:120])
                g = groups[key]
                g["cases"].add(r["seed"])
                g["n"] += 1
                g["oracle"] = f["oracle"]
                if g["example"] is None:
                    g["example"] = {"seed": r["seed"], "where": f["where"], "detail": f["detail"]}
    rows = []
    js = []
    for (part, kind, sig), g in sorted(groups.items(), key=lambda kv: (-len(kv[1]["cases"]), kv[0])):
        ex = g["example"]
        fid, cls, note = classify(kind, sig)
        rows.append([fid, cls, part, g["oracle"], kind, sig, len(g["cases"]), g["n"],
                     ", ".join(map(str, sorted(g["cases"])[:8])) + (" …" if len(g["cases"]) > 8 else ""),
                     f"{ex['where']}", note])
        js.append({"id": fid, "class": cls, "note": note, "part": part, "kind": kind, "sig": sig, "oracle": g["oracle"],
                   "cases": sorted(g["cases"]), "occurrences": g["n"], "example": ex})
    (OUT / "findings.md").write_text(
        "# Findings by signature\n\nEvery signature a fuzz case reported, with the finding it belongs to (see the "
        "README) and its class. Cases are seeds; `where` is the step of the first example.\n\n" + table(
            rows, ["finding", "class", "part", "oracle", "kind", "signature", "cases", "occurrences", "seeds", "where",
                   "reading"]) + "\n")
    (OUT / "findings.json").write_text(json.dumps(js, indent=1, default=str, ensure_ascii=False))

    # ---- rollback
    rb = defaultdict(Counter)
    restored = Counter()
    trans = defaultdict(Counter)
    for r in cases + alt:
        prev = {}
        for row in r.get("rollback") or []:
            step, kind, sub, side, status, why = row[:6]
            k = kind if kind != "schema" else f"schema:{sub}"
            rb[(k, side)][status + (f" ({why})" if why else "")] += 1
            if len(row) > 6:
                restored[(k, side, row[6])] += 1
            p = prev.get(side)
            if p is not None and p != status:
                trans[(k, side)][f"{p} -> {status}"] += 1
            prev[side] = status
    rows = []
    for (k, side), c in sorted(rb.items()):
        rows.append([k, side, sum(c.values()), ", ".join(f"{s}: {n}" for s, n in c.most_common()),
                     ", ".join(f"{s}: {n}" for s, n in trans[(k, side)].most_common()) or "—",
                     ", ".join(f"{x}: {n}" for (kk, ss, x), n in restored.items() if kk == k and ss == side) or "—"])
    (OUT / "rollback.md").write_text(
        "# Rollback per NEW step kind and GQ 2 binary\n\n"
        "After every NEW step each GQ 2 binary opened the graph (`snapshot`), ran the edition-2 read set, exported it, "
        "wrote to a copy, and on another copy ran its own `index build` and `index rebuild --kind full-text`. Status after "
        "the step: `ok`; `fts_refused` (lexical reads refused with `full_text_index_rebuild_required`, everything else "
        "answers); `whole_refused` with its cause (`one_way`: the stored schema holds a construct `schema plan` listed; "
        "`stamp`: the graph is at a storage format the binary does not serve). Transitions count the status change from the "
        "previous observation of the same case. `restored` counts GQ 2's own full-text rebuild bringing its lexical reads "
        "back.\n\n" + table(rows, ["NEW step", "GQ 2", "observations", "status after", "transitions", "GQ 2's rebuild"]) + "\n")

    # ---- mixed
    rows = []
    for m in mixed:
        if m["mode"] == "concurrent":
            fin = m["final"]
            rows.append([m["pair"] + (" + maintenance" if m.get("maint") else ""), m["gq2"], m["secs"], m["writes"], m["reads"], m["errors"] or "—",
                         len(m["panics"]), len(m["uncoded"]), len(m["nonmonotonic"]),
                         {k: (v["n_missing"], v["acked"]) for k, v in fin.items()}, m["exports_equal"]])
    lines = ["# Part 3: mixed binaries", "", "## Concurrent processes on a 20,000-row graph", "",
             table(rows, ["pair", "GQ 2", "secs", "writes", "reads", "errors", "panics", "uncoded", "count went down",
                          "acked missing / acked per binary", "exports equal"]), ""]
    rows = []
    for m in mixed:
        if m["mode"] == "kill":
            a = m["after_kill"]
            rows.append([m["op"], m["seed"], m.get("gq2"), "killed" if m["killed"] else "finished first",
                         f"{m['delay']} / {m.get('op_duration', '?')}",
                         " ".join(f"{k}:{'open' if v['snapshot'] else 'refused'}{'' if v['same_content'] else '(content?)'}"
                                  + ("" if v["lexical"] is None else f",lex={'ok' if v['lexical'][0] else 'refused'}")
                                  for k, v in a.items()),
                         m.get("reads_after_kill", m["new_reads_same_after_kill"]), m["rerun"]["ok"],
                         m["new_reads_same_after_rerun"], m["content_same_after_rerun"], m["cleanup_after"]["ok"]])
    lines += ["## kill -9 of NEW mid-operation", "",
              table(rows, ["operation", "seed", "built by", "kill", "delay / duration s", "after the kill",
                           "NEW's answers", "rerun ok", "answers after rerun", "content kept", "cleanup ok"]), ""]
    (OUT / "mixed.md").write_text("\n".join(lines) + "\n")
    print("wrote", sorted(p.name for p in OUT.iterdir()))


if __name__ == "__main__":
    main()
