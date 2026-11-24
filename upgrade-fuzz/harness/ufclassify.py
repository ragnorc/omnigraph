"""Oracle 3: is a GQ 2/NEW difference one the matrix already sanctions?

A difference is reduced to the matrix's signature (`analyze.read_signature`:
the p22 aspects that differ, plus whether the rows hold the same identities
in the same order or the same values within F32 precision, plus both error
codes) and classified the matrix's way:

1. `analyze.classify_read` on the catalogue entry the fuzz read shares
   (`R.catalog_twin`): its explicit rules (score type `F32` -> `F64`, the
   `_score`/`_distance` renames, `T38` lifted, ...);
2. the matrix's index-state rule: a search difference seen only where
   edition 2's full-text index did not cover every row is
   `results.index-state` (class a);
3. a signature listed for that catalogue entry in
   `migration-matrix/results/differences.json` (class a there);
4. otherwise, for a lexical read on a fully indexed field, the reference
   analyzers (`ufanalyzer.py`): when edition 2's answer is what edition 2's
   analysis gives and NEW's is what the field's profile gives, the
   difference is the listed `results.full-text-analysis` (class a);
5. anything else is a new signature: a finding, classified by hand.
"""

from __future__ import annotations

import json
from functools import lru_cache

import ufcommon as C  # noqa: F401  (sets up the matrix path)
from ufcommon import lib


@lru_cache(maxsize=1)
def _analyze():
    import analyze  # the matrix's classifier
    return analyze


@lru_cache(maxsize=1)
def table() -> set:
    d = json.loads((C.MM.parent / "results" / "differences.json").read_text())
    out = set()
    for x in d:
        if x["kind"] == "read" and x["class"] == "a":
            out.add((x["key"][1], x["key"][4]))
    return out


def observation(d: dict):
    o = lib.p22().Observation()
    o.columns, o.types, o.rows, o.ordered, o.error = d.get("columns"), d.get("types"), d.get("rows"), d.get("ordered"), d.get("error")
    return o


def _sig6(v):
    if isinstance(v, float):
        return float(f"{v:.6g}")
    if isinstance(v, list):
        return [_sig6(x) for x in v]
    if isinstance(v, dict):
        return {k: _sig6(x) for k, x in sorted(v.items())}
    return v


def refined(a: dict, b: dict) -> dict:
    from collections import Counter
    if a.get("rows") is None or b.get("rows") is None:
        return {}
    ra = [tuple(json.dumps(_sig6(v), sort_keys=True) for v in r.values()) for r in a["rows"]]
    rb = [tuple(json.dumps(_sig6(v), sort_keys=True) for v in r.values()) for r in b["rows"]]
    ka = [x[0] if x else None for x in ra]
    kb = [x[0] if x else None for x in rb]
    ordered = bool(a.get("ordered") and b.get("ordered"))
    return {"values_equal": (ra == rb) if ordered else Counter(ra) == Counter(rb),
            "keys_equal": (ka == kb) if ordered else Counter(ka) == Counter(kb),
            "keys_equal_unordered": Counter(ka) == Counter(kb)}


def signature(old: dict, new: dict) -> tuple | None:
    diffs = lib.p22().differences(observation(old), observation(new))
    if not diffs:
        return None
    rec = {"results": {"x": {"diffs": diffs, "refined": refined(old, new), "new": new}}, "old": old}
    return _analyze().read_signature(rec, "x")


def classify(rd: dict, old: dict, new: dict, state: str, cfg, export_rows=None) -> dict:
    sig = signature(old, new)
    if sig is None:
        return {"class": "same", "sig": None}
    twin = rd["kind"]
    twin_name = {"scan": "q_scalars_all", "count": "q_count", "traverse": "q_traverse",
                 "lex_any": "s_search_title", "fuzzy": "s_fuzzy_one", "bm25": "s_bm25_title",
                 "nearest": "s_nearest", "rrf": "s_rrf", "range": "q_range", "equality": "q_brace_enum",
                 "contains": "q_contains_str", "starts_with": "q_starts_with", "list_in": "q_contains_list"}.get(twin, twin)
    a = _analyze()
    cls, why = a.classify_read(twin_name, sig, "read")
    lexical = rd["kind"] in ("lex_any", "fuzzy", "bm25", "rrf")
    full = state.split(":")[0] in ("complete", "rebuilt")
    sigs = f"{rd['kind']} {' '.join(map(str, sig))} [{'full' if full else state.split(':')[0]}]"
    if cls == "a":
        return {"class": "a", "sig": sigs, "why": why}
    if (old.get("code") == "full_text_index_rebuild_required" and not new.get("error")):
        return {"class": "a", "sig": sigs, "why": "reverting.fts-index (oracle 4 checks the step and GQ 2's own rebuild)"}
    if rd["kind"] == "rrf" and "error" not in sig:
        return {"class": "a", "sig": sigs, "why": "results.fusion-window + results.full-text-analysis: an arm's ranks moved (the bm25 arm ranks by bm25_v1 over the profile's analysis)"}
    if lexical and not full:
        return {"class": "a", "sig": sigs, "why": "results.index-state (edition 2's index did not cover every row: " + state + ")"}
    for name, s in table():
        if name == twin_name and s.endswith(str(sig)):
            return {"class": "a", "sig": sigs, "why": f"listed in the matrix table ({name} {s})"}
    if lexical:
        try:
            import ufanalyzer
            verdict = ufanalyzer.explain(rd, old, new, cfg, export_rows)
        except Exception as e:  # the reference could not judge
            verdict = {"explained": False, "why": f"reference failed: {e}"}
        if verdict.get("explained"):
            return {"class": "a", "sig": sigs, "why": "results.full-text-analysis: " + verdict["why"]}
        return {"class": "new", "sig": sigs, "why": verdict.get("why", ""), "ref": verdict}
    return {"class": "new", "sig": sigs, "why": f"matrix classifier: {cls} {why}"}
