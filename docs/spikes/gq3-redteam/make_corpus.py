#!/usr/bin/env python3
"""Write the GQ 3 red-team corpus: one `.gq` file per case under corpus/.

The cases below are the source of truth; the `.gq` files are generated so the
headers stay uniform. Run from anywhere: `python3 docs/spikes/gq3-redteam/make_corpus.py`.

Header lines (each `// key: value`, before the query text):
  task     one line naming the task
  kind     one of the before-and-after asset's eight groups, or adversarial
  expect   accept | refuse | refuse-with-fix:<fix text>  (`<empty>` = delete the construct)
  habit    adversarial cases only: the language habit the text comes from
  rfc      the RFC section the case tests
  shape    optional: the printed clauses of the first declaration, ` | ` between clauses
  returns  optional: the printed return items of the first declaration, ` | ` between items
  raw      optional: `yes` when the file carries its own edition line (no `gq 3;` is prepended)
  later    optional: the refusal the RFC assigns to a later stage (type checker, a later RFC)
  note     optional

The runner (crates/omnigraph-compiler/src/query/parser3_redteam.rs) prepends
`gq 3;` unless the file opens with its own edition line.
"""

import os
import re
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "corpus")

KINDS = {
    1: "lookups-and-filters",
    2: "traversal",
    3: "aggregation",
    4: "shapes-of-a-result",
    5: "ranking-and-search",
    6: "writes",
    7: "catalogue-and-declarations",
    8: "statements",
    9: "adversarial",
}

CASES = []


def case(name, kind, expect, task, body, **extra):
    CASES.append(dict(name=name, kind=kind, expect=expect, task=task, body=body.strip("\n"), **extra))


def m(clauses, ret="$p.name", tail="", name="q", params=""):
    """A read with the given match clauses and return items."""
    return f"query {name}({params}) {{\n  match {{ {clauses} }}\n  return {{ {ret} }}{tail}\n}}"


# ---------------------------------------------------------------------------
# 1. Lookups and filters
# ---------------------------------------------------------------------------
K = 1
case("1-01-key-lookup", K, "accept", "a person by key, the asset's text",
     m("$p: Person { handle: $h }", "$p.name, $p.city", params="$h: String"), rfc="§3 Kept")
case("1-02-key-lookup-as-filter", K, "accept", "a person by key written as a filter clause, which fmt moves into the braces",
     m("$p: Person  $p.handle = $h", "$p.name, $p.city", params="$h: String"),
     rfc="§6 queries fmt", shape="$p: Person | $p.handle = $h")
case("1-03-braces-trailing-comma", K, "accept", "two property matches in braces with a trailing comma",
     m('$p: Person { handle: $h, city: "Berlin", }', params="$h: String"), rfc="§3 Kept")
case("1-04-filter-order-limit-param", K, "accept", "adults ordered by age, the limit a parameter with a default",
     "query adults($min: I32, $k: I64 = 25) {\n  match { $p: Person  $p.age >= $min }\n  return { $p.name }\n  order { $p.age desc }\n  limit $k\n}",
     rfc="§3 Kept, limit $k")
case("1-05-string-calls-in-or", K, "accept", "a title containing a word or starting with a parameter",
     m('$d: Doc  contains($d.title, "graph") or starts_with($d.title, $p)', "$d.title", params="$p: String"),
     rfc="§6 rewriter, infix rows")
case("1-06-list-membership", K, "accept", "documents tagged rust",
     m('$d: Doc  "rust" in $d.tags', "$d.title"), rfc="§6 rewriter, list contains")
case("1-07-in-list-parameter", K, "accept", "people in any of a list of cities",
     m("$p: Person  $p.city in $cities", params="$cities: [String]"), rfc="§3 Kept")
case("1-08-null-tests", K, "accept", "people with a city and an age",
     m("$p: Person  $p.city is not null and not ($p.age is null)"), rfc="§3 Kept")
case("1-09-arith-precedence", K, "accept", "arithmetic on both sides of a comparison",
     m("$p: Person  $p.age * 2 + 1 >= $min - 3", params="$min: I32"),
     rfc="§3 arithmetic", shape="$p: Person | $p.age * 2 + 1 >= $min - 3")
case("1-10-minus-no-spaces", K, "accept", "a let value minus one, written with no spaces",
     m("$p: Person  let $n = $p.age  $n-1 > 0"), rfc="§7 ladder", shape="$p: Person | let $n = $p.age | $n - 1 > 0")
case("1-11-minus-literal-glued", K, "accept", "`$n -1`, which the RFC says is one subtraction",
     m("$p: Person  let $n = $p.age  $n -1 > 0"), rfc="§3 arithmetic: `$n -1` and `$n - 1` are one subtraction",
     shape="$p: Person | let $n = $p.age | $n - 1 > 0")
case("1-12-unary-minus-return", K, "accept", "the negated age as a return item",
     m("$p: Person", "-$p.age as neg"), rfc="§3 arithmetic", returns="-$p.age as neg")
case("1-13-newline-items-leading-minus", K, "accept",
     "two return items on separate lines, the second a negation; the grammar reads one subtraction",
     "query q() {\n  match { $p: Person }\n  return {\n    $p.age\n    -$p.age as neg\n  }\n}",
     rfc="§3 arithmetic; parser spike F5", returns="$p.age - $p.age as neg",
     note="an agent meant two items; edition 2 accepts newline-separated items, so the silent reading is the RFC's own rule")
case("1-14-exponent-float", K, "accept", "a float with an exponent",
     m("$d: Doc  $d.year * 1.0 > 2.0e3", "$d.title"), rfc="§7 float_lit")
case("1-15-exponent-without-fraction", K, "refuse-with-fix:2.0e3", "an exponent float written without a fraction, `2e3`",
     m("$d: Doc  $d.year * 1.0 > 2e3", "$d.title"), rfc="§7 float_lit; parser spike F10")
case("1-16-coalesce-if", K, "accept", "a default city and an age band, the asset's text",
     m("$p: Person", 'coalesce($p.city, "unknown") as city, if($p.age >= 65, "senior", "active") as band'),
     rfc="§3 arithmetic: the conditional rows")
case("1-17-concat", K, "accept", "a display name joined from two properties",
     m("$p: Person", 'concat($p.name, " (", $p.handle, ")") as label'), rfc="§3: concat as a registry row")
case("1-18-nested-parens", K, "accept", "a filter wrapped in redundant parentheses",
     m("$p: Person  ((($p.age))) > 3"), rfc="§7 operand", shape="$p: Person | $p.age > 3")
case("1-19-modulo", K, "accept", "people with an even age",
     m("$p: Person  $p.age % 2 = 0"), rfc="§3 arithmetic")
case("1-20-interface-binding", K, "accept", "every implementor of Named",
     m("$n: Named", "$n.name"), rfc="§3 Kept")
case("1-21-meta-field-filter", K, "accept", "a node by its id meta-field",
     m("$p: Person  $p.@id = $id", params="$id: String"), rfc="§6 freeze list, meta-fields")
case("1-22-leading-minus-filter", K, "accept", "a filter that begins with a negative literal, `-1 < $x`",
     m("$p: Person  let $x = $p.age  -1 < $x"), rfc="§3 arithmetic; parser spike F5",
     note="nothing in the RFC refuses a filter that begins with `-`")
case("1-23-date-compare", K, "accept", "documents after a date literal",
     m('$d: Doc  $d.year >= 2025  $p: Person  $p.age > 3  date("2025-01-01") < datetime("2026-01-01T00:00:00Z")', "$d.title"),
     rfc="§3 Kept")
case("1-24-param-default-negative", K, "accept", "a parameter with a negative default",
     m("$p: Person  $p.age > $floor", params="$floor: I32 = -1"), rfc="§6 freeze list, parameters")
case("1-25-division-by-param", K, "accept", "a ratio of two properties against a threshold",
     m("$d: Doc  $p: Person  $d.year / $p.age > $t", "$d.title", params="$t: F64"), rfc="§3 arithmetic")
case("1-26-unary-minus-double", K, "accept", "a double negation",
     m("$p: Person  - -$p.age < 0"), rfc="§3 arithmetic", note="a filter beginning with `-` again")

# ---------------------------------------------------------------------------
# 2. Traversal
# ---------------------------------------------------------------------------
K = 2
case("2-01-one-hop-edge-bound", K, "accept", "an employer with the edge bound and its endpoints",
     m("$p: Person { handle: $h }  $p $k:worksAt $c", "$c.name, $k.@src, $k.@dst", params="$h: String"), rfc="§3 Kept")
case("2-02-undirected", K, "accept", "friends in either direction",
     m("$p: Person { handle: $h }  $p <knows> $f", "$f.name", params="$h: String"), rfc="§3 Kept")
case("2-03-alternation", K, "accept", "a person reached by knows or by worksAt-colleague (alternation)",
     m("$p: Person  $p (knows | authored) $x", "$p.name"), rfc="§3 Kept", later="T5 (members end on different types)")
case("2-04-bounded-hops", K, "accept", "friends of friends up to three hops",
     m("$p: Person { handle: $h }  $p knows{1,3} $q", "$q.name", params="$h: String"), rfc="§3 Kept")
case("2-05-wildcard-bounded", K, "accept", "anything one to two hops away",
     m("$p: Person { handle: $h }  $p *{1,2} $x  $x: Doc", "$x.title", params="$h: String"), rfc="§3 Kept")
case("2-06-quoted-in-edge", K, "accept", "documents in a collection, the edge quoted",
     m('$x: Collection { slug: $s }  $d "in" $x', "$d.title", params="$s: String"), rfc="§8 schema comment")
case("2-07-against-schema-order", K, "accept", "employees of a company, written against schema order",
     m("$c: Company { slug: $s }  $c worksAt $p", params="$s: String"), rfc="§6 queries fmt")
case("2-08-bare-in-edge", K, 'refuse-with-fix:$d "in" $x', "documents in a collection with the reserved edge name written bare",
     m("$x: Collection { slug: $s }  $d in $x", "$d.title", params="$s: String"),
     rfc="§3 refused respellings: `$d in $x` over a reserved edge is a parse error",
     note="parser spike F19: it is a legal membership filter")
case("2-09-edge-named-from", K, "accept", "a traversal over an edge named `from`",
     m("$a: Person  $a from $b", "$b.name"), rfc="§7: let and from are legal as edge names", shape="$a: Person | $a from $b",
     note="parser spike F8 makes this a quoted-only edge")
case("2-10-edge-named-let", K, "accept", "a traversal over an edge named `let`",
     m("$a: Person  $a let $b", "$b.name"), rfc="§7: let and from are legal as edge names", shape="$a: Person | $a let $b")
case("2-11-edge-named-union", K, "accept", "a traversal over an edge named `union`",
     m("$a: Person  $a union $b", "$b.name"), rfc="§7", shape="$a: Person | $a union $b")
case("2-12-edge-named-optional", K, "accept", "a traversal over an edge named `optional`",
     m("$a: Person  $a optional $b", "$b.name"), rfc="§7", shape="$a: Person | $a optional $b")
case("2-13-edge-named-exists", K, "accept", "a traversal over an edge named `exists`",
     m("$a: Person  $a exists $b", "$b.name"), rfc="§7", shape="$a: Person | $a exists $b")
case("2-14-edge-named-not", K, 'refuse-with-fix:$a "not" $b', "a traversal over an edge named `not`, written bare",
     m("$a: Person  $a not $b", "$b.name"), rfc="§6 reserved words")
case("2-15-edge-named-as", K, "accept", "a traversal over an edge named `as`",
     m("$a: Person  $a as $b", "$b.name"), rfc="§7", shape="$a: Person | $a as $b")
case("2-16-edge-named-insert-in-write", K, "accept", "a write whose clause traverses an edge named `insert`",
     "query w() {\n  $a: Person  $a insert $b\n  delete $b\n}", rfc="§7 stmt_keyword", shape="$a: Person | $a insert $b")
case("2-17-arrow-out", K, "refuse-with-fix:$a knows $b", "a directed traversal written with an arrow",
     m("$a: Person  $a -knows-> $b", "$b.name"), rfc="§3 refused respellings: the arrow traversals")
case("2-18-arrow-in", K, "refuse-with-fix:$b knows $a", "an incoming traversal written with a left arrow",
     m("$a: Person  $a <-knows- $b", "$b.name"), rfc="§3 refused respellings")
case("2-19-arrow-undirected", K, "refuse-with-fix:$a <knows> $b", "an undirected traversal written with dashes",
     m("$a: Person  $a -knows- $b", "$b.name"), rfc="§7 parser test pins `$a -knows- $b`")
