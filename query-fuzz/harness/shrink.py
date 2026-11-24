"""Shrink a failing case to the smallest schema, data and query that still fail.

A case is (schema, data, query, params, kind). The shrinker greedily removes
query lines and list items, schema members (types, properties, constraints)
and data rows, keeping each removal for which the predicate still holds, until
a full pass removes nothing. Predicates (PREDICATES) re-run the binaries
through replay.Built; a predicate that needs no graph (a lint outcome) skips
the graph build.

    shrink.py --seed S --qi I --predicate NAME --out DIR
    shrink.py --case case.json --predicate NAME --out DIR

Writes DIR/schema.pg, DIR/data.jsonl, DIR/query.gq, DIR/params.json and
DIR/outcome.json (the observations on the shrunk case).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import classify  # noqa: E402
import peek  # noqa: E402
import replay  # noqa: E402
import run  # noqa: E402

DATA = "/src/qfuzz-data"


class Case:
    def __init__(self, schema, data, query, params, kind, index_build=False):
        self.schema, self.data, self.query, self.params, self.kind = schema, data, query, params, kind
        self.index_build = index_build

    def name(self):
        return run.QNAME.findall(self.query)[0]

    def to_json(self):
        return {"schema": self.schema, "data": self.data, "query": self.query, "params": self.params,
                "kind": self.kind, "index_build": self.index_build}


# --------------------------------------------------------------------------
# Running a candidate
# --------------------------------------------------------------------------

def observe(case: Case, need_graph=True, fix=True, fixed_run=True, unchanged=True):
    d = Path(tempfile.mkdtemp(prefix="qf-shrink-"))
    try:
        out = {}
        if need_graph:
            b = replay.Built(d, case.schema, case.data, case.index_build)
            if not b.ok:
                return {"setup_failed": True, "e2_setup": b.e2.get("error", "")[:300], "new_setup": b.new.get("error", "")[:300]}
            out["e2"] = b.run("e2", case.query, case.name(), case.params, case.kind, "e2")
            if unchanged:
                out["new"] = b.run("new", case.query, case.name(), case.params, case.kind, "new")
        else:
            (d / "schema.pg").write_text(case.schema)
            b = None
        if fix:
            work = d / "q"
            work.mkdir(exist_ok=True)
            fx = run.lint_fix(replay.NEW, case.query, d / "schema.pg", work, "fix", False)
            out["fix"] = fx
            final = fx
            if fx["status"] not in ("fixed", "unchanged"):
                fr = run.lint_fix(replay.NEW, case.query, d / "schema.pg", work, "fixr", True)
                out["fix_review"] = fr
                if fr["status"] == "fixed":
                    final = fr
            if fixed_run and b is not None and final["status"] == "fixed" and final["text"] != case.query:
                names = run.QNAME.findall(final["text"])
                out["fixed"] = b.run("new", final["text"], names[0] if len(names) == 1 else case.name(), case.params, case.kind, "fixed")
        return out
    finally:
        shutil.rmtree(d, ignore_errors=True)


def lint_e2(case: Case):
    """EDITION-2's own lint on the text (accepts?), without a graph."""
    d = Path(tempfile.mkdtemp(prefix="qf-e2lint-"))
    try:
        (d / "schema.pg").write_text(case.schema)
        (d / "q.gq").write_text(case.query)
        res = run.cli(replay.E2, ["lint", "--query", str(d / "q.gq"), "--schema", str(d / "schema.pg"), "--json"], d)
        doc = run.parse_json(res["stdout"]) or {}
        return doc.get("status") == "ok" and all(r.get("status") == "ok" for r in doc.get("results") or [{}])
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------
# Predicates: True while the failure persists
# --------------------------------------------------------------------------

def p_dnc(case):
    if not lint_e2(case):
        return False
    o = observe(case, need_graph=False)
    return o["fix"]["status"] == "does_not_converge"


