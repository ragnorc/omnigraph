import json, sys
T = []
def t(id, kind, section, prompt, truth3, truth2=None, m0=None, alt3=(), alt2=(), note=None):
    T.append(dict(id=id, kind=kind, asset_section=section, m0=m0, prompt=prompt,
                  truth3=[truth3, *alt3], truth2=([truth2, *alt2] if truth2 else []), note=note))

t("t01", "key lookup", "1", "Show the name and city of the person whose handle is `$h`.",
  "query person($h: String) {\n  match { $p: Person { handle: $h } }\n  return { $p.name, $p.city }\n}",
  "query person($h: String) {\n  match { $p: Person { handle: $h } }\n  return { $p.name, $p.city }\n}")
t("t02", "comparison filter", "1", "List the names of people aged at least `$min`, oldest first.",
  "query adults($min: I32) {\n  match { $p: Person  $p.age >= $min }\n  return { $p.name }\n  order { $p.age desc }\n}",
  "query adults($min: I32) {\n  match { $p: Person  $p.age >= $min }\n  return { $p.name }\n  order { $p.age desc }\n}")
t("t03", "string predicate", "1", "Find the titles of documents whose title contains \"graph\" and starts with \"The\".",
  "query titled() {\n  match { $d: Doc  contains($d.title, \"graph\")  starts_with($d.title, \"The\") }\n  return { $d.title }\n}",
  "query titled() {\n  match { $d: Doc  $d.title contains \"graph\"  $d.title starts_with \"The\" }\n  return { $d.title }\n}")
t("t04", "membership in a list literal", "1", "List the names of people who live in Berlin or Paris.",
  "query in_cities() {\n  match { $p: Person  $p.city in [\"Berlin\", \"Paris\"] }\n  return { $p.name }\n}",
  "query in_cities() {\n  match { $p: Person  $p.city in [\"Berlin\", \"Paris\"] }\n  return { $p.name }\n}")
t("t05", "membership in a list property", "1", "List the titles of documents tagged \"rust\".",
  "query tagged() {\n  match { $d: Doc  \"rust\" in $d.tags }\n  return { $d.title }\n}",
  "query tagged() {\n  match { $d: Doc  $d.tags contains \"rust\" }\n  return { $d.title }\n}")
t("t06", "null test", "1", "List the names of people whose city is not recorded.",
  "query homeless() {\n  match { $p: Person  $p.city is null }\n  return { $p.name }\n}",
  "query homeless() {\n  match { $p: Person  $p.city is null }\n  return { $p.name }\n}")
t("t07", "interface binding", "1", "List the name of everything that has a name, people and companies alike, in one result.",
  "query names() {\n  match { $n: Named }\n  return { $n.name }\n}",
  note="Edition 2 parses `$n: Named` but today's type checker refuses it (`T1: unknown node type Named`), so no edition-2 ground truth exists, although the asset marks the kind Unchanged.")
t("t08", "arithmetic", "1", "List the people whose age doubled is at least `$min`, returning each name, the age times two as `twice`, and 2026 minus the age as `born_about`.",
  "query doubled($min: I32) {\n  match { $p: Person  $p.age * 2 >= $min }\n  return { $p.name, $p.age * 2 as twice, 2026 - $p.age as born_about }\n}")
t("t09", "conditional and default values", "1", "List every person's name, their city or \"unknown\" when it is missing (as `city`), and \"senior\" when they are 65 or older, otherwise \"active\" (as `band`).",
  "query with_city() {\n  match { $p: Person }\n  return { $p.name, coalesce($p.city, \"unknown\") as city, if($p.age >= 65, \"senior\", \"active\") as band }\n}")
t("t10", "one hop, edge bound", "2", "For the person with handle `$h`, show their employer's name and the employment link's source and destination ids.",
  "query employer($h: String) {\n  match { $p: Person { handle: $h }  $p $k:worksAt $c }\n  return { $c.name, $k.@src, $k.@dst }\n}",
  "query employer($h: String) {\n  match { $p: Person { handle: $h }  $p $k:worksAt $c }\n  return { $c.name, $k.@src, $k.@dst }\n}")
t("t11", "either orientation", "2", "List the names of the people linked to the person with handle `$h` by a `knows` edge in either direction.",
  "query around($h: String) {\n  match { $p: Person { handle: $h }  $p <knows> $f }\n  return { $f.name }\n}",
  "query around($h: String) {\n  match { $p: Person { handle: $h }  $p <knows> $f }\n  return { $f.name }\n}")
