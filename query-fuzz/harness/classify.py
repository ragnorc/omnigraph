"""Judge the raw observations of a campaign against the six oracles.

Reads `<data>/raw/*.jsonl` (written by run.py) and writes
`<out>/checks.jsonl`, one line per failed check: the case's seed and index,
the oracle, a signature (what kind of difference), the sanction it was
attributed to (a key of SANCTIONS) or none, and a short detail. `report.py`
groups them.

The signature vocabulary follows scripts/cross-edition.py (aspects `columns`,
`types`, `rows`, `affected`, `error`, plus `state` for a write's resulting
graph) and the migration matrix's row kinds (`same-identities-other-order`,
`values-equal-within-f32`, `different-identities`).
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from collections import Counter
from pathlib import Path

# Sanctioned differences: the Compatibility bullets of the GQ 3 RFC (rfc/gq-3,
# docs/rfcs/2026-11-07-gq-3.md) as scripts/cross-edition.py keys them, the
# `open:` rows of crates/omnigraph-gqt/cross-edition.tsv (named nowhere a user
# reads: class b), the upgrade guide's map, and the contract's D10 (ties).
SANCTIONS = {
    # class a: a Compatibility bullet or the upgrade guide names it
    "language.diagnostic": ("a", "Compatibility, Language: an edition-2 text fails with its diagnostic"),
    "language.grouped-order-meta-field": ("a", "Compatibility, Language; guide 'Other changes': an order key over grouped rows that is a meta-field"),
    "language.accepted-block-compared": ("a", "Compatibility, Language: T40 accepted"),
    "language.accepted-unbound-triple": ("a", "Compatibility, Language: T5 accepted"),
    "language.accepted-order-key-not-returned": ("a", "Compatibility, Language: T42 accepted"),
    "language.accepted-item-beside-aggregates": ("a", "Compatibility, Language: T9/T58 accepted"),
    "language.accepted-lexical-under-not-or": ("a", "Compatibility, Language: T38 accepted"),
    "language.accepted-full-text-as-value": ("a", "Compatibility, Language: T35 accepted"),
    "language.accepted-edge-binding-whole": ("a", "Compatibility, Language: T23 edge binding returned whole"),
    "language.accepted-spelling": ("a", "Guide, Other changes: any letter case, case…end, ||, <>, single quotes"),
    "language.rrf-identical-arms": ("a", "Compatibility, Language: identical rrf arms refused with no rewrite"),
    "language.rrf-two-bindings": ("a", "Compatibility, Language: rrf over two bindings refused with no rewrite"),
    "results.index-state": ("a", "Compatibility, Results: index state"),
    "results.letter-case": ("a", "Compatibility, Results: letter case"),
    "results.full-text-analysis": ("a", "Compatibility, Results: full-text analysis (english_v1)"),
    "results.bm25-f64": ("a", "Compatibility, Results: bm25 F64"),
    "results.full-text-index-required": ("a", "Compatibility, Results: FullTextIndexRequired reads return rows"),
    "results.fusion-window": ("a", "Compatibility, Results: fusion window"),
    "results.limit-tie-rule": ("a", "Compatibility, Results: limit without order"),
    "results.integer-float-comparison": ("a", "Compatibility, Results: integer vs whole-number float literal"),
    "results.distance-rename": ("a", "Guide, Other changes: _distance rename"),
    "results.bm25-score-rename": ("a", "Compatibility, Results: _score rename"),
    "results.hop-range-self-loop": ("a", "Compatibility, Results: hop range self-loop"),
    "results.avg-exact-integer": ("a", "Compatibility, Results: avg over integers"),
    "results.mutation-where-lexical": ("a", "Compatibility, Results: lexical predicate in a write's where"),
    "writes.keyed-insert": ("a", "Compatibility, Writes: keyed insert fails with key_conflict"),
    "writes.from-to": ("a", "Compatibility, Writes: from:/to: refused"),
    "writes.edge-update": ("a", "Compatibility, Writes: edge update is new"),
    "results.rank-column-rename": ("a", "Guide, Rankings: an unaliased ranking in return is `$score as _score` (edition 2: `<binding>._rrf`)"),
    "contract.D10-ties": ("a", "Contract D10: tied sort keys come back in no stated order"),
    "language.ranking-under-aggregate-no-edit": ("a", "Guide, Rankings: a ranking under an aggregate or `return distinct`: the refusal prints the form without an edit"),
    "language.ranking-without-window-no-edit": ("a", "Guide, Rankings: the edit is written only when a `limit` sizes the `nearest` window"),
    "language.ranking-calls-differ-no-edit": ("a", "Guide, Rankings: the edit is written only when the same call appears wherever the ranking does"),
    # class c: defects
    "panic.grouped-order-key": ("c", "index out of bounds in grouped_order_key (crates/omnigraph-compiler/src/query/check/mod.rs:2772): an order key's position among the written return items indexes the checked columns, which omit a refused item"),
    "fix.filter-only-block-loops": ("c", "lint --fix never converges: Q017 writes a block's own `where`, Q014 deletes it (omnigraph-compiler)"),
    "fix.statements-join-match-group": ("c", "Q008's fix leaves the next statement in the new `match` group, so it runs once per selected row (omnigraph-compiler)"),
    "fix.rrf-explicit-k-no-edit": ("c", "T65 prints no edit for an `rrf` with an explicit third argument, `60` included, the guide's own Rankings example (omnigraph-compiler)"),
    "fix.rrf-over-a-triple-bound-binding-no-edit": ("c", "T65 prints no edit for an `rrf` ranking a binding only a triple introduces; nearest and bm25 get one (omnigraph-compiler)"),
    # class b: intended (the branch's own table or docs say so) but not in the guide or a bullet
    "fix.read-named-after-function-no-edit": ("b", "T74 (or T69 where the read calls the function it is named after) prints no edit; the guide says every older form prints its fix"),
    "fix.fuzzy-max-edits-above-2-no-edit": ("b", "RFC section 4 bounds max_edits to 0..=2; the map writes `max_edits: n` for fuzzy's third argument with no bound, and edition 2 accepted any u32"),
    "accepted.empty-list-in-assignment": ("b", "an empty list assigned to a non-String list property, refused by edition 2 (T3, typed [String]), is accepted"),
    "accepted.whole-float-into-integer": ("b", "a whole-number float literal assigned to an integer property, refused at run time by edition 2, is stored"),
    "writes.keyed-insert-repeated-in-one-write": ("b", "two keyed inserts of one key in one write: edition 2 kept the last, GQ 3 refuses with key_conflict; the mutations guide names a key staged earlier, the Compatibility bullet only an existing key"),
    "accepted.exponent-float-literal": ("b", "a float literal with an exponent (`4.6e+18`), a parse error in edition 2, is accepted"),
    "scoping.later-outer-name-in-block": ("b", "a block naming a variable that a later outer binding declares was independent in edition 2; GQ 3 correlates it (T5/T68, no fix)"),
    "open:write-groups-one-snapshot": ("b", "cross-edition.tsv OPEN: T91, groups select on one snapshot"),
    "open:empty-list-without-context": ("b", "cross-edition.tsv OPEN: empty list literal refused (T7)"),
    "open:full-text-call-as-a-value": ("b", "cross-edition.tsv OPEN: T35 lifted"),
    "open:stop-word-only-text": ("b", "RFC section 4 'Empty text': no searchable terms is a type error (migration matrix #73)"),
}

GUIDE_CODES = {  # every code the upgrade guide's respelling map, Rankings or Moved codes names
    "Q009", "Q017", "Q010", "T7", "Q016", "Q012", "T68", "T84", "T82", "T81", "T6", "Q008",
    "T11", "Q004", "T65", "T8", "T9", "T23", "T42", "T74", "T75", "T24", "T92", "T93", "Q018",
    "Q013", "T76", "T3", "T40", "T27",
}

COMPILE_CODE = re.compile(r"^[QTP]\d+$")


def is_compile(obs) -> bool:
    return obs.get("status") == "error" and bool(obs.get("code")) and bool(COMPILE_CODE.match(obs["code"]))


def canon(v):
    return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def approx(v):
    if isinstance(v, float):
        return float(f"{v:.5g}")
    if isinstance(v, list):
        return [approx(x) for x in v]
    if isinstance(v, dict):
        return {k: approx(x) for k, x in v.items()}
    return v


RENAME = re.compile(r"^[a-z][a-z0-9_]*\.(_distance|_score|_rrf)$")


def renamed_columns(old_cols, new_cols):
    """old columns under the GQ 3 rename `x._distance` -> `_distance`, `x._score` -> `_score`."""
    if not old_cols:
        return old_cols, []
    out, hits = [], []
    for c in old_cols:
        m = RENAME.match(c)
        target = "_score" if m and m.group(1) == "_rrf" else (m.group(1) if m else None)
        if m and c not in (new_cols or []) and target in (new_cols or []):
            out.append(target)
            hits.append(m.group(1))
        else:
            out.append(c)
    return out, hits


ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


def unulid(v):
    """A generated id differs between the two binaries' graphs: compare it as a placeholder."""
    if isinstance(v, str) and ULID.match(v):
        return "<generated>"
    if isinstance(v, list):
        return [unulid(x) for x in v]
    if isinstance(v, dict):
        return {k: unulid(x) for k, x in v.items()}
    return v


