"""Random core terms over random small graphs, checking the core's stated
rules. Run through `run.py`, or `python3 -m gq3.stress --terms N --seed S`.

Five kinds of term, interleaved:

  generic   a random operator tree, depth <= 4: termination, every operator's
            bound (checked inside the evaluator), determinism of content and of
            order across physical orders, Union refusal against an oracle
  fix       a random recursive read: naive (Kleene) and semi-naive agree on
            every term the proposed admission rule accepts
  desugar   a random surface read: its desugaring equals the core term built
            directly from the derived-forms table
  cut       `from { … order … limit k }` under an outer filter: the result never
            widens the inner cut
  union     two random arms: the typer accepts exactly when an independent
            positional oracle says the column types agree
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from collections import Counter
from dataclasses import replace

from . import core as C
from .core import (Agg, And, Arith, Cmp, Col, Correlate, Cut, Env, Expand, Filter, Fix, Fuse, Group,
                   IsNull, Lit, Not, Or, Order, Param, Project, Prop, RecRef, Scan, Score, ApproxTopK,
                   Union, lit)
from .fixtures import section8_schema
from .model import BOOL, BoundViolation, F64, GQError, Graph, I64, STRING, T
from . import surface as Sf
from .dsl import bind, inline, read, tri, where, pr, v, L
from .surface import SBlock, SExists, SFrom, SLet, SNot

WORDS = ["graph", "rust", "search", "data", "notes", "vector", "query", "plan"]
CITIES = ["Berlin", "Paris", "Rome", None]
STRESS = replace(C.PROPOSED, fix_hard_cap=300)
RFC_TIES = replace(STRESS, order_ties="rfc")
RFC_ADMIT = replace(STRESS, fix_admission="rfc")

EDGES_FROM = {
    "Person": [("Knows", "out", "Person"), ("Knows", "in", "Person"), ("Authored", "out", "Doc"), ("WorksAt", "out", "Company")],
    "Doc": [("Cites", "out", "Doc"), ("Cites", "in", "Doc"), ("Authored", "in", "Person"), ("In", "out", "Collection")],
    "Company": [("WorksAt", "in", "Person")],
    "Collection": [("In", "in", "Doc")],
}
PROPS = {
    "Person": [("handle", "String"), ("name", "String"), ("age", "I32"), ("city", "String")],
    "Doc": [("id", "String"), ("title", "String"), ("year", "I32")],
    "Company": [("slug", "String"), ("name", "String")],
    "Collection": [("slug", "String"), ("min_docs", "I32")],
}


# ====================================================================== graphs

def random_graph(rng: random.Random) -> Graph:
    g = Graph(section8_schema(rng.choice(["l2", "cosine"])))
    persons = [f"p{i}" for i in range(rng.randint(2, 6))]
    docs = [f"d{i}" for i in range(rng.randint(2, 6))]
    comps = [f"c{i}" for i in range(rng.randint(1, 3))]
    colls = [f"k{i}" for i in range(rng.randint(1, 2))]
    for h in persons:
        g.add_node("Person", handle=h, name=rng.choice(["Ann", "Bo", "Cy"]), age=rng.choice([None, 20, 30, 30, 70]),
                   city=rng.choice(CITIES))
    for d in docs:
        body = " ".join(rng.choice(WORDS) for _ in range(rng.randint(0, 5)))
        emb = None if rng.random() < 0.2 else tuple(float(rng.randint(0, 2)) for _ in range(3))
        if emb == (0.0, 0.0, 0.0):
            emb = (1.0, 0.0, 0.0)
        g.add_node("Doc", id=d, title=rng.choice(WORDS), body=body, year=rng.choice([2023, 2024, 2025]),
                   tags=tuple(rng.sample(WORDS, rng.randint(0, 2))), embedding=emb)
    for c in comps:
        g.add_node("Company", slug=c, name=rng.choice(["Acme", "Globex"]))
    for k in colls:
        g.add_node("Collection", slug=k, min_docs=rng.randint(0, 3))
    for _ in range(rng.randint(0, 10)):
        g.add_edge("knows", rng.choice(persons), rng.choice(persons))       # self-loops and parallels allowed
    for _ in range(rng.randint(0, 8)):
        g.add_edge("authored", rng.choice(persons), rng.choice(docs))
    for _ in range(rng.randint(0, 8)):
        g.add_edge("cites", rng.choice(docs), rng.choice(docs))
    for p_ in persons:
        if rng.random() < 0.7:
            g.add_edge("worksAt", p_, rng.choice(comps))
    for d in docs:
        if rng.random() < 0.6:
            g.add_edge("in", d, rng.choice(colls))
    return g


# ====================================================================== generic terms

class Gen:
    def __init__(self, rng: random.Random, schema):
        self.rng = rng
        self.schema = schema
        self.n = 0

    def fresh(self, base="c"):
        self.n += 1
        return f"{base}{self.n}"

    def cols(self, t, gamma=()):
        return C.type_term(t, C.TEnv(self.schema, {}, tuple(gamma), cfg=STRESS))

    # scalar expressions over a column list
    def scalar(self, cols, want=None):
        rng = self.rng
        choices = []
        for n, t in cols:
            if t.kind == "Node":
                for p_, k in PROPS.get(t.arg, []):
                    choices.append((Prop(Col(n), p_), k))
                choices.append((C.Meta(Col(n), "@id"), "String"))
            elif t.kind in ("I32", "I64"):
                choices.append((Col(n), "I64"))
            elif t.kind == "String":
                choices.append((Col(n), "String"))
            elif t.kind in ("F32", "F64"):
                choices.append((Col(n), "F64"))
            elif t.kind == "Edge":
                choices.append((C.Meta(Col(n), "@src"), "String"))
        if want:
            choices = [c for c in choices if c[1] == want or (want == "I64" and c[1] == "I32")]
        if not choices:
            return None, None
        e, k = rng.choice(choices)
        if k in ("I32", "I64") and rng.random() < 0.3:
            e = Arith(rng.choice(["+", "-", "*"]), e, lit(rng.randint(0, 3)))
            k = "I64"
        return e, k

    def pred(self, cols, depth=0):
        rng = self.rng
        e, k = self.scalar(cols)
        if e is None:
            return lit(True)
        r = rng.random()
        if r < 0.15:
            p_ = IsNull(e, rng.random() < 0.5)
        elif k in ("I32", "I64"):
            p_ = Cmp(rng.choice(["=", "!=", "<", "<=", ">", ">="]), e, lit(rng.choice([0, 20, 25, 30, 2024])))
        elif k == "String":
            p_ = Cmp(rng.choice(["=", "!="]), e, lit(rng.choice(WORDS + ["Berlin", "Ann", "p1", "d1"])))
        else:
            p_ = Cmp(">=", e, lit(0.5))
        if depth < 1 and rng.random() < 0.3:
            other = self.pred(cols, depth + 1)
            p_ = rng.choice([And, Or])(p_, other)
        if rng.random() < 0.1:
            p_ = Not(p_)
        return p_

    def node_cols(self, cols, types=None):
        return [(n, t) for n, t in cols if t.kind == "Node" and (types is None or t.arg in types)]

    def leaf(self, gamma):
        if gamma and self.rng.random() < 0.3:
            return Env()
        return Scan(self.rng.choice(["Person", "Doc", "Person", "Doc", "Company", "Collection"]), self.fresh("n"))

    def rel(self, depth, gamma=()):
        rng = self.rng
        if depth <= 0:
            return self.leaf(gamma)
        op = rng.choice(["expand", "expand", "filter", "filter", "project", "group", "correlate", "union",
                         "order", "cut", "cut", "score", "topk", "fuse"])
        src = self.rel(depth - 1, gamma)
        try:
            cols = self.cols(src, gamma)
        except GQError:
            return src            # an ill-typed subterm: the check refuses the whole term
        if op == "expand":
            nodes = self.node_cols(cols)
            if not nodes:
                return src
            x, xt = rng.choice(nodes)
            opts = EDGES_FROM.get(xt.arg, [])
            if not opts:
                return src
            e, d, end = rng.choice(opts)
            sel = ((e, d),)
            same = [o for o in opts if o[2] == end and o != (e, d, end)]
            if same and rng.random() < 0.3:
                sel = sel + ((same[0][0], same[0][1]),)
            m = n = 1
            if end == xt.arg and rng.random() < 0.5:
                m = rng.randint(1, 2)
                n = rng.randint(m, 3)
            k = self.fresh("k") if m == n == 1 and rng.random() < 0.4 else None
            return Expand(src, x, sel, m, n, self.fresh("y"), k)
        if op == "filter":
            return Filter(src, self.pred(cols))
        if op == "project":
            keep = [c for c in cols if rng.random() < 0.6]
            items = [(self.fresh("v"), Col(n)) for n, _ in keep]
            e, _ = self.scalar(cols)
            if e is not None and rng.random() < 0.6:
                items.append((self.fresh("v"), e))
            if not items:
                items = [(self.fresh("v"), lit(1))]
            return Project(src, tuple(items))
        if op == "group":
            keys = []
            for _ in range(rng.randint(0, 2)):
                e, _ = self.scalar(cols)
                if e is not None:
                    keys.append((self.fresh("g"), e))
            nodes = self.node_cols(cols)
            if nodes and rng.random() < 0.4:
                keys.append((self.fresh("g"), Col(rng.choice(nodes)[0])))
            aggs = [(self.fresh("a"), Agg("count"))]
            e, k = self.scalar(cols)
            if e is not None:
                fn = rng.choice(["min", "max", "count"] + (["sum", "avg"] if k in ("I32", "I64") else []))
                aggs.append((self.fresh("a"), Agg(fn, e, fn == "count" and rng.random() < 0.3)))
            return Group(src, tuple(keys), tuple(aggs))
        if op == "correlate":
            inner = self.inner(depth - 1, tuple(dict(list(gamma) + list(cols)).items()))
            return Correlate(src, inner)
        if op == "union":
            return Union(src, self.union_arm(src, cols, depth, gamma))
        if op == "order":
            keys = []
            for _ in range(rng.randint(0, 2)):
                e, _ = self.scalar(cols)
                if e is not None:
                    keys.append((e, rng.choice(["asc", "desc"]), rng.choice([None, None, "first", "last"])))
            return Order(src, tuple(keys))
        if op == "cut":
            if rng.random() < 0.6:
                keys = []
                e, _ = self.scalar(cols)
                if e is not None:
                    keys.append((e, rng.choice(["asc", "desc"]), None))
                src = Order(src, tuple(keys))
            return Cut(src, lit(rng.randint(0, 4)))
        if op in ("score", "topk", "fuse"):
            pop = self.population(src, cols)
            if pop is None:
                return src
            return self.ranking(op, pop)
        return src

    def population(self, src, cols):
        docs = self.node_cols(cols, {"Doc"})
        if not docs:
            return None
        x = self.rng.choice(docs)[0]
        extra = [(n, Col(n)) for n, t in cols if t.kind not in ("Node",) and self.rng.random() < 0.3]
        return Project(src, ((x, Col(x)),) + tuple(extra))

    def ranking(self, op, pop):
        rng = self.rng
        qv = lit(tuple(float(rng.randint(0, 2)) for _ in range(3)))
        if qv.value == (0.0, 0.0, 0.0):
            qv = lit((1.0, 0.0, 0.0))
        qt = lit(rng.choice(WORDS))
        if op == "score":
            if rng.random() < 0.5:
                return Score(pop, "l2", "embedding", qv, self.fresh("s"))
            return Score(pop, "bm25", "body", qt, self.fresh("s"))
        if op == "topk":
            return ApproxTopK(pop, "vector", "embedding", qv, lit(rng.randint(1, 4)), self.fresh("s"),
                              rng.choice([None, 0.9]))
        a = ApproxTopK(pop, "vector", "embedding", qv, lit(rng.randint(1, 4)), "distance")
        b = Score(pop, "bm25", "body", qt, "score")
        w = (rng.choice([0.5, 1.0, 2.0]), rng.choice([0.5, 1.0])) if rng.random() < 0.4 else None
        return Fuse((a, b), rng.choice([1, 60]), w)

    def inner(self, depth, gamma):
        """A correlated inner term that exports only fresh names."""
        rng = self.rng
        base = Env()
        cols = gamma
        t = base
        for _ in range(rng.randint(0, 2)):
            nodes = self.node_cols(cols)
            if nodes and rng.random() < 0.7:
                x, xt = rng.choice(nodes)
                opts = EDGES_FROM.get(xt.arg, [])
                if opts:
                    e, d, _ = rng.choice(opts)
                    t = Expand(t, x, ((e, d),), 1, 1, self.fresh("y"))
            else:
                t = Filter(t, self.pred(cols))
            cols = self.cols(t, gamma)
        gnames = {n for n, _ in gamma}
        new = [(n, ty) for n, ty in cols if n not in gnames]
        if rng.random() < 0.5 or not new:
            return Group(t, (), ((self.fresh("a"), Agg("count")),))
        out = Project(t, tuple((self.fresh("w"), Col(n)) for n, _ in new))
        if rng.random() < 0.4:
            out = Cut(Order(out, ()), lit(rng.randint(0, 2)))
        return out

    def union_arm(self, src, cols, depth, gamma):
        rng = self.rng
        r = rng.random()
        if r < 0.5:
            return Filter(src, self.pred(cols))           # same types
        if r < 0.7:
            return Project(src, tuple((self.fresh("u"), Col(n)) for n, _ in cols))   # same types, other names
        return self.rel(max(0, depth - 2), gamma)          # usually a mismatch


def union_oracle(lc, rc):
    if len(lc) != len(rc):
        return False
    for (_, a), (_, b) in zip(lc, rc):
        if a.kind == "Unknown" or b.kind == "Unknown":
            continue
        if (a.kind, a.arg) != (b.kind, b.arg):
            return False
    return True


# ====================================================================== fix terms

def fix_term(rng: random.Random, gen: Gen, graph: Graph):
    """A random recursive read over Person, in the shape of `reachable` and
    `distance`, with random extra structure in the step."""
    start = rng.choice(sorted(graph.nodes["Person"]))
    edge = rng.choice([(("Knows", "out"),), (("Knows", "in"),), (("Knows", "out"), ("Knows", "in"))])
    lattice = rng.random() < 0.4
    fn = rng.choice(["min", "max"]) if lattice else None
    base_src = Filter(Scan("Person", "p"), Cmp("=", Prop(Col("p"), "handle"), lit(start)))
    base_items = (("q", Col("y")),) + ((("d", lit(1)),) if lattice else ())
    base = Project(Expand(base_src, "p", edge, 1, 1, "y"), base_items)
    rec = RecRef("R", 1)
    step_src = rec
    shape = rng.choice(["plain", "plain", "filter", "lattice_filter", "cut", "exists", "not", "inner_agg", "two_hop"])
    if shape == "filter":
        step_src = Filter(step_src, gen.pred((("q", T("Node", "Person")),)))
    if shape == "lattice_filter" and lattice:
        step_src = Filter(step_src, Cmp(rng.choice(["<", ">", "<="]), Col("d"), lit(rng.randint(1, 3))))
    if shape == "cut":
        step_src = Cut(Order(step_src, ((Prop(Col("q"), "handle"), rng.choice(["asc", "desc"]), None),)), lit(rng.randint(1, 2)))
    step = Expand(step_src, "q", edge, 1, 1, "r")
    if shape == "two_hop":
        step = Expand(step, "r", edge, 1, 1, "r2")
        step = Project(step, tuple((n, Col(n)) for n in (["q", "r2"] + (["d"] if lattice else []))))
        step = Project(step, (("q", Col("q")), ("r", Col("r2"))) + ((("d", Col("d")),) if lattice else ()))
    if shape in ("exists", "not"):
        inner = Group(Filter(Project(RecRef("R", 2), (("q2", Col("q")),)), Cmp("=", Col("q2"), Col("r"))), (), (("c", Agg("count")),))
        corr = Correlate(step, inner)
        test = Cmp(">", Col("c"), Lit(0, I64)) if shape == "exists" else Not(Cmp(">", Col("c"), Lit(0, I64)))
        keep = ["q", "r"] + (["d"] if lattice else [])
        step = Project(Filter(corr, test), tuple((n, Col(n)) for n in keep))
    if shape == "inner_agg":
        inner = Group(RecRef("R", 2), (), (("m", Agg("count")),)) if not lattice else \
            Group(RecRef("R", 2), (), (("m", Agg("min", Col("d"))),))
        step = Project(Filter(Correlate(step, inner), Cmp(">=", Col("m"), lit(1))),
                       tuple((n, Col(n)) for n in ["q", "r"] + (["d"] if lattice else [])))
    step_items = (("q", Col("r")),) + ((("d", Arith("+", Col("d"), lit(rng.choice([1, 1, 2])))),) if lattice else ())
    step = Project(step, step_items)
    union = Union(base, step)
    if lattice:
        body = Group(union, (("q", Col("q")),), (("d", Agg(fn, Col("d"))),))
        budget = rng.choice([None, 3, 8, 40])
    else:
        body = Group(union, (("q", Col("q")),), ()) if rng.random() < 0.8 else Project(union, (("q", Col("q")),))
        budget = None
    return Fix(("R",), (body,), "R", budget), shape


# ====================================================================== surface reads

class SurfaceGen:
    """A random surface read and the core term the derived-forms table gives
    for it, built side by side."""

    def __init__(self, rng: random.Random, schema):
        self.rng = rng
        self.schema = schema
        self.k = 0

    def fresh(self, base):
        self.k += 1
        return f"{base}{self.k}"

    def build(self):
        rng = self.rng
        clauses, term, cols, scope = [], Env(), [], {}

        def start(t):
            return t if isinstance(term, Env) and not cols else Correlate(term, t)
        nv = 0
        for _ in range(rng.randint(1, 5)):
            persons = [c for c in cols if scope[c] == "Person"]
            docs = [c for c in cols if scope[c] == "Doc"]
            r = rng.random()
            if r < 0.25 or not cols:
                nv += 1
                name = f"x{nv}"
                typ = rng.choice(["Person", "Doc"])
                if rng.random() < 0.3:
                    key = "handle" if typ == "Person" else "id"
                    val = rng.choice(["p0", "p1", "d0", "d1"])
                    clauses.append(bind(name, typ, **{key: L(val)}))
                    term = Filter(start(Scan(typ, name)), Cmp("=", Prop(Col(name), key), L(val)))
                else:
                    clauses.append(bind(name, typ))
                    term = start(Scan(typ, name))
                cols = cols + [name]
                scope[name] = typ
            elif r < 0.5 and (persons or docs):
                nv += 1
                name = f"x{nv}"
                if persons and (not docs or rng.random() < 0.5):
                    a = rng.choice(persons)
                    e, et, end, hops = rng.choice([("knows", "Knows", "Person", (1, rng.randint(1, 2))),
                                                   ("authored", "Authored", "Doc", (1, 1))])
                    clauses.append(tri(a, e, name, hops=hops))
                    term = Expand(term, a, ((et, "out"),), hops[0], hops[1], name)
                else:
                    a = rng.choice(docs)
                    if rng.random() < 0.5:
                        clauses.append(tri(name, "authored", a))          # written against the anchor: reversed
                        term = Expand(term, a, (("Authored", "in"),), 1, 1, name)
                        end = "Person"
                    else:
                        clauses.append(tri(a, "cites", name))
                        term = Expand(term, a, (("Cites", "out"),), 1, 1, name)
                        end = "Doc"
                cols = cols + [name]
                scope[name] = end
            elif r < 0.65 and (persons or docs):
                c = rng.choice(persons + docs)
                e = Cmp(">=", Prop(Col(c), "age" if scope[c] == "Person" else "year"), L(rng.choice([20, 30, 2024])))
                clauses.append(where(e))
                term = Filter(term, e)
            elif r < 0.75 and (persons or docs):
                c = rng.choice(persons + docs)
                nv += 1
                name = f"l{nv}"
                e = Prop(Col(c), "age" if scope[c] == "Person" else "year")
                clauses.append(SLet(name, e))
                term = Project(term, tuple((x, Col(x)) for x in cols) + ((name, e),))
                cols = cols + [name]
                scope[name] = "I32"
            elif r < 0.9 and persons:
                a = rng.choice(persons)
                kind = rng.choice(["exists", "not"])
                nv += 1
                block = (tri(a, "knows", f"_q{nv}"),)
                yname = block[0].dst
                clauses.append(SExists(block) if kind == "exists" else SNot(block))
                b = self.fresh("_b")
                g = Group(Expand(Env(), a, (("Knows", "out"),), 1, 1, yname), (), ((b, Agg("count")),))
                test = Cmp(">", Col(b), Lit(0, I64))
                if kind == "not":
                    test = Not(test)
                term = Project(Filter(Correlate(term, g), test), tuple((x, Col(x)) for x in cols))
            else:
                nv += 1
                name = f"f{nv}"
                inner_var = f"i{nv}"
                clauses.append(SFrom(inline([bind(inner_var, "Doc")], [(v(inner_var), name)])))
                gamma_nonempty = bool(cols)
                scan = Correlate(Env(), Scan("Doc", inner_var)) if gamma_nonempty else Scan("Doc", inner_var)
                inner = Project(scan, ((name, Col(inner_var)),))
                term = start(inner)
                cols = cols + [name]
                scope[name] = "Doc"
        refs = [c for c in cols if scope[c] in ("Person", "Doc")]
        ret_col = rng.choice(refs) if refs else cols[0]
        mode = rng.choice(["plain", "distinct", "agg", "ordered"])
        if mode == "plain":
            r_ = read(clauses, [v(ret_col)])
            core = Project(term, ((ret_col, Col(ret_col)),))
        elif mode == "distinct":
            r_ = read(clauses, [v(ret_col)], distinct=True)
            core = Group(term, ((ret_col, Col(ret_col)),), ())
        elif mode == "agg":
            r_ = read(clauses, [v(ret_col), (Agg("count"), "n")])
            core = Group(term, ((ret_col, Col(ret_col)),), (("n", Agg("count")),))
        else:
            k = rng.randint(0, 3)
            r_ = read(clauses, [v(ret_col)], order=[(v(ret_col), "desc")], limit=L(k))
            f = self.fresh("_r")
            ext = Project(term, tuple((x, Col(x)) for x in cols) + ((f, Col(ret_col)),))
            core = Project(Cut(Order(ext, ((Col(f), "desc", None),)), L(k)), ((ret_col, Col(f)),))
        return r_, core


# ====================================================================== runner

class Report:
    def __init__(self):
        self.c = Counter()
        self.failures = []

    def fail(self, kind, msg, term=None):
        self.c[f"FAIL {kind}"] += 1
        if len(self.failures) < 20:
            self.failures.append((kind, msg, repr(term)[:600]))


def run_once(t, g, cfg):
    return C.run_ctx(t, g, {}, cfg)


def check_generic(t, g, rep: Report):
    try:
        C.check_read(t, g.schema, cfg=STRESS)
    except GQError as e:
        rep.c[f"refused {e.code}"] += 1
        return
    rep.c["generic accepted"] += 1
    results = []
    for seed in (1, 2):
        try:
            r, ctx = run_once(t, g, replace(STRESS, perturb=seed))
        except BoundViolation as e:
            rep.fail("bound", str(e), t)
            return
        except GQError as e:
            rep.c[f"typed error {e.code}"] += 1
            return
        results.append(r)
        rep.c["bound checks"] += ctx.stats["bound_checks"]
        rep.c["expand over the table's bound"] += ctx.stats.get("expand_table_bound_exceeded", 0)
        rep.c["topk rows over k"] += ctx.stats["topk_rows_over_k"]
        for op, n in ctx.stats["ops"].items():
            rep.c[f"op {op}"] += n
    a, b = results
    if Counter(a.rows) != Counter(b.rows):
        rep.fail("content differs across physical orders", "", t)
    elif a.ordered and a.rows != b.rows:
        rep.fail("order differs across physical orders", "", t)
    else:
        rep.c["deterministic (proposed tie rule)"] += 1
    if any(isinstance(x, (Order, Cut)) for x in C.walk_terms(t)):
        rep.c["generic with Order or Cut"] += 1
        outs = set()
        for seed in (1, 2, 3):
            try:
                r, _ = run_once(t, g, replace(RFC_TIES, perturb=seed))
            except GQError:
                break
            outs.add(tuple(r.rows) if r.ordered else tuple(sorted(Counter(r.rows).items(), key=repr)))
        if len(outs) > 1:
            rep.c["nondeterministic under the RFC's tie rule"] += 1


def check_union(gen: Gen, g, rep: Report):
    a = gen.rel(gen.rng.randint(0, 2))
    try:
        b = gen.rel(gen.rng.randint(0, 2)) if gen.rng.random() < 0.6 else Filter(a, gen.pred(gen.cols(a)))
        lc, rc = gen.cols(a), gen.cols(b)
    except GQError:
        rep.c["union arm refused"] += 1
        return
    want = union_oracle(lc, rc)
    try:
        C.check_read(Union(a, b), g.schema, cfg=STRESS)
        got = True
    except GQError as e:
        got = False
        if e.code != "TUNION":
            rep.fail("union refused with the wrong code", e.code, Union(a, b))
    if got != want:
        rep.fail("union typer disagrees with the oracle", f"typer {got} oracle {want}", Union(a, b))
    rep.c["union agree" if want else "union mismatch refused"] += 1


def check_fix(rng, gen, g, rep: Report):
    fix, shape = fix_term(rng, gen, g)
    rep.c[f"fix shape {shape}"] += 1
    try:
        C.check_read(fix, g.schema, cfg=STRESS)
    except GQError as e:
        rep.c[f"fix refused by typer {e.code}"] += 1
        return
    adm = {}
    for rule in ("rfc", "proposed"):
        try:
            C.admit_fix(fix, rule)
            adm[rule] = True
        except GQError:
            adm[rule] = False
    rep.c[f"fix admitted rfc={adm['rfc']} proposed={adm['proposed']}"] += 1
    if not adm["rfc"] and not adm["proposed"]:
        return
    outs = {}
    for strat in ("seminaive", "naive"):
        try:
            r, ctx = run_once(fix, g, replace(STRESS, fix_strategy=strat))
            outs[strat] = set(r.rows)
            rep.c["fix rounds"] += ctx.stats["fix_rounds"][0] if ctx.stats["fix_rounds"] else 0
            rep.c["bound checks"] += ctx.stats["bound_checks"]
            for op, n in ctx.stats["ops"].items():
                rep.c[f"op {op}"] += n
        except GQError as e:
            outs[strat] = e.code
        except BoundViolation as e:
            rep.fail("fix bound", str(e), fix)
            return
    same = outs["seminaive"] == outs["naive"]
    if adm["proposed"]:
        if not same:
            rep.fail("naive and semi-naive disagree on an admitted recursion", repr(outs)[:300], fix)
        else:
            rep.c["fix admitted: naive == semi-naive"] += 1
        if "E_FIX_DIVERGE" in (outs["seminaive"], outs["naive"]):
            rep.fail("an admitted recursion did not terminate", repr(outs)[:200], fix)
    elif adm["rfc"]:
        rep.c["fix admitted only by the RFC rule"] += 1
        if not same:
            rep.c[f"RFC-admitted recursion: naive != semi-naive ({shape})"] += 1


def check_desugar(sg: SurfaceGen, g, rep: Report):
    sg.k = 0
    r, want = sg.build()
    try:
        got = Sf.Desugarer(g.schema, {}).read(r)
    except GQError as e:
        rep.fail("desugar refused a generated read", e.code + " " + e.message, r)
        return
    if got != want:
        rep.fail("desugaring differs from the direct core term", first_diff(got, want), r)
        return
    rep.c["desugar == direct"] += 1
    try:
        C.run(got, g, {}, STRESS)
        rep.c["desugared read ran"] += 1
    except GQError as e:
        rep.c[f"desugared read typed error {e.code}"] += 1


def first_diff(a, b, path="term"):
    if type(a) is not type(b):
        return f"{path}: {type(a).__name__} vs {type(b).__name__}: {a!r:.200} | {b!r:.200}"
    if isinstance(a, tuple):
        if len(a) != len(b):
            return f"{path}: lengths {len(a)} vs {len(b)}: {a!r:.300} | {b!r:.300}"
        for i, (x, y) in enumerate(zip(a, b)):
            d = first_diff(x, y, f"{path}[{i}]")
            if d:
                return d
        return ""
    if hasattr(a, "__dataclass_fields__"):
        for f in a.__dataclass_fields__:
            d = first_diff(getattr(a, f), getattr(b, f), f"{path}.{f}")
            if d:
                return d
        return ""
    return "" if a == b else f"{path}: {a!r} vs {b!r}"


def check_cut(rng, g, rep: Report):
    k = rng.randint(0, 3)
    inner = inline([tri("p", "authored", "d")], [v("d")], order=[(pr("d", "year"), rng.choice(["asc", "desc"]))], limit=L(k))
    outer_pred = Cmp(rng.choice(["=", "!=", ">="]), Prop(Col("d"), "year"), lit(rng.choice([2023, 2024, 2025])))
    full = read([bind("p", "Person"), SFrom(inner), where(outer_pred)], [v("p"), v("d")])
    alone = read([bind("p", "Person"), SFrom(inner)], [v("p"), v("d")])
    ds = Sf.Desugarer(g.schema, {})
    try:
        filtered = C.run(ds.read(full), g, {}, STRESS)
        base = C.run(ds.read(alone), g, {}, STRESS)
    except GQError as e:
        rep.c[f"cut typed error {e.code}"] += 1
        return
    fb, bb = Counter(filtered.rows), Counter(base.rows)
    if any(fb[r] > bb[r] for r in fb):
        rep.fail("an outer filter widened an inner cut", "", full)
        return
    per = Counter(r[0] for r in filtered.rows)
    if any(n > k for n in per.values()):
        rep.fail("more rows per outer row than the inner limit", "", full)
        return
    rep.c["cut never widened"] += 1


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--terms", type=int, default=50_000)
    ap.add_argument("--seed", type=int, default=20261108)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    rng = random.Random(a.seed)
    rep = Report()
    t0 = time.time()
    g = None
    kinds = ["generic"] * 6 + ["fix", "desugar", "cut", "union"]
    for i in range(a.terms):
        if i % 50 == 0:
            g = random_graph(rng)
            gen = Gen(rng, g.schema)
            sg = SurfaceGen(rng, g.schema)
        kind = kinds[i % len(kinds)]
        rep.c[f"terms {kind}"] += 1
        try:
            if kind == "generic":
                check_generic(gen.rel(rng.randint(1, 4)), g, rep)
            elif kind == "fix":
                check_fix(rng, gen, g, rep)
            elif kind == "desugar":
                check_desugar(sg, g, rep)
            elif kind == "cut":
                check_cut(rng, g, rep)
            else:
                check_union(gen, g, rep)
        except RecursionError as e:
            rep.fail(f"{kind} recursion", str(e))
        except Exception as e:   # a crash in the interpreter is a failure, never a refusal
            import traceback
            rep.fail(f"{kind} crash", traceback.format_exc()[-800:])
    dt = time.time() - t0
    total = sum(v_ for k_, v_ in rep.c.items() if k_.startswith("terms "))
    if not a.quiet:
        print(f"stress: {total} terms in {dt:.1f}s (seed {a.seed})")
        for k_ in sorted(rep.c):
            print(f"  {k_}: {rep.c[k_]}")
    for kind, msg, term in rep.failures:
        print(f"FAIL [{kind}] {msg}\n    {term}")
    return 1 if rep.failures else 0


if __name__ == "__main__":
    sys.exit(main())