t("t12", "unbounded reachability", "2", "List the distinct names of everyone reachable from the person with handle `$from` by following `knows` edges any number of times.",
  "query reachable($from: String) {\n  match {\n    from union(\n      { match { $p: Person { handle: $from }  $p knows $q } return { $q } },\n      { match { from reachable($from)  $q knows $r } return { $r as q } })\n  }\n  return distinct { $q.name }\n}")
t("t13", "edge whose name is a reserved word", "2", "List the titles of the documents in the collection with slug `$slug`.",
  "query in_collection($slug: String) {\n  match { $x: Collection { slug: $slug }  $d \"in\" $x }\n  return { $d.title }\n}",
  "query in_collection($slug: String) {\n  match { $x: Collection { slug: $slug }  $d \"in\" $x }\n  return { $d.title }\n}")
t("t14", "traversal against schema order", "2", "Who works at the company with slug `$slug`? Return their names.",
  "query staff($slug: String) {\n  match { $c: Company { slug: $slug }  $p worksAt $c }\n  return { $p.name }\n}",
  "query staff($slug: String) {\n  match { $c: Company { slug: $slug }  $p worksAt $c }\n  return { $p.name }\n}")
t("t15", "count per group", "3", "For each company, return its name and its number of employees as `n`, largest first.",
  "query headcount() {\n  match { $c: Company  $p worksAt $c }\n  return { $c.name, count($p) as n }\n  order { n desc }\n}",
  "query headcount() {\n  match { $c: Company  $p worksAt $c }\n  return { $c.name, count($p) as n }\n  order { n desc }\n}")
t("t16", "distinct counts", "3", "How many distinct cities do people live in? Return the number as `cities`.",
  "query distinct_cities() {\n  match { $p: Person }\n  return { count(distinct $p.city) as cities }\n}",
  "query distinct_cities() {\n  match { $p: Person }\n  return { count(distinct $p.city) as cities }\n}")
t("t17", "aggregate of an aggregate", "3", "Across companies, what is the average number of employees per company and the largest? Return them as `mean` and `largest`.",
  "query average_headcount() {\n  match { from { match { $c: Company  $p worksAt $c } return { $c, count($p) as n } } }\n  return { avg($n) as mean, max($n) as largest }\n}")
t("t18", "count block with a parameter bound", "3", "List the names of people who know at least `$min` people.",
  "query well_connected($min: I64) {\n  match { $p: Person  count { $p knows $q } >= $min }\n  return { $p.name }\n}",
  "query well_connected($min: I64) {\n  match { $p: Person  count { $p knows $q } >= $min }\n  return { $p.name }\n}")
t("t19", "existence and absence", "3", "List the names of people who have authored at least one document and none of whose documents is cited by any document.",
  "query uncited_authors() {\n  match {\n    $p: Person\n    exists { $p authored $d }\n    not { $p authored $d2  $other cites $d2 }\n  }\n  return { $p.name }\n}",
  "query uncited_authors() {\n  match {\n    $p: Person\n    exists { $p authored $d }\n    not { $p authored $d2  $other cites $d2 }\n  }\n  return { $p.name }\n}")
t("t20", "outer join", "4", "List every person's name together with their employer's name, keeping people who have no employer.",
  "query people_and_employers() {\n  match { $p: Person  optional { $p worksAt $c } }\n  return { $p.name, $c.name }\n}")
t("t21", "union of two reads", "4", "List the names of all people and all companies in one result, with a column `kind` that says \"person\" or \"company\".",
  "query people_and_companies() {\n  match {\n    from union(\n      { match { $p: Person } return { $p.name as name, \"person\" as kind } },\n      { match { $c: Company } return { $c.name as name, \"company\" as kind } })\n  }\n  return { $name, $kind }\n}")
t("t22", "correlated subquery per group", "4", "For each person, list the titles and years of their three most recent documents.",
  "query latest_per_author() {\n  match {\n    $p: Person\n    from { match { $p authored $d } return { $d } order { $d.year desc } limit 3 }\n  }\n  return { $p.name, $d.title, $d.year }\n}")
