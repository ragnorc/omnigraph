# Spike: the rewriter's ranking rows on the engine (GQ 3, section 6)

**Branch:** `spike/gq3-exec`. It starts from `spike/gq3-rewriter` (`f7b4d934`) with
`spike/gq3-parser` (`0162a330`) merged in. This is throwaway code.
**Specification:** `docs/rfcs/2026-11-07-gq-3.md` at `rfc/gq-3` (`d79b6ea7`), with its two
assets. The spike tests section 6's claim: "for the `nearest` and `bm25` rows it holds after a
stated normalisation that inlines a `from` whose relation is a ranking over the whole
`match` … It does not hold for the `rrf` row … That row is proved rows-equal on the corpus
instead."
**Inputs:** the rewriter's corpus (`docs/spikes/gq3-rewriter/`). This branch regenerated it after
one rewriter change (finding 14).

## (a) Findings

The counts cover the whole stored corpus: 111 `nearest`, 39 `bm25` and 28 `rrf` rewritten
declarations, as the rewriter's report counts them. Engine counts cover the GQT steps that run
on a local engine with a fixture: 73 case files and 140 ranked steps. The tables are under
`docs/spikes/gq3-exec/` (`ir_summary.md`, `ir.tsv`, `rows_summary.md`, `rows.tsv`,
`write_groups.tsv`).

### Typed IR (section 6, "The rewriter", the proof paragraph)

**1. For `nearest` the normalisation is enough on its own: 107 of 111 are typed-IR equal with no
other rule.** Of the other four, three are refused alike by both editions: one out-of-range
float literal (`T3`), and two skill-page blocks whose page schema does not declare their types
(`T1`, `T4`). The fourth is the known rename of the result column `p._distance` to `_distance`
(`issue_640_search_score_projection.gqt#7`). Ten of the 111 come from benchmark or doc texts
that have no catalog. Those ten were compared as edition-2 trees, field by field, not as typed
IR. The RFC is right for this row.
**Proposal:** state the normalisation as the named rules of finding 4, so the proof can be
checked.

**2. For `bm25` the inlining gives only 5 of 39. One more rule, *rank-key direction*, brings it to
35 of 39.** Edition 2 stores two IRs for one order:
- `order { bm25($n.body, $q) }` lowers with `descending: false`.
- `order { bm25($n.body, $q) desc }` lowers with `descending: true`.

`Ordering.descending` comes straight from the written direction (`parse_ordering`), and the
executor ignores it for a rank key. The rewriter writes `$score desc` for both spellings, as
its table says, so the inverse cannot recover which one was written. 30 declarations needed
the rule. The four that remain are three refused alike (`T27` and two skill blocks) and the
`_score` rename. On the engine, all 31 bridged `bm25` steps return equal rows with
`descending: true`, which is evidence that the flag carries no meaning today.
**Proposal:** add "a `bm25` or `rrf` order key is descending" to the normalisation. Better:
make edition 2's parser record a bare rank key as descending, which removes the
second IR at the source.

**3. The `rrf` row is IR-comparable after all: 27 of 28 are typed-IR equal after the same
normalisation plus two more rules.** The 28th is refused alike: a skill block, `T1`. The two
extra rules:
- *Let-inlining:* `let $p = { match { P } … }  from rrf(arm($p, …), arm($p, …), k: K)` becomes one
  `match { P }` with edition 2's `rrf(…)` as the leading key.
- *rrf default k:* the rewriter writes `k: 60` where edition 2 omitted `k`, and the IR keeps
  `k: None` against `Some(60)`. 27 needed this rule.

All 27 also needed the direction rule. The "different IR" in section 6 is a difference between
the edition-3 core (`Fuse` over two arms) and edition 2. Today's compiler has no `Fuse`, so it
cannot see that difference. Typed-IR equality after these rules still does not make the row
sound. Finding 5 shows why: the rows differ, because edition 2's `rrf` is not the RFC's `Fuse`.
**Proposal:** keep rows equality for `rrf`, and change the reason section 6 gives for it: "the
edition-3 fusion selects within the population, edition 2's arms do not". Do not cite an IR
difference.

