"""The fixed read set of a configuration, in edition 2 and GQ 3, and writes.

Every read is one declaration, written twice: `e2`, what a graph owner wrote
for a GQ 2 binary, and `e3`, the same intent in GQ 3 written by hand from
the respelling map (`docs/user/queries/upgrading-to-gq3.md`). The case also
respells `e2` mechanically with NEW's `lint --fix` and runs that text.

`kind` ties a read to the matrix catalogue entry whose classification it
shares (`catalog_twin`), so the matrix's classifier (`analyze.classify_read`)
sanctions a GQ 2/NEW difference the same way it did for the catalogue.

A read projects only what a fresh graph rebuilt from an export reproduces:
keys and properties, never `@id` of an unkeyed type.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ufgen import Config, NodeT, Prop

MAX_TYPES = 3
MAX_TEXT = 4
MAX_VEC = 2


@dataclass
class R:
    name: str
    kind: str          # scan | count | traverse | lex_any | lex_all | fuzzy | bm25 | bm25_ties | nearest | rrf
    e2: str | None     # None: no edition-2 form (a GQ 3-only read)
    e3: str
    params: dict = field(default_factory=dict)
    ordered: bool = True
    type_name: str = ""
    prop: str = ""
    keyed: bool = True  # rows identify themselves by a key (fresh graphs reproduce them exactly)

    @property
    def catalog_twin(self) -> str:
        return {
            "scan": "q_scalars_all", "count": "q_count", "traverse": "q_traverse",
            "lex_any": "s_search_title", "lex_all": "s_search_title", "fuzzy": "s_fuzzy_one",
            "bm25": "s_bm25_title", "bm25_ties": "s_bm25_nolimit", "nearest": "s_nearest",
            "nearest_null": "s_nearest_nullable", "rrf": "s_rrf",
        }.get(self.kind, self.kind)


def _cols(n: NodeT) -> list[str]:
    cols = [p.name for p in n.props if p.ty not in ("vector", "blob")]
    return cols[:8]


def _order(n: NodeT, cols: list[str]) -> list[str]:
    k = n.key_props()
    return k if k else cols


def _ident(n: NodeT) -> list[str]:
    """What names a row in a lexical or ranked read."""
    k = n.key_props()
    if k:
        return k
    # floats print differently in an export and a read (F32), lists are not identities
    return [p.name for p in n.props if p.ty in ("String", "I32", "I64", "U32", "U64", "Bool", "enum")][:4]


def text_props(n: NodeT) -> list[Prop]:
    return [p for p in n.props if p.ty == "String" and not (n.key_mode == "compound" and p.name in n.compound)]


def reads(cfg: Config) -> list[R]:
    out: list[R] = []
    nodes = [n for n in cfg.nodes][:MAX_TYPES]
    q0, q1, q2 = (cfg.queries + ["graph", "run", "café"])[:3]
    n_text = 0
    for n in nodes:
        cols = _cols(n)
        x = "$x"
        if cols:
            ret = ", ".join(f"{x}.{c}" for c in cols)
            order = ", ".join(f"{x}.{c}" for c in _order(n, cols))
            out.append(R(f"scan_{n.name}", "scan",
                         f"query scan_{n.name}() {{\n  match {{ {x}: {n.name} }}\n  return {{ {ret} }}\n  order {{ {order} }}\n}}",
                         f"query scan_{n.name}() {{\n  match {{ {x}: {n.name} }}\n  return {{ {ret} }}\n  order {{ {order} }}\n}}",
                         type_name=n.name, keyed=bool(n.key_props())))
        out.append(R(f"count_{n.name}", "count",
                     f"query count_{n.name}() {{\n  match {{ {x}: {n.name} }}\n  return {{ count({x}) as n }}\n}}",
                     f"query count_{n.name}() {{\n  match {{ {x}: {n.name} }}\n  return {{ count({x}) as n }}\n}}",
                     ordered=False, type_name=n.name))
        ident = _ident(n)
        idr = ", ".join(f"$x.{c}" for c in ident)
        keyed = bool(n.key_props())
        for p in text_props(n):
            if n_text >= MAX_TEXT:
                break
            n_text += 1
            f = f"$x.{p.name}"
            tn = f"{n.name}_{p.name}"
            out.append(R(f"lex_any_{tn}", "lex_any",
                         f"query lex_any_{tn}($q: String) {{\n  match {{\n    $x: {n.name}\n    search({f}, $q)\n  }}\n  return {{ {idr} }}\n  order {{ {idr} }}\n}}",
                         f"query lex_any_{tn}($q: String) {{\n  match {{ $x: {n.name} }}\n  where match_terms({f}, terms($q, mode: any))\n  return {{ {idr} }}\n  order {{ {idr} }}\n}}",
                         params={"q": q0}, type_name=n.name, prop=p.name, keyed=keyed))
            out.append(R(f"lex_all_{tn}", "lex_all", None,
                         f"query lex_all_{tn}($q: String) {{\n  match {{ $x: {n.name} }}\n  where match_terms({f}, terms($q, mode: all, max_edits: 1))\n  return {{ {idr} }}\n  order {{ {idr} }}\n}}",
                         params={"q": q1}, type_name=n.name, prop=p.name, keyed=keyed))
            out.append(R(f"fuzzy_{tn}", "fuzzy",
                         f"query fuzzy_{tn}($q: String) {{\n  match {{\n    $x: {n.name}\n    fuzzy({f}, $q, 2)\n  }}\n  return {{ {idr} }}\n  order {{ {idr} }}\n}}",
                         f"query fuzzy_{tn}($q: String) {{\n  match {{ $x: {n.name} }}\n  where match_terms({f}, terms($q, mode: any, max_edits: 2))\n  return {{ {idr} }}\n  order {{ {idr} }}\n}}",
                         params={"q": q2}, type_name=n.name, prop=p.name, keyed=keyed))
            pop = f"let $pop = {{ match {{ $x: {n.name} }} return {{ $x }} }}"
            out.append(R(f"bm25_{tn}", "bm25",
                         f"query bm25_{tn}($q: String) {{\n  match {{ $x: {n.name} }}\n  return {{ {idr}, bm25({f}, $q) as score }}\n  order {{ bm25({f}, $q) }}\n  limit 10\n}}",
                         f"query bm25_{tn}($q: String) {{\n  match {{ {pop}, from bm25($pop, {f}, $q) as $score }}\n  return {{ {idr}, $score as score }}\n  order {{ $score desc }}\n  limit 10\n}}",
                         params={"q": q0}, type_name=n.name, prop=p.name, keyed=keyed))
            out.append(R(f"bm25t_{tn}", "bm25_ties", None,
                         f"query bm25t_{tn}($q: String) {{\n  match {{ {pop}, from bm25($pop, {f}, $q) as $score }}\n  return {{ {idr}, $score as score }}\n  order {{ $score desc }}\n}}",
                         params={"q": q1}, type_name=n.name, prop=p.name, keyed=keyed))
        out += scalar_reads(cfg, n, idr, keyed)
        vecs = [p for p in n.props if p.ty == "vector"][:MAX_VEC]
        for p in vecs:
            v = cfg.vec_query[f"{n.name}.{p.name}"]
            tn = f"{n.name}_{p.name}"
            pop = f"let $pop = {{ match {{ $x: {n.name} }} return {{ $x }} }}"
            out.append(R(f"near_{tn}", "nearest",
                         f"query near_{tn}($v: Vector({p.dim})) {{\n  match {{ $x: {n.name} }}\n  return {{ {idr}, nearest($x.{p.name}, $v) as dist }}\n  order {{ nearest($x.{p.name}, $v) }}\n  limit 5\n}}",
                         f"query near_{tn}($v: Vector({p.dim})) {{\n  match {{ {pop}, from nearest($pop, $x.{p.name}, $v, k: 5) as $d }}\n  return {{ {idr}, $d as dist }}\n  order {{ $d }}\n  limit 5\n}}",
                         params={"v": v}, type_name=n.name, prop=p.name, keyed=keyed))
        tps = text_props(n)
        if vecs and tps:
            p, t = vecs[0], tps[0]
            v = cfg.vec_query[f"{n.name}.{p.name}"]
            tn = f"{n.name}_{p.name}_{t.name}"
            out.append(R(f"rrf_{tn}", "rrf",
                         f"query rrf_{tn}($v: Vector({p.dim}), $q: String) {{\n  match {{ $x: {n.name} }}\n  return {{ {idr}, rrf(nearest($x.{p.name}, $v), bm25($x.{t.name}, $q)) as s }}\n  order {{ rrf(nearest($x.{p.name}, $v), bm25($x.{t.name}, $q)) }}\n  limit 8\n}}",
                         f"query rrf_{tn}($v: Vector({p.dim}), $q: String) {{\n  match {{ let $all = {{ match {{ $x: {n.name} }} return {{ $x }} }}, from rrf(nearest($all, $x.{p.name}, $v, k: 8), bm25($all, $x.{t.name}, $q), k: 60) as $s }}\n  return {{ {idr}, $s as s }}\n  order {{ $s desc }}\n  limit 8\n}}",
                         params={"v": v, "q": q0}, type_name=n.name, prop=p.name, keyed=keyed))
    for e in cfg.edges[:2]:
        a, b = cfg.node(e.src), cfg.node(e.dst)
        ka, kb = a.key_props()[0], b.key_props()[0]
        out.append(R(f"trav_{e.name}", "traverse",
                     f"query trav_{e.name}() {{\n  match {{\n    $a: {a.name}\n    $a {e.verb} $b\n  }}\n  return {{ $a.{ka}, $b.{kb} }}\n  order {{ $a.{ka}, $b.{kb} }}\n}}",
                     f"query trav_{e.name}() {{\n  match {{ $a: {a.name}, $b: {b.name}, $a {e.verb} $b }}\n  return {{ $a.{ka}, $b.{kb} }}\n  order {{ $a.{ka}, $b.{kb} }}\n}}",
                     ordered=True, type_name=e.name, keyed=not (e.keyed or e.unique_pair) and False or True))
    return out


SCALAR_PARAM = {"I32": 0, "I64": 0, "U32": 2000000000, "U64": 1000, "F32": 0.0, "F64": 0.0,
                "Date": "2000-01-01", "DateTime": "2000-01-01T00:00:00Z"}


def scalar_reads(cfg: Config, n: NodeT, idr: str, keyed: bool) -> list:
    """Predicates the new scalar index kinds serve (BTREE, BITMAP, NGRAM,
    LABEL_LIST): equality and ranges, `contains` and `starts_with` on a
    String, `in` over a list, on indexed and unindexed properties alike."""
    out = []
    t = n.name
    for p in n.props[:10]:
        if p.ty in SCALAR_PARAM:
            v = SCALAR_PARAM[p.ty]
            ty = p.ty
            out.append(R(f"rng_{t}_{p.name}", "range",
                         f"query rng_{t}_{p.name}($v: {ty}) {{\n  match {{\n    $x: {t}\n    $x.{p.name} > $v\n  }}\n  return {{ {idr} }}\n  order {{ {idr} }}\n}}",
                         f"query rng_{t}_{p.name}($v: {ty}) {{\n  match {{ $x: {t} }}\n  where $x.{p.name} > $v\n  return {{ {idr} }}\n  order {{ {idr} }}\n}}",
                         params={"v": v}, type_name=t, prop=p.name, keyed=keyed))
        elif p.ty in ("Bool", "enum"):
            v = True if p.ty == "Bool" else p.enum_vals[0]
            lit = "true" if p.ty == "Bool" else f'"{v}"'
            out.append(R(f"eq_{t}_{p.name}", "equality",
                         f"query eq_{t}_{p.name}() {{\n  match {{\n    $x: {t}\n    $x.{p.name} = {lit}\n  }}\n  return {{ {idr} }}\n  order {{ {idr} }}\n}}",
                         f"query eq_{t}_{p.name}() {{\n  match {{ $x: {t} }}\n  where $x.{p.name} = {lit}\n  return {{ {idr} }}\n  order {{ {idr} }}\n}}",
                         type_name=t, prop=p.name, keyed=keyed))
        elif p.ty == "String" and not (n.key_mode == "compound" and p.name in n.compound):
            out.append(R(f"cont_{t}_{p.name}", "contains",
                         f"query cont_{t}_{p.name}() {{\n  match {{\n    $x: {t}\n    $x.{p.name} contains \"ra\"\n  }}\n  return {{ {idr} }}\n  order {{ {idr} }}\n}}",
                         f"query cont_{t}_{p.name}() {{\n  match {{ $x: {t} }}\n  where contains($x.{p.name}, \"ra\")\n  return {{ {idr} }}\n  order {{ {idr} }}\n}}",
                         type_name=t, prop=p.name, keyed=keyed))
            out.append(R(f"sw_{t}_{p.name}", "starts_with",
                         f"query sw_{t}_{p.name}() {{\n  match {{\n    $x: {t}\n    $x.{p.name} starts_with \"t\"\n  }}\n  return {{ {idr} }}\n  order {{ {idr} }}\n}}",
                         f"query sw_{t}_{p.name}() {{\n  match {{ $x: {t} }}\n  where starts_with($x.{p.name}, \"t\")\n  return {{ {idr} }}\n  order {{ {idr} }}\n}}",
                         type_name=t, prop=p.name, keyed=keyed))
            out.append(R(f"seq_{t}_{p.name}", "equality",
                         f"query seq_{t}_{p.name}($v: String) {{\n  match {{\n    $x: {t}\n    $x.{p.name} = $v\n  }}\n  return {{ {idr} }}\n  order {{ {idr} }}\n}}",
                         f"query seq_{t}_{p.name}($v: String) {{\n  match {{ $x: {t} }}\n  where $x.{p.name} = $v\n  return {{ {idr} }}\n  order {{ {idr} }}\n}}",
                         params={"v": "graph"}, type_name=t, prop=p.name, keyed=keyed))
        elif p.ty in ("list_str", "list_i32"):
            v = '"rust"' if p.ty == "list_str" else "3"
            out.append(R(f"in_{t}_{p.name}", "list_in",
                         f"query in_{t}_{p.name}() {{\n  match {{\n    $x: {t}\n    $x.{p.name} contains {v}\n  }}\n  return {{ {idr} }}\n  order {{ {idr} }}\n}}",
                         f"query in_{t}_{p.name}() {{\n  match {{ $x: {t} }}\n  where {v} in $x.{p.name}\n  return {{ {idr} }}\n  order {{ {idr} }}\n}}",
                         type_name=t, prop=p.name, keyed=keyed))
    return out


def gq_file(rs: list[R], edition: int) -> str:
    parts = []
    for r in rs:
        t = r.e2 if edition == 2 else r.e3
        if t is not None:
            parts.append(t)
    return "\n\n".join(parts) + "\n"


# ---------------------------------------------------------------- writes

def lit(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def assign(row: dict, n: NodeT) -> str:
    """Property assignments of a node row, as GQ text (no Blob, no vector)."""
    out = []
    for p in n.props:
        if p.name in row and p.ty not in ("blob",):
            out.append(f"{p.name}: {lit(row[p.name])}")
    return ", ".join(out)
