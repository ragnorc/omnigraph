#!/usr/bin/env python3
"""Seeded repairs: edition-3 answers written with the habits section 3's refused-respellings
table and section 5's foreign-word table name, one per task, written to seeds/seeds.json."""
import json, os, sys
S = []
def s(id, task, habit, text):
    S.append({"id": id, "task": task, "habit": habit, "text": "gq 3;\n" + text})
s("s01", "t03", "infix contains / starts_with (edition 2)",
  'query titled() {\n  match { $d: Doc  $d.title contains "graph"  $d.title starts_with "The" }\n  return { $d.title }\n}')
s("s02", "t05", "list contains (edition 2)",
  'query tagged() {\n  match { $d: Doc  $d.tags contains "rust" }\n  return { $d.title }\n}')
s("s03", "t28", "positional fuzzy budget (edition 2)",
  'query mentions($s: String, $q: String) {\n  match { $d: Doc  search($d.body, $s)  fuzzy($d.body, $q, 2) }\n  return { $d.title }\n}')
s("s04", "t24", "nearest in order (edition 2)",
  'query similar($q: Vector(3)) {\n  match { $d: Doc  $d.year >= 2025 }\n  return { $d.title }\n  order { nearest($d.embedding, $q) }\n  limit 20\n}')
s("s05", "t27", "bm25 in return and order (edition 2)",
  'query about($q: String) {\n  match { $d: Doc }\n  return { $d.title, bm25($d.body, $q) as s }\n  order { bm25($d.body, $q) desc }\n  limit 10\n}')
s("s06", "t33", "delete T where (edition 2)",
  'query retire($h: String) {\n  delete WorksAt where @src = $h\n}')
s("s07", "t35", "update T set where (edition 2)",
  'query birthday($h: String) {\n  update Person set { age: age + 1 } where handle = $h\n}')
s("s08", "m3", "having (SQL)",
  'query big_employers($min: I64) {\n  match { $c: Company  $p worksAt $c }\n  return { $c.name, count($p) as n }\n  having { n >= $min }\n  order { n desc }\n}')
s("s09", "t20", "optional without braces (Cypher OPTIONAL MATCH)",
  'query people_and_employers() {\n  match { $p: Person  optional $p worksAt $c }\n  return { $p.name, $c.name }\n}')
s("s10", "t09", "CASE ... END (SQL)",
  'query with_city() {\n  match { $p: Person }\n  return { $p.name, coalesce($p.city, "unknown") as city, CASE WHEN $p.age >= 65 THEN "senior" ELSE "active" END as band }\n}')
s("s11", "t08", "minus spaced on one side only",
  'query doubled($min: I32) {\n  match { $p: Person  $p.age * 2 >= $min }\n  return { $p.name, $p.age * 2 as twice, 2026 -$p.age as born_about }\n}')
s("s12", "t02", "comparison inside binding braces",
  'query adults($min: I32) {\n  match { $p: Person { age >= $min } }\n  return { $p.name }\n  order { $p.age desc }\n}')
s("s13", "t01", "missing empty parameter list",
  'query person {\n  match { $p: Person { handle: $h } }\n  return { $p.name, $p.city }\n}')
s("s14", "t32", "mutation keyword (GraphQL)",
  'mutation hire($h: String, $slug: String) {\n  insert Person { handle: $h, name: $h }\n  insert WorksAt { from: $h, to: $slug }\n}')
s("s15", "t35", "write body wrapped in match",
  'query birthday($h: String) {\n  match { $p: Person { handle: $h } }\n  update $p set { age: $p.age + 1 }\n}')
s("s16", "t13", "bare reserved edge name",
  'query in_collection($slug: String) {\n  match { $x: Collection { slug: $slug }  $d in $x }\n  return { $d.title }\n}')
s("s17", "t18", "where (SQL / Cypher)",
  'query well_connected($min: I64) {\n  match { $p: Person  where count { $p knows $q } >= $min }\n  return { $p.name }\n}')
s("s18", "t11", "arrow traversal (Cypher)",
  'query around($h: String) {\n  match { $p: Person { handle: $h }  $p -knows- $f }\n  return { $f.name }\n}')
s("s19", "t22", "let ... in (PR 1244)",
  'query latest_per_author() {\n  match {\n    $p: Person\n    let $d in { match { $p authored $d } return { $d } order { $d.year desc } limit 3 }\n  }\n  return { $p.name, $d.title, $d.year }\n}')
s("s20", "t16", "count(*) (SQL)",
  'query distinct_cities() {\n  match { $p: Person }\n  return { count(*) as cities }\n}')
s("s21", "t06", "null literal in a read",
  'query homeless() {\n  match { $p: Person  $p.city = null }\n  return { $p.name }\n}')
s("s22", "t02", "uppercase keyword",
  'query adults($min: I32) {\n  match { $p: Person  $p.age >= $min }\n  return { $p.name }\n  order { $p.age DESC }\n}')
s("s23", "t17", "aggregate of an aggregate",
  'query average_headcount() {\n  match { $c: Company  $p worksAt $c }\n  return { avg(count($p)) as mean, max(count($p)) as largest }\n}')
s("s24", "t30", "two rankings drawn into one scope (T68)",
  'query weighted($q: String, $v: Vector(3)) {\n  match {\n    from distance({ match { $d: Doc } return { $d } }, $d.embedding, $v)\n    from bm25({ match { $d: Doc } return { $d } }, $d.body, $q)\n  }\n  return { $d.title, 0.7 * (1.0 / (1.0 + $distance)) + 0.3 * $score as s }\n  order { s desc }\n  limit 10\n}')
s("s25", "t24", "nearest without k",
  'query similar($q: Vector(3)) {\n  match { from nearest({ match { $d: Doc  $d.year >= 2025 } return { $d } }, $d.embedding, $q) }\n  return { $d.title }\n  order { $distance }\n  limit 20\n}')
s("s26", "t21", "UNION between two reads (SQL)",
  'query people_and_companies() {\n  match { $p: Person } return { $p.name as name, "person" as kind }\n  union\n  match { $c: Company } return { $c.name as name, "company" as kind }\n}')
s("s27", "t04", "single-quoted strings and == (other languages)",
  "query in_cities() {\n  match { $p: Person  $p.city == 'Berlin' or $p.city == 'Paris' }\n  return { $p.name }\n}")
s("s28", "m4", "space before a call's parenthesis",
  'query colleagues($of: String) {\n  match { $p: Person { handle: $of }  $p worksAt $c  $q worksAt $c  $q != $p }\n  return { $q }\n}\n\nquery colleagues_of_colleagues($of: String) {\n  match {\n    from { match { from colleagues ($of) } return { $q as c } }\n    from { match { from colleagues ($c.handle) } return { $q as r } }\n  }\n  return distinct { $r.name }\n}')
json.dump(S, open(os.path.join(sys.argv[1], "seeds.json"), "w"), indent=2)
print(len(S), "seeds")