def rows_under(rows, mapping):
    return [{mapping.get(k, k): unulid(v) for k, v in r.items()} for r in rows or []]


def has_order(text: str) -> bool:
    return re.search(r"\border\s*\{", text) is not None


def has_limit(text: str) -> bool:
    return re.search(r"\blimit\s+\d+", text) is not None


def compare_read(case, old, new, world=None):
    """Differences between two successful reads: list of (aspect, kind, sanction)."""
    out = []
    forms = set(case["forms"])
    text = case["text"]
    oc, hits = renamed_columns(old.get("columns"), new.get("columns"))
    mapping = dict(zip(old.get("columns") or [], oc))
    orows = rows_under(old.get("rows"), mapping)
    nrows = rows_under(new.get("rows"), {})
    if oc != new.get("columns"):
        out.append(("columns", f"{old.get('columns')} -> {new.get('columns')}", None))
        return out
    if hits:
        out.append(("columns", "rename " + ",".join(sorted(set(hits))), "results.distance-rename" if "_distance" in hits else "results.rank-column-rename" if "_rrf" in hits else "results.bm25-score-rename"))
    # types (from explain), after the rename
    ot, nt = old.get("types"), new.get("types")
    if ot is not None and nt is not None:
        ot2 = [re.sub(r"^[a-z][a-z0-9_]*\.(_distance|_score|_rrf):", lambda m: ("_score" if m.group(1) == "_rrf" else m.group(1)) + ":", t) if hits else t for t in ot]
        if ot2 != nt:
            pairs = [(a, b) for a, b in zip(ot2, nt) if a != b]
            kinds = sorted({f"{a.split(': ',1)[-1]}->{b.split(': ',1)[-1]}" for a, b in pairs})
            sanction = None
            if all(re.search(r"F32\??->F64", k) for k in kinds) and ("bm25" in text or "rrf" in text):
                sanction = "results.bm25-f64"
            out.append(("types", " ".join(kinds) or f"{len(ot2)}->{len(nt)} columns", sanction))
    ordered = has_order(text)
    a = [canon(r) for r in orows]
    b = [canon(r) for r in nrows]
    if a == b:
        return out
    if Counter(a) == Counter(b):
        if ordered:
            s = order_sanction(case, orows, nrows)
            if s is None and "rrf" in set(case["forms"]):
                s = "results.fusion-window"
            out.append(("rows", "same-identities-other-order", s))
        return out
    aa = Counter(canon(approx(r)) for r in orows)
    bb = Counter(canon(approx(r)) for r in nrows)
    if aa == bb:
        sanction = None
        if "rrf" in text:
            sanction = "results.fusion-window"
        elif "bm25" in text:
            sanction = "results.bm25-f64"
        elif "avg_int" in forms:
            sanction = "results.avg-exact-integer"
        out.append(("rows", "values-equal-within-f32", sanction))
        return out
    out.append(("rows", f"different-identities {len(orows)}->{len(nrows)}", rows_sanction(case, orows, nrows, world)))
    return out


