# Analysis of the 2026-11-11 run

`summary.md` beside this file is generated from `records.jsonl` by
`summary.py`; this page reads it. Model `claude-sonnet-5-5`, Claude Code
2.1.316, cards `gq3.md` `9aa2fd3cea11` and `where.md` `2637d4a75c66`,
instrument at `d18bb217` on fork branch `gq3/variant-match-where`, over PR
1316's head `ae9c2a42`. Three runs per arm, the same 75 items, repair turns
and prompts as the 2026-11-09 run: 636 agent calls, $5.28 at list price.

What changed from 2026-11-09: both arms are judged by rows on the seed, on
#1316's engine, as edition 2 was then. Edition 2 is deleted on #1316, so its
2026-11-09 numbers are the baseline: on the 45 shared items, accepted
[43, 43, 43] and correct by rows [39, 39, 40].

## The numbers on the 45 shared items, per run

| | gq3 | where |
|---|---|---|
| First try accepted | 45, 45, 45 | 44, 44, 42 |
| First try correct by rows | 31, 30, 31 | 31, 32, 30 |
| Planner gaps (accepted, `P006` at run time) | 10, 11, 10 | 11, 11, 11 |
| Gaps whose core IR equals a ground truth's | 3, 4, 3 | 2, 1, 1 |
| IR-equal (all answers) | 24–25 | 21–22 |

## The gate

- **gq3: fails.** Acceptance 45 against 43 passes; correctness 30.7 against
  39.3 fails.
- **where: fails.** Acceptance 43.3 against 43 passes; correctness 31.0
  against 39.3 fails.

Neither failure is a language failure as the instrument separates them. Of
the shared items the arms miss, almost all are planner gaps: #1316's planner
does not lower what both arms write for `a02`, `a07`, `s03`, `s08`, `t18`,
`t24`, `t25`, `t27`, `t33`, `t34` (and `r01`, `m3` in one arm). Five limits
account for every gap in the run:

| What the planner does not lower | Tasks (both arms unless marked) |
|---|---|
| a `Project` node in a pattern (`let $x = count { … }`, `from { … return { …, count(…) } }` then a filter, a block value per row) | a02, a07, a08, m3 (where), r01, r04, r10, s03, s04, s08, t18, t27 |
| a write group whose clauses are more than a filtered scan of its target | a05, a09, r07, r11, s06, t33, t34 |
| a `nearest` window `k` other than the read's `limit` | t24, t25, r03 (gq3, once) |
| an `ApproxTopK` node in a pattern (a traversal after a window) | r02 |
| an `order` over a ranking that does not lead with its score | t25 (where; gq3 once), r01 (where, once) |

Counting a gap whose core IR equals a ground truth's as correct, the arms
reach 34, 34, 34 (gq3) and 33, 33, 31 (where): still below edition 2. That
bound is low because most gapped answers are other spellings of the truth,
not wrong ones; the truth spellings themselves gap (see the truth check), so
rows cannot settle them on this engine.

## Truth check (step 1)

Every edition-3 ground truth, alternatives included (98), is accepted by
#1316's front door. Run on #1316's engine against the recorded rows:

- 50 return exactly the recorded rows; **no truth returns different rows.**
- 24 are planner gaps (`P006`), and `t28`'s second truth fails at run time
  with "`match_terms` reaches no evaluator; the planner does not lower it",
  a refusal without a code.
- 23 belong to the 20 tasks with no edition-2 rows. For those, the first
  truth was run to record rows: `t08` and `t09` were recorded
  (`--- expect … edition3`); the other 18 (`a06`, `m1`, `m2`, `m4`, `m5`,
  `m6`, `t07`, `t12`, `t17`, `t20`, `t21`, `t22`, `t23`, `t26`, `t30`,
  `t31`, `t35`, `t36`) gap, so they have no expected rows and every answer
  to them is unjudged (54 per arm). None is a shared item.
- On the shared items, `r03` has no truth that runs; `a07`, `m3`, `s08`,
  `t24`, `t25`, `t33`, `t34` run only through an alternative truth.

`t09`'s recorded rows give Carol, whose age is null, the band `active`:
`if(null >= 65, …)` takes the else branch on #1316. That is the engine's
meaning, recorded as is.

## Wrong first answers

| Class | gq3 | where |
|---|---|---|
| Planner gap | 61 | 61 |
| Syntax misuse (refused) | 0 | 6: `t29` ×2 and `a09`, `s03` (`Q001`), `r05` ×2 (`T6`) |
| Wrong rows: a column the task did not ask for | 9: `t02`, `t29`, `r05` ×3 | 4: `t02` ×3, `r05` |
| Wrong rows: a draft and a final declaration with one name | 3: `m3` ×3 | 1: `r04` |
| Ground-truth problem | 0 | 0 |
| Unjudged (no expected rows) | 54 | 54 |

The extra columns are the same miss edition 2 made on `t02` on 2026-11-09
(`$p.age`), and `$score` on `t29` and `r05`. The draft-and-final answers
declare one query name twice; #1316's front door accepts that, and the
engine runs the first declaration of the name, which is the draft. The
variant's syntax misuses are its own: `let` written before `match` at the
top of the body (`t29`), a block's `where` written after the block's
braces instead of inside them (`s03`), a typed endpoint `$s: Source` inside
a triple in a draft (`a09`), and a draft missing its `let` (`r05`). None of
the 7 refused first answers was correct after one repair turn: the repairs
land on the extra `$score` or on a planner gap.

## Seeds

Each arm's 29 seeds were repaired 87 times (three runs) from the diagnostic
alone: 75 accepted in both arms; judged by rows, gq3 54 correct, 6 gaps, and
where 53 correct, 7 gaps; 15 accepted repairs per arm are of tasks with no
expected rows. Neither arm repaired `count(*)`, the aggregate of an
aggregate, two rankings in one scope or SQL's `union` from the diagnostic
alone, as on 2026-11-09. The variant seeds are the GQ 3 seeds converted;
`w-15` (a write's clauses written bare, the inverse of GQ 3's `match { }`
around a write body) and `w-17` (`where` inside the braces, `Q020`) keep the
variant's own form of their class.

## Reading

On #1316's engine the gate cannot pass for either arm on correctness until
the planner lowers at least `Project` in a pattern and write groups over a
traversal: on the shared items both arms answer in forms the front door
accepts and the planner refuses about 11 times per run. Between the arms the
numbers are within run-to-run noise: the variant's correctness by rows is
31.0 against gq3's 30.7, and its acceptance 43.3 against 45.0, the gap being
the variant's own syntax misuses above.