case("2-20-arrow-with-binding-and-bounds", K, "refuse-with-fix:$a $k:knows{1,2} $b", "an arrow with an edge binding and bounds",
     m("$a: Person  $a -$k:knows{1,2}-> $b", "$b.name"), rfc="§3 refused respellings")
case("2-21-edge-binding-with-bounds", K, "accept", "an edge binding on a multi-hop traversal",
     m("$a: Person  $a $k:knows{1,2} $b", "$b.name"), rfc="§2 Expand: $k binds only at m = n = 1",
     later="a type error: the edge binds only on a single hop")
case("2-22-catalogue-edge", K, "accept", "schema types and their properties through a catalogue edge",
     m("$t: @Type  $p: @Property  $t @property $p", "$t.name, $p.name"), rfc="§6 freeze list, catalogue names",
     later="unknown `@` name until stage S")
case("2-23-minus-between-node-vars", K, "accept", "`$a - $b` with two node variables (a subtraction, ill-typed)",
     m("$a: Person  $b: Person  $a - $b > 0", "$a.name"), rfc="§7: `$n - $m > 0` is arithmetic",
     shape="$a: Person | $b: Person | $a - $b > 0", later="type error: arithmetic over node references")
case("2-24-minus-bare-name-minus", K, "refuse-with-fix:$a <knows> $b", "`$a - knows - $b`, spaced dashes around an edge name",
     m("$a: Person  $a - knows - $b", "$b.name"), rfc="§7 arrow_traversal; parser spike F15")
case("2-25-lt-gt-around-edge-and-compare", K, "accept",
     "an undirected traversal and an age comparison in neighbouring clauses",
     m("$a: Person  $a <knows> $b  $a.age < $b.age  $b.age > 3", "$b.name"), rfc="§7",
     shape="$a: Person | $a <knows> $b | $a.age < $b.age | $b.age > 3")
case("2-26-lt-name-gt-is-a-traversal", K, "accept",
     "`$x < y > $z`, a chained comparison an agent might write, reads as an undirected traversal over edge y",
     m("$a: Person  let $x = 1  let $z = 9  $x < y > $z", "$a.name"), rfc="§7 undirected_edge",
     shape="$a: Person | let $x = 1 | let $z = 9 | $x <y> $z",
     note="silent: the comparison chain becomes a traversal; edge y does not exist, so the type checker refuses it as an unknown edge, not as a chain")
case("2-27-open-upper-bound", K, "refuse", "unbounded reachability as an open hop range `knows{1,}`",
     m("$a: Person  $a knows{1,} $b", "$b.name"), rfc="§2 Expand: an open upper bound is a later change",
     note="additive later; must be refused now with a message that does not pretend it exists")
case("2-28-reachable-recursion", K, "accept", "unbounded reachability as a recursive declared read, the RFC's text",
     "query reachable($from: String) {\n  match {\n    from union(\n      { match { $p: Person { handle: $from }  $p knows $q } return { $q } },\n      { match { from reachable($from)  $q knows $r } return { $r as q } })\n  }\n  return distinct { $q }\n}",
     rfc="§3 Recursion", later="T70 until stage Rec")
case("2-29-reachable-reordered", K, "accept", "the reachable read with its step arm first and the base clauses swapped",
     "query reachable($from: String) {\n  match {\n    from union(\n      { match { from reachable($from)  $q knows $r } return { $r as q } },\n      { match { $p knows $q  $p: Person { handle: $from } } return { $q } })\n  }\n  return distinct { $q }\n}",
     rfc="§3 Recursion; §2 Union is a bag (arm order changes the names: from the first)",
     note="equivalent rows; the column names now come from the step arm, which aliases to q")

# ---------------------------------------------------------------------------
# 3. Aggregation
# ---------------------------------------------------------------------------
K = 3
case("3-01-count-per-group", K, "accept", "headcount per company ordered by the alias",
     "query headcount() {\n  match { $p worksAt $c }\n  return { $c.name, count($p) as n }\n  order { n desc }\n}", rfc="§3 Kept")
case("3-02-count-distinct", K, "accept", "the number of distinct cities",
     m("$p: Person", "count(distinct $p.city) as cities"), rfc="§6 freeze list: agg(distinct x)")
case("3-03-big-employers", K, "accept", "employers with at least 100 people, the RFC's §8 text",
     "query big_employers() {\n  match {\n    from { match { $p worksAt $c } return { $c, count($p) as headcount } }\n    $headcount >= 100\n  }\n  return { $c.name, $headcount }\n  order { $headcount desc }\n}",
     rfc="§8")
case("3-04-big-employers-param", K, "accept", "employers above a parameter threshold, filter written before the from",
     "query big_employers($min: I64) {\n  match {\n    from { match { $c: Company  $p worksAt $c } return { $c, count($p) as headcount } }\n    $headcount >= $min\n  }\n  return { $c.name, $headcount }\n}",
     rfc="§8 rewritten: the binding added inside the inline read")
case("3-05-aggregate-of-aggregate", K, "accept", "the mean and largest headcount",
     m("from { match { $p worksAt $c } return { $c, count($p) as n } }", "avg($n) as mean, max($n) as largest"), rfc="asset §3")
case("3-06-count-block-param", K, "accept", "people who know at least $min others",
     m("$p: Person  count { $p knows $q } >= $min", params="$min: I64"), rfc="§3 Kept")
case("3-07-count-block-other-binding", K, "accept", "collections with at least their minimum of documents (T40 lifted)",
     m('$x: Collection  count { $d "in" $x } >= $x.min_docs', "$x.slug"), rfc="§3 refused respellings: legal")
case("3-08-let-block-value", K, "accept", "degree as a reusable value, the asset's text",
     "query degrees($min: I64) {\n  match {\n    $p: Person\n    let $degree = count { $p knows $q }\n    $degree >= $min\n  }\n  return { $p.name, $degree }\n  order { $degree desc }\n}",
     rfc="asset §3")
case("3-09-sum-block", K, "accept", "authors whose documents' years sum past a threshold",
     m("$p: Person  sum($d.year) { $p authored $d } > 4000"), rfc="§3 Kept")
case("3-10-block-in-return", K, "accept", "the degree as a return item without a let",
     m("$p: Person", "$p.name, count { $p knows $q } as degree"), rfc="§7 block_value as an operand")
case("3-11-exists-in-return", K, "accept", "whether each person authored anything, as a column",
     m("$p: Person", "$p.name, exists { $p authored $d } as is_author"), rfc="§7 block_value")
case("3-12-count-block-or", K, "accept", "a count block compared, then or-ed with another filter",
     m("$p: Person  count { $p knows $q } > 1 or $p.age > 3"), rfc="§7; parser spike F7",
     shape="$p: Person | count { $p knows $q } > 1 or $p.age > 3")
case("3-13-nested-blocks", K, "accept", "people who know someone well connected",
     m("$p: Person  count { $p knows $q  count { $q knows $r } > 2 } >= 1"), rfc="§3 Kept")
case("3-14-exists-and-not", K, "accept", "authors without citations, the asset's text",
     m("$p: Person  exists { $p authored $d }  not { $p authored $d2  $other cites $d2 }"), rfc="asset §3")
case("3-15-avg-distinct", K, "accept", "the average of distinct ages",
     m("$p: Person", "avg(distinct $p.age) as a"), rfc="§6 freeze list; unresolved question 5",
     later="T57 distinct applies only to count")
case("3-16-group-by-two-keys", K, "accept", "headcount per company and city",
     "query q() {\n  match { $p worksAt $c }\n  return { $c.name, $p.city, count($p) as n }\n  order { n desc, $c.name }\n}", rfc="§3 Kept")
case("3-17-min-max-block", K, "accept", "the earliest year among a person's documents, as a value",
     m("$p: Person  let $first = min($d.year) { $p authored $d }", "$p.name, $first"), rfc="§7 block_value")
case("3-18-block-arith", K, "accept", "a ratio of two block values",
     m("$p: Person  count { $p knows $q } * 2 > count { $p authored $d } + 1"), rfc="§7 block_value with arithmetic")

# ---------------------------------------------------------------------------
# 4. Shapes of a result
# ---------------------------------------------------------------------------
K = 4
case("4-01-optional", K, "accept", "people with their employer when they have one",
     m("$p: Person  optional { $p worksAt $c }", "$p.name, $c.name"), rfc="§3 Kept: optional served")
case("4-02-union-two-reads", K, "accept", "names of people and companies, the asset's text",
     "query people_and_companies() {\n  match {\n    from union(\n      { match { $p: Person } return { $p.name as name, \"person\" as kind } },\n      { match { $c: Company } return { $c.name as name, \"company\" as kind } })\n  }\n  return { $name, $kind }\n}",
     rfc="§3 relation expressions: union")
case("4-03-union-three-flat", K, "accept", "a union of three reads in one call",
     "query q() {\n  match {\n    from union(\n      { match { $p: Person } return { $p.name as name } },\n      { match { $c: Company } return { $c.name as name } },\n      { match { $d: Doc } return { $d.title as name } })\n  }\n  return { $name }\n}",
     rfc="§3: union folded from the left")
case("4-04-asset-colleagues-of-colleagues", K, "accept", "the asset's colleagues_of_colleagues, inner read without match",
     "query colleagues($of: String) {\n  match { $p: Person { handle: $of }  $p worksAt $c  $q worksAt $c  $q != $p }\n  return { $q }\n}\n\nquery colleagues_of_colleagues($of: String) {\n  match {\n    from colleagues($of)\n    from { from colleagues($q.handle) return { $q as r } }\n  }\n  return distinct { $r.name }\n}",
     rfc="asset §4", note="parser spike F3 and core spike F2 report the missing match; it is still in the asset")
case("4-05-colleagues-of-colleagues-fixed", K, "accept", "colleagues_of_colleagues with the inner match and a renaming wrapper",
     "query colleagues($of: String) {\n  match { $p: Person { handle: $of }  $p worksAt $c  $q worksAt $c  $q != $p }\n  return { $q }\n}\n\nquery colleagues_of_colleagues($of: String) {\n  match {\n    from colleagues($of)\n    from { match { from colleagues($q.handle) } return { $q as r } }\n  }\n  return distinct { $r.name }\n}",
     rfc="asset §4 rewritten", later="T68 ($q imported into a scope that has $q; core spike F2)")
case("4-06-latest-per-author", K, "accept", "the newest three documents per author",
     m("$p: Person  from { match { $p authored $d } return { $d } order { $d.year desc } limit 3 }", "$p.name, $d.title, $d.year"),
     rfc="asset §4")
case("4-07-let-relation-twice", K, "accept", "a named population drawn from twice",
     m("let $people = { match { $p: Person } return { $p } }  from $people  from { match { from $people } return { $p as other } }",
       "$p.name, $other.name"), rfc="§3 let $r = rel")
case("4-08-call-with-named-arg", K, "accept", "a declared read called with a named argument",
     "query colleagues($of: String) {\n  match { $p: Person { handle: $of }  $p worksAt $c  $q worksAt $c }\n  return { $q }\n}\n\nquery q($h: String) {\n  match { from colleagues(of: $h) }\n  return { $q.name }\n}",
     rfc="§2 Call: scalar arguments", note="the grammar reads `of: $h` as an option; the RFC never says whether a declared read takes options")
case("4-09-from-paren-inline", K, "refuse-with-fix:from { match { $p: Person } return { $p } }",
     "a from whose relation is a parenthesised inline read",
     m("from ({ match { $p: Person } return { $p } })"), rfc="§7 relation_expr")
case("4-10-from-paren-call", K, "refuse-with-fix:from union({ match { $p: Person } return { $p } }, { match { $p: Person } return { $p } })",
     "a from whose relation is a parenthesised call",
     m("from (union({ match { $p: Person } return { $p } }, { match { $p: Person } return { $p } }))"), rfc="§7 relation_expr")
case("4-11-from-scalar-let", K, "accept", "a from over a scalar let (a type error)",
     m("$p: Person  let $n = 1  from $n"), rfc="§5: T69 the other way", later="T69")
case("4-12-let-call-relation", K, "accept", "a let naming a ranking call's relation",
     m("let $near = nearest({ match { $d: Doc } return { $d } }, embedding, $q, k: 5)  from $near", "$d.title, $distance",
       params="$q: Vector(3)"), rfc="§7: a call on the right of let is typed by its registry kind")