def order_sanction(case, orows, nrows):
    """Same rows, another order: a tie (D10) when every key is returned and the
    key sequences agree; a ranking's tie otherwise attributed to index state."""
    text = case["text"]
    m = re.search(r"\border\s*\{(.*?)\}\s*(limit|\}|$)", text, re.S)
    keys = []
    if m:
        for k in m.group(1).split(","):
            k = re.sub(r"\s+(asc|desc)\s*$", "", k.strip())
            keys.append(k)
    resolved = []
    for k in keys:
        m2 = re.search(re.escape(k) + r"\s+as\s+([a-z_][a-z0-9_]*)", text)
        if k.startswith("$") and "(" not in k:
            resolved.append(m2.group(1) if m2 else k.lstrip("$"))
        elif re.match(r"^[a-z_][a-z0-9_]*$", k):
            resolved.append(k)  # an alias
        else:
            resolved = None
            break
    # a null cell is absent from a CLI row: r.get gives None for it
    if resolved:
        ka = [[r.get(c) for c in resolved] for r in orows]
        kb = [[r.get(c) for c in resolved] for r in nrows]
        if ka == kb:
            return "contract.D10-ties"
        return None
    if any(x in text for x in ("bm25(", "rrf(", "nearest(")):
        return "ranking-tie?"
    return None