def p_unfixed(code):
    def p(case):
        if not lint_e2(case):
            return False
        o = observe(case, need_graph=False)
        st = o.get("fix_review", o["fix"])
        codes = {u.get("code") for u in (st.get("unfixed") or []) if isinstance(u, dict)}
        return st["status"] == "unfixed" and code in codes
    return p


def p_new_panic(case):
    o = observe(case, need_graph=True, fix=False)
    n = o.get("new") or {}
    return "panicked" in str(n.get("error") or "") or bool(n.get("panic"))


def p_lint_panic(case):
    o = observe(case, need_graph=False)
    return o["fix"]["status"] is None and "panicked" in str(o["fix"].get("raw") or "")


def fixed_differs(aspect_re: str):
    def p(case):
        o = observe(case)
        e2, fixed = o.get("e2"), o.get("fixed")
        if not e2 or e2.get("status") != "ok" or not fixed:
            return False
        c = {"forms": [], "text": case.query, "kind": case.kind}
        if fixed.get("status") != "ok":
            return re.search(aspect_re, "error") is not None
        diffs = classify.compare_read(c, e2, fixed) if case.kind == "read" else classify.compare_write(c, e2, fixed, set())
        return any(re.search(aspect_re, a) and s is None for a, _, s in diffs)
    return p


def unchanged_differs(aspect_re: str):
    def p(case):
        o = observe(case, fix=False)
        e2, new = o.get("e2"), o.get("new")
        if not e2 or e2.get("status") != "ok" or not new:
            return False
        c = {"forms": [], "text": case.query, "kind": case.kind}
        if new.get("status") != "ok":
            return re.search(aspect_re, "error") is not None and not classify.is_compile(new)
        diffs = classify.compare_read(c, e2, new) if case.kind == "read" else classify.compare_write(c, e2, new, set())
        return any(re.search(aspect_re, a) and s is None for a, _, s in diffs)
    return p


def fixed_refused(case):
    o = observe(case)
    e2, fixed = o.get("e2"), o.get("fixed")
    return bool(e2) and e2.get("status") == "ok" and bool(fixed) and classify.is_compile(fixed)


def e2_refuses_new_accepts(case):
    o = observe(case, fix=False)
    return classify.is_compile(o["e2"]) and o["new"]["status"] == "ok"


def refusal_without_fix(case):
    o = observe(case, fix=False)
    n = o["new"]
    return o["e2"]["status"] == "ok" and classify.is_compile(n) and not n.get("fix") and not (n.get("suggestion") or {}).get("edits")


def new_runtime_error_where_e2_ok(case):
    o = observe(case, fix=False)
    n = o["new"]
    return o["e2"]["status"] == "ok" and n["status"] == "error" and not classify.is_compile(n)


PREDICATES = {
    "dnc": p_dnc,
    "unfixed-T65": p_unfixed("T65"),
    "unfixed-T74": p_unfixed("T74"),
    "unfixed-T71": p_unfixed("T71"),
    "unfixed-T91": p_unfixed("T91"),
    "unfixed-T69": p_unfixed("T69"),
    "unfixed-T81": p_unfixed("T81"),
    "new-panic": p_new_panic,
    "lint-panic": p_lint_panic,
    "fixed-rows": fixed_differs(r"^rows$"),
    "fixed-columns": fixed_differs(r"^columns$"),
    "fixed-types": fixed_differs(r"^types$"),
    "fixed-state": fixed_differs(r"^(state|affected|error)$"),
    "fixed-error": fixed_differs(r"^error$"),
    "fixed-refused": fixed_refused,
    "unchanged-rows": unchanged_differs(r"^rows$"),
    "unchanged-state": unchanged_differs(r"^(state|affected|error)$"),
    "unchanged-error": new_runtime_error_where_e2_ok,
    "e2-refused-new-accepts": e2_refuses_new_accepts,
    "refusal-without-fix": refusal_without_fix,
}


# --------------------------------------------------------------------------
# Reductions
# --------------------------------------------------------------------------

