"""One fuzz case: (seed, schema, data, operation script), built by a GQ 2
binary and then upgraded step by step with NEW.

Part 1 builds the graph with the case's GQ 2 builder (GQ2-REL or GQ2-MAIN):
`init`, the base load, then a drawn script of edition-2 operations (loads in
every mode, mutations, deletes, `index build` at random points, `optimize`,
`cleanup`, `index rebuild --kind full-text`, branches created, written,
merged, deleted and recreated, schema evolution by apply).

Part 2 copies the graph and runs a drawn NEW sequence. After every NEW step
(`observe`) it records, on the same graph state:

* NEW's answers to the fixed read set (hand respelling, and `lint --fix`'s
  respelling of the edition-2 text);
* each GQ 2 binary's probe: open (`snapshot`), the edition-2 read set,
  `export`, a write on a copy, and on another copy GQ 2's own `index build`
  and `index rebuild --kind full-text`, then NEW's reads again on that copy;

and checks the oracles of the brief:

1. index state never changes NEW's answer: states with the same logical
   content (NEW's canonical export) and the same semantic schema answer
   identically, across every index state, GQ 2's rebuild, and a fresh graph
   built by export and load;
2. analyzer changes: covered by (1)'s grouping (a profile is part of the
   semantic schema, so changing back must restore the earlier answer, and
   the following `index build` must not move it) and by the fresh-graph
   comparison right after the change;
3. GQ 2 against NEW on one graph: every difference must be one the
   matrix's classifier sanctions (`analyze.classify_read`, and the
   signatures in `migration-matrix/results/differences.json`);
4. rollback: a GQ 2 refusal of the whole graph must follow a stored
   one-way construct listed by `schema plan` (or the stamp-18 upgrade for
   GQ2-REL), every listed construct must make GQ 2 refuse, and a
   full-text-only refusal must follow a step the upgrade guide names and be
   restored by GQ 2's own rebuild;
5. integrity: no panic, hang, uncoded `--json` refusal, unreadable graph,
   and exports of one commit equal across binaries.

Every check that fails is a finding, recorded with the case's seed and the
script, which `ufshrink.py` replays.
"""

from __future__ import annotations

import copy
import json
import os
import random
import shutil
import time
import traceback
from collections import Counter, defaultdict
from pathlib import Path

import ufcommon as C
from ufcommon import lib
import ufgen as G
import ufquery as Q

LEX = {"lex_any", "lex_all", "fuzzy", "bm25", "bm25_ties", "rrf"}
MM_RESULTS = C.MM.parent / "results"
CASE_BUDGET = float(os.environ.get("UF_CASE_BUDGET", "420"))
PROBE_REBUILD = os.environ.get("UF_PROBE_REBUILD", "1") == "1"

# Steps the upgrade guide names as building or rebuilding an index, after
# which an earlier binary may refuse full-text reads (docs/user/operations/upgrade.md).
GUIDE_FTS_STEPS = {"index_build", "fts_rebuild", "optimize", "schema", "write", "load", "branch"}


def chunks(rows: list, n: int):
    """Load batches of at most n entities: nodes first, edges after their nodes."""
    nodes = [r for r in rows if "type" in r]
    edges = [r for r in rows if "edge" in r]
    for i in range(0, len(nodes), n):
        yield nodes[i:i + n]
    for i in range(0, len(edges), n):
        yield edges[i:i + n]


def _obs(r, ordered: bool) -> dict:
    """A read's observation: columns, Arrow types, rows, or the error code."""
    o = lib.arrow_observation(r, ordered)
    d = o.to_json()
    d["code"] = None
    if o.error:
        j = None
        try:
            j = json.loads(r.out) if r.out else None
        except Exception:
            pass
        d["code"] = o.code()
        if isinstance(j, dict):
            d["code"] = (j.get("diagnostic") or {}).get("code") or j.get("code") or d["code"]
            if j.get("full_text_index_rebuild_required"):
                d["code"] = "full_text_index_rebuild_required"
        if d["code"] is None and "full-text index" in (o.error or "") and "requires rebuild" in (o.error or ""):
            d["code"] = "full_text_index_rebuild_required"
    return d


def _same(a: dict, b: dict, multiset: bool = False) -> bool:
    if (a.get("error") is None) != (b.get("error") is None):
        return False
    if a.get("error") is not None:
        return a.get("code") == b.get("code")
    if a.get("columns") != b.get("columns") or a.get("types") != b.get("types"):
        return False
    ra, rb = a.get("rows") or [], b.get("rows") or []
    if multiset:
        ka = sorted(json.dumps(r, sort_keys=True) for r in ra)
        kb = sorted(json.dumps(r, sort_keys=True) for r in rb)
        return ka == kb
    return json.dumps(ra, sort_keys=True) == json.dumps(rb, sort_keys=True)


def _brief(o: dict, n: int = 6) -> dict:
    if o is None:
        return None
    rows = o.get("rows")
    return {"error": (o.get("error") or "")[:300] or None, "code": o.get("code"), "columns": o.get("columns"),
            "types": o.get("types"), "n": None if rows is None else len(rows), "rows": (rows or [])[:n]}


# ---------------------------------------------------------------- edits

def apply_edits(cfg: G.Config, edits: list) -> G.Config | None:
    """A configuration with the edits applied, or None when a target is gone."""
    c = G.clone(cfg)
    for e in edits:
        if "add_type" in e:
            nt = e["add_type"]
            if c.node(nt["name"]):
                return None
            c.nodes.append(G.NodeT(**{**nt, "props": [G.Prop(**p) for p in nt["props"]]}))
            c.sizes[nt["name"]] = 0
            continue
        t = c.node(e["type"])
        if t is None:
            return None
        if "add" in e:
            if t.prop(e["add"]["name"]):
                return None
            t.props.append(G.Prop(**e["add"]))
            continue
        if "drop" in e:
            p = t.prop(e["drop"])
            if p is None:
                return None
            t.props.remove(p)
            continue
        p = t.prop(e["prop"])
        if p is None:
            return None
        if "rename" in e:
            if t.prop(e["rename"]):
                return None
            p.rename_from = p.name
            p.name = e["rename"]
            for k, v in list(c.vec_query.items()):
                if k == f"{t.name}.{p.rename_from}":
                    c.vec_query[f"{t.name}.{p.name}"] = v
        if "set" in e:
            for k, v in e["set"].items():
                setattr(p, k, v)
    return c


def settle(cfg: G.Config) -> G.Config:
    c = G.clone(cfg)
    for t in c.nodes:
        for p in t.props:
            p.rename_from = None
    return c


def semantic_key(cfg: G.Config) -> str:
    """The schema as it decides NEW's answers: a plain `@index` and
    `@index(fulltext)` read alike (both are matched by the exact scan with
    the field's profile, english_v1 by default), an explicit `l2` is an
    omitted distance, and where a list's `@index` is written does not
    matter. Index presence stays (an unindexed String refuses `T27`)."""
    c = settle(cfg)
    parts = []
    for t in c.nodes:
        ps = []
        for p in t.props:
            lex = ""
            if p.ty == "String" and (p.index or p.fulltext):
                lex = f"lex({p.analyzer or 'english_v1'},{p.scorer or 'bm25_v1'})"
            dist = (p.distance or "l2") if p.ty == "vector" else ""
            ps.append(f"{p.name}:{p.ty_text().split(',')[0]}:{lex}:{dist}:{p.key}")
        parts.append(f"{t.name}[{t.key_mode}:{','.join(t.compound)}]{{{';'.join(ps)}}}")
    for e in c.edges:
        parts.append(f"{e.name}:{e.src}->{e.dst}{{{';'.join(p.name + ':' + p.ty for p in e.props)}}}")
    return C.sha("\n".join(parts))


# ---------------------------------------------------------------- scripts

def _pick_type(rng, cfg, pred=lambda n: True):
    ts = [n for n in cfg.nodes if pred(n)]
    return rng.choice(ts).name if ts else None


