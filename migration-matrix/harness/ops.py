"""Run NEW on every OLD-built graph, and OLD again after every NEW operation.

`prepare` (once per schema): lint each edition-2 declaration with NEW (its
refusal and printed fix), respell it mechanically from those fixes
(`fixes.respell`), and lint the hand respelling.

`run_graph` (once per graph):

* read-only phase on one copy, whose bytes are digested before and after:
  open, snapshot, schema show, commit and branch lists, every catalogue read
  (OLD edition 2 vs NEW hand respelling vs NEW mechanical respelling, by
  p22's `differences`), historical and branch reads, export;
* write phase, each operation on a fresh copy: the operation with NEW, then
  OLD's probes on the result (rollback), compared with the same probes on
  the untouched graph.

Records go to `<work>/results/<graph>.jsonl`, one JSON object per line.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

import catalog
from build import mutations_e2
from fixes import respell, lint_one, lint_diagnostics as diagnostics
from gen import BASE_PG, EVOLVED_PG, Data, write_jsonl
from lib import (WORK, og, copy_graph, tree_digest, tree_listing, arrow_observation, p22,
                 export_rows, canonical_export, append_jsonl, write_json)

PREP = WORK / "prep"


# ---------------------------------------------------------------- prepare

def schema_text(kind: str) -> str:
    return EVOLVED_PG if kind == "evolved" else BASE_PG


def prepare(kind: str, graph: Path) -> dict:
    """`graph`: an OLD-built graph of this schema (history one, no index
    pass), on scratch copies of which the mechanical respelling runs."""
    d = PREP / kind
    d.mkdir(parents=True, exist_ok=True)
    pg = d / "schema.pg"
    pg.write_text(schema_text(kind))
    out = {}
    for q in catalog.all_queries(kind == "evolved") + catalog.writes(kind):
        rec = {"name": q.name, "kind": q.kind}
        # edition-2 text under NEW: its refusal and printed fix
        e2 = lint_one(pg, q.e2, d / "lint_e2", q.name)
        j = e2["json"] or {}
        rec["e2_under_new"] = {"rc": e2["rc"], "status": j.get("status"),
                               "diagnostics": diagnostics(j), "raw": e2["raw"]}
        rec["fixed"] = respell(q.kind, graph, q.e2, d / "fix" / q.name, q.name, q.params)
        if q.e3:
            e3 = lint_one(pg, q.e3, d / "lint_e3", q.name)
            j3 = e3["json"] or {}
            rec["e3_lint"] = {"rc": e3["rc"], "status": j3.get("status"), "diagnostics": diagnostics(j3), "raw": e3["raw"]}
        for alt, text in q.e3_alt.items():
            ea = lint_one(pg, text, d / "lint_e3alt", f"{q.name}.{alt}")
            ja = ea["json"] or {}
            rec.setdefault("e3_alt_lint", {})[alt] = {"rc": ea["rc"], "status": ja.get("status"), "diagnostics": diagnostics(ja), "raw": ea["raw"]}
        out[q.name] = rec
    write_json(d / "prep.json", out)
    # query files
    qs = catalog.all_queries(kind == "evolved")
    (d / "e2.gq").write_text("\n\n".join(q.e2 for q in qs))
    (d / "e3.gq").write_text("\n\n".join(q.e3 for q in qs if q.e3))
    (d / "fixed.gq").write_text("\n\n".join(out[q.name]["fixed"]["text"] for q in qs if out[q.name]["fixed"]["compiled"]))
    for q in catalog.writes(kind):
        (d / "w").mkdir(exist_ok=True)
        (d / "w" / f"{q.name}.e2.gq").write_text(q.e2)
        if q.e3:
            (d / "w" / f"{q.name}.e3.gq").write_text(q.e3)
        for alt, text in q.e3_alt.items():
            (d / "w" / f"{q.name}.e3.{alt}.gq").write_text(text)
        if out[q.name]["fixed"]["compiled"]:
            (d / "w" / f"{q.name}.fixed.gq").write_text(out[q.name]["fixed"]["text"])
    return out


def load_prep(kind: str) -> dict:
    return json.loads((PREP / kind / "prep.json").read_text())


# ---------------------------------------------------------------- helpers

class Ctx:
    def __init__(self, gdir: Path, out: Path):
        self.gdir = gdir
        self.graph = gdir / "graph"
        self.meta = json.loads((gdir / "meta.json").read_text())
        self.kind = self.meta["schema"]
        self.prep = PREP / self.kind
        self.scratch = WORK / "scratch" / gdir.name
        if self.scratch.exists():
            shutil.rmtree(self.scratch)
        self.scratch.mkdir(parents=True)
        self.out = out
        self.home = {s: self.scratch / f"home-{s}" for s in ("old", "new")}

    def rec(self, **kw):
        kw.setdefault("graph", self.gdir.name)
        append_jsonl(self.out, kw)

    def run(self, side, *args, **kw):
        return og(side, *args, home=self.home[side], **kw)

    def copy(self, label: str) -> Path:
        return copy_graph(self.graph, self.scratch / label / "graph")


def status_of(r) -> str:
    if r.rc == -999:
        return "timeout"
    if r.panicked():
        return "panic"
    return "ok" if r.ok else "refused"


def query(ctx: Ctx, side: str, g: Path, qfile: Path, name: str, params: dict, extra=()):
    args = ["query", name, "--query", qfile, "--target", g, "--format", "arrow"]
    if params:
        args += ["--params", json.dumps(params)]
    args += list(extra)
    return ctx.run(side, *args, binary=True)


def _sig(v):
    if isinstance(v, float):
        return float(f"{v:.6g}")
    if isinstance(v, list):
        return [_sig(x) for x in v]
    if isinstance(v, dict):
        return {k: _sig(x) for k, x in sorted(v.items())}
    return v


def refined(o_old, o_new) -> dict:
    """Beyond p22's aspects: rows compared by position (a renamed column
    does not count), floats to six significant digits (an F32 score against
    its F64 value), and the first column alone (the identities returned)."""
    if o_old.rows is None or o_new.rows is None:
        return {}
    a = [tuple(json.dumps(_sig(v), sort_keys=True) for v in r.values()) for r in o_old.rows]
    b = [tuple(json.dumps(_sig(v), sort_keys=True) for v in r.values()) for r in o_new.rows]
    ka = [x[0] if x else None for x in a]
    kb = [x[0] if x else None for x in b]
    from collections import Counter
    ordered = o_old.ordered and o_new.ordered
    return {
        "values_equal": (a == b) if ordered else Counter(a) == Counter(b),
        "values_equal_unordered": Counter(a) == Counter(b),
        "keys_equal": (ka == kb) if ordered else Counter(ka) == Counter(kb),
        "keys_equal_unordered": Counter(ka) == Counter(kb),
        "old_keys": ka[:25], "new_keys": kb[:25],
    }


def obs_json(o) -> dict:
    d = o.to_json()
    if d.get("rows") is not None and len(d["rows"]) > 40:
        d["rows"] = d["rows"][:40] + [f"... {len(o.rows)} rows"]
    return d


def compare_query(ctx: Ctx, g: Path, q: catalog.Q, prep: dict, label: str, extra=(), phase="ro"):
    ordered = "order {" in q.e2
    old = query(ctx, "old", g, ctx.prep / "e2.gq", q.name, q.params, extra)
    o_old = arrow_observation(old, ordered)
    results = {}
    variants = [("e3", ctx.prep / "e3.gq")]
    if prep[q.name]["fixed"]["compiled"]:
        variants.append(("fixed", ctx.prep / "fixed.gq"))
    for tag, f in variants:
        new = query(ctx, "new", g, f, q.name, q.params, extra)
        o_new = arrow_observation(new, ordered)
        diffs = p22().differences(o_old, o_new)
        results[tag] = {"status": status_of(new), "secs": new.secs, "diffs": diffs,
                        "refined": refined(o_old, o_new) if diffs else {},
                        "new": obs_json(o_new), "panic": new.panicked()}
    ctx.rec(phase=phase, op=f"{label}:{q.name}", kind="compare", query=q.name, qkind=q.kind,
            rfc=q.rfc, old_status=status_of(old), old_secs=old.secs, old=obs_json(o_old), results=results)
    return o_old, results


def exports_equal(ctx: Ctx, g: Path, branch=None) -> dict:
    ro, rows_o = export_rows("old", g, branch)
    rn, rows_n = export_rows("new", g, branch)
    co, cn = canonical_export(rows_o, False), canonical_export(rows_n, False)
    diff = sorted(set(co) ^ set(cn))
    return {"old": status_of(ro), "new": status_of(rn), "old_msg": None if ro.ok else ro.message(),
            "new_msg": None if rn.ok else rn.message(), "equal": co == cn, "old_rows": len(co),
            "new_rows": len(cn), "sample_diff": diff[:6]}


# ---------------------------------------------------------------- probes

PROBE_READS = ["q_scalars_all", "q_traverse", "s_search_title", "s_bm25_title", "s_nearest", "s_rrf"]


def probes(ctx: Ctx, g: Path, label: str) -> dict:
    """OLD's view of a graph: open, reads, export, a write, an index pass."""
    out = {}
    r = ctx.run("old", "snapshot", "--target", g, "--json")
    out["snapshot"] = {"status": status_of(r), "msg": None if r.ok else r.message()[-600:]}
    qs = {q.name: q for q in catalog.READS + catalog.SEARCHES}
    for name in PROBE_READS:
        q = qs[name]
        rr = query(ctx, "old", g, ctx.prep / "e2.gq", name, q.params)
        o = arrow_observation(rr, "order {" in q.e2)
        out[f"read:{name}"] = {"status": status_of(rr), "msg": None if rr.ok else rr.message()[-600:],
                              "rows": len(o.rows or []), "digest": hash(json.dumps(o.rows, sort_keys=True, default=str))}
    ro, rows = export_rows("old", g)
    out["export"] = {"status": status_of(ro), "msg": None if ro.ok else ro.message()[-600:], "rows": len(rows)}
    ctx_q = ctx.scratch / "probe.gq"
    ctx_q.write_text(mutations_e2(ctx.kind == "evolved"))
    r = ctx.run("old", "mutate", "h_person", "--query", ctx_q, "--target", g, "--json",
                "--params", json.dumps({"e": f"probe-{label.replace(':', '-')}@example.com", "nm": "probe"}))
    out["write"] = {"status": status_of(r), "msg": None if r.ok else r.message()[-600:]}
    r = ctx.run("old", "index", "build", "--target", g, "--json")
    out["index_build"] = {"status": status_of(r), "msg": None if r.ok else r.message()[-600:]}
    r = ctx.run("old", "optimize", "--target", g, "--json")
    out["optimize"] = {"status": status_of(r), "msg": None if r.ok else r.message()[-600:]}
    if any(v["status"] != "ok" for k, v in out.items() if k.startswith("read:s_")):
        # can OLD recover by rebuilding the full-text indexes itself?
        r = ctx.run("old", "index", "rebuild", "--kind", "full-text", "--target", g, "--json")
        out["recover:fts_rebuild"] = {"status": status_of(r), "msg": None if r.ok else r.message()[-600:]}
        for name in ("s_search_title", "s_bm25_title"):
            q = qs[name]
            rr = query(ctx, "old", g, ctx.prep / "e2.gq", name, q.params)
            out[f"recover:read:{name}"] = {"status": status_of(rr), "msg": None if rr.ok else rr.message()[-600:]}
    return out