case("4-13-optional-from", K, "accept", "an optional from that keeps targets an arm missed",
     m("$d: Doc  optional { from nearest({ match { $d: Doc } return { $d } }, embedding, $q, k: 5) }", "$d.title, $distance",
       params="$q: Vector(3)"), rfc="§4 Fusion: optional { from … }")
case("4-14-from-in-count-block", K, "accept", "a count over a from inside a block",
     m("$p: Person  count { from { match { $p knows $q } return { $q } } } > 2"), rfc="§7 clause in a block")
case("4-15-inline-without-return", K, "refuse", "an inline read with order but no return",
     m("$p: Person  from { match { $p knows $q } order { $q.age } }"), rfc="§7 read_body")
case("4-16-inline-distinct", K, "accept", "an inline read with return distinct",
     m("from { match { $p knows $q } return distinct { $q } }", "$q.name"), rfc="§7 read_body")
case("4-17-three-level-nesting", K, "accept", "a from inside a from inside a from",
     m("from { match { from { match { from { match { $p: Person } return { $p } } } return { $p } } } return { $p } }"),
     rfc="§3 relation expressions")
case("4-18-distance-recursion", K, "accept", "shortest hop count by recursion with a round budget, the RFC's text",
     "query distance($from: String) @budget(rounds = 50) {\n  match {\n    from union(\n      { match { $p: Person { handle: $from }  $p knows $q } return { $q, 1 as d } },\n      { match { from distance($from)  $q knows $r } return { $r as q, $d + 1 as d } })\n  }\n  return { $q, min($d) as d }\n}",
     rfc="§3 Recursion", later="T70 until stage Rec")
case("4-19-cross-product", K, "accept", "two patterns that share no variable",
     m("$p: Person  $c: Company", "$p.name, $c.name"), rfc="§2 derived forms")
case("4-20-from-call-space-before-paren", K, "accept", "a from over a declared read with a space before the parenthesis",
     "query colleagues($of: String) {\n  match { $p: Person { handle: $of }  $p worksAt $c  $q worksAt $c }\n  return { $q }\n}\n\nquery q($h: String) {\n  match { from colleagues ($h) }\n  return { $q.name }\n}",
     rfc="§7: from is the clause when the next token is an identifier followed by `(`", shape="from colleagues($h)")
case("4-21-from-nearest-space-before-paren", K, "accept", "a from over nearest with a space before the parenthesis",
     m("from nearest ({ match { $d: Doc } return { $d } }, embedding, $q, k: 5)", "$d.title", params="$q: Vector(3)"),
     rfc="§7 from lookahead", shape="from nearest({ match { $d: Doc } return { $d } }, embedding, $q, k: 5)")
case("4-22-optional-nested", K, "accept", "an optional inside an optional",
     m("$p: Person  optional { $p worksAt $c  optional { $q worksAt $c } }", "$p.name, $c.name, $q.name"), rfc="§3")
case("4-23-correlated-from-reads-outer", K, "accept", "a correlated from whose inner read uses an outer property",
     m("$p: Person  from { match { $q: Person  $q.age > $p.age } return { $q as older } }", "$p.name, $older.name"),
     rfc="§3 Scope and correlation")
case("4-24-let-then-let-relation-same-name", K, "accept", "a let value and a let relation in one match",
     m("$p: Person  let $a = $p.age + 1  let $r = { match { $q: Person } return { $q } }  from $r", "$a, $q.name"), rfc="§3")

# ---------------------------------------------------------------------------
# 5. Ranking and search
# ---------------------------------------------------------------------------
K = 5
case("5-01-nearest-window", K, "accept", "the 20 nearest recent documents, the asset's text",
     m("from nearest({ match { $d: Doc  $d.year >= 2025 } return { $d } }, embedding, $q, k: 20)", "$d.title",
       tail="\n  order { $distance }", params="$q: Vector(3)"), rfc="asset §5")
case("5-02-nearest-scored", K, "accept", "the distance returned under an alias",
     m("from nearest({ match { $d: Doc } return { $d } }, embedding, $q, k: 20)", "$d.title, $distance as s",
       tail="\n  order { $distance }", params="$q: Vector(3)"), rfc="asset §5")
case("5-03-cited-near", K, "accept", "a window scoped by a collection, then a traversal, the RFC's §8 text",
     "query cited_near($coll: String, $q: Vector(3)) {\n  match {\n    from nearest(\n      { match { $d \"in\" $x  $x: Collection { slug: $coll } } return { $d } },\n      embedding, $q, k: 20\n    )\n    $d cites $other\n  }\n  return { $d.title, $distance, count($other) as cites }\n  order { $distance }\n}",
     rfc="§8")
case("5-04-cited-near-let-population", K, "accept", "cited_near rewritten with the population named by let first",
     "query cited_near($coll: String, $q: Vector(3)) {\n  match {\n    let $pop = { match { $x: Collection { slug: $coll }  $d \"in\" $x } return { $d } }\n    from nearest($pop, embedding, $q, k: 20)\n    $d cites $other\n  }\n  return { $d.title, $distance, count($other) as cites }\n  order { $distance }\n}",
     rfc="§8 rewritten (A6's literal-and-reference exception)")
case("5-05-within-radius", K, "accept", "every recent document within a distance, by l2",
     m("from l2({ match { $d: Doc  $d.year >= 2025 } return { $d } }, embedding, $q)  $distance <= $r", "$d.title, $distance",
       tail="\n  order { $distance, $d.id }\n  limit 10", params="$q: Vector(3), $r: F32"), rfc="asset §5")
case("5-06-bm25", K, "accept", "documents ranked by BM25",
     m("from bm25({ match { $d: Doc } return { $d } }, body, $q)", "$d.title, $score as s",
       tail="\n  order { $score desc }\n  limit 10", params="$q: String"), rfc="asset §5")
case("5-07-passages", K, "accept", "the RFC's §6 passages example",
     "query passages($matter: String, $q: String, $k: I64 = 5) {\n  match {\n    from bm25(\n      { match { $r: SourceRevision { matter_number: $matter }  $r hasPassage $p  contains($p.text, \"tariff\") }\n        return { $r, $p } },\n      text, $q)\n  }\n  return { $r.path, $p.locator, $score }\n  order { $score desc }\n  limit $k\n}",
     rfc="§6", later="core spike F9: two node columns in the population")
case("5-08-lexical-filters", K, "accept", "search and fuzzy as filters, the asset's text",
     m("$d: Doc  search($d.body, $q)  fuzzy($d.title, $q, max_edits: 2)", "$d.title", params="$q: String"), rfc="asset §5")
case("5-09-hybrid-rrf", K, "accept", "hybrid fused ranking with a named population",
     "query hybrid($q: String, $v: Vector(3)) {\n  match {\n    let $docs = { match { $d: Doc } return { $d } }\n    from rrf(nearest($docs, embedding, $v, k: 50), bm25($docs, body, $q), k: 60)\n  }\n  return { $d.title, $score }\n  order { $score desc }\n  limit 10\n}",
     rfc="asset §5")
case("5-10-asset-weighted", K, "accept", "the asset's weighted hybrid, inner reads without match",
     "query weighted($q: String, $v: Vector(3)) {\n  match {\n    let $docs = { match { $d: Doc } return { $d } }\n    from { from l2($docs, embedding, $v) return { $d, $distance as vec } }\n    from { from bm25($docs, body, $q) return { $d as d2, $score as lex } }\n    $d = $d2\n  }\n  return { $d.title, 0.7 * (1.0 / (1.0 + $vec)) + 0.3 * $lex as s }\n  order { $s desc }\n  limit 10\n}",
     rfc="asset §5", note="parser spike F3")
case("5-11-weighted-fixed", K, "accept", "the weighted hybrid with match added to the inner reads",
     "query weighted($q: String, $v: Vector(3)) {\n  match {\n    let $docs = { match { $d: Doc } return { $d } }\n    from { match { from l2($docs, embedding, $v) } return { $d, $distance as vec } }\n    from { match { from bm25($docs, body, $q) } return { $d as d2, $score as lex } }\n    $d = $d2\n  }\n  return { $d.title, 0.7 * (1.0 / (1.0 + $vec)) + 0.3 * $lex as s }\n  order { $s desc }\n  limit 10\n}",
     rfc="asset §5 rewritten")
case("5-12-recall", K, "accept", "a declared recall on nearest",
     m("from nearest({ match { $d: Doc } return { $d } }, embedding, $q, k: 20, recall: 0.9)", "$d.title, $distance",
       params="$q: Vector(3)"), rfc="§3 relation expressions")
case("5-13-bm25-terms", K, "accept", "BM25 over an all-terms one-edit lexical query",
     m("from bm25({ match { $d: Doc } return { $d } }, body, terms($q, mode: all, max_edits: 1))", "$d.title, $score",
       params="$q: String"), rfc="§4 the lexical query")
case("5-14-match-terms", K, "accept", "a lexical predicate with any-term matching",
     m("$d: Doc  match_terms($d.body, terms($q, mode: any))", "$d.title", params="$q: String"), rfc="§4")
case("5-15-rrf-weights-three-arms", K, "accept", "three fused arms with weights",
     "query q($q: String, $v: Vector(3)) {\n  match {\n    let $docs = { match { $d: Doc } return { $d } }\n    from rrf(nearest($docs, embedding, $v, k: 50), bm25($docs, body, $q), bm25($docs, title, $q), k: 60, weights: [0.5, 0.3, 0.2])\n  }\n  return { $d.title, $score }\n  order { $score desc }\n}",
     rfc="§4 Fusion")
case("5-16-nearest-k-param", K, "accept", "a nearest window sized by a parameter",
     m("from nearest({ match { $d: Doc } return { $d } }, embedding, $q, k: $k)", "$d.title",
       params="$q: Vector(3), $k: I64 = 10"), rfc="§3")
case("5-17-order-nearest", K, "accept", "edition 2's ranking in order",
     m("$d: Doc", "$d.title", tail="\n  order { nearest($d.embedding, $q) }\n  limit 20", params="$q: Vector(3)"),
     rfc="§3 refused respellings", later="T65 with the from fix")
case("5-18-return-bm25", K, "accept", "edition 2's score as a return item",
     m("$d: Doc", "$d.title, bm25($d.body, $q) as s", params="$q: String"), rfc="§3 refused respellings",
     later="T65 with the from fix")
case("5-19-positional-k", K, "accept", "nearest with k written positionally",
     m("from nearest({ match { $d: Doc } return { $d } }, embedding, $q, 20)", "$d.title", params="$q: Vector(3)"),
     rfc="§3 refused respellings: positional k", later="positional option refused with the option as the fix")
case("5-20-fuzzy-positional", K, "accept", "fuzzy with the edit budget positional",
     m("$d: Doc  fuzzy($d.title, $q, 2)", "$d.title", params="$q: String"), rfc="§6 rewriter row",
     later="positional option refused")
case("5-21-nearest-without-k", K, "accept", "nearest with no window",
     m("from nearest({ match { $d: Doc } return { $d } }, embedding, $q)", "$d.title", params="$q: Vector(3)"),
     rfc="§4 Errors", later="T17")
case("5-22-population-shorthand", K, "accept", "nearest over a node binding instead of a relation (the unresolved shorthand)",
     m("$d: Doc  $d.year > 2020  from nearest($d, embedding, $q, k: 5)", "$d.title", params="$q: Vector(3)"),
     rfc="unresolved question 1", later="T69")
case("5-23-field-as-prop-access", K, "accept", "nearest with the field written as a property access, edition 2's habit",
     m("from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 5)", "$d.title", params="$q: Vector(3)"),
     rfc="§3: field is an identifier, never a string", later="type error naming the field form")
case("5-24-field-as-string", K, "accept", "nearest with the field written as a string",
     m('from nearest({ match { $d: Doc } return { $d } }, "embedding", $q, k: 5)', "$d.title", params="$q: Vector(3)"),
     rfc="§3", later="type error")
case("5-25-rrf-in-order", K, "accept", "edition 2's fused order",
     m("$d: Doc", "$d.title", tail="\n  order { rrf(nearest($d.embedding, $v), bm25($d.body, $q), 60) }\n  limit 10",
       params="$q: String, $v: Vector(3)"), rfc="§6 rewriter", later="T65")
case("5-26-options-reordered", K, "accept", "nearest with recall before k",
     m("from nearest({ match { $d: Doc } return { $d } }, embedding, $q, recall: 0.9, k: 20)", "$d.title",
       params="$q: Vector(3)"), rfc="§3")