def query_candidates(q: str):
    lines = q.split("\n")
    # drop one line (not the declaration's first or last)
    for i in range(1, len(lines) - 1):
        s = lines[i].strip()
        if not s or s in ("}", "match {"):
            continue
        yield "\n".join(lines[:i] + lines[i + 1:])
    # drop one item of a comma- or space-separated list in braces
    for m in re.finditer(r"\{ ([^{}\n]*) \}", q):
        body = m.group(1)
        for sep in (", ", "  "):
            items = body.split(sep)
            if len(items) > 1:
                for j in range(len(items)):
                    nb = sep.join(items[:j] + items[j + 1:])
                    yield q[: m.start(1)] + nb + q[m.end(1):]
    # drop a disjunct or conjunct
    for m in re.finditer(r" (or|and) ", q):
        line_start = q.rfind("\n", 0, m.start()) + 1
        line_end = q.find("\n", m.end())
        line = q[line_start:line_end]
        rel = m.start() - line_start
        yield q[:line_start] + line[:rel] + q[line_end:]
        yield q[:line_start] + re.match(r"\s*", line).group(0) + line[rel + len(m.group(0)):] + q[line_end:]
    # unused parameters
    for m in re.finditer(r"(, )?\$arg\d+: [^,)]+", q):
        name = re.match(r"(?:, )?(\$arg\d+)", m.group(0)).group(1)
        if q.count(name) == 1:
            yield q[: m.start()] + q[m.end():]


def schema_blocks(schema: str):
    blocks, cur = [], []
    for line in schema.splitlines():
        if line.startswith(("node ", "edge ")):
            if cur:
                blocks.append(cur)
            cur = [line]
        elif cur:
            cur.append(line)
    if cur:
        blocks.append(cur)
    return blocks


def block_name(block):
    m = re.match(r"(node|edge) (\w+)", block[0])
    return m.group(1), m.group(2)


def schema_candidates(case: Case):
    blocks = schema_blocks(case.schema)
    data = case.data.splitlines()
    for i, b in enumerate(blocks):
        kind, name = block_name(b)
        if re.search(r"\b" + re.escape(name) + r"\b", case.query) or re.search(r"\b" + re.escape(name[0].lower() + name[1:]) + r"\b", case.query):
            continue
        nb = blocks[:i] + blocks[i + 1:]
        if kind == "node":
            # drop edges touching it
            nb = [x for x in nb if not (block_name(x)[0] == "edge" and re.search(r":\s*" + name + r"\s*->|->\s*" + name + r"\b", x[0]))]
            keep_edges = {block_name(x)[1] for x in nb if block_name(x)[0] == "edge"}
            nd = [l for l in data if not (f'"type": "{name}"' in l or f'"type":"{name}"' in l)]
            nd = [l for l in nd if '"edge"' not in l or any(f'"edge": "{e}"' in l or f'"edge":"{e}"' in l for e in keep_edges)]
        else:
            nd = [l for l in data if not (f'"edge": "{name}"' in l or f'"edge":"{name}"' in l)]
        yield Case("\n".join(sum(nb, [])) + "\n", "\n".join(nd) + ("\n" if nd else ""), case.query, case.params, case.kind, case.index_build)
    # drop one property or constraint line inside a block
    for i, b in enumerate(blocks):
        for j in range(1, len(b) - 1):
            line = b[j].strip()
            m = re.match(r"(\w+):", line)
            if m:
                prop = m.group(1)
                if re.search(r"\b" + prop + r"\b", case.query):
                    continue
                nb = [list(x) for x in blocks]
                nb[i] = b[:j] + b[j + 1:]
                nb[i] = [l for l in nb[i] if not re.search(r"@(key|unique|index|range)\([^)]*\b" + prop + r"\b", l)]
                nd = []
                for l in data:
                    try:
                        rec = json.loads(l)
                    except json.JSONDecodeError:
                        nd.append(l)
                        continue
                    kind, name = block_name(b)
                    if (rec.get("type") == name or rec.get("edge") == name) and prop in (rec.get("data") or {}):
                        del rec["data"][prop]
                    nd.append(json.dumps(rec, ensure_ascii=False))
                yield Case("\n".join(sum(nb, [])) + "\n", "\n".join(nd) + ("\n" if nd else ""), case.query, case.params, case.kind, case.index_build)
            elif line.startswith("@") or " @" in line:
                nb = [list(x) for x in blocks]
                if line.startswith("@"):
                    nb[i] = b[:j] + b[j + 1:]
                else:
                    nb[i] = b[:j] + [re.sub(r"\s@(unique|index)\b", "", b[j])] + b[j + 1:]
                    if nb[i] == b:
                        continue
                yield Case("\n".join(sum(nb, [])) + "\n", case.data, case.query, case.params, case.kind, case.index_build)
        if "@card(" in b[0]:
            nb = [list(x) for x in blocks]
            nb[i] = [re.sub(r" @card\([^)]*\)", "", b[0])] + b[1:]
            yield Case("\n".join(sum(nb, [])) + "\n", case.data, case.query, case.params, case.kind, case.index_build)