def rollback(ctx: Ctx, g: Path, op: str, baseline: dict, before_digest: str):
    after = tree_digest(g)
    if after == before_digest:
        ctx.rec(phase="rollback", op=op, kind="rollback", changed=False)
        return
    p = probes(ctx, g, op)
    regress = {k: v for k, v in p.items() if v["status"] != "ok" and baseline.get(k, {}).get("status") == "ok"}
    ctx.rec(phase="rollback", op=op, kind="rollback", changed=True, probes=p, regressions=regress)


def new_op(ctx: Ctx, op: str, fn, baseline: dict, probe: bool = True, capture: bool = False):
    """Run `fn(graph) -> Run|dict` with NEW on a fresh copy, record it, then
    OLD's probes on the result. With `capture`, NEW's export of the result
    is taken before the probes write to it and returned as the third value."""
    g = ctx.copy(op.replace(":", "_").replace("/", "_"))
    captured = None
    before = tree_digest(g)
    t0 = time.perf_counter()
    try:
        res = fn(g)
    except Exception as e:  # harness error, recorded
        ctx.rec(phase="write", op=op, kind="harness_error", error=repr(e)[-1500:])
        return (None, g, None) if capture else (None, g)
    runs = res if isinstance(res, list) else [res]
    detail = []
    for r in runs:
        if hasattr(r, "rc"):
            detail.append({"args": [str(a) for a in r.args][:8], "status": status_of(r), "rc": r.rc,
                           "secs": round(r.secs, 3), "msg": None if r.ok else r.message()[-1500:],
                           "out": r.out[-1500:] if r.ok else None, "panic": r.panicked()})
        else:
            detail.append(r)
    ctx.rec(phase="write", op=op, kind="new_op", runs=detail, secs=time.perf_counter() - t0)
    if capture:
        captured = canonical_export(export_rows("new", g)[1])
    if probe:
        rollback(ctx, g, op, baseline, before)
    return (runs, g, captured) if capture else (runs, g)


