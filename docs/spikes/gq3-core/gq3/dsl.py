"""Short builders for hand-translating edition-3 text to the AST."""
from .core import (Agg, And, Arith, Cmp, Col, Fn, InList, IsNull, Lit, Meta, Neg, Not, Or,
                   Param, Prop, TermsQ, lit)
from .model import T
from .surface import (SBind, SBlock, SBm25, SCall, SDelete, SDeleteWhere, SExists, SFilter,
                      SFrom, SInline, SInsert, SL2, SLet, SNearest, SNot, SOptional, SQuery,
                      SRead, SRelVar, SRrf, STriple, SUnion, SUpdate, SUpdateWhere, SWriteBody, Bare)


def v(n):
    return Col(n)


def p(n):
    return Param(n)


def pr(var, prop):
    return Prop(Col(var), prop)


def L(x):
    return lit(x)


def LL(*xs):
    """A list literal."""
    return Lit(tuple(xs), T("List", "String"))


def cmp(op, a, b):
    return Cmp(op, a, b)


def bind(var, typ, **props):
    return SBind(var, typ, tuple(props.items()))


def tri(a, e, b, hops=(1, 1), k=None, either=False):
    edges = tuple(e) if isinstance(e, (tuple, list)) else (e,)
    return STriple(a, edges, b, hops, k, either)


def where(e):
    return SFilter(e)


def read(match, ret, distinct=False, order=(), limit=None):
    ret = tuple(r if isinstance(r, tuple) else (r, None) for r in ret)
    order = tuple(o if isinstance(o, tuple) else (o, "asc") for o in order)
    return SRead(tuple(match), ret, distinct, order, limit)


def inline(*a, **k):
    return SInline(read(*a, **k))


def q(name, params, body, budget=None):
    return SQuery(name, tuple(params), body, budget)


def count(arg=None, distinct=False):
    return Agg("count", arg, distinct)


def agg(fn, arg, distinct=False):
    return Agg(fn, arg, distinct)
