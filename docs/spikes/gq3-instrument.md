# Spike: the GQ 3 competence instrument, run with a live agent

**Branch:** `spike/gq3-instrument`, from `spike/gq3-parser` `0162a330` (itself from `main`
`e7e8e03b`). This is throwaway prototype code.
**Specification:** `docs/rfcs/2026-11-07-gq-3.md` at `rfc/gq-3` `d79b6ea7`, with its assets
`2026-11-07-gq-3-before-and-after.md` and `2026-11-07-gq-3-grammar-comparison.md`.
**Question:** does the edition-3 spelling meet the RFC's `blocked_on` gate, "the in-context
competence instrument run on the edition-3 spelling through a parser-only build: first-try validity
at or above the current grammar's 37 of 45, and 45 of 45 after one repair; the six composition
tasks of the M0 corpus added and reported"?
**Agent under test:** Claude Code 2.1.316, `claude -p --model sonnet`, which reported the model
`claude-sonnet-5-5` on every one of the 324 recorded calls (eight pilot calls were discarded). Each call is a fresh headless turn in an empty
directory with `--tools ""`, `--safe-mode` (no CLAUDE.md, skills, memory or plugins) and
`--no-session-persistence`; no session is resumed between tasks. The card is the only GQ
reference the agent sees.

**Answer:** the gate passes, and it passes too easily to be a gate. Edition 3 was valid on the
first try for 45 of 45 tasks in each of three runs; edition 2 was valid for 44 of 45, and 45 of
45 after one repair. But a parser-only build accepts 11 of the 28 refused forms that I fed it
(finding 2), so the agents' near-perfect parse rate measures less than the RFC needs it to.
Semantic correctness is the number that moves. By tree or typed-IR equality with a hand-written
ground truth, edition 3 scored 27 to 29 of 45. Read by hand, 38 to 39 of 45 were correct, 5 or 6
were defensible alternatives, and 0 or 1 were wrong.

## (a) Findings

Each finding names the RFC section it bears on, what I observed (with the exact query or test),
and the change I propose. The numbers come from `docs/spikes/gq3-instrument/results.md`, which
`summarize.py` regenerates from the stored runs.

### The gate

**1. The gate's numbers: edition 3 meets both thresholds in every run.** (`blocked_on` item 2;
section 10, "The competence instrument"; section 11, stage M0.)

| | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| Edition 3, first-try valid (parser-only, `parse_query3`) | 45/45 | 45/45 | 45/45 |
| Edition 3, valid after one repair | 45/45 (no repair sent) | 45/45 | 45/45 |
| Edition 2, first-try valid (today's front door) | 44/45 | 44/45 | 44/45 |
| Edition 2, valid after one repair | 45/45 | 45/45 | 45/45 |
| Edition 3, tree-equal to a ground truth | 27/45 | 27/45 | 29/45 |
| Edition 3, correct when read by hand | 39/45 | 38/45 | 39/45 |
| Edition 2, typed-IR-equal to a ground truth | 20/26 | 21/26 | 22/26 |
| Edition 2, correct when read by hand | 25/26 | 24/26 | 25/26 |
| Edition 2, type-checks against the schema | 40/45 | 39/45 | 41/45 |

- **Spread.** The spread across runs is zero for validity and two tasks for semantic equality.
- **The one edition-2 refusal.** It is the same in all three runs: task `t08` (arithmetic). The
  agent wrote `$p.age * 2 >= $min` and got `Q001: expected clause, kw_and, kw_or, kw_is, kw_in,
  comp_op`. The repair dropped the arithmetic and moved it to the client, which is the right
  move.
- **The old failure did not recur.** The 37-of-45 baseline came from 8 first attempts that wrote
  `query name {` without `()` (RFC 0047, Motivation). That failure never happened here. Both
  cards say that an empty list needs parentheses, and the agent is a newer model.
- **Change.** The gate, as written, cannot fail on this agent. Restate it in three parts:
  - first-try *acceptance by the full edition-3 front end*, type checker included, once one
    exists;
  - semantic correctness on the shared tasks at or above edition 2's;
  - the seeded-repair rate of finding 8.

  Until the type checker exists, report the parser-only number beside the count of answers that
  the type checker would refuse (finding 2). Pin the agent model and the card text in the
  release-note line that section 11, rule 4, asks for.

**2. A parser-only build accepts 11 of the 28 refused forms, and 4 of those 11 are parse errors in
the RFC's own text.** (Section 3, "Refused respellings"; section 5; section 7, "Decisions the
prototype forced"; the spike parser at `0162a330`.)

I fed `seeds.py` one edition-3 answer per habit that the RFC refuses (`seeds/seeds.json`). The
parser-only build accepted these seeds:

- **Type-level by the RFC's design, so invisible to a parser-only gate:**
  - `order { nearest($d.embedding, $q) }` and `return { bm25(…) as s }` (`T65`);
  - `fuzzy($d.body, $q, 2)` (a positional option);
  - `$d in $x` over the reserved edge (`T7`);
  - `avg(count($p))`;
  - two `from` rankings that both bind `$d` (`T68`);
  - `nearest(…)` without `k` (`T17`).
- **Parse errors in the current RFC that the spike parser still accepts:**
  - `match { $p: Person  optional $p worksAt $c }`. `optional` is read as a bare-name filter.
  - `match { $p: Person  where count { $p knows $q } >= $min }`. `where` is read as a bare-name
    filter.
  - `return { …, 2026 -$p.age as born_about }`. It is read as a subtraction. Section 3 now
    refuses `$n -1`; the spike's F5 chose the opposite rule.
  - `from colleagues ($of)`, with a space before the parenthesis. It parses silently as three
    filter clauses, `from`, `colleagues` and `$of`; the printer gives
    `from  colleagues  $of`. Section 7 says that `from` before an identifier that does not touch
    `(` "is a parse error there". This one changes meaning without a refusal.

Change: bring the parser spike to the RFC's later rules before the gate is rerun ("a bare
identifier is not a value in a read", the asymmetric-minus refusal, and `from` and `let` refused
before a word that does not touch `(`). Add these four seeds to the red-team corpus. Name the
parser commit in the gate.

