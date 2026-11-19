# M0 re-run, 2026-11-19, re-judged: t27 and r01 judged by their own rows

The 2026-11-19 M0 run (`results/2026-11-19/`) judged every GQ 3 answer to
t27 and r01 wrong however it was written: their expected rows were edition
2's, whose `bm25` scores are Lance's `F32`, while the GQ 3 RFC types a
`bm25` score as `bm25_v1`'s `F64`. Their texts were kept as `edition3
disputed`. This directory is the same run judged again: no agent call was
made (`run.py rejudge results/2026-11-19 results/2026-11-19-rejudged`), so
the prompts, replies, extracted answers, agent figures and the card digests
in `meta.json` are the run's; only each record's verdict is new.

## What changed before judging

- **t27 and r01 have rows from their first edition-3 truth**, recorded on
  #1316's head (`bbc418b6`, merged into this branch) and marked
  `--- expect ordered edition3`; their texts are truths again. Against the
  edition-2 rows they replace: t27 returns the same three documents in the
  same order, and each `F64` score rounds to edition 2's `F32` score exactly
  (2.0232521362860822 to 2.0232522, 1.0116260681430411 to 1.0116261,
  0.9156193762630337 to 0.9156194). r01 returns the same three documents in
  the same order, and each score rounded to `F32` is one `F32` place from
  edition 2's (0.7411201885074448 against 0.7411202, 0.7186620009769161
  against 0.71866196, 0.6587735008955066 against 0.65877354), since
  `bm25_v1` computes the score in `F64`. So the rows equal the recorded ones
  up to the score's width, exactly for t27 and within one `F32` place for
  r01.
- **The judge is #1316's head.** It accepts the spellings p24 admitted
  (`case … end` in any letter case, `||`, `<>`, single quotes, keywords in
  any letter case), so two seeds are refusals no more and leave
  `seeds/gq3.seeds` (e3-10 and e3-22); the run's seeded records for them
  stay, judged like the others.
- **The card** is regenerated from #1316's skill (`cards/gq3.md`, its pin in
  `manifest.json`), since the card test requires it. The run's agents read
  the previous card; nothing here asks the agent again.

## What changed in the verdicts

Every verdict that moved, of 553 records:

| Condition | Task | Moved | Records |
|---|---|---|---|
| first answer | t27 | wrong to correct | 5 (runs 1 to 5) |
| first answer | r01 | wrong to correct | 4 (runs 2 to 5; run 1 is refused, `T83`) |
| repair with the card | r01 | wrong to correct | 1 (run 1) |
| repair without the card | r01 | wrong to correct | 1 (run 1) |
| seeded repair | t27 (seed e3-05) | wrong to correct | 5 |

No acceptance moved: every answer the run's judge accepted, this one
accepts, and every refusal stays a refusal.

## The gate

On the 45 shared tasks, judged by rows on the seed, five runs per edition:
GQ 3's first-try acceptance and its correct answers, each pooled over the
runs, at least edition 2's pooled total less 5. The edition-2 numbers and the
margin are the run's (`edition2.json`, the edition-2 arm of fork branch
`gq3/phase-0-instrument` at `cb0f4634`).

| Arm | Run 1 | Run 2 | Run 3 | Run 4 | Run 5 | Pooled |
|---|---|---|---|---|---|---|
| GQ 3 accepted | 41 | 42 | 43 | 43 | 43 | **212** |
| Edition 2 accepted | 43 | 44 | 43 | 43 | 43 | 216 |
| GQ 3 correct | 39 | 40 | 41 | 41 | 41 | **202** |
| Edition 2 correct | 38 | 38 | 39 | 41 | 40 | 196 |

| Scope | Acceptance | Correctness | Verdict |
|---|---|---|---|
| All 45 shared tasks | 212 ≥ 216 − 5 = 211: met, by one task | 202 ≥ 196 − 5 = 191: met | **pass** |

Every shared task's truth now runs and returns its recorded rows, so the
RFC's second scope (the shared tasks whose truths run) is the same 45 tasks.
Against the run as first judged, GQ 3's correct count rises by 9 (193 to
202), the figure the run's analysis gave for a comparison within a relative
tolerance of 1e-6; acceptance is unchanged at 212, one task above the margin.

Seeded repairs, from the diagnostic alone, over the run's 26 seeds: accepted
20 in each run (100 / 130), correct 17 in each run (85 / 130); t27's seed
(e3-05) is now correct.
