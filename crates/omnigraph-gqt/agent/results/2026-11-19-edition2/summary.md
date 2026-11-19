# Agent instrument results: 2026-11-19-edition2

Model `claude-sonnet-5-5`, 2.1.328 (Claude Code), git `cb0f4634a9896615b128828e438c0d3523d9d991`, started 2026-11-19T08:25:48+00:00.
Card digests: `edition2.md` `0f791365b296`, `edition3.md` `52014d3aa9ba`.
README.md beside the runner defines every column. Not a CI gate.

| Edition | Run | Set | First try accepted | First try correct | Correct, shared tasks | Unjudged | Several queries | Repair with card accepted | Repair without card accepted | Correct after repair | Tokens in | Tokens out |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2 | 1 | tasks | 38/45 | 22/24 | 22/24 | 14 | 3 | 7/7 | 2/7 | 23/26 | 9372 | 310 |
| 2 | 1 | corpus | 30/30 | 16/16 | 16/16 | 14 | 13 | - | - | 16/16 | 9223 | 341 |
| 2 | 2 | tasks | 39/45 | 22/25 | 22/25 | 14 | 3 | 5/6 | 3/6 | 22/25 | 9372 | 305 |
| 2 | 2 | corpus | 30/30 | 16/16 | 16/16 | 14 | 13 | - | - | 16/16 | 9223 | 351 |
| 2 | 3 | tasks | 39/45 | 22/24 | 22/24 | 15 | 3 | 5/6 | 1/6 | 22/25 | 9370 | 305 |
| 2 | 3 | corpus | 30/30 | 17/17 | 17/17 | 13 | 13 | - | - | 17/17 | 9221 | 322 |
| 2 | 4 | tasks | 38/45 | 23/24 | 23/24 | 14 | 3 | 6/7 | 2/7 | 23/25 | 9371 | 313 |
| 2 | 4 | corpus | 30/30 | 18/18 | 18/18 | 12 | 11 | - | - | 18/18 | 9222 | 294 |
| 2 | 5 | tasks | 38/45 | 22/24 | 22/24 | 14 | 5 | 7/7 | 2/7 | 23/26 | 9372 | 323 |
| 2 | 5 | corpus | 30/30 | 18/18 | 18/18 | 12 | 11 | - | - | 18/18 | 9223 | 309 |

| Seeded repairs | Accepted | Correct |
|---|---|---|
| e2-r1 | 17/18 | 17/17 |
| e2-r2 | 17/18 | 17/17 |
| e2-r3 | 17/18 | 17/17 |
| e2-r4 | 17/18 | 17/17 |
| e2-r5 | 17/18 | 17/17 |

## Spread over runs

Counts per run; a range is the lowest and highest run. Shared tasks are those edition 2 expresses in one query, where both editions are judged automatically.

| Edition | Set | Tasks | First try accepted | First try correct | Shared: accepted | Shared: correct | Refused first | Repaired with card | Repaired without card | Seeded accepted | Seeded correct | Tokens in | Tokens out |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2 | tasks | 45 | 38–39 (mean 38.4) | 22–23 (mean 22.2) | 24–25 (mean 24.2) of 26 | 22–23 (mean 22.2) of 26 | 6–7 (mean 6.6) | 5–7 (mean 6.0) | 1–3 (mean 2.0) | - | - | 9370–9372 (mean 9371.4) | 305–323 (mean 311.2) |
| 2 | corpus | 30 | 30 | 16–18 (mean 17.0) | 19 of 19 | 16–18 (mean 17.0) of 19 | 0 | 0 | 0 | - | - | 9221–9223 (mean 9222.4) | 294–351 (mean 323.4) |
| 2 | all | 75 | 68–69 (mean 68.4) | 38–41 (mean 39.2) | 43–44 (mean 43.2) of 45 | 38–41 (mean 39.2) of 45 | 6–7 (mean 6.6) | 5–7 (mean 6.0) | 1–3 (mean 2.0) | 17 of 18 | 17 of 18 | 9310–9312 (mean 9311.4) | 306–323 (mean 316.2) |

## By task

First-try correct over the runs, per edition; `n/j` is correct of judged, `-` not judged automatically (no edition-2 truth, or an edition-2 answer of several queries; `q` is the most queries an edition-2 answer used). Rows where the editions differ, or where either edition was refused or wrong at least once.