case("5-27-option-before-positional", K, "refuse", "an option written before a positional argument",
     m("from nearest({ match { $d: Doc } return { $d } }, k: 20, embedding, $q)", "$d.title", params="$q: Vector(3)"),
     rfc="§6 freeze list: name(positional…, option: value…)")
case("5-28-tag-near-write", K, "accept", "a write whose targets come from a ranked read",
     "query tag_near($q: Vector(3)) {\n  from nearest({ match { $d: Doc } return { $d } }, embedding, $q, k: 20)\n  update $d set { tags: [\"near\"] }\n}",
     rfc="asset §6")

# ---------------------------------------------------------------------------
# 6. Writes
# ---------------------------------------------------------------------------
K = 6
case("6-01-hire", K, "accept", "insert a node and a keyed edge",
     "query hire($h: String, $slug: String) {\n  insert Person { handle: $h, name: $h }\n  insert WorksAt { from: $h, to: $slug }\n}", rfc="asset §6")
case("6-02-retire", K, "accept", "the RFC's §8 retire",
     "query retire($h: String) as commit {\n  $p: Person { handle: $h }\n  $p $k:worksAt $c\n  delete $k\n  update $p set { city: null }\n}", rfc="§8")
case("6-03-close-company", K, "accept", "the RFC's §3 close_company",
     "query close_company($slug: String) {\n  $c: Company { slug: $slug }\n  $p $k:worksAt $c\n  delete $k\n  update $p set { city: null }\n}", rfc="§3")
case("6-04-close-company-reordered", K, "accept", "close_company with its statements swapped and the binding after the triple",
     "query close_company($slug: String) {\n  $p $k:worksAt $c\n  $c: Company { slug: $slug }\n  update $p set { city: null }\n  delete $k\n}",
     rfc="§3: statements see one snapshot, so their order cannot matter")
case("6-05-birthday", K, "accept", "a computed update",
     "query birthday($h: String) {\n  $p: Person { handle: $h }\n  update $p set { age: $p.age + 1 }\n}", rfc="asset §6")
case("6-06-propose-unattached", K, "accept", "an insert as an unattached branch",
     "query propose($h: String) as unattached branch {\n  insert Person { handle: $h, name: $h }\n}", rfc="asset §6")
case("6-07-write-in-match-wrapper", K, "refuse-with-fix:$p: Person { handle: $h }",
     "a delete whose clauses sit in a `match { }` block, as the prose says",
     "query w($h: String) {\n  match { $p: Person { handle: $h } }\n  delete $p\n}",
     rfc="§3 Writes fed by reads: 'zero or more match clauses'; parser spike F2")
case("6-08-delete-where-type", K, "refuse-with-fix:$x: Person  $x.handle = $h  delete $x", "edition 2's delete by predicate",
     "query w($h: String) {\n  delete Person where handle = $h\n}", rfc="§6 rewriter row")
case("6-09-update-where-type", K, "refuse-with-fix:$x: Person  $x.handle = $h  update $x set { age: 3 }", "edition 2's update by predicate",
     "query w($h: String) {\n  update Person set { age: 3 } where handle = $h\n}", rfc="§6 rewriter row")
case("6-10-delete-var-where", K, "refuse-with-fix:$p.age > 90  delete $p", "a bound delete with a where",
     "query w() {\n  $p: Person\n  delete $p where $p.age > 90\n}", rfc="§5: a where on a statement")
case("6-11-delete-edge-type-where", K, "refuse-with-fix:$x: WorksAt  $x.@src = $h  delete $x", "edition 2's edge delete by source",
     "query w($h: String) {\n  delete WorksAt where @src = $h\n}", rfc="§6 rewriter row; core spike F18",
     note="the RFC's row writes `$x: WorksAt`, a binding over an edge type; core spike F18 says no edition-3 form binds an edge type",
     later="core spike F18: is `$x: WorksAt` a legal binding?")
case("6-12-clause-after-statement", K, "refuse-with-fix:$p: Person { handle: $h }  delete $p", "a binding written after its statement",
     "query w($h: String) {\n  delete $p\n  $p: Person { handle: $h }\n}", rfc="§5: a statement before a clause … prints the order")
case("6-13-mutation-keyword", K, "refuse-with-fix:query hire($h: String) {\n  insert Person { handle: $h, name: $h }\n}",
     "a write declared with mutation",
     "mutation hire($h: String) {\n  insert Person { handle: $h, name: $h }\n}", rfc="§3 refused respellings")
case("6-14-as-commit-on-read", K, "refuse-with-fix:<empty>", "as commit on a read body",
     "query q() as commit {\n  match { $p: Person }\n  return { $p.name }\n}", rfc="§5: a type error naming the missing sink")
case("6-15-insert-edge-by-reference", K, "accept", "an edge inserted between two bound nodes",
     "query w($h: String, $s: String) {\n  $p: Person { handle: $h }\n  $c: Company { slug: $s }\n  insert WorksAt { from: $p, to: $c }\n}",
     rfc="§3: an endpoint assignment is a key literal or a bound reference")
case("6-16-null-two-props", K, "accept", "two properties cleared",
     "query w($h: String) {\n  $p: Person { handle: $h }\n  update $p set { city: null, age: null }\n}", rfc="§6 freeze list: null")
case("6-17-let-in-write", K, "accept", "a let value feeding an update",
     "query w($h: String) {\n  $p: Person { handle: $h }\n  let $n = count { $p knows $q }\n  update $p set { age: $n }\n}", rfc="§3 Writes fed by reads")
case("6-18-write-with-from-optional-block", K, "accept", "a write whose clauses use from, optional and a block",
     "query w() {\n  from { match { $p worksAt $c } return { $c, count($p) as n } }\n  optional { $c worksAt $z }\n  not { $c: Company { slug: \"keep\" } }\n  $n < 2\n  delete $c\n}",
     rfc="§10 Writes: a write whose clauses use from, let, optional and a block")
case("6-19-endpoint-meta-fields", K, "refuse-with-fix:from: $h, to: $slug", "an edge insert with @src and @dst as the endpoint names",
     "query hire($h: String, $slug: String) {\n  insert WorksAt { @src: $h, @dst: $slug }\n}",
     rfc="unresolved question 7")
case("6-20-set-with-equals", K, "refuse-with-fix:age: 3", "an update assignment written with `=`",
     "query w($h: String) {\n  $p: Person { handle: $h }\n  update $p set { age = 3 }\n}", rfc="§3 Writes", habit="SQL")
case("6-21-set-qualified-prop", K, "refuse-with-fix:age: 3", "an update assignment naming `$p.age`",
     "query w($h: String) {\n  $p: Person { handle: $h }\n  update $p set { $p.age: 3 }\n}", rfc="§3 Writes")
case("6-22-returning", K, "refuse", "a write with returning, a writes-RFC form",
     "query w($h: String) {\n  $p: Person { handle: $h }\n  delete $p\n  returning { @id }\n}", rfc="§6 freeze list: returning is later")
case("6-23-delete-two-vars", K, "refuse-with-fix:delete $p  delete $q", "one delete naming two variables",
     "query w() {\n  $p knows $q\n  delete $p, $q\n}", rfc="§3 Writes")
case("6-24-two-updates", K, "accept", "two updates of one entity",
     "query w($h: String) {\n  $p: Person { handle: $h }\n  update $p set { age: 1 }\n  update $p set { city: \"x\" }\n}",
     rfc="§3 Writes", later="core spike F18: conflicting effects")
case("6-25-write-no-clause-delete", K, "accept", "a delete of an unbound variable",
     "query w() {\n  delete $p\n}", rfc="§5: delete takes a bound variable", later="type error: unbound")
case("6-26-insert-from-read", K, "accept", "an insert per row of a read",
     "query w() {\n  $p: Person  $p.city = \"Berlin\"\n  insert Doc { id: $p.handle, title: $p.name, body: \"\", year: 2026, tags: [] }\n}",
     rfc="§2 Insert sink")
case("6-27-update-where-and-as-commit", K, "refuse-with-fix:$x: Person  $x.age > 90  update $x set { city: null }",
     "an edition-2 update with null under as commit",
     "query w() as commit {\n  update Person set { city: null } where age > 90\n}", rfc="§6 rewriter row")
case("6-28-delete-where-or", K, "refuse-with-fix:$x: Person  $x.age > 90 or $x.city is null  delete $x",
     "edition 2's delete with a disjunction",
     "query w() {\n  delete Person where age > 90 or city is null\n}", rfc="§6 rewriter row")
case("6-29-null-in-read-filter", K, "refuse-with-fix:$p.city is null", "a read comparing to null",
     m("$p: Person  $p.city = null"), rfc="§6 freeze list: no null literal in a read")

# ---------------------------------------------------------------------------
# 7. Catalogue and declarations
# ---------------------------------------------------------------------------
K = 7
case("7-01-operator-catalogue", K, "accept", "the engine's operator catalogue",
     m("$o: @Operator", "$o.name, $o.inputs"), rfc="asset §7")
case("7-02-description-instruction", K, "accept", "annotations on a declaration",
     "query people_over($cutoff: I32)\n  @description(\"People over a given age\")\n  @instruction(\"Pass the age as an integer\") {\n  match { $p: Person  $p.age > $cutoff }\n  return { $p.name }\n}", rfc="asset §7")
case("7-03-unknown-annotation", K, "accept", "an unknown annotation retained as metadata",
     "query q() @owner(\"search-team\") {\n  match { $p: Person }\n  return { $p.name }\n}", rfc="§5: an unknown annotation is retained")
case("7-04-if-commit", K, "refuse", "an @if_commit precondition",
     "query retract($h: String) @if_commit(\"abc\") {\n  $p: Person { handle: $h }\n  delete $p\n}", rfc="§5: @if_commit refused, naming the writes RFC")
case("7-05-valid", K, "refuse", "an @valid interval",
     "query q() @valid(\"2026-01-01\", \"2027-01-01\") {\n  match { $p: Person }\n  return { $p.name }\n}", rfc="§5")
case("7-06-budget-rows", K, "accept", "@budget with rows and on_exceed (retained until stage N)",
     "query bounded() @budget(rows = 1000, on_exceed = refuse) {\n  match { $p: Person }\n  return { $p.handle }\n}",
     rfc="§6 freeze list", note="parser spike F13: a later stage gives this accepted text a meaning")
case("7-07-namespace-type", K, "refuse", "a namespaced type",
     m("$o: crm::Person", "$o.name"), rfc="§6 freeze list: a :: segment is a parse error")
case("7-08-commit-param-kind", K, "refuse", "a Commit parameter kind",
     "query retract($h: String, $c: Commit) {\n  $p: Person { handle: $h }\n  delete $p\n}", rfc="§6 freeze list: the Branch and Commit kinds are later")
case("7-09-param-defaults", K, "accept", "nullable and defaulted parameters of several types",
     m("$p: Person  $p.age > $a  $p.city = $c", params='$a: I32 = 18, $c: String? = "Berlin", $f: F64 = 0.5, $b: Bool = true'),
     rfc="§6 freeze list: parameters")
case("7-10-two-declarations", K, "accept", "two read declarations in one request",
     "query a() {\n  match { $p: Person }\n  return { $p.name }\n}\n\nquery b() {\n  match { $c: Company }\n  return { $c.name }\n}",
     rfc="§6 freeze list: a request may carry several declarations")
case("7-11-missing-params", K, "refuse-with-fix:query q()", "a declaration without its parameter list",
     "query q {\n  match { $p: Person }\n  return { $p.name }\n}", rfc="§6 (today's Q002)")
case("7-12-schema-in-query-grammar", K, "accept", "a schema declaration in the one grammar",
     "node Person { handle: String @key  name: String }", rfc="§7: one grammar for schema and queries; §6 freeze list",
     note="parser spike (c): schema declarations not built")
case("7-13-annotation-colon-args", K, "refuse-with-fix:@budget(rows = 1000)", "@budget written with call-style `:` options",
     "query q() @budget(rows: 1000) {\n  match { $p: Person }\n  return { $p.name }\n}", rfc="§6 freeze list; parser spike F12")
case("7-14-catalogue-schema-types", K, "accept", "the asset's later-stage schema_types",
     "query schema_types() {\n  match { $t: @Type  $p: @Property  $t @property $p }\n  return { $t.name, $p.name }\n}", rfc="asset §7")
case("7-15-view-decl", K, "refuse-with-fix:query", "a view declaration",
     "view big() {\n  match { $c: Company }\n  return { $c.name }\n}", rfc="§3 refused respellings: view → a named query")
