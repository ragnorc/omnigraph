"""The core algebra of RFC GQ 3, section 2: scalar expressions, the thirteen
operators, three sinks and the `Call` term former, with a static typer and a
reference evaluator that checks each operator's cardinality bound.

Where the RFC leaves a rule open, `Config` names the choice. Each knob has an
`rfc` setting (the RFC's text read literally) and a proposed setting; the
report's findings cite the knob.
"""
from __future__ import annotations

import functools
import math
import random
from dataclasses import dataclass, field, replace
from typing import Any, Optional

from .model import (BOOL, F32, F64, FLOAT_KINDS, I32, I64, INT_KINDS, INT_RANGE,
                    NUMERIC, STRING, BoundViolation, EdgeRef, GQError, Graph,
                    NodeRef, T, edge_t, node_t, value_key)
from . import search as S


# ====================================================================== config

@dataclass(frozen=True)
class Config:
    # Order's tie rule. "rfc": keys, then node identities in column order,
    # then the input order ("unspecified"). "total": keys, then every
    # reference identity (node and edge), then every remaining column value.
    order_ties: str = "total"
    # Union type agreement. "strict": equal types including nullability.
    # "join": equal base types, nullable when either arm is.
    union_nullability: str = "join"
    # ApproxTopK's k. "targets": k distinct targets with all their rows (the
    # section 4 wording). "rows": at most k rows (the operators table).
    topk_unit: str = "targets"
    # Which column a ranking function ranks. "rfc": the population must carry
    # exactly one node column (section 5). "field": the one node column whose
    # type declares the field.
    score_population: str = "rfc"
    # Fix evaluation.
    fix_strategy: str = "seminaive"     # or "naive" (Kleene iteration R := f(R))
    fix_admission: str = "proposed"     # or "rfc"
    fix_set: bool = True                # rows are a set inside Fix (False: bags)
    fix_hard_cap: int = 2000            # internal stop; never reached by an admitted term
    # Two writes to one entity property in one write: "refuse" or "last".
    write_conflicts: str = "refuse"
    # Seed for the physical order of Scan, Expand, Group, Union and Fix output.
    perturb: Optional[int] = None
    # Whether Correlate's output keeps the outer relation's order (the RFC
    # does not say which operators preserve an order).
    correlate_keeps_order: bool = False


RFC_LITERAL = Config(order_ties="rfc", union_nullability="strict", topk_unit="rows",
                     score_population="rfc", fix_admission="rfc", fix_set=True,
                     write_conflicts="last")
PROPOSED = Config()


# ====================================================================== expressions

@dataclass(frozen=True)
class Lit:
    value: Any
    type: T


@dataclass(frozen=True)
class Param:
    name: str


@dataclass(frozen=True)
class Col:
    name: str


@dataclass(frozen=True)
class Prop:
    base: Any
    name: str


@dataclass(frozen=True)
class Meta:
    base: Any
    field: str     # @id @src @dst @type


@dataclass(frozen=True)
class Arith:
    op: str        # + - * / %
    l: Any
    r: Any


@dataclass(frozen=True)
class Neg:
    e: Any


@dataclass(frozen=True)
class Cmp:
    op: str        # = != < <= > >=
    l: Any
    r: Any


@dataclass(frozen=True)
class And:
    l: Any
    r: Any


@dataclass(frozen=True)
class Or:
    l: Any
    r: Any


@dataclass(frozen=True)
class Not:
    e: Any


@dataclass(frozen=True)
class IsNull:
    e: Any
    neg: bool = False


@dataclass(frozen=True)
class InList:
    e: Any
    lst: Any


@dataclass(frozen=True)
class Fn:
    name: str
    args: tuple


@dataclass(frozen=True)
class TermsQ:
    text: Any
    mode: str = "all"
    max_edits: Any = 0


@dataclass(frozen=True)
class Agg:
    fn: str        # count sum avg min max
    arg: Any = None
    distinct: bool = False


def lit(v) -> Lit:
    if v is None:
        raise ValueError("no null literal in a read")
    if isinstance(v, bool):
        return Lit(v, BOOL)
    if isinstance(v, int):
        return Lit(v, I64)
    if isinstance(v, float):
        return Lit(v, F64)
    if isinstance(v, str):
        return Lit(v, STRING)
    if isinstance(v, tuple):
        return Lit(v, T("Vector", len(v)))
    raise TypeError(v)


def null_lit(t: T) -> Lit:
    return Lit(None, t.null())


def conj(preds):
    preds = list(preds)
    out = preds[0]
    for p in preds[1:]:
        out = And(out, p)
    return out


def expr_cols(e) -> set:
    """Column names an expression reads."""
    out = set()

    def walk(x):
        if isinstance(x, Col):
            out.add(x.name)
        elif isinstance(x, (Lit, Param)) or x is None:
            return
        elif isinstance(x, tuple):
            for y in x:
                walk(y)
        elif hasattr(x, "__dataclass_fields__"):
            for f in x.__dataclass_fields__:
                walk(getattr(x, f))
    walk(e)
    return out


# ====================================================================== terms

@dataclass(frozen=True)
class Env:
    """The correlated outer row: one row of the enclosing bindings. At the
    top of a read, one empty row. The RFC's core has no name for this leaf;
    every correlated inner term needs one (finding: Correlate's columns)."""


@dataclass(frozen=True)
class Scan:
    type: str
    var: str


@dataclass(frozen=True)
class Expand:
    src: Any
    x: str
    sel: tuple            # ((edge type, "out"|"in"), ...)
    m: int
    n: int
    y: str
    k: Optional[str] = None


@dataclass(frozen=True)
class Filter:
    src: Any
    pred: Any


@dataclass(frozen=True)
class Project:
    src: Any
    items: tuple          # ((name, expr), ...)


@dataclass(frozen=True)
class Group:
    src: Any
    keys: tuple           # ((name, expr), ...)
    aggs: tuple           # ((name, Agg), ...)


@dataclass(frozen=True)
class Correlate:
    outer: Any
    inner: Any


@dataclass(frozen=True)
class Union:
    left: Any
    right: Any


@dataclass(frozen=True)
class Order:
    src: Any
    keys: tuple           # ((expr, "asc"|"desc", "first"|"last"|None), ...)


@dataclass(frozen=True)
class Cut:
    src: Any
    k: Any                # expr


@dataclass(frozen=True)
class Score:
    src: Any
    kind: str             # l2 | bm25
    field: str
    q: Any
    out: str              # distance | score
    x: Optional[str] = None


@dataclass(frozen=True)
class ApproxTopK:
    src: Any
    kind: str             # vector (the field's distance) | bm25
    field: str
    q: Any
    k: Any
    out: str
    recall: Optional[float] = None
    x: Optional[str] = None


@dataclass(frozen=True)
class Fuse:
    arms: tuple
    k: int = 60
    weights: Optional[tuple] = None
    method: str = "rrf"


@dataclass(frozen=True)
class RecRef:
    name: str
    occ: int = 0


@dataclass(frozen=True)
class Fix:
    names: tuple
    bodies: tuple
    select: str
    budget: Optional[int] = None


@dataclass(frozen=True)
class Call:
    """Unresolved term former; `Program` resolves it to Inlined or Fix."""
    name: str
    args: tuple


@dataclass(frozen=True)
class Inlined:
    name: str
    params: tuple         # ((name, T), ...)
    args: tuple           # scalar exprs, evaluated in the caller's scope
    body: Any             # closed: sees only its params


# sinks
@dataclass(frozen=True)
class Insert:
    type: str
    assigns: tuple        # ((prop, expr), ...)


@dataclass(frozen=True)
class Update:
    x: str
    assigns: tuple


@dataclass(frozen=True)
class Delete:
    x: str


@dataclass(frozen=True)
class Write:
    src: Any
    stmts: tuple


RELATION_TERMS = (Env, Scan, Expand, Filter, Project, Group, Correlate, Union, Order, Cut,
                  Score, ApproxTopK, Fuse, RecRef, Fix, Call, Inlined)
OPERATORS = ("Scan", "Expand", "Filter", "Project", "Group", "Correlate", "Union", "Order",
             "Cut", "Score", "ApproxTopK", "Fuse", "Fix")


