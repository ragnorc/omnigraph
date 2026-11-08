# Spike: the edition-3 type checker of the GQ 3 RFC

**Branch:** `spike/gq3-typeck`, from `spike/gq3-parser` (`0162a330`). This is throwaway prototype code.
**Specification:** `docs/rfcs/2026-11-07-gq-3.md` at `rfc/gq-3` `d79b6ea7` (the 2026-11-08
revision), with its assets `2026-11-07-gq-3-before-and-after.md` and
`2026-11-07-gq-3-grammar-comparison.md`. The parser spike's report (`docs/spikes/gq3-parser.md`)
and the core spike's report and interpreter (`spike/gq3-core` `c5ba069a`,
`docs/spikes/gq3-core/`) were read and used as evidence and oracle.
**Question:** can sections 2, 3 and 5 be type-checked as written, into a typed IR, without
changing what any edition-2 text means? Do the two tests section 10 names, the
desugaring-uniqueness test and the closure table, hold?

## (a) Findings

Each finding names the RFC section it bears on, what was observed (the exact query or the test
that shows it), and the change proposed. Test names are in
`crates/omnigraph-compiler/src/query/typeck3_tests.rs` unless another file is named.

### Breaks the RFC does not list

**1. An inner binding of an outer name is an eighth break, with no rewriter row**
(section 2, `Correlate`; section 3, "Added: two clauses" and refused respellings; section 6, the
rewriter; Compatibility, "seven spellings").
- Edition 2 reads a block's binding of an outer variable as a filter on the outer row:
  `match { $p: Person  not { $p: Person { name: "bob" } } }` means "`$p` is not bob".
- The RFC's `T68` covers "a binding inside the inner term that reuses an outer variable's name",
  and every block desugars to a `Correlate` inner term, so edition 3 refuses it.
- Five GQT cases depend on the edition-2 reading: `issue_605_two_anonymous_destinations.gqt`
  (`bound_twice_in_negation`, `rebind_inside_negation_non_root`),
  `issue_659_compiler_scopes.gqt` (`future_outer_selected_edge_does_not_capture_child_edge`),
  `issue_763_exists_predicate.gqt` (`outer_variable_rebound_after_the_inner_binding`,
  `search_inside_the_block_runs_after_a_rebinding`).
- The core spike's interpreter accepts the same input (its `T68` checks `from` only), so the
  two executable readings of the RFC disagree here (`compare_core.py`, probe
  `inner_rebinding_in_not`).
- **Change:** keep `T68` for blocks and add a rewriter row, `not { $p: T { k: v } … }` →
  `not { $p.k = v … }` (the same `Filter`, so IR-equal), and count it in Compatibility. Test:
  `an_inner_rebinding_that_edition_2_reads_as_a_filter`.

**2. A block that names a later-bound variable changes meaning, silently when the types agree**
(section 2, derived forms: "a filter or block placed after the last variable it names";
section 3, "Scope and correlation").
- Edition 2 correlates a block only on variables bound *before* it; `issue_659_compiler_scopes.gqt`
  pins this (`future_outer_name_is_not_correlated`, `future_outer_name_not_block`,
  `future_outer_node_does_not_capture_grandchild_node`, `nested_local_names_remain_independent`).
- Under the RFC's rule the block waits for every variable it names, so it correlates on the later
  one. In those four cases the types differ, and edition 3 refuses them (`T5`).
- With the types equal, both editions accept the text and it means two things:
  `match { $p: Person  not { $p knows $x }  $x: Person { handle: "q" } }` is "`$p` knows nobody"
  in edition 2 and "`$p` does not know `q`" in edition 3. Test:
  `a_block_naming_a_later_variable_changes_meaning_silently`.
- **Change:** a block correlates only on names bound before it, as `let` and `from` already
  do (section 3) and as edition 2 does. Filter clauses can keep waiting for their variables,
  since a filter has no local names to capture.

**3. Two different upserts of one key in one write are a break**
(section 2, sinks; Compatibility).
- Edition 2 accepts `insert Doc { slug: "same", note: "first" }  insert Doc { slug: "same",
  note: "second" }`, and the last write wins (`write_max_bytes_superseded_upsert_payload.gqt`,
  `upsert_twice`).