case("7-16-type-variable-binding", K, "refuse", "a type-variable binding `$x: $t` (stage Poly)",
     m("$x: $t", "$x.@id"), rfc="§11 Poly")
case("7-17-is-type-test", K, "refuse", "a type test `$n is Person` (stage Poly)",
     m("$n: Named  $n is Person", "$n.name"), rfc="§11 Poly")
case("7-18-type-id-meta", K, "accept", "the @type_id meta-field",
     m("$p: Person", "$p.@type_id"), rfc="§6 freeze list: an unknown meta-field is a type error", later="type error")
case("7-19-explain-read", K, "accept", "explain on a read declaration",
     "explain query q() {\n  match { $p: Person }\n  return { $p.name }\n}", rfc="§3 Kept: explain")
case("7-20-explain-write", K, "accept", "explain on a write",
     "explain query w($h: String) {\n  $p: Person { handle: $h }\n  delete $p\n}", rfc="§10 Writes: explain on a write")
case("7-21-batch", K, "refuse", "a batch of two reads",
     "batch {\n  query a() { match { $p: Person } return { $p.name } }\n  query b() { match { $c: Company } return { $c.name } }\n}",
     rfc="§6: batch removed")
case("7-22-assert-sink", K, "refuse", "an assert sink (stage W1)",
     "query w($h: String) {\n  $p: Person { handle: $h }\n  assert { match { $p knows $q } return { $q } }\n  delete $p\n}", rfc="§11 W1")
case("7-23-description-on-write", K, "accept", "an annotation and as commit on a write",
     "query w($h: String) @description(\"remove a person\") as commit {\n  $p: Person { handle: $h }\n  delete $p\n}", rfc="§7 query_decl")
case("7-24-view-binding", K, "refuse", "the withdrawn view binding `$x: Name($arg)`",
     m("$x: Colleagues($h)", "$x.name", params="$h: String"), rfc="§6: the view binding removed")

# ---------------------------------------------------------------------------
# 8. Statements outside a declaration
# ---------------------------------------------------------------------------
K = 8
case("8-01-show-all", K, "accept", "show every setting", "show all;", rfc="asset §8")
case("8-02-branch-create", K, "accept", "a branch from main", "branch create feature from main", rfc="asset §8")
case("8-03-branch-merge", K, "accept", "merge a branch", "branch merge feature into main", rfc="asset §8")
case("8-04-commit-publish", K, "accept", "publish a commit", 'commit publish "01J0000000000000000000000"', rfc="asset §8")
case("8-05-load", K, "accept", "load a CSV", 'load csv "people.csv" into node:Person mode append header', rfc="asset §8")
case("8-06-setting-then-query", K, "accept", "a setting then a read",
     "set ann_nprobes = 20;\n" + m("$p: Person"), rfc="§6: the edition line before its settings lines")
case("8-07-edition-4", K, "refuse-with-fix:gq 3;", "an edition this compiler does not read",
     "gq 4;\n" + m("$p: Person"), raw="yes", rfc="§6 the edition line")
case("8-08-edition-twice", K, "refuse", "the edition line written twice",
     "gq 3;\ngq 3;\n" + m("$p: Person"), raw="yes", rfc="§6")
case("8-09-edition-no-semicolon", K, "refuse-with-fix:gq 3;", "the edition line without its semicolon",
     "gq 3\n" + m("$p: Person"), raw="yes", rfc="§6")
case("8-10-edition-uppercase", K, "refuse-with-fix:gq 3;", "the edition line in uppercase",
     "GQ 3;\n" + m("$p: Person"), raw="yes", rfc="§6")
case("8-11-setting-before-edition", K, "refuse-with-fix:gq 3;\nset ann_nprobes = 20;", "a setting written before the edition line",
     "set ann_nprobes = 20;\ngq 3;\n" + m("$p: Person"), raw="yes", rfc="§6: the edition line precedes the settings lines")
case("8-12-statement-after-query", K, "refuse", "a branch statement after a read",
     m("$p: Person") + "\nbranch list", rfc="§3 Kept: statements")
case("8-13-branch-diff", K, "accept", "a branch diff", "branch diff feature into main", rfc="asset §8")
case("8-14-explain-load", K, "accept", "explain a load", 'explain load csv "people.csv" into node:Person mode merge header', rfc="asset §8")
case("8-15-edition-2-explicit", K, "accept", "an explicit edition-2 file", "gq 2;\n" + m("$p: Person"), raw="yes", rfc="§6: gq 2; for one release")

case("1-27-brace-after-filter", 1, "refuse", "a brace block written after a filter",
     m("$p: Person  $p.age > 3 { handle: $h }", params="$h: String"), rfc="§7: a clause cannot start with `{`")
case("1-28-brace-after-bool-var", 1, "refuse", "property braces after a let Boolean",
     m("$p: Person  let $ok = $p.age > 3  $ok { handle: $h }", params="$h: String"), rfc="§7")
case("6-30-prop-named-delete-in-write", 6, "accept", "a write filtering on a property named delete",
     "query w() {\n  $p: Person  $p.delete = true\n  delete $p\n}", rfc="§7 stmt_keyword: a property named insert is legal after a dot",
     shape="$p: Person | $p.delete = true")
case("6-31-edge-named-update-in-write", 6, "accept", "a write traversing an edge named update",
     "query w() {\n  $p: Person  $p update $q\n  delete $q\n}", rfc="§7 stmt_keyword", shape="$p: Person | $p update $q")
case("6-32-filter-then-insert-call", 6, "accept", "a write whose last clause is a call named like a statement word",
     "query w() {\n  $p: Person  inserted($p.handle)\n  delete $p\n}", rfc="§7 stmt_keyword is word-bounded", shape="$p: Person | inserted($p.handle)")

case("1-29-let-absorbs-leading-minus", 1, "accept", "a let followed by a filter that begins with a negative literal",
     m("$p: Person  let $x = $p.age  -1 < $x"), rfc="§3 arithmetic; parser spike F5",
     shape="$p: Person | let $x = $p.age - 1 < $x",
     note="F5 refuses a filter that begins with `-`, but after a let the ladder takes the `-` first, so the let's value silently becomes a comparison")
case("3-19-alias-named-count-in-order", 3, "accept", "an alias named count used as an order key",
     m("$p: Person", "$p.age as count", tail="\n  order { count desc }"), rfc="§7 block_value; agg names are not reserved")
case("6-33-bare-match-in-write", 6, "refuse", "a write whose first clause is the bare word match",
     "query w() {\n  match\n  $p: Person\n  delete $p\n}", rfc="§7: the two bodies are distinct productions")

case("6-34-where-fix-captures-param", 6, "refuse-with-fix:$p: Person  $p.handle = $x  delete $p",
     "edition 2's delete whose parameter is named $x, the name the fix binds",
     "query w($x: String) {\n  delete Person where handle = $x\n}", rfc="§6 rewriter row: `$x: T  φ′  delete $x`",
     note="the fixed binding name collides with the parameter")
case("6-35-where-fix-bare-in-assignment", 6, "refuse-with-fix:$x: Person  $x.age > 3  update $x set { name: $x.handle }",
     "an update by predicate whose assignment reads another property bare",
     "query w() {\n  update Person set { name: handle } where age > 3\n}", rfc="§6 rewriter row; §3: assignments read `$x.prop`")
case("2-30-arrow-both-ends", 2, "refuse-with-fix:$a <knows> $b", "a two-headed arrow",
     m("$a: Person  $a <-knows-> $b", "$b.name"), rfc="§3 refused respellings")

case("1-30-contains-call-after-bool-filter", 1, "accept", "the contains call written after a Boolean let filter",
     m('$p: Person  let $ok = $p.age > 3  $ok  contains($p.name, "a")'), rfc="§6 rewriter row; parser spike F16",
     shape='$p: Person | let $ok = $p.age > 3 | $ok | contains($p.name, "a")')

# ---------------------------------------------------------------------------
# 9. Adversarial: other languages' habits
# ---------------------------------------------------------------------------
K = 9
A = []


def adv(name, habit, expect, task, body, **extra):
    case(name, K, expect, task, body, habit=habit, **extra)


adv("9-01-cypher-match-whole", "Cypher", "refuse", "a whole Cypher query",
    "MATCH (a:Person)-[:KNOWS]->(b) RETURN b.name")
adv("9-02-cypher-pattern-in-match", "Cypher", "refuse-with-fix:$a: Person  $a knows $b", "a Cypher pattern inside match braces",
    m("(a:Person)-[:KNOWS]->(b)", "$b.name"))
adv("9-03-cypher-bracket-edge", "Cypher", "refuse-with-fix:$a knows $b", "a Cypher bracketed edge between GQ variables",
    m("$a: Person  $a -[:knows]-> $b", "$b.name"))
adv("9-04-cypher-arrow-glued", "Cypher", "refuse-with-fix:$a knows $b", "an arrow with no spaces",
    m("$a: Person  $a-knows->$b", "$b.name"), rfc="§3 refused respellings")
adv("9-05-cypher-where-upper", "Cypher", "refuse-with-fix:$p.age > 30", "an uppercase WHERE inside match",
    m("$p: Person WHERE $p.age > 30"))
adv("9-06-where-lower", "SQL", "refuse-with-fix:$p.age > 30", "a lowercase where inside match",
    m("$p: Person  where $p.age > 30"), note="`where` is a legal bare name in a filter")
adv("9-07-order-by", "SQL", "refuse-with-fix:order { $p.age desc }", "ORDER BY after return",
    m("$p: Person", tail="\n  order by $p.age desc"))
adv("9-08-desc-upper", "SQL", "refuse-with-fix:desc", "DESC in uppercase", m("$p: Person", tail="\n  order { $p.age DESC }"))
adv("9-09-limit-offset", "SQL", "refuse", "limit then offset (paging is stage P)", m("$p: Person", tail="\n  limit 10 offset 5"),
    rfc="§11 P")
adv("9-10-cypher-skip-limit", "Cypher", "refuse", "skip then limit", m("$p: Person", tail="\n  skip 5 limit 10"))
adv("9-11-count-star", "Cypher", "refuse-with-fix:count($p)", "count(*)", m("$p: Person", "count(*) as n"))
adv("9-12-cypher-with", "Cypher", "refuse-with-fix:from { match { $p worksAt $c } return { $c, count($p) as n } }",
    "a WITH clause carrying an aggregate",
    "query q() {\n  match { $p worksAt $c  with $c, count($p) as n  $n > 3 }\n  return { $c.name }\n}")
adv("9-13-unwind", "Cypher", "refuse-with-fix:$x in $list", "unwind a list", m("unwind $list as $x  $p: Person { city: $x }", params="$list: [String]"),
    rfc="§3 refused respellings")
adv("9-14-for-in-list", "GQL", "refuse-with-fix:$x in $list", "for over a list", m("for $x in $list  $p: Person { city: $x }", params="$list: [String]"),
    rfc="§3 refused respellings")
adv("9-15-for-in-relation", "GQL", "refuse-with-fix:from { match { $p: Person } return { $p } }", "for over a relation",
    m("for $x in { match { $p: Person } return { $p } }", "$x.name"), rfc="§3 refused respellings: for $x in rel")
adv("9-16-let-in-relation", "GQ edition 2", "refuse-with-fix:from { match { $p: Person } return { $p } }", "PR 1244's let … in",
    m("let $x in { match { $p: Person } return { $p } }", "$x.name"), rfc="§3 refused respellings")
adv("9-17-let-tuple-in", "GQ edition 2", "refuse-with-fix:from { match { $p worksAt $c } return { $p, $c } }", "positional destructuring",
    m("let ($a, $b) in { match { $p worksAt $c } return { $p, $c } }", "$a.name"), rfc="§3 refused respellings")
adv("9-18-case-upper", "SQL", 'refuse-with-fix:if($p.age > 65, "senior", "active")', "a CASE expression",
    m("$p: Person", '$p.name, CASE WHEN $p.age > 65 THEN "senior" ELSE "active" END as band'), rfc="§3 refused respellings")
adv("9-19-case-lower", "Cypher", 'refuse-with-fix:if($p.age > 65, "senior", "active")', "a lowercase case expression",
    m("$p: Person", '$p.name, case when $p.age > 65 then "senior" else "active" end as band'), rfc="§3 refused respellings")
adv("9-20-concat-bars", "SQL", 'refuse-with-fix:concat($p.name, " ", $p.handle)', "string concatenation with ||",
    m("$p: Person", '$p.name || " " || $p.handle as label'), rfc="§3 refused respellings")
