"""Every edition-3 query of the before-and-after asset
(docs/rfcs/assets/2026-11-07-gq-3-before-and-after.md at rfc/gq-3),
hand-translated to the AST. The comment above each is the asset's text.
"""
from .dsl import *
from .model import I32, I64, F32, STRING, T

V3 = T("Vector", 3)

Q = {}


def add(query):
    Q[query.name] = query
    return query


# ---- 1. Lookups and filters
# query person($h: String) { match { $p: Person { handle: $h } } return { $p.name, $p.city } }
add(q("person", [("h", STRING)], read([bind("p", "Person", handle=p("h"))], [pr("p", "name"), pr("p", "city")])))
# query adults($min: I32) { match { $p: Person  $p.age >= $min } return { $p.name } order { $p.age desc } }
add(q("adults", [("min", I32)], read([bind("p", "Person"), where(cmp(">=", pr("p", "age"), p("min")))],
                                     [pr("p", "name")], order=[(pr("p", "age"), "desc")])))
# query titled() { match { $d: Doc  contains($d.title, "graph")  starts_with($d.title, "The") } return { $d.title } }
add(q("titled", [], read([bind("d", "Doc"), where(Fn("contains", (pr("d", "title"), L("graph")))),
                          where(Fn("starts_with", (pr("d", "title"), L("The"))))], [pr("d", "title")])))
# query in_cities() { match { $p: Person  $p.city in ["Berlin", "Paris"] } return { $p.name } }
add(q("in_cities", [], read([bind("p", "Person"), where(InList(pr("p", "city"), LL("Berlin", "Paris")))],
                            [pr("p", "name")])))
# query tagged() { match { $d: Doc  "rust" in $d.tags } return { $d.title } }
add(q("tagged", [], read([bind("d", "Doc"), where(InList(L("rust"), pr("d", "tags")))], [pr("d", "title")])))
# query homeless() { match { $p: Person  $p.city is null } return { $p.name } }
add(q("homeless", [], read([bind("p", "Person"), where(IsNull(pr("p", "city")))], [pr("p", "name")])))
# query names() { match { $n: Named } return { $n.name } }
add(q("names", [], read([bind("n", "Named")], [pr("n", "name")])))
# query doubled($min: I32) { match { $p: Person  $p.age * 2 >= $min }
#   return { $p.name, $p.age * 2 as twice, (2026 - $p.age) as born_about } }
add(q("doubled", [("min", I32)], read(
    [bind("p", "Person"), where(cmp(">=", Arith("*", pr("p", "age"), L(2)), p("min")))],
    [pr("p", "name"), (Arith("*", pr("p", "age"), L(2)), "twice"), (Arith("-", L(2026), pr("p", "age")), "born_about")])))
# query with_city() { match { $p: Person }
#   return { $p.name, coalesce($p.city, "unknown") as city, if($p.age >= 65, "senior", "active") as band } }
add(q("with_city", [], read([bind("p", "Person")], [
    pr("p", "name"), (Fn("coalesce", (pr("p", "city"), L("unknown"))), "city"),
    (Fn("if", (cmp(">=", pr("p", "age"), L(65)), L("senior"), L("active"))), "band")])))

# ---- 2. Traversal
# query employer($h: String) { match { $p: Person { handle: $h }  $p $k:worksAt $c } return { $c.name, $k.@src, $k.@dst } }
add(q("employer", [("h", STRING)], read([bind("p", "Person", handle=p("h")), tri("p", "worksAt", "c", k="k")],
                                        [pr("c", "name"), Meta(v("k"), "@src"), Meta(v("k"), "@dst")])))
# query around($h: String) { match { $p: Person { handle: $h }  $p <knows> $friend  $p (knows | authored) $thing
#   $p knows{2,2} $second  $p *{1,3} $near } return { $friend.name, count($second) as seconds, count($near) as near } }
add(q("around", [("h", STRING)], read(
    [bind("p", "Person", handle=p("h")), tri("p", "knows", "friend", either=True),
     tri("p", ("knows", "authored"), "thing"), tri("p", "knows", "second", hops=(2, 2)),
     tri("p", ("knows",), "near", hops=(1, 3))],   # `*` over Person -> Person is `knows` in this schema
    [pr("friend", "name"), (count(v("second")), "seconds"), (count(v("near")), "near")])))
# reachable: see section 8 of the RFC
add(q("reachable", [("from", STRING)], read(
    [SFrom(SUnion((inline([bind("p", "Person", handle=p("from")), tri("p", "knows", "q")], [v("q")]),
                   inline([SFrom(SCall("reachable", (p("from"),))), tri("q", "knows", "r")], [(v("r"), "q")]))))],
    [v("q")], distinct=True)))