def self_loop_on_hop(case, world):
    """A hop range in the text over an edge type that has a self-loop in the seed data."""
    if world is None:
        return False
    edges = set()
    for m in re.finditer(r"\$\w+ (?:\$\w+:)?(?:<|\()?([a-z][A-Za-z| ]*?)(?:>|\))?\{\d+,\d*\}", case["text"]):
        for e in m.group(1).split("|"):
            edges.add(e.strip())
    for line in world.get("data", "").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        e = rec.get("edge")
        if e and (e[0].lower() + e[1:]) in edges and rec.get("from") == rec.get("to"):
            return True
    return False


def rows_sanction(case, orows, nrows, world=None):
    forms = set(case["forms"])
    text = case["text"]
    if "hop_range" in forms and self_loop_on_hop(case, world):
        return "results.hop-range-self-loop"
    if "rrf" in forms:
        return "results.fusion-window"
    if any(f in forms for f in ("search", "match_text", "fuzzy2", "fuzzy3", "bm25_order")):
        return "results.full-text-analysis"
    if ("int_float_cmp" in forms or "big_int" in forms) and re.search(r"\d\.\d", text):
        return "results.integer-float-comparison"
    if has_order(text) and has_limit(text) and len(orows) == len(nrows):
        return "contract.D10-ties"
    if "limit_no_order" in forms and len(orows) == len(nrows):
        return "results.limit-tie-rule"
    if any(f in forms for f in ("search", "match_text", "fuzzy2", "fuzzy3")):
        return "full-text?"
    if "rrf" in forms:
        return "fusion?"
    if "bm25_order" in forms:
        return "full-text?"
    if "hop_range" in forms:
        return "hop-range?"
    if "int_float_cmp" in forms or "big_int" in forms:
        return "int-float?"
    if has_order(text) and has_limit(text) and len(orows) == len(nrows):
        return "tie-at-limit?"
    return None


def norm_export(lines, known_ids):
    if not isinstance(lines, list):
        return lines
    out = []
    for line in lines:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            out.append(line)
            continue
        if "id" in rec and rec["id"] not in known_ids:
            rec["id"] = "<generated>"
        out.append(canon(rec))
    return sorted(out)