**3. With the card in context, no edition-2 habit leaked into an edition-3 answer.** (Section 3;
section 6, the rewriter rows.) The RFC is right here.
- **The habits checked.** None of the 135 edition-3 answers wrote:
  - a ranking in `order` or `return` (the checker's `T65` scan found none);
  - an infix `contains`;
  - a `where` on a statement;
  - a positional option.
- **The edition line.** All 135 answers wrote `gq 3;` themselves.
- **The writes.** Every write task was tree-equal to its ground truth in all three runs: delete
  by predicate, a write fed by a traversal, a computed value, and a write fed by a ranked read.
- **The quoted edge.** Every edition-3 run quoted the reserved edge (`$d "in" $x`). Edition-2
  runs wrote the bare `$d in $c` in tasks `t13`, `m2` and `m6`, and the type checker refused it
  (`T7`). That part is a difference between the cards: the edition-2 skill states the rule only
  in a schema comment.

### Card sentences the agents misread

**4. The `T68` sentence was read as "an alias on the inner `return` is enough".** (Section 3,
"Scope and correlation" and the `from` paragraph; section 5, `T68`; asset §4
`colleagues_of_colleagues`.)
- **Run 3 of `m4`.** It wrote
  `from colleagues($of)  from { match { from colleagues($q.handle) } return { $q as r } }`. The
  inner call binds `$q` while the outer `$q` is in scope, which section 5 makes a `T68` error.
  The agent had aliased the inner `return` as the card says, but the collision is inside the
  inner read. The parser-only build accepts it.
- **Run 1 of `m4`.** It found a legal form simpler than the asset's double wrapper:
  `from { match { from colleagues($of) } return { $q.handle as h1 } }  from colleagues($h1)`.
- **Change.** In section 3, state that a called read's columns enter the *inner* scope, so a
  helper called twice needs its first call wrapped, and show the pattern. Use run 1's form in
  the asset.

**5. "The exact top `n` is `distance` with `order` and `limit n`" made the agent choose the exact
scan for every "most similar" task.** (Section 4, "Vector retrieval"; asset §5 "Nearest window".)
- **Task `t24`** ("the 20 documents from 2025 or later that are most similar to `$q`"). All
  three runs wrote
  `from distance({ match { $d: Doc  $d.year >= 2025 } return { $d } }, $d.embedding, $q)  …  order { $distance }  limit 20`,
  not `from nearest(…, k: 20)`.
- **The cost.** Both are correct under the RFC, but the first is a full scan of the population.
  It is also not what the rewriter emits for the same intent, so stored text and agent text will
  diverge.
- **Change.** Decide in section 4 which form is the canonical answer to an unqualified top-k
  similarity request. If the answer is `nearest`, the card and the skill must say "use `nearest`
  for the k most similar; use `distance` only when the user asks for an exact answer or a
  radius", and say that `distance` scans.

**6. Agents prefer `let $n = count { … }` to grouping for "for each X", and the two forms differ
on empty groups.** (Section 2, `Group` against `Correlate`; section 3; asset §3 `headcount`,
`average_headcount` and `big_employers`; section 8 `big_employers`.)
- **The tasks.** For "for each company, its number of employees" (`t15`), 2 of 3 runs wrote
  `$c: Company  let $headcount = count { $p worksAt $c }`. For the mean and maximum headcount
  (`t17`), 3 of 3 did the same. The `M0` count-plus-projection task (`m1`) did it 3 of 3.
- **The difference.** The block form keeps companies that have no employees, at 0. The asset's
  grouped form drops them, so the asset's `average_headcount` leaves empty companies out of the
  mean.
- **Not an A6 violation.** These are different terms. It is the kind of population trap that
  section 4's laws exist for.
- **Change.** State the difference beside the derived-forms table. Use the block form where the
  question is "for each company" (`average_headcount`). Keep the GQT case "`big_employers` with
  an empty group" that section 10 lists.

### Diagnostics

**7. Generic `Q001` texts still name grammar rules, yet every seeded repair succeeded, because of
the card.** (Section 5: "A parse error names the construct, never a grammar rule", and the
foreign-word table.)
- **The seeds.** Seeded with the habits the RFC's table lists, the parser answered:

  | Habit | Diagnostic |
  |---|---|
  | `having { n >= $min }` | `Q001: expected kw_limit, kw_order` |
  | `CASE WHEN … END` | `expected kw_as, kw_and, kw_or, kw_is, kw_in, comp_op, not_expr3, infix_string_op, concat_bars, product_op` |
  | `count(*)` | `expected kw_distinct, not_expr3, call_args` |
  | `order { $p.age DESC }` | `… order_dir3 …` |
  | `match … return … union match …` | `expected kw_limit, kw_order` |
  | `'Berlin'` and `==` | `expected unary` |
  | a write body wrapped in `match { }` | `expected kw_return` |

  The RFC promises a fix for this last one.
- **The repairs.** One repair turn produced a valid answer in all 21 (7 × 3) cases, and 20 of
  those 21 equal a ground truth. The agent repaired from the card and the position, not from the
  text.
- **Change.** Build the foreign-word table and the `match {` recognizer before the gate.
- **Measuring the diagnostics alone.** Add a repair condition *without* the card (task, answer
  and diagnostic only), so that the instrument measures the diagnostics rather than the card.

**8. Two printed fixes are wrong.** (Section 5: "Every fix is a suggestion of byte-range edits";
the `where` fix "the bare names of the predicate and of the assignments qualified".)
- **The `null` literal.** `match { $p: Person  $p.city = null }` gives
  ``Q005: `null` is a reserved word; a property of that name is written `$p.null` ``. The fix
  points at a property named `null`; the intended form is `$p.city is null`.
- **The `where` fix.** `update Person set { age: age + 1 } where handle = $h` gives
  `Q008 … fix: $x: Person  $x.handle = $h  update $x set { age: age + 1 }`. The predicate's
  `handle` is qualified, but the assignment's `age` is not, so the printed fix is itself refused
  under section 3's bare-identifier rule.
- **What the agent did.** It repaired both, 3 of 3, because the card shows `is null` and
  `$p.age + 1`.
- **Change.** Fix both in the parser spike. The "every printed fix applied and reparsed" test of
  `blocked_on` item 3 would have caught the second.

**9. The seeded repairs overall.** (Section 5; `blocked_on` item 3.)
- **The refused seeds.** 17 of the 28 seeds were refused by the parser, with these codes:
  `Q012` (×3), `Q008` (×2), `Q001` (×7), `Q010`, `Q002`, `Q009`, `Q007` and `Q005`.
- **The repairs.** One repair turn produced a valid edition-3 answer in 51 of 51 cases (17 seeds
  × 3 repetitions).
- **Equality.** 46 of the 51 equal a ground truth. The other 5 are equivalent or defensible:
  they add `$p.age is not null`, add a column, re-alias a `from` column, or order the rows.
- **Machine-applicable fixes.** The fixes that carry a byte-range edit (`Q002`, `Q009`) and the
  fixes that print the whole legal text (`Q007`, `Q008`, `Q010`, `Q012`) were repaired exactly
  as printed.

### Forms the RFC does not expect

**10. Agents wrote non-canonical but legal forms that the formatter's list does not name.**
(Section 6, "`queries fmt`"; A6.) The forms, counted over the 135 edition-3 answers:

| Form | Count | Tasks |
|---|---|---|
| `from` columns re-aliased in `return` (`$headcount as n`, `$n as cites`) | 18 | `m1`, `m3`, `m5`, `t15`, `t30` |
| `$n as n`, which `fmt` drops | 6 | |
| `return distinct` | 12 | `m4`, `t11`, `t12`, `t19` (unasked in `t11` and `t19`) |
| a `limit k` after `nearest(…, k: k)` (redundant) | 8 | `m5`, `t25`, `t29` |

There are also four single forms:
- `order { $distance }` after a window when no order was asked for;
- node inequality through the key (`$q.handle != $p.handle`);
- `let $s = <expr>` and then `return { $s as s }`, beside the inline expression;
- a wrapper declaration around the recursive read (`reachable_names` calling `reachable`).

None of these is refused, and none changes rows except `distinct` over names. Change:
- Add the redundant `limit` after an equal or larger `k` to the formatter's list, or to lint.
- The M0 corpus's semantic check cannot be tree equality. These forms alone put edition 3 at
  27–29 tree-equal against 38–39 correct. Normalise through the formatter's list (the checker
  already applies "alias equal to its column dropped"), or compare rows on a fixture.