| Task | Kind | Edition 2 | Edition 3 | Difference |
|---|---|---|---|---|
| m2 | count block compared with another binding's value | -, 5 refused |  |  |
| m6 | window versus population | -, 5 refused |  |  |
| t02 | comparison filter | 1/5 |  |  |
| t07 | interface binding | -, 5 refused |  |  |
| t08 | arithmetic | -, 3 refused |  |  |
| t13 | edge whose name is a reserved word | -, 5 refused |  |  |
| t17 | aggregate of an aggregate | -, 1 refused |  |  |
| t21 | union of two reads | -, 2 refused |  |  |
| t35 | write with a computed value | -, 3 refused, q2 |  |  |
| t37 | unattached branch snapshot | 0/5 |  |  |
| t38 | the engine catalogue | 0/1, 4 refused |  |  |

## Codes behind every repair

Every refused first answer, the code it was refused with, and whether one repair turn was accepted with the card and without it; then the seeded answers by code.

| Edition | Run | Task | Code | With card | Without card |
|---|---|---|---|---|---|
| 2 | 1 | m2 | T7 | accepted | accepted |
| 2 | 1 | m6 | T7 | accepted | accepted |
| 2 | 1 | t07 | T1 | accepted | refused (Q001) |
| 2 | 1 | t08 | Q001 | accepted | refused (Q001) |
| 2 | 1 | t13 | T7 | accepted, correct | refused (Q001) |
| 2 | 1 | t35 | Q001 | accepted | refused (Q001) |
| 2 | 1 | t38 | none | accepted, wrong | refused (T1) |
| 2 | 2 | m2 | T7 | accepted | accepted |
| 2 | 2 | m6 | T7 | accepted | accepted |
| 2 | 2 | t07 | T1 | accepted | refused (Q001) |
| 2 | 2 | t08 | Q001 | accepted | refused (Q001) |
| 2 | 2 | t13 | T7 | refused (T7) | refused (Q001) |
| 2 | 2 | t21 | T1 | accepted | accepted |
| 2 | 3 | m2 | T7 | accepted | refused (T1) |
| 2 | 3 | m6 | T7 | accepted | accepted |
| 2 | 3 | t07 | T1 | accepted | refused (Q001) |
| 2 | 3 | t13 | T7 | refused (T7) | refused (T4) |
| 2 | 3 | t35 | Q001 | accepted | refused (Q001) |
| 2 | 3 | t38 | Q001 | accepted, wrong | refused (T1) |
| 2 | 4 | m2 | T7 | accepted | accepted |
| 2 | 4 | m6 | T7 | accepted | accepted |
| 2 | 4 | t07 | T1 | accepted | refused (Q001) |
| 2 | 4 | t13 | T7 | refused (Q001) | refused (Q001) |
| 2 | 4 | t17 | T8 | accepted | refused (Q001) |
| 2 | 4 | t35 | Q001 | accepted | refused (Q001) |
| 2 | 4 | t38 | Q001 | accepted, wrong | refused (T1) |
| 2 | 5 | m2 | T7 | accepted | accepted |
| 2 | 5 | m6 | T7 | accepted | accepted |
| 2 | 5 | t07 | T1 | accepted | refused (Q001) |
| 2 | 5 | t08 | Q001 | accepted | refused (Q001) |
| 2 | 5 | t13 | T7 | accepted, correct | refused (T4) |
| 2 | 5 | t21 | T1 | accepted | refused (Q001) |
| 2 | 5 | t38 | Q001 | accepted, wrong | refused (T1) |

| Edition | Seed code | Class | Accepted | Correct |
|---|---|---|---|---|
| 2 | Q001 | a limit given as a parameter | 5/5 | 5/5 |
| 2 | Q001 | single-quoted strings | 5/5 | 5/5 |
| 2 | Q002 | a declaration without its parameter list | 5/5 | 5/5 |
| 2 | T1 | an unknown node type | 5/5 | 5/5 |
| 2 | T12 | an insert without a required property | 5/5 | 5/5 |
| 2 | T14 | an undeclared parameter in a write | 5/5 | 5/5 |
| 2 | T17 | nearest ordering without a limit | 5/5 | 5/5 |
| 2 | T22 | an undirected traversal over an edge between two types | 5/5 | 5/5 |
| 2 | T25 | two result columns with one name | 5/5 | 5/5 |
| 2 | T27 | a full-text call on an unindexed property | 5/5 | 5/5 |
| 2 | T33 | a projected rank that does not repeat the ordering | 5/5 | 5/5 |
| 2 | T4 | an unknown edge type | 5/5 | 5/5 |
| 2 | T42 | an order key that names nothing returned | 5/5 | 5/5 |
| 2 | T5 | a traversal with no bound end | 5/5 | 5/5 |
| 2 | T6 | an unknown property | 5/5 | 5/5 |
| 2 | T7 | a parameter of the wrong type in a comparison | 5/5 | 5/5 |
| 2 | T7 | a reserved edge name written bare | 0/5 | 0/5 |
| 2 | T9 | an uncorrelated negation block | 5/5 | 5/5 |