# query in_collection($slug: String) { match { $x: Collection { slug: $slug }  $d "in" $x } return { $d.title } }
add(q("in_collection", [("slug", STRING)], read([bind("x", "Collection", slug=p("slug")), tri("d", "in", "x")],
                                                [pr("d", "title")])))
# query boss($slug: String) { match { $c: Company { slug: $slug }  $c worksAt $p } return { $p.name } }
add(q("boss", [("slug", STRING)], read([bind("c", "Company", slug=p("slug")), tri("c", "worksAt", "p")],
                                       [pr("p", "name")])))

# ---- 3. Aggregation
# query headcount() { match { $p worksAt $c } return { $c.name, count($p) as n } order { n desc } }
add(q("headcount", [], read([tri("p", "worksAt", "c")], [pr("c", "name"), (count(v("p")), "n")],
                            order=[(v("n"), "desc")])))
# query reach($h: String) { match { $p: Person { handle: $h }  $p knows{1,2} $q } return distinct { $q.name } }
add(q("reach", [("h", STRING)], read([bind("p", "Person", handle=p("h")), tri("p", "knows", "q", hops=(1, 2))],
                                     [pr("q", "name")], distinct=True)))
# query distinct_cities() { match { $p: Person } return { count(distinct $p.city) as cities } }
add(q("distinct_cities", [], read([bind("p", "Person")], [(count(pr("p", "city"), distinct=True), "cities")])))
# query big_employers() { match { from { match { $p worksAt $c } return { $c, count($p) as n } }  $n >= 100 }
#   return { $c.name, $n } order { $n desc } }
def big_employers(threshold):
    return q("big_employers", [], read(
        [SFrom(inline([tri("p", "worksAt", "c")], [v("c"), (count(v("p")), "n")])), where(cmp(">=", v("n"), L(threshold)))],
        [pr("c", "name"), v("n")], order=[(v("n"), "desc")]))
add(big_employers(100))
# query average_headcount() { match { from { match { $p worksAt $c } return { $c, count($p) as n } } }
#   return { avg($n) as mean, max($n) as largest } }
add(q("average_headcount", [], read(
    [SFrom(inline([tri("p", "worksAt", "c")], [v("c"), (count(v("p")), "n")]))],
    [(agg("avg", v("n")), "mean"), (agg("max", v("n")), "largest")])))
# query well_connected($min: I64) { match { $p: Person  count { $p knows $q } >= $min } return { $p.name } }
add(q("well_connected", [("min", I64)], read(
    [bind("p", "Person"), where(cmp(">=", SBlock("count", None, (tri("p", "knows", "q"),)), p("min")))],
    [pr("p", "name")])))
# query full_collections() { match { $x: Collection  count { $d "in" $x } >= $x.min_docs } return { $x.slug } }
add(q("full_collections", [], read(
    [bind("x", "Collection"), where(cmp(">=", SBlock("count", None, (tri("d", "in", "x"),)), pr("x", "min_docs")))],
    [pr("x", "slug")])))
# query degrees($min: I64) { match { $p: Person  let $degree = count { $p knows $q }  $degree >= $min }
#   return { $p.name, $degree } order { $degree desc } }
add(q("degrees", [("min", I64)], read(
    [bind("p", "Person"), SLet("degree", SBlock("count", None, (tri("p", "knows", "q"),))),
     where(cmp(">=", v("degree"), p("min")))],
    [pr("p", "name"), v("degree")], order=[(v("degree"), "desc")])))
# query authors_without_citations() { match { $p: Person  exists { $p authored $d }
#   not { $p authored $d2  $other cites $d2 } } return { $p.name } }
add(q("authors_without_citations", [], read(
    [bind("p", "Person"), SExists((tri("p", "authored", "d"),)),
     SNot((tri("p", "authored", "d2"), tri("other", "cites", "d2")))],
    [pr("p", "name")])))

# ---- 4. Shapes of a result
# query people_and_employers() { match { $p: Person  optional { $p worksAt $c } } return { $p.name, $c.name } }
add(q("people_and_employers", [], read([bind("p", "Person"), SOptional((tri("p", "worksAt", "c"),))],
                                       [pr("p", "name"), pr("c", "name")])))
# query people_and_companies() { match { from union(
#   { match { $p: Person } return { $p.name as name, "person" as kind } },
#   { match { $c: Company } return { $c.name as name, "company" as kind } }) } return { $name, $kind } }
add(q("people_and_companies", [], read(
    [SFrom(SUnion((inline([bind("p", "Person")], [(pr("p", "name"), "name"), (L("person"), "kind")]),
                   inline([bind("c", "Company")], [(pr("c", "name"), "name"), (L("company"), "kind")]))))],
    [v("name"), v("kind")])))
