# Spike: the GQ 3 rewriter (`omnigraph queries migrate`)

**Branch:** `spike/gq3-rewriter` (from `main` `e7e8e03b`), throwaway code.
**Specification:** `docs/rfcs/2026-11-07-gq-3.md` at `rfc/gq-3` on the fork (`ab74f629`), with its two
assets (`2026-11-07-gq-3-before-and-after.md`, `2026-11-07-gq-3-grammar-comparison.md`).
**Earlier spike reused:** `spike/gq3-1202` (`docs/spikes/gq3-1202.md`): the idea of
byte-range edits over the edition-2 parse and a corpus harness, rewritten here because
that rewriter worked on pest pairs and targeted the withdrawn `retrieve` rows.
**Edition-3 parser:** `spike/gq3-parser` appeared on the fork near the end of this run
(`0162a330`). Every rewritten text was parsed with it, and the rewrites its bridge can
lower were compared in typed IR (finding 18); it has no type checker for `from`, `let`
or `$x`-statements, so rows 5 to 9 are proved by parse, by the confinement and residue
checks, and (rows 8 and 9) by the engine, not by typed IR.

## (a) Findings

Counts are over the corpus of (b): 4,029 query texts (2,756 GQT steps, 1,201 benchmark
steps, 72 doc and skill texts), 3,231 declarations. GQT steps repeat a query text across
steps; counts are per stored text, not per distinct text. The full tables are after the
findings.

### Writes (section 2 "Sinks", section 3 "Writes fed by reads", section 6 rows 8 and 9)