def children(t):
    if isinstance(t, (Env, Scan, RecRef, Call)):
        return ()
    if isinstance(t, (Expand, Filter, Project, Group, Order, Cut, Score, ApproxTopK)):
        return (t.src,)
    if isinstance(t, Correlate):
        return (t.outer, t.inner)
    if isinstance(t, Union):
        return (t.left, t.right)
    if isinstance(t, Fuse):
        return t.arms
    if isinstance(t, Fix):
        return t.bodies
    if isinstance(t, Inlined):
        return (t.body,)
    if isinstance(t, Write):
        return (t.src,)
    raise TypeError(t)


def term_size(t, memo=None) -> int:
    """Size of the expanded tree, computed over the shared DAG."""
    memo = {} if memo is None else memo
    k = id(t)
    if k in memo:
        return memo[k]
    s = 1 + sum(term_size(c, memo) for c in children(t))
    memo[k] = s
    return s


def contains_rec(t, names=None) -> bool:
    if isinstance(t, RecRef):
        return names is None or t.name in names
    return any(contains_rec(c, names) for c in children(t))


def walk_terms(t):
    yield t
    for c in children(t):
        yield from walk_terms(c)


# ====================================================================== relations

@dataclass
class Rel:
    names: tuple
    rows: list
    ordered: bool = False
    meta: dict = field(default_factory=dict)

    def dicts(self):
        return [dict(zip(self.names, r)) for r in self.rows]


# ====================================================================== typing

class _Pending(Exception):
    pass


@dataclass
class TEnv:
    schema: Any
    params: dict
    gamma: tuple = ()             # ((name, T), ...)
    rec: dict = field(default_factory=dict)
    cfg: Config = PROPOSED
    in_read: bool = True
    notes: dict = field(default_factory=dict)   # static facts the evaluator reads, by id(term)
    calls: dict = field(default_factory=dict)   # declared read -> result columns (None: pending)

    def with_gamma(self, gamma):
        return replace(self, gamma=tuple(gamma))


def _numeric(t):
    return t.kind in NUMERIC


UNKNOWN = T("Unknown", None, True)


def arith_type(op, lt: T, rt: T) -> T:
    if lt.kind == "Unknown" or rt.kind == "Unknown":
        return UNKNOWN
    if not (_numeric(lt) and _numeric(rt)):
        raise GQError("T_ARITH", f"non-numeric operand: {lt} {op} {rt}")
    nullable = lt.nullable or rt.nullable
    a, b = lt.kind, rt.kind
    if a in INT_KINDS and b in INT_KINDS:
        kind = "I64" if "I64" in (a, b) else "I32"
    elif a in FLOAT_KINDS and b in FLOAT_KINDS:
        # The RFC states no rule for F32 with F64; this picks F64.
        kind = "F64" if "F64" in (a, b) else "F32"
    else:
        kind = "F64"
    return T(kind, None, nullable)


def comparable(a: T, b: T) -> bool:
    if a.kind == "Unknown" or b.kind == "Unknown":
        return True
    if _numeric(a) and _numeric(b):
        return True
    if a.kind != b.kind:
        return False
    if a.is_ref:
        return True
    return a.arg == b.arg or a.kind == "List"


def type_expr(e, scope: dict, tenv: TEnv) -> T:
    sch = tenv.schema
    if isinstance(e, Lit):
        return e.type
    if isinstance(e, Param):
        if e.name not in tenv.params:
            raise GQError("T_PARAM", f"unknown parameter ${e.name}")
        return tenv.params[e.name]
    if isinstance(e, Col):
        if e.name not in scope:
            raise GQError("T_SCOPE", f"unknown variable ${e.name}")
        return scope[e.name]
    if isinstance(e, Prop):
        bt = type_expr(e.base, scope, tenv)
        if bt.kind == "Unknown":
            return UNKNOWN
        if not bt.is_ref:
            raise GQError("T_PROP", f"property access on {bt}")
        pt = sch.prop_type(bt.arg, e.name)
        return pt.null(pt.nullable or bt.nullable)
    if isinstance(e, Meta):
        bt = type_expr(e.base, scope, tenv)
        if not bt.is_ref:
            raise GQError("T_META", f"meta-field on {bt}")
        if e.field == "@id" or e.field == "@type":
            return STRING.null(bt.nullable)
        if e.field in ("@src", "@dst") and bt.kind == "Edge":
            return STRING.null(bt.nullable)
        raise GQError("T_META", f"unknown meta-field {e.field}")
    if isinstance(e, Arith):
        return arith_type(e.op, type_expr(e.l, scope, tenv), type_expr(e.r, scope, tenv))
    if isinstance(e, Neg):
        t = type_expr(e.e, scope, tenv)
        if not _numeric(t):
            raise GQError("T_ARITH", f"unary minus on {t}")
        return t
    if isinstance(e, Cmp):
        a, b = type_expr(e.l, scope, tenv), type_expr(e.r, scope, tenv)
        if not comparable(a, b):
            raise GQError("T_CMP", f"cannot compare {a} with {b}")
        if e.op not in ("=", "!=") and (a.is_ref or a.kind in ("Vector", "List")):
            raise GQError("T_CMP", f"{e.op} on {a}")
        return BOOL.null(a.nullable or b.nullable)
    if isinstance(e, (And, Or)):
        a, b = type_expr(e.l, scope, tenv), type_expr(e.r, scope, tenv)
        if a.kind != "Bool" or b.kind != "Bool":
            raise GQError("T_BOOL", "and/or over non-Bool")
        return BOOL.null(a.nullable or b.nullable)
    if isinstance(e, Not):
        a = type_expr(e.e, scope, tenv)
        if a.kind != "Bool":
            raise GQError("T_BOOL", "not over non-Bool")
        return a
    if isinstance(e, IsNull):
        type_expr(e.e, scope, tenv)
        return BOOL
    if isinstance(e, InList):
        a, b = type_expr(e.e, scope, tenv), type_expr(e.lst, scope, tenv)
        if b.kind != "List":
            raise GQError("T_IN", f"in over {b}")
        return BOOL.null(a.nullable or b.nullable)
    if isinstance(e, TermsQ):
        type_expr(e.text, scope, tenv)
        return T("Terms")
    if isinstance(e, Fn):
        ts = [type_expr(a, scope, tenv) for a in e.args]
        if e.name in ("contains", "starts_with"):
            return BOOL.null(any(t.nullable for t in ts))
        if e.name in ("match_terms", "search", "fuzzy"):
            return BOOL
        if e.name == "if":
            return ts[1].null(ts[1].nullable or ts[2].nullable)
        if e.name == "coalesce":
            return ts[-1].null(all(t.nullable for t in ts))
        if e.name == "concat":
            return STRING.null(any(t.nullable for t in ts))
        raise GQError("T_FN", f"unknown function {e.name}")
    if isinstance(e, Agg):
        raise GQError("T_AGG", "an aggregate outside Group")
    if isinstance(e, RELATION_TERMS):
        raise GQError("T69", "a relation where a scalar is read")
    raise TypeError(f"not an expression: {e!r}")


def agg_type(a: Agg, scope, tenv) -> T:
    if a.fn == "count":
        if a.arg is not None:
            type_expr(a.arg, scope, tenv)
        return I64
    t = type_expr(a.arg, scope, tenv)
    if a.fn == "sum":
        if not _numeric(t):
            raise GQError("T_AGG", "sum over non-numeric")
        return F64.null() if t.kind in INT_KINDS else t.base().null()
    if a.fn == "avg":
        if not _numeric(t):
            raise GQError("T_AGG", "avg over non-numeric")
        return F64.null()
    if a.fn in ("min", "max"):
        return t.null()
    raise GQError("T_AGG", f"unknown aggregate {a.fn}")


def _check_unique(names, what):
    seen = set()
    for n in names:
        if n in seen:
            raise GQError("T68", f"column {n} collides in {what}")
        seen.add(n)


def node_columns(cols):
    return [(n, t) for n, t in cols if t.kind == "Node"]


def resolve_population(cols, fld, tenv: TEnv, x: Optional[str]):
    """The node column a ranking function ranks."""
    nodes = node_columns(cols)
    if x is not None:
        d = dict(cols)
        if x not in d or d[x].kind != "Node":
            raise GQError("T_POP_NODECOL", f"{x} is not a node column of the population")
        return x, d[x]
    if tenv.cfg.score_population == "rfc":
        if len(nodes) != 1:
            raise GQError("T_POP_NODECOL",
                          f"the population must carry exactly one node column, it has {len(nodes)}: "
                          f"{[n for n, _ in nodes]}")
        return nodes[0]
    owners = []
    for n, t in nodes:
        try:
            tenv.schema.prop_type(t.arg, fld)
            owners.append((n, t))
        except GQError:
            pass
    if len(owners) != 1:
        raise GQError("T_POP_NODECOL", f"{len(owners)} node columns declare {fld}")
    return owners[0]