**4. Every rewritten ranking in the corpus was recognised: 178 of 178, with zero bridge
refusals.** The bridge needed these named rules and side conditions, all part of the
"inlining":
- **N1:** the read's only `match` clause is `from R(pop, $v.f, q, …)`, and nothing follows it.
- **N2:** the population is the whole `match`. Its `return` lists exactly the variables the
  pattern binds at top level, and it has no `distinct`, `order` or `limit`.
- **N3:** a `nearest` window satisfies `k = limit`. A `nearest` arm under `rrf` satisfies
  `k = limit`, or `k = 100` when the read has no `limit`. Any other `k` has no edition-2 IR.
- **N4:** the score column is the leading order key. Under an aggregate it is absent, and the
  bridge puts the rank back as the leading key (the rewriter's dropped-key rule; 7
  declarations).
- **N5:** rank-key direction (finding 2).
- **N6:** let-inlining (finding 3).
- **N7:** rrf default `k` (finding 3).
- `recall:`, `weights:` other than 1.0, more than two arms, and a bare field name (`embedding`
  instead of `$d.embedding`) all refuse with `T60` naming the condition.

**Proposal:** write N1 to N7 into section 6 as the normalisation the proof uses.

### Rows (section 4 "Fusion" and "Populations and laws", section 6 rrf row)

The harness runs each case through the GQT runner's executor (`execute_steps`). Each ranked
step gets its own **twin**: the case with that one query replaced by the rewritten text,
lowered by the bridge and printed as edition-2 text. Each ranked step is also evaluated a
second way, called the **RFC route** below. That route evaluates the step under the RFC's
semantics, built from edition-2 pieces on the session the case leaves at that step:
- **Window or score:** `ApproxTopK`, `Score` or `Fuse` over the population. Each arm ranks its
  distinct targets by its own score, then by target id. A `nearest` arm is cut to `k` targets.
  The fused score is `Σ 1/(k+rank)` in `F64`.
- **Rows:** the population's rows for the selected targets, or one row per target after `rrf`.
- **Order and cut:** the read's order keys, then the tie rule over the reference columns, then
  `limit`.

**5. The `rrf` row is not rows-equal on populations that hold a traversal: 18 of 21 match, and
3 differ.** The three:
- `issue_659_ranked_selection_differential.gqt` steps 6 and 7 (`match { $d: Doc $t: Doc
  $d knows $t }`, `limit 3`): edition 2 returns `d020, d040`, edition 3 returns `d020, d030,
  d040`.
- `v2/planner/issue_789_nearest_and_rrf_rank_a_reached_binding.gqt` step 3: edition 2 returns
  `d1`, edition 3 returns `d1, d2`.

The cause is not `k`'s value. It is which rows the arm ranks. Edition 2's `nearest` arm under
`rrf` windows the whole node type, and the traversal filters the window afterwards. The case's
own notes say so: "the nearest arm's corpus-wide top two (d0, d1) leave d1 alone after the
traversal". The rewrite `nearest($p, $d.embedding, $q, k: n)` windows the eligible population
(law 1), so it finds more targets.

