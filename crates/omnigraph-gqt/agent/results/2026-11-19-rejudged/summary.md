# Agent instrument results: 2026-11-19-rejudged

Model `claude-sonnet-5-5`, 2.1.328 (Claude Code), git `46b39dfe6662362561517868d8d7221b9202a496`, started 2026-11-19T08:18:17+00:00.
Card digests: `gq3.md` `6608ff7720fe`.
README.md beside the runner defines every column. Not a CI gate.

Means over the runs, the range in parentheses. `shared` is the 45 items edition 2 expresses in one query; `all` is the 75.

| Arm | Set | Items | First try accepted | First try correct (rows) | Planner gaps (P006) | Not supported yet (T86) | Unjudged (no expected rows) | IR-equal | Refused | Repair with card accepted / correct | Repair without card accepted / correct | Correct after repair | Tokens in / out |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| gq3 | shared | 45 | 42.4 (41–43) | 40.4 (39–41) | 0.0 (0) | 0.0 (0) | 0.0 (0) (0.0 (0)) | 24.6 (23–26) | 2.6 (2–4) | 2.4 (2–4) / 1.6 (1–3) | 1.6 (1–2) / 1.4 (1–2) | 42.0 (42) | 16026.6 (16025–16028) / 190.2 (179–199) |
| gq3 | tasks | 45 | 41.8 (40–43) | 24.8 (24–25) | 0.0 (0) | 0.2 (0–1) | 15.0 (14–16) (15.0 (14–16)) | 21.0 (20–22) | 3.2 (2–5) | 1.8 (1–4) / 0.2 (0–1) | 1.0 (0–2) / 0.0 (0) | 25.0 (25) | 16094.6 (16093–16096) / 292.6 (284–300) |
| gq3 | corpus | 30 | 28.4 (27–29) | 26.4 (25–27) | 0.0 (0) | 0.0 (0) | 1.8 (1–2) (1.0 (1)) | 9.0 (8–10) | 1.6 (1–3) | 1.6 (1–3) / 1.6 (1–3) | 1.6 (1–3) / 1.6 (1–3) | 28.0 (28) | 15945.6 (15944–15947) / 316.0 (296–326) |
| gq3 | all | 75 | 70.2 (67–72) | 51.2 (49–52) | 0.0 (0) | 0.2 (0–1) | 16.8 (16–18) (16.0 (15–17)) | 30.0 (29–32) | 4.8 (3–8) | 3.4 (2–7) / 1.8 (1–4) | 2.6 (1–5) / 1.6 (1–3) | 53.0 (53) | 16034.6 (16033–16036) / 301.8 (293–309) |

| Arm | Seeds | Seeded repairs accepted | Seeded repairs correct |
|---|---|---|---|
| gq3 | 26 | 20.0 (20) | 17.0 (17) |

## The gate

On the 45 shared items, first-try acceptance and correct answers, each pooled over the runs, at least edition 2's pooled total less one task per run (RFC `blocked_on` item 2, 2026-11-18).
Edition 2 on 2026-11-09, per run, for comparison only: accepted [43, 43, 43], correct by rows [39, 39, 40].

- `gq3`: accepted [41, 42, 43, 43, 43] (pooled 212), correct by rows [39, 40, 41, 41, 41] (pooled 202); planner gaps [0, 0, 0, 0, 0], not supported yet (T86) [0, 0, 0, 0, 0]. Edition 2 (results/2026-11-19-edition2: the edition-2 arm of fork branch gq3/phase-0-instrument at cb0f4634, judged by its own engine): accepted [43, 44, 43, 43, 43] (pooled 216), correct [38, 38, 39, 41, 40] (pooled 196); margin 5. Acceptance 212 ≥ 211: yes; correctness 202 ≥ 191: yes. Verdict: **pass**.

## By task

First answers over the runs per arm: `c` correct, `g` planner gap, `t` refused as not supported yet (T86), `w` wrong rows, `e` engine error, `r` refused, `u` unjudged. Rows where the arms differ or either is not correct every run.