def type_term(t, tenv: TEnv):
    """Columns of a relation term: ((name, T), ...)."""
    cols = _type_term(t, tenv)
    tenv.notes[("cols", id(t))] = cols
    return cols


def _type_term(t, tenv: TEnv):
    sch = tenv.schema
    if isinstance(t, Env):
        return tenv.gamma
    if isinstance(t, Scan):
        if not sch.is_node_type(t.type):
            raise GQError("T1", f"unknown node type {t.type}")
        return ((t.var, node_t(t.type)),)
    if isinstance(t, Expand):
        cols = type_term(t.src, tenv)
        d = dict(cols)
        if t.x not in d:
            raise GQError("T_EXPAND_SRC", f"no column {t.x} to traverse from")
        xt = d[t.x]
        if xt.kind == "Unknown":
            et = sch.edge_by_type[t.sel[0][0]]
            end = et.dst if t.sel[0][1] == "out" else et.src
            xt = node_t(et.src if t.sel[0][1] == "out" else et.dst)
        if xt.kind != "Node":
            raise GQError("T_EXPAND_SRC", f"{t.x} is {xt}, not a node reference: nothing to traverse from")
        if not (1 <= t.m <= t.n):
            raise GQError("T_HOPS", f"hops {t.m}..{t.n}")
        if t.k is not None and not (t.m == t.n == 1):
            raise GQError("T_EDGE_BIND", "an edge binds only at 1..1")
        ytypes = set()
        x_impls = set(sch.implementors(xt.arg))
        for ename, dirn in t.sel:
            et = sch.edge_by_type[ename]
            start, end = (et.src, et.dst) if dirn == "out" else (et.dst, et.src)
            if not (x_impls & set(sch.implementors(start))):
                raise GQError("T5", f"{ename} does not start at {xt.arg}")
            ytypes.add(end)
        if len(ytypes) != 1:
            raise GQError("T5", f"alternation ends on several types {sorted(ytypes)}")
        if t.n > 1 and ytypes != {xt.arg}:
            raise GQError("T5", "multi-hop needs one node type at both ends")
        out = cols + ((t.y, node_t(ytypes.pop())),)
        if t.k is not None:
            ets = {e for e, _ in t.sel}
            out = out + ((t.k, edge_t(ets.pop() if len(ets) == 1 else "*")),)
        _check_unique([n for n, _ in out], "Expand")
        return out
    if isinstance(t, Filter):
        cols = type_term(t.src, tenv)
        pt = type_expr(t.pred, {**dict(tenv.gamma), **dict(cols)}, tenv)
        if pt.kind != "Bool":
            raise GQError("T_BOOL", "a filter must be Bool")
        return cols
    if isinstance(t, Project):
        cols = type_term(t.src, tenv)
        scope = {**dict(tenv.gamma), **dict(cols)}
        out = tuple((n, type_expr(e, scope, tenv)) for n, e in t.items)
        _check_unique([n for n, _ in out], "Project")
        for n, ty in out:
            if ty.kind == "Terms":
                raise GQError("T69", "a terms value is not a column")
        return out
    if isinstance(t, Group):
        cols = type_term(t.src, tenv)
        scope = {**dict(tenv.gamma), **dict(cols)}
        out = tuple((n, type_expr(e, scope, tenv)) for n, e in t.keys) + \
            tuple((n, agg_type(a, scope, tenv)) for n, a in t.aggs)
        _check_unique([n for n, _ in out], "Group")
        return out
    if isinstance(t, Correlate):
        oc = type_term(t.outer, tenv)
        ic = type_term(t.inner, tenv.with_gamma(_merge_gamma(tenv.gamma, oc)))
        onames = {n for n, _ in oc}
        for n, _ in ic:
            if n in onames:
                raise GQError("T68", f"{n} is already in scope; alias it on the inner return")
        return oc + ic
    if isinstance(t, Union):
        try:
            lc = type_term(t.left, tenv)
        except _Pending:
            rc = type_term(t.right, tenv)
            return rc
        try:
            rc = type_term(t.right, tenv)
        except _Pending:
            return lc
        return union_cols(lc, rc, tenv.cfg)
    if isinstance(t, Order):
        cols = type_term(t.src, tenv)
        scope = {**dict(tenv.gamma), **dict(cols)}
        for e, d, nl in t.keys:
            ty = type_expr(e, scope, tenv)
            if ty.kind in ("Vector", "Terms"):
                raise GQError("T_ORDER", f"cannot order by {ty}")
        return cols
    if isinstance(t, Cut):
        cols = type_term(t.src, tenv)
        kt = type_expr(t.k, {}, tenv)
        if kt.kind not in INT_KINDS:
            raise GQError("T_LIMIT", "limit must be an integer")
        return cols
    if isinstance(t, (Score, ApproxTopK)):
        cols = type_term(t.src, tenv)
        x, xt = resolve_population(cols, t.field, tenv, t.x)
        tenv.notes[("x", id(t))] = (x, xt)
        ft = sch.prop_type(xt.arg, t.field)
        kind = t.kind
        if kind in ("l2", "vector"):
            if ft.kind != "Vector":
                raise GQError("T27", f"{t.field} is not a Vector")
            qt = type_expr(t.q, {**dict(tenv.gamma), **dict(cols)}, tenv)
            if qt.kind != "Vector":
                raise GQError("T_QUERY", "a vector query must be a Vector")
            if qt.arg is not None and qt.arg != ft.arg:
                raise GQError("T_DIM", f"query dimension {qt.arg} against {ft.arg}")
            st = F32
        else:
            if ft.kind != "String" or not sch.is_fulltext(xt.arg, t.field):
                raise GQError("T27", f"{t.field} is not an indexed full-text field")
            qt = type_expr(t.q, {**dict(tenv.gamma), **dict(cols)}, tenv)
            if qt.kind not in ("String", "Terms"):
                raise GQError("T_QUERY", "a lexical query is a String or terms(...)")
            # The operators table says F32; section 4's bm25_v1 numeric
            # profile is F64. This prototype follows section 4.
            st = F64
        if isinstance(t, ApproxTopK):
            if t.k is None:
                raise GQError("T17", "nearest requires k")
            kt = type_expr(t.k, {}, tenv)
            if kt.kind not in INT_KINDS:
                raise GQError("T_OPTION", "k must be an integer")
            if t.recall is not None and not (0 < t.recall <= 1):
                raise GQError("T_OPTION_RANGE", "recall must be in (0, 1]")
        out = cols + ((t.out, st),)
        _check_unique([n for n, _ in out], "ranking")
        return out
    if isinstance(t, Fuse):
        if not 2 <= len(t.arms) <= 16:
            raise GQError("T_FUSE_ARITY", f"rrf takes 2 to 16 arms, not {len(t.arms)}")
        if t.weights is not None:
            if len(t.weights) != len(t.arms):
                raise GQError("T_FUSE_WEIGHTS", f"{len(t.weights)} weights for {len(t.arms)} arms")
            if not all(0 < w <= 1_000_000 for w in t.weights):
                raise GQError("T_OPTION_RANGE", "each weight must be in (0, 1000000]")
        if not 1 <= t.k <= 1_000_000:
            raise GQError("T_OPTION_RANGE", "k must be in 1..=1000000")
        cols0 = None
        seen = []
        for arm in t.arms:
            spec = rank_spec(arm)
            if spec is None:
                raise GQError("T_FUSE_UNRANKED", "an rrf arm must be a ranking function or an ordered read")
            ac = type_term(arm, tenv)
            if isinstance(arm, (Score, ApproxTopK)):
                x, xt = resolve_population(type_term(arm.src, tenv), arm.field, tenv, arm.x)
                ident = (arm.src, arm.field, arm.q, arm.kind)
                if ident in seen:
                    raise GQError("T_FUSE_DUP", "two arms with the same population, field and query")
                seen.append(ident)
            else:
                x, xt = resolve_population(ac, None, replace(tenv, cfg=replace(tenv.cfg, score_population="rfc")), None)
            if cols0 is None:
                cols0 = (x, xt)
            elif cols0 != (x, xt):
                raise GQError("T_FUSE_COLUMNS", f"arms over different node columns: {cols0[0]} and {x}")
        tenv.notes[("x", id(t))] = cols0
        return ((cols0[0], cols0[1]), ("score", F64))
    if isinstance(t, RecRef):
        if tenv.rec.get(t.name) is None:
            raise _Pending()
        return tenv.rec[t.name]
    if isinstance(t, Fix):
        rec = dict(tenv.rec)
        for n in t.names:
            rec.setdefault(n, None)
        # first pass: the base arms give the type
        found = {}
        for _ in range(len(t.names) + 1):
            for n, b in zip(t.names, t.bodies):
                try:
                    found[n] = type_term(b, replace(tenv, rec={**rec, **found}))
                except _Pending:
                    pass
        tenv.notes[("fix", id(t))] = dict(found)
        missing = [n for n in t.names if n not in found]
        if missing:
            raise GQError("T70", f"recursion with no base case: {missing}")
        for n, b in zip(t.names, t.bodies):
            again = type_term(b, replace(tenv, rec={**rec, **found}))
            if [ty for _, ty in again] != [ty for _, ty in found[n]] and tenv.cfg.union_nullability == "strict":
                raise GQError("TUNION", f"recursive relation {n} changes type")
        return found[t.select]
    if isinstance(t, Inlined):
        scope = dict(tenv.gamma)
        for (pn, pt), a in zip(t.params, t.args):
            if isinstance(a, RELATION_TERMS):
                raise GQError("TCALLARG", f"relation argument to {t.name}: Call takes scalars only")
            at = type_expr(a, scope, tenv)
            if not (at.base() == pt.base() or (_numeric(at) and _numeric(pt))):
                raise GQError("T_ARG", f"argument {pn} of {t.name}: {at} for {pt}")
        return type_term(t.body, replace(tenv, gamma=(), params=dict(t.params)))
    if isinstance(t, Call):
        if t.name in tenv.calls:
            if tenv.calls[t.name] is None:
                raise _Pending()
            for a in t.args:
                if isinstance(a, RELATION_TERMS):
                    raise GQError("TCALLARG", f"relation argument to {t.name}: Call takes scalars only")
                type_expr(a, dict(tenv.gamma), tenv)
            return tenv.calls[t.name]
        raise GQError("T70", f"unresolved call {t.name}")
    if isinstance(t, Write):
        raise GQError("A7", "a sink inside a read")
    raise TypeError(f"not a term: {t!r}")


