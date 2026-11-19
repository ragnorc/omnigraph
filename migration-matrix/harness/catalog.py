"""The query catalogue: edition-2 text and its edition-3 respelling.

Every entry is one declaration in edition 2 (what a graph's owner wrote for
OLD) and the same intent in edition 3, written by hand from the RFC's
respelling map (section 6) and `docs/user/queries/upgrading-to-gq3.md`.
`rfc` names the Compatibility bullet a difference is expected under, when
the RFC predicts one for this entry; the classifier reads it.

The harness also respells every edition-2 entry mechanically, by applying
the byte-range edits NEW's `lint --json` prints until the text compiles or
no edit remains (what `omnigraph lint --fix` would do; NEW ships no such
flag), and runs that text too.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Q:
    name: str
    kind: str  # read | search | write
    e2: str
    e3: str | None
    params: dict = field(default_factory=dict)
    rfc: list[str] = field(default_factory=list)  # bullets predicting a change
    note: str = ""
    evolved_only: bool = False
    e3_alt: dict = field(default_factory=dict)  # name -> other edition-3 text (writes)


VEC = [0.5, -0.25, 0.125, 0.0, 0.75, -0.5, 0.25, 1.0]

SCALAR_COLS = ("$s.slug, $s.s, $s.s_opt, $s.b, $s.b_opt, $s.i32, $s.i32_opt, $s.i64, "
               "$s.i64_opt, $s.u32, $s.u32_opt, $s.u64, $s.u64_opt, $s.f32, $s.f64, "
               "$s.f64_opt, $s.d, $s.d_opt, $s.dt, $s.dt_opt, $s.kind, $s.kind_opt, "
               "$s.tags, $s.tags_opt, $s.nums")

READS: list[Q] = [
    Q("q_scalars_all", "read",
      f"query q_scalars_all() {{\n  match {{ $s: Scalars }}\n  return {{ {SCALAR_COLS} }}\n  order {{ $s.slug }}\n}}",
      f"query q_scalars_all() {{\n  match {{ $s: Scalars }}\n  return {{ {SCALAR_COLS} }}\n  order {{ $s.slug }}\n}}"),
    Q("q_brace_enum", "read",
      'query q_brace_enum() {\n  match { $s: Scalars { kind: "alpha" } }\n  return { $s.slug, $s.kind }\n  order { $s.slug }\n}',
      'query q_brace_enum() {\n  match { $s: Scalars }\n  where $s.kind = "alpha"\n  return { $s.slug, $s.kind }\n  order { $s.slug }\n}'),
    Q("q_range", "read",
      "query q_range() {\n  match {\n    $s: Scalars\n    $s.i32 > 500\n    $s.i32 <= 900\n  }\n  return { $s.slug, $s.i32 }\n  order { $s.i32 desc, $s.slug }\n}",
      "query q_range() {\n  match { $s: Scalars }\n  where $s.i32 > 500 and $s.i32 <= 900\n  return { $s.slug, $s.i32 }\n  order { $s.i32 desc, $s.slug }\n}"),
    Q("q_is_null", "read",
      "query q_is_null() {\n  match {\n    $s: Scalars\n    $s.s_opt is null\n  }\n  return { $s.slug }\n  order { $s.slug }\n}",
      "query q_is_null() {\n  match { $s: Scalars }\n  where $s.s_opt is null\n  return { $s.slug }\n  order { $s.slug }\n}"),
    Q("q_or_null", "read",
      "query q_or_null() {\n  match {\n    $s: Scalars\n    $s.i32_opt > 10 or $s.i32_opt is null\n  }\n  return { $s.slug, $s.i32_opt }\n  order { $s.slug }\n}",
      "query q_or_null() {\n  match { $s: Scalars }\n  where $s.i32_opt > 10 or $s.i32_opt is null\n  return { $s.slug, $s.i32_opt }\n  order { $s.slug }\n}"),
    Q("q_contains_str", "read",
      'query q_contains_str() {\n  match {\n    $d: Doc\n    $d.title contains "raph"\n  }\n  return { $d.slug, $d.title }\n  order { $d.slug }\n}',
      'query q_contains_str() {\n  match { $d: Doc }\n  where contains($d.title, "raph")\n  return { $d.slug, $d.title }\n  order { $d.slug }\n}'),
    Q("q_contains_list", "read",
      'query q_contains_list() {\n  match {\n    $s: Scalars\n    $s.tags contains "rust"\n  }\n  return { $s.slug, $s.tags }\n  order { $s.slug }\n}',
      'query q_contains_list() {\n  match { $s: Scalars }\n  where "rust" in $s.tags\n  return { $s.slug, $s.tags }\n  order { $s.slug }\n}'),
    Q("q_starts_with", "read",
      'query q_starts_with() {\n  match {\n    $d: Doc\n    $d.slug starts_with "Graph"\n  }\n  return { $d.slug }\n  order { $d.slug }\n}',
      'query q_starts_with() {\n  match { $d: Doc }\n  where starts_with($d.slug, "Graph")\n  return { $d.slug }\n  order { $d.slug }\n}'),
    Q("q_in_list", "read",
      'query q_in_list() {\n  match {\n    $s: Scalars\n    $s.kind in ["alpha", "gamma"]\n    $s.i32 < 300\n  }\n  return { $s.slug, $s.kind }\n  order { $s.slug }\n}',
      'query q_in_list() {\n  match { $s: Scalars }\n  where $s.kind in ["alpha", "gamma"] and $s.i32 < 300\n  return { $s.slug, $s.kind }\n  order { $s.slug }\n}'),
    Q("q_int_float_literal", "read",
      "query q_int_float_literal() {\n  match {\n    $s: Scalars\n    $s.i64 = 9007199254740992.0\n  }\n  return { $s.slug, $s.i64 }\n  order { $s.slug }\n}",
      "query q_int_float_literal() {\n  match { $s: Scalars }\n  where $s.i64 = 9007199254740992.0\n  return { $s.slug, $s.i64 }\n  order { $s.slug }\n}",
      rfc=["results.integer-float-comparison"]),
    Q("q_u64_big", "read",
      "query q_u64_big() {\n  match {\n    $s: Scalars\n    $s.u64 > 9007199254740992\n  }\n  return { $s.slug, $s.u64 }\n  order { $s.slug }\n}",
      "query q_u64_big() {\n  match { $s: Scalars }\n  where $s.u64 > 9007199254740992\n  return { $s.slug, $s.u64 }\n  order { $s.slug }\n}"),
    Q("q_f32_literal", "read",
      "query q_f32_literal() {\n  match {\n    $s: Scalars\n    $s.f32 = 0.1\n  }\n  return { $s.slug, $s.f32 }\n  order { $s.slug }\n}",
      "query q_f32_literal() {\n  match { $s: Scalars }\n  where $s.f32 = 0.1\n  return { $s.slug, $s.f32 }\n  order { $s.slug }\n}"),
    Q("q_date_param", "read",
      "query q_date_param($from: Date, $t: DateTime) {\n  match {\n    $s: Scalars\n    $s.d >= $from\n    $s.dt > $t\n  }\n  return { $s.slug, $s.d, $s.dt }\n  order { $s.dt desc, $s.slug }\n}",
      "query q_date_param($from: Date, $t: DateTime) {\n  match { $s: Scalars }\n  where $s.d >= $from and $s.dt > $t\n  return { $s.slug, $s.d, $s.dt }\n  order { $s.dt desc, $s.slug }\n}",
      params={"from": "2000-01-01", "t": "2026-06-01T00:00:00Z"}),
    Q("q_traverse", "read",
      "query q_traverse($e: String) {\n  match {\n    $a: Person { email: $e }\n    $a knows $b\n  }\n  return { $b.email, $b.name }\n  order { $b.email }\n}",
      "query q_traverse($e: String) {\n  match { $a: Person, $a knows $b }\n  where $a.email = $e\n  return { $b.email, $b.name }\n  order { $b.email }\n}",
      params={"e": "p001@example.com"}),
    Q("q_hops", "read",
      "query q_hops($e: String) {\n  match {\n    $a: Person { email: $e }\n    $a knows{1,2} $b\n  }\n  return distinct { $b.email }\n  order { $b.email }\n}",
      "query q_hops($e: String) {\n  match { $a: Person, $a knows{1,2} $b }\n  where $a.email = $e\n  return distinct { $b.email }\n  order { $b.email }\n}",
      params={"e": "p001@example.com"}, rfc=["results.hop-range-self-loop"],
      note="p001 has a self-loop: the RFC drops the start node from a range other than the single hop"),
    Q("q_reverse", "read",
      'query q_reverse() {\n  match {\n    $c: Company { slug: "c0" }\n    $p worksAt $c\n  }\n  return { $p.email }\n  order { $p.email }\n}',
      'query q_reverse() {\n  match { $c: Company, $p worksAt $c }\n  where $c.slug = "c0"\n  return { $p.email }\n  order { $p.email }\n}'),
    Q("q_undirected", "read",
      "query q_undirected($e: String) {\n  match {\n    $a: Person { email: $e }\n    $a <knows> $b\n  }\n  return { $b.email }\n  order { $b.email }\n}",
      "query q_undirected($e: String) {\n  match { $a: Person, $a <knows> $b }\n  where $a.email = $e\n  return { $b.email }\n  order { $b.email }\n}",
      params={"e": "p003@example.com"}),
    Q("q_edge_binding", "read",
      "query q_edge_binding() {\n  match {\n    $a: Person\n    $a $k:knows $b\n    $k.since > 2040\n  }\n  return { $a.email, $b.email, $k.since }\n  order { $k.since, $a.email, $b.email }\n}",
      "query q_edge_binding() {\n  match { $a: Person, $a $k:knows $b }\n  where $k.since > 2040\n  return { $a.email, $b.email, $k.since }\n  order { $k.since, $a.email, $b.email }\n}"),
    Q("q_parallel_edges", "read",
      'query q_parallel_edges() {\n  match {\n    $a: Person { email: "p000@example.com" }\n    $a $k:knows $b\n  }\n  return { $b.email, $k.since }\n  order { $b.email, $k.since }\n}',
      'query q_parallel_edges() {\n  match { $a: Person, $a $k:knows $b }\n  where $a.email = "p000@example.com"\n  return { $b.email, $k.since }\n  order { $b.email, $k.since }\n}'),
    Q("q_not_block", "read",
      "query q_not_block() {\n  match {\n    $p: Person\n    not { $p worksAt $_ }\n  }\n  return { $p.email }\n  order { $p.email }\n}",
      "query q_not_block() {\n  match { $p: Person }\n  where not exists { $c: Company, $p worksAt $c }\n  return { $p.email }\n  order { $p.email }\n}"),
    Q("q_count_block", "read",
      "query q_count_block() {\n  match {\n    $p: Person\n    count { $p knows $q } >= 2\n  }\n  return { $p.email }\n  order { $p.email }\n}",
      "query q_count_block() {\n  match { $p: Person }\n  where count { $q: Person, $p knows $q } >= 2\n  return { $p.email }\n  order { $p.email }\n}"),
    Q("q_aggregates", "read",
      "query q_aggregates() {\n  match {\n    $p: Person\n    $p knows $f\n  }\n  return { $p.email, count($f) as n, avg($f.age) as a, min($f.age) as lo, max($f.age) as hi, sum($f.age) as total }\n  order { n desc, $p.email }\n  limit 10\n}",
      "query q_aggregates() {\n  match { $p: Person, $p knows $f }\n  return { $p.email, count($f) as n, avg($f.age) as a, min($f.age) as lo, max($f.age) as hi, sum($f.age) as total }\n  order { n desc, $p.email }\n  limit 10\n}"),
    Q("q_avg_int_extremes", "read",
      "query q_avg_int_extremes() {\n  match { $s: Scalars }\n  return { avg($s.i64) as a, sum($s.i64) as t, count($s) as n, max($s.u64) as hi }\n}",
      "query q_avg_int_extremes() {\n  match { $s: Scalars }\n  return { avg($s.i64) as a, sum($s.i64) as t, count($s) as n, max($s.u64) as hi }\n}",
      rfc=["results.avg-exact-integer"]),
    Q("q_distinct", "read",
      "query q_distinct() {\n  match { $s: Scalars }\n  return distinct { $s.kind, $s.b }\n  order { $s.kind, $s.b }\n}",
      "query q_distinct() {\n  match { $s: Scalars }\n  return distinct { $s.kind, $s.b }\n  order { $s.kind, $s.b }\n}"),
    Q("q_limit_no_order", "read",
      "query q_limit_no_order() {\n  match { $s: Scalars }\n  return { $s.slug }\n  limit 5\n}",
      "query q_limit_no_order() {\n  match { $s: Scalars }\n  return { $s.slug }\n  limit 5\n}",
      rfc=["results.limit-tie-rule"]),
    Q("q_compound_key", "read",
      'query q_compound_key() {\n  match { $x: Pair { a: "y", b: 4 } }\n  return { $x.@id, $x.a, $x.b, $x.label }\n}',
      'query q_compound_key() {\n  match { $x: Pair }\n  where $x.a = "y" and $x.b = 4\n  return { $x.@id, $x.a, $x.b, $x.label }\n}'),
    Q("q_node_object", "read",
      'query q_node_object() {\n  match { $p: Person { email: "p005@example.com" } }\n  return { $p }\n}',
      'query q_node_object() {\n  match { $p: Person }\n  where $p.email = "p005@example.com"\n  return { $p }\n}'),
    Q("q_meta_fields", "read",
      "query q_meta_fields() {\n  match {\n    $a: Person\n    $a $k:worksAt $c\n  }\n  return { $a.@id, $k.@src, $k.@dst, $k.@id, $k.role }\n  order { $a.@id }\n}",
      "query q_meta_fields() {\n  match { $a: Person, $a $k:worksAt $c }\n  return { $a.@id, $k.@src, $k.@dst, $k.@id, $k.role }\n  order { $a.@id }\n}"),
    Q("q_vector_column", "read",
      "query q_vector_column() {\n  match { $d: Doc }\n  return { $d.slug, $d.emb, $d.emb_opt }\n  order { $d.slug }\n  limit 6\n}",
      "query q_vector_column() {\n  match { $d: Doc }\n  return { $d.slug, $d.emb, $d.emb_opt }\n  order { $d.slug }\n  limit 6\n}"),
    Q("q_unkeyed", "read",
      'query q_unkeyed() {\n  match { $l: Loose { name: "loose1" } }\n  return { $l.@id, $l.name }\n  order { $l.@id }\n}',
      'query q_unkeyed() {\n  match { $l: Loose }\n  where $l.name = "loose1"\n  return { $l.@id, $l.name }\n  order { $l.@id }\n}'),
    Q("q_cites", "read",
      "query q_cites() {\n  match {\n    $a: Doc\n    $a $c:cites $b\n    $c.weight > 3.0\n  }\n  return { $a.slug, $b.slug, $c.weight }\n  order { $a.slug, $b.slug }\n}",
      "query q_cites() {\n  match { $a: Doc, $a $c:cites $b }\n  where $c.weight > 3.0\n  return { $a.slug, $b.slug, $c.weight }\n  order { $a.slug, $b.slug }\n}"),
    Q("q_empty_edge_type", "read",
      "query q_empty_edge_type() {\n  match {\n    $d: Doc\n    $d tagged $l\n  }\n  return { $d.slug, $l.name }\n}",
      "query q_empty_edge_type() {\n  match { $d: Doc, $d tagged $l }\n  return { $d.slug, $l.name }\n}"),
    Q("q_date_order_limit", "read",
      "query q_date_order_limit() {\n  match { $s: Scalars }\n  return { $s.slug, $s.dt }\n  order { $s.dt desc, $s.slug }\n  limit 7\n}",
      "query q_date_order_limit() {\n  match { $s: Scalars }\n  return { $s.slug, $s.dt }\n  order { $s.dt desc, $s.slug }\n  limit 7\n}"),
    Q("q_case_sensitive_eq", "read",
      'query q_case_sensitive_eq() {\n  match {\n    $s: Scalars\n    $s.tags contains "Rust"\n  }\n  return { $s.slug }\n  order { $s.slug }\n}',
      'query q_case_sensitive_eq() {\n  match { $s: Scalars }\n  where "Rust" in $s.tags\n  return { $s.slug }\n  order { $s.slug }\n}'),
    Q("q_evolved_added", "read",
      'query q_evolved_added() {\n  match { $l: Loose { name: "loose2" } }\n  return { $l.name, $l.count }\n  order { $l.count }\n}',
      'query q_evolved_added() {\n  match { $l: Loose }\n  where $l.name = "loose2"\n  return { $l.name, $l.count }\n  order { $l.count }\n}',
      evolved_only=True),
    Q("q_evolved_extra", "read",
      "query q_evolved_extra() {\n  match {\n    $d: Doc\n    $d.extra is not null\n  }\n  return { $d.slug, $d.extra }\n  order { $d.slug }\n}",
      "query q_evolved_extra() {\n  match { $d: Doc }\n  where $d.extra is not null\n  return { $d.slug, $d.extra }\n  order { $d.slug }\n}",
      evolved_only=True),
]


def _pop(cls: str = "Doc", var: str = "$d", where: str = "") -> str:
    w = f" where {where}" if where else ""
    return f"let $pop = {{ match {{ {var}: {cls} }}{w} return {{ {var} }} }}"


SEARCHES: list[Q] = [
    Q("s_search_title", "search",
      "query s_search_title($q: String) {\n  match {\n    $d: Doc\n    search($d.title, $q)\n  }\n  return { $d.slug }\n  order { $d.slug }\n}",
      "query s_search_title($q: String) {\n  match { $d: Doc }\n  where match_terms($d.title, terms($q, mode: any))\n  return { $d.slug }\n  order { $d.slug }\n}",
      params={"q": "graph running"}, rfc=["results.full-text-analysis", "results.letter-case"]),
    Q("s_match_text_body", "search",
      "query s_match_text_body($q: String) {\n  match {\n    $d: Doc\n    match_text($d.body, $q)\n  }\n  return { $d.slug }\n  order { $d.slug }\n}",
      "query s_match_text_body($q: String) {\n  match { $d: Doc }\n  where match_terms($d.body, terms($q, mode: any))\n  return { $d.slug }\n  order { $d.slug }\n}",
      params={"q": "tariff harbor"}, rfc=["results.full-text-analysis"]),
    Q("s_fuzzy_default", "search",
      "query s_fuzzy_default($q: String) {\n  match {\n    $d: Doc\n    fuzzy($d.title, $q)\n  }\n  return { $d.slug }\n  order { $d.slug }\n}",
      "query s_fuzzy_default($q: String) {\n  match { $d: Doc }\n  where match_terms($d.title, terms($q, mode: any, max_edits: 2))\n  return { $d.slug }\n  order { $d.slug }\n}",
      params={"q": "grph"}, rfc=["results.full-text-analysis"]),
    Q("s_fuzzy_one", "search",
      "query s_fuzzy_one($q: String) {\n  match {\n    $d: Doc\n    fuzzy($d.body, $q, 1)\n  }\n  return { $d.slug }\n  order { $d.slug }\n}",
      "query s_fuzzy_one($q: String) {\n  match { $d: Doc }\n  where match_terms($d.body, terms($q, mode: any, max_edits: 1))\n  return { $d.slug }\n  order { $d.slug }\n}",
      params={"q": "zebre"}, rfc=["results.full-text-analysis"]),
    Q("s_bm25_title", "search",
      "query s_bm25_title($q: String) {\n  match { $d: Doc }\n  return { $d.slug, bm25($d.title, $q) as score }\n  order { bm25($d.title, $q) }\n  limit 10\n}",
      f"query s_bm25_title($q: String) {{\n  match {{ {_pop()}, from bm25($pop, $d.title, $q) as $score }}\n  return {{ $d.slug, $score as score }}\n  order {{ $score desc }}\n  limit 10\n}}",
      params={"q": "graph"}, rfc=["results.full-text-analysis", "results.bm25-f64"]),
    Q("s_bm25_filtered", "search",
      "query s_bm25_filtered($q: String) {\n  match {\n    $d: Doc\n    $d.year > 2005\n  }\n  return { $d.slug, bm25($d.body, $q) as score }\n  order { bm25($d.body, $q) }\n  limit 10\n}",
      f"query s_bm25_filtered($q: String) {{\n  match {{ {_pop(where='$d.year > 2005')}, from bm25($pop, $d.body, $q) as $score }}\n  return {{ $d.slug, $score as score }}\n  order {{ $score desc }}\n  limit 10\n}}",
      params={"q": "river stone"}, rfc=["results.full-text-analysis", "results.bm25-f64"]),
    Q("s_bm25_unaliased", "search",
      "query s_bm25_unaliased($q: String) {\n  match { $d: Doc }\n  return { $d.slug, bm25($d.title, $q) }\n  order { bm25($d.title, $q) }\n  limit 5\n}",
      f"query s_bm25_unaliased($q: String) {{\n  match {{ {_pop()}, from bm25($pop, $d.title, $q) as $score }}\n  return {{ $d.slug, $score as _score }}\n  order {{ $score desc }}\n  limit 5\n}}",
      params={"q": "lance vector"}, rfc=["results.full-text-analysis", "results.bm25-f64", "results.bm25-score-rename"]),
    Q("s_bm25_nolimit", "search",
      "query s_bm25_nolimit($q: String) {\n  match { $d: Doc }\n  return { $d.slug }\n  order { bm25($d.title, $q) }\n}",
      f"query s_bm25_nolimit($q: String) {{\n  match {{ {_pop()}, from bm25($pop, $d.title, $q) as $score }}\n  return {{ $d.slug }}\n  order {{ $score desc }}\n}}",
      params={"q": "quantum"}, rfc=["results.full-text-analysis"]),
    Q("s_nearest", "search",
      "query s_nearest($v: Vector(8)) {\n  match { $d: Doc }\n  return { $d.slug, nearest($d.emb, $v) as dist }\n  order { nearest($d.emb, $v) }\n  limit 10\n}",
      f"query s_nearest($v: Vector(8)) {{\n  match {{ {_pop()}, from nearest($pop, $d.emb, $v, k: 10) as $distance }}\n  return {{ $d.slug, $distance as dist }}\n  order {{ $distance }}\n  limit 10\n}}",
      params={"v": VEC}),
    Q("s_nearest_unaliased", "search",
      "query s_nearest_unaliased($v: Vector(8)) {\n  match { $d: Doc }\n  return { $d.slug, nearest($d.emb, $v) }\n  order { nearest($d.emb, $v) }\n  limit 5\n}",
      f"query s_nearest_unaliased($v: Vector(8)) {{\n  match {{ {_pop()}, from nearest($pop, $d.emb, $v, k: 5) as $distance }}\n  return {{ $d.slug, $distance as _distance }}\n  order {{ $distance }}\n  limit 5\n}}",
      params={"v": VEC}, rfc=["results.distance-rename"]),
    Q("s_nearest_nullable", "search",
      "query s_nearest_nullable($v: Vector(8)) {\n  match { $d: Doc }\n  return { $d.slug, nearest($d.emb_opt, $v) as dist }\n  order { nearest($d.emb_opt, $v) }\n  limit 8\n}",
      f"query s_nearest_nullable($v: Vector(8)) {{\n  match {{ {_pop()}, from nearest($pop, $d.emb_opt, $v, k: 8) as $distance }}\n  return {{ $d.slug, $distance as dist }}\n  order {{ $distance }}\n  limit 8\n}}",
      params={"v": VEC}),
    Q("s_nearest_traversal", "search",
      "query s_nearest_traversal($v: Vector(8)) {\n  match {\n    $a: Doc\n    $a cites $b\n  }\n  return { $a.slug, $b.slug, nearest($b.emb, $v) as dist }\n  order { nearest($b.emb, $v) }\n  limit 5\n}",
      "query s_nearest_traversal($v: Vector(8)) {\n  match { let $pop = { match { $a: Doc, $b: Doc, $a cites $b } return { $a, $b } }, from nearest($pop, $b.emb, $v, k: 5) as $distance }\n  return { $a.slug, $b.slug, $distance as dist }\n  order { $distance }\n  limit 5\n}",
      params={"v": VEC}, rfc=["results.fusion-window"]),
    Q("s_rrf", "search",
      "query s_rrf($v: Vector(8), $q: String) {\n  match { $d: Doc }\n  return { $d.slug, rrf(nearest($d.emb, $v), bm25($d.title, $q)) as s }\n  order { rrf(nearest($d.emb, $v), bm25($d.title, $q)) }\n  limit 10\n}",
      "query s_rrf($v: Vector(8), $q: String) {\n  match { let $all = { match { $d: Doc } return { $d } }, from rrf(nearest($all, $d.emb, $v, k: 10), bm25($all, $d.title, $q), k: 60) as $score }\n  return { $d.slug, $score as s }\n  order { $score desc }\n  limit 10\n}",
      params={"v": VEC, "q": "graph"}, rfc=["results.fusion-window", "results.full-text-analysis"]),
    Q("s_search_key", "search",
      "query s_search_key($q: String) {\n  match {\n    $d: Doc\n    search($d.slug, $q)\n  }\n  return { $d.slug }\n  order { $d.slug }\n}",
      "query s_search_key($q: String) {\n  match { $d: Doc }\n  where match_terms($d.slug, terms($q, mode: any))\n  return { $d.slug }\n  order { $d.slug }\n}",
      params={"q": "graph"}, rfc=["results.full-text-analysis"]),
    Q("s_case", "search",
      "query s_case($q: String) {\n  match {\n    $d: Doc\n    search($d.body, $q)\n  }\n  return { $d.slug }\n  order { $d.slug }\n}",
      "query s_case($q: String) {\n  match { $d: Doc }\n  where match_terms($d.body, terms($q, mode: any))\n  return { $d.slug }\n  order { $d.slug }\n}",
      params={"q": "ÜBER"}, rfc=["results.full-text-analysis", "results.letter-case"]),
    Q("s_stopword", "search",
      "query s_stopword($q: String) {\n  match {\n    $d: Doc\n    search($d.title, $q)\n  }\n  return { $d.slug }\n  order { $d.slug }\n}",
      "query s_stopword($q: String) {\n  match { $d: Doc }\n  where match_terms($d.title, terms($q, mode: any))\n  return { $d.slug }\n  order { $d.slug }\n}",
      params={"q": "the"}, rfc=["results.full-text-analysis"]),
    Q("s_stem", "search",
      "query s_stem($q: String) {\n  match {\n    $d: Doc\n    search($d.title, $q)\n  }\n  return { $d.slug }\n  order { $d.slug }\n}",
      "query s_stem($q: String) {\n  match { $d: Doc }\n  where match_terms($d.title, terms($q, mode: any))\n  return { $d.slug }\n  order { $d.slug }\n}",
      params={"q": "run"}, rfc=["results.full-text-analysis"]),
    Q("s_nfd", "search",
      "query s_nfd($q: String) {\n  match {\n    $d: Doc\n    search($d.title, $q)\n  }\n  return { $d.slug }\n  order { $d.slug }\n}",
      "query s_nfd($q: String) {\n  match { $d: Doc }\n  where match_terms($d.title, terms($q, mode: any))\n  return { $d.slug }\n  order { $d.slug }\n}",
      params={"q": "café"}, rfc=["results.full-text-analysis"]),
    Q("s_company_name", "search",
      "query s_company_name($q: String) {\n  match {\n    $c: Company\n    search($c.name, $q)\n  }\n  return { $c.slug }\n  order { $c.slug }\n}",
      "query s_company_name($q: String) {\n  match { $c: Company }\n  where match_terms($c.name, terms($q, mode: any))\n  return { $c.slug }\n  order { $c.slug }\n}",
      params={"q": "graph"}, rfc=["results.full-text-analysis"]),
    Q("s_not_search", "search",
      "query s_not_search($q: String) {\n  match {\n    $d: Doc\n    not search($d.title, $q)\n  }\n  return { $d.slug }\n  order { $d.slug }\n}",
      "query s_not_search($q: String) {\n  match { $d: Doc }\n  where not coalesce(match_terms($d.title, terms($q, mode: any)), false)\n  return { $d.slug }\n  order { $d.slug }\n}",
      params={"q": "graph"}, rfc=["language.accepted-lexical-under-not-or"]),
    Q("s_search_unindexed", "search",
      "query s_search_unindexed($q: String) {\n  match {\n    $d: Doc\n    search($d.note, $q)\n  }\n  return { $d.slug }\n  order { $d.slug }\n}",
      "query s_search_unindexed($q: String) {\n  match { $d: Doc }\n  where match_terms($d.note, terms($q, mode: any))\n  return { $d.slug }\n  order { $d.slug }\n}",
      params={"q": "graph"}, rfc=["results.full-text-index-required"]),
]


# Writes: run the edition-2 text with OLD on one copy and the edition-3 text
# (and every e3_alt) with NEW on another, then compare the two graphs.
WRITES: list[Q] = [
    Q("w_insert_new_key", "write",
      'query w_insert_new_key() {\n  insert Person { email: "new@example.com", name: "New", age: 7 }\n}',
      'query w_insert_new_key() {\n  insert Person { email: "new@example.com", name: "New", age: 7 }\n}',
      e3_alt={"replace": 'query w_insert_new_key() {\n  replace Person { email: "new@example.com", name: "New", age: 7 }\n}'}),
    Q("w_insert_existing_key", "write",
      'query w_insert_existing_key() {\n  insert Person { email: "p001@example.com", name: "Changed" }\n}',
      'query w_insert_existing_key() {\n  replace Person { email: "p001@example.com", name: "Changed" }\n}',
      e3_alt={"insert": 'query w_insert_existing_key() {\n  insert Person { email: "p001@example.com", name: "Changed" }\n}',
              "upsert": 'query w_insert_existing_key() {\n  upsert Person { email: "p001@example.com", name: "Changed" }\n}'},
      rfc=["writes.keyed-insert-key-conflict"]),
    Q("w_insert_compound_existing", "write",
      'query w_insert_compound_existing() {\n  insert Pair { a: "y", b: 4, label: "relabelled" }\n}',
      'query w_insert_compound_existing() {\n  replace Pair { a: "y", b: 4, label: "relabelled" }\n}',
      e3_alt={"insert": 'query w_insert_compound_existing() {\n  insert Pair { a: "y", b: 4, label: "relabelled" }\n}'}),
    Q("w_insert_unkeyed", "write",
      'query w_insert_unkeyed() {\n  insert Loose { name: "fresh", n: 99 }\n}',
      'query w_insert_unkeyed() {\n  insert Loose { name: "fresh", n: 99 }\n}',
      note="unkeyed ids are generated; compared without @id"),
    Q("w_insert_edge", "write",
      'query w_insert_edge() {\n  insert Knows { from: "p000@example.com", to: "p002@example.com", since: 5 }\n}',
      'query w_insert_edge() {\n  insert Knows { @src: "p000@example.com", @dst: "p002@example.com", since: 5 }\n}',
      e3_alt={"from_to": 'query w_insert_edge() {\n  insert Knows { from: "p000@example.com", to: "p002@example.com", since: 5 }\n}'},
      rfc=["writes.from-to-refused"]),
    Q("w_insert_keyed_edge_existing", "write",
      'query w_insert_keyed_edge_existing() {\n  insert WorksAt { from: "p000@example.com", to: "c0", role: "boss" }\n}',
      'query w_insert_keyed_edge_existing() {\n  replace WorksAt { @src: "p000@example.com", @dst: "c0", role: "boss" }\n}',
      e3_alt={"insert": 'query w_insert_keyed_edge_existing() {\n  insert WorksAt { @src: "p000@example.com", @dst: "c0", role: "boss" }\n}'}),
    Q("w_update_node", "write",
      'query w_update_node() {\n  update Person set { name: "Older" } where age > 50\n}',
      'query w_update_node() {\n  match { $x: Person } where $x.age > 50\n  update $x set { name: "Older" }\n}'),
    Q("w_update_scalars", "write",
      'query w_update_scalars() {\n  update Scalars set { i64_opt: 9007199254740993, f64_opt: 0.5, s_opt: "x" } where kind = "beta"\n}',
      'query w_update_scalars() {\n  match { $x: Scalars } where $x.kind = "beta"\n  update $x set { i64_opt: 9007199254740993, f64_opt: 0.5, s_opt: "x" }\n}'),
    Q("w_update_vector", "write",
      'query w_update_vector() {\n  update Doc set { emb: [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0] } where year = 1995\n}',
      'query w_update_vector() {\n  match { $x: Doc } where $x.year = 1995\n  update $x set { emb: [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0] }\n}'),
    Q("w_update_edge", "write",
      'query w_update_edge() {\n  update Knows set { since: 1 } where since > 2070\n}',
      'query w_update_edge() {\n  match { $x: Knows } where $x.since > 2070\n  update $x set { since: 1 }\n}',
      rfc=["writes.edge-update-new"], note="edition 2 refuses an edge update (T16)"),
    Q("w_delete_node", "write",
      'query w_delete_node() {\n  delete Person where email = "p002@example.com"\n}',
      'query w_delete_node() {\n  match { $x: Person } where $x.email = "p002@example.com"\n  delete $x\n}'),
    Q("w_delete_edge", "write",
      'query w_delete_edge() {\n  delete Knows where since > 2050\n}',
      'query w_delete_edge() {\n  match { $x: Knows } where $x.since > 2050\n  delete $x\n}'),
    Q("w_delete_edge_from", "write",
      'query w_delete_edge_from() {\n  delete Knows where @src = "p000@example.com"\n}',
      'query w_delete_edge_from() {\n  match { $x: Knows } where $x.@src = "p000@example.com"\n  delete $x\n}'),
    Q("w_multi_statement", "write",
      'query w_multi_statement() {\n  insert Company { slug: "cx", name: "Graph Co" }\n  insert WorksAt { from: "p001@example.com", to: "cx", role: "r" }\n}',
      'query w_multi_statement() {\n  replace Company { slug: "cx", name: "Graph Co" }\n  replace WorksAt { @src: "p001@example.com", @dst: "cx", role: "r" }\n}'),
    Q("w_blob_insert", "write",
      'query w_blob_insert($c: Blob) {\n  insert Asset { slug: "a000", content: $c }\n}',
      'query w_blob_insert($c: Blob) {\n  replace Asset { slug: "a000", content: $c }\n}',
      e3_alt={"upsert": 'query w_blob_insert($c: Blob) {\n  upsert Asset { slug: "a000", content: $c }\n}'},
      params={"c": "base64:aGVsbG8="}, note="a000 holds ext; edition 2's keyed insert clears it"),
    Q("w_scalar_extremes", "write",
      'query w_scalar_extremes($lo: I64, $hi: U64, $z: F64, $dd: Date, $tt: DateTime) {\n  insert Scalars { slug: "zz", s: "x", b: true, i32: 1000, i64: $lo, u32: 1, u64: $hi, f32: 1.5, f64: $z, d: $dd, dt: $tt, kind: "gamma", tags: ["a"] }\n}',
      'query w_scalar_extremes($lo: I64, $hi: U64, $z: F64, $dd: Date, $tt: DateTime) {\n  insert Scalars { slug: "zz", s: "x", b: true, i32: 1000, i64: $lo, u32: 1, u64: $hi, f32: 1.5, f64: $z, d: $dd, dt: $tt, kind: "gamma", tags: ["a"] }\n}',
      params={"lo": -9223372036854775808, "hi": 18446744073709551615, "z": -0.0, "dd": "2262-04-11", "tt": "2026-01-01T00:00:00.001Z"}),
    Q("w_range_violation", "write",
      'query w_range_violation() {\n  update Scalars set { i32: 5000 } where slug = "s00001"\n}',
      'query w_range_violation() {\n  match { $x: Scalars } where $x.slug = "s00001"\n  update $x set { i32: 5000 }\n}'),
    Q("w_unique_violation", "write",
      'query w_unique_violation() {\n  update Scalars set { u32: 3999999999 } where slug = "s00005"\n}',
      'query w_unique_violation() {\n  match { $x: Scalars } where $x.slug = "s00005"\n  update $x set { u32: 3999999999 }\n}'),
    Q("w_card_violation", "write",
      'query w_card_violation() {\n  insert WorksAt { from: "p000@example.com", to: "c3", role: "second" }\n}',
      'query w_card_violation() {\n  insert WorksAt { @src: "p000@example.com", @dst: "c3", role: "second" }\n}'),
    Q("w_delete_mixed", "write",
      'query w_delete_mixed() {\n  delete Person where email = "p004@example.com"\n  delete Company where slug = "c7"\n}',
      'query w_delete_mixed() {\n  match { $x: Person } where $x.email = "p004@example.com"\n  delete $x\n  match { $y: Company } where $y.slug = "c7"\n  delete $y\n}'),
]


def all_queries(evolved: bool) -> list[Q]:
    return [q for q in READS + SEARCHES if evolved or not q.evolved_only]


def writes(kind: str) -> list[Q]:
    """The writes, with the evolved schema's rename (`Loose.n` is `count`)."""
    if kind != "evolved":
        return WRITES
    import dataclasses
    out = []
    for q in WRITES:
        if q.name == "w_insert_unkeyed":
            fix = lambda s: s.replace("n: 99", "count: 99") if s else s
            q = dataclasses.replace(q, e2=fix(q.e2), e3=fix(q.e3), e3_alt={k: fix(v) for k, v in q.e3_alt.items()})
        out.append(q)
    return out