# ---------------------------------------------------------------- loads

def load_files(ctx: Ctx) -> dict[str, Path]:
    d = ctx.scratch / "loads"
    d.mkdir(exist_ok=True)
    data = Data(seed=123)
    n = "count" if ctx.kind == "evolved" else "n"
    new_rows = [
        {"type": "Person", "data": {"email": "load-new-1@example.com", "name": "LN1", "age": 1}},
        {"type": "Person", "data": {"email": "load-new-2@example.com", "name": "LN2"}},
        {"type": "Loose", "data": {"name": "load-loose", n: 7}},
        {"edge": "Knows", "from": "load-new-1@example.com", "to": "p000@example.com", "data": {"since": 1}},
    ] + data.docs(900, 2)
    existing = [
        {"type": "Person", "data": {"email": "p001@example.com", "name": "LoadedName"}},
        {"type": "Person", "data": {"email": "p002@example.com", "name": "LoadedTwo", "age": 99}},
        {"type": "Pair", "data": {"a": "y", "b": 4}},
        {"edge": "WorksAt", "from": "p000@example.com", "to": "c0", "data": {"role": "loaded"}},
        {"edge": "Knows", "from": "p000@example.com", "to": "p003@example.com", "data": {"since": 1}},
        {"type": "Asset", "data": {"slug": "a000", "content": "base64:bG9hZGVk"}},
    ]
    # overwrite replaces every type a batch represents; Pair and Loose have
    # no incoming edges, so OLD and NEW can both apply it
    overwrite = [{"type": "Pair", "data": {"a": "w", "b": i, "label": "ow"}} for i in range(5)] + \
        [{"type": "Loose", "data": {"name": f"ow{i}", n: i}} for i in range(3)]
    return {"new": write_jsonl(d / "new.jsonl", new_rows), "existing": write_jsonl(d / "existing.jsonl", existing),
            "overwrite": write_jsonl(d / "overwrite.jsonl", overwrite)}