| Task | Set | Shared | Kind | gq3 | where |
|---|---|---|---|---|---|
| a03 | corpus | yes | filter on an edge property | crccc |  |
| a06 | corpus |  | an aggregate of an aggregate | uuuuu |  |
| a07 | corpus | yes | filter on a sum | rcccc |  |
| a08 | corpus |  | a top-1 read feeding a traversal | rcccc |  |
| r01 | corpus | yes | lexical ranking with the score | rcccc |  |
| r03 | corpus | yes | a vector window over a traversal's population | crrrr |  |
| r04 | corpus |  | a lexical cut across the corpus, then a traversal | uwuuu |  |
| m1 | tasks |  | block as a value (count plus projection) | uuuuu |  |
| m2 | tasks |  | count block compared with another binding's value | uuuuu |  |
| m4 | tasks |  | reuse of a declared read with arguments | ruuuu |  |
| m5 | tasks |  | a window over documents, then a traversal from the hits | uuuuu |  |
| m6 | tasks |  | window versus population | uuuuu |  |
| t02 | tasks | yes | comparison filter | wwwww |  |
| t07 | tasks |  | interface binding | uuuuu |  |
| t12 | tasks |  | unbounded reachability | uuuuu |  |
| t17 | tasks |  | aggregate of an aggregate | rrrru |  |
| t20 | tasks |  | outer join | uuuuu |  |
| t21 | tasks |  | union of two reads | uuuuu |  |
| t22 | tasks |  | correlated subquery per group | uuuuu |  |
| t23 | tasks |  | parameterised limit with a default | uuuuu |  |
| t26 | tasks |  | every document within a distance | uuuuu |  |
| t30 | tasks |  | hybrid, weighted | rrtrr |  |
| t31 | tasks |  | declared recall | uuuuu |  |
| t33 | tasks | yes | delete by predicate | rcccc |  |
| t35 | tasks |  | write with a computed value | uuuuu |  |
| t36 | tasks |  | write whose targets come from a ranked read | uuuuu |  |
| t37 | tasks | yes | unattached branch snapshot | wwwww |  |
| t38 | tasks | yes | the engine catalogue | rrrrr |  |

## Every first answer that is not correct

| Arm | Run | Task | Outcome | Code or reason |
|---|---|---|---|---|
| gq3 | 1 | a06 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 1 | a07 | refused | T84 |
| gq3 | 1 | a08 | refused | T84 |
| gq3 | 1 | m1 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 1 | m2 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 1 | m4 | refused | T68 |
| gq3 | 1 | m5 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 1 | m6 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 1 | r01 | refused | T83 |
| gq3 | 1 | r04 | unjudged | several queries: the glue between them is prose; judge by hand |
| gq3 | 1 | t02 | wrong-rows | rows differ from the expected rows on the seed |
| gq3 | 1 | t07 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 1 | t12 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 1 | t17 | refused | T84 |
| gq3 | 1 | t20 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 1 | t21 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 1 | t22 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 1 | t23 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 1 | t26 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 1 | t30 | refused | T68 |
| gq3 | 1 | t31 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 1 | t33 | refused | Q001 |
| gq3 | 1 | t35 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 1 | t36 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 1 | t37 | wrong-rows | rows differ from the expected rows on the seed |
| gq3 | 1 | t38 | refused | Q001 |
| gq3 | 2 | a03 | refused | T83 |
| gq3 | 2 | a06 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 2 | m1 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 2 | m2 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 2 | m4 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 2 | m5 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 2 | m6 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 2 | r03 | refused | T84 |
| gq3 | 2 | r04 | wrong-rows | rows differ from the expected rows on the seed |
| gq3 | 2 | t02 | wrong-rows | rows differ from the expected rows on the seed |
| gq3 | 2 | t07 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 2 | t12 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 2 | t17 | refused | T84 |
| gq3 | 2 | t20 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 2 | t21 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 2 | t22 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 2 | t23 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 2 | t26 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 2 | t30 | refused | T68 |
| gq3 | 2 | t31 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 2 | t35 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 2 | t36 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 2 | t37 | wrong-rows | rows differ from the expected rows on the seed |
| gq3 | 2 | t38 | refused | Q001 |
| gq3 | 3 | a06 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 3 | m1 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 3 | m2 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 3 | m4 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 3 | m5 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 3 | m6 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 3 | r03 | refused | T84 |
| gq3 | 3 | r04 | unjudged | several queries: the glue between them is prose; judge by hand |
| gq3 | 3 | t02 | wrong-rows | rows differ from the expected rows on the seed |
| gq3 | 3 | t07 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 3 | t12 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 3 | t17 | refused | T84 |
| gq3 | 3 | t20 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 3 | t21 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 3 | t22 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 3 | t23 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 3 | t26 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 3 | t30 | not-yet | T86 |
| gq3 | 3 | t31 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 3 | t35 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 3 | t36 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 3 | t37 | wrong-rows | rows differ from the expected rows on the seed |
| gq3 | 3 | t38 | refused | Q001 |
| gq3 | 4 | a06 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 4 | m1 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 4 | m2 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 4 | m4 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 4 | m5 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 4 | m6 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 4 | r03 | refused | T84 |
| gq3 | 4 | r04 | unjudged | several queries: the glue between them is prose; judge by hand |
| gq3 | 4 | t02 | wrong-rows | rows differ from the expected rows on the seed |
| gq3 | 4 | t07 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 4 | t12 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 4 | t17 | refused | T84 |
| gq3 | 4 | t20 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 4 | t21 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 4 | t22 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 4 | t23 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 4 | t26 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 4 | t30 | refused | T68 |
| gq3 | 4 | t31 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 4 | t35 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 4 | t36 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 4 | t37 | wrong-rows | rows differ from the expected rows on the seed |
| gq3 | 4 | t38 | refused | Q001 |
| gq3 | 5 | a06 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 5 | m1 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 5 | m2 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 5 | m4 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 5 | m5 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 5 | m6 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 5 | r03 | refused | T84 |
| gq3 | 5 | r04 | unjudged | several queries: the glue between them is prose; judge by hand |
| gq3 | 5 | t02 | wrong-rows | rows differ from the expected rows on the seed |
| gq3 | 5 | t07 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 5 | t12 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 5 | t17 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 5 | t20 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 5 | t21 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 5 | t22 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 5 | t23 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 5 | t26 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 5 | t30 | refused | T68 |
| gq3 | 5 | t31 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 5 | t35 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 5 | t36 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 5 | t37 | wrong-rows | rows differ from the expected rows on the seed |
| gq3 | 5 | t38 | refused | Q001 |