t("t23", "parameterised limit with a default", "4", "List the names of the `$k` oldest people, where `$k` defaults to 10.",
  "query top_people($k: I64 = 10) {\n  match { $p: Person }\n  return { $p.name }\n  order { $p.age desc }\n  limit $k\n}")
t("t24", "nearest window", "5", "Find the titles of the 20 documents from 2025 or later that are most similar to the vector `$q`.",
  "query similar($q: Vector(3)) {\n  match { from nearest({ match { $d: Doc  $d.year >= 2025 } return { $d } }, $d.embedding, $q, k: 20) }\n  return { $d.title }\n  order { $distance }\n}",
  "query similar($q: Vector(3)) {\n  match { $d: Doc  $d.year >= 2025 }\n  return { $d.title }\n  order { nearest($d.embedding, $q) }\n  limit 20\n}",
  alt3=["query similar($q: Vector(3)) {\n  match { from nearest({ match { $d: Doc  $d.year >= 2025 } return { $d } }, $d.embedding, $q, k: 20) }\n  return { $d.title }\n  order { $distance }\n  limit 20\n}"])
t("t25", "nearest with the distance returned", "5", "Find the 20 documents most similar to the vector `$q`; return each title and its distance as `s`.",
  "query similar_scored($q: Vector(3)) {\n  match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 20) }\n  return { $d.title, $distance as s }\n  order { $distance }\n}",
  "query similar_scored($q: Vector(3)) {\n  match { $d: Doc }\n  return { $d.title, nearest($d.embedding, $q) as s }\n  order { nearest($d.embedding, $q) }\n  limit 20\n}",
  alt3=["query similar_scored($q: Vector(3)) {\n  match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 20) }\n  return { $d.title, $distance as s }\n  order { $distance }\n  limit 20\n}"])
t("t26", "every document within a distance", "5", "List the titles and distances of documents from 2025 or later whose vector is within distance `$r` of `$q`, closest first, at most 10.",
  "query within_radius($q: Vector(3), $r: F32) {\n  match {\n    from distance({ match { $d: Doc  $d.year >= 2025 } return { $d } }, $d.embedding, $q)\n    $distance <= $r\n  }\n  return { $d.title, $distance }\n  order { $distance }\n  limit 10\n}")
t("t27", "lexical ranking", "5", "Rank documents by the BM25 relevance of their body to the text `$q`; return the top 10 titles with the score as `s`.",
  "query about($q: String) {\n  match { from bm25({ match { $d: Doc } return { $d } }, $d.body, $q) }\n  return { $d.title, $score as s }\n  order { $score desc }\n  limit 10\n}",
  "query about($q: String) {\n  match { $d: Doc }\n  return { $d.title, bm25($d.body, $q) as s }\n  order { bm25($d.body, $q) desc }\n  limit 10\n}")
t("t28", "lexical filter without a score", "5", "List the titles of documents whose body matches the search text `$s` and also matches the text `$q` allowing up to 2 typos.",
  "query mentions($s: String, $q: String) {\n  match { $d: Doc  search($d.body, $s)  fuzzy($d.body, $q, max_edits: 2) }\n  return { $d.title }\n}",
  "query mentions($s: String, $q: String) {\n  match { $d: Doc  search($d.body, $s)  fuzzy($d.body, $q, 2) }\n  return { $d.title }\n}",
  alt3=["query mentions($s: String, $q: String) {\n  match { $d: Doc  match_terms($d.body, terms($s, mode: any))  match_terms($d.body, terms($q, mode: any, max_edits: 2)) }\n  return { $d.title }\n}"],
  note="The asset's `mentions` runs `fuzzy` over `Doc.title`, which its schema does not index (`T27`); the task uses the indexed body.")
t("t29", "hybrid, fused", "5", "Fuse, by reciprocal rank fusion with constant 60, the 10 documents nearest to the vector `$v` and every document whose body matches the text `$q` ranked by BM25; return the 10 best fused titles.",
  "query hybrid($q: String, $v: Vector(3)) {\n  match {\n    let $docs = { match { $d: Doc } return { $d } }\n    from rrf(nearest($docs, $d.embedding, $v, k: 10), bm25($docs, $d.body, $q), k: 60)\n  }\n  return { $d.title }\n  order { $score desc }\n  limit 10\n}",
  "query hybrid($q: String, $v: Vector(3)) {\n  match { $d: Doc }\n  return { $d.title }\n  order { rrf(nearest($d.embedding, $v), bm25($d.body, $q), 60) }\n  limit 10\n}")