adv("9-21-coalesce-upper", "SQL", 'refuse-with-fix:coalesce($p.city, "none")', "COALESCE in uppercase",
    m("$p: Person", 'COALESCE($p.city, "none") as c'), rfc="§3 refused respellings: coalesce as a keyword")
adv("9-22-regex-match", "Cypher", "refuse", "a regular-expression match", m('$p: Person  $p.name =~ "A.*"'))
adv("9-23-starts-with-words", "Cypher", 'refuse-with-fix:starts_with($p.name, "A")', "STARTS WITH", m('$p: Person  $p.name STARTS WITH "A"'))
adv("9-24-infix-starts-with", "GQ edition 2", 'refuse-with-fix:starts_with($p.name, "A")', "edition 2's infix starts_with",
    m('$p: Person  $p.name starts_with "A"'), rfc="§3 refused respellings")
adv("9-25-infix-contains-list", "GQ edition 2", 'refuse-with-fix:"rust" in $d.tags', "edition 2's list contains",
    m('$d: Doc  $d.tags contains "rust"', "$d.title"), rfc="§6 rewriter row: list contains → in")
adv("9-26-infix-contains-string", "GQ edition 2", 'refuse-with-fix:contains($d.title, "graph")', "edition 2's string contains",
    m('$d: Doc  $d.title contains "graph"', "$d.title"), rfc="§6 rewriter row")
adv("9-27-infix-contains-in-or", "GQ edition 2", 'refuse-with-fix:contains($d.title, "graph")', "infix contains inside an or",
    m('$d: Doc  $d.year > 2020 or $d.title contains "graph"', "$d.title"), rfc="§6 rewriter row")
adv("9-28-cypher-create", "Cypher", "refuse-with-fix:insert Person { handle: \"x\", name: \"x\" }", "CREATE",
    "query w() {\n  CREATE (p:Person {handle: \"x\", name: \"x\"})\n}")
adv("9-29-cypher-merge", "Cypher", "refuse-with-fix:insert Person { handle: $h, name: $h }", "MERGE (upsert)",
    "query w($h: String) {\n  merge Person { handle: $h, name: $h }\n}", note="keyed insert is upsert")
adv("9-30-cypher-set", "Cypher", "refuse-with-fix:update $p set { age: 3 }", "SET a property",
    "query w($h: String) {\n  $p: Person { handle: $h }\n  set $p.age = 3\n}")
adv("9-31-delete-upper", "Cypher", "refuse-with-fix:delete $p", "DELETE in uppercase",
    "query w($h: String) {\n  $p: Person { handle: $h }\n  DELETE $p\n}")
adv("9-32-detach-delete", "Cypher", "refuse-with-fix:delete $p", "DETACH DELETE in lowercase",
    "query w($h: String) {\n  $p: Person { handle: $h }\n  detach delete $p\n}", note="`detach` is a legal bare-name filter")
adv("9-33-optional-match", "Cypher", "refuse-with-fix:optional { $p worksAt $c }", "OPTIONAL MATCH as two words",
    m("$p: Person  optional match { $p worksAt $c }", "$p.name, $c.name"))
adv("9-34-call-subquery", "Cypher", "refuse-with-fix:from { match { $p knows $q } return { $q } }", "CALL { } as a subquery",
    m("$p: Person  call { match { $p knows $q } return { $q } }", "$q.name"))
adv("9-35-exists-cypher-braces", "Cypher", "refuse-with-fix:exists { $p knows $q }", "EXISTS with a Cypher pattern inside",
    m("$p: Person  exists { MATCH (p)-[:KNOWS]->(q) }"))
adv("9-36-exists-gq-pattern", "Cypher", "accept", "exists with a GQ pattern, which Cypher also spells with braces",
    m("$p: Person  exists { $p knows $q }"))
adv("9-37-exists-function", "Cypher", "accept", "exists() as a property test",
    m("$p: Person  exists($p.city)"), later="unknown function; the fix is `$p.city is not null`")
adv("9-38-is-not-null-upper", "SQL", "refuse-with-fix:$p.city is not null", "IS NOT NULL in uppercase", m("$p: Person  $p.city IS NOT NULL"))
adv("9-39-not-is-null", "SQL", "accept", "not … is null", m("$p: Person  not $p.city is null"))
adv("9-40-neq-null", "SQL", "refuse-with-fix:$p.city is not null", "!= null", m("$p: Person  $p.city != null"))
adv("9-41-is-not-null-function", "SQL", "accept", "isnotnull() as a function", m("$p: Person  is_not_null($p.city)"),
    later="unknown function")
adv("9-42-sql-neq", "SQL", 'refuse-with-fix:$p.city != "Berlin"', "<> for inequality", m('$p: Person  $p.city <> "Berlin"'))
adv("9-43-double-equals", "Gremlin", 'refuse-with-fix:$p.city = "Berlin"', "== for equality", m('$p: Person  $p.city == "Berlin"'))
adv("9-44-single-quotes", "SQL", 'refuse-with-fix:"Berlin"', "a single-quoted string", m("$p: Person  $p.city = 'Berlin'"))
adv("9-45-backtick-ident", "Cypher", "refuse", "a backtick-quoted property", m("$p: Person", "$p.`full name`"))
adv("9-46-sql-select", "SQL", "refuse", "a SQL SELECT", "SELECT p.name FROM Person p WHERE p.age > 30")
adv("9-47-group-by", "SQL", "refuse-with-fix:<empty>", "GROUP BY after return",
    "query q() {\n  match { $p worksAt $c }\n  return { $c.name, count($p) as n }\n  group by $c.name\n}", note="the keys are implicit")
adv("9-48-having-sql", "SQL", "refuse", "HAVING after return",
    "query q() {\n  match { $p worksAt $c }\n  return { $c.name, count($p) as n }\n  having n > 3\n}", rfc="§3 refused respellings")
adv("9-49-having-block", "GQ edition 2", "refuse", "the withdrawn having { } clause",
    "query q() {\n  match { $p worksAt $c }\n  return { $c.name, count($p) as n }\n  having { n > 3 }\n}", rfc="§3 refused respellings")
adv("9-50-retrieve", "GQ edition 2", "refuse", "the withdrawn retrieve { } clause",
    "query q($v: Vector(3)) {\n  match { $d: Doc }\n  retrieve { nearest($d.embedding, $v, k: 10) }\n  return { $d.title }\n}", rfc="§3 refused respellings")
adv("9-51-like", "SQL", 'refuse-with-fix:contains($d.title, "graph")', "LIKE with wildcards",
    m("$d: Doc  $d.title LIKE '%graph%'", "$d.title"))
adv("9-52-between", "SQL", "refuse-with-fix:$p.age >= 18 and $p.age <= 65", "BETWEEN", m("$p: Person  $p.age BETWEEN 18 AND 65"))
adv("9-53-order-by-inside", "SQL", "refuse-with-fix:order { $p.age desc }", "ORDER BY in uppercase",
    m("$p: Person", tail="\n  ORDER BY $p.age DESC"))
adv("9-54-limit-upper", "SQL", "refuse-with-fix:limit 10", "LIMIT in uppercase", m("$p: Person", tail="\n  LIMIT 10"))
adv("9-55-as-upper", "SQL", "refuse-with-fix:as n", "AS in uppercase", m("$p: Person", "$p.name AS n"))
adv("9-56-in-parens", "SQL", "refuse-with-fix:[30, 40]", "IN with a parenthesised list", m("$p: Person  $p.age in (30, 40)"))
adv("9-57-not-in", "SQL", 'refuse-with-fix:not $p.city in ["Berlin"]', "NOT IN", m('$p: Person  $p.city not in ["Berlin"]'))
adv("9-58-graphql-query", "GraphQL", "refuse", "a GraphQL selection set", 'query { person(handle: "x") { name } }')
adv("9-59-graphql-mutation", "GraphQL", "refuse-with-fix:query addPerson($h: String) {\n  insert Person { handle: $h, name: $h }\n}",
    "a GraphQL mutation", "mutation addPerson($h: String) {\n  insert Person { handle: $h, name: $h }\n}", rfc="§3 refused respellings")
adv("9-60-graphql-alias", "GraphQL", "refuse-with-fix:$p.name as n", "a GraphQL alias in return", m("$p: Person", "n: $p.name"))
adv("9-61-graphql-fragment", "GraphQL", "refuse", "an inline fragment", m("$n: Named", "... on Person { handle }"))
adv("9-62-gremlin-traversal", "Gremlin", "refuse", "a Gremlin traversal", "g.V().hasLabel('Person').out('knows').values('name')")
adv("9-63-gremlin-method", "Gremlin", "refuse-with-fix:$p knows $q", "a method-call step", m("$p: Person  $p.out(knows)", "$p.name"))
adv("9-64-sparql-vars", "SPARQL", "refuse-with-fix:$p knows $q", "SPARQL variables", m("?p knows ?q", "?q"))
adv("9-65-sparql-select", "SPARQL", "refuse", "a SPARQL SELECT", "SELECT ?x WHERE { ?x :knows ?y }")
adv("9-66-sparql-filter", "SPARQL", "accept", "filter() as a call", m("$p: Person  filter($p.age > 3)"),
    later="unknown function filter; the fix is the bare filter clause")
adv("9-67-datalog-atom", "Datalog", "accept", "an edge as a Datalog atom", m("$p: Person  knows($p, $q)"),
    later="unknown function knows; the fix is the triple `$p knows $q`")
adv("9-68-datalog-rule", "Datalog", "refuse", "a Datalog rule", "reach(X, Y) :- knows(X, Y).")
adv("9-69-datalog-negation", "Datalog", "refuse-with-fix:not { $p knows $q }", "negation with !",
    m("$p: Person  $q: Person  !knows($p, $q)"))
adv("9-70-and-and", "Gremlin", "refuse-with-fix:and", "&& for conjunction", m("$p: Person  $p.age >= 18 && $p.age < 65"))
adv("9-71-bang-not", "Gremlin", "refuse-with-fix:not ($p.age > 3)", "! for negation", m("$p: Person  !($p.age > 3)"))
adv("9-72-brace-comparison", "Cypher", "refuse-with-fix:$p: Person  $p.age > 30", "a comparison inside binding braces",
    m("$p: Person { age > 30 }"), rfc="§3 refused respellings")
adv("9-73-brace-comparison-two", "Cypher", 'refuse-with-fix:$p: Person { city: "Berlin" }  $p.age > 30',
    "an equality and a comparison inside binding braces", m('$p: Person { city: "Berlin", age > 30 }'), rfc="§3 refused respellings")
adv("9-74-brace-colon-comparison", "GraphQL", "refuse-with-fix:$p: Person  $p.age > 30", "`age: > 30` inside braces",
    m("$p: Person { age: > 30 }"))
adv("9-75-cypher-parens-binding", "Cypher", "refuse-with-fix:$p: Person { handle: $h }", "a binding with parentheses",
    m("$p: Person(handle: $h)", params="$h: String"))
adv("9-76-lowercase-type", "Cypher", "refuse-with-fix:$p: Person", "a lowercase type name", m("$p: person"))
adv("9-77-uppercase-variable", "Cypher", "refuse-with-fix:$p", "an uppercase variable", m("$P: Person", "$P.name"))
adv("9-78-type-dot-property", "SQL", "refuse-with-fix:$p.name", "property access on a type", m("$p: Person", "Person.name"))
adv("9-79-chained-path", "GraphQL", "refuse-with-fix:$p knows $f", "a chained property path through an edge", m("$p: Person", "$p.knows.name"))
adv("9-80-star-projection", "SQL", "refuse", "a star projection", m("$p: Person", "$p.*"))
adv("9-81-return-no-braces", "Cypher", "refuse-with-fix:return { $p.name }", "return without braces",
    "query q() {\n  match { $p: Person }\n  return $p.name\n}")
adv("9-82-match-no-braces", "Cypher", "refuse-with-fix:match { $p: Person }", "match without braces",
    "query q() {\n  match $p: Person\n  return { $p.name }\n}")
adv("9-83-missing-return", "Datalog", "refuse-with-fix:return { $p.name }", "a read with no return",
    "query q() {\n  match { $p: Person }\n}")
adv("9-84-return-before-match", "SQL", "refuse", "return written before match",
    "query q() {\n  return { $p.name }\n  match { $p: Person }\n}")
adv("9-85-limit-before-order", "SQL", "refuse", "limit written before order",
    m("$p: Person", tail="\n  limit 10\n  order { $p.age }"))