## Seeds by code

| Arm | Seed | Code | Class | Accepted | Correct |
|---|---|---|---|---|---|
| gq3 | e3-01 | T7 | infix `contains` and `starts_with` (edition 2's spelling) | 5/5 | 5/5 |
| gq3 | e3-02 | T7 | `contains` over a list property (edition 2's spelling) | 5/5 | 5/5 |
| gq3 | e3-03 | T6 | a positional option (edition 2's `fuzzy` budget) | 5/5 | 5/5 |
| gq3 | e3-04 | T65 | a ranking in `order` (edition 2's spelling) | 5/5 | 5/5 |
| gq3 | e3-05 | T65 | a ranking in `return` and `order` (edition 2's spelling) | 5/5 | 5/5 |
| gq3 | e3-06 | Q008 | `delete T where` (edition 2's spelling) | 5/5 | 5/5 |
| gq3 | e3-07 | Q008 | `update T set … where` (edition 2's spelling) | 5/5 | 0/5 |
| gq3 | e3-08 | Q014 | `having` (SQL) | 0/5 | 0/5 |
| gq3 | e3-09 | Q014 | `optional` without braces (Cypher) | 0/5 | 0/5 |
| gq3 | e3-10 | Q012 | `CASE … END` (SQL) | 5/5 | 5/5 |
| gq3 | e3-12 | Q010 | a comparison inside binding braces | 5/5 | 5/5 |
| gq3 | e3-13 | Q002 | a declaration without its parameter list | 5/5 | 5/5 |
| gq3 | e3-14 | Q009 | the `mutation` keyword (GraphQL) | 5/5 | 5/5 |
| gq3 | e3-15 | Q013 | a write's elements written bare, without `match { }` (GQ 3's earlier write body) | 5/5 | 0/5 |
| gq3 | e3-17 | Q017 | `where` (SQL, Cypher) inside the `match` braces | 5/5 | 5/5 |
| gq3 | e3-18 | Q007 | an arrow traversal (Cypher) | 5/5 | 5/5 |
| gq3 | e3-19 | Q012 | `let … in` (a row-binding `let`) | 5/5 | 0/5 |
| gq3 | e3-20 | Q014 | `count(*)` (SQL) | 0/5 | 0/5 |
| gq3 | e3-21 | T7 | a `null` literal in a read | 5/5 | 5/5 |
| gq3 | e3-22 | Q014 | an uppercase keyword | 5/5 | 5/5 |
| gq3 | e3-23 | T8 | an aggregate of an aggregate in one `return` | 0/5 | 0/5 |
| gq3 | e3-24 | T68 | two rankings drawn into one scope | 0/5 | 0/5 |
| gq3 | e3-25 | T17 | `nearest` without `k` | 5/5 | 5/5 |
| gq3 | e3-26 | Q014 | `union` between two reads (SQL) | 0/5 | 0/5 |
| gq3 | e3-27 | Q014 | single-quoted strings and `==` | 5/5 | 5/5 |
| gq3 | e3-29 | T74 | a drawn column that is neither a variable nor aliased | 5/5 | 5/5 |