def compare_write(case, old, new, known):
    out = []
    if old["status"] != new["status"]:
        out.append(("error", f"{old['status']}:{old.get('code') or ''} -> {new['status']}:{new.get('code') or ''}", None))
    elif old["status"] == "error":
        return out
    elif old.get("affected") != new.get("affected"):
        out.append(("affected", f"{old.get('affected')} -> {new.get('affected')}", None))
    a = norm_export(old.get("export"), known)
    b = norm_export(new.get("export"), known)
    if a != b and "now_assign" in case["forms"]:
        return out  # now() reads the clock: the two runs store different instants
    if a != b:
        sa, sb = set(a or []), set(b or [])
        out.append(("state", f"-{len(sa - sb)} +{len(sb - sa)} lines", None))
    return out


def write_sanction(case, aspect, kind, old, new):
    forms = set(case["forms"])
    err = new.get("error") or ""
    keyed_conflict = new.get("code") in ("key_conflict", "conflict") and "already has id" in err
    if keyed_conflict:
        m = re.search(r"already has id '(.*?)'", err)
        if "insert_keyed_existing" in forms or (m and m.group(1) in KNOWN_IDS) or err.startswith("edge type"):
            return "writes.keyed-insert"
        if statements_join_group(case):
            return "fix.statements-join-match-group"
        if m and len(re.findall(r"\binsert\s+[A-Z]", case["text"])) > 1:
            return "writes.keyed-insert-repeated-in-one-write"
        return "conflict-without-existing-key?"
    if aspect == "error" and statements_join_group(case) and old.get("status") == "ok":
        return "fix.statements-join-match-group"
    if aspect in ("state", "affected") and re.search(r"\d\.\d", case["text"]) and ("int_float_cmp" in forms or "big_int" in forms):
        return "results.integer-float-comparison"
    if aspect in ("state", "affected") and statements_join_group(case):
        return "fix.statements-join-match-group"
    if aspect == "error" and old.get("status") == "error" and "cannot convert Float" in (old.get("error") or ""):
        return "accepted.whole-float-into-integer"
    if aspect in ("state", "affected") and "insert_keyed_existing" in forms and new["status"] == "error":
        return "writes.keyed-insert"
    if new.get("code") == "T91" or "T91" in (new.get("error") or ""):
        return "open:write-groups-one-snapshot"
    return None


KNOWN_IDS: set = set()


def statements_join_group(case) -> bool:
    """The edition-2 write has an `update … where` or `delete … where` followed
    by an `insert`: the Q008 fix opens a group with `match` for the first and
    leaves the next statement in that group, run once per selected row."""
    lines = [l.strip() for l in case["text"].splitlines()]
    seen_selecting = False
    for l in lines:
        if re.match(r"^(update|delete)\s+[A-Z]\w*\b.*\bwhere\b", l):
            seen_selecting = True
        elif seen_selecting and re.match(r"^insert\s+[A-Z]", l):
            return True
    return False


def t65_reason(case) -> str | None:
    """Why the guide says T65 prints no edit for this text, or None."""
    forms = set(case["forms"])
    text = case["text"]
    if forms & {"aggregate", "distinct", "rank_under_aggregate", "rank_under_distinct"}:
        return "language.ranking-under-aggregate-no-edit"
    if "nearest(" in text and not has_limit(text):
        return "language.ranking-without-window-no-edit"
    calls = re.findall(r"(?:nearest|bm25|rrf)\((?:[^()]|\([^()]*\))*\)", text)
    if len(set(calls)) > 1 and not any(c.startswith("rrf(") for c in calls):
        return "language.ranking-calls-differ-no-edit"
    arms = re.findall(r"rrf\((?:nearest|bm25)\((\$\w+)\.[^()]*\),\s*(?:nearest|bm25)\((\$\w+)\.", text)
    if arms and arms[0][0] != arms[0][1]:
        return "language.rrf-two-bindings"
    if re.search(r"rrf\((?:nearest|bm25)\([^()]*\),\s*(?:nearest|bm25)\([^()]*\),\s*[^)]", text):
        return "fix.rrf-explicit-k-no-edit"
    m = re.search(r"rrf\((?:nearest|bm25)\((\$\w+)\.", text)
    if m and not re.search(re.escape(m.group(1)) + r":\s*[A-Z]", text):
        return "fix.rrf-over-a-triple-bound-binding-no-edit"
    return None