# query colleagues($of: String) { match { $p: Person { handle: $of }  $p worksAt $c  $q worksAt $c  $q != $p } return { $q } }
add(q("colleagues", [("of", STRING)], read(
    [bind("p", "Person", handle=p("of")), tri("p", "worksAt", "c"), tri("q", "worksAt", "c"),
     where(cmp("!=", v("q"), v("p")))], [v("q")])))
# query colleagues_of_colleagues($of: String) { match { from colleagues($of)
#   from { from colleagues($q.handle) return { $q as r } } } return distinct { $r.name } }
# (the inner read has no `match`; the grammar requires one, so it is read as `{ match { from ... } return ... }`)
add(q("colleagues_of_colleagues", [("of", STRING)], read(
    [SFrom(SCall("colleagues", (p("of"),))),
     SFrom(inline([SFrom(SCall("colleagues", (pr("q", "handle"),)))], [(v("q"), "r")]))],
    [pr("r", "name")], distinct=True)))
# query latest_per_author() { match { $p: Person
#   from { match { $p authored $d } return { $d } order { $d.year desc } limit 3 } } return { $p.name, $d.title, $d.year } }
add(q("latest_per_author", [], read(
    [bind("p", "Person"), SFrom(inline([tri("p", "authored", "d")], [v("d")], order=[(pr("d", "year"), "desc")], limit=L(3)))],
    [pr("p", "name"), pr("d", "title"), pr("d", "year")])))
# query top_people($k: I64 = 10) { match { $p: Person } return { $p.name } order { $p.age desc } limit $k }
add(q("top_people", [("k", I64)], read([bind("p", "Person")], [pr("p", "name")],
                                       order=[(pr("p", "age"), "desc")], limit=p("k"))))

# ---- 5. Ranking and search
DOCS_2025 = inline([bind("d", "Doc"), where(cmp(">=", pr("d", "year"), L(2025)))], [v("d")])
ALL_DOCS = inline([bind("d", "Doc")], [v("d")])
# query similar($q: Vector(3)) { match { from nearest({ match { $d: Doc  $d.year >= 2025 } return { $d } }, embedding, $q, k: 20) }
#   return { $d.title } order { $distance } }
add(q("similar", [("q", V3)], read([SFrom(SNearest(DOCS_2025, "embedding", p("q"), L(20)))],
                                   [pr("d", "title")], order=[v("distance")])))
# query similar_scored($q: Vector(3)) { ... return { $d.title, $distance as s } order { $distance } }
add(q("similar_scored", [("q", V3)], read([SFrom(SNearest(ALL_DOCS, "embedding", p("q"), L(20)))],
                                          [pr("d", "title"), (v("distance"), "s")], order=[v("distance")])))
# query cited_near($coll: String, $q: Vector(3)) { match {
#   from nearest({ match { $d "in" $x  $x: Collection { slug: $coll } } return { $d } }, embedding, $q, k: 20)
#   $d cites $other } return { $d.title, $distance, count($other) as cites } order { $distance } }
add(q("cited_near", [("coll", STRING), ("q", V3)], read(
    [SFrom(SNearest(inline([tri("d", "in", "x"), bind("x", "Collection", slug=p("coll"))], [v("d")]), "embedding", p("q"), L(20))),
     tri("d", "cites", "other")],
    [pr("d", "title"), v("distance"), (count(v("other")), "cites")], order=[v("distance")])))
# query within_radius($q: Vector(3), $r: F32) { match {
#   from l2({ match { $d: Doc  $d.year >= 2025 } return { $d } }, embedding, $q)  $distance <= $r }
#   return { $d.title, $distance } order { $distance, $d.id } limit 10 }
add(q("within_radius", [("q", V3), ("r", F32)], read(
    [SFrom(SL2(DOCS_2025, "embedding", p("q"))), where(cmp("<=", v("distance"), p("r")))],
    [pr("d", "title"), v("distance")], order=[v("distance"), pr("d", "id")], limit=L(10))))
# query about($q: String) { match { from bm25({ match { $d: Doc } return { $d } }, body, $q) }
#   return { $d.title, $score as s } order { $score desc } limit 10 }
add(q("about", [("q", STRING)], read([SFrom(SBm25(ALL_DOCS, "body", p("q")))],
                                     [pr("d", "title"), (v("score"), "s")], order=[(v("score"), "desc")], limit=L(10))))
# query mentions($q: String) { match { $d: Doc  search($d.body, $q)  fuzzy($d.title, $q, max_edits: 2) } return { $d.title } }
add(q("mentions", [("q", STRING)], read(
    [bind("d", "Doc"), where(Fn("search", (pr("d", "body"), p("q")))), where(Fn("fuzzy", (pr("d", "title"), p("q"), L(2))))],
    [pr("d", "title")])))