def _evolve_edits(rng, cfg: G.Config, kind: str) -> list | None:
    if kind == "add_type":
        i = len(cfg.nodes)
        name = f"A{i}x{rng.randint(0, 99)}"
        return [{"add_type": {"name": name, "key_mode": "single", "compound": [],
                              "props": [G.Prop("k", "String", key=True).__dict__,
                                        G.Prop("txt0", "String", nullable=True, index=True).__dict__,
                                        G.Prop("s0", "I32", nullable=True).__dict__]}}]
    t = cfg.node(_pick_type(rng, cfg) or "")
    if t is None:
        return None
    movable = [p for p in t.props if not p.key and p.name not in t.compound]
    if kind == "add_prop":
        ty = rng.choice(["String", "I32", "F64", "list_str"])
        name = f"n{rng.randint(0, 999)}"
        p = G.Prop(name, ty, nullable=True, index=(ty in ("String", "I32") and rng.random() < 0.6))
        return [{"type": t.name, "add": p.__dict__}]
    if not movable:
        return None
    p = rng.choice(movable)
    if kind == "rename_prop":
        return [{"type": t.name, "prop": p.name, "rename": p.name + "r"}]
    if kind == "drop_prop":
        return [{"type": t.name, "drop": p.name}]
    if kind == "add_index":
        cand = [q for q in movable if not q.index and q.ty not in ("blob", "list_str", "list_i32")]
        if not cand:
            return None
        return [{"type": t.name, "prop": rng.choice(cand).name, "set": {"index": True}}]
    if kind == "drop_index":
        cand = [q for q in movable if q.index and not q.fulltext]
        if not cand:
            return None
        return [{"type": t.name, "prop": rng.choice(cand).name, "set": {"index": False}}]
    return None