t("t30", "hybrid, weighted", "5", "Score each document whose body matches the text `$q` as 0.7 times 1/(1 + its vector distance to `$v`) plus 0.3 times its BM25 score; return the 10 best titles with the score as `s`.",
  "query weighted($q: String, $v: Vector(3)) {\n  match {\n    let $docs = { match { $d: Doc } return { $d } }\n    from { match { from distance($docs, $d.embedding, $v) } return { $d, $distance as vec } }\n    from { match { from bm25($docs, $d.body, $q) } return { $d as d2, $score as lex } }\n    $d = $d2\n  }\n  return { $d.title, 0.7 * (1.0 / (1.0 + $vec)) + 0.3 * $lex as s }\n  order { s desc }\n  limit 10\n}")
t("t31", "declared recall", "5", "Find the 20 documents nearest to the vector `$q`, accepting 90% recall in exchange for speed; return titles and distances.",
  "query similar_fast($q: Vector(3)) {\n  match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 20, recall: 0.9) }\n  return { $d.title, $distance }\n}",
  alt3=["query similar_fast($q: Vector(3)) {\n  match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 20, recall: 0.9) }\n  return { $d.title, $distance }\n  order { $distance }\n}"])
t("t32", "insert a node and a keyed edge", "6", "Hire someone: create a person with handle `$h` (whose name is also `$h`) and record that they work at the company with slug `$slug`.",
  "query hire($h: String, $slug: String) {\n  insert Person { handle: $h, name: $h }\n  insert WorksAt { from: $h, to: $slug }\n}",
  "query hire($h: String, $slug: String) {\n  insert Person { handle: $h, name: $h }\n  insert WorksAt { from: $h, to: $slug }\n}")
t("t33", "delete by predicate", "6", "Remove every employment link of the person with handle `$h`.",
  "query retire($h: String) {\n  $p: Person { handle: $h }\n  $p $k:worksAt $c\n  delete $k\n}",
  "query retire($h: String) {\n  delete WorksAt where @src = $h\n}",
  alt3=["query retire($h: String) {\n  $x: WorksAt  $x.@src = $h\n  delete $x\n}"])
t("t34", "write whose targets come from a traversal", "6", "Close a company: delete the employment links of everyone who works at the company with slug `$slug`.",
  "query close_company($slug: String) {\n  $c: Company { slug: $slug }\n  $p $k:worksAt $c\n  delete $k\n}",
  "query close_company($slug: String) {\n  delete WorksAt where @dst = $slug\n}",
  alt3=["query close_company($slug: String) {\n  $x: WorksAt  $x.@dst = $slug\n  delete $x\n}"],
  note="The asset marks this kind 'Not supported' in edition 2, but `delete WorksAt where @dst = $slug` expresses it: the company's key is the edge's destination id.")
t("t35", "write with a computed value", "6", "It is the birthday of the person with handle `$h`: increase their age by one.",
  "query birthday($h: String) {\n  $p: Person { handle: $h }\n  update $p set { age: $p.age + 1 }\n}")
t("t36", "write whose targets come from a ranked read", "6", "Tag the 20 documents nearest to the vector `$q` with the single tag \"near\", replacing their existing tags.",
  "query tag_near($q: Vector(3)) {\n  from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 20)\n  update $d set { tags: [\"near\"] }\n}")
t("t37", "unattached branch snapshot", "6", "Create a person with handle `$h` (name also `$h`) as an unattached branch snapshot instead of writing to the branch.",
  "query propose($h: String) as unattached branch {\n  insert Person { handle: $h, name: $h }\n}",
  "query propose($h: String) as unattached branch {\n  insert Person { handle: $h, name: $h }\n}")
t("t38", "the engine catalogue", "7", "List the name and inputs of every physical operator the engine has.",
  "query operators() {\n  match { $o: @Operator }\n  return { $o.name, $o.inputs }\n}",
  "query operators() {\n  match { $o: @Operator }\n  return { $o.name, $o.inputs }\n}")