- The RFC refuses "two different upserts of one key within one write". The checker refuses it
  statically (`T76`) when both keys are literals.
- **Change:** list it in Compatibility, or see 4, which removes it.

**4. "One group per statement" has no spelling, and one group cannot run `hire`**
(section 2, sinks; section 3, "Writes fed by reads"; section 6, the rewriter row "a body of
several statements | one group per statement"; section 7, `write_group`).
- `write_group = (!stmt_keyword ~ clause3)* ~ mutation_stmt3+` puts consecutive statements with
  no clause between them in one group. So the rewriter cannot write edition 2's
  `insert Person {…}  insert WorksAt { from: $h, … }` (the asset's `hire`) as two groups.
- In one group, "every sink sees the one snapshot the group's clauses read". The edge insert's
  endpoint lookup then cannot see the person inserted beside it, which edition 2 runs in order.
- The parser spike refused any clause after a statement (`Q013`). This branch implements the
  group rule in the grammar (`write_group3`); a clause after the *last* statement is still `Q013`.
- **Change:** state that a group's clauses are evaluated once (A2), and that its statements run
  in order, each keyed effect (upsert, endpoint resolution) seeing the statements before it, as
  edition 2 does. This also makes 3 a non-break, and leaves the conflict refusal for
  same-entity effects reached through the clauses' rows.

**5. Six smaller acceptance changes edition 2 → 3, all from the RFC's own rules**
(section 6, Compatibility "everything else is additive"). In the corpus comparison
(`the_edition2_corpus_type_checks_alike_in_both_editions`):
- Edition 3 accepts nine texts edition 2 refuses:
  - `T40` lifted (1);
  - a traversal with no anchored endpoint, which edition 2 refuses as unexecutable (`T5`) and
    the core reads as a `Correlate` (4);
  - an order key that is not a return item (`T42`, 1);
  - a Boolean or property item beside aggregates (`T9`, `T58`, 2).
- Edition 3 refuses three texts edition 2 accepts: an order key over grouped rows that is a
  meta-field neither returned nor a key (`meta_fields_resolve_by_role.gqt`
  `aggregate_identity_order`; `issue_955_aggregate_comparison.gqt`, two cases). Under
  `Project(Cut(Order(Group(…))))` an order key over groups reads only keys and aggregates.
- **Change:** list the refusals as breaks, or define how an order key over groups reads a
  constant-per-group column. List the acceptances as intentional additions, and say whether the
  planner must execute an unanchored component.

### Where the RFC cannot be checked as stated

**6. Monotonicity must be stated on the term, not on spellings**
(section 2, `Fix`; section 3, Recursion: "`exists` over its rows is admitted", "no aggregate
over them except `min` and `max` in its own `return`").
- `exists { p }` *is* `count { p } > 0` (the derived-forms table), so the rule as written refuses
  `exists`.
- The checker admits a count block over the recursive rows only when its column is read as
  `> 0` or `>= 1` in a filter. It refuses that count under `not`, in any other comparison, or
  as a value.
- `distinct` (a `Group` with no aggregate) inside the cycle must also be named. The checker's
  first cut refused the RFC's own mutual recursion: the second read's `return distinct` is a
  `Group` over the recursive rows.
- **Change:** state the rule over the core term: a `Group` over the recursive relation is
  admitted when it has no aggregate, or when it is the read's own return with only `min` and
  `max`. A block count over it is admitted only in its `exists` reading.
- Tests: `t70_for_a_non_monotone_cycle_and_a_budget_overrun`; probes
  `exists_over_recursion`, `not_over_recursion` (both checkers agree).

**7. The type of a recursive read is not defined**
(section 2, `Fix`; section 5, `Call`).
- Typing `distance` needs `distance`'s own result type inside its body.
- The checker first types a base pass, skipping the `union` arms that reach the read. It then
  types the body again under that assumption until the type is stable (only nullability can
  change). For the RFC's `distance`, `d` is `I64?`: the base arm's `1 as d` is `I64`, and the
  return's `min($d)` is nullable.
- A recursion with no arm that avoids the call is refused (`T70`, "a recursion needs a base
  case").
- **Change:** say that a recursive read's type is the fixpoint from its base arms, and that a
  self-call with no base arm is `T70`.

**8. Recursion in release B: compiled or refused?**
(section 3: "until then a self-call is `T70` naming it"; the freeze list: "a declared read
calling itself is `T70`"; section 5: "a monotone cycle compiles to `Fix`").
- The checker compiles a monotone cycle to a typed `Fix` node, the compiler's half, and the
  planner reports `P006`.
- **Change:** say which layer refuses in B. Proposed: the type checker accepts, so that every
  `T70` rule is tested at B, and the planner refuses with stage Rec named.

**9. A filter's position makes three spellings one term, and the formatter lists none of them**
(A6; section 2, derived forms; section 6, `queries fmt`).
- These three lower to one term under "a filter … placed after the last variable it names",
  and no formatter row names them:
  - `$p: Person  $q: Person  $p.age > 3`
  - `$p: Person  $p.age > 3  $q: Person`
  - `$p.age > 3  $p: Person  $q: Person`
- The uniqueness test pins these as the only unlisted collisions among 38 forms
  (`desugar3_tests.rs::two_forms_share_a_term_only_when_the_formatter_names_one`).
- **Change:** add "a filter moves to just after the binder of the last variable it names" to the
  formatter's list. The alternative, keeping a filter at its written position, would make
  position part of the term.

**10. Two formatter rows change the typed IR**
(section 6: the formatter "never changes the typed IR"; section 2, `exists` and `not`).
- `count { P } >= 1` → `exists { P }` rewrites `Filter(c >= 1)` to `Filter(c > 0)`.
- `count { P } = 0` → `not { P }` rewrites `Filter(c = 0)` to `Filter(¬ (c > 0))`.
- The rows are equal only because a count is a non-negative integer. The same test asserts these
  are the only two formatter rows whose forms lower to different terms; the other 13 rows and
  A6's literal-and-reference exception are exactly collisions.
- **Change:** normalise count comparisons with 0 and 1 to the `exists` term in the core, or move
  the two rows to a separate list of rows-equal rewrites.

**11. Integer literals in arithmetic need a rule, and so does assignment**
(section 2, arithmetic; section 5).
- "A narrower integer widens to the wider operand", with a literal typed `I64` as edition 2 types
  it, makes `$p.age + 1` an `I64`. The asset's `birthday` (`update $p set { age: $p.age + 1 }`,
  `age: I32?`) then needs a narrowing the RFC never states.
- The checker gives an integer literal the other operand's type when it fits (edition 2's
  contextual literal typing), so `$p.age * 2` is `I32`. It refuses assigning a wider number to a
  narrower property.
- The core spike types every literal `I64`. Its `birthday` passes only because it does not check
  assignment widths.
- **Change:** state the literal rule (contextual; the literal must fit), and the assignment rule
  (no implicit narrowing).

**12. Arithmetic cases the RFC leaves open** (section 2, arithmetic). The checker chose these;
the RFC should state them:
- mixed signedness: `U32 + I32` is `I64`, and `U64` with a signed integer is refused (`T72`);
- a float literal is `F64`, so `$f * 0.5` over an `F32` is `F64`, and `$distance * 0.5` widens;
- `%` over floats is admitted;
- `Date` arithmetic is refused (`T72`; Unresolved question 5).

Test: `arithmetic_widens_and_propagates_null`.

**13. The term needs placement rules the RFC does not state**
(section 2, derived forms: "binders in written order").
- A filter that names no variable, only parameters, has no "last variable". The checker places
  it after the first binder, or over `Unit` when the scope has none.
- A triple with neither end bound starts somewhere. The checker scans the edge's schema source
  for a directed single member, the smaller name for `<e>`, and the written source otherwise.
- With neither end typed, the written order *is* the schema order, as today: `$c worksAt $p` alone
  makes `$c` the Person. So "a directed triple to schema order" (the formatter) is a rewrite only
  when an end is typed.
- These choices decide term identity, which A6 and the uniqueness test depend on.
- **Change:** state them in the derived-forms table.

**14. Section 10 contradicts section 5 on unknown annotations**
(section 10, "Type-check tests": "an unknown annotation retained"; section 5 and the freeze list:
refused except the metadata allow-list).
- The checker follows section 5: `T80` for any other name, `@budget(plan, rounds)` typed, and
  `@budget(rows | ms | on_exceed)` refused with stage N named.
- **Change:** fix section 10's line. Test:
  `an_unknown_annotation_is_refused_and_metadata_is_retained`.

### Where the typed IR needs a node the RFC does not name

**15. `Unit`, `Env`, `RecRef`, `Call` as a node, and a bound-destination `Expand`**
(section 2, Operators; A3). `crates/omnigraph-compiler/src/ir/core3.rs` holds the thirteen
operators, and also needed these:
- **`Unit`**, the one empty row: the input of a write group with no clauses ("runs once over the
  one empty row"), and of a filter in a scope with no binder.
- **`Env(cols)`**, the leaf the `Correlate` semantics names but the operator table does not. To
  keep binding semantics it must expose exactly the inner term's free variables. A `let $r`
  relation typed at its `let` and used after more names are bound must not see those names.
- **`RecRef`**, the recursive relation inside a `Fix`.
- **`Call { name, bindings, body }`** kept as a node. Binding, not substitution, needs a frame
  that holds the arguments typed in the caller's scope, and A3 captures "a declared helper …
  with the plan".
- **A triple whose two ends are already bound.** `Expand` only adds a column. The checker
  expands to a fresh column and filters it equal to the bound end. The RFC should say whether
  `Expand` has a bound-destination mode.
- **Hidden aggregate columns.** A block value is `Correlate(R, Group(p, [], [agg as _bN]))`, and an
  expression over aggregates in `return` (`count($p) > 3 as big`, which edition 2 accepts) is a
  `Group` followed by a `Project`. The desugaring row "with one [aggregate] it is a `Group`"
  should say so.
- **`Score`'s query** is a vector expression or a `LexicalQuery { text, mode, max_edits }`,
  defaults filled, so that `bm25(…, $q)` and `bm25(…, terms($q, mode: any))` are one term.
- **Change:** list `Unit`, `Env` and `RecRef` in section 2's table, and the bound-destination
  `Expand`.

### Codes the error table needs

**16. Section 5's refusals without a code, and the code split for rankings**
(section 5; section 4, Errors).
- The RFC names `T65`, `T68`, `T69` and `T70`, and reuses `T7`, `T15`, `T17` and `T27`. The
  checker allocates the rest (`codes.rs`):

  | Code | Refusal |
  |---|---|
  | `T71` | an option unknown (listing the admitted names), out of range, or positional |
  | `T72` | an arithmetic operand not numeric |
  | `T73` | `union` arms that disagree, naming the arm and column |
  | `T74` | a name refused as a relation: a write drawn from a read (A7), a declared read named like a registry row or clause keyword, a declared read called from a write before stage W2, a column that is not variable-shaped (the RFC's "the type checker names the column and the alias to add" has no code) |
  | `T75` | `rrf` arms: count outside 2..16, duplicate arms, an unranked arm, two target columns, a `weights` length |
  | `T76` | a write mixing constructive and destructive statements, or two conflicting effects |
  | `T77` | a statement's variable that is not a bound node or edge reference |
  | `T78` | an endpoint assignment of the wrong type |
  | `T79` | a ranked column that is not a node column of the population |
  | `T80` | an annotation that is not read, or that names a later stage |
  | `P006` | a core node the planner does not lower yet |

- `T74` holds four refusals and should be split.
- The RFC writes "`T27` *[as today]*" for every ranking's property error. Today `T27` is the
  full-text-index code and `T15` the code for nearest operands. The checker uses `T27` for
  `bm25`, `T15` for `distance` and `nearest`, and `T79` for the column.
- The execution errors the RFC types but does not code also need codes, proposed under a new `E`
  namespace: integer overflow, a zero divisor, a non-finite float, a round-budget overrun, and a
  write conflict found at run time (one entity reached by two rows with different values, which
  no static check can see).

### Where the assets and the other spikes are wrong

**17. The asset's schema is not today's `.pg`, and three asset examples are refused**
(the before-and-after asset; section 8).
- `body: String @index(fulltext)` is refused by today's schema parser ("@index on Doc references
  unknown property 'fulltext'"). Today a String's `@index` is its full-text index. The asset
  says the schema "is `.pg` text and is the same in both editions". The test reads it with
  `@index`. Test: `the_asset_schema_is_not_todays_pg`.
- **`names`** binds the interface `Named`; edition 2 refuses that (`T1`), although the asset
  calls it unchanged. The core spike admits it; this checker follows edition 2 and names stage
  Poly. Interface bindings are in neither Kept nor Added.
- **`mentions`** runs `fuzzy` over `Doc.title`, which has no full-text index: `T27` in both
  editions.
- **`weighted`** is still `T68` after the 2026-11-08 fix. The second inner read's
  `from bm25($docs, …)` draws `$d` into a scope where the first `from`'s `$d` is visible. Both
  checkers agree. Any reuse of a population after its column name is bound collides. A legal
  spelling renames at `let` level, before `$d` is bound:
  `let $docs2 = { match { from $docs } return { $d as d2 } }`, then `bm25($docs2, $d2.body, $q)`
  (test `weighted_type_checks_with_the_population_renamed_before_d_is_bound`).
- **Change:** fix the three examples. Also consider the core spike's F2(b), explicit correlation
  imports, which removes this class.
- Test: `every_edition3_example_of_the_asset_type_checks` pins every other asset declaration
  accepted (49 of 52, the later-stage block included).

**18. The parser spike accepts bare identifiers as clauses, and some refusals name grammar rules**
(section 7: "A bare identifier is an operand nowhere in a `match` or `return`"; section 5: "a
parse error names the construct, never a grammar rule"; "a refusal of a form a later stage adds
names the stage").
- `match { $p: Person  delete $p }` parses as two filter clauses (`delete`, `$p`). The type
  checker refuses it (`T6`, "a bare identifier `delete` is not a value in a read"), but the
  red-team row wants a parse error at the word.
- The asset's `retract` gets generic `Q001`s naming grammar rules: for `$c: Commit` ("expected
  type_ref") and for `returning { }` ("expected kw_and, kw_or, …").
- Section 5 decides infix `contains` at type check, because the left operand's type picks the
  fix. The parser spike refuses it at parse (`Q012`), so the list case's `in` fix cannot be
  chosen.
- **Change (parser):** implement the bare-identifier rule in `operand3`, and add recognizers that
  name the stage. Then move infix `contains` to a tree node the checker refuses. (The checker
  already refuses `contains(list, x)` with the `in` fix, `T7`.)

**19. Endpoint assignments and the core spike's write checks**
(section 5: "an endpoint assignment is a key literal of the endpoint's key type").
- Edition 2 accepts a String for an integer key: `insert Knows { from: "0", to: "100" }` over
  `id: I64 @key`, in six GQT cases. The checker keeps that (as today).
- **Change:** state it, or add a rewriter row.
- The core spike's interpreter runs `delete $k  update $p` in one write without refusing it
  (probe `update_and_delete_one_write`), against the RFC's per-write split. That is an oracle
  gap, not an RFC question.

### Where the RFC is right

**20. The derived forms, the closure table and the scoping rules hold as written**
(sections 2, 3 and 5).
- **Derived forms.** Every row of the table lowers to exactly the term built directly, for 15
  rows plus `return`/`order`/`limit`, `distinct`, `rrf`, `Call`, `Fix`, write groups and
  edition 2's `delete T where φ`
  (`desugar3_tests.rs::every_surface_form_lowers_to_the_term_the_table_names`). "Nothing in the
  table needs an operator outside the thirteen and the three sinks" holds, apart from the leaves
  in 15.
- **Formatter rows.** Thirteen formatter rows and A6's literal-and-reference exception are
  exactly collisions: braces and filter, a reversed directed triple, nested and flat `union`,
  alternation order, `<e>` ends, `asc`, a same-name alias, a split `and`, `bm25` over
  `terms(q, mode: any)`, defaulted options, `fuzzy` with two edits, and `let $r … from $r`
  against `from rel`.
- **Closure table.** All 19 cases are accepted or refused with the stated reason
  (`desugar3_tests.rs::the_closure_table`).
- **`T40` lifted.** It needs no other rule (`t40_lifted`).
- **`optional`.** Its nullable columns fall out of `Union`'s nullability rule (`optional_served`).
- **Ranking populations.** A population with two node columns works because the call names its
  column; with none, the column error names the population's node columns
  (`a_relation_function_over_zero_and_two_node_columns`).
- **`rrf`.** It yields the target and `score` only, and a correlated `from` draws the rest
  (`the_ranking_functions_type_their_result_columns`).
- **`T68`'s fix.** An alias on the inner return always clears it.
- **`Call`.** Binding keeps the core spike's capture example closed
  (`a_call_binds_its_arguments_and_never_substitutes`).
- **The edition line.** It isolates edition 2 completely. No edition-2 text's verdict changes
  through the dispatcher (`every_edition2_text_type_checks_exactly_as_before`).
- **The corpus.** Read as edition 3, 1,525 of 1,731 GQT declarations get the same verdict from
  both checkers. Every acceptance change is an RFC rule (findings 1 to 5) and is pinned in the
  test. The remaining code-only differences are refusals both editions make, with another code
  first.
- **The core spike's typer.** It agrees with this checker on 53 of 57 shared inputs
  (`docs/spikes/gq3-typeck/compare_core.py`). The four differences are findings 1, 17 (`names`,
  and `mentions`, which the interpreter cannot see because it has no index model) and 19.

## (b) What was built and how to run it

| Piece | Where | What it does |
|---|---|---|
| Core IR | `crates/omnigraph-compiler/src/ir/core3.rs` | The thirteen operators, the sinks, `Call`, and the leaves `Unit`, `Env` and `RecRef`, each node typed and `PartialEq`: relations `R[c: t]`, scalar expressions with `can_fail`, `Agg`, `LexicalQuery`, `CoreRead` and `CoreWrite`. The planner does not lower it. |
| Type checker | `crates/omnigraph-compiler/src/query/typeck3.rs` | `typecheck_file3(catalog, &File3)` and `compile3(catalog, source)` give each declaration a `CoreDecl` or a typed `QueryDiagnostic`. It covers: `let` (scalar and relation); `from` with `T68` and the variable-shape check; inline reads; `Call` with bound arguments, defaults, recursion detection over frames, pass-through arguments, the type fixpoint, the monotonicity walk (`T70`), `@budget(plan, rounds)` and the plan budget over the expanded tree; `distance`, `nearest(k:, recall:)`, `bm25` over a String or `terms(…)`, `rrf(k:, weights:)` and `union`; arithmetic; block values (`T40` lifted); `optional`; `$k: E`; write groups with `insert`, `update $x` and `delete $x`, `null`, the constructive-or-destructive split, static conflicts and endpoint typing; and the annotation allow-list. As-today rules reuse the edition-2 checker's traversal resolution, literal typing and compatibility (now `pub(crate)`; no behaviour change). |
| Bridge | `bridge3.rs` | The parser spike's placeholder `T60` is gone. A ranking written as a scalar is `T65` with the `from` fix, a positional `fuzzy` budget is `T71`, and every other new node is `P006`, which the edition-3 checker types instead. Shared-subset text still lowers to the edition-2 tree, which the planner runs. |
| Grammar | `query.pest`, `ast3.rs`, `parser3.rs`, `print3.rs` | Write groups (finding 4); nothing else changed. |
| Codes | `codes.rs` | `T65`, `T68` to `T80` and `P006` (finding 16). |
| Tests | `typeck3_tests.rs` (30), `desugar3_tests.rs` (3) | Section 10's type-check tests, the asset type-checked, the corpus checked by both editions, the core-spike probes, the uniqueness test and the closure table. |
| Asset copy | `src/query/testdata/2026-11-07-gq-3-before-and-after.md` | Refreshed from `rfc/gq-3` `d79b6ea7`. The parser spike's asset test now expects every block to parse (its F3 and F4 are fixed in the asset). |
| Oracle comparison | `docs/spikes/gq3-typeck/compare_core.py` | Runs the core spike's typer on the asset and 14 probes, and prints where it disagrees with this checker's pinned verdicts. |

Run:

```bash
cargo test -p omnigraph-compiler --lib typeck3          # type-check tests, asset, corpus, probes
cargo test -p omnigraph-compiler --lib desugar3         # uniqueness test and closure table
cargo test -p omnigraph-compiler --lib the_edition2_corpus_type_checks -- --nocapture   # the verdict table
GQ3_ALL=1 cargo test -p omnigraph-compiler --lib the_edition2_corpus_type_checks -- --nocapture   # every differing case
cargo test -p omnigraph-compiler --lib every_edition3_example -- --nocapture            # the asset, per declaration
git archive fork/spike/gq3-core docs/spikes/gq3-core | tar x -C /tmp/core
python3 -I docs/spikes/gq3-typeck/compare_core.py /tmp/core/docs/spikes/gq3-core
```

Verification on the branch:
- `cargo test -p omnigraph-compiler --lib`: 556 tests pass. That is every pre-existing test, the
  parser spike's 30, and the 33 added here.
- `cargo clippy -p omnigraph-compiler --all-targets --locked -- -D warnings -W clippy::dbg_macro`
  is clean, and `cargo fmt --all --check` is clean.
- `cargo dev-test -p omnigraph-compiler` (the alias runs the whole canonical workspace graph), run
  with `RUST_MIN_STACK=16777216` and `--no-fail-fast`: 5,215 passed and 10 failed. The compiler
  crate's 555 (at that commit) all passed. All 10 failures are in crates this branch does not
  change, and all are the sandbox facts the parser spike's report lists (gVisor, running as root):
  - 5 symlink-refusal tests: `omnigraph-bench` archive (3) and projection (1), and
    `cli_cluster::managed_lifecycle_capture_and_retry_records_refuse_symlinks_without_reading_targets`;
  - 4 `omnigraph-bench` machine-identity tests: `/proc/self/status` has no `Cpus_allowed_list`;
  - 1 engine test, `unreadable_legacy_object_asks_for_a_rerun`: root reads a mode-000 file.

Only `crates/omnigraph-compiler` changed, and nothing under `crates/omnigraph-reference-engine/`.

## (c) What I did not get to

- **Planner lowering of the core IR.** Every new node is `P006`. Edition-3 text that has an
  edition-2 node still runs through the bridge. Nothing checks the core term against the bridged
  `QueryIR` for the shared subset, beyond the parser spike's tree equality.
- **Byte-range suggestions.** The checker's fixes are `fix` text, not the `Suggestion` edits
  section 5 requires. So the "every printed fix applied and reparsed" test does not cover the
  new codes, and type errors carry no position.
- **The section 4 schema surface.** That is `analyzer` and `scorer` on `@index`, and
  `Vector(n, distance = …)`. Today's `.pg` grammar has neither, so `scorer = "none"`, analyzer
  profiles and per-field distance are not checked; `distance` is typed `F32` whatever the field
  declares.
- **Lexical rules.** Only literals are checked for a token-empty query, with a simplified
  tokenizer. `match_terms` is typed, but its `T38` placement is checked only as today's search
  predicates are.
- **Monotonicity provenance.** Lattice and computed columns are tracked by name through
  `Project` and `Union`. A rename through a `Call` body inside a cycle is followed only as far as
  the column names match.
- **Write conflicts.** Only the static cases are refused: one variable updated twice with
  different values, an update and a delete of one variable, and two literal upserts of one key.
  Conflicts reached through rows are run-time errors, with no code yet.
- **Interfaces.** Polymorphic columns are refused (`T1`), as edition 2 does, rather than modelled.
- **GQT cases.** The `expect error` cases section 11 asks for are not written.
- **The rewriter, `queries fmt`, the competence instrument and a property test over the checker.**
  None were attempted.