def _merge_gamma(gamma, cols):
    d = dict(gamma)
    d.update(dict(cols))
    return tuple(d.items())


def union_cols(lc, rc, cfg: Config):
    if len(lc) != len(rc):
        raise GQError("TUNION", f"union arms have {len(lc)} and {len(rc)} columns")
    out = []
    for i, ((ln, lt), (rn, rt)) in enumerate(zip(lc, rc)):
        if lt.kind == "Unknown":
            out.append((ln, rt.null(True)))
            continue
        if rt.kind == "Unknown":
            out.append((ln, lt.null(True)))
            continue
        if lt.base() != rt.base():
            if lt.kind == "Node" and rt.kind == "Node":
                raise GQError("TUNION", f"column {i} ({ln}): {lt} against {rt}")
            raise GQError("TUNION", f"column {i} ({ln}): {lt} against {rt}")
        if lt.nullable != rt.nullable and cfg.union_nullability == "strict":
            raise GQError("TUNION", f"column {i} ({ln}): {lt} against {rt} (nullability)")
        out.append((ln, lt.null(lt.nullable or rt.nullable)))
    return tuple(out)


def rank_spec(arm):
    """How a Fuse arm ranks its targets: (score column, direction) or the
    arm's own Order keys."""
    if isinstance(arm, Score):
        return ("col", arm.out, "asc" if arm.kind == "l2" else "desc")
    if isinstance(arm, ApproxTopK):
        return ("col", arm.out, "asc" if arm.kind == "vector" else "desc")
    if isinstance(arm, (Order, Cut)):
        return ("order",)
    return None


def type_write(w: Write, tenv: TEnv):
    cols = type_term(w.src, replace(tenv, in_read=False))
    d = dict(cols)
    sch = tenv.schema
    for s in w.stmts:
        if isinstance(s, (Update, Delete)):
            if s.x not in d or not d[s.x].is_ref:
                raise GQError("T_SINK", f"{type(s).__name__.lower()} needs a bound reference, {s.x} is not one")
        if isinstance(s, Update):
            for p, e in s.assigns:
                pt = sch.prop_type(d[s.x].arg, p)
                et = type_expr(e, d, tenv) if not (isinstance(e, Lit) and e.value is None) else pt.null()
                if et.nullable and not pt.nullable:
                    raise GQError("T_ASSIGN", f"{p} is required")
        if isinstance(s, Insert):
            if s.type in sch.nodes:
                for p, e in s.assigns:
                    sch.prop_type(s.type, p)
                    type_expr(e, d, tenv)
            elif s.type in sch.edge_by_type:
                et = sch.edge_by_type[s.type]
                for p, e in s.assigns:
                    if p in ("from", "to"):
                        want = et.src if p == "from" else et.dst
                        at = type_expr(e, d, tenv)
                        if at.is_node and want not in sch.implementors(at.arg) and at.arg != want:
                            raise GQError("T_ENDPOINT", f"{p} wants {want}, got {at}")
                        if not at.is_ref:
                            kt = sch.prop_type(want, sch.nodes[want].key)
                            if at.base() != kt.base():
                                raise GQError("T_ENDPOINT", f"{p} key literal {at} for {want}.{sch.nodes[want].key}: {kt}")
                    else:
                        sch.prop_type(s.type, p)
            else:
                raise GQError("T1", f"unknown type {s.type}")
    return cols


# ====================================================================== evaluation

class Ctx:
    def __init__(self, graph: Graph, cfg: Config = PROPOSED, params: Optional[dict] = None):
        self.graph = graph
        self.schema = graph.schema
        self.cfg = cfg
        self.params = dict(params or {})
        self.rng = random.Random(cfg.perturb) if cfg.perturb is not None else None
        self.rec = {}
        self.rec_occ = {}
        self.stats = {"bound_checks": 0, "topk_rows_over_k": 0, "fix_rounds": [], "ops": {}}
        self._corpus = {}
        self.notes = {}

    def shuffle(self, rows):
        if self.rng is not None:
            self.rng.shuffle(rows)
        return rows

    def bound(self, op, ok, detail=""):
        self.stats["bound_checks"] += 1
        self.stats["ops"][op] = self.stats["ops"].get(op, 0) + 1
        if not ok:
            raise BoundViolation(f"{op}: {detail}")

    def corpus(self, tname, fld):
        key = (tname, fld)
        if key not in self._corpus:
            docs = {}
            for ref in self.graph.all_nodes(tname):
                p = self.graph.props(ref)
                if fld in p:
                    docs[ref] = S.analyze(p.get(fld))
            self._corpus[key] = S.FieldCorpus(docs)
        return self._corpus[key]