t("t39", "annotations on a declaration", "7", "Write a stored query returning the names of people older than `$cutoff`, with the description \"People over a given age\" and the instruction \"Pass the age as an integer\".",
  "query people_over($cutoff: I32) @description(\"People over a given age\") @instruction(\"Pass the age as an integer\") {\n  match { $p: Person  $p.age > $cutoff }\n  return { $p.name }\n}",
  "query people_over($cutoff: I32) @description(\"People over a given age\") @instruction(\"Pass the age as an integer\") {\n  match { $p: Person  $p.age > $cutoff }\n  return { $p.name }\n}")
# The six M0 composition tasks (RFC section 10).
t("m1", "block as a value (count plus projection)", "3", "For every person, return their name and how many people they know as `degree` (zero for people who know nobody), most connected first.",
  "query degrees() {\n  match {\n    $p: Person\n    let $degree = count { $p knows $q }\n  }\n  return { $p.name, $degree }\n  order { $degree desc }\n}", m0="count plus projection")
t("m2", "count block compared with another binding's value", "3", "List the slugs of the collections that hold at least as many documents as their own `min_docs` value.",
  "query full_collections() {\n  match { $x: Collection  count { $d \"in\" $x } >= $x.min_docs }\n  return { $x.slug }\n}", m0="a group-specific threshold")
t("m3", "filter on an aggregate", "3", "List the companies with at least `$min` employees: the company name and its employee count as `n`, largest first.",
  "query big_employers($min: I64) {\n  match {\n    from { match { $c: Company  $p worksAt $c } return { $c, count($p) as n } }\n    $n >= $min\n  }\n  return { $c.name, $n }\n  order { $n desc }\n}",
  "query big_employers($min: I64) {\n  match { $c: Company  count { $q worksAt $c } >= $min  $p worksAt $c }\n  return { $c.name, count($p) as n }\n  order { n desc }\n}",
  alt3=["query big_employers($min: I64) {\n  match { $c: Company  count { $q worksAt $c } >= $min  $p worksAt $c }\n  return { $c.name, count($p) as n }\n  order { n desc }\n}",
        "query big_employers($min: I64) {\n  match {\n    $c: Company\n    let $n = count { $p worksAt $c }\n    $n >= $min\n  }\n  return { $c.name, $n }\n  order { $n desc }\n}"],
  m0="filtering grouped output",
  note="The asset marks 'filter on an aggregate' Not supported in edition 2; with a literal or parameter threshold, a count block in `match` beside the grouped traversal expresses it.")
t("m4", "reuse of a declared read with arguments", "4", "Define a query `colleagues($of)` that returns the people working at the same company as the person with handle `$of`, and a query `colleagues_of_colleagues($of)` that reuses it to list the distinct names of the colleagues of those colleagues.",
  "query colleagues($of: String) {\n  match { $p: Person { handle: $of }  $p worksAt $c  $q worksAt $c  $q != $p }\n  return { $q }\n}\n\nquery colleagues_of_colleagues($of: String) {\n  match {\n    from { match { from colleagues($of) } return { $q as c } }\n    from { match { from colleagues($c.handle) } return { $q as r } }\n  }\n  return distinct { $r.name }\n}", m0="reuse with parameters")
t("m5", "a window over documents, then a traversal from the hits", "5", "Find the 10 documents most similar to the vector `$q` and, for each, return its title, its distance and how many documents it cites as `cites` (zero when it cites none).",
  "query cited_near($q: Vector(3)) {\n  match {\n    from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 10)\n    let $cites = count { $d cites $other }\n  }\n  return { $d.title, $distance, $cites }\n  order { $distance }\n}", m0="search followed by a graph operation")
t("m6", "window versus population", "5", "Take the 10 documents nearest to the vector `$q` among all documents, and return the titles of those of the 10 that belong to the collection with slug `$coll`.",
  "query near_then_in($q: Vector(3), $coll: String) {\n  match {\n    from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 10)\n    $x: Collection { slug: $coll }\n    $d \"in\" $x\n  }\n  return { $d.title }\n}",
  alt3=["query near_then_in($q: Vector(3), $coll: String) {\n  match {\n    from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 10)\n    $d \"in\" $x\n    $x: Collection { slug: $coll }\n  }\n  return { $d.title }\n}"],
  m0="the window-versus-population distinction")
assert len(T) == 45, len(T)
out = sys.argv[1]
for task in T:
    with open(f"{out}/{task['id']}.json", "w") as f:
        json.dump(task, f, indent=2); f.write("\n")
print(len(T), "tasks;", sum(1 for x in T if x['truth2']), "with an edition-2 truth")