adv("9-86-limit-negative", "SQL", "refuse", "a negative limit", m("$p: Person", tail="\n  limit -1"))
adv("9-87-return-distinct-inside", "SQL", "refuse-with-fix:return distinct { $p.city }", "DISTINCT inside the return braces",
    m("$p: Person", "distinct $p.city"), note="`distinct` is a legal bare name")
adv("9-88-select-distinct-upper", "SQL", "refuse-with-fix:return distinct { $p.city }", "RETURN DISTINCT in uppercase",
    "query q() {\n  match { $p: Person }\n  RETURN DISTINCT { $p.city }\n}")
adv("9-89-alias-string", "SQL", "refuse-with-fix:as name", "an alias written as a string", m("$p: Person", '$p.name as "Name"'))
adv("9-90-alias-reserved", "SQL", "refuse", "an alias named in", m("$p: Person", "$p.name as in"))
adv("9-91-param-untyped", "Cypher", "refuse-with-fix:$h: String", "a parameter without a type",
    "query q($h) {\n  match { $p: Person { handle: $h } }\n  return { $p.name }\n}")
adv("9-92-numbered-param", "Cypher", "refuse", "a numbered parameter", m("$p: Person { handle: $1 }"))
adv("9-93-sql-comment", "SQL", "refuse-with-fix://", "a SQL line comment", m("$p: Person  -- adults only\n  $p.age > 18"))
adv("9-94-hash-comment", "Gremlin", "refuse-with-fix://", "a hash comment", m("$p: Person  # adults only\n  $p.age > 18"))
adv("9-95-trailing-semicolon", "SQL", "refuse-with-fix:<empty>", "a semicolon after a declaration", m("$p: Person") + ";")
adv("9-96-chained-comparison", "Datalog", "refuse-with-fix:18 <= $p.age and $p.age < 65", "a chained comparison",
    m("$p: Person  18 <= $p.age < 65"))
adv("9-97-batch", "GQ edition 2", "refuse", "the withdrawn batch",
    "batch { a() b() }")
adv("9-98-order-nearest-gq", "GQ edition 2", "accept", "order { nearest(…) } with k", m("$d: Doc", "$d.title",
    tail="\n  order { nearest($d.embedding, $q, k: 10) }", params="$q: Vector(3)"), later="T65")
adv("9-99-at-src-filter", "GQ edition 2", "accept", "an edge meta-field filter in a read",
    m("$p $k:worksAt $c  $k.@src = $h", "$c.name", params="$h: String"))
adv("9-100-bare-at-src", "GQ edition 2", "accept", "a bare @src in a read filter (edition 2's where habit)",
    m("$p $k:worksAt $c  @src = $h", "$c.name", params="$h: String"), later="type error: a bare meta-field in a read; fix `$k.@src = $h`")
adv("9-101-arrow-reversed-cypher", "Cypher", "refuse-with-fix:$b knows $a", "a left arrow with brackets",
    m("$a: Person  $a <-[:knows]- $b", "$b.name"))
adv("9-102-mutation-read", "GraphQL", "refuse-with-fix:query q() {\n  match { $p: Person }\n  return { $p.name }\n}",
    "a read declared with mutation", "mutation q() {\n  match { $p: Person }\n  return { $p.name }\n}")
adv("9-103-insert-cypher-props", "Cypher", 'refuse-with-fix:insert Person { handle: "x", name: "x" }', "insert with parentheses",
    'query w() {\n  insert Person ({ handle: "x", name: "x" })\n}')
adv("9-104-update-without-set", "SQL", "refuse-with-fix:update $p set { age: 3 }", "update without set",
    "query w($h: String) {\n  $p: Person { handle: $h }\n  update $p { age: 3 }\n}")
adv("9-105-delete-type", "GQ edition 2", "refuse-with-fix:$x: Person  delete $x", "delete a whole type without where",
    "query w() {\n  delete Person\n}")
adv("9-106-sql-as-table-alias", "SQL", "refuse", "a SQL table alias in a binding", m("$p: Person AS p"))
adv("9-107-cypher-count-distinct-upper", "Cypher", "refuse-with-fix:count(distinct $p.city)", "COUNT(DISTINCT x) uppercase",
    m("$p: Person", "COUNT(DISTINCT $p.city) as n"))
adv("9-108-distinct-call", "SQL", "accept", "distinct(x) as a function call in return", m("$p: Person", "distinct($p.city)"),
    later="unknown function distinct; the fix is return distinct")
adv("9-109-null-literal-return", "SQL", "refuse", "a null literal in a return", m("$p: Person", "null as nothing"),
    rfc="§6 freeze list: no null literal in a read")
adv("9-110-order-desc-first", "SQL", "refuse-with-fix:$p.age desc", "the direction before the key", m("$p: Person", tail="\n  order { desc $p.age }"))

adv("9-111-optional-no-braces", "SPARQL", "refuse-with-fix:optional { $p worksAt $c }", "OPTIONAL without a block",
    m("$p: Person  optional $p worksAt $c", "$p.name, $c.name"))
adv("9-112-not-no-braces", "Datalog", "refuse-with-fix:not { $p knows $q }", "negation of a triple without a block",
    m("$p: Person  $q: Person  not $p knows $q", "$p.name"))
adv("9-113-exists-no-braces", "SQL", "refuse-with-fix:exists { $p knows $q }", "EXISTS over a triple without a block",
    m("$p: Person  exists $p knows $q", "$p.name"))
adv("9-114-cypher-where-not-pattern", "Cypher", "refuse-with-fix:not { $p knows $q }", "WHERE NOT over a pattern",
    m("$p: Person  $q: Person  where not $p knows $q", "$p.name"))
adv("9-115-sql-select-alias-eq", "SQL", "refuse-with-fix:$p.name as n", "an alias written `n = expr`",
    m("$p: Person", "n = $p.name"))
adv("9-116-cypher-return-star", "Cypher", "refuse", "return *", m("$p: Person", "*"))
adv("9-117-gql-filter-keyword", "GQL", "refuse-with-fix:$p.age > 30", "GQL's FILTER statement", m("$p: Person  FILTER $p.age > 30"))
adv("9-118-gql-let-keyword-upper", "GQL", "refuse-with-fix:let $n = $p.age + 1", "GQL's uppercase LET", m("$p: Person  LET $n = $p.age + 1", "$n"))
adv("9-119-kusto-pipe", "SQL", "refuse", "a pipeline operator", m("$p: Person", "$p.name", tail="\n  | limit 10"))
adv("9-120-sparql-optional-braces-upper", "SPARQL", "refuse-with-fix:optional { $p worksAt $c }", "OPTIONAL in uppercase with braces",
    m("$p: Person  OPTIONAL { $p worksAt $c }", "$p.name"))

# ---------------------------------------------------------------------------
# Contextual keywords in every position (kinds 1 to 4)
# ---------------------------------------------------------------------------
KW = ["from", "let", "union", "insert", "update", "delete", "in", "as", "distinct", "asc", "desc", "optional", "exists", "not"]
RESERVED = {"in", "not"}
for i, w in enumerate(KW, 1):
    # as a property after a dot (legal for every word, the reserved included)
    case(f"1-kw{i:02d}-prop-{w}", 1, "accept", f"`{w}` as a property after a dot",
         m(f"$p: Person  $p.{w} = 1", f"$p.{w}"), rfc="§7 contextual keywords; §6 reserved words",
         shape=f"$p: Person | $p.{w} = 1")
    # as an alias, and as an order key naming the alias
    exp = "refuse" if w in RESERVED else "accept"
    case(f"3-kw{i:02d}-alias-{w}", 3, exp, f"`{w}` as a return alias, then ordered by",
         m("$p: Person", f"$p.age as {w}", tail=f"\n  order {{ {w} desc }}"), rfc="§7 contextual keywords; §6 reserved words")
    # as a parameter name and a let name
    case(f"4-kw{i:02d}-param-let-{w}", 4, "accept", f"`${w}` as a parameter and `$_{w}` as a let name",
         m(f"$p: Person  let $_{w} = ${w} + 1  $p.age > $_{w}", params=f"${w}: I32"), rfc="§7: variables carry `$`")
    # as a binding key
    case(f"1-kw{i:02d}-binding-key-{w}", 1, "accept", f"`{w}` as a key inside binding braces",
         m(f"$p: Person {{ {w}: 1 }}"), rfc="§7 contextual keywords", shape=f"$p: Person {{ {w}: 1 }}")
    # as an update assignment
    case(f"6-kw{i:02d}-assign-{w}", 6, "accept", f"`{w}` as an update assignment name",
         f"query w() {{\n  $p: Person\n  update $p set {{ {w}: 1 }}\n}}", rfc="§7: the three statement words stay unreserved")

# ---------------------------------------------------------------------------
# One-spelling pairs: two accepted texts the RFC says mean the same thing.
# covered: fmt (section 6's formatter list), a6 (A6's literal-and-reference
# exception), none (neither).
# ---------------------------------------------------------------------------
PAIRS = []


def pair(name, covered, desc, a, b, rfc=""):
    PAIRS.append(dict(name=name, covered=covered, desc=desc, a=a.strip("\n"), b=b.strip("\n"), rfc=rfc))


V = "$q: Vector(3)"
pair("p01-braces-vs-filter", "fmt", "a property equality in the braces or as a filter clause",
     m("$p: Person { handle: $h }", params="$h: String"), m("$p: Person  $p.handle = $h", params="$h: String"), "§6 queries fmt")
pair("p02-exists-vs-count-gt-0", "fmt", "exists { p } against count { p } > 0",
     m("$p: Person  exists { $p knows $q }"), m("$p: Person  count { $p knows $q } > 0"), "§2 derived forms; §6 fmt")
pair("p03-not-vs-count-eq-0", "fmt", "not { p } against count { p } = 0",
     m("$p: Person  not { $p knows $q }"), m("$p: Person  count { $p knows $q } = 0"), "§6 fmt; core spike F14")
pair("p04-triple-reversed", "fmt", "a triple in schema order and reversed",
     m("$p worksAt $c"), m("$c worksAt $p"), "§6 fmt")
pair("p05-union-nested", "none", "union(a, b, c) against union(union(a, b), c)",
     m("from union({ match { $p: Person } return { $p } }, { match { $p: Person } return { $p } }, { match { $p: Person } return { $p } })"),
     m("from union(union({ match { $p: Person } return { $p } }, { match { $p: Person } return { $p } }), { match { $p: Person } return { $p } })"),
     "§3 union folded from the left; core spike F14")
pair("p06-let-relation-vs-inline", "a6", "let $r = rel then from $r against from rel",
     m("let $r = { match { $p: Person } return { $p } }  from $r"), m("from { match { $p: Person } return { $p } }"), "A6")
pair("p07-clause-order", "none", "a binding before or after the traversal that uses it",
     m("$p: Person  $p knows $q"), m("$p knows $q  $p: Person"), "§2 derived forms: composed in dependency order; core spike F17")
pair("p08-asc-explicit", "none", "order with asc written or omitted",
     m("$p: Person", tail="\n  order { $p.age asc }"), m("$p: Person", tail="\n  order { $p.age }"), "§3 Kept: order")
pair("p09-not-exists-filter-vs-not-block", "none", "not { p } against the filter not exists { p }",
     m("$p: Person  not { $p knows $q }"), m("$p: Person  not exists { $p knows $q }"), "§2 derived forms: not { p } is Filter(¬ exists { p })")
pair("p10-exists-vs-count-ge-1", "none", "exists { p } against count { p } >= 1",
     m("$p: Person  exists { $p knows $q }"), m("$p: Person  count { $p knows $q } >= 1"), "§6 fmt lists only `> 0`")
pair("p11-two-filters-vs-and", "none", "two filter clauses against one conjunction",
     m("$p: Person  $p.age >= 18  $p.age < 65"), m("$p: Person  $p.age >= 18 and $p.age < 65"), "§2 Filter")
pair("p12-alias-equal-to-name", "none", "an inner return item aliased to its own name",
     m("from { match { $p: Person } return { $p } }"), m("from { match { $p: Person } return { $p as p } }"), "§3 from: columns by name")
pair("p13-single-hop-bounds", "none", "a single hop written bare or as {1,1}",
     m("$p knows $q"), m("$p knows{1,1} $q"), "§2 Expand: the single hop is m = n = 1")
pair("p14-undirected-swapped", "none", "an undirected triple with its ends swapped",
     m("$p: Person  $p <knows> $q"), m("$p: Person  $q <knows> $p"), "§2 derived forms: <e> is the union of the orientations")