def eval_expr(e, row: dict, ctx: Ctx):
    if isinstance(e, Lit):
        return e.value
    if isinstance(e, Param):
        if e.name not in ctx.params:
            raise GQError("E_PARAM", f"unbound parameter ${e.name}")
        return ctx.params[e.name]
    if isinstance(e, Col):
        return row[e.name]
    if isinstance(e, Prop):
        b = eval_expr(e.base, row, ctx)
        if b is None:
            return None
        p = ctx.graph.props(b)
        return None if p is None else p.get(e.name)
    if isinstance(e, Meta):
        b = eval_expr(e.base, row, ctx)
        if b is None:
            return None
        if e.field == "@id":
            return b.id
        if e.field == "@type":
            return b.type
        if e.field == "@src":
            return b.src.id
        if e.field == "@dst":
            return b.dst.id
    if isinstance(e, Arith):
        return _arith(e.op, eval_expr(e.l, row, ctx), eval_expr(e.r, row, ctx), e, row, ctx)
    if isinstance(e, Neg):
        v = eval_expr(e.e, row, ctx)
        if v is None:
            return None
        if isinstance(v, int) and not isinstance(v, bool):
            lo, hi = INT_RANGE["I64"]
            if not lo <= -v <= hi:
                raise GQError("E_OVERFLOW", "integer overflow in unary minus")
        return -v
    if isinstance(e, Cmp):
        a, b = eval_expr(e.l, row, ctx), eval_expr(e.r, row, ctx)
        if a is None or b is None:
            return None
        if e.op == "=":
            return a == b
        if e.op == "!=":
            return a != b
        ka, kb = value_key(a), value_key(b)
        return {"<": ka < kb, "<=": ka <= kb, ">": ka > kb, ">=": ka >= kb}[e.op]
    if isinstance(e, And):
        a = eval_expr(e.l, row, ctx)
        if a is False:
            return False
        b = eval_expr(e.r, row, ctx)
        if b is False:
            return False
        if a is None or b is None:
            return None
        return True
    if isinstance(e, Or):
        a = eval_expr(e.l, row, ctx)
        if a is True:
            return True
        b = eval_expr(e.r, row, ctx)
        if b is True:
            return True
        if a is None or b is None:
            return None
        return False
    if isinstance(e, Not):
        a = eval_expr(e.e, row, ctx)
        return None if a is None else (not a)
    if isinstance(e, IsNull):
        v = eval_expr(e.e, row, ctx)
        return (v is not None) if e.neg else (v is None)
    if isinstance(e, InList):
        v, lst = eval_expr(e.e, row, ctx), eval_expr(e.lst, row, ctx)
        if v is None or lst is None:
            return None
        if v in lst:
            return True
        return None if any(x is None for x in lst) else False
    if isinstance(e, TermsQ):
        return S.make_terms(eval_expr(e.text, row, ctx), e.mode,
                            eval_expr(e.max_edits, row, ctx) if not isinstance(e.max_edits, int) else e.max_edits)
    if isinstance(e, Fn):
        args = [eval_expr(a, row, ctx) for a in e.args]
        n = e.name
        if n == "contains":
            return None if None in args else args[1] in args[0]
        if n == "starts_with":
            return None if None in args else args[0].startswith(args[1])
        if n == "if":
            return args[1] if args[0] is True else args[2]
        if n == "coalesce":
            return next((a for a in args if a is not None), None)
        if n == "concat":
            return None if None in args else "".join(args)
        if n == "match_terms":
            return S.matches(S.analyze(args[0]), args[1])
        if n == "search":
            return S.matches(S.analyze(args[0]), S.make_terms(args[1], "any", 0))
        if n == "fuzzy":   # fuzzy(f, q) with no budget is two edits, as today
            return S.matches(S.analyze(args[0]), S.make_terms(args[1], "any", args[2] if len(args) > 2 else 2))
    raise TypeError(f"cannot evaluate {e!r}")


def _arith(op, a, b, e, row, ctx):
    if a is None or b is None:
        return None
    ints = isinstance(a, int) and isinstance(b, int) and not isinstance(a, bool) and not isinstance(b, bool)
    if ints:
        if op in ("/", "%") and b == 0:
            raise GQError("E_DIV_ZERO", "integer division by zero")
        if op == "+":
            r = a + b
        elif op == "-":
            r = a - b
        elif op == "*":
            r = a * b
        else:
            q = abs(a) // abs(b)
            q = q if (a >= 0) == (b >= 0) else -q
            r = q if op == "/" else a - b * q
        # the static type decides the width; the prototype carries I64 for
        # literals and checks I32 when both operands are I32 columns
        width = "I64"
        lo, hi = INT_RANGE[width]
        if not lo <= r <= hi:
            raise GQError("E_OVERFLOW", f"integer overflow: {a} {op} {b}")
        return r
    a, b = float(a), float(b)
    try:
        if op == "+":
            r = a + b
        elif op == "-":
            r = a - b
        elif op == "*":
            r = a * b
        elif op == "/":
            if b == 0:
                raise GQError("E_NONFINITE", "float division by zero")
            r = a / b
        else:
            if b == 0:
                raise GQError("E_NONFINITE", "float remainder by zero")
            r = math.fmod(a, b)
    except OverflowError:
        raise GQError("E_NONFINITE", "float overflow")
    if not math.isfinite(r):
        raise GQError("E_NONFINITE", f"non-finite result {a} {op} {b}")
    return r


def _row_env(gamma: dict, names, row):
    d = dict(gamma)
    d.update(zip(names, row))
    return d


def evaluate(t, ctx: Ctx, gamma: Optional[dict] = None, gnames: tuple = ()) -> Rel:
    gamma = gamma or {}
    return _ev(t, ctx, gamma, gnames)


