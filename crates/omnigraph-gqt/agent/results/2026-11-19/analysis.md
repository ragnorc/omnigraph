# M0 re-run, 2026-11-19: the competence gate on #1316's head

Stage M0 of the GQ 3 RFC (`docs/rfcs/2026-11-07-gq-3.md`, fork branch
`rfc/gq-3` at `c963461b`, `blocked_on` item 2, the M0 row of section 11 and
the 2026-11-18 decision-log entries), re-run after the p23 runs on #1316,
which added the write statements `insert`, `upsert` and `replace`. This is a
measurement: the gate's margin was fixed before the run and is not changed
here.

## What was measured

| | GQ 3 | Edition 2 |
|---|---|---|
| Engine and front end | #1316's head `a1b89621` (`gq3/phase-0-front-end` when the run started, 2026-11-19 05:29:58 UTC), fixed for the whole run | fork branch `gq3/phase-0-instrument` at `cb0f4634`, on its own engine |
| Instrument | this branch, `46b39dfe` (`results/2026-11-19/`) | that branch's edition-2 arm, unchanged (`results/2026-11-19-edition2/`) |
| Card | `cards/gq3.md`, generated from `skills/omnigraph` at `a1b89621`: 35,828 bytes, SHA-256 `6608ff7720fe3eb063ef8578e3271b6557ac28011d9b99b09b8674d7e68145e5` | `cards/edition2.md`, generated from main's `skills/omnigraph`: 17,682 bytes, SHA-256 `0f791365b296977e01908105f82e9233c723f3f3194cad784dbee8e4eb0f2379`; it regenerates byte for byte from the skill at `cb0f4634` and at today's main `fd4d61f6` |
| Sections | both cards: the preface, then SKILL.md's `` ### `.gq` grammar `` and references/queries.md's Query Structure, System Fields and Result Values, Search Functions, Aggregations, Filter Operators and Mutations, whole, by the same code (`src/agent/card.rs`) | same |
| Model | `claude-sonnet-5-5`, every one of 553 calls; `claude` 2.1.328 | same, 531 calls |
| Runs | 5, fresh (`run.py run --arms gq3 --runs 5`, one run per invocation with `--resume`) | 5, fresh (`run.py run --editions 2 --runs 5`, likewise) |

`meta.json` is rewritten by each resumed invocation, so its `started` is
run 5's; run 1 of GQ 3 started at 08:12:20 UTC and run 1 of edition 2 at
08:19:56 UTC. One Claude account served every call; no usage limit was hit
and nothing was resumed on another account.

## The gate

On the 45 shared tasks, judged by rows on the seed, five runs per edition:
GQ 3's first-try acceptance and its correct answers, each pooled over the
runs, at least edition 2's pooled total less 5.

| Arm | Run 1 | Run 2 | Run 3 | Run 4 | Run 5 | Pooled |
|---|---|---|---|---|---|---|
| GQ 3 accepted | 41 | 42 | 43 | 43 | 43 | **212** |
| Edition 2 accepted | 43 | 44 | 43 | 43 | 43 | 216 |
| GQ 3 correct | 38 | 38 | 39 | 39 | 39 | **193** |
| Edition 2 correct | 38 | 38 | 39 | 41 | 40 | 196 |

| Scope | Acceptance | Correctness | Verdict |
|---|---|---|---|
| All 45 shared tasks, as judged | 212 ≥ 216 − 5 = 211: met, by one task | 193 ≥ 196 − 5 = 191: met | **pass** |
| The 43 shared tasks whose truths run and return the recorded rows (without t27 and r01) | 203 ≥ 206 − 5 = 201: met | 193 ≥ 186 − 5 = 181: met | **pass** |

No GQ 3 answer to a shared task hit `P006` or a `T86` "not supported yet",
and every shared task's truth runs on this head, so the RFC's two scopes
(all 45; those whose truths run) differ only by t27 and r01, whose texts
run but whose rows differ from the recorded ones by float width (below).
For comparison only: compared within a relative tolerance of 1e-6, GQ 3's
answers to t27 (5 of 5) and r01 (4 of 5) return the recorded rows, which
would make its pooled correct count 202. The judge was not changed for this.