def draw_gq2_script(cfg: G.Config, seed: int) -> list:
    rng = random.Random(seed * 101 + 3)
    sim = G.clone(cfg)
    nkeys = dict(cfg.sizes)
    live: list[str] = []
    ops = []
    n_ops = rng.randint(2, 6) if cfg.stress else rng.randint(2, 14)
    bn = 0
    for _ in range(n_ops):
        kind = rng.choices(
            ["load", "insert", "update", "delete", "insert_edge", "index_build", "optimize", "cleanup",
             "fts_rebuild", "branch", "evolve"],
            [10, 5, 7, 5, 2, 10, 4, 3, 3, 7, 6])[0]
        if kind in ("load", "insert", "update", "delete"):
            t = _pick_type(rng, sim)
            if t is None:
                continue
            n = sim.node(t)
            if kind == "load":
                mode = rng.choices(["merge", "append", "overwrite"], [6, 3, 1])[0]
                new = rng.randint(1, max(2, min(3000, nkeys[t] // 3 + 5)))
                ex = 0 if mode == "append" or not n.key_props() else rng.randint(0, min(nkeys[t], 200))
                ops.append({"op": "load", "mode": mode, "type": t, "new": new, "existing": ex,
                            "start": nkeys[t], "seed": rng.randrange(10**9)})
                nkeys[t] += new
            elif kind == "insert":
                ops.append({"op": "insert", "type": t, "key": nkeys[t], "seed": rng.randrange(10**9)})
                nkeys[t] += 1
            else:
                ops.append({"op": kind, "type": t, "key": rng.randrange(max(nkeys[t], 1)),
                            "seed": rng.randrange(10**9)})
        elif kind == "insert_edge":
            if sim.edges:
                e = rng.choice(sim.edges)
                ops.append({"op": "insert_edge", "edge": e.name, "a": rng.randrange(max(nkeys[e.src], 1)),
                            "b": rng.randrange(max(nkeys[e.dst], 1)), "seed": rng.randrange(10**9)})
        elif kind == "cleanup":
            ops.append({"op": "cleanup", "keep": rng.choice([1, 2, 5])})
        elif kind == "branch":
            sub = rng.choice(["create_write", "merge", "delete", "recreate"])
            if sub == "create_write" or not live:
                name = f"b{bn}"
                bn += 1
                t = _pick_type(rng, sim)
                ops.append({"op": "branch_create", "name": name})
                if t:
                    ops.append({"op": "insert", "type": t, "key": nkeys[t], "branch": name, "seed": rng.randrange(10**9)})
                    nkeys[t] += 1
                live.append(name)
            elif sub == "merge":
                name = rng.choice(live)
                ops.append({"op": "branch_merge", "name": name, "delete": rng.random() < 0.5})
                if ops[-1]["delete"]:
                    live.remove(name)
            elif sub == "delete":
                name = rng.choice(live)
                ops.append({"op": "branch_delete", "name": name})
                live.remove(name)
            else:
                name = rng.choice(live)
                ops.append({"op": "branch_delete", "name": name})
                ops.append({"op": "branch_create", "name": name})
        elif kind == "evolve":
            ek = rng.choice(["add_type", "add_prop", "rename_prop", "drop_prop", "add_index", "drop_index"])
            edits = _evolve_edits(rng, sim, ek)
            if edits is None:
                continue
            nxt = apply_edits(sim, edits)
            if nxt is None:
                continue
            for b in live:
                ops.append({"op": "branch_delete", "name": b})
            live = []
            ops.append({"op": "evolve", "kind": ek, "edits": edits})
            sim = settle(nxt)
            for t in sim.nodes:
                nkeys.setdefault(t.name, 0)
        else:
            ops.append({"op": kind})
    return ops


SCHEMA_KINDS = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "evolve"]


def _text_fields(cfg, pred=lambda p: True):
    out = []
    for t in cfg.nodes:
        for p in t.props:
            if p.ty == "String" and not p.key and p.name not in t.compound and pred(p):
                out.append((t.name, p.name))
    return out


def _schema_step(rng, cfg: G.Config, kind: str) -> list | None:
    """One or more NEW schema steps of sub-kind (a)-(i), each with its edits."""
    def step(k, edits):
        return {"step": "schema", "kind": k, "edits": edits}
    if kind == "a":
        f = _text_fields(cfg, lambda p: p.index and not p.fulltext)
        if not f:
            return None
        t, p = rng.choice(f)
        return [step("a", [{"type": t, "prop": p, "set": {"fulltext": True}}])]
    if kind == "b":
        f = _text_fields(cfg, lambda p: p.index)
        if not f:
            return None
        t, p = rng.choice(f)
        return [step("b", [{"type": t, "prop": p, "set": {"fulltext": True, "analyzer": rng.choice(G.ANALYZERS)}}])]
    if kind == "c":
        f = _text_fields(cfg, lambda p: p.index)
        if not f:
            return None
        t, p = rng.choice(f)
        a, b = rng.sample(G.ANALYZERS, 2)
        out = [step("c_set", [{"type": t, "prop": p, "set": {"fulltext": True, "analyzer": a}}]),
               step("c_change", [{"type": t, "prop": p, "set": {"fulltext": True, "analyzer": b}}])]
        if rng.random() < 0.6:
            out.append({"step": "index_build"})
        out.append(step("c_back", [{"type": t, "prop": p, "set": {"fulltext": True, "analyzer": a}}]))
        return out
    if kind == "d":
        f = _text_fields(cfg, lambda p: p.index)
        if not f:
            return None
        t, p = rng.choice(f)
        return [step("d", [{"type": t, "prop": p, "set": {"fulltext": True, "scorer": rng.choice(G.SCORERS)}}])]
    if kind == "e":
        f = _text_fields(cfg, lambda p: p.index)
        if not f:
            return None
        t, p = rng.choice(f)
        return [step("e_explicit", [{"type": t, "prop": p, "set": {"fulltext": True, "analyzer": "english_v1"}}]),
                step("e_remove", [{"type": t, "prop": p, "set": {"fulltext": True, "analyzer": None}}])]
    if kind == "f":
        f = _text_fields(cfg, lambda p: p.index)
        if not f:
            return None
        t, p = rng.choice(f)
        return [step("f_to_fulltext", [{"type": t, "prop": p, "set": {"fulltext": True, "analyzer": rng.choice([None] + G.ANALYZERS)}}]),
                step("f_to_plain", [{"type": t, "prop": p, "set": {"fulltext": False, "analyzer": None, "scorer": None}}])]
    if kind == "g":
        f = _text_fields(cfg, lambda p: p.index)
        if not f:
            return None
        t, p = rng.choice(f)
        tn = cfg.node(t)
        prop = copy.deepcopy(tn.prop(p))
        prop.fulltext = True
        prop.analyzer = rng.choice([None] + G.ANALYZERS)
        prop.nullable = True  # a re-added property is optional
        first = step("g_fulltext", [{"type": t, "prop": p, "set": {"fulltext": True, "analyzer": prop.analyzer}}])
        if rng.random() < 0.5:
            return [first, step("g_rename", [{"type": t, "prop": p, "rename": p + "_rn"}])]
        return [first, step("g_drop", [{"type": t, "drop": p}]), step("g_readd", [{"type": t, "add": prop.__dict__}])]
    if kind == "h":
        vs = [(t.name, p.name) for t in cfg.nodes for p in t.props if p.ty == "vector"]
        if not vs:
            return None
        t, p = rng.choice(vs)
        return [step("h", [{"type": t, "prop": p, "set": {"distance": rng.choice(G.DISTANCES)}}])]
    if kind == "i":
        ls = [(t.name, p.name) for t in cfg.nodes for p in t.props if p.ty in ("list_str", "list_i32")]
        if not ls:
            t = _pick_type(rng, cfg)
            if t is None:
                return None
            name = f"lst{rng.randint(0, 99)}"
            return [step("i_add_list", [{"type": t, "add": G.Prop(name, "list_str", nullable=True).__dict__}]),
                    step(rng.choice(["i_prop", "i_body"]), [{"type": t, "prop": name, "set": {}}])]
        t, p = rng.choice(ls)
        sub = rng.choice(["i_prop", "i_body"])
        s = {"index": True, "body_index": False} if sub == "i_prop" else {"index": False, "body_index": True}
        return [step(sub, [{"type": t, "prop": p, "set": s}])]
    if kind == "evolve":
        ek = rng.choice(["add_type", "add_prop", "rename_prop", "drop_prop", "add_index", "drop_index"])
        edits = _evolve_edits(rng, cfg, ek)
        return [step("evolve_" + ek, edits)] if edits else None
    return None


def draw_new_script(cfg: G.Config, live_gq2: list, builder: str, seed: int) -> list:
    rng = random.Random(seed * 7 + 11)
    sim = G.clone(cfg)
    nkeys = dict(cfg.sizes)
    steps = []
    n = rng.randint(3, 5) if cfg.stress else rng.randint(4, 12)
    gq2_branches = list(live_gq2)
    bn = 0
    while len(steps) < n:
        kind = rng.choices(
            ["ro_open", "rw_open", "write", "load", "index_build", "fts_rebuild", "optimize", "cleanup",
             "branch", "merge_gq2", "schema", "upgrade"],
            [3, 3, 9, 6, 8, 6, 5, 3, 5, 3 if gq2_branches else 0, 22, 2 if builder == "rel" else 0])[0]
        if kind in ("write", "load"):
            t = _pick_type(rng, sim)
            if t is None:
                continue
            node = sim.node(t)
            keyed = bool(node.key_props())
            if kind == "write":
                wk = rng.choice(["insert", "update", "delete"] + (["upsert", "replace"] if keyed else []) + (["insert_edge"] if sim.edges else []))
                st = {"step": "write", "kind": wk, "type": t, "key": rng.randrange(max(nkeys[t], 1)),
                      "seed": rng.randrange(10**9)}
                if wk == "insert":
                    st["key"] = nkeys[t]
                    nkeys[t] += 1
                if wk == "insert_edge":
                    e = rng.choice(sim.edges)
                    st.update({"edge": e.name, "a": rng.randrange(max(nkeys[e.src], 1)), "b": rng.randrange(max(nkeys[e.dst], 1))})
                steps.append(st)
            else:
                mode = rng.choice(["insert", "upsert", "replace", "overwrite"] if keyed else ["insert", "overwrite"])
                new = rng.randint(1, max(2, min(2000, nkeys[t] // 3 + 5)))
                ex = 0 if mode == "insert" or not keyed else rng.randint(0, min(nkeys[t], 200))
                steps.append({"step": "load", "mode": mode, "type": t, "new": new if mode != "upsert" else 0,
                              "existing": ex, "start": nkeys[t], "seed": rng.randrange(10**9)})
                if mode != "upsert":
                    nkeys[t] += new
        elif kind == "cleanup":
            steps.append({"step": "cleanup", "keep": rng.choice([1, 3])})
        elif kind == "branch":
            t = _pick_type(rng, sim)
            steps.append({"step": "branch", "name": f"nb{bn}", "type": t, "key": nkeys.get(t, 0),
                          "merge": rng.random() < 0.6, "delete": rng.random() < 0.6, "seed": rng.randrange(10**9)})
            if t:
                nkeys[t] += 1
            bn += 1
        elif kind == "merge_gq2":
            b = rng.choice(gq2_branches)
            steps.append({"step": "merge_gq2", "name": b, "delete": rng.random() < 0.5})
        elif kind == "schema":
            sk = rng.choices(SCHEMA_KINDS, [3, 3, 3, 2, 2, 2, 2, 2, 2, 2])[0]
            sts = _schema_step(rng, sim, sk)
            if not sts:
                continue
            for s in sts:
                if s.get("step") == "schema":
                    nxt = apply_edits(sim, s["edits"])
                    if nxt is None:
                        break
                    sim = settle(nxt)
                    for t in sim.nodes:
                        nkeys.setdefault(t.name, 0)
                steps.append({**s, "grp": len(steps) if s is sts[0] else steps[-1].get("grp")})
        else:
            steps.append({"step": kind})
    if rng.random() < 0.6:
        # one-way applies last, so GQ 2's probes see the earlier steps
        oneway = {s.get("grp") for s in steps if s.get("step") == "schema" and s["kind"] in ONE_WAY_KINDS}
        steps = [s for s in steps if s.get("grp") not in oneway] + [s for s in steps if s.get("grp") in oneway]
    return steps


ONE_WAY_KINDS = {"a", "b", "c_set", "d", "e_explicit", "f_to_fulltext", "g_fulltext", "h", "i_prop"}


# ---------------------------------------------------------------- case

class Case:
    def __init__(self, seed: int, *, stress: bool = False, cfg: G.Config | None = None,
                 script: dict | None = None, root: Path | None = None, out: Path | None = None):
        self.seed = seed
        self.cfg0 = cfg or G.draw(seed, stress)
        self.root = root or (C.WORK / "cases" / f"s{seed}")
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)
        self.out = out
        self.script = script or {}
        self.findings: list[dict] = []
        self.events: list[dict] = []
        self.cov: Counter = Counter()
        self.t0 = time.time()
        self.invocations = Counter()
        self.groups: dict = {}
        self.cur: G.Config = G.clone(self.cfg0)      # the schema NEW and GQ 2 are told
        self.stored: G.Config | None = None          # the schema the graph stores
        self.nkeys = dict(self.cfg0.sizes)
        self.hist: list[dict] = []                   # NEW steps run so far
        self.idx_state: dict = {}
        self.truncated = False
        self.rollback: list = []                     # (step, kind, sub, side, status)

    # ---- running -------------------------------------------------
    def og(self, side, *args, timeout=240, binary=False, stdin=None, what=""):
        r = lib.og(side, *args, home=self.root / f"home-{side}", timeout=timeout, binary=binary, stdin=stdin)
        self.invocations[side] += 1
        for problem in C.integrity(r):
            self.finding("integrity", 5, f"{side}:{problem}", what or " ".join(map(str, args[:2])),
                         {"args": [str(a) for a in args], "rc": r.rc, "out": C.clean(r.out)[-1500:] if not binary else "",
                          "err": C.clean(r.err)[-2500:]})
        return r

    def finding(self, kind, oracle, sig, where, detail):
        f = {"seed": self.seed, "kind": kind, "oracle": oracle, "sig": sig, "where": where,
             "step": len(self.hist), "detail": detail}
        self.findings.append(f)

    def event(self, **kw):
        self.events.append(kw)

    def mq(self, text: str, name="w") -> Path:
        p = self.root / f"q-{len(self.events)}-{C.sha(text)}.gq"
        p.write_text(text)
        return p

    # ---- data ----------------------------------------------------
    def rows_for(self, cfg, t, start, new, existing, seed, keep_existing_text=False):
        rng = random.Random(seed)
        n = cfg.node(t)
        out = []
        for i in range(start, start + new):
            out.append(G.node_row(rng, n, i))
        pool = max(min(start, self.nkeys.get(t, start)), 1)
        for i in rng.sample(range(pool), min(existing, pool)) if existing else []:
            out.append(G.node_row(rng, n, i))
        return out

    def write_text(self, cfg: G.Config, op: dict, edition: int):
        """An edition-2 or GQ 3 write for a drawn operation, with params."""
        t = cfg.node(op.get("type") or "") if op.get("type") else None
        kind = op.get("kind") or op.get("op")
        rng = random.Random(op.get("seed", 0))
        params = {}
        decl = []

        def bind(p: G.Prop, v):
            if p.ty == "enum":
                return json.dumps(v, ensure_ascii=False)
            nm = f"p_{p.name}"
            ty = {"blob": "Blob", "list_str": "[String]", "list_i32": "[I32]",
                  "vector": f"Vector({p.dim})"}.get(p.ty, p.ty)
            decl.append(f"${nm}: {ty}")
            params[nm] = v
            return f"${nm}"

        def where_key(var):
            kps = t.key_props()
            row = G.node_row(random.Random(op.get("seed", 0) + 1), t, op.get("key", 0))["data"]
            if kps:
                conds = []
                for k in kps:
                    p = t.prop(k)
                    conds.append((k, bind(p, row[k])))
                if edition == 2:
                    return " and ".join(f"{k} = {v}" for k, v in conds)
                return " and ".join(f"{var}.{k} = {v}" for k, v in conds)
            # unkeyed: a range over a numeric property, or everything
            num = [p for p in t.props if p.ty in ("I32", "I64") and not p.nullable]
            if num:
                p = num[0]
                v = bind(p, 0)
                return f"{p.name} > {v}" if edition == 2 else f"{var}.{p.name} > {v}"
            return None

        if kind == "insert_edge":
            e = next((x for x in cfg.edges if x.name == op["edge"]), None)
            if e is None:
                return None
            a = G.key_value(cfg.node(e.src), op["a"])
            b = G.key_value(cfg.node(e.dst), op["b"])
            src, dst = ("from", "to") if edition == 2 else ("@src", "@dst")
            body = f'insert {e.name} {{ {src}: {json.dumps(a)}, {dst}: {json.dumps(b)} }}'
            return f"query w() {{\n  {body}\n}}", params
        if t is None:
            return None
        if kind in ("insert", "upsert", "replace"):
            row = G.node_row(rng, t, op["key"])["data"]
            if kind == "upsert":
                keep = set(t.key_props())
                others = [p.name for p in t.props if p.name in row and p.name not in keep]
                keep |= set(rng.sample(others, min(len(others), 2)))
                row = {k: v for k, v in row.items() if k in keep}
            if not row:
                tp = next((p for p in t.props if p.ty == "String"), None)
                if tp is None:
                    return None
                row = {tp.name: "probe"}
            assigns = ", ".join(f"{p.name}: {bind(p, row[p.name])}" for p in t.props if p.name in row)
            verb = kind if edition == 3 else "insert"
            if edition == 2 and kind == "upsert":
                return None
            head = f"query w({', '.join(decl)})" if decl else "query w()"
            return f"{head} {{\n  {verb} {t.name} {{ {assigns} }}\n}}", params
        if kind == "update":
            tps = [p for p in t.props if p.ty == "String" and not p.key and p.name not in t.compound]
            if not tps:
                return None
            p = rng.choice(tps)
            val = G.text_value(rng) or "updated"
            w = where_key("$x")
            if w is None:
                return None
            sv = bind(p, val)
            head = f"query w({', '.join(decl)})" if decl else "query w()"
            if edition == 2:
                return f"{head} {{\n  update {t.name} set {{ {p.name}: {sv} }} where {w}\n}}", params
            return f"{head} {{\n  match {{ $x: {t.name} }} where {w}\n  update $x set {{ {p.name}: {sv} }}\n}}", params
        if kind == "delete":
            w = where_key("$x")
            if w is None:
                return None
            head = f"query w({', '.join(decl)})" if decl else "query w()"
            if edition == 2:
                return f"{head} {{\n  delete {t.name} where {w}\n}}", params
            return f"{head} {{\n  match {{ $x: {t.name} }} where {w}\n  delete $x\n}}", params
        return None

    def run_write(self, side, g, cfg, op, edition, branch=None):
        wt = self.write_text(cfg, op, edition)
        if wt is None:
            return None
        text, params = wt
        q = self.mq(text)
        args = ["mutate", "w", "--query", q, "--target", g, "--json"]
        if params:
            args += ["--params", json.dumps(params, ensure_ascii=False)]
        if branch:
            args += ["--branch", branch]
        return self.og(side, *args, what=f"{op.get('op') or op.get('step')}:{op.get('kind', '')}")

    def run_load(self, side, g, cfg, op, mode, branch=None):
        t = cfg.node(op["type"])
        if t is None:
            return None
        rows = self.rows_for(cfg, op["type"], op["start"], op["new"], op["existing"], op["seed"])
        if op.get("step") == "load" and op["mode"] == "upsert":
            # an upsert batch names the key and one or two properties
            rng = random.Random(op["seed"] + 5)
            keep = set(t.key_props())
            out = []
            for r in rows:
                d = r["data"]
                others = [k for k in d if k not in keep]
                sel = keep | set(rng.sample(others, min(len(others), 2)))
                out.append({"type": r["type"], "data": {k: v for k, v in d.items() if k in sel}})
            rows = out
        r = None
        for ci, chunk in enumerate(chunks(rows, 8000)):
            f = G.write_jsonl(self.root / f"load-{len(self.events)}-{op['seed']}-{ci}.jsonl", chunk)
            args = ["load", "--data", f, "--mode", mode, "--target", g, "--json"]
            if branch:
                args += ["--branch", branch]
            r = self.og(side, *args, what=f"load {mode}", timeout=480)
            if not r.ok:
                break
        return r

    # ---- Part 1 ----------------------------------------------------
    def part1(self):
        cfg = self.cfg0
        b = cfg.builder
        g = self.root / "g"
        pg = self.root / "schema0.pg"
        pg.write_text(G.render(cfg))
        r = self.og(b, "init", "--schema", pg, g, what="init")
        refusable = G.gq2_refusable(cfg)
        self.event(part=1, op="init", side=b, ok=r.ok, refusable=refusable, msg=None if r.ok else C.clean(r.err)[-600:])
        if not r.ok:
            self.cov["gq2_schema_refused"] += 1
            if not refusable:
                self.event(part=1, op="init_unexpected_refusal", msg=C.clean(r.err)[-1200:])
                self.cov["gq2_schema_refused_unexplained"] += 1
            # strip what edition 2 refuses and build the rest
            for t in list(cfg.nodes) + list(cfg.edges):
                for p in t.props:
                    if p.ty == "vector":
                        p.distance = None
                    if p.ty in ("list_str", "list_i32") and p.index:
                        p.index = False
                    p.fulltext = False
            if g.exists():
                shutil.rmtree(g)
            pg.write_text(G.render(cfg))
            r = self.og(b, "init", "--schema", pg, g, what="init stripped")
            if not r.ok:
                self.event(part=1, op="init_stripped_refused", msg=C.clean(r.err)[-1200:])
                return False
        self.cur = G.clone(cfg)
        self.stored = G.clone(cfg)
        rows = G.base_rows(cfg)
        # GQ 2 refuses a load of more than 8,192 keyed entities of one type
        for ci, chunk in enumerate(chunks(rows, 8000)):
            f = G.write_jsonl(self.root / f"base-{ci}.jsonl", chunk)
            r = self.og(b, "load", "--data", f, "--mode", "merge", "--target", g, "--json", what="base load", timeout=480)
            self.event(part=1, op="base_load", ok=r.ok, rows=len(chunk), msg=None if r.ok else r.message()[-600:])
            if not r.ok:
                return False
        ops = self.script.get("gq2")
        if ops is None:
            ops = draw_gq2_script(cfg, self.seed)
        self.script["gq2"] = ops
        self.live = set()
        for op in ops:
            if time.time() - self.t0 > CASE_BUDGET * 0.4:
                self.truncated = True
                break
            self.gq2_op(b, g, op)
        return True

    def touch(self, t, what):
        for tn in ([t] if t else [n.name for n in self.cur.nodes]):
            self.idx_state.setdefault(tn, {"built": None, "after": []})
            self.idx_state[tn]["after"].append(what)

    def index_event(self, what, side):
        """GQ 2's full-text coverage: only GQ 2's own full-text rebuild, or
        its first index pass over a field it has never indexed, covers
        every row; NEW never refreshes a GQ 2 full-text index (its `index
        build` builds BTREE/NGRAM, its rebuild writes indexes GQ 2 refuses)."""
        for n in self.cur.nodes:
            s = self.idx_state.get(n.name)
            if side != "new" and (what == "fts_rebuild" or not s or not s.get("built")):
                self.idx_state[n.name] = {"built": f"{what}:{side}", "after": []}
            elif s is not None and s.get("built"):
                s["after"].append(f"{side}_{what}")
            else:
                self.idx_state[n.name] = {"built": None, "after": [f"{side}_{what}"]}
            self.cov[f"index_event:{side}:{what}"] += 1

    def state_label(self, t) -> str:
        s = self.idx_state.get(t)
        if not s or not s["built"]:
            return "never_built"
        after = {a for a in s["after"] if not a.startswith("new_") and not a.startswith("rel_index_build")
                 and not a.startswith("main_index_build")}
        if any(a.startswith("new_fts_rebuild") for a in s["after"]):
            return "rebuilt_by_new"
        if not after:
            return {"index_build": "complete", "fts_rebuild": "rebuilt", "optimize": "optimized",
                    "schema": "after_schema"}.get(s["built"].split(":")[0], "complete") + ":" + s["built"].split(":")[1]
        if "update" in after or "load_existing" in after:
            return "lagging"
        if "delete" in after:
            return "deletions"
        return "uncovered_tail"

    def gq2_op(self, b, g, op):
        kind = op["op"]
        r = None
        if op.get("type") and op["op"] in ("load", "insert") and not op.get("branch"):
            self.nkeys[op["type"]] = max(self.nkeys.get(op["type"], 0), op.get("start", op.get("key", 0)) + op.get("new", 1))
        if kind == "load":
            r = self.run_load(b, g, self.cur, op, op["mode"])
            self.touch(op["type"], "load_existing" if op["existing"] else "append")
        elif kind in ("insert", "update", "delete"):
            r = self.run_write(b, g, self.cur, op, 2, branch=op.get("branch"))
            if not op.get("branch"):
                self.touch(op["type"], kind)
        elif kind == "insert_edge":
            r = self.run_write(b, g, self.cur, op, 2)
        elif kind == "index_build":
            r = self.og(b, "index", "build", "--target", g, "--json")
            self.index_event("index_build", b)
        elif kind == "fts_rebuild":
            r = self.og(b, "index", "rebuild", "--kind", "full-text", "--target", g, "--json")
            self.index_event("fts_rebuild", b)
        elif kind == "optimize":
            r = self.og(b, "optimize", "--target", g, "--json")
        elif kind == "cleanup":
            r = self.og(b, "cleanup", "--keep", str(op["keep"]), "--confirm", "--target", g, "--json")
        elif kind == "branch_create":
            r = self.og(b, "branch", "create", op["name"], "--from", "main", "--target", g, "--json")
            if r.ok:
                self.live.add(op["name"])
        elif kind == "branch_merge":
            args = ["branch", "merge", op["name"], "--into", "main", "--target", g, "--json"]
            if op.get("delete"):
                args.insert(-3, "--delete-branch")
            r = self.og(b, *args)
            if r.ok and op.get("delete"):
                self.live.discard(op["name"])
            self.touch(None, "append")
        elif kind == "branch_delete":
            r = self.og(b, "branch", "delete", op["name"], "--target", g, "--json")
            if r.ok:
                self.live.discard(op["name"])
        elif kind == "evolve":
            nxt = apply_edits(self.cur, op["edits"])
            if nxt is None:
                self.event(part=1, op="evolve_skipped", kind=op["kind"])
                return
            pg = self.root / f"evolve-{len(self.events)}.pg"
            pg.write_text(G.render(nxt))
            r = self.og(b, "schema", "apply", "--schema", pg, "--target", g, "--json")
            if r.ok:
                self.cur = settle(nxt)
                self.stored = G.clone(self.cur)
                for t in self.cur.nodes:
                    self.nkeys.setdefault(t.name, 0)
        if r is not None:
            self.cov[f"gq2_op:{kind}:{'ok' if r.ok else 'refused'}"] += 1
            self.event(part=1, op=kind, side=b, ok=r.ok, msg=None if r.ok else r.message()[-400:])

    # ---- reads -----------------------------------------------------
    def read_files(self, cfg: G.Config):
        """The read set for a schema: hand GQ 3, edition 2, and lint --fix's respelling."""
        key = C.sha(G.render(cfg))
        d = self.root / "reads" / key
        if (d / "meta.json").exists():
            return json.loads((d / "meta.json").read_text()), d
        d.mkdir(parents=True, exist_ok=True)
        rs = Q.reads(cfg)
        (d / "e3.gq").write_text(Q.gq_file(rs, 3))
        (d / "e2.gq").write_text(Q.gq_file(rs, 2))
        (d / "fixed.gq").write_text(Q.gq_file(rs, 2))
        (d / "s.pg").write_text(G.render(cfg))
        lint = self.og("new", "lint", "--fix", "--query", d / "fixed.gq", "--schema", d / "s.pg", "--json",
                       what="lint --fix")
        fixed_text = (d / "fixed.gq").read_text()
        meta = {"reads": [r.__dict__ for r in rs], "lint_rc": lint.rc,
                "lint_findings": [x.get("code") for x in ((lint.json() or {}).get("findings") or [])][:40],
                "fixed_changed": fixed_text != (d / "e2.gq").read_text()}
        (d / "meta.json").write_text(json.dumps(meta))
        return meta, d

    def run_reads(self, side, g, cfg, which: str, only=None) -> dict:
        meta, d = self.read_files(cfg)
        f = d / {"e3": "e3.gq", "e2": "e2.gq", "fixed": "fixed.gq"}[which]
        out = {}
        for rd in meta["reads"]:
            if only and not only(rd):
                continue
            if which != "e3" and rd["e2"] is None:
                continue
            args = ["query", rd["name"], "--query", f, "--target", g, "--format", "arrow"]
            if rd["params"]:
                args += ["--params", json.dumps(rd["params"], ensure_ascii=False)]
            r = self.og(side, *args, binary=True, what=f"read {which}:{rd['name']}")
            out[rd["name"]] = _obs(r, rd["ordered"])
        return out

    # ---- export ----------------------------------------------------
    def export(self, side, g, cfg=None):
        r = self.og(side, "export", "--target", g, what="export", timeout=300)
        rows = C.parse_jsonl(r.out) if r.ok else []
        return r, rows

    def canon(self, rows, cfg=None):
        cfg = cfg or self.cur
        unkeyed = {n.name for n in cfg.nodes if not n.key_props()}
        keyed_edges = {e.name for e in cfg.edges if e.keyed}
        return C.canonical_export(rows, unkeyed, keyed_edges)

    # ---- Part 2 ----------------------------------------------------
    def part2(self):
        g0 = self.root / "g"
        g = self.root / "u"
        lib.copy_graph(g0, g)
        builder = self.cfg0.builder
        steps = self.script.get("new")
        if steps is None:
            cfg = G.clone(self.cur)
            cfg.sizes = {t.name: max(self.nkeys.get(t.name, 0), 1) + 5 for t in cfg.nodes}
            steps = draw_new_script(cfg, sorted(self.live), builder, self.seed)
        self.script["new"] = steps
        # the starting index state of every lexical field
        for t in self.cur.nodes:
            for p in t.props:
                if p.ty == "String" and (p.index or p.key):
                    self.cov[f"start_state:{self.state_label(t.name)}"] += 1
        self.observe(g, {"step": "start"})
        for st in steps:
            if time.time() - self.t0 > CASE_BUDGET:
                self.truncated = True
                break
            ok = self.new_step(g, st)
            self.hist.append({**st, "ok": ok})
            self.observe(g, st)
        self.final(g)

    def delete_branches(self, side, g):
        r = self.og(side, "branch", "list", "--target", g, "--json")
        names = []
        j = r.json() or {}
        for b in j.get("branches") or []:
            nm = b if isinstance(b, str) else (b.get("name") or b.get("branch"))
            if nm and nm != "main":
                names.append(nm)
        for nm in names:
            self.og(side, "branch", "delete", nm, "--target", g, "--json")
        return names

    def new_step(self, g, st) -> bool:
        k = st["step"]
        self.cov[f"new_step:{k}"] += 1
        pre = None
        if k in ("write", "load"):
            pre = self.root / "pre"
            lib.copy_graph(g, pre)
        r = None
        if k == "ro_open":
            before = lib.tree_digest(g)
            r = self.og("new", "snapshot", "--target", g, "--json")
            for extra in (["schema", "show", "--target", g], ["branch", "list", "--target", g, "--json"],
                          ["commit", "list", "--target", g, "--json"]):
                self.og("new", *extra)
            if lib.tree_digest(g) != before:
                self.finding("ro_open_wrote", 5, "read-only commands changed files", "ro_open", {})
        elif k == "rw_open":
            t = self.cur.nodes[0] if self.cur.nodes else None
            if t is not None:
                q = self.mq(f'query w() {{\n  match {{ $x: {t.name} }} where false\n  delete $x\n}}')
                r = self.og("new", "mutate", "w", "--query", q, "--target", g, "--json")
        elif k == "write":
            r = self.run_write("new", g, self.cur, st, 3)
            self.touch(st.get("type"), st["kind"])
            if r is not None:
                self.mirror_write(pre, g, st, r)
        elif k == "load":
            r = self.run_load("new", g, self.cur, st, st["mode"])
            self.touch(st["type"], "load_existing" if st["existing"] else "append")
            if r is not None:
                self.mirror_write(pre, g, st, r)
        elif k == "index_build":
            r = self.og("new", "index", "build", "--target", g, "--json")
            self.index_event("index_build", "new")
            if r.ok:
                pend = (r.json() or {}).get("pending_indexes") or []
                for p in pend:
                    txt = json.dumps(p)
                    if "rebuild --kind full-text" in txt or "full-text" in txt:
                        self.event(op="index_build_pending_fts", pending=p)
                        self.cov["index_build_advised_fts_rebuild"] += 1
        elif k == "fts_rebuild":
            r = self.og("new", "index", "rebuild", "--kind", "full-text", "--target", g, "--json")
            self.index_event("fts_rebuild", "new")
        elif k == "optimize":
            r = self.og("new", "optimize", "--target", g, "--json")
        elif k == "cleanup":
            r = self.og("new", "cleanup", "--keep", str(st["keep"]), "--confirm", "--target", g, "--json")
        elif k == "branch":
            if st.get("type") is None:
                return False
            r = self.og("new", "branch", "create", st["name"], "--from", "main", "--target", g, "--json")
            if r.ok:
                w = self.run_write("new", g, self.cur, {**st, "kind": "insert", "step": "write"}, 3, branch=st["name"])
                if st.get("merge"):
                    args = ["branch", "merge", st["name"], "--into", "main", "--target", g, "--json"]
                    r = self.og("new", *args)
                    self.touch(st["type"], "append")
                if st.get("delete"):
                    self.og("new", "branch", "delete", st["name"], "--target", g, "--json")
        elif k == "merge_gq2":
            args = ["branch", "merge", st["name"], "--into", "main", "--target", g, "--json"]
            r = self.og("new", *args)
            self.touch(None, "append")
            if r.ok and st.get("delete"):
                self.og("new", "branch", "delete", st["name"], "--target", g, "--json")
        elif k == "upgrade":
            r0 = self.og("new", "upgrade", "--check", "--target", g, "--json")
            r = self.og("new", "upgrade", "--target", g, "--json")
            self.event(op="upgrade", check=(r0.json() or {}).get("outcome"), outcome=(r.json() or {}).get("outcome"))
        elif k == "schema":
            return self.schema_step(g, st)
        ok = bool(r is not None and r.ok)
        if k in ("index_build", "fts_rebuild", "optimize", "cleanup") and r is not None and not r.ok \
                and "changed during" not in r.message():
            self.finding("new_maintenance_refused", 5, f"NEW {k} refused: {C.clean(r.message())[-120:]}", k,
                         {"msg": C.clean(r.message())[-1200:], "err": C.clean(r.err)[-1500:],
                          "history": [h.get("step") + ":" + str(h.get("kind", h.get("mode", ""))) for h in self.hist]})
        self.event(op=k, ok=ok, sub=st.get("kind") or st.get("mode"),
                   msg=None if (r is None or r.ok) else r.message()[-500:])
        return ok

    def mirror_write(self, pre: Path, g: Path, st: dict, r_new):
        """Oracle 3 for writes: the same intent by GQ2-MAIN on the pre-step
        graph leaves the same export as NEW's write."""
        k = st["step"]
        side = "main"
        if G.one_way_constructs(self.stored):
            self.cov["mirror_skipped_one_way"] += 1
            return
        mg = self.root / "mirror"
        lib.copy_graph(pre, mg)
        if k == "write":
            if st["kind"] == "upsert":
                return
            rm = self.run_write(side, mg, self.cur, st, 2)
            label = f"write:{st['kind']}"
        else:
            m = {"insert": "append", "replace": "merge", "overwrite": "overwrite"}.get(st["mode"])
            if m is None:
                return
            rm = self.run_load(side, mg, self.cur, st, m)
            label = f"load:{st['mode']}~{m}"
        if rm is None:
            return
        en = self.canon(self.export("new", g)[1])
        em = self.canon(self.export("new", mg)[1])
        same = en == em
        status = (r_new.ok, rm.ok)
        self.cov[f"mirror:{label}:{'same' if same else 'differ'}"] += 1
        if status[0] != status[1] or not same:
            # sanctioned: an edition-2 keyed insert replaced; NEW insert on an existing key is key_conflict
            sanctioned = None
            if st.get("kind") == "insert" and not r_new.ok and ("key_conflict" in r_new.message() or "already has id" in r_new.message()):
                sanctioned = "writes.keyed-insert"
            if st.get("step") == "load" and st["mode"] == "insert" and not r_new.ok and rm.ok is False:
                sanctioned = "both refused"
            if sanctioned is None:
                diff = sorted(set(en) ^ set(em))[:6]
                self.finding("write_mirror", 3, f"{label} new_ok={status[0]} gq2_ok={status[1]} same={same}",
                             label, {"new_msg": None if r_new.ok else r_new.message()[-500:],
                                     "gq2_msg": None if rm.ok else rm.message()[-500:], "diff": diff,
                                     "op": st})

    def schema_step(self, g, st) -> bool:
        nxt = apply_edits(self.cur, st["edits"])
        sub = st["kind"]
        self.cov[f"new_schema:{sub}"] += 1
        if nxt is None:
            self.event(op="schema", sub=sub, skipped="target gone")
            self.cov[f"new_schema_skipped:{sub}"] += 1
            return False
        deleted = self.delete_branches("new", g)
        pg = self.root / f"new-schema-{len(self.events)}.pg"
        pg.write_text(G.render(nxt, edition=3))
        plan = self.og("new", "schema", "plan", "--schema", pg, "--target", g, "--json")
        pj = plan.json() or {}
        app = self.og("new", "schema", "apply", "--schema", pg, "--target", g, "--json")
        aj = app.json() or {}
        listed = {(c.get("construct"), c.get("type_name"), c.get("property")) for c in (pj.get("one_way_constructs") or [])}
        listed_apply = {(c.get("construct"), c.get("type_name"), c.get("property")) for c in (aj.get("one_way_constructs") or [])}
        expect = G.one_way_constructs(settle(nxt))
        applied = bool(app.ok and aj.get("applied"))
        self.event(op="schema", sub=sub, plan_rc=plan.rc, steps=[s.get("kind") for s in pj.get("steps") or []],
                   one_way=sorted(map(list, listed)), apply_rc=app.rc, applied=applied,
                   apply_steps=aj.get("step_count"), msg=None if app.ok else app.message()[-600:],
                   deleted_branches=deleted)
        if plan.ok and not pj.get("steps") and not listed and expect:
            self.cov["one_way_unlisted_on_zero_step_plan"] += 1
            self.finding("one_way_listing_noop", 4, "a plan with no step lists none of the one-way constructs the schema holds",
                         f"schema {sub}", {"expected": sorted(map(list, expect))})
        elif plan.ok and listed != expect:
            self.finding("one_way_listing", 4, f"plan lists {sorted(listed - expect)} extra / misses {sorted(expect - listed)}",
                         f"schema {sub}", {"listed": sorted(map(list, listed)), "expected": sorted(map(list, expect)),
                                           "schema": G.render(nxt, 3)})
        if app.ok and listed_apply != listed:
            self.finding("one_way_listing", 4, "apply lists other constructs than plan", f"schema {sub}",
                         {"plan": sorted(map(list, listed)), "apply": sorted(map(list, listed_apply))})
        if app.ok:
            self.cur = settle(nxt)
            if applied or (aj.get("step_count") or 0) > 0:
                self.stored = G.clone(self.cur)
            for t in self.cur.nodes:
                self.nkeys.setdefault(t.name, 0)
            self.index_event("schema", "new") if applied else None
        self.cov[f"new_schema_result:{sub}:{'applied' if applied else ('noop' if app.ok else 'refused')}"] += 1
        return app.ok

    # ---- observation -------------------------------------------------
    def observe(self, g, st, label=None):
        label = label or f"{len(self.hist)}:{st.get('step')}:{st.get('kind', st.get('mode', ''))}"
        snap = self.og("new", "snapshot", "--target", g, "--json")
        if not snap.ok:
            self.finding("new_cannot_open", 5, "NEW refuses its own graph", label, {"msg": snap.message()[-800:]})
            return
        stamp = C.stamp_of(snap)
        rn, rows_n = self.export("new", g)
        content = C.sha("\n".join(self.canon(rows_n)))
        sem = semantic_key(self.cur)
        new_reads = self.run_reads("new", g, self.cur, "e3")
        meta, _ = self.read_files(self.cur)
        if meta["fixed_changed"]:
            fixed = self.run_reads("new", g, self.cur, "fixed")
            for name, o in fixed.items():
                h = new_reads.get(name)
                if h is None:
                    continue
                if o.get("code") in ("Q017", "Q010", "Q016", "Q012", "Q008", "T65", "T11", "T6"):
                    self.cov["fixed_left_refused"] += 1
                    continue
                if not _same(h, o, multiset=not o.get("ordered")) and not (
                        h.get("rows") == o.get("rows") and h.get("types") == o.get("types")):
                    self.finding("fixed_vs_hand", 3, f"lint --fix respelling answers differently ({name.split('_')[0]})",
                                 label, {"read": name, "hand": _brief(h), "fixed": _brief(o)})
        for t in self.cur.nodes:
            self.cov[f"obs_state:{self.state_label(t.name)}"] += 1
        self.check_group(sem, content, new_reads, label, g)
        self.check_reference(sem, content, new_reads, rows_n, label)
        # GQ 2 probes
        if os.environ.get("UF_NO_PROBE") == "1":
            return
        for side in C.GQ2:
            if side == "rel" and self.cfg0.builder == "main":
                continue
            self.probe(side, g, label, stamp, new_reads, rows_n, content, sem)

    def check_reference(self, sem, content, reads, rows_n, label):
        """Oracle 2 against the reference profile, once per semantic state."""
        import ufanalyzer
        key = ("ref", sem, content)
        if key in self.groups:
            return
        self.groups[key] = True
        meta, _ = self.read_files(self.cur)
        for rd in meta["reads"]:
            o = reads.get(rd["name"])
            if o is None:
                continue
            try:
                bad = ufanalyzer.check_new(rd, o, self.cur, rows_n)
            except Exception as e:
                self.event(op="reference_error", read=rd["name"], err=str(e)[-300:])
                continue
            if bad is None:
                continue
            self.cov[f"oracle2_reference:{rd['kind']}:{'ok' if not bad else 'mismatch'}"] += 1
            if bad:
                prof = bad.get("profile")
                self.finding("profile_reference_mismatch", 2, f"{rd['kind']} {prof}", label,
                             {"read": rd["name"], "params": rd["params"], "ref": bad})

    def check_group(self, sem, content, reads, label, g, kind="state"):
        key = (sem, content)
        if key not in self.groups:
            self.groups[key] = {"label": label, "reads": reads}
            return
        base = self.groups[key]
        for name, o in reads.items():
            b = base["reads"].get(name)
            if b is None:
                continue
            self.cov["oracle1_compared"] += 1
            if not _same(b, o):
                ctx = {"read": name, "first": base["label"], "then": label, "first_obs": _brief(b, 12),
                       "then_obs": _brief(o, 12), "via": kind,
                       "history": [h.get("step") + ":" + str(h.get("kind", h.get("mode", ""))) for h in self.hist]}
                self.finding("index_state_changed_answer", 1, f"{name.split('_')[0]} {kind}", label, ctx)

    def probe(self, side, g, label, stamp, new_reads, rows_n, content, sem):
        lo, hi = C.STAMP_SERVES[side]
        snap = self.og(side, "snapshot", "--target", g, "--json")
        one_way = G.one_way_constructs(self.stored)
        expect_whole = bool(one_way) or (stamp is not None and not (lo <= stamp <= hi))
        status = "ok"
        if not snap.ok:
            status = "whole_refused"
            msg = snap.message()
            self.cov[f"rollback:{side}:whole_refused"] += 1
            if not expect_whole:
                self.finding("rollback_whole_unlisted", 4, f"{side} refuses the whole graph with no one-way construct stored",
                             label, {"msg": msg[-800:], "stamp": stamp, "stored": G.render(self.stored, 3),
                                     "history": [h.get("step") + ":" + str(h.get("kind", "")) for h in self.hist]})
            self.event(op="probe", side=side, label=label, status=status, msg=msg[-300:], one_way=sorted(map(list, one_way)),
                       stamp=stamp)
            self.note_rollback(side, status, "stamp" if (stamp is not None and not (lo <= stamp <= hi)) else
                               ("one_way" if one_way else "unexplained"))
            return
        if expect_whole:
            self.finding("rollback_listed_not_refused", 4, f"{side} opens a graph storing {sorted(one_way)[:3]} (stamp {stamp})",
                         label, {"stored": G.render(self.stored, 3)})
        reads = self.run_reads(side, g, self.cur, "e2")
        lex_refused = [n for n, o in reads.items() if o.get("code") == "full_text_index_rebuild_required"]
        other_refused = [n for n, o in reads.items() if o.get("error") and o.get("code") not in
                         ("full_text_index_rebuild_required", "T27", "T20", "T87")]
        if lex_refused:
            status = "fts_refused"
            named = [h for h in self.hist if h.get("step") in GUIDE_FTS_STEPS]
            if not named:
                self.finding("rollback_fts_unnamed", 4, f"{side} refuses full-text reads after no step the guide names",
                             label, {"reads": lex_refused, "history": [h.get("step") for h in self.hist]})
        self.cov[f"rollback:{side}:{status}"] += 1
        self.note_rollback(side, status, "")
        # oracle 3: GQ 2 against NEW, read by read
        self.compare(side, reads, new_reads, label, rows_n)
        # oracle 5: exports of one commit equal across binaries
        ro, rows_o = self.export(side, g)
        if ro.ok and rows_n:
            co, cn = sorted(json.dumps(r, sort_keys=True) for r in rows_o), sorted(json.dumps(r, sort_keys=True) for r in rows_n)
            if co != cn:
                diff = sorted(set(co) ^ set(cn))[:6]
                self.finding("export_differs", 5, f"{side} export differs from NEW's", label,
                             {"diff": diff, "n_old": len(co), "n_new": len(cn)})
        elif not ro.ok:
            self.finding("gq2_export_refused", 4, f"{side} opens the graph but export refuses", label,
                         {"msg": ro.message()[-600:]})
        # a GQ 2 write on a copy
        wg = self.root / f"probe-{side}"
        lib.copy_graph(g, wg)
        t = next((n for n in self.cur.nodes if not any(p.ty == "blob" and not p.nullable for p in n.props)), None)
        if t is not None:
            op = {"op": "insert", "type": t.name, "key": self.nkeys.get(t.name, 0) + 700 + len(self.hist), "seed": self.seed}
            w = self.run_write(side, wg, self.cur, op, 2)
            if w is not None:
                self.cov[f"probe_write:{side}:{'ok' if w.ok else 'refused'}"] += 1
                if not w.ok and not any(x in w.message() for x in ("key_conflict", "@range", "@unique", "@card")):
                    self.finding("gq2_write_refused", 4, f"{side} reads but cannot write", label,
                                 {"msg": w.message()[-800:], "status": status})
        # GQ 2's own index pass and full-text rebuild on another copy, then
        # NEW's reads there (an index state GQ 2 rebuilt) and GQ 2's lexical reads
        if PROBE_REBUILD and (side == "main" or lex_refused or random.Random(self.seed + len(self.hist)).random() < 0.5):
            lib.copy_graph(g, wg)
            ib = self.og(side, "index", "build", "--target", wg, "--json", what=f"{side} index build (probe)")
            fr = self.og(side, "index", "rebuild", "--kind", "full-text", "--target", wg, "--json",
                         what=f"{side} fts rebuild (probe)")
            self.cov[f"probe_gq2_index_build:{side}:{'ok' if ib.ok else 'refused'}"] += 1
            self.cov[f"probe_gq2_fts_rebuild:{side}:{'ok' if fr.ok else 'refused'}"] += 1
            if not ib.ok:
                self.event(op="probe_index_build_refused", side=side, label=label, msg=ib.message()[-400:])
            if lex_refused:
                again = self.run_reads(side, wg, self.cur, "e2", only=lambda rd: rd["name"] in lex_refused)
                still = [n for n, o in again.items() if o.get("error") and o.get("code") not in ("T27", "T20")]
                self.cov[f"rollback_fts_restored:{side}:{'no' if still else 'yes'}"] += 1
                if self.rollback:
                    self.rollback[-1].append("restored" if not still else "not_restored")
                if still:
                    self.finding("rollback_fts_not_restored", 4, f"{side}'s own full-text rebuild does not restore its lexical reads",
                                 label, {"still": still, "rebuild": fr.message()[-500:] if not fr.ok else "ok",
                                         "index_build": ib.message()[-400:] if not ib.ok else "ok",
                                         "obs": {n: _brief(again[n]) for n in still[:3]}})
            nr = self.run_reads("new", wg, self.cur, "e3")
            rows2 = self.export("new", wg)[1]
            c2 = C.sha("\n".join(self.canon(rows2)))
            if c2 != content:
                self.finding("gq2_index_pass_changed_content", 1, f"{side} index build/rebuild changed NEW's export", label,
                             {"diff": sorted(set(self.canon(rows2)) ^ set(self.canon(rows_n)))[:6]})
            else:
                self.check_group(sem, content, nr, label + f"+{side}-rebuilt", wg, kind=f"after {side} index build+rebuild")
        shutil.rmtree(wg, ignore_errors=True)

    def note_rollback(self, side, status, why):
        st = self.hist[-1] if self.hist else {"step": "start"}
        self.rollback.append([len(self.hist), st.get("step"), st.get("kind") or st.get("mode") or "", side, status, why])

    def compare(self, side, old_reads, new_reads, label, rows_n=None):
        import ufclassify
        meta, _ = self.read_files(self.cur)
        byname = {r["name"]: r for r in meta["reads"]}
        for name, o in old_reads.items():
            n = new_reads.get(name)
            if n is None:
                continue
            rd = byname[name]
            verdict = ufclassify.classify(rd, o, n, self.state_label(rd["type_name"]), self.cur, rows_n)
            self.cov[f"oracle3:{side}:{verdict['class']}"] += 1
            if verdict["class"] not in ("same", "a"):
                self.finding("gq2_new_difference", 3, verdict["sig"], label,
                             {"side": side, "read": name, "verdict": verdict, "old": _brief(o, 10), "new": _brief(n, 10),
                              "state": self.state_label(rd["type_name"]),
                              "field": f"{rd['type_name']}.{rd['prop']}", "params": rd["params"]})

    # ---- end of case ---------------------------------------------------
    def final(self, g):
        """A fresh graph from NEW's export answers as the upgraded graph does."""
        rn, rows = self.export("new", g)
        if not rn.ok:
            return
        self.fresh_check(g, rows, "final")

    def fresh_check(self, g, rows, label):
        fg = self.root / "fresh"
        if fg.exists():
            shutil.rmtree(fg)
        pg = self.root / "fresh.pg"
        pg.write_text(G.render(self.cur, 3))
        r = self.og("new", "init", "--schema", pg, fg, what="fresh init")
        if not r.ok:
            self.event(op="fresh_init_refused", msg=r.message()[-400:])
            return
        for ci, chunk in enumerate(chunks(rows, 8000)):
            f = self.root / f"fresh-{ci}.jsonl"
            f.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in chunk) + "\n")
            r = self.og("new", "load", "--data", f, "--mode", "insert", "--target", fg, "--json", what="fresh load", timeout=480)
            if not r.ok:
                break
        if not r.ok:
            self.finding("fresh_load_refused", 5, "NEW cannot load its own export into a fresh graph", label,
                         {"msg": r.message()[-800:]})
            return
        a = self.run_reads("new", g, self.cur, "e3")
        b = self.run_reads("new", fg, self.cur, "e3")
        meta, _ = self.read_files(self.cur)
        byname = {r["name"]: r for r in meta["reads"]}
        self.cov["oracle1_fresh"] += 1
        for name, o in a.items():
            p = b.get(name)
            if p is None:
                continue
            ms = not byname[name]["keyed"]
            if not _same(o, p, multiset=ms):
                self.finding("fresh_graph_differs", 1, f"{name.split('_')[0]} fresh", label,
                             {"read": name, "upgraded": _brief(o, 12), "fresh": _brief(p, 12),
                              "history": [h.get("step") + ":" + str(h.get("kind", h.get("mode", ""))) for h in self.hist]})

    # ---- driver --------------------------------------------------------
    def run(self) -> dict:
        status = "ok"
        err = None
        try:
            if self.part1():
                self.part2()
            else:
                status = "build_failed"
        except Exception as e:  # a harness error, recorded and counted
            status = "harness_error"
            err = traceback.format_exc()[-3000:]
        rec = {"seed": self.seed, "status": status, "error": err, "secs": round(time.time() - self.t0, 1),
               "builder": self.cfg0.builder, "stress": self.cfg0.stress, "truncated": self.truncated,
               "schema": G.render(self.cfg0), "sizes": self.cfg0.sizes, "script": self.script,
               "findings": self.findings, "coverage": dict(self.cov), "invocations": dict(self.invocations),
               "events": self.events[-400:], "hist": self.hist, "rollback": self.rollback,
               "fts_restored": [e for e in self.events if e.get("op") == "fts_restore"]}
        (self.root / "case.json").write_text(json.dumps(rec, default=str))
        return rec