def _ev(t, ctx: Ctx, gamma: dict, gnames: tuple) -> Rel:
    sch = ctx.schema
    g = ctx.graph
    if isinstance(t, Env):
        return Rel(tuple(gnames), [tuple(gamma[n] for n in gnames)])
    if isinstance(t, Scan):
        rows = [(r,) for r in g.all_nodes(t.type)]
        ctx.shuffle(rows)
        ctx.bound("Scan", len(rows) == g.node_count(t.type))
        return Rel((t.var,), rows)
    if isinstance(t, Expand):
        src = _ev(t.src, ctx, gamma, gnames)
        xi = src.names.index(t.x)
        adj = g.adjacency()
        out = []
        fanout = 0
        for (ename, dirn) in t.sel:
            for lst in adj[(ename, dirn)].values():
                fanout = max(fanout, len(lst) if t.k else len({n for n, _ in lst}))
        if len(t.sel) > 1:
            fanout = fanout * len(t.sel)
        for row in src.rows:
            x = row[xi]
            if x is None:
                continue
            if t.k is not None:
                seen = set()
                hits = []
                for (ename, dirn) in t.sel:
                    for nb, er in adj[(ename, dirn)].get(x, []):
                        if (er, nb) in seen:
                            continue   # an undirected self-loop appears once
                        seen.add((er, nb))
                        hits.append((nb, er))
                ctx.shuffle(hits)
                out.extend(row + (nb, er) for nb, er in hits)
            else:
                ys = _reach(adj, t.sel, x, t.m, t.n)
                ys = list(ys)
                ctx.shuffle(ys)
                out.extend(row + (y,) for y in ys)
        # The table's bound is |R| * fanout^n. With set semantics over m..n
        # hops the reached set is bounded by the sum over each hop count.
        if len(out) > len(src.rows) * (fanout ** t.n):
            ctx.stats["expand_table_bound_exceeded"] = ctx.stats.get("expand_table_bound_exceeded", 0) + 1
        ctx.bound("Expand", len(out) <= len(src.rows) * sum(fanout ** h for h in range(t.m, t.n + 1)),
                  f"{len(out)} > {len(src.rows)}*sum fanout^h")
        names = src.names + (t.y,) + ((t.k,) if t.k else ())
        return Rel(names, out)
    if isinstance(t, Filter):
        src = _ev(t.src, ctx, gamma, gnames)
        out = [r for r in src.rows if eval_expr(t.pred, _row_env(gamma, src.names, r), ctx) is True]
        ctx.bound("Filter", len(out) <= len(src.rows))
        return Rel(src.names, out, src.ordered, dict(src.meta))
    if isinstance(t, Project):
        src = _ev(t.src, ctx, gamma, gnames)
        out = []
        for r in src.rows:
            env = _row_env(gamma, src.names, r)
            out.append(tuple(eval_expr(e, env, ctx) for _, e in t.items))
        ctx.bound("Project", len(out) == len(src.rows))
        return Rel(tuple(n for n, _ in t.items), out, src.ordered, dict(src.meta))
    if isinstance(t, Group):
        src = _ev(t.src, ctx, gamma, gnames)
        groups = {}
        order = []
        for r in src.rows:
            env = _row_env(gamma, src.names, r)
            key = tuple(eval_expr(e, env, ctx) for _, e in t.keys)   # null is a key value
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(env)
        if not t.keys and not groups:
            groups[()] = []
            order.append(())
        out = []
        for key in order:
            envs = groups[key]
            out.append(key + tuple(_agg(a, envs, ctx) for _, a in t.aggs))
        ctx.shuffle(out)
        if t.keys:
            ctx.bound("Group", len(out) <= len(src.rows))
        else:
            ctx.bound("Group", len(out) == 1, "exactly one row with no keys")
        return Rel(tuple(n for n, _ in t.keys) + tuple(n for n, _ in t.aggs), out)
    if isinstance(t, Correlate):
        outer = _ev(t.outer, ctx, gamma, gnames)
        out = []
        inner_names = None
        total = 0
        new_gnames = tuple(dict.fromkeys(gnames + outer.names))
        for r in outer.rows:
            g2 = _row_env(gamma, outer.names, r)
            inner = _ev(t.inner, ctx, g2, new_gnames)
            inner_names = inner.names
            total += len(inner.rows)
            out.extend(r + ir for ir in inner.rows)
        if inner_names is None:
            inner_names = tuple(n for n, _ in ctx.notes[("cols", id(t.inner))])
        ctx.bound("Correlate", len(out) == total)
        # Not ordered: the inner rows of one outer row arrive in physical
        # order, so an outer order is not a total order of the result.
        return Rel(outer.names + inner_names, out, outer.ordered and ctx.cfg.correlate_keeps_order)
    if isinstance(t, Union):
        a = _ev(t.left, ctx, gamma, gnames)
        b = _ev(t.right, ctx, gamma, gnames)
        if len(a.names) != len(b.names):
            raise GQError("TUNION", "arity")
        out = a.rows + b.rows
        ctx.shuffle(out)
        ctx.bound("Union", len(out) == len(a.rows) + len(b.rows))
        return Rel(a.names, out)
    if isinstance(t, Order):
        src = _ev(t.src, ctx, gamma, gnames)
        rows = _order_rows(src, t.keys, ctx, gamma, ctx.notes.get(("cols", id(t.src))))
        ctx.bound("Order", len(rows) == len(src.rows))
        return Rel(src.names, rows, True, dict(src.meta))
    if isinstance(t, Cut):
        src = _ev(t.src, ctx, gamma, gnames)
        k = eval_expr(t.k, {}, ctx)
        if k is None or k < 0:
            raise GQError("E_LIMIT", f"limit {k}")
        rows = src.rows if src.ordered else _order_rows(src, (), ctx, gamma, ctx.notes.get(("cols", id(t.src))))
        out = rows[:k]
        ctx.bound("Cut", len(out) == min(k, len(src.rows)))
        return Rel(src.names, out, True, dict(src.meta))
    if isinstance(t, Score):
        src = _ev(t.src, ctx, gamma, gnames)
        x, xt = ctx.notes[("x", id(t))]
        xi = src.names.index(x)
        out = []
        for r in src.rows:
            env = _row_env(gamma, src.names, r)
            s = _score(t.kind, xt.arg, t.field, r[xi], eval_expr(t.q, env, ctx), ctx)
            if s is not None:
                out.append(r + (s,))
        ctx.bound("Score", len(out) <= len(src.rows))
        return Rel(src.names + (t.out,), out)
    if isinstance(t, ApproxTopK):
        src = _ev(t.src, ctx, gamma, gnames)
        x, xt = ctx.notes[("x", id(t))]
        xi = src.names.index(x)
        k = eval_expr(t.k, {}, ctx)
        scored = []
        for r in src.rows:
            env = _row_env(gamma, src.names, r)
            s = _score(t.kind, xt.arg, t.field, r[xi], eval_expr(t.q, env, ctx), ctx)
            if s is not None:
                scored.append(r + (s,))
        asc = t.kind == "vector"
        best = {}
        for r in scored:
            best.setdefault(r[xi], r[-1])
        ranked = sorted(best, key=functools.cmp_to_key(
            lambda a, b: _cmp_score(best[a], best[b], asc) or _cmp(value_key(a), value_key(b))))
        if ctx.cfg.topk_unit == "rows":
            rows = sorted(scored, key=functools.cmp_to_key(
                lambda a, b: _cmp_score(a[-1], b[-1], asc) or _cmp(value_key(a[xi]), value_key(b[xi]))))
            out = rows[:k]
        else:
            keep = set(ranked[:k])
            out = [r for r in scored if r[xi] in keep]
            if len(out) > k:
                ctx.stats["topk_rows_over_k"] += 1
        targets = {r[xi] for r in out}
        ctx.bound("ApproxTopK", len(targets) <= k)
        return Rel(src.names + (t.out,), out, False,
                   {"recall": t.recall if t.recall is not None else "engine-policy",
                    "exhaustive": True, "achieved": "exact"})
    if isinstance(t, Fuse):
        arms = [_ev(a, ctx, gamma, gnames) for a in t.arms]
        x = ctx.notes[("x", id(t))][0]
        weights = t.weights or tuple(1.0 for _ in t.arms)
        total = {}
        for arm_t, arm, w in zip(t.arms, arms, weights):
            xi = arm.names.index(x)
            spec = rank_spec(arm_t)
            if spec[0] == "col":
                si = arm.names.index(spec[1])
                asc = spec[2] == "asc"
                best = {}
                for r in arm.rows:
                    if r[xi] is None:
                        continue
                    best.setdefault(r[xi], r[si])
                ranked = sorted(best, key=functools.cmp_to_key(
                    lambda a, b: _cmp_score(best[a], best[b], asc) or _cmp(value_key(a), value_key(b))))
            else:
                ranked = []
                for r in arm.rows:
                    if r[xi] is not None and r[xi] not in ranked:
                        ranked.append(r[xi])
            for rank, target in enumerate(ranked, 1):
                total[target] = total.get(target, 0.0) + w / (t.k + rank)
        out = [(x_, s) for x_, s in total.items()]
        ctx.shuffle(out)
        ctx.bound("Fuse", len(out) <= sum(len(a.rows) for a in arms))
        return Rel((x, "score"), out)
    if isinstance(t, RecRef):
        key = (t.name, t.occ)
        if key in ctx.rec_occ:
            return ctx.rec_occ[key]
        return ctx.rec[t.name]
    if isinstance(t, Fix):
        return _eval_fix(t, ctx, gamma, gnames)
    if isinstance(t, Inlined):
        frame = {pn: eval_expr(a, gamma, ctx) for (pn, _), a in zip(t.params, t.args)}
        saved = ctx.params
        ctx.params = frame
        try:
            return _ev(t.body, ctx, {}, ())
        finally:
            ctx.params = saved
    if isinstance(t, Write):
        raise GQError("A7", "a sink inside a read")
    raise TypeError(f"cannot evaluate {t!r}")


def _ptypes(ctx):
    out = {}
    for k, v in ctx.params.items():
        out[k] = _value_type(v)
    return out


def _value_type(v):
    if v is None:
        return STRING.null()
    if isinstance(v, bool):
        return BOOL
    if isinstance(v, int):
        return I64
    if isinstance(v, float):
        return F64
    if isinstance(v, str):
        return STRING
    if isinstance(v, tuple):
        return T("Vector", len(v))
    if isinstance(v, S.TermsValue):
        return T("Terms")
    if isinstance(v, NodeRef):
        return node_t(v.type)
    return STRING


def _reach(adj, sel, x, m, n):
    """Shortest-distance reachability, as edition 2 documents it: a node is
    never re-reached through a cycle; the start node is returned only
    through its own self-loop, which counts as one hop."""
    dist = {x: 0}
    frontier = [x]
    out = set()
    self_loop = False
    for hop in range(1, n + 1):
        nxt = []
        for u in frontier:
            for ename, dirn in sel:
                for v, _ in adj[(ename, dirn)].get(u, []):
                    if v == x and u == x:
                        self_loop = True
                    if v not in dist:
                        dist[v] = hop
                        nxt.append(v)
        frontier = nxt
    for v, d in dist.items():
        if v != x and m <= d <= n:
            out.add(v)
    if self_loop and m <= 1:
        out.add(x)
    return out


def _agg(a: Agg, envs, ctx):
    if a.fn == "count" and a.arg is None:
        return len(envs)
    vals = [eval_expr(a.arg, e, ctx) for e in envs]
    vals = [v for v in vals if v is not None]
    if a.distinct:
        vals = list(dict.fromkeys(vals))
    if a.fn == "count":
        return len(vals)
    if not vals:
        return None
    if a.fn == "sum":
        return float(sum(vals))
    if a.fn == "avg":
        return float(sum(vals)) / len(vals)
    if a.fn == "min":
        return min(vals, key=value_key)
    if a.fn == "max":
        return max(vals, key=value_key)
    raise GQError("T_AGG", a.fn)


def _cmp(a, b):
    return (a > b) - (a < b)


def _cmp_score(a, b, asc):
    c = _cmp(a, b)
    return c if asc else -c


def _cmp_nullable(a, b, desc, nulls):
    if nulls is None:
        nulls = "last" if desc else "first"    # edition 2: ascending puts nulls first
    if a is None or b is None:
        if a is None and b is None:
            return 0
        first = -1 if nulls == "first" else 1
        return first if a is None else -first
    c = _cmp(value_key(a), value_key(b))
    return -c if desc else c