The 2026-11-09 edition-2 numbers (accepted 43, 43, 43; correct 39, 39, 40)
are a comparison only; the fresh edition-2 run is in line with them.

## Per arm

Pooled over the five runs; shared is the 45 tasks, all the 75.

| | GQ 3 shared | GQ 3 all | Edition 2 shared | Edition 2 all |
|---|---|---|---|---|
| First try accepted | 212 / 225 | 351 / 375 | 216 / 225 | 342 / 375 |
| First try correct (rows) | 193 | 247 | 196 | 196 |
| Planner gaps (`P006`) | 0 | 0 | 0 | 0 |
| Refused as not supported yet (`T86`) | 0 | 1 (t30, run 3) | - | - |
| Unjudged | 0 | 84 | 10 | 136 |
| IR-equal with a truth | 123 | 150 | 137 | 137 |
| Refused first answers | 13 | 24 | 9 | 33 |
| Repair with card: accepted / correct | 12 / 7 | 17 / 8 | 6 / 2 | 30 / 2 |
| Repair without card: accepted / correct | 8 / 6 | 13 / 7 | 0 / 0 | 10 / 0 |
| Mean tokens in / out, first call | 16,027 / 190 | 16,035 / 302 | 9,303 / 169 | 9,311 / 316 |

Edition 2's IR-equal is against its own ground truths, and an edition-2
answer of several queries is unjudged. GQ 3's 84 unjudged answers are the
answers to the 16 tasks whose truths now run but that have no recorded rows
(m1, m2, m4, m5, m6, t07, t17, t20, t21, t22, t23, t26, t31, t35, t36, a06;
`record --check` lists them) and to t12 and t30. Their rows were not
recorded in this measurement, so they stay unjudged; none is a gate task.

Seeded repairs, from the diagnostic alone (no card, no schema):

| Arm | Seeds | Accepted per run | Correct per run |
|---|---|---|---|
| GQ 3 | 26 | 20 in each run (100 / 130) | 16 in each run (80 / 130) |
| Edition 2 | 18 | 17 in each run (85 / 90) | 17 in each run (85 / 90) |

Of GQ 3's 26 seeds, 20 are repaired into an accepted answer every run. The
six never repaired are e3-08 (`having`; the repair forgets to bind `$p`
inside the inline read, `T84`), e3-09 (`optional` without braces; `T84`),
e3-20 (`count(*)`; the repair, written without the schema, drops a comma,
`Q016`), e3-23 (an aggregate of an aggregate; the repair invents `with`),
e3-24 (two rankings in one scope) and e3-26 (`union` between reads; the
repair returns bare names, `Q018`). Four accepted seeds cannot be correct by
construction: e3-05 is t27 (the float width below), and e3-07, e3-15 and
e3-19 are t35 and t22, which have no recorded rows.

## Truths on this head

- No edition-3 truth hits `P006`.
- Three truths are refused with `T86` as forms this release does not
  support yet, none of them on a shared task: t12 (a read that calls itself,
  `Fix`), t30 (a `Score` node in a pattern) and r04 (a ranking inside an
  inline read with its own `order` and `limit`).
- t27 and r01 (both shared) return the recorded documents in the recorded
  order, but `bm25` scores are F64 on this head and the rows recorded from
  edition 2 hold F32 scores (2.0232521362860822 against 2.0232522); a few
  scores also differ in the last F32 place, since the score is now computed
  in F64. Their texts are kept as `--- edition3 disputed`, not truths, and
  the rows are not edited. Every GQ 3 answer to these two tasks is therefore
  judged wrong however it is written: a judge or truth defect.
- Every other truth that has recorded rows returns them (73 of 73).

## Skill gaps

What the 2026-11-11 hand-written card taught and the card generated from
the skill does not:

| Not in the card's sections | Where the skill has it | Misses it explains |
|---|---|---|
| The declaration suffixes `as commit` and `as unattached branch` | only named in SKILL.md's Common Gotchas and in references/migrations.md, never shown as syntax | t37, 5 of 5 (edition 2's card lacks it too: 5 of 5) |
| The engine catalogue: `$o: @Operator` and `@Pass`, `@Setting`, `@Engine`, `@LogicalKind` | nowhere | t38, 5 of 5 (edition 2: 5 of 5) |
| `distance(rel, prop, q)`, the exact ranking of every row, and the radius idiom | references/search.md | none on this run |
| `nearest`'s `recall:` and `rrf`'s `weights:` | references/search.md | none |
| `concat` | nowhere | none |
| A read that calls itself through `union` (recursion) | named only as a `T86` trap | none (`T86` on this head) |
| "Grouping drops empty groups; a block does not" | nowhere | none |
| Calling one helper twice, renaming the first call's column | nowhere | none |

## Every GQ 3 miss on the shared tasks

32 first answers to shared tasks were not correct (13 refused, 19 with
other rows); planner gaps 0.

| Cause | Category | Misses | Example |
|---|---|---|---|
| `bm25` scores in F64 against the F32 scores recorded from edition 2 | judge or truth defect | 9: t27 runs 1 to 5, r01 runs 2 to 5 | t27 run 2: the three recorded documents in order, `2.0232521362860822` against `2.0232522` |
| A name new to an inline read used unbound there (`T84`), the population written `{ match { $p: Person, $p knows $d } … }` | language difficulty | 5: r03 runs 2 to 5, a07 run 1 | r03 run 2; the diagnostic is enough: every repair, with or without the card, is accepted and correct |
| The engine catalogue is in no part of the skill | skill gap | 5: t38 runs 1 to 5 | t38 run 1: the agent answers in prose that no type exposes operators |
| `as unattached branch` is not in the card | skill gap | 5: t37 runs 1 to 5 | t37 run 1: `insert Person { handle: $h, name: $h }` writes to the branch |
| An extra `$p.age` column beside the names, where the task says "List the names … oldest first" | judge or truth defect (the task's wording and the judge's column count, not the language: edition 2 misses it 4 of 5) | 5: t02 runs 1 to 5 | t02 run 1: `return { $p.name, $p.age }` |
| Two alternative spellings sent as two declarations of one name (`T83`) | language difficulty (hedging between two spellings the card leaves open) | 2: a03 run 2, r01 run 1 | a03 run 2: the same read with and without `$t: Touchpoint` |
| A typed end inside a triple, `$p $k:worksAt $c: Company` (`Q001`) | language difficulty | 1: t33 run 1 | t33 run 1 |

The five most common causes, by count: the `bm25` float width (9), the
inline-read binding rule (5), the undocumented engine catalogue (5), the
undocumented `as unattached branch` (5) and the extra column on t02 (5).
By category: judge or truth defect 14, skill gap 10, language difficulty 8,
diagnostic gap 0, planner gap 0. Edition 2's 29 misses on the same tasks:
t13 (5), t37 (5), t38 (5), a02 (5), t02 (4), s03 (2), r05 (2), a03 (1).

## How the instrument was ported

The GQ 3 arm of fork branch `gq3/instrument-rows` (`e448303b`) was
cherry-picked onto `a1b89621`, with three API fixes. Its card is now
generated from the skill (above). Every edition-3 truth, check and seed was
brought to the surface the RFC specifies and this head parses (`match { … }
where`, commas, bindings in blocks and inline reads, a ranked `from` named
with `as`, `match_terms`, `@src`/`@dst`), by applying the compiler's
machine-applicable fixes and reading every result; no intent, parameter or
expected row changed. The keyed creates (a04, t32, t37) take `insert` with
`replace`, the RFC's rewrite of edition 2's keyed insert, as an equal
second truth. Three seed classes are refusals no more on this head and are
dropped: a minus spaced on one side only (`2026 -$p.age`), a reserved edge
name written bare (`$d in $x`) and a space before a call's parenthesis
(`colleagues ($of)`).