def unfixed_sanction(case, codes) -> str | None:
    codes = [c for c in codes if c != "T81"] or codes
    if all(c == "T65" for c in codes):
        return t65_reason(case)
    if all(c in ("T74", "T69") for c in codes) or ("T74" in codes and all(c in ("T74", "T69", "T65") for c in codes)):
        return "fix.read-named-after-function-no-edit"
    if codes == ["T71"]:
        return "fix.fuzzy-max-edits-above-2-no-edit"
    if codes == ["T91"] or (set(codes) <= {"T91", "T76"}):
        return "open:write-groups-one-snapshot"
    if codes == ["T42"] and "grouped_order_meta" in case["forms"]:
        return "language.grouped-order-meta-field"
    if codes == ["T75"]:
        return "language.rrf-two-bindings"
    return None


def judge(case, world) -> list[dict]:
    checks = []
    e2, new = case.get("e2"), case.get("new")
    if case.get("harness_error"):
        return [mk(case, 6, "harness-error", None, case["harness_error"][:300])]
    if e2 is None or new is None:
        return checks
    kind = case["kind"]
    forms = set(case["forms"])
    known = world["known_ids"]

    # Oracle 6: integrity
    for label, obs in (("e2", e2), ("new", new), ("fixed", case.get("fixed") or {})):
        if not obs.get("panic") and "panicked" in str(obs.get("error") or ""):
            obs["panic"] = obs["error"]
        if obs.get("panic"):
            checks.append(mk(case, 6, f"panic:{label}", None, obs["panic"][-300:]))
        if obs.get("status") == "timeout":
            checks.append(mk(case, 6, f"timeout:{label}", None, ""))
        if label != "e2" and obs.get("status") == "error" and obs.get("uncoded"):
            checks.append(mk(case, 6, f"uncoded-error:{label}", None, (obs.get("error") or "")[:300]))
    if (case.get("fix") or {}).get("status") is None and "panicked" in str((case.get("fix") or {}).get("raw") or ""):
        case["fix"]["panic"] = case["fix"]["raw"]
    if case.get("fix", {}).get("panic"):
        checks.append(mk(case, 6, "panic:lint", None, case["fix"]["panic"][-300:]))
    if "new_again" in case:
        na = case["new_again"]
        if canon(na.get("rows")) != canon(new.get("rows")) and not (Counter(map(canon, na.get("rows") or [])) == Counter(map(canon, new.get("rows") or [])) and not has_order(case["text"])):
            checks.append(mk(case, 6, "nondeterministic:new", None, ""))

    e2_ok = e2["status"] == "ok"
    e2_compile_err = is_compile(e2)
    new_ok = new["status"] == "ok"
    new_compile_err = is_compile(new)

    # Oracle 1 and 5: the unchanged text
    if e2_ok and new_ok:
        diffs = compare_read(case, e2, new, world) if kind == "read" else compare_write(case, e2, new, known)
        for aspect, detail, sanction in diffs:
            if kind == "write" and sanction is None:
                sanction = write_sanction(case, aspect, detail, e2, new)
            checks.append(mk(case, 1, f"unchanged:{kind}:{aspect}:{detail_kind(detail)}", sanction, detail))
    elif e2_ok and new["status"] == "error" and not new_compile_err:
        detail = f"edition 2 ok; NEW {new.get('code')}: {(new.get('error') or '')[:200]}"
        sanction = write_sanction(case, "error", detail, e2, new) if kind == "write" else None
        checks.append(mk(case, 1, f"unchanged:{kind}:error:ok->{new.get('code')}", sanction, detail))
    elif e2_compile_err and new_ok:
        checks.append(mk(case, 5, f"e2-refused-new-accepts:{e2.get('code')}", accepted_sanction(case, e2), (e2.get("error") or "")[:200]))
    elif e2["status"] == "error" and not e2_compile_err and new_ok:
        sanction = "accepted.whole-float-into-integer" if "cannot convert Float" in (e2.get("error") or "") else None
        checks.append(mk(case, 1, f"unchanged:{kind}:error:{e2.get('code')}->ok", sanction, (e2.get("error") or "")[:200]))

    # Oracle 2: refusals carry code, position and a fix
    if new_compile_err and (e2_ok or not e2_compile_err):
        if not new.get("position"):
            checks.append(mk(case, 2, f"refusal-without-position:{new['code']}", None, (new.get("error") or "")[:200]))
        has_fix = bool(new.get("fix")) or bool((new.get("suggestion") or {}).get("edits"))
        if not has_fix and new["code"] not in NO_FIX_OK:
            checks.append(mk(case, 2, f"refusal-without-fix:{new['code']}", unfixed_sanction(case, [new["code"]]), (new.get("error") or "")[:200]))
        if new["code"] not in GUIDE_CODES:
            checks.append(mk(case, 2, f"refusal-code-not-in-guide:{new['code']}", None, (new.get("error") or "")[:200]))

    # Oracles 3 and 4: the fix
    fx = case.get("fix") or {}
    status = fx.get("status")
    review = case.get("fix_review") or {}
    if new_compile_err:
        if status == "does_not_converge":
            checks.append(mk(case, 4, "fix-does-not-converge", "fix.filter-only-block-loops" if set((fx.get("applied") or {}).keys()) >= {"Q014", "Q017"} else None, f"applied {fx.get('applied')}"))
        elif status == "unfixed":
            codes = sorted({u.get("code") for u in fx.get("unfixed") or [] if isinstance(u, dict)})
            if review.get("status") == "fixed":
                pass  # stopped at a fix for review, which --review-fixes applied
            elif not all(c in NO_FIX_OK for c in codes):
                checks.append(mk(case, 4, "fix-stops-unfixed:" + ",".join(c or "?" for c in codes), unfixed_sanction(case, codes) if e2_ok else "e2-refused-too", unfixed_detail(fx)))
        elif status == "unchanged":
            checks.append(mk(case, 4, f"fix-leaves-refused-text-unchanged:{new['code']}", None, ""))
        elif status not in ("fixed",):
            checks.append(mk(case, 4, f"fix-status:{status}", None, (fx.get("raw") or "")[:300]))
    elif new_ok and status == "fixed":
        applied = sorted((fx.get("applied") or {}).keys())
        if not all(re.match(r"^L2\d\d$", c) for c in applied):
            checks.append(mk(case, 4, "fix-rewrites-a-compiling-text:" + ",".join(applied), None, ""))
    fixed = case.get("fixed")
    if fixed:
        if is_compile(fixed):
            checks.append(mk(case, 4, f"fix-writes-refused-source:{fixed.get('code')}", None, (fixed.get("error") or "")[:200]))
        elif e2_ok:
            if fixed["status"] == "ok":
                fcase = dict(case, text=(case.get("fix_review") or {}).get("text") if case.get("fix_review", {}).get("status") == "fixed" and fx.get("status") != "fixed" else fx.get("text"))
                diffs = compare_read(dict(case), e2, fixed, world) if kind == "read" else compare_write(case, e2, fixed, known)
                for aspect, detail, sanction in diffs:
                    if kind == "write" and sanction is None:
                        sanction = write_sanction(case, aspect, detail, e2, fixed)
                    checks.append(mk(case, 3, f"fixed:{kind}:{aspect}:{detail_kind(detail)}", sanction, detail))
            else:
                detail = f"edition 2 ok; fixed text {fixed.get('code')}: {(fixed.get('error') or '')[:200]}"
                sanction = write_sanction(case, "error", detail, e2, fixed) if kind == "write" else None
                checks.append(mk(case, 3, f"fixed:{kind}:error:ok->{fixed.get('code')}", sanction, detail))
        cl = case.get("closure") or {}
        if cl.get("changed") or cl.get("status") not in ("unchanged", None):
            checks.append(mk(case, 4, f"fix-not-closed:{cl.get('status')}", None, json.dumps(cl.get("applied"))))
    return checks