# ---------------------------------------------------------------- schema variants

def schema_variant(kind: str, variant: str) -> str | None:
    s = schema_text(kind)
    if variant == "reapply_same":
        return s
    if variant == "evolve":
        return EVOLVED_PG if kind == "base" else None
    if variant == "fulltext":
        return s.replace("    title: String @index\n", "    title: String @index(fulltext)\n")
    if variant == "fulltext_analyzer":
        return s.replace("    title: String @index\n", '    title: String @index(fulltext, analyzer = "english_v1")\n')
    if variant == "cosine":
        return s.replace("    emb: Vector(8) @index\n", '    emb: Vector(8, distance = "cosine") @index\n')
    if variant == "l2_explicit":
        return s.replace("    emb: Vector(8) @index\n", '    emb: Vector(8, distance = "l2") @index\n')
    if variant == "list_index":
        return s.replace("    tags: [String]\n", "    tags: [String] @index\n")
    if variant == "add_type":
        return s + "\nnode Late {\n    slug: String @key\n    t: String? @index\n}\n"
    return None


SCHEMA_VARIANTS = ["reapply_same", "evolve", "fulltext", "fulltext_analyzer", "cosine", "l2_explicit", "list_index", "add_type"]


# ---------------------------------------------------------------- the graph run

def run_graph(gdir: Path, out_dir: Path, phases=("ro", "write", "load", "schema", "branch", "maint", "roundtrip")) -> None:
    out = out_dir / f"{gdir.name}.jsonl"
    if out.exists():
        out.unlink()
    ctx = Ctx(gdir, out)
    prep = load_prep(ctx.kind)
    qs = catalog.all_queries(ctx.kind == "evolved")
    ids = ctx.meta.get("ids", {})

    # Baseline probes on an untouched copy.
    base_copy = ctx.copy("baseline")
    baseline = probes(ctx, base_copy, "baseline")
    ctx.rec(phase="baseline", op="old_probes", kind="baseline", probes=baseline)

    if "ro" in phases:
        g = ctx.copy("ro")
        d0 = tree_digest(g)
        files0 = tree_listing(g)
        for side in ("old", "new"):
            r = ctx.run(side, "snapshot", "--target", g, "--json")
            ctx.rec(phase="ro", op="snapshot", kind="side", side=side, status=status_of(r), msg=None if r.ok else r.message(), json=r.json())
            r = ctx.run(side, "schema", "show", "--target", g, "--json")
            ctx.rec(phase="ro", op="schema_show", kind="side", side=side, status=status_of(r), msg=None if r.ok else r.message(), json=r.json())
            r = ctx.run(side, "branch", "list", "--target", g, "--json")
            ctx.rec(phase="ro", op="branch_list", kind="side", side=side, status=status_of(r), msg=None if r.ok else r.message(), json=r.json())
            for br in ids.get("branches", ["main"]):
                r = ctx.run(side, "commit", "list", "--target", g, "--branch", br, "--json")
                ctx.rec(phase="ro", op=f"commit_list:{br}", kind="side", side=side, status=status_of(r), msg=None if r.ok else r.message(), json=r.json())
            r = ctx.run(side, "commit", "list", "--target", g, "--unpublished", "--json")
            ctx.rec(phase="ro", op="commit_list_unpublished", kind="side", side=side, status=status_of(r), msg=None if r.ok else r.message(), json=r.json())
            r = ctx.run(side, "upgrade", "--target", g, "--check", "--json")
            ctx.rec(phase="ro", op="upgrade_check", kind="side", side=side, status=status_of(r), msg=None if r.ok else r.message(), json=r.json())
        for q in qs:
            compare_query(ctx, g, q, prep, "read")
        # history: the first commit, every live branch, a deleted branch's commit
        hist_qs = [q for q in qs if q.name in ("q_scalars_all", "q_traverse", "s_search_title", "s_bm25_title", "s_nearest")]
        for snap_key in ("first_commit", "gone_commit", "merge_commit", "proposal_pending", "merge_proposal"):
            if ids.get(snap_key):
                for q in hist_qs:
                    compare_query(ctx, g, q, prep, f"snapshot[{snap_key}]", extra=("--snapshot", ids[snap_key]))
        for br in ids.get("branches", []):
            if br != "main":
                for q in hist_qs:
                    compare_query(ctx, g, q, prep, f"branch[{br}]", extra=("--branch", br))
        ctx.rec(phase="ro", op="export", kind="exports", **exports_equal(ctx, g))
        d1 = tree_digest(g)
        files1 = tree_listing(g)
        ctx.rec(phase="ro", op="readonly_digest", kind="digest", unchanged=d0 == d1,
                added=sorted(files1 - files0)[:30], removed=sorted(files0 - files1)[:30])

    if "write" in phases:
        # open read-write with a write that selects nothing
        noop = ctx.scratch / "noop.gq"
        noop.write_text('query noop() {\n  match { $x: Person } where $x.email = "nobody@nowhere"\n  update $x set { name: "x" }\n}')
        new_op(ctx, "open_rw_noop", lambda g: ctx.run("new", "mutate", "noop", "--query", noop, "--target", g, "--json"), baseline)
        wdir = ctx.prep / "w"
        for q in catalog.writes(ctx.kind):
            # OLD's edition-2 write, for the comparison only
            go = ctx.copy(f"w_old_{q.name}")
            ro = ctx.run("old", "mutate", q.name, "--query", wdir / f"{q.name}.e2.gq", "--target", go, "--json",
                         *(["--params", json.dumps(q.params)] if q.params else []))
            _, rows_old = export_rows("new", go)
            variants = [("e3", wdir / f"{q.name}.e3.gq")] + [(a, wdir / f"{q.name}.e3.{a}.gq") for a in q.e3_alt]
            if (wdir / f"{q.name}.fixed.gq").exists():
                variants.append(("fixed", wdir / f"{q.name}.fixed.gq"))
            for tag, f in variants:
                runs, gn, cn = new_op(ctx, f"write:{q.name}:{tag}", lambda g, f=f: ctx.run(
                    "new", "mutate", q.name, "--query", f, "--target", g, "--json",
                    *(["--params", json.dumps(q.params)] if q.params else [])), baseline, capture=True)
                co = canonical_export(rows_old)
                ctx.rec(phase="write", op=f"write:{q.name}:{tag}", kind="write_compare", query=q.name, variant=tag,
                        rfc=q.rfc, old_status=status_of(ro), old_msg=None if ro.ok else ro.message()[-800:],
                        old_out=ro.out[-800:] if ro.ok else None,
                        new_status=status_of(runs[0]) if runs else "harness_error",
                        new_msg=None if (not runs or runs[0].ok) else runs[0].message()[-800:],
                        new_out=runs[0].out[-800:] if runs and runs[0].ok else None,
                        same_state=co == cn, diff=sorted(set(co) ^ set(cn))[:8])

    if "load" in phases:
        files = load_files(ctx)
        for fname, f in files.items():
            olds = {}
            for mode in ("append", "merge", "overwrite"):
                go = ctx.copy(f"l_old_{fname}_{mode}")
                r = ctx.run("old", "load", "--data", f, "--mode", mode, "--target", go, "--json")
                _, rows = export_rows("new", go)
                olds[mode] = (r, canonical_export(rows))
            pairs = {"insert": "append", "replace": "merge", "overwrite": "overwrite", "upsert": None, "append": "append", "merge": "merge"}
            for mode, old_mode in pairs.items():
                runs, gn, cn = new_op(ctx, f"load:{fname}:{mode}", lambda g, mode=mode: ctx.run(
                    "new", "load", "--data", f, "--mode", mode, "--target", g, "--json"), baseline, capture=True)
                rec = {"phase": "load", "op": f"load:{fname}:{mode}", "kind": "load_compare", "file": fname,
                       "mode": mode, "old_mode": old_mode,
                       "new_status": status_of(runs[0]) if runs else "harness_error",
                       "new_msg": None if (not runs or runs[0].ok) else runs[0].message()[-800:]}
                if old_mode:
                    ro, co = olds[old_mode]
                    rec.update(old_status=status_of(ro), old_msg=None if ro.ok else ro.message()[-800:],
                               same_state=co == cn, diff=sorted(set(co) ^ set(cn))[:8])
                ctx.rec(**rec)
        # --from: fork a branch and load onto it
        new_op(ctx, "load:new:replace_from_main", lambda g: ctx.run(
            "new", "load", "--data", files["new"], "--mode", "replace", "--branch", "loaded", "--from", "main", "--target", g, "--json"), baseline)

    if "schema" in phases:
        for v in SCHEMA_VARIANTS:
            text = schema_variant(ctx.kind, v)
            if text is None:
                continue
            f = ctx.scratch / f"schema_{v}.pg"
            f.write_text(text)

            def apply(g, f=f):
                # apply is main-only: delete the other live branches first
                dels = [ctx.run("new", "branch", "delete", br, "--target", g, "--json")
                        for br in ids.get("branches", []) if br != "main"]
                plan = ctx.run("new", "schema", "plan", "--schema", f, "--target", g, "--json")
                app = ctx.run("new", "schema", "apply", "--schema", f, "--target", g, "--json")
                show = ctx.run("new", "schema", "show", "--target", g, "--json")
                ib = ctx.run("new", "index", "build", "--target", g, "--json")
                return dels + [plan, app, show, ib]
            runs, gn = new_op(ctx, f"schema:{v}", apply, baseline)
            # OLD applying the same text, where OLD can parse it
            go = ctx.copy(f"s_old_{v}")
            for br in ids.get("branches", []):
                if br != "main":
                    ctx.run("old", "branch", "delete", br, "--target", go, "--json")
            ro = ctx.run("old", "schema", "apply", "--schema", f, "--target", go, "--json")
            ctx.rec(phase="schema", op=f"schema:{v}", kind="schema_old_side", old_status=status_of(ro),
                    old_msg=None if ro.ok else ro.message()[-800:], old_out=ro.out[-1500:] if ro.ok else None)

    if "branch" in phases:
        bq = ctx.scratch / "branch_w.gq"
        bq.write_text('query bw() {\n  replace Person { email: "nb1@example.com", name: "NB" }\n}')

        def create_write_merge(g):
            a = ctx.run("new", "branch", "create", "nb", "--from", "main", "--target", g, "--json")
            b = ctx.run("new", "mutate", "bw", "--query", bq, "--branch", "nb", "--target", g, "--json")
            c = ctx.run("new", "branch", "merge", "nb", "--into", "main", "--target", g, "--json")
            d = ctx.run("new", "branch", "delete", "nb", "--target", g, "--json")
            return [a, b, c, d]
        new_op(ctx, "branch:create_write_merge_delete", create_write_merge, baseline)
        for br in ids.get("branches", []):
            if br == "main":
                continue
            _, _, e_new = new_op(ctx, f"branch:merge_old[{br}]", lambda g, br=br: [
                ctx.run("new", "branch", "merge", br, "--into", "main", "--dry-run", "--target", g, "--json"),
                ctx.run("new", "branch", "merge", br, "--into", "main", "--target", g, "--json")], baseline, capture=True)
            new_op(ctx, f"branch:delete_old[{br}]", lambda g, br=br: ctx.run("new", "branch", "delete", br, "--target", g, "--json"), baseline)
            # OLD merging the same branch, for the comparison of results
            go = ctx.copy(f"b_old_{br}")
            ro = ctx.run("old", "branch", "merge", br, "--into", "main", "--target", go, "--json")
            e_old = canonical_export(export_rows("new", go)[1])
            ctx.rec(phase="branch", op=f"branch:merge_old[{br}]", kind="merge_compare", old_status=status_of(ro),
                    old_msg=None if ro.ok else ro.message()[-800:], same_state=e_old == e_new,
                    diff=sorted(set(e_old) ^ set(e_new or []))[:8])
        if ids.get("gone_commit"):
            new_op(ctx, "branch:create_from_deleted_commit", lambda g: ctx.run(
                "new", "branch", "create", "fromgone", "--from-commit", ids["gone_commit"], "--target", g, "--json"), baseline)
        if ids.get("proposal_pending"):
            new_op(ctx, "commit:publish_old_proposal", lambda g: ctx.run(
                "new", "commit", "publish", ids["proposal_pending"], "--target", g, "--json"), baseline)
        if ids.get("merge_proposal"):
            new_op(ctx, "commit:drop_old_merge_proposal", lambda g: ctx.run(
                "new", "commit", "drop", ids["merge_proposal"], "--target", g, "--json"), baseline)
            new_op(ctx, "commit:publish_old_merge_proposal", lambda g: ctx.run(
                "new", "commit", "publish", ids["merge_proposal"], "--target", g, "--json"), baseline)

    if "maint" in phases:
        new_op(ctx, "maint:index_build", lambda g: ctx.run("new", "index", "build", "--target", g, "--json"), baseline)
        new_op(ctx, "maint:index_rebuild_fulltext", lambda g: ctx.run("new", "index", "rebuild", "--kind", "full-text", "--target", g, "--json"), baseline)
        new_op(ctx, "maint:optimize", lambda g: ctx.run("new", "optimize", "--target", g, "--json"), baseline)
        new_op(ctx, "maint:cleanup_keep1", lambda g: ctx.run("new", "cleanup", "--keep", "1", "--confirm", "--target", g, "--json"), baseline)
        new_op(ctx, "maint:upgrade", lambda g: ctx.run("new", "upgrade", "--target", g, "--json"), baseline)
        # every read again after NEW's index pass, OLD's answer on the
        # untouched graph against NEW's on the indexed copy
        gi = ctx.scratch / "maint_index_build" / "graph"
        if gi.exists():
            for q in qs:
                if q.kind == "search":
                    old = query(ctx, "old", ctx.graph if False else base_copy, ctx.prep / "e2.gq", q.name, q.params)
                    new = query(ctx, "new", gi, ctx.prep / "e3.gq", q.name, q.params)
                    o_old, o_new = arrow_observation(old, "order {" in q.e2), arrow_observation(new, "order {" in q.e2)
                    ctx.rec(phase="maint", op=f"after_new_index_build:{q.name}", kind="compare", query=q.name, qkind=q.kind,
                            rfc=q.rfc, old_status=status_of(old), old_secs=old.secs, old=obs_json(o_old),
                            results={"e3": {"status": status_of(new), "secs": new.secs, "diffs": p22().differences(o_old, o_new),
                                            "refined": refined(o_old, o_new),
                                            "new": obs_json(o_new), "panic": new.panicked()}})

    if "roundtrip" in phases:
        rt = ctx.scratch / "roundtrip"
        rt.mkdir(exist_ok=True)
        r_old, rows_old = export_rows("old", ctx.graph)
        exp = rt / "old_export.jsonl"
        exp.write_text(r_old.out)
        pg = rt / "schema.pg"
        pg.write_text(schema_text(ctx.kind))
        steps = {}
        r = ctx.run("new", "init", "--schema", pg, rt / "new_graph")
        steps["new_init"] = status_of(r), None if r.ok else r.message()[-800:]
        r = ctx.run("new", "load", "--data", exp, "--mode", "insert", "--target", rt / "new_graph", "--json")
        steps["new_load"] = status_of(r), None if r.ok else r.message()[-800:]
        r_new, rows_new = export_rows("new", rt / "new_graph")
        (rt / "new_export.jsonl").write_text(r_new.out)
        r = ctx.run("old", "init", "--schema", pg, rt / "old_again")
        steps["old_init"] = status_of(r), None if r.ok else r.message()[-800:]
        r = ctx.run("old", "load", "--data", rt / "new_export.jsonl", "--mode", "append", "--target", rt / "old_again", "--json")
        steps["old_load"] = status_of(r), None if r.ok else r.message()[-800:]
        r_back, rows_back = export_rows("old", rt / "old_again")
        a, b, c = canonical_export(rows_old), canonical_export(rows_new), canonical_export(rows_back)
        ctx.rec(phase="roundtrip", op="export_old_load_new_and_back", kind="roundtrip", steps=steps,
                old_rows=len(a), new_rows=len(b), back_rows=len(c), old_eq_new=a == b, old_eq_back=a == c,
                diff_old_new=sorted(set(a) ^ set(b))[:6], diff_old_back=sorted(set(a) ^ set(c))[:6])
    shutil.rmtree(ctx.scratch, ignore_errors=True)