### Composition tasks (section 10's M0 additions)

**11. Edition 3 answered all six composition tasks in one query. Edition 2 needed two queries for
five of the six, and silently approximated where it could not.** (Section 10; Motivation, "the
two-step an agent writes today".)

| Task | Composition | Edition 3 (3 runs) | Edition 2 (3 runs) |
|---|---|---|---|
| `m1` | count plus projection (zero for no friends) | 1 query, 3/3 correct | 2 queries (connected people, then `not { … }` for the isolated ones, merged by the client) |
| `m2` | a group-specific threshold (`count { $d "in" $x } >= $x.min_docs`) | 1 query, 3/3 correct | 2 queries filtered by the client; all 3 also refused by `T7` (bare `in`) |
| `m3` | filtering grouped output (`>= $min`) | 1 query in runs 1 and 3; run 2 hedged with two declarations, the first unfiltered | 1 query, 3/3 correct with a count block (see finding 12c) |
| `m4` | reuse with parameters | 1 file, 2 declarations; run 3 has the `T68` collision of finding 4 | 2 independent declarations; the helper is inlined, "GQ has no way to call one query from another" |
| `m5` | a nearest window, then a citation count | 1 query, 3/3 correct | 2 queries: ids out of the first, `$d.id in $ids` into the second, on two snapshots |
| `m6` | window against population (the filter after the window) | 1 query, 3/3 correct | 2 queries, ids passed through the client |

The window-against-population law (section 4, law 1 and law 3) was read correctly in every
edition-3 run. `m6` put the collection filter after `from nearest(…)`, and `t24`, `t26` and
`t31` put the year filter inside the population. The RFC is right here.

### The before-and-after asset against today's compiler

**12. Six claims of the asset or of section 8 do not hold against today's parser and type
checker.** (The asset; sections 4 and 8.) I checked every ground truth with today's compiler
(`target/debug/examples/gq3_instrument`):
- **(a)** "Interface binding. Unchanged". Today's type checker refuses `match { $n: Named }` with
  ``T1: unknown node type `Named` ``, so edition 2 cannot express `t07`. The three edition-2 runs
  of `t21` used `$n: Named` with `$n.@type as kind` and got the same `T1`.
- **(b)** "Count per group. Unchanged" (`match { $p worksAt $c }`). Today's type checker refuses
  it with
  ``T5: traversal from `$p` to `$c` requires an executable source or destination node binding``
  unless it adds `$c: Company`. Section 8's `big_employers` and the asset's `average_headcount`
  use the same unbound pattern inside an inline read. Either edition 3 relaxes `T5` (say so in section 2), or the
  examples add the binding. The card and the ground truths add it.
- **(c)** "Filter on an aggregate. Not supported" in edition 2. With a literal or parameter
  threshold, edition 2 expresses it as
  `match { $c: Company  count { $q worksAt $c } >= $min  $p worksAt $c } return { $c.name, count($p) as n }`,
  and all three edition-2 runs wrote that form. The new capability is the comparison with a
  column (`m2`, `T40` lifted).
- **(d)** "A write whose targets come from a traversal. Not supported". `close_company` is
  `delete WorksAt where @dst = $slug` in edition 2, because the company's key is the edge's
  destination id. All three edition-2 runs wrote it, and it is typed-IR-equal to the ground
  truth. The read-fed write is new only where the target set is not an endpoint key.
- **(e)** The asset schema's `body: String @index(fulltext)` is not edition-2 `.pg`. Today's
  schema parser reads `@index(x)` on a property as a composite index over a property named `x`
  (`@index on Doc references unknown property 'fulltext'`). Section 4 says "A String
  `@index(fulltext)` written without `analyzer` keeps edition 2's index analysis", but edition
  2's spelling is `@index`. List `@index(fulltext, …)` as new schema syntax in the break, or
  write the edition-2 form as `@index`.
- **(f)** The asset's `mentions` runs `fuzzy` over `Doc.title`, which its schema does not index
  (`T27`). Task `t28` uses the body.

**13. Edition 2 accepts a ranking inside a filter at compile time and refuses it only at run
time.** (Motivation; section 4, the radius form.)
- **What the agents wrote.** For "documents within distance `$r`" (`t26`), runs 1 and 3 on
  edition 2 wrote `match { $d: Doc  $d.year >= 2025  nearest($d.embedding, $q) <= $r }`.
- **What today's compiler does.** It parses and type-checks this, and lowers it to IR.
- **What the engine does.** On a local graph, `omnigraph query` refuses it with
  `"unsupported expression in filter: nearest($d.embedding, $q)", "code": "bad_request"`, which
  carries no `Q`/`T` code and no fix.
- **The other silent approximations.** On the other tasks edition 2 cannot express, the agents
  wrote valid queries that answer another question:
  - unbounded reachability as `knows{1,20}`, `{1,50}` or `{1,100}`;
  - the outer join as an inner join;
  - weighted hybrid as `rrf`;
  - per-author top 3 as every document of every author.

  Most carried a comment that names the gap.
- **Change.** This is evidence for the RFC's motivation. Independently of the break, today's type
  checker should refuse a ranking call in a filter position with a typed code.

### Cost

**14. The edition-3 card is 12% shorter and its answers are shorter.** (Section 10, "tokens".)
- **The card.** The edition-3 card is 2,117 words, against 2,412 for the edition-2 card (the
  skill as it stands).
- **Tokens.** Input is 6,848 tokens per call, against 8,514. Output is 162 tokens per call,
  against 280; edition-2 answers spend their extra output explaining what the language cannot
  do.
- **Cost.** The six runs cost $1.29 (edition 3) and $1.80 (edition 2) at list price; the seeded
  repairs cost $0.49.

## (b) What I built, and how to run it

| Piece | Where | What it does |
|---|---|---|
| Checker | `crates/omnigraph-compiler/examples/gq3_instrument.rs` | JSON lines in, JSON lines out. Edition 2: today's front door (`parse_query_diagnostic`), then `typecheck_query_decl` and `lower_query`/`lower_mutation_query` against `schema2.pg`; it compares typed IR with each ground truth three ways: strict, renamed in source order, and renamed in IR order. Edition 3: `parse_query3`, a `T65` scan for ranking calls in `order` and `return`, the `bridge3` lowering and type check where the tree has edition-2 nodes, and tree equality after `print3` prints and reparses both sides with the formatter's alias and `asc` rules applied. Every diagnostic is rendered with its code, message, position, fix and edits, as the repair turn shows it. |
| Tasks | `docs/spikes/gq3-instrument/tasks/*.json` (from `make_tasks.py`) | 45 tasks over the section 8 schema: 39 kinds from the asset (the kind "Update and delete by predicate" kept as delete; "Added by later stages" and "Statements outside a declaration" left out) and the six M0 composition tasks (`m1`–`m6`), which replace the 5 asset kinds that they compose. Each task has one sentence of intent with the parameters named, the kind, the asset section, an edition-3 ground truth (with alternatives where two spellings are both canonical), an edition-2 ground truth where edition 2 can express the task (26 tasks), and notes. Every ground truth parses, and every edition-2 ground truth type-checks. |
| Cards | `cards/edition2.md`, `cards/edition3.md` | Edition 2 is the skill as it stands: `SKILL.md`'s `.gq` grammar section and `references/queries.md` from "Query Structure" to "Mutations", verbatim, without the CLI, JSON rendering and date-format paragraphs. Edition 3 is my rewrite from sections 3, 4 and 8, in the same order. Both open with the schema block. Both end with an identical "Also" line on `@Operator` (the skill omits the catalogue); the edition-2 card also has an "Also" line on `as commit` and `as unattached branch`. The edition-3 card has the three section 8 examples, with `$c: Company` added (finding 12b), and the recursion example in its rules. |
| Runner | `run.py` | One `claude -p` call per task; parse, then at most one fresh repair call; per-task records under `runs/e<edition>-r<run>/`. `--recheck` re-runs the checker on stored answers without calling the agent. |
| Seeds | `make_seeds.py`, `seeds.py`, `seeds/` | The 28 seeded answers of findings 2, 7, 8 and 9, and three repetitions of their repair turns. |
| Summary | `summarize.py` → `results.md`, `results.json` | The per-run, per-task, per-kind, repair-code, composition, adjudicated-correctness and seed tables. |
| Adjudication | `adjudication.json` | My verdict and reason for every answer that is not tree- or IR-equal to a ground truth. |

Run, from the repository root:

```bash
cargo build -p omnigraph-compiler --example gq3_instrument
python3 docs/spikes/gq3-instrument/run.py --edition 3 --run 1      # and --edition 2; --only t01,m3
python3 docs/spikes/gq3-instrument/seeds.py --rep 1
python3 docs/spikes/gq3-instrument/summarize.py
python3 docs/spikes/gq3-instrument/run.py --edition 3 --run 1 --recheck   # after a checker change
```

`run.py` needs a logged-in `claude`. In an agent sandbox whose shell lacks
`CLAUDE_CODE_OAUTH_TOKEN`, it passes the parent Claude Code process's token to the child
process only; the token is never printed or stored. Each edition's three runs took about two
minutes with five workers.

Verification on the branch:
- `cargo build -p omnigraph-compiler --example gq3_instrument`: clean.
- `rustfmt --edition 2024` applied to the example.
- `RUST_MIN_STACK=16777216 cargo dev-test -p omnigraph-compiler --no-fail-fast` (the alias runs
  the whole canonical workspace graph): see the note below.

No existing source changed: the only Rust addition is the example file. `crates/omnigraph-reference-engine/` is untouched.

## (c) What I did not get to

- **A rows-based semantic check.** Edition 3 has no executor, so its correctness is tree
  equality plus my reading. The adjudication was made by one reader who knew the RFC, not
  blinded. Edition 2 could be checked on rows; I checked rows on a local graph only for the
  `bm25` direction, the `rrf` constant and the `nearest` filter of finding 13.
- **A repair turn without the card** (finding 7), which would separate the diagnostics' quality
  from the card's.
- **A type-level edition-3 gate.** `T65`, `T68`, `T69` and `T70` do not exist yet. The checker
  scans for `T65` shapes only; the `bridge3` lowering type-checks the 20 or 21 answers per run
  that have edition-2 nodes (18 or 19 pass; the rest are `T40`, which edition 3 lifts, and the
  `T1` interface binding of finding 12a).
- **The 30-task M0 agent corpus with exact-query ground truth** (section 11's M0 row). I built
  the 45-task instrument only.
- **Other models, effort settings or temperatures.** Every call used `sonnet` (reported as
  `claude-sonnet-5-5`) at the CLI default.
- **Edition 2 seeded with edition-3 habits.** The seeds are edition-3 only.
- **The parser fixes of findings 2 and 8.** The spike parser is unchanged, so the numbers above
  are for `spike/gq3-parser` `0162a330` as it stands.