def _order_rows(src: Rel, keys, ctx: Ctx, gamma, types=None):
    envs = [_row_env(gamma, src.names, r) for r in src.rows]
    keyvals = [[eval_expr(e, env, ctx) for e, _, _ in keys] for env in envs]
    # tie columns, from the static column types
    node_idx, ref_idx = [], []
    for i, (_, ty) in enumerate(types or ()):
        if ty.kind == "Node":
            node_idx.append(i)
        if ty.is_ref:
            ref_idx.append(i)
    if ctx.cfg.order_ties == "rfc":
        tie_idx = node_idx
    else:
        tie_idx = ref_idx + [i for i in range(len(src.names)) if i not in ref_idx]
    idx = list(range(len(src.rows)))

    def cmp(i, j):
        for (e, d, nl), a, b in zip(keys, keyvals[i], keyvals[j]):
            c = _cmp_nullable(a, b, d == "desc", nl)
            if c:
                return c
        for ci in tie_idx:
            c = _cmp_nullable(src.rows[i][ci], src.rows[j][ci], False, "first")
            if c:
                return c
        return 0
    idx.sort(key=functools.cmp_to_key(cmp))    # stable: remaining ties keep input order
    return [src.rows[i] for i in idx]


def _score(kind, tname, fld, x, q, ctx: Ctx):
    if x is None:
        return None
    props = ctx.graph.props(x)
    val = props.get(fld) if props else None
    if kind == "l2":
        if val is None:
            return None    # a null field value drops the row
        return S.vector_distance("l2", val, q)
    if kind == "vector":
        if val is None:
            return None
        return S.vector_distance(ctx.schema.field_distance(tname, fld), val, q)
    if kind == "bm25":
        qv = q if isinstance(q, S.TermsValue) else S.make_terms(q, "any", 0)
        toks = S.analyze(val)
        if not S.matches(toks, qv):
            return None    # membership is the terms relation
        return S.bm25_v1(ctx.corpus(tname, fld), toks, qv)
    raise GQError("T_OPTION", kind)


# ====================================================================== Fix

def _fix_shape(body):
    """(kind, inner, top) for semi-naive: kind is 'lattice' for a top Group
    whose aggregates are all min/max, 'set' otherwise."""
    if isinstance(body, Group) and body.aggs and all(a.fn in ("min", "max") for _, a in body.aggs):
        return "lattice", body.src, body
    if isinstance(body, (Group, Project)):
        return "set", body.src, body
    return "set", body, None


def _eval_fix(t: Fix, ctx: Ctx, gamma, gnames) -> Rel:
    cfg = ctx.cfg
    names_of = {}
    saved_rec, saved_occ = dict(ctx.rec), dict(ctx.rec_occ)
    try:
        # column names of each recursive relation
        for n, b in zip(t.names, t.bodies):
            names_of[n] = None
        state = {n: [] for n in t.names}

        def rel(n, rows):
            return Rel(names_of[n] if names_of[n] is not None else (), list(rows))

        def bind_all(st):
            ctx.rec = dict(saved_rec)
            for n in t.names:
                ctx.rec[n] = rel(n, st[n])
            ctx.rec_occ = dict(saved_occ)

        found = ctx.notes[("fix", id(t))]
        for n in t.names:
            names_of[n] = tuple(c for c, _ in found[n])
        rounds = 0
        if cfg.fix_strategy == "naive":
            cur = {n: [] for n in t.names}
            while True:
                rounds += 1
                _fix_budget(t, rounds, cfg, cur)
                bind_all(cur)
                nxt = {}
                for n, b in zip(t.names, t.bodies):
                    rows = _ev(b, ctx, gamma, gnames).rows
                    nxt[n] = _canon(rows, cfg.fix_set)
                if all(_same(nxt[n], cur[n], cfg.fix_set) for n in t.names):
                    break
                cur = nxt
            final = cur
        else:
            shapes = {n: _fix_shape(b) for n, b in zip(t.names, t.bodies)}
            full = {n: {} for n in t.names}     # key -> row (lattice) or row -> row (set)
            delta = {n: [] for n in t.names}
            # round 1: every recursive relation empty
            rounds = 1
            _fix_budget(t, rounds, cfg, {n: [] for n in t.names})
            bind_all({n: [] for n in t.names})
            for n, b in zip(t.names, t.bodies):
                rows = _ev(b, ctx, gamma, gnames).rows
                delta[n] = _merge(full[n], rows, shapes[n], names_of[n], cfg.fix_set)
            while any(delta[n] for n in t.names):
                rounds += 1
                _fix_budget(t, rounds, cfg, {n: list(full[n].values()) for n in t.names})
                new_delta = {}
                for n, b in zip(t.names, t.bodies):
                    kind, inner, top = shapes[n]
                    occs = [r for r in walk_terms(b) if isinstance(r, RecRef) and r.name in t.names]
                    cand = []
                    for occ in occs:
                        if not delta[occ.name]:
                            continue
                        bind_all({m: list(full[m].values()) for m in t.names})
                        ctx.rec_occ[(occ.name, occ.occ)] = rel(occ.name, delta[occ.name])
                        cand.extend(_ev(b, ctx, gamma, gnames).rows)
                    new_delta[n] = _merge(full[n], cand, shapes[n], names_of[n], cfg.fix_set)
                delta = new_delta
            final = {n: list(full[n].values()) for n in t.names}
        ctx.stats["fix_rounds"].append(rounds)
        rows = list(final[t.select])
        # The table's bound: the domain of the reference columns (a min/max
        # column adds one value per key), or the budget when a column is computed.
        body = t.bodies[t.names.index(t.select)]
        lattice = {c for c, a in body.aggs} if isinstance(body, Group) else set()
        dom, checkable = 1, True
        for cname, ty in found[t.select]:
            if cname in lattice:
                continue
            if ty.kind == "Node":
                dom *= max(1, ctx.graph.node_count(ty.arg))
            elif ty.kind == "Edge":
                dom *= max(1, sum(len(v) for v in ctx.graph.edges.values()))
            else:
                checkable = False
        if checkable:
            ctx.bound("Fix", len(rows) <= dom, f"{len(rows)} rows over a domain of {dom}")
        elif t.budget is None:
            ctx.stats["fix_unbounded_columns"] = ctx.stats.get("fix_unbounded_columns", 0) + 1
        ctx.shuffle(rows)
        return Rel(names_of[t.select], rows)
    finally:
        ctx.rec, ctx.rec_occ = saved_rec, saved_occ


def _canon(rows, as_set):
    if as_set:
        return list(dict.fromkeys(rows))
    return list(rows)


def _same(a, b, as_set):
    if as_set:
        return set(a) == set(b)
    from collections import Counter
    return Counter(a) == Counter(b)


def _merge(full: dict, rows, shape, names, as_set):
    kind, inner, top = shape
    changed = []
    if kind == "lattice":
        nk = len(top.keys)
        fns = [a.fn for _, a in top.aggs]
        for r in rows:
            key = r[:nk]
            if key not in full:
                full[key] = r
                changed.append(r)
                continue
            old = full[key]
            vals = list(old)
            for i, fn in enumerate(fns):
                a, b = old[nk + i], r[nk + i]
                if a is None:
                    v = b
                elif b is None:
                    v = a
                else:
                    v = min(a, b, key=value_key) if fn == "min" else max(a, b, key=value_key)
                vals[nk + i] = v
            vals = tuple(vals)
            if vals != old:
                full[key] = vals
                changed.append(vals)
        return list(dict.fromkeys(changed))
    if not as_set:
        # bag semantics: every derived row is new
        for r in rows:
            full[(len(full), r)] = r
            changed.append(r)
        return changed
    for r in rows:
        if r not in full:
            full[r] = r
            changed.append(r)
    return changed


def _fix_budget(t: Fix, rounds, cfg: Config, cur):
    if t.budget is not None and rounds > t.budget:
        raise GQError("E_FIX_BUDGET", f"{t.select} exceeded @budget(rounds = {t.budget})")
    if rounds > cfg.fix_hard_cap or sum(len(v) for v in cur.values()) > 50_000:
        raise GQError("E_FIX_DIVERGE", f"{t.select} did not converge within the internal cap")


# ---------------------------------------------------------------- admission

