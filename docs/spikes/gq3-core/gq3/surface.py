"""A small typed AST for the surface of RFC GQ 3 section 3, and its
desugaring to core terms, following the derived-forms table row by row.

`Desugarer(alternation="rfc")` desugars `$a <e> $b` and `$a (e1 | e2) $b`
to a `Union` of `Expand`s, as the table says. `alternation="selector"` gives
one `Expand` over an edge selector, the edition-2 behaviour (finding F3).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Optional

from .model import GQError, I64, T, node_t
from . import core as C
from .core import (Agg, And, Arith, Cmp, Col, Fn, InList, IsNull, Lit, Meta, Neg, Not, Or,
                   Param, Prop, TermsQ, conj)


# ====================================================================== AST

# clauses
@dataclass(frozen=True)
class SBind:
    var: str
    type: str
    props: tuple = ()          # ((prop, expr), ...): the braces


@dataclass(frozen=True)
class STriple:
    src: str
    edges: tuple               # traversal names; more than one is an alternation
    dst: str
    hops: tuple = (1, 1)
    edge_var: Optional[str] = None
    either: bool = False       # `<e>`


@dataclass(frozen=True)
class SFilter:
    expr: Any


@dataclass(frozen=True)
class SExists:
    clauses: tuple


@dataclass(frozen=True)
class SNot:
    clauses: tuple


@dataclass(frozen=True)
class SOptional:
    clauses: tuple


@dataclass(frozen=True)
class SLet:
    var: str
    value: Any                 # a scalar expression or a relation expression


@dataclass(frozen=True)
class SFrom:
    rel: Any


# block values in expressions
@dataclass(frozen=True)
class SBlock:
    agg: str                   # count sum avg min max exists
    arg: Any
    clauses: tuple


# relation expressions
@dataclass(frozen=True)
class SRead:
    match: tuple
    ret: tuple                 # ((expr, alias or None), ...)
    distinct: bool = False
    order: tuple = ()          # ((expr, "asc"|"desc"), ...)
    limit: Any = None


@dataclass(frozen=True)
class SInline:
    read: SRead


@dataclass(frozen=True)
class SCall:
    name: str
    args: tuple


@dataclass(frozen=True)
class SRelVar:
    name: str


@dataclass(frozen=True)
class SL2:
    rel: Any
    field: str
    q: Any


@dataclass(frozen=True)
class SBm25:
    rel: Any
    field: str
    q: Any


@dataclass(frozen=True)
class SNearest:
    rel: Any
    field: str
    q: Any
    k: Any = None
    recall: Optional[float] = None


@dataclass(frozen=True)
class SRrf:
    arms: tuple
    k: int = 60
    weights: Optional[tuple] = None


@dataclass(frozen=True)
class SUnion:
    arms: tuple


SREL = (SInline, SCall, SRelVar, SL2, SBm25, SNearest, SRrf, SUnion)


# writes
@dataclass(frozen=True)
class SInsert:
    type: str
    assigns: tuple


@dataclass(frozen=True)
class SUpdate:
    var: str
    assigns: tuple


@dataclass(frozen=True)
class SDelete:
    var: str


@dataclass(frozen=True)
class Bare:
    """An edition-2 bare property or meta-field inside a `where`."""
    name: str


@dataclass(frozen=True)
class SDeleteWhere:            # edition 2
    type: str
    pred: Any


@dataclass(frozen=True)
class SUpdateWhere:            # edition 2
    type: str
    assigns: tuple
    pred: Any


@dataclass(frozen=True)
class SWriteBody:
    clauses: tuple
    stmts: tuple


@dataclass(frozen=True)
class SQuery:
    name: str
    params: tuple              # ((name, T), ...)
    body: Any                  # SRead | SWriteBody
    budget_rounds: Optional[int] = None
    as_commit: bool = False


# ====================================================================== helpers

def item_name(expr, alias):
    if alias is not None:
        return alias
    if isinstance(expr, Col):
        return expr.name
    if isinstance(expr, Prop) and isinstance(expr.base, Col):
        return f"{expr.base.name}.{expr.name}"
    if isinstance(expr, Meta) and isinstance(expr.base, Col):
        return f"{expr.base.name}.{expr.field}"
    if isinstance(expr, Agg):
        inner = item_name(expr.arg, None) if expr.arg is not None else ""
        return f"{expr.fn}({inner})"
    raise GQError("T43", f"return item {expr!r} needs an alias")


def has_agg(e) -> bool:
    if isinstance(e, Agg):
        return True
    if isinstance(e, (SBlock,)) or e is None or isinstance(e, (Lit, Param, Col)):
        return False
    if isinstance(e, tuple):
        return any(has_agg(x) for x in e)
    if hasattr(e, "__dataclass_fields__"):
        return any(has_agg(getattr(e, f)) for f in e.__dataclass_fields__)
    return False


def surface_vars(e, out=None, inside_blocks=True):
    """Variable names an expression or clause list mentions."""
    out = set() if out is None else out
    if isinstance(e, Col):
        out.add(e.name)
    elif isinstance(e, (SBind,)):
        out.add(e.var)
        for _, v in e.props:
            surface_vars(v, out)
    elif isinstance(e, STriple):
        out.update({e.src, e.dst} | ({e.edge_var} if e.edge_var else set()))
    elif isinstance(e, tuple):
        for x in e:
            surface_vars(x, out, inside_blocks)
    elif isinstance(e, SBlock):
        if inside_blocks:
            surface_vars(e.arg, out)
            surface_vars(e.clauses, out)
    elif hasattr(e, "__dataclass_fields__") and not isinstance(e, (Lit, Param)):
        for f in e.__dataclass_fields__:
            surface_vars(getattr(e, f), out, inside_blocks)
    return out


def binds(clause) -> set:
    if isinstance(clause, SBind):
        return {clause.var}
    if isinstance(clause, STriple):
        return {clause.src, clause.dst} | ({clause.edge_var} if clause.edge_var else set())
    if isinstance(clause, SLet):
        return {clause.var}
    if isinstance(clause, SOptional):
        out = set()
        for c in clause.clauses:
            out |= binds(c)
        return out
    return set()


def calls_in(x, out=None):
    out = set() if out is None else out
    if isinstance(x, SCall):
        out.add(x.name)
        for a in x.args:
            calls_in(a, out)
    elif isinstance(x, tuple):
        for y in x:
            calls_in(y, out)
    elif hasattr(x, "__dataclass_fields__") and not isinstance(x, (Lit, T)):
        for f in x.__dataclass_fields__:
            calls_in(getattr(x, f), out)
    return out


# ====================================================================== desugarer

@dataclass
class _St:
    term: Any
    cols: list                 # column names of term, in order
    scope: dict                # name -> T for every visible name (gamma and cols)
    gamma: tuple               # ((name, T), ...) visible from outside
    rels: dict                 # let-named relations


class Desugarer:
    def __init__(self, schema, params: dict, calls: Optional[dict] = None,
                 alternation: str = "rfc", cfg: C.Config = C.PROPOSED, call_returns: Optional[dict] = None):
        self.schema = schema
        self.params = dict(params)
        self.calls = dict(calls or {})
        self.call_returns = dict(call_returns or {})
        self.alternation = alternation
        self.cfg = cfg
        self.n = 0

    # ---------------------------------------------------------------- utils
    def fresh(self, base="_b"):
        self.n += 1
        return f"{base}{self.n}"

    def tenv(self, gamma):
        return C.TEnv(self.schema, self.params, tuple(gamma), cfg=self.cfg, calls=self.calls)

    def rel_cols(self, term, gamma):
        try:
            return C.type_term(term, self.tenv(gamma))
        except C._Pending:
            # first pass over a recursive read: names from the callee's return,
            # types unknown
            names = self._pending_names(term, tuple(x for x, _ in gamma))
            if names is None:
                raise
            return tuple((n, C.UNKNOWN) for n in names)

    def _pending_names(self, term, gamma_names=()):
        """Column names of a term over a pending recursive call, without types."""
        n = lambda t: self._pending_names(t, gamma_names)
        if isinstance(term, C.Call):
            return self.call_returns.get(term.name)
        if isinstance(term, C.Env):
            return tuple(gamma_names)
        if isinstance(term, C.Scan):
            return (term.var,)
        if isinstance(term, C.Project):
            return tuple(x for x, _ in term.items)
        if isinstance(term, C.Group):
            return tuple(x for x, _ in term.keys) + tuple(x for x, _ in term.aggs)
        if isinstance(term, (C.Filter, C.Order, C.Cut)):
            return n(term.src)
        if isinstance(term, C.Expand):
            s = n(term.src)
            return None if s is None else s + (term.y,) + ((term.k,) if term.k else ())
        if isinstance(term, (C.Score, C.ApproxTopK)):
            s = n(term.src)
            return None if s is None else s + (term.out,)
        if isinstance(term, C.Correlate):
            a, b = n(term.outer), n(term.inner)
            return None if a is None or b is None else a + b
        if isinstance(term, C.Union):
            return n(term.left) or n(term.right)
        return None

    def edge(self, name):
        if name not in self.schema.edges:
            raise GQError("T1", f"unknown edge {name}")
        return self.schema.edges[name]

    def matches_type(self, t: T, tname: str) -> bool:
        if t.kind == "Unknown":
            return True
        if t.kind != "Node":
            return False
        return t.arg == tname or tname in self.schema.implementors(t.arg) or \
            t.arg in self.schema.implementors(tname)

    # ---------------------------------------------------------------- reads
    def read(self, r: SRead, gamma=(), rels=None) -> Any:
        st = _St(C.Env(), [n for n, _ in gamma], dict(gamma), tuple(gamma), dict(rels or {}))
        st = self.clauses(r.match, st)
        return self.ret(r, st)

    def clauses(self, clauses, st: _St) -> _St:
        all_bound = set()
        for c in clauses:
            all_bound |= binds(c)
        pending = list(clauses)
        progress = True
        while pending and progress:
            progress = False
            for c in list(pending):
                if self.ready(c, st, all_bound):
                    st = self.clause(c, st)
                    pending.remove(c)
                    progress = True
                    break
                if isinstance(c, (SBind, STriple, SFrom, SLet)):
                    break
        if pending:
            raise GQError("T_SCOPE", f"clause reads an unbound variable: {pending[0]!r}")
        return st

    def ready(self, c, st, all_bound):
        # Binders run in written order. `from` and `let` read only what is
        # bound before them in the text (section 3, Scope and correlation);
        # filters and blocks wait for every match variable they mention, as
        # edition 2's order-independent conjunction does.
        if isinstance(c, (SBind, STriple, SFrom, SLet)):
            return True
        need = surface_vars(c) & (all_bound - binds(c))
        return need <= set(st.scope)

    def _start(self, st: _St, term):
        """The first relation of a read with no outer bindings is the term
        itself; otherwise it is correlated with the current rows."""
        if isinstance(st.term, C.Env) and not st.cols:
            return term
        return C.Correlate(st.term, term)

    def clause(self, c, st: _St) -> _St:
        sch = self.schema
        if isinstance(c, SBind):
            if c.var in st.scope:
                t = st.scope[c.var]
                term = st.term
                if not self.matches_type(t, c.type):
                    raise GQError("T_TYPE", f"{c.var} is {t}, not {c.type}")
                preds = [Cmp("=", Prop(Col(c.var), p), v) for p, v in c.props]
                if t.kind == "Node" and t.arg != c.type and sch.is_node_type(c.type):
                    preds.insert(0, InList(Meta(Col(c.var), "@type"),
                                           Lit(tuple(sch.implementors(c.type)), T("List", "String"))))
                if preds:
                    term = C.Filter(term, conj(preds))
                return replace(st, term=term)
            if not sch.is_node_type(c.type):
                raise GQError("T1", f"unknown node type {c.type}")
            term = self._start(st, C.Scan(c.type, c.var))
            if c.props:
                term = C.Filter(term, conj([Cmp("=", Prop(Col(c.var), p), v) for p, v in c.props]))
            return replace(st, term=term, cols=st.cols + [c.var],
                           scope={**st.scope, c.var: node_t(c.type)})
        if isinstance(c, STriple):
            return self.triple(c, st)
        if isinstance(c, SFilter):
            cols0 = list(st.cols)
            term, e, hoisted = self.hoist(c.expr, st)
            term = C.Filter(term, e)
            if hoisted:
                term = C.Project(term, tuple((n, Col(n)) for n in cols0))
            return replace(st, term=term)
        if isinstance(c, SExists):
            return self.clause(SFilter(SBlock("exists", None, c.clauses)), st)
        if isinstance(c, SNot):
            return self.clause(SFilter(Not(SBlock("exists", None, c.clauses))), st)
        if isinstance(c, SLet):
            if isinstance(c.value, SREL):
                rel = self.rel(c.value, st)
                return replace(st, rels={**st.rels, c.var: rel})
            if c.var in st.scope:
                raise GQError("T68", f"{c.var} is already in scope")
            cols0 = list(st.cols)
            term, e, _ = self.hoist(c.value, st)
            scope_now = {**st.scope}
            for n in self._hoisted_types:
                scope_now[n] = self._hoisted_types[n]
            ty = C.type_expr(e, scope_now, self.tenv(()))
            if ty.kind == "Terms" or isinstance(c.value, SREL):
                raise GQError("T69", "a relation or terms value where a scalar is read")
            term = C.Project(term, tuple((n, Col(n)) for n in cols0) + ((c.var, e),))
            return replace(st, term=term, cols=cols0 + [c.var], scope={**st.scope, c.var: ty})
        if isinstance(c, SFrom):
            rel = self.rel(c.rel, st)
            cols = self.rel_cols(rel, self._gamma_of(st))
            for n, _ in cols:
                if "." in n or "(" in n:
                    raise GQError("T68", f"column {n} is not variable-shaped; alias it on the inner return")
                if n in st.scope:
                    raise GQError("T68", f"{n} is already in scope; alias it on the inner return")
            term = self._start(st, rel)
            return replace(st, term=term, cols=st.cols + [n for n, _ in cols],
                           scope={**st.scope, **dict(cols)})
        if isinstance(c, SOptional):
            return self.optional(c, st)
        raise TypeError(c)

    def _gamma_of(self, st):
        return tuple((n, st.scope[n]) for n in st.scope)

    def triple(self, c: STriple, st: _St) -> _St:
        sch = self.schema
        ets = [self.edge(e) for e in c.edges]
        m, n = c.hops
        a, b = c.src, c.dst
        a_in, b_in = a in st.scope, b in st.scope
        term = st.term
        cols = list(st.cols)
        scope = dict(st.scope)

        def direction(anchor_t, et, anchor_is_written_src):
            if c.either:
                return [(et.name, "out"), (et.name, "in")]
            if anchor_is_written_src:
                if self.matches_type(anchor_t, et.src):
                    return [(et.name, "out")]
                return [(et.name, "in")]
            if self.matches_type(anchor_t, et.dst):
                return [(et.name, "in")]
            return [(et.name, "out")]

        if not a_in and not b_in:
            et = ets[0]
            term = self._start(st, C.Scan(et.src, a))
            cols.append(a)
            scope[a] = node_t(et.src)
            a_in = True
        if a_in:
            anchor, other, from_src = a, b, True
        else:
            anchor, other, from_src = b, a, False
        at = scope[anchor]
        members = []
        for et in ets:
            members.extend(direction(at, et, from_src))
        target = other
        check = None
        if other in scope:
            target = self.fresh("_y")
            check = other
        if self.alternation == "selector" or len(members) == 1:
            new = C.Expand(term, anchor, tuple(members), m, n, target, c.edge_var)
        else:
            arms = [C.Expand(term, anchor, (mem,), m, n, target, c.edge_var) for mem in members]
            new = arms[0]
            for arm in arms[1:]:
                new = C.Union(new, arm)
        ends = set()
        for en, d in members:
            et = sch.edge_by_type[en]
            ends.add(et.dst if d == "out" else et.src)
        yt = node_t(sorted(ends)[0])
        if check is not None:
            new = C.Filter(new, Cmp("=", Col(target), Col(check)))
            keep = cols + ([c.edge_var] if c.edge_var else [])
            new = C.Project(new, tuple((x, Col(x)) for x in keep))
            if c.edge_var:
                scope[c.edge_var] = C.edge_t(c.edges[0]) if len(c.edges) == 1 else C.edge_t("*")
            return replace(st, term=new, cols=keep, scope=scope)
        cols.append(target)
        scope[target] = yt
        if c.edge_var:
            cols.append(c.edge_var)
            scope[c.edge_var] = C.edge_t(sch.edges[c.edges[0]].name) if len(c.edges) == 1 else C.edge_t("*")
        return replace(st, term=new, cols=cols, scope=scope)

    def optional(self, c: SOptional, st: _St) -> _St:
        gamma = self._gamma_of(st)
        inner = self.clauses(c.clauses, _St(C.Env(), list(st.scope), dict(st.scope), gamma, dict(st.rels)))
        new_vars = [v for v in inner.cols if v not in st.scope]
        p = C.Project(inner.term, tuple((v, Col(v)) for v in new_vars))
        arm1 = C.Correlate(st.term, p)
        neg = self.clause(SNot(c.clauses), st)
        arm2 = C.Project(neg.term, tuple((x, Col(x)) for x in st.cols) +
                         tuple((v, C.null_lit(inner.scope[v])) for v in new_vars))
        scope = dict(st.scope)
        for v in new_vars:
            scope[v] = inner.scope[v].null()
        return replace(st, term=C.Union(arm1, arm2), cols=st.cols + new_vars, scope=scope)

    # blocks inside expressions become correlated Group columns
    def hoist(self, e, st: _St):
        self._hoisted_types = {}
        term = [st.term]
        hoisted = []

        def go(x):
            if isinstance(x, SBlock):
                gamma = self._gamma_of(st)
                inner = self.clauses(x.clauses, _St(C.Env(), list(st.scope), dict(st.scope), gamma, dict(st.rels)))
                name = self.fresh()
                if x.agg in ("count", "exists"):
                    agg = Agg("count", x.arg)
                else:
                    agg = Agg(x.agg, x.arg)
                g = C.Group(inner.term, (), ((name, agg),))
                term[0] = C.Correlate(term[0], g)
                hoisted.append(name)
                self._hoisted_types[name] = C.agg_type(agg, inner.scope, self.tenv(()))
                if x.agg == "exists":
                    return Cmp(">", Col(name), Lit(0, I64))
                return Col(name)
            if isinstance(x, SREL):
                raise GQError("T69", "a relation where a scalar is read; use an aggregate over it, or from")
            if isinstance(x, (Lit, Param, Col)) or x is None:
                return x
            if isinstance(x, tuple):
                return tuple(go(y) for y in x)
            if hasattr(x, "__dataclass_fields__"):
                return type(x)(**{f: go(getattr(x, f)) for f in x.__dataclass_fields__})
            return x
        out = go(e)
        return term[0], out, hoisted

    # ---------------------------------------------------------------- return
    def ret(self, r: SRead, st: _St):
        items = [(e, item_name(e, a)) for e, a in r.ret]
        names = [n for _, n in items]
        if len(set(names)) != len(names):
            raise GQError("T25", f"duplicate return column in {names}")
        term = st.term
        aggregated = any(has_agg(e) for e, _ in items)
        for e, _ in items:
            if any(isinstance(x, SBlock) for x in _iter(e)):
                raise GQError("T_RETURN", "a block in return; bind it with let first")
        if r.distinct or aggregated:
            keys = tuple((n, e) for e, n in items if not has_agg(e))
            aggs = tuple((n, e) for e, n in items if has_agg(e))
            for n, e in aggs:
                if not isinstance(e, Agg):
                    raise GQError("T_RETURN", "an expression over an aggregate needs a wrapper read")
            if r.distinct and aggs:
                raise GQError("T56", "distinct does not apply to an aggregated return")
            out = C.Group(term, keys, aggs)
            written = [n for _, n in items]
            if [n for n, _ in keys] + [n for n, _ in aggs] != written:
                out = C.Project(out, tuple((n, Col(n)) for n in written))
            if r.order:
                ok = []
                for e, d in r.order:
                    hit = next((n for ie, n in items if ie == e), None)
                    ok.append((Col(hit) if hit else e, d, None))
                out = C.Order(out, tuple(ok))
            if r.limit is not None:
                out = C.Cut(out, r.limit)
            return out
        if not r.order and r.limit is None:
            return C.Project(term, tuple((n, e) for e, n in items))
        ext = [self.fresh("_r") for _ in items]
        extended = C.Project(term, tuple((c, Col(c)) for c in st.cols) +
                             tuple((f, e) for f, (e, _) in zip(ext, items)))
        out = extended
        if r.order:
            ok = []
            for e, d in r.order:
                hit = None
                for f, (ie, n) in zip(ext, items):
                    if ie == e or e == Col(n) and n not in st.cols:
                        hit = f
                        break
                ok.append((Col(hit) if hit else e, d, None))
            out = C.Order(out, tuple(ok))
        if r.limit is not None:
            out = C.Cut(out, r.limit)
        return C.Project(out, tuple((n, Col(f)) for f, (_, n) in zip(ext, items)))

    # ---------------------------------------------------------------- relations
    def rel(self, r, st: _St):
        gamma = self._gamma_of(st)
        if isinstance(r, SInline):
            return self.read(r.read, gamma, st.rels)
        if isinstance(r, SCall):
            for a in r.args:
                if isinstance(a, SREL):
                    raise GQError("TCALLARG", f"relation argument to {r.name}: Call takes scalars only")
            return C.Call(r.name, tuple(r.args))
        if isinstance(r, SRelVar):
            if r.name not in st.rels:
                if r.name in st.scope:
                    raise GQError("T69", f"${r.name} is a scalar where a relation is read")
                raise GQError("T_SCOPE", f"unknown relation ${r.name}")
            return st.rels[r.name]
        if isinstance(r, SL2):
            return C.Score(self.rel(r.rel, st), "l2", r.field, r.q, "distance")
        if isinstance(r, SBm25):
            return C.Score(self.rel(r.rel, st), "bm25", r.field, r.q, "score")
        if isinstance(r, SNearest):
            if r.k is None:
                raise GQError("T17", "nearest requires k")
            return C.ApproxTopK(self.rel(r.rel, st), "vector", r.field, r.q, r.k, "distance", r.recall)
        if isinstance(r, SRrf):
            return C.Fuse(tuple(self.rel(a, st) for a in r.arms), r.k, r.weights)
        if isinstance(r, SUnion):
            arms = [self.rel(a, st) for a in r.arms]
            out = arms[0]
            for a in arms[1:]:
                out = C.Union(out, a)
            return out
        raise TypeError(r)

    # ---------------------------------------------------------------- writes
    def write(self, w: SWriteBody):
        sinks = []
        st = _St(C.Env(), [], {}, (), {})
        if w.clauses:
            st = self.clauses(w.clauses, st)
        legacy = [s for s in w.stmts if isinstance(s, (SDeleteWhere, SUpdateWhere))]
        if legacy:
            if len(w.stmts) != 1 or w.clauses:
                raise GQError("T_WRITE", "edition-2 statements are rewritten one at a time")
            s = legacy[0]
            x = "x"
            pred = _prefix(s.pred, x)
            if isinstance(s, SDeleteWhere):
                return self.write(SWriteBody((SBind(x, s.type), SFilter(pred)), (SDelete(x),)))
            return self.write(SWriteBody((SBind(x, s.type), SFilter(pred)),
                                         (SUpdate(x, tuple((p, _prefix(e, x)) for p, e in s.assigns)),)))
        for s in w.stmts:
            if isinstance(s, SInsert):
                sinks.append(C.Insert(s.type, tuple(s.assigns)))
            elif isinstance(s, SUpdate):
                sinks.append(C.Update(s.var, tuple(s.assigns)))
            elif isinstance(s, SDelete):
                sinks.append(C.Delete(s.var))
            else:
                raise TypeError(s)
        return C.Write(st.term, tuple(sinks))


def _iter(e):
    yield e
    if isinstance(e, tuple):
        for x in e:
            yield from _iter(x)
    elif hasattr(e, "__dataclass_fields__") and not isinstance(e, (Lit,)):
        for f in e.__dataclass_fields__:
            yield from _iter(getattr(e, f))


def _prefix(e, x):
    if isinstance(e, Bare):
        if e.name.startswith("@"):
            return Meta(Col(x), e.name)
        return Prop(Col(x), e.name)
    if isinstance(e, (Lit, Param, Col)) or e is None:
        return e
    if isinstance(e, tuple):
        return tuple(_prefix(y, x) for y in e)
    if hasattr(e, "__dataclass_fields__"):
        return type(e)(**{f: _prefix(getattr(e, f), x) for f in e.__dataclass_fields__})
    return e


# ====================================================================== programs

class Program:
    """A source unit of declared reads and writes. `core(name)` returns the
    closed core term: every Call inlined (bound, never textually
    substituted), every monotone cycle a Fix, within the plan budget."""

    def __init__(self, schema, queries, cfg: C.Config = C.PROPOSED, plan_budget: int = 5000,
                 alternation: str = "rfc", admission: Optional[str] = None):
        self.schema = schema
        self.q = {q.name: q for q in queries}
        self.cfg = cfg
        self.plan_budget = plan_budget
        self.alternation = alternation
        self.admission = admission or cfg.fix_admission
        self._compiled = {}
        self._types = {}
        self._chain = {}
        self._compute_sccs()

    def _compute_sccs(self):
        graph = {n: sorted(calls_in(q.body) & set(self.q)) for n, q in self.q.items()}
        for n, q in self.q.items():
            missing = calls_in(q.body) - set(self.q)
            if missing:
                self._missing = missing
        index, low, stack, on, sccs = {}, {}, [], set(), []
        counter = [0]

        def strong(v):
            index[v] = low[v] = counter[0]
            counter[0] += 1
            stack.append(v)
            on.add(v)
            for w in graph[v]:
                if w not in index:
                    strong(w)
                    low[v] = min(low[v], low[w])
                elif w in on:
                    low[v] = min(low[v], index[w])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                sccs.append(tuple(sorted(comp)))
        for v in sorted(graph):
            if v not in index:
                strong(v)
        self.scc_of = {}
        for comp in sccs:
            for v in comp:
                self.scc_of[v] = comp
        self.graph = graph

    def is_recursive(self, name):
        comp = self.scc_of[name]
        return len(comp) > 1 or name in self.graph[name]

    def params_of(self, name):
        return dict(self.q[name].params)

    def returns_of(self, name):
        b = self.q[name].body
        return tuple(item_name(e, a) for e, a in b.ret)

    def desugarer(self, name, calls, call_returns=None):
        return Desugarer(self.schema, self.params_of(name), calls, self.alternation, self.cfg, call_returns)

    def result_cols(self, name):
        if name not in self._types:
            self.core(name)
        return self._types[name]

    def core(self, name, _stack=()):
        if name in self._compiled:
            return self._compiled[name]
        if name not in self.q:
            raise GQError("T70", f"unknown declared read {name}")
        if name in _stack:
            raise GQError("T70", f"cycle {' -> '.join(_stack + (name,))}")
        q = self.q[name]
        if isinstance(q.body, SWriteBody):
            raise GQError("A7", f"{name} is a write; a write cannot be called from a read")
        comp = self.scc_of[name]
        if self.is_recursive(name):
            body = self._compile_scc(comp, name, _stack)
        else:
            calls = {}
            for callee in self.graph[name]:
                self.core(callee, _stack + (name,))
                calls[callee] = self._types[callee]
            ds = self.desugarer(name, calls)
            term = ds.read(q.body)
            body = self._resolve(term, _stack + (name,), set())
            self._types[name] = C.type_term(body, C.TEnv(self.schema, self.params_of(name), cfg=self.cfg))
        size = C.term_size(body)
        if size > self.plan_budget:
            raise GQError("T70", f"plan budget {self.plan_budget} exceeded: {size} nodes through "
                                 f"{' -> '.join(_stack + (name,))}")
        self._compiled[name] = body
        return body

    def _resolve(self, term, stack, rec_names, occ=None):
        """Replace Call nodes: SCC members become RecRef, others Inlined."""
        if isinstance(term, C.Call):
            if term.name in rec_names:
                want = tuple(Param(p) for p, _ in self.q[term.name].params)
                if tuple(term.args) != want:
                    raise GQError("T70", f"recursive call {term.name}{term.args!r} passes computed arguments; "
                                         f"a recursion must pass its parameters through")
                occ[0] += 1
                return C.RecRef(term.name, occ[0])
            body = self.core(term.name, stack)
            return C.Inlined(term.name, tuple(self.q[term.name].params), tuple(term.args), body)
        if not hasattr(term, "__dataclass_fields__") or isinstance(term, (Lit,)):
            return term
        kw = {}
        for f in term.__dataclass_fields__:
            v = getattr(term, f)
            if isinstance(v, C.RELATION_TERMS):
                kw[f] = self._resolve(v, stack, rec_names, occ)
            elif isinstance(v, tuple) and v and all(isinstance(x, C.RELATION_TERMS) for x in v):
                kw[f] = tuple(self._resolve(x, stack, rec_names, occ) for x in v)
            else:
                kw[f] = v
        return type(term)(**kw)

    def _compile_scc(self, comp, name, stack):
        outside = set()
        for m in comp:
            outside |= set(self.graph[m]) - set(comp)
        calls = {}
        for callee in sorted(outside):
            self.core(callee, stack + (name,))
            calls[callee] = self._types[callee]
        # pass 1: recursive calls pending, their names from the return clause
        pend = {**calls, **{m: None for m in comp}}
        rets = {m: self.returns_of(m) for m in comp}
        occ = [0]
        bodies1 = []
        for m in comp:
            t = self.desugarer(m, pend, rets).read(self.q[m].body)
            bodies1.append(self._resolve(t, stack + (m,), set(comp), occ))
        budget = self.q[name].budget_rounds
        for m in comp:
            if self.q[m].budget_rounds is not None:
                budget = self.q[m].budget_rounds if budget is None else min(budget, self.q[m].budget_rounds)
        fix1 = C.Fix(tuple(comp), tuple(bodies1), name, budget)
        tenv = C.TEnv(self.schema, self.params_of(name), cfg=self.cfg)
        C.type_term(fix1, tenv)
        found = tenv.notes[("fix", id(fix1))]
        # pass 2: real types
        occ = [0]
        bodies = []
        for m in comp:
            t = self.desugarer(m, {**calls, **found}).read(self.q[m].body)
            bodies.append(self._resolve(t, stack + (m,), set(comp), occ))
        fix = C.Fix(tuple(comp), tuple(bodies), name, budget)
        C.admit_fix(fix, self.admission)
        self._types[name] = C.type_term(fix, C.TEnv(self.schema, self.params_of(name), cfg=self.cfg))
        for m in comp:
            self._types.setdefault(m, C.type_term(C.Fix(tuple(comp), tuple(bodies), m, budget),
                                                  C.TEnv(self.schema, self.params_of(m), cfg=self.cfg)))
        return fix

    def write(self, name):
        q = self.q[name]
        calls = {}
        for callee in self.graph[name]:
            self.core(callee, (name,))
            calls[callee] = self._types[callee]
        w = self.desugarer(name, calls).write(q.body)
        src = self._resolve(w.src, (name,), set())
        return C.Write(src, w.stmts)

    def run(self, name, graph, params=None, cfg: Optional[C.Config] = None):
        cfg = cfg or self.cfg
        q = self.q[name]
        if isinstance(q.body, SWriteBody):
            return C.run_write(self.write(name), graph, cfg, params)
        return C.run(self.core(name), graph, params, cfg, self.params_of(name))