pair("p15-alternation-order", "none", "an alternation in two orders",
     m("$p (knows | worksAt) $q"), m("$p (worksAt | knows) $q"), "§2 derived forms: (e1 | e2) is a Union")
pair("p16-options-order", "none", "nearest's options in two orders",
     m("from nearest({ match { $d: Doc } return { $d } }, embedding, $q, k: 20, recall: 0.9)", "$d.title", params=V),
     m("from nearest({ match { $d: Doc } return { $d } }, embedding, $q, recall: 0.9, k: 20)", "$d.title", params=V), "§3 relation expressions")
pair("p17-bm25-string-vs-terms-any", "none", "bm25 over a String against bm25 over terms(q, mode: any)",
     m("from bm25({ match { $d: Doc } return { $d } }, body, $q)", "$d.title", params="$q: String"),
     m("from bm25({ match { $d: Doc } return { $d } }, body, terms($q, mode: any))", "$d.title", params="$q: String"),
     "§4: a String means terms(q, mode: any)")
pair("p18-terms-defaults", "none", "terms with its defaults omitted or written",
     m("$d: Doc  match_terms($d.body, terms($q))", "$d.title", params="$q: String"),
     m("$d: Doc  match_terms($d.body, terms($q, mode: all, max_edits: 0))", "$d.title", params="$q: String"), "§4 the lexical query")
pair("p19-rrf-default-weights", "none", "rrf with weights omitted or written as their default",
     m("let $docs = { match { $d: Doc } return { $d } }  from rrf(nearest($docs, embedding, $v, k: 50), bm25($docs, body, $s), k: 60)", "$d.title", params="$s: String, $v: Vector(3)"),
     m("let $docs = { match { $d: Doc } return { $d } }  from rrf(nearest($docs, embedding, $v, k: 50), bm25($docs, body, $s), k: 60, weights: [1.0, 1.0])", "$d.title", params="$s: String, $v: Vector(3)"),
     "§4 Fusion: weights default 1.0")
pair("p20-limit-literal-vs-default-param", "a6", "limit 10 against limit $k with $k = 10",
     m("$p: Person", tail="\n  limit 10"), m("$p: Person", tail="\n  limit $k", params="$k: I64 = 10"), "A6")
pair("p21-commas-vs-whitespace", "same-tree", "return items with commas or with whitespace",
     m("$p: Person", "$p.name, $p.city"), m("$p: Person", "$p.name  $p.city"), "§3 Kept")
pair("p22-parens", "same-tree", "redundant parentheses",
     m("$p: Person  $p.age > 3"), m("$p: Person  ($p.age > 3)"), "§7 operand")
pair("p23-optional-vs-union", "n/a", "optional { p } against its derived union: not a pair, the null padding has no read spelling",
     m("$p: Person  optional { $p worksAt $c }", "$p.name"),
     m("from union({ match { $p: Person  $p worksAt $c } return { $p, $c } }, { match { $p: Person  not { $p worksAt $c } } return { $p, $c } })", "$p.name"),
     "§2 derived forms: optional", )
pair("p24-statement-order", "none", "two write statements in two orders",
     "query w($h: String) {\n  $p: Person { handle: $h }\n  $p $k:worksAt $c\n  delete $k\n  update $p set { city: null }\n}",
     "query w($h: String) {\n  $p: Person { handle: $h }\n  $p $k:worksAt $c\n  update $p set { city: null }\n  delete $k\n}",
     "§3 Writes: every sink sees one snapshot")
pair("p25-block-filter-vs-let", "none", "a block compared inline against a let then a filter (rows equal; core terms differ by a Project)",
     m("$p: Person  count { $p knows $q } > 2"), m("$p: Person  let $n = count { $p knows $q }  $n > 2"), "§2 derived forms: let is a Project")
pair("p26-from-call-vs-inline-body", "none", "a declared read called, against its body written inline",
     "query friends($h: String) {\n  match { $p: Person { handle: $h }  $p knows $q }\n  return { $q }\n}\n\nquery q($h: String) {\n  match { from friends($h) }\n  return { $q.name }\n}",
     "query friends($h: String) {\n  match { $p: Person { handle: $h }  $p knows $q }\n  return { $q }\n}\n\nquery q($h: String) {\n  match { from { match { $p: Person { handle: $h }  $p knows $q } return { $q } } }\n  return { $q.name }\n}",
     "§2 Call is inlined")

case("1-31-bare-property-in-filter", 1, 'refuse-with-fix:$p.name = "x"',
     "a filter on a bare property name, edition 2's mutation habit in a read",
     m('$p: Person  name = "x"'), rfc="§7: a bare identifier is not an operand in match (2026-11-08)",
     amended="added by spike/gq3-grammar2: the bare-name refusal (Q018) and its guess")
case("1-32-bare-parameter-name", 1, "refuse-with-fix:$h",
     "a parameter written without its `$`",
     m("$p: Person { handle: h }", params="$h: String"), rfc="§7: a bare identifier is not an operand",
     amended="added by spike/gq3-grammar2: the bare-name refusal (Q018) names the parameter")


# ---------------------------------------------------------------------------
# Amendments of 2026-11-08 (spike/gq3-grammar2). The RFC adopted decisions
# that change what some cases expect; each amended case says so in its
# `amended` header, and docs/spikes/gq3-grammar2.md lists them. The cases
# above are kept as the red team wrote them.
# ---------------------------------------------------------------------------

RANKED = "the ranked column is a property access and `l2` is `distance` (RFC §3, 2026-11-08)"


def ranked_column(body):
    """The RFC's 2026-11-08 spelling of a ranking call: `$d.embedding`, not `embedding`."""
    out = body.replace("l2(", "distance(")
    out = re.sub(r"(\}|\$\w+|\d),(\s*)(embedding|body|title),", r"\1,\2$d.\3,", out)
    out = out.replace("},\n      text, $q)", "},\n      $p.text, $q)")
    return out


AMENDMENTS = {
    "1-11-minus-literal-glued": dict(
        expect="refuse-with-fix:$n - 1 > 0", shape=None,
        amended="`$n -1` is refused with `$n - 1` as the fix (RFC §3, 2026-11-08)"),
    "1-13-newline-items-leading-minus": dict(
        expect="refuse-with-fix:$p.age, -$p.age", returns=None,
        amended="`X  -Y` is refused (RFC §3, 2026-11-08); the intended text is two items"),
    "1-22-leading-minus-filter": dict(
        expect="refuse-with-fix:$p.age  (-1) < $x",
        amended="`$p.age  -1` is refused (RFC §3, 2026-11-08); the intended text is a let, then the filter"),
    "1-29-let-absorbs-leading-minus": dict(
        expect="refuse-with-fix:$p.age  (-1) < $x", shape=None,
        amended="`$p.age  -1` is refused (RFC §3, 2026-11-08); the intended text is a let, then the filter"),
    "2-09-edge-named-from": dict(
        expect='refuse-with-fix:$a "from" $b', shape=None,
        amended="`from` is never a bare edge name; the quoted edge is the fix (RFC §7, 2026-11-08)"),
    "2-10-edge-named-let": dict(
        expect='refuse-with-fix:$a "let" $b', shape=None,
        amended="`let` is never a bare edge name; the quoted edge is the fix (RFC §7, 2026-11-08)"),
    "2-16-edge-named-insert-in-write": dict(
        expect='refuse-with-fix:$a "insert" $b', shape=None,
        amended="grammar2 G8: a statement word is never a bare edge name"),
    "6-31-edge-named-update-in-write": dict(
        expect='refuse-with-fix:$p "update" $q', shape=None,
        amended="grammar2 G8: a statement word is never a bare edge name"),
    "2-27-open-upper-bound": dict(
        expect="accept", later="T15 (RFC §5, 2026-11-08: the type-level door with a pinned test)",
        amended="an open bound is a later form whose door is the type checker's T15 (RFC §5, 2026-11-08)"),
    "4-04-asset-colleagues-of-colleagues": dict(
        body="query colleagues($of: String) {\n  match { $p: Person { handle: $of }  $p worksAt $c  $q worksAt $c  $q != $p }\n  return { $q }\n}\n\nquery colleagues_of_colleagues($of: String) {\n  match {\n    from { match { from colleagues($of) } return { $q as c } }\n    from { match { from colleagues($c.handle) } return { $q as r } }\n  }\n  return distinct { $r.name }\n}",
        note=None, amended="the asset's text of 2026-11-08 (the inner reads open with match)"),
    "5-10-asset-weighted": dict(
        body="query weighted($q: String, $v: Vector(3)) {\n  match {\n    let $docs = { match { $d: Doc } return { $d } }\n    from { match { from distance($docs, $d.embedding, $v) } return { $d, $distance as vec } }\n    from { match { from bm25($docs, $d.body, $q) } return { $d as d2, $score as lex } }\n    $d = $d2\n  }\n  return { $d.title, 0.7 * (1.0 / (1.0 + $vec)) + 0.3 * $lex as s }\n  order { s desc }\n  limit 10\n}",
        note=None, amended="the asset's text of 2026-11-08"),
    "4-20-from-call-space-before-paren": dict(
        expect="refuse-with-fix:from colleagues($h)", shape=None,
        amended="a space before a call's `(` is refused with the touching form (RFC §3, 2026-11-08)"),
    "4-21-from-nearest-space-before-paren": dict(
        expect="refuse-with-fix:nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 5)", shape=None,
        amended="a space before a call's `(` is refused with the touching form (RFC §3, 2026-11-08)"),
    "5-23-field-as-prop-access": dict(
        later=None, amended="the property access is the RFC's spelling of the ranked column (2026-11-08)"),
    "7-03-unknown-annotation": dict(
        expect="refuse-with-fix:<empty>",
        amended="an unknown annotation is refused (RFC §5, 2026-11-08)"),
    "7-06-budget-rows": dict(
        expect="refuse", note=None,
        amended="`@budget(rows = …)` is refused naming stage N (RFC §5, 2026-11-08)"),
    "7-13-annotation-colon-args": dict(
        expect="refuse-with-fix:@budget(plan = 1000)",
        body="query q() @budget(plan: 1000) {\n  match { $p: Person }\n  return { $p.name }\n}",
        amended="`rows` is stage N's; `plan` is this release's, so the fix leads to accepted text"),
    "9-37-exists-function": dict(
        expect="refuse-with-fix:$p.city is not null", later=None,
        amended="a registry row is never named `exists` (RFC §5, 2026-11-08)"),
}


def amend():
    for c in CASES:
        body = ranked_column(c["body"])
        if body != c["body"]:
            c["body"] = body
            c["amended"] = RANKED
        for key, value in AMENDMENTS.get(c["name"], {}).items():
            if value is None:
                c.pop(key, None)
            elif key == "amended" and "amended" in c:
                c["amended"] = c["amended"] + "; " + value
            else:
                c[key] = value
    for p in PAIRS:
        p["a"] = ranked_column(p["a"])
        p["b"] = ranked_column(p["b"])


def main():
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)
    amend()
    names = set()
    for c in CASES:
        assert c["name"] not in names, c["name"]
        names.add(c["name"])
        kind = KINDS[c["kind"]]
        lines = [f"// task: {c['task']}", f"// kind: {kind}"]
        expect = c["expect"].replace("\n", "\\n")
        lines.append(f"// expect: {expect}")
        if c["kind"] == 9 or "habit" in c:
            lines.append(f"// habit: {c.get('habit', 'GQ edition 2')}")
        for key in ("raw", "rfc", "shape", "returns", "later", "note", "amended"):
            if key in c:
                lines.append(f"// {key}: {c[key]}")
        with open(os.path.join(OUT, c["name"] + ".gq"), "w") as f:
            f.write("\n".join(lines) + "\n" + c["body"] + "\n")
    pairs_dir = os.path.join(HERE, "pairs")
    if os.path.isdir(pairs_dir):
        shutil.rmtree(pairs_dir)
    os.makedirs(pairs_dir)
    for p in PAIRS:
        head = f"// pair: {p['desc']}\n// covered: {p['covered']}\n// rfc: {p['rfc']}\n"
        with open(os.path.join(pairs_dir, p["name"] + ".gq"), "w") as f:
            f.write(head + p["a"] + "\n// ---\n" + p["b"] + "\n")
    print(len(PAIRS), "pairs")
    counts = {}
    for c in CASES:
        counts[KINDS[c["kind"]]] = counts.get(KINDS[c["kind"]], 0) + 1
    print(len(CASES), "cases;", ", ".join(f"{k} {v}" for k, v in counts.items()))


if __name__ == "__main__":
    main()