# query hybrid($q: String, $v: Vector(3)) { match { let $docs = { match { $d: Doc } return { $d } }
#   from rrf(nearest($docs, embedding, $v, k: 50), bm25($docs, body, $q), k: 60) }
#   return { $d.title, $score } order { $score desc } limit 10 }
add(q("hybrid", [("q", STRING), ("v", V3)], read(
    [SLet("docs", ALL_DOCS),
     SFrom(SRrf((SNearest(SRelVar("docs"), "embedding", p("v"), L(50)), SBm25(SRelVar("docs"), "body", p("q"))), 60))],
    [pr("d", "title"), v("score")], order=[(v("score"), "desc")], limit=L(10))))
# query weighted($q: String, $v: Vector(3)) { match { let $docs = { match { $d: Doc } return { $d } }
#   from { from l2($docs, embedding, $v) return { $d, $distance as vec } }
#   from { from bm25($docs, body, $q) return { $d as d2, $score as lex } }  $d = $d2 }
#   return { $d.title, 0.7 * (1.0 / (1.0 + $vec)) + 0.3 * $lex as s } order { $s desc } limit 10 }
add(q("weighted", [("q", STRING), ("v", V3)], read(
    [SLet("docs", ALL_DOCS),
     SFrom(inline([SFrom(SL2(SRelVar("docs"), "embedding", p("v")))], [v("d"), (v("distance"), "vec")])),
     SFrom(inline([SFrom(SBm25(SRelVar("docs"), "body", p("q")))], [(v("d"), "d2"), (v("score"), "lex")])),
     where(cmp("=", v("d"), v("d2")))],
    [pr("d", "title"),
     (Arith("+", Arith("*", L(0.7), Arith("/", L(1.0), Arith("+", L(1.0), v("vec")))), Arith("*", L(0.3), v("lex"))), "s")],
    order=[(v("s"), "desc")], limit=L(10))))
# query similar_fast($q: Vector(3)) { match { from nearest({ match { $d: Doc } return { $d } }, embedding, $q, k: 20, recall: 0.9) }
#   return { $d.title, $distance } }
add(q("similar_fast", [("q", V3)], read([SFrom(SNearest(ALL_DOCS, "embedding", p("q"), L(20), 0.9))],
                                        [pr("d", "title"), v("distance")])))

# ---- 6. Writes
# query hire($h: String, $slug: String) { insert Person { handle: $h, name: $h }  insert WorksAt { from: $h, to: $slug } }
add(q("hire", [("h", STRING), ("slug", STRING)], SWriteBody((), (
    SInsert("Person", (("handle", p("h")), ("name", p("h")))),
    SInsert("WorksAt", (("from", p("h")), ("to", p("slug"))))))))
# query retire($h: String) as commit { $p: Person { handle: $h }  $p $k:worksAt $c  delete $k  update $p set { city: null } }
NULL_STR = Lit(None, STRING.null())
add(q("retire", [("h", STRING)], SWriteBody(
    (bind("p", "Person", handle=p("h")), tri("p", "worksAt", "c", k="k")),
    (SDelete("k"), SUpdate("p", (("city", NULL_STR),))))))
# edition 2: delete WorksAt where @src = $h   update Person set { city: null } where handle = $h
add(q("retire_e2_delete", [("h", STRING)], SWriteBody((), (SDeleteWhere("WorksAt", cmp("=", Bare("@src"), p("h"))),))))
add(q("retire_e2_update", [("h", STRING)], SWriteBody((), (
    SUpdateWhere("Person", (("city", NULL_STR),), cmp("=", Bare("handle"), p("h"))),))))
# query close_company($slug: String) { $c: Company { slug: $slug }  $p $k:worksAt $c  delete $k  update $p set { city: null } }
add(q("close_company", [("slug", STRING)], SWriteBody(
    (bind("c", "Company", slug=p("slug")), tri("p", "worksAt", "c", k="k")),
    (SDelete("k"), SUpdate("p", (("city", NULL_STR),))))))
# query birthday($h: String) { $p: Person { handle: $h }  update $p set { age: $p.age + 1 } }
add(q("birthday", [("h", STRING)], SWriteBody(
    (bind("p", "Person", handle=p("h")),), (SUpdate("p", (("age", Arith("+", pr("p", "age"), L(1))),)),))))
# query tag_near($q: Vector(3)) { from nearest({ match { $d: Doc } return { $d } }, embedding, $q, k: 20)  update $d set { tags: ["near"] } }
add(q("tag_near", [("q", V3)], SWriteBody(
    (SFrom(SNearest(ALL_DOCS, "embedding", p("q"), L(20))),), (SUpdate("d", (("tags", LL("near")),)),))))
# query propose($h: String) as unattached branch { insert Person { handle: $h, name: $h } }
add(q("propose", [("h", STRING)], SWriteBody((), (SInsert("Person", (("handle", p("h")), ("name", p("h")))),))))