**1. A body of several write statements has no rewrite, and some have no edition-3
meaning at all.** Section 3: "Each statement runs once per row of the clauses'
relation … a later statement cannot read an earlier statement's effect". The table
rewrites each `delete T where φ` to `$x: T  φ′  delete $x`, which is sound only when the
statement is alone: with two, the clause relation is the product of both bindings, so
one statement runs once per row of the other's selection and an empty selection
suppresses the other statement, an `insert` included. The corpus has **57 such
statements in 28 declarations** (30 bodies of 2 statements, 11 of 3, 16 of 4; e.g.
`benchmarks/workloads/parallel_tables_2.gqt` `update T0 … where key = "row"` then
`update T1 … where key = "row"`). Worse, edition 2 runs statements in sequence with
read-your-writes: `cases/blob_update_carries_unassigned_blobs.gqt`
`insert_then_assign_second` inserts `Doc { slug: "c" }` and then
`update Doc set { second: $second } where slug = "c"`, expecting `affected: nodes=2`;
the update sees the insert. Edition 3 forbids exactly that. The rewriter refuses all 57.
**Proposal:** let a write body be a sequence of groups, `clauses  statements`, each group
its own relation over the one snapshot, and have the rewriter emit one group per
edition-2 statement; then decide read-your-writes explicitly, either by keeping
sequential visibility between groups (edition 2's behaviour) or by listing it as an
eighth break with the cases named.

**2. The RFC's own write examples break a rule it says is unchanged.** Section 2 says
"the constructive-or-destructive split and one publication per write are unchanged",
but `close_company` (sections 3, 8 and the before-and-after asset, "A write whose
targets come from a traversal") and `retire` both put `delete $k` and
`update $p set { … }` in one body. Today the engine refuses that
(`enforce_no_mixed_destructive_constructive`, `crates/omnigraph/src/exec/mutation.rs:880`:
"mixes inserts/updates and deletes"; `docs/user/mutations/index.md`, "cannot mix inserts
or updates with deletes"). **Proposal:** split the examples into two writes, or state
that edition 3 lifts the split and say what replaces its reason (one table version per
query).

**3. The selection half of rows 8 and 9 is right: `$x: T  φ′` selects what `where φ`
selects.** Checked on the engine for every single-statement `delete`/`update … where`
a GQT case reaches on main from its fixture (`crates/omnigraph-gqt/tests/gq3_write_rows.rs`):
**114 statements, 75 with non-empty selections equal (the ids a delete
removed, or an update's affected count and changed rows), 10 equal and empty,
29 where the write itself refuses (expect-error steps), 0 disagreements.** φ′
there is φ with bare names prefixed and its edition-2 spelling kept, so today's compiler
runs it. No proposal; the claim holds.

**4. φ′ is not "every bare name prefixed `$x.`" for edges.** A mutation `where` still
accepts `from` and `to` for `@src` and `@dst` (`mutation_property_type`,
`query/typecheck.rs`); prefixing gives `$x.from`, which names nothing. **6 statements**
(`cases/keyed_edge_delete_wins_over_readd.gqt` `delete Knows where from = "alice"`, and
five others). The rewriter writes `$x.@src`/`$x.@dst`. **Proposal:** add that to the row.

**5. The core cannot express a write on an edge type, and no binding binds one.**
Section 2's `Scan(T)` is "every node of type T", but rows 8 and 9 lower
`delete T where φ` to `Delete(Filter(Scan(T), φ), $x)` for every `T`, and
**32 rewritten statements target an edge type** (16 GQT, 16 benchmark), giving
`$x: Knows`. Neither section 3's `binding` nor today's read path admits that: the
engine check above first bound edges as `$s $x:knows $d` and today's type checker
refused 3 of them with `T5: traversal from $s to $d requires an executable source or
destination node binding`; binding the source by its type (`$s: Person  $s $x:knows $d`)
fixed it. The rewriter still writes `$x: Knows`, as the table does. **Proposal:** give `Scan` an edge form (`Scan(E) → R[$x: E<e>]`) and say
whether `$x: E` is a binding or rewrite edge targets to `$s: From  $s $x:e $d`.

**6. Unresolved question 6 misstates today.** It says an edition-2 `where` matching
nothing "publishes an empty commit". It publishes nothing: `docs/user/mutations/index.md`
("A successful mutation that matches no entities publishes no commit and returns
`"commit": null`") and `Session::mutate_as_with_expected_head_receipt`'s contract
("A successful no-op has no commit"). The 10 empty-selection statements of finding 3 ran
through that path. **Proposal:** restate the question with today's answer as the default.

**7. The generated name must be fresh, for both `$x` and the `rrf` row's `$p`.** The
table spells `$x` and `let $p`; a parameter or binding of that name collides. No write in
the corpus has a `$x`, but **3 `rrf` reads bind `$p` already**
(`cases/issue_640_search_score_projection.gqt` `rrf_projected`, `match { $p: Person }`;
`v2/planner/contains_beside_search_and_ranking.gqt`; `v2/planner/issue_789_…`). The
rewriter takes the first free name (`$p2`, `$x2`). **Proposal:** "a name not otherwise
used in the declaration" in the table.

### Rankings (section 4 "Fusion", section 5, section 6 rows 5 to 7)

**8. The generated population breaks section 5's "exactly one node column", and the bare
field cannot say which column it means.** Section 5: "their population must carry
exactly one node column"; section 6 writes `return { <every variable P binds> }` and
`nearest({ … }, e, $q, …)` with `e` a bare identifier "resolved on the node column's
type". **68 of the 178 rewritten rankings** return two or more node columns, and in
**58** the ranked variable is one of several (`cases/issue_567_traversal_nearest_fills_limit.gqt`:
`match { $d: Doc  $d knows $t }` gives `return { $d, $t }`, field `embedding`, both
columns `Doc`). The RFC's own example in section 6 ("What a reader sees changed")
returns `{ $r, $p }` and ranks `text`. **4** populations also carry an edge column
(`$e`, `$w`). Returning only the ranked variable would be wrong: the traversal's other
end is used downstream (`return { $d.slug, $t.slug }`). **Proposal:** name the target in
the call, `nearest(rel, $d.embedding, $q, k: n)`, and relax section 5 to "the named
node column; the others ride as fan-out payload", which is law 4 already.

**9. "No result column name changes" is false for the unaliased rows.** Today an
unaliased ranking is the column `<var>._distance` (`projection_name`,
`query/typecheck.rs`); the rewrite's `$distance as _distance` is the column `_distance`,
since an alias names its column. `d._distance` is not an identifier, so no alias can
keep it. **2 GQT steps** (`issue_640_search_score_projection.gqt`
`nearest_projected_default_name` and `bm25_projected`, `p._distance`/`p._score`).
**Proposal:** put the rename in the fixed map the blocked-on line names, or keep the
table and change its text ("the result column `d._distance` as today") to say
`_distance`.

**10. A bare `bm25` or `rrf` key sorts descending; `$score` alone sorts ascending.** The
table's `bm25` row shows only an edition-2 key written `desc`. Edition 2 reads a bare rank
key as the rank's own direction. **57 keys** are bare; the rewriter writes `$score desc`.
Related: `asc` on a rank key is `T54` today (`cases/rank_key_asc_direction_refused.gqt`),
while `$score asc` is a legal ascending order in edition 3, so a rewrite would turn a
refusal into rows; the rewriter refuses those **2 declarations**. **Proposal:** the row
reads "`bm25(…)` or `bm25(…) desc` → `$score desc`; `asc` has no rewrite and keeps its
error through the code map".

**11. A ranking key under an aggregate selects rows and orders nothing; the table would
order groups by it.** Today the leading search key of an aggregate read "selects the
population an aggregate reads and does not order the groups"
(`cases/issue_788_search_led_aggregate_order_keys.gqt` notes); `bm25` counts every match
despite `limit` (`issue_563_aggregate_uncapped.gqt`: `total` 20 with `limit 2`) and
`nearest` counts its window (`count_over_traversal_nearest_counts_window.gqt`).
Following the table literally leaves `order { $score desc, n desc }` after the
aggregate, ordering groups by a column they do not carry. **7 declarations.** The
rewriter drops the ranking key from `order` (the window or score already sits in
`from`). The window itself agrees: a probe on today's engine
([`gq3-rewriter/fanout_window.gqt`](gq3-rewriter/fanout_window.gqt), run with
`omnigraph-gqt` and blessed) has `a` with three `knows` edges and `b` with one; with
`order { nearest(…) } limit 2`, the plain read returns 2 rows (both of `a`), but
`count($t)` returns **4** and the grouped read returns `a: 3, b: 1`. So under an
aggregate edition 2 already windows two *targets* with all their fan-out, which is
what `from nearest(…, k: 2)` followed by `count` gives (law 4). **Proposal:** add the
dropped-key sentence to rows 5 to 7, and say that `limit` stays a cut of the groups
while `k` takes its value.

**12. Duplicate `rrf` arms and arms on two bindings are edition-3 type errors that the
seven rows do not cover.** Section 4: "Two arms with the same population, field and
query are a type error" and "arms over different node columns are a type error".
**15 declarations in 7 GQT files** fuse an arm with itself (`rrf(bm25($d.text, $t),
bm25($d.text, $t))`, used to test fusion mechanics: `typed_search_options.gqt`,
`rrf_tied_rows_under_limit.gqt`, `issue_659_rrf_typed_edge_identity.gqt`, …) and **2**
fuse arms on `$d` and `$t` (`v2/planner/search_order_on_an_unranked_binding_and_a_destination.gqt`).
Edition 2 accepts all 17. No rewrite compiles, so the gate "the whole corpus rewritten
with no hand edit to query text, run green" cannot pass. **Proposal:** list both refusals
in "Compatibility" as breaks with these cases, and rewrite the 15 by hand to two distinct
arms; or keep duplicates legal with a lint.

**13. Expect-error texts need the code map in both directions.** The rewriter edits
**16 GQT declarations that today's type checker refuses** (expect-error steps: 14
writes, 2 rankings), so their expected code must be re-derived under edition 3; and it
cannot rewrite **3** that are illegal in both editions (`issue_640_search_score_projection.gqt`:
`nearest` in `return` with no ranking key, `T33`; a second `nearest` beside the window,
`T33`; `max(nearest(…))`, `T32`), whose code becomes `T65`. The earlier spike found
the same three. **Proposal:** the fixed map names `T33 → T65`, `T32 → T65` and `T54`
(finding 10).

**14. The typed-IR proof for rows 5 to 7 cannot compare raw IR.** Section 6: "the
window over a population lowers to the ranked scan the edition-2 `order` lowered to".
Edition 2's IR has the ranking in `order_by` over one pipeline; edition 3's has a
`from` (`Correlate`) over an inline read whose `return` lists the population. They are
equal only after a normalisation that inlines a `from` whose relation is a ranking over
the whole `match`. **Proposal:** name that normalisation as part of the proof, or prove
rows 5 and 6 on the bound plan, as the `rrf` row already falls back to rows.

**15. Variables bound inside blocks and after the ranking: the generated population is
right.** Edition 2 has no clause "after the ranking" (the ranking lives in `order`), so
the whole `match` is the population and nothing follows the `from`; the rewriter never
needs the RFC's `cited_near` shape, whose traversal after `from` changes meaning. Block
variables stay local: no ranked corpus read binds one, and
`migrate::tests::variables_bound_inside_a_block_stay_out_of_the_population` pins it.
The RFC is right here.

**16. The `rrf` row: the window it writes equals today's arm window, but rows-equality
was not run.** Today a `nearest` arm under `rrf` fetches `limit` candidates, or
`RRF_NEAREST_ARM_K` = 100 without a limit (`crates/omnigraph-planner/src/optimizer.rs:4293`),
and a `bm25` arm is unwindowed; `k: <limit>` and an unwindowed `bm25($p, …)` match
that. Every corpus `rrf` has a limit, so the table's missing no-limit case (`k: 100`)
never arises; the rewriter implements it. The before-and-after asset's `hybrid` example
writes `k: 50` with `limit 10`, which is not what the table produces (`k: 10`).
**28 `rrf` constructs** are rewritten, in 17 GQT files, 2 benchmark files and 3 doc
blocks (list in (b)'s `corpus.tsv`); rows-equality needs an edition-3 executor and was
not run. **Proposal:** fix the asset's `k`, and add the no-limit case to the row.

### The mechanical rows (section 6 rows 1 to 4) and the tool

**17. The four expression rows are right and mechanical.** **36** infix `contains` →
`contains()`, **25** `starts_with` → `starts_with()`, **22** list `contains` → `in`,
**4** `fuzzy(…, n)` → `max_edits: n`, every one confined to its comparison or call
(a comment beside the construct survives: `$d.title contains "graph"   // keep me` becomes
`contains($d.title, "graph")   // keep me`). The list row's
output is legal edition 2, so it was proved directly: **18 of 18 declarations compile
to equal typed IR** under today's compiler. Deciding list against String needs the
schema; with it, every `contains` in the corpus resolved, including an edge alternation
whose members agree (`$e:(knows | likes)`). **Proposal:** make the schema a required
input of `queries migrate`, not an option.

**18. Every rewritten text parses under the edition-3 parser, and rows 1 to 4 keep
their typed IR.** Run with `spike/gq3-parser` `0162a330`: its `parse_query3` over all
4,018 parseable texts (`gq3-rewriter/gq3_rewriter_check.rs.txt`, added as
`crates/omnigraph-compiler/tests/` on a worktree of that branch, fed
[`gq3-rewriter/corpus.jsonl`](gq3-rewriter/corpus.jsonl); results in
[`gq3-rewriter/edition3_parse_summary.txt`](gq3-rewriter/edition3_parse_summary.txt)):
**599 of 599 fully rewritten sources parse**, covering every rewritten construct of every
row (`nearest` 111, `bm25` 39, `rrf` 28, `delete` 106, `update` 254, the four expression
rows 82); the 3,369 untouched sources parse with the edition line; of the 50 sources
with a refusal, 22 parse (their refused construct is legal syntax that edition 3 refuses
by type, such as duplicate `rrf` arms) and 28 do not, all of them finding 1's
multi-statement writes, which that parser's `where` recognizer refuses with `Q008`.
Through its bridge into the edition-2 tree (which lowers only nodes edition 2 has, and
refuses `from`, `let` and the `$x` statements with `T60`), **48 declarations whose
rewrite uses only rows 1 to 4 compile to typed IR equal to the original's, 0 differ**
(`contains()`, `starts_with()`, `in`, `max_edits:`). The two branches also agree on the
write fix: that parser's `where` recognizer prints `$x: WorksAt  $x.@src = $h  delete $x`
for `delete WorksAt where @src = $h`, the rewriter's text.

**19. Byte-range edits over the edition-2 AST work, with two additions to the AST.** The
span side table (`DeclSpans`) leaves `Expr` and every parse result unchanged; two
facts were needed that pest does not give: the positions of the literal keywords `set`
and `where` (found by skipping trivia from the token before), and trailing whitespace,
which pest leaves inside a repetition's span (`mutation_predicate` ended with the
newline before `}` until trimmed). Over the corpus: no overlap, every edit rendered
once, every edit confined to its construct, no edition-2 spelling left where nothing
was refused; idempotent (the edition line makes a second run a no-op, and the residue
scan shows it would find nothing). The RFC is right that layout and comments survive.

**20. Doc fragments carry edition-2 spellings the tool cannot reach.** Section 6:
"The bundled skill, the user guide and the GQT corpus are edition 3 in the release
that ships it". 10 fenced `gq` blocks under `docs/` and `skills/omnigraph` are not
whole files (a lone `match { … }`, a statement list, `return … order … limit` without
its declaration) and do not parse, and four of them hold one of the seven spellings:
`skills/omnigraph/references/queries.md` (`fuzzy($d.title, $q, 2)`;
`$p.name contains "Al"  $p.name starts_with "A"`), `skills/omnigraph/references/search.md`
(`fuzzy(…, 2)`) and `docs/user/mutations/index.md` (`update Person set { … } where …`,
`delete Person where …`). **Proposal:** a fragment mode (wrap the block in a synthetic
declaration, rewrite, unwrap), or name these blocks as hand edits in the release
checklist; the skill is exported byte-for-byte, so a stale block is a user-facing
bug (`AGENTS.md`).

**21. How often each spelling is used.** Declarations using each spelling (rewritten or
refused), over all stored texts: infix `contains` 35, `starts_with` 25, list
`contains` 18, positional `fuzzy` 4, `nearest` 112, `bm25` 40,
`rrf` 46, `delete … where` 108, `update … where` 280. Of 3,231
declarations, 600 are rewritten whole, 2,581 need no edit, and 50 carry a
refusal (findings 1, 10, 12, 13).

### The corpus table

Produced by `migrate::corpus_tests::whole_corpus_through_the_rewriter`
([`gq3-rewriter/summary.md`](gq3-rewriter/summary.md); every source's outcome, refusal
and note is a row of [`gq3-rewriter/corpus.tsv`](gq3-rewriter/corpus.tsv), and every
rewritten text with its source is in [`gq3-rewriter/rewrites.md`](gq3-rewriter/rewrites.md)).
"Without a schema" covers the benchmark files that take their graph from `--fixture`
and the doc blocks whose page has no ```pg block; only `contains` needs one, and none of
those texts holds a `contains`. The doc and skill rows marked "refused by today's type
checker" are blocks checked against a schema block of their page that does not
describe them, not expect-error steps. The 11 doc and skill texts that do not parse
under edition 2 are 10 fragments (finding 20) and `docs/user/queries/catalogue.gq`,
which holds schema declarations.

| | benchmarks | docs | gqt cases | skill | total |
|---|---:|---:|---:|---:|---:|
| files | 123 | 20 | 449 | 4 | 596 |
| query texts | 1201 | 39 | 2756 | 33 | 4029 |
| already `gq 3;` | 0 | 0 | 0 | 0 | 0 |
| not edition-2 text (parse refused) | 0 | 5 | 0 | 6 | 11 |
| statements (no declaration) | 132 | 6 | 654 | 0 | 792 |
| declarations | 1069 | 29 | 2102 | 31 | 3231 |
|   reads | 954 | 20 | 1487 | 22 | 2483 |
|   writes | 115 | 9 | 615 | 9 | 748 |
|   without a schema | 752 | 26 | 0 | 21 | 799 |
|   refused by today's type checker (expect-error steps) | 0 | 0 | 132 | 7 | 139 |
| declarations rewritten | 112 | 8 | 468 | 12 | 600 |
| declarations untouched | 955 | 21 | 1586 | 19 | 2581 |
| declarations with a refusal | 2 | 0 | 48 | 0 | 50 |
| proof failures | 0 | 0 | 0 | 0 | 0 |
| list `contains` -> `in`: typed IR equal (today's compiler) | 0 | 0 | 18 | 0 | 18 |
| list `contains` -> `in`: typed IR differs | 0 | 0 | 0 | 0 | 0 |

**Edits per row** (constructs rewritten)

| | benchmarks | docs | gqt cases | skill | total |
|---|---:|---:|---:|---:|---:|
| infix contains -> contains() | 0 | 0 | 36 | 0 | 36 |
| infix starts_with -> starts_with() | 14 | 0 | 11 | 0 | 25 |
| list contains -> in | 0 | 0 | 22 | 0 | 22 |
| fuzzy 3rd arg -> max_edits: | 0 | 0 | 4 | 0 | 4 |
| nearest -> from nearest() | 10 | 1 | 96 | 4 | 111 |
| bm25 -> from bm25() | 0 | 3 | 34 | 2 | 39 |
| rrf -> let + from rrf() | 3 | 1 | 22 | 2 | 28 |
| delete T where -> $x: T phi delete $x | 20 | 2 | 82 | 2 | 106 |
| update T set where -> $x: T phi update $x | 65 | 1 | 186 | 2 | 254 |

**Declarations using each spelling** (rewritten or refused)

| | benchmarks | docs | gqt cases | skill | total |
|---|---:|---:|---:|---:|---:|
| infix contains -> contains() | 0 | 0 | 35 | 0 | 35 |
| infix starts_with -> starts_with() | 14 | 0 | 11 | 0 | 25 |
| list contains -> in | 0 | 0 | 18 | 0 | 18 |
| fuzzy 3rd arg -> max_edits: | 0 | 0 | 4 | 0 | 4 |
| nearest -> from nearest() | 10 | 1 | 97 | 4 | 112 |
| bm25 -> from bm25() | 0 | 3 | 35 | 2 | 40 |
| rrf -> let + from rrf() | 3 | 1 | 40 | 2 | 46 |
| delete T where -> $x: T phi delete $x | 20 | 2 | 84 | 2 | 108 |
| update T set where -> $x: T phi update $x | 67 | 1 | 210 | 2 | 280 |

**Of those, in declarations today's type checker refuses** (expect-error steps whose text the rewriter edits)

| | benchmarks | docs | gqt cases | skill | total |
|---|---:|---:|---:|---:|---:|
| infix contains -> contains() | 0 | 0 | 0 | 0 | 0 |
| infix starts_with -> starts_with() | 0 | 0 | 0 | 0 | 0 |
| list contains -> in | 0 | 0 | 0 | 0 | 0 |
| fuzzy 3rd arg -> max_edits: | 0 | 0 | 0 | 0 | 0 |
| nearest -> from nearest() | 0 | 0 | 1 | 2 | 3 |
| bm25 -> from bm25() | 0 | 0 | 1 | 2 | 3 |
| rrf -> let + from rrf() | 0 | 0 | 0 | 1 | 1 |
| delete T where -> $x: T phi delete $x | 0 | 0 | 9 | 0 | 9 |
| update T set where -> $x: T phi update $x | 0 | 0 | 5 | 0 | 5 |

**Refusals**

| | benchmarks | docs | gqt cases | skill | total |
|---|---:|---:|---:|---:|---:|
| ranking: `asc` on a rank key (edition-2 T54) would become legal | 0 | 0 | 2 | 0 | 2 |
| ranking: a second ranking beside the window | 0 | 0 | 1 | 0 | 1 |
| ranking: nearest/rrf in return with no ranking order key | 0 | 0 | 2 | 0 | 2 |
| rrf: arms over different bindings | 0 | 0 | 2 | 0 | 2 |
| rrf: duplicate arms (edition-3 type error) | 0 | 0 | 15 | 0 | 15 |
| write: several statements in one body | 6 | 0 | 51 | 0 | 57 |

**Notes** (observations a rewrite carries)

| | benchmarks | docs | gqt cases | skill | total |
|---|---:|---:|---:|---:|---:|
| bare-rank-key-desc | 3 | 3 | 47 | 4 | 57 |
| edge-binding | 16 | 0 | 16 | 0 | 32 |
| legacy-endpoint | 0 | 0 | 6 | 0 | 6 |
| population-edge-column | 0 | 0 | 4 | 0 | 4 |
| population-node-columns | 3 | 2 | 61 | 2 | 68 |
| ranked-variable-dropped | 3 | 2 | 51 | 2 | 58 |
| ranking-under-aggregate | 0 | 0 | 7 | 0 | 7 |
| result-column-renamed | 0 | 0 | 2 | 0 | 2 |
| rrf-let-name | 0 | 0 | 3 | 0 | 3 |

### The write-row check

`crates/omnigraph-gqt/tests/gq3_write_rows.rs`, per statement in
[`gq3-rewriter/write_rows.tsv`](gq3-rewriter/write_rows.tsv):

| Outcome | Statements |
|---|---:|
| selection equals the statement's effect, non-empty | 75 |
| selection equals the statement's effect, both empty | 10 |
| the write refuses (expect-error step); the read ran (16) or refused alike (13) | 29 |
| the read of the rewrite's clauses refuses while the write runs | 0 |
| disagreement | 0 |
| total | 114 |

## (b) What was built and how to run it

| Piece | Where | What it does |
|---|---|---|
| Byte spans on the edition-2 AST | `crates/omnigraph-compiler/src/query/ast.rs` (`Span`, `DeclSpans`, `ExprSpan`, `SpannedExpr`), recorded by `query/parser.rs` | A side table on `QueryDecl` (`spans`): the declaration, body, `match` and its inner range, each top-level clause, projection, ordering and mutation statement (keyword, type, `set { … }`, `where`, predicate), and every expression node a rewrite needs (infix comparisons, `in`, calls with argument spans, property accesses, variables, bare names in a property scope). `Expr` is untouched, so equality, type checking and lowering are unchanged; `migrate::tests::spans_do_not_change_parse_results` lowers two layouts of one declaration to equal IR, and the full compiler suite stays green. |
| The rewriter | `crates/omnigraph-compiler/src/migrate/mod.rs` | `migrate(source, catalog) -> Migration`: parses under edition 2, builds byte-range edits per row, applies the outermost edits highest offset first, prepends `gq 3;`. A replacement is literal text plus source ranges it copies (`Src`, carrying the edits nested in the range, or `Raw`, verbatim), so a moved predicate keeps its own rewrites and nothing is re-printed. Refusals and notes are typed per declaration. |
| Proof per source | same module | `Proof`: the outermost edits never overlap; every edit is rendered exactly once (none lost in a deleted range, none duplicated by a copy); every edit and every range it copies lies inside the construct it names, and its literal text uses only the row's vocabulary, names the construct holds and the variables it generated; no edition-2 spelling of a row survives (a lexical residue scan). `apply_rows_in_edition_2` and `typed_ir` give the typed-IR comparison for the one row whose output edition 2 parses. `write_selections` builds, for a single-statement write, the edition-2 read of the rewrite's clauses. |
| Unit tests | `crates/omnigraph-compiler/src/migrate/tests.rs` | 19 tests: each row, comments and layout, bare-key direction, aliases, fresh names, refusals, block-local variables, aggregates, the proof's own negative cases. |
| Corpus run | `crates/omnigraph-compiler/src/migrate/corpus_tests.rs` | Every query text in `crates/omnigraph-gqt/cases/**/*.gqt` (`--- query`, `--- mutate`, `--- concurrent` sessions) and `benchmarks/**/*.gqt`, every fenced `gq` block and `.gq` file under `docs/` and `skills/omnigraph`. Fails on any proof failure or `in` IR difference. Writes `target/spike-gq3-rewriter/{summary.md,corpus.tsv,rewrites.md,corpus.jsonl}`; copies are in [`gq3-rewriter/`](gq3-rewriter/). |
| Write-row check on the engine | `crates/omnigraph-gqt/tests/gq3_write_rows.rs` | Replays each case's main-branch mutate steps on its fixture; before each single-statement `delete`/`update … where`, runs the edition-2 read of `$x: T  φ′` on main, runs the statement on a fresh branch, and compares the ids a delete removes, or the affected count and changed rows of an update, with that selection. Fails on any disagreement. Writes `target/spike-gq3-rewriter/write_rows.tsv`. |
| Edition-3 cross-check | [`gq3-rewriter/gq3_rewriter_check.rs.txt`](gq3-rewriter/gq3_rewriter_check.rs.txt), run on a worktree of `spike/gq3-parser` | Parses every rewritten text of `corpus.jsonl` (written by the corpus run, with each text's schema) with that branch's `parse_query3`, and compares typed IR through its bridge where the bridge can lower the rewrite. Not committed to either branch's code: it needs both. |
| CLI | `omnigraph queries migrate [--schema S.pg] [--write] [--json] FILE…` (`crates/omnigraph-cli/src/commands/control/mod.rs`, `run_queries_migrate`) | Offline: prints the edition-3 text with the refusals, notes and proof failures as comments, or replaces each file whose rewrite has no refusal. Exits 1 when any source is refused. Registered as CLI-only (`omnigraph-api-types` operations inventory) and in the skill's command index. |

Run:

```bash
cargo test -p omnigraph-compiler --lib migrate::                      # unit tests + corpus table (add -- --nocapture to print it)
cargo test -p omnigraph-gqt --test gq3_write_rows -- --nocapture       # the write rows against the engine
cargo run -p omnigraph-cli --bin omnigraph -- queries migrate --schema schema.pg queries.gq

# edition-3 cross-check, from a worktree of spike/gq3-parser outside this checkout
cp docs/spikes/gq3-rewriter/gq3_rewriter_check.rs.txt <wt>/crates/omnigraph-compiler/tests/gq3_rewriter_check.rs
cd <wt>/crates/omnigraph-compiler && GQ3_CORPUS=<this>/target/spike-gq3-rewriter/corpus.jsonl GQ3_OUT=/tmp \
  cargo test --test gq3_rewriter_check -- --nocapture
```

### Test runs

- `cargo dev-test -p <crate>` selects the whole workspace: the alias carries
  `--workspace`, and Cargo ignores `-p` beside it. I built that graph
  (`cargo dev-test -p omnigraph-compiler --no-run`, 51 test executables) and ran the
  changed packages through the same artifacts with the filterset the local-builds guide
  names: `cargo dev-nextest -E 'package(omnigraph-compiler) | package(omnigraph-cli) |
  package(omnigraph-api-types) | package(omnigraph-skill)'`: **1,281 run, 1,278
  passed, 3 failed**. The three are CLI integration tests on paths this branch does not
  touch: `cli_cluster::managed_lifecycle_capture_and_retry_records_refuse_symlinks_without_reading_targets`
  (exit 1 where 2 is expected; the capture relies on `O_NOFOLLOW`, and the box is a
  gVisor sandbox) and `cli_mcp::mcp_binds_one_graph_and_relays_its_reads_unchanged`,
  `cli_mcp::mcp_lists_and_admits_write_tools_only_with_the_agents_consent` (the bridge
  reports a lost connection). All three fail the same way when run from the CLI
  integration binary the warm snapshot built from `main` before this branch existed; that
  binary still invokes this branch's `omnigraph` executable, so the check does not fully
  separate the two, and I did not rebuild `main`'s CLI.
- `cargo test -p omnigraph-compiler --lib`: 512 passed (after the last change).
- `cargo test -p omnigraph-gqt --locked` (the crate is outside the dev aliases): 463
  GQT cases, 52 runner unit tests, 14 integration, 29 dispatch and `gq3_write_rows`, all
  green, so the span side table changes no case.
- `cargo fmt --all --check` clean; `scripts/check-docs.py` OK (474 Markdown files).
  Clippy, DST and the canonical workspace graph were not run.

## (c) What I did not get to

- **Type-checking the ranking and write rewrites in edition 3.** They parse under
  `spike/gq3-parser` (finding 18), but that branch's bridge refuses `from`, `let` and the
  `$x` statements, so the RFC's typed-IR proof for rows 5 to 9 is unproved here.
- **The `rrf` rows-equality proof.** It needs an edition-3 executor; the corpus cases
  that exercise the row are listed in finding 16.
- **The edition-2 boot rewrite, `cluster plan` output, and `queries migrate --target`.**
  The CLI takes files and an optional `--schema`; it neither reads a cluster's registry
  nor rewrites `.gqt` files (the corpus harness extracts their query text itself and
  writes nothing back).
- **The fixed map for `expect error` codes and result column names.** The run lists the
  steps that need it (findings 4, 8, 9) but does not rewrite expectations.
- **Write steps on other branches and after loads.** The engine check replays only a
  case's main-branch mutate steps up to its first load, loop, concurrent block or
  control write, so it covers the statements listed in finding 11, not every write.
- **`cases_slow/`** (2 files) and `benchmarks/deferred/` (`.disabled`, outside discovery)
  were not run.
- **Workspace-wide gates.** I ran the test suites of the crates I changed (see (b)), not
  the canonical workspace graph, DST, clippy or the docs checks.