NO_FIX_OK = {"T75", "T81"}  # rrf arms (no rewrite); T81 is printed for review


def unfixed_detail(fx):
    out = []
    for u in fx.get("unfixed") or []:
        if isinstance(u, dict):
            out.append(f"{u.get('code')}: {u.get('message', '')[:160]}")
    return " | ".join(out)[:400]


def accepted_sanction(case, e2):
    code = e2.get("code")
    forms = set(case["forms"])
    if code == "Q001" and re.search(r"\d\.\d+e[+-]?\d+", case["text"]):
        return "accepted.exponent-float-literal"
    if code == "T3" and re.search(r"has type \[\w+\]\?? but got \[String\]", e2.get("error") or ""):
        return "accepted.empty-list-in-assignment"
    if any(f in forms for f in ("refuse:mixed_case_keyword", "refuse:angle_ne", "refuse:single_quote", "refuse:concat_bars")):
        return "language.accepted-spelling"
    return {
        "T40": "language.accepted-block-compared",
        "T5": "language.accepted-unbound-triple",
        "T42": "language.accepted-order-key-not-returned",
        "T9": "language.accepted-item-beside-aggregates",
        "T58": "language.accepted-item-beside-aggregates",
        "T38": "language.accepted-lexical-under-not-or",
        "T35": "language.accepted-full-text-as-value",
        "T23": "language.accepted-edge-binding-whole",
        "T44": "results.mutation-where-lexical",
        "T16": "writes.edge-update",
    }.get(code)