The harness also evaluated the alternative rewrite: each arm ranks
`{ match { $d: Doc } return { $d } }`, and the population filters the fused targets afterwards.
That rewrite is **rows-equal on 21 of 21** (`rfc-type-arms` rows in `rows.tsv`). So today,
edition 2's `rrf` breaks law 1, while its single rankings keep it (finding 6).
**Proposal:** choose one of two:
- The rewriter emits
  `let $all = { match { $v: T } return { $v } }  from rrf(nearest($all, …, k: n), bm25($all, …), k: K)`
  followed by the rest of `P`, correlated on `$v` (the RFC's `cited_near` shape). This
  rewrite was measured rows-equal.
- Or list the population rewrite as an intended change in "Compatibility", naming these three
  cases.

**6. Single rankings already follow the RFC's semantics. On the RFC route, 79 of 79 evaluated
`nearest` steps and 29 of 29 `bm25` steps equal edition 2's rows, in the same order.** The twins
match too: 81 `nearest` and 31 `bm25` steps are rows-equal. The two "different" rows are the
`_distance`/`_score` rename, which also turns the twin red under GQT ("expected name
`p._distance`, the executor returned `_distance`"). Traversal populations agree here, because
edition 2's single ranked scan ranks the reached binding (issue 789). So the normalisation is
sound for rows, not only equal in IR.

**7. Ties and duplicate arms: the RFC's tie rule agrees with edition 2 wherever it was tested, but
the corpus barely tests it.** All 21 evaluated `rrf` steps come out in the same order. Every
tie-focused fusion case uses duplicate arms, which the rewriter refuses as an edition-3 type
error, so none of them ran (15 steps):
- `v2/rrf_tied_rows_under_limit.gqt`
- `typed_search_options.gqt`
- `issue_659_rrf_typed_edge_identity.gqt`
- `rrf_cross_join_batch_order_preserves_rank.gqt`
- `v2/rrf_narrow_limit_memory.gqt`
- `issue_788_search_led_aggregate_order_keys.gqt`

The `k: 100` no-limit window is also untested, because every corpus `rrf` has a `limit`.
**Proposal:** when these seventeen cases are edited by hand (section 6), give the tie cases two
distinct arms with tied ranks, so the tie rule is still covered. Add one no-limit `rrf` case.

**8. After `rrf`, no corpus read uses a population column other than the target, so the missing
correlated `from` never shows.** Section 3 says the rewriter "draws the population's other
columns again with a correlated `from`". The rewriter does not, and the bridge refuses any
reference to a non-target column after `rrf`. That refusal never fired. Populations with
fan-out under `rrf` (`$d knows $t`) are one-to-one in the corpus fixtures, so "one row per
target" was never tested against edition 2's arm rows.
**Proposal:** implement the correlated `from` in the rewriter before the corpus grows such a read,
and add a fan-out fusion case.

**9. A `bm25` order drops rows whose field does not match, as `Score` requires. A `bm25` in
`return` without a rank key is `T33` in edition 2 but legal in edition 3.** The probe
`docs/spikes/gq3-exec/bm25_membership.gqt` (green under `omnigraph-gqt`) shows both:
- Step 1: `order { bm25(…) } limit 10` over three notes returns only the two that match. So the
  normalisation is also sound for rows on the 32 `bm25` reads that have no `search()` filter.
- Step 2: `return { bm25(…) as s }` with no rank key is refused with `T33`. The rewriter's "lone
  `bm25` in `return`" branch writes `from bm25(…)` with no `order`, a legal edition-3 read, so
  a refusal turns into rows.

**Proposal:** add `T33` to the fixed code map for this branch, as for `T54`, or have the rewriter
refuse the branch.

**10. A rank key under `return distinct` has no edition-3 meaning.**
`bm25_distinct_scores_rank_descending.gqt` step 2 writes `return distinct { $n.slug, 1 as one }
order { bm25(…), 1 }`. Edition 2 ignores the rank and returns `n1, n2, n3` (plan: `sort no
tiebreak`). The rewrite `order { $score desc, 1 }` orders by a column that a `Group` (section 2,
`return distinct`) does not carry. The IR proof calls the two IR-equal, because the bridge folds
the rank back. The RFC route cannot type the read and reports it as not evaluated.
**Proposal:** section 3 should say what an order key over a non-grouped column means under
`distinct`. The rewriter should treat `distinct` like an aggregate (drop the key) or refuse.

### Writes (section 2 "Sinks", section 3 "Writes fed by reads", section 6 "a body of several statements")

**11. The group rule reproduces edition 2 on every body it reached: 52 bodies of several
statements, 36 with the same final state and the same affected count, 5 refused alike, 0
that differ.** Each group ran as its edition-2 statement on its own, in order, on one branch.
The rewriter's finding 3 established that a group selects exactly what its statement selects.
The final state of every node and edge type was compared with the whole body run on a branch
from the same head. Edges are compared by endpoints and properties, because `@id` is generated
per insert. Coverage:
- 24 of the rewriter's 26 GQT bodies were reached, 10 of them on branches through the GQT
  runner's prefix.
- The other 2 sit behind fault seams (`detached_abandoned_mixed_write_stays_invisible.gqt`).
- The 2 benchmark bodies were not run.
- 26 more bodies of several statements (all inserts, which the rewriter leaves untouched) were
  also reached.

8 more rows differ only because of the harness. The harness publishes each group alone, while
`@card` minimums (`card_minimum_counts_every_written_source.gqt`) and the
`write_max_bytes` limits (7 bodies) judge the whole write.
**Proposal:** section 2 should state that whole-write validation (cardinality, uniqueness, byte
limits) runs once, over every group, at the single publication. That is how edition 2 runs these
bodies today.

**12. The RFC's within-one-write conflict rule would refuse 4 bodies that the group rule
otherwise accepts.** Section 2 says: "Two different values for one entity and property … within
one write are a typed refusal". The four:
- `reserved_word_property_in_mutation_predicate.gqt` `chained_updates` sets `Span "c".exists`
  to 5 and then to 6. The case is green today.
- `write_max_bytes_insert_then_update_same_row.gqt`, `write_max_bytes_superseded_upsert_payload.gqt`
  and `write_max_bytes_update_after_duplicate_upserts.gqt` write a key twice. Today these fail on
  byte limits, so they would get a different error.

This contradicts section 3's "a body that inserts a document and then updates it by key still
works" and section 9's "28 stored bodies depend on it". The rule is also ambiguous for
`blob_update_carries_unassigned_blobs.gqt` `insert_then_assign_second`: an insert of
`Doc { slug: "c" }` followed by an assignment of `second`. Does an insert "write" the properties
it leaves unassigned?
**Proposal:** limit the conflict rule to effects within one group. Across groups, a later effect
wins, which is what sequential visibility means. Say whether an insert writes its unassigned
properties.

### Tooling and smaller points

**13. Edition 2's `nearest_ordering` rule shadows an order key that starts with `nearest(`.**
`order { nearest($d.embedding, $v) < 1.0 asc }` does not reparse unless the key is wrapped in
parentheses. PEG tries `nearest_ordering` first and never backtracks into `expr`. Any printer
or formatter of edition 2 needs the parentheses. `print_read2` adds them. Also, every
normalised read printed back to edition 2 lowers to the same typed IR, except three
expect-error texts that hold a non-finite float, which has no literal.

**14. The rewriter now writes the ranked column the way the RFC does.**
`nearest({ … }, $d.embedding, $q, k: n)` replaces the bare `embedding`, adopting the rewriter's
finding 8 now that section 3 says "names the column it ranks as a property access". All 178
rewritten rankings parse under `parse_query3` and normalise. The corpus artifacts under
`docs/spikes/gq3-rewriter/` were regenerated with the change (`rewrites.md`, `corpus.tsv`,
`corpus.jsonl`, `summary.md`).

**15. Running the harness needs a larger stack.** Running whole cases through `execute_steps`
from one `#[tokio::test]` overflows the default stack. Run with `RUST_MIN_STACK=67108864`, as
the 0.12 qualification command in `docs/dev/testing.md` does.

## (b) What was built, and how to run it

| Piece | Where | What it does |
|---|---|---|
| Normalising bridge | `crates/omnigraph-compiler/src/query/normalise3.rs` | `normalise_decl(Decl3)` recognises the rewriter's three ranking shapes on the edition-3 tree and lowers them to the edition-2 `QueryDecl` today's compiler checks, under N1 to N7 (finding 4). It refuses every other ranking shape with `T60` naming the condition. Reads without a ranking `from` go through the parser spike's `bridge3::lower_decl`. `Normalised.fusion` keeps the arms, population and `k` for an executor of the RFC's semantics. `print_read2` prints an edition-2 read as text. |
| Typed-IR proof | `crates/omnigraph-compiler/src/migrate/exec_tests.rs` | For every corpus text with a ranking rewrite and no refusal, it compares each ranked declaration's typed IR with the original's, raw and after N5 and N7. Without a catalog it compares edition-2 trees. It also checks that each printed read reparses to the same IR. Writes `target/spike-gq3-exec/ir.tsv` and `ir_summary.md`. |
| Ranked rows on the engine | `crates/omnigraph-gqt/tests/gq3_ranked_rows.rs` | Per ranked step: the twin through `execute_steps` with a recording host, compared with the original run (ordered where the case orders). Then the RFC route on the session the case prefix leaves (finding 6), and for `rrf` the type-arm route (finding 5). Writes `rows.tsv` and `rows_summary.md`. |
| Write groups | `crates/omnigraph-gqt/tests/gq3_write_rows.rs`, `several_statements_as_sequential_groups_match_the_whole_body` | Replays main-branch writes, or runs the case prefix through `execute_steps` for bodies on branches. Runs the whole body and the per-statement groups on two branches from one head, compares final states, and evaluates the conflict rule. Writes `write_groups.tsv`. |
| Probe | `docs/spikes/gq3-exec/bm25_membership.gqt` | Finding 9. Run with `omnigraph-gqt`. It lives outside the corpus directory. |
| Rewriter change | `crates/omnigraph-compiler/src/migrate/mod.rs` | Finding 14. |
| Merge fix | `crates/omnigraph-compiler/src/query/bridge3.rs` | `spans: Default::default()`, since the rewriter's `QueryDecl.spans` met the parser's bridge. `lower_expr` is `pub(crate)`. |

```bash
cargo test -p omnigraph-compiler --lib migrate::                     # rewriter tests, corpus table, typed-IR proof
RUST_MIN_STACK=67108864 cargo test -p omnigraph-gqt --locked --test gq3_ranked_rows -- --nocapture
RUST_MIN_STACK=67108864 GQ3_CASE=issue_659 cargo test -p omnigraph-gqt --locked --test gq3_ranked_rows -- --nocapture
RUST_MIN_STACK=67108864 cargo test -p omnigraph-gqt --locked --test gq3_write_rows -- --nocapture
cargo run -p omnigraph-gqt --bin omnigraph-gqt -- docs/spikes/gq3-exec/bm25_membership.gqt
```

**Test runs** (at this report's commit):
- `cargo dev-nextest -E 'package(omnigraph-compiler)'`: 543 run, 543 passed, on the workspace
  dev graph that `cargo dev-test` builds. That graph's `-p` is ignored beside `--workspace`, so
  the filter set is the per-crate form `docs/dev/local-builds.md` names.
- `cargo test -p omnigraph-gqt --locked`: 463 corpus cases, 52 runner unit tests, 14
  integration tests, 29 dispatch tests and the three spike harnesses, all passed.
- `cargo clippy -p omnigraph-compiler -p omnigraph-gqt --all-targets --locked -- -D warnings
  -W clippy::dbg_macro` is clean.
- `cargo fmt --all --check` is clean.

Merging the two spikes broke three of the parser spike's tests
(`a_block_in_clause_position_is_a_filter`, `shared_examples_mean_the_same_in_both_editions`,
`the_edition2_corpus_read_as_edition_3`). Each compares the `Debug` form of two `QueryFile`s,
and the rewriter's `spans` side table is now part of that form. The tests now compare without
spans. Four clippy errors on the rewriter branch were also fixed, in `call_at`, `corpus_tests`
and the large `FileBody::Explain` variant, which `spans` enlarged and which an `allow` now
covers.

## (c) What I did not get to

- **An edition-3 executor.** The RFC route reaches the RFC's semantics through edition-2 reads
  and a fusion in the harness. It is an independent evaluator, not the engine running `Fuse`.
  Its `nearest` arm uses a 10,000-row exact scan. That matches an indexed window only where the
  index misses nothing, which holds on these small fixtures.
- **Five ranked steps the RFC route does not evaluate:** an order key or item computed from the
  score (`rank_call_nested_in_a_boolean_projection.gqt` steps 3, 5, 7 and 9) and the `distinct`
  read of finding 10. Four expect-error steps have no rows to compare.
- **Steps outside these harnesses:** ranked steps inside loops, `--- concurrent` blocks, DST or
  served cases, and benchmark and doc texts. Those texts appear only in the typed-IR proof.
- **Typed IR for writes and for edition-3 shapes beyond the ranking rows.** The bridge still
  refuses `let` values, `optional`, arithmetic and the `$x` statements.
- **The `rrf` evaluator on fan-out populations** (finding 8) and **the `k: 100` window**
  (finding 7): no corpus case exercises them.
- **The two seam-gated write bodies and the two benchmark bodies** (finding 11).
- **The canonical workspace graph, DST and the docs checks.** The run covered the changed
  crates only: the compiler's nextest filter set on the dev graph, the whole GQT crate, and
  clippy for both.