def data_candidates(case: Case):
    lines = [l for l in case.data.splitlines() if l.strip()]
    n = len(lines)
    chunk = max(1, n // 2)
    while chunk >= 1:
        for start in range(0, n, chunk):
            nd = lines[:start] + lines[start + chunk:]
            yield Case(case.schema, "\n".join(nd) + ("\n" if nd else ""), case.query, case.params, case.kind, case.index_build)
        if chunk == 1:
            break
        chunk //= 2


def shrink(case: Case, pred, log=print, max_tries=600):
    if not pred(case):
        raise SystemExit("the predicate does not hold on the original case")
    tries = 0
    changed = True
    while changed and tries < max_tries:
        changed = False
        for gen in (lambda c: (Case(c.schema, c.data, q, c.params, c.kind, c.index_build) for q in query_candidates(c.query)),
                    schema_candidates, data_candidates):
            progress = True
            while progress and tries < max_tries:
                progress = False
                for cand in gen(case):
                    tries += 1
                    if tries >= max_tries:
                        break
                    if pred(cand):
                        case = cand
                        progress = changed = True
                        log(f"  kept ({tries}): query {len(case.query)}B schema {len(case.schema)}B data {len(case.data.splitlines())} rows")
                        break
        if case.index_build:
            cand = Case(case.schema, case.data, case.query, case.params, case.kind, False)
            tries += 1
            if pred(cand):
                case = cand
                changed = True
    return case


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int)
    ap.add_argument("--qi", type=int)
    ap.add_argument("--case")
    ap.add_argument("--predicate", required=True, choices=sorted(PREDICATES))
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-tries", type=int, default=600)
    args = ap.parse_args()
    if args.case:
        c = json.loads(Path(args.case).read_text())
        case = Case(c["schema"], c["data"], c["query"], c.get("params", {}), c.get("kind", "read"), c.get("index_build", False))
    else:
        world, rec = peek.load_case(DATA, args.seed, args.qi)
        case = Case(world["schema"], world["data"], rec["text"], rec["params"], rec["kind"], world["index_build"])
    small = shrink(case, PREDICATES[args.predicate], max_tries=args.max_tries)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "schema.pg").write_text(small.schema)
    (out / "data.jsonl").write_text(small.data)
    (out / "query.gq").write_text(small.query)
    (out / "params.json").write_text(json.dumps(small.params) + "\n")
    (out / "case.json").write_text(json.dumps(dict(small.to_json(), predicate=args.predicate,
                                                   origin={"seed": args.seed, "qi": args.qi}), indent=1, ensure_ascii=False) + "\n")
    outcome = observe(small)
    (out / "outcome.json").write_text(json.dumps(outcome, indent=1, ensure_ascii=False) + "\n")
    print(small.schema)
    print(small.data)
    print(small.query, json.dumps(small.params))
    return 0


if __name__ == "__main__":
    sys.exit(main())