if __name__ == "__main__":
    import concurrent.futures
    cmd = sys.argv[1]
    if cmd == "prepare":
        groot = Path(sys.argv[2])
        for k in ("base", "evolved"):
            prepare(k, groot / f"{k}__none__one" / "graph")
            print("prepared", k)
    elif cmd == "run":
        gdir_root = Path(sys.argv[2])
        out_dir = WORK / "results"
        out_dir.mkdir(parents=True, exist_ok=True)
        names = sys.argv[3:] or sorted(p.name for p in gdir_root.iterdir() if (p / "meta.json").exists())
        phases = os.environ.get("MM_PHASES", "ro,write,load,schema,branch,maint,roundtrip").split(",")

        def one(n):
            t0 = time.time()
            done = out_dir / f"{n}.done"
            if done.exists():
                return n, "skipped (done)", 0.0
            try:
                run_graph(gdir_root / n, out_dir, tuple(phases))
                done.write_text(str(time.time() - t0))
                return n, "ok", time.time() - t0
            except Exception as e:
                import traceback
                append_jsonl(out_dir / f"{n}.jsonl", {"graph": n, "kind": "harness_error", "error": traceback.format_exc()[-3000:]})
                return n, "harness_error", time.time() - t0
        with concurrent.futures.ProcessPoolExecutor(max_workers=int(os.environ.get("MM_JOBS", "6"))) as ex:
            for n, st, secs in ex.map(one, names):
                print(n, st, round(secs, 1), flush=True)