def computed_columns(body, names_rec=()):
    """Output columns of a recursive body that are computed rather than read:
    a literal, an arithmetic term or a function anywhere in their lineage."""
    if isinstance(body, Group):
        inner = computed_columns(body.src, names_rec)
        out = set()
        for n, e in body.keys:
            if not _is_read(e) or (expr_cols(e) & inner):
                out.add(n)
        for n, a in body.aggs:
            if a.arg is None or not _is_read(a.arg) or (expr_cols(a.arg) & inner):
                out.add(n)
        return out
    if isinstance(body, Project):
        inner = computed_columns(body.src, names_rec)
        return {n for n, e in body.items if not _is_read(e) or (expr_cols(e) & inner)}
    if isinstance(body, Union):
        lc = computed_columns(body.left, names_rec)
        rc = computed_columns(body.right, names_rec)
        # names from the left, by position
        return lc | rc
    if isinstance(body, Correlate):
        return computed_columns(body.outer, names_rec) | computed_columns(body.inner, names_rec)
    if isinstance(body, (Filter, Order, Cut, Expand, Score, ApproxTopK)):
        return computed_columns(body.src, names_rec)
    return set()


def _is_read(e):
    return isinstance(e, (Col, Prop, Meta, Param))


def admit_fix(fix: Fix, rule: str = "proposed"):
    """The monotonicity check. 'rfc': refuse `not` and any aggregate but
    min/max over the recursive relation, and a computed column without
    min/max and a budget. 'proposed' adds the rules this spike found
    necessary."""
    names = set(fix.names)
    for n, b in zip(fix.names, fix.bodies):
        lattice = set()
        if isinstance(b, Group):
            lattice = {c for c, a in b.aggs if a.fn in ("min", "max")}
        for sub in walk_terms(b):
            if isinstance(sub, Group) and contains_rec(sub.src, names):
                bad = [a.fn for _, a in sub.aggs if a.fn not in ("min", "max")]
                if bad:
                    if rule == "proposed" and _is_exists_shape(b, sub):
                        pass
                    else:
                        raise GQError("T70", f"{n}: aggregate {bad[0]} over the recursive relation "
                                             f"(a not or count over its own rows)")
                if rule == "proposed" and sub is not b and sub.aggs and not _is_exists_shape(b, sub):
                    raise GQError("T70", f"{n}: an aggregate over the recursive relation below the read's own return")
            if rule == "proposed":
                if isinstance(sub, (Order, Cut, ApproxTopK, Fuse)) and any(contains_rec(c, names) for c in children(sub)):
                    raise GQError("T70", f"{n}: {type(sub).__name__} over the recursive relation is not monotone")
                if isinstance(sub, (Filter,)) and contains_rec(sub.src, names) and (expr_cols(sub.pred) & lattice):
                    raise GQError("T70", f"{n}: a filter reads the min/max column {sorted(expr_cols(sub.pred) & lattice)} "
                                         f"of the recursive relation")
                if isinstance(sub, Group) and sub is not b and contains_rec(sub.src, names) and \
                        any(expr_cols(e) & lattice for _, e in sub.keys):
                    raise GQError("T70", f"{n}: a group key reads a min/max column")
        comp = computed_columns(b)
        if comp:
            if isinstance(b, Group):
                agg_cols = {c for c, a in b.aggs if a.fn in ("min", "max")}
                stray = comp - agg_cols
            else:
                stray = comp
            if stray:
                raise GQError("T70", f"{n}: computed column(s) {sorted(stray)} not under min or max")
            if fix.budget is None:
                raise GQError("T70", f"{n}: computed column(s) {sorted(comp)} need @budget(rounds = n)")
    return True


def _is_exists_shape(body, group):
    """`exists { p }` desugars to Correlate(R, Group(p, [], [count])) under a
    Filter `> 0`; the proposed rule admits it as positive."""
    if group.keys or len(group.aggs) != 1 or group.aggs[0][1].fn != "count":
        return False
    col = group.aggs[0][0]
    for sub in walk_terms(body):
        if isinstance(sub, Filter) and isinstance(sub.pred, Cmp) and sub.pred.op == ">" \
                and sub.pred.l == Col(col) and sub.pred.r == Lit(0, I64) \
                and isinstance(sub.src, Correlate) and sub.src.inner is group:
            return True
    return False


# ====================================================================== writes

def run_write(w: Write, graph: Graph, cfg: Config = PROPOSED, params=None):
    ctx = Ctx(graph, cfg, params)
    tenv = TEnv(graph.schema, _ptypes(ctx), cfg=cfg)
    type_write(w, tenv)
    ctx.notes = tenv.notes
    src = _ev(w.src, ctx, {}, ())
    sch = graph.schema
    sets = {}           # (ref, prop) -> value
    upserts = {}        # (type, key) -> props
    deletes = []
    edges = []
    counts = {}
    for s in w.stmts:
        n = 0
        for r in src.rows:
            env = dict(zip(src.names, r))
            if isinstance(s, Delete):
                ref = env[s.x]
                if ref is not None:
                    if ref not in deletes:
                        deletes.append(ref)
                    n += 1
            elif isinstance(s, Update):
                ref = env[s.x]
                if ref is None:
                    continue
                n += 1
                for p, e in s.assigns:
                    v = eval_expr(e, env, ctx)
                    k = (ref, p)
                    if k in sets and sets[k] != v and cfg.write_conflicts == "refuse":
                        raise GQError("E_WRITE_CONFLICT", f"{ref!r}.{p} assigned {sets[k]!r} and {v!r} in one write")
                    sets[k] = v
            elif isinstance(s, Insert):
                n += 1
                vals = {p: eval_expr(e, env, ctx) for p, e in s.assigns}
                if s.type in sch.nodes:
                    key = (s.type, vals[sch.nodes[s.type].key])
                    if key in upserts and upserts[key] != vals and cfg.write_conflicts == "refuse":
                        raise GQError("E_WRITE_CONFLICT", f"two upserts of {key} with different values")
                    upserts[key] = vals
                else:
                    edges.append((s.type, vals))
        counts[type(s).__name__] = counts.get(type(s).__name__, 0) + n
        ctx.bound(type(s).__name__, n <= len(src.rows) or isinstance(s, Insert) and n == len(src.rows))
    if cfg.write_conflicts == "refuse":
        for ref, p in sets:
            if ref in deletes:
                raise GQError("E_WRITE_CONFLICT", f"{ref!r} is updated and deleted in one write")
    new = graph.copy()
    for ref in deletes:
        if isinstance(ref, NodeRef):
            new.nodes[ref.type].pop(ref.id, None)
            for et in new.edges:
                e = sch.edge_by_type[et]
                new.edges[et] = [x for x in new.edges[et]
                                 if not ((e.src == ref.type and x.src == ref.id) or (e.dst == ref.type and x.dst == ref.id))]
        else:
            new.edges[ref.type] = [x for x in new.edges[ref.type] if x.id != ref.id]
    for (ref, p), v in sets.items():
        if ref in deletes:
            continue
        if isinstance(ref, NodeRef):
            if ref.id in new.nodes[ref.type]:
                new.nodes[ref.type][ref.id][p] = v
        else:
            for x in new.edges[ref.type]:
                if x.id == ref.id:
                    x.props[p] = v
    for (tname, key), vals in upserts.items():
        cur = new.nodes[tname].get(key)
        if cur is None:
            for p, pt in sch.nodes[tname].props.items():
                if not pt.nullable and p not in vals:
                    raise GQError("E_REQUIRED", f"{tname}.{p} is required")
            new.nodes[tname][key] = dict(vals)
        else:
            cur.update(vals)
    for ename, vals in edges:
        et = sch.edge_by_type[ename]
        s, d = vals["from"], vals["to"]
        s = s.id if isinstance(s, NodeRef) else s
        d = d.id if isinstance(d, NodeRef) else d
        if s not in new.nodes[et.src] or d not in new.nodes[et.dst]:
            raise GQError("E_ENDPOINT", f"{ename} endpoint missing")
        new.add_edge(ename, s, d, **{k: v for k, v in vals.items() if k not in ("from", "to")})
    new._adj_cache = None
    return new, counts


# ====================================================================== checking

def check_read(t, schema, params_types=None, cfg: Config = PROPOSED):
    return type_term(t, TEnv(schema, dict(params_types or {}), cfg=cfg))


def run(t, graph: Graph, params=None, cfg: Config = PROPOSED, params_types=None) -> Rel:
    return run_ctx(t, graph, params, cfg, params_types)[0]


def run_ctx(t, graph, params=None, cfg: Config = PROPOSED, params_types=None):
    ctx = Ctx(graph, cfg, params)
    pt = dict(params_types or {})
    for k, v in (params or {}).items():
        pt.setdefault(k, _value_type(v))
    tenv = TEnv(graph.schema, pt, cfg=cfg)
    type_term(t, tenv)
    ctx.notes = tenv.notes
    return _ev(t, ctx, {}, ()), ctx