def detail_kind(detail: str) -> str:
    d = re.sub(r"\d+", "N", detail.split(";")[0])
    d = re.sub(r"\[.*?\]", "[…]", d)
    return d[:80]


def mk(case, oracle, signature, sanction, detail):
    if sanction is None and "panicked" in str(detail):  # every panic seen is at check/mod.rs:2772 (report.py lists the locations)
        sanction = "panic.grouped-order-key"
    if sanction is None and signature.startswith("refusal-code-not-in-guide:T69"):
        sanction = "fix.read-named-after-function-no-edit"
    return {
        "seed": case["seed"], "qi": case["qi"], "kind": case["kind"], "intent": case["intent"],
        "oracle": oracle, "signature": signature, "sanction": sanction, "detail": detail,
        "forms": case["forms"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    n_cases = 0
    sig = Counter()
    with (out / "checks.jsonl").open("w") as fh:
        for f in sorted(glob.glob(str(Path(args.data) / "raw" / "*.jsonl")), key=lambda p: int(Path(p).stem)):
            world = None
            for line in open(f):
                rec = json.loads(line)
                if rec["record"] == "world":
                    world = rec
                    # ids both seeded graphs hold are stable (supplied or derived
                    # from a key); a generated id differs between the two graphs
                    sides = []
                    for side in ("e2_setup", "new_setup"):
                        ids = set()
                        for l in rec.get(side, {}).get("export") or []:
                            try:
                                ids.add(json.loads(l).get("id"))
                            except (json.JSONDecodeError, AttributeError):
                                pass
                        sides.append(ids)
                    world["known_ids"] = sides[0] & sides[1]
                    KNOWN_IDS.clear()
                    KNOWN_IDS.update(world["known_ids"])
                    continue
                n_cases += 1
                for c in judge(rec, world):
                    fh.write(json.dumps(c, ensure_ascii=False) + "\n")
                    sig[(c["oracle"], c["signature"], c["sanction"])] += 1
    print(f"cases {n_cases}")
    for (o, s, sa), v in sorted(sig.items(), key=lambda x: (-x[1])):
        print(f"{v:6} o{o} {s} [{sa}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
