# GQ 3 core: a reference interpreter and what it found

**Audience:** the author of the GQ 3 RFC
**Status:** spike, 2026-11-08; not engine code and not a proposal by itself
**Specification under test:** `docs/rfcs/2026-11-07-gq-3.md` at `ab74f629`
(branch `rfc/gq-3` of `ragnorc/omnigraph`), with its two assets
**Code:** [`gq3-core/`](gq3-core/), Python 3 with the standard library only

This spike implements the core algebra of the RFC's section 2 as a reference
interpreter. It adds a typed surface AST that desugars by the derived-forms
table, runs every edition-3 query of the before-and-after asset, and runs the
core's stated rules against 50,000 random terms. Where the RFC is ambiguous,
the interpreter has a `Config` setting with the RFC's literal reading and a
proposed one. Each finding below cites its setting and the test that shows
it.

## (a) Findings

The findings are ordered by how much they would change the RFC. Each one
names the section it bears on, what was observed, and the proposed change.
Test paths are relative to `docs/spikes/gq3-core/`.

### 1. Order's tie rule is not total, and the RFC never says which operators keep an order

**Section 2, Order; section 4, law 6 ("every cut is total, over one total
order").** The rule is "the keys, then the identities of node columns in
column order, then unspecified within remaining ties *[as today]*". That
leaves three kinds of tie open, and in each case a `Cut` depends on physical
order:

- Rows that differ only in an edge reference. `$p $k:knows $q` over two
  parallel edges, followed by `order { $q.handle } limit 1`, returns either
  edge.
- Rows with no node column: grouped output keyed by a scalar (`return
  { $p.city, count(...) as n } order { n } limit 1` with tied counts), or a
  `union` of names.
- A `limit` with no `order` over scalar rows. The rule orders by node
  identities, and there are none.

The stress run shows the scale: 138 of 12,799 random terms that contain an
`Order` or a `Cut` give different rows across physical orders under the
RFC's rule, and 0 of 28,685 accepted terms do under the proposed rule.

The *[as today]* mark is also inaccurate. Edition 2 already breaks more
ties: `docs/user/queries/index.md` says that for DISTINCT, "Remaining
projected columns break ties ascending", and that ordinary returns break
ties on entity ids.

Related gap: the RFC never says which operators preserve an order. If
`Correlate` keeps its outer order, a `Cut` above it cuts each outer row's
inner rows in physical order. The stress generator found this in my first
evaluator, which propagated the outer order.

*Tests:* `tests/test_findings.py::F01_OrderIsNotTotal` (four cases,
including `test_which_operators_keep_an_order_is_unstated`).
*Settings:* `order_ties`, `correlate_keeps_order`.

*Proposed rule:* the keys, then the identity of every reference column
(node **and edge**, as `(canonical type, id)`, since an id alone is
ambiguous in a polymorphic column), then every remaining column value in
column order, under a fixed total order on values (nulls first ascending, as
edition 2 does). Rows that tie on all of these are equal in every column, so
a bag cannot tell them apart. Also state that only `Order`, and `Filter`,
`Project` and `Cut` over an ordered input, produce an ordered relation; every
other operator's output is unordered, and a `Cut` over it orders first.

### 2. Implicit correlation by name: two of the RFC's own examples are refused, and an unrelated outer variable changes an inner read silently

**Section 3, `from`, Scope and correlation, and `T68`; section 5.** An inner
read sees every outer name, and a name a `from` imports into a scope that
already has it is `T68`. Under those two rules:

- The asset's `colleagues_of_colleagues` is refused. The inner
  `{ from colleagues($q.handle) return { $q as r } }` imports `$q` into a
  scope where the outer `$q` is visible.
- The asset's `weighted` is refused for the same reason: the second inner
  read's `from bm25($docs, …)` imports `$d`.
- Both of those inner reads also omit `match`, which the grammar's
  `read_body` requires.
- Correlation by name is silent. The inner read
  `{ match { $d: Doc } return { count($d) as n } }` returns 6 under
  `$p: Person` and 1 under `$d: Doc`. The outer `$d` turns the inner binding
  into a filter, and nothing reports it.

*Tests:* `tests/test_corpus.py` (`test_colleagues_of_colleagues_is_refused_finding_F2`,
`test_weighted_is_refused_finding_F2`) and
`tests/test_findings.py::F02_CorrelationScope` and
`F02b_InnerBindingIsCorrelated`.

*Proposed change:* choose one of these.

- **(a)** Keep implicit correlation and `T68`. Rewrite the two examples with
  a wrapper read that renames (shown legal in
  `test_wrapper_read_makes_colleagues_of_colleagues_legal`), add `match` to
  their inner reads, and add a lint for an inner binding whose name is an
  outer variable.
- **(b)** Adopt Cypher's explicit import, `from (q) { … }`. Only listed
  names correlate; every other name inside is local.

I recommend (b). It also gives the planner the correlation set
syntactically, and five more changes like this one will each add outer
variables.

### 3. `<e>` and `(e1 | e2)` are not a `Union` of `Expand`s; the asset's `around` is illegal

**Section 2, derived forms; section 3, Kept ("traversal unchanged").** The
derived-forms table lowers both forms to a `Union` of two `Expand`s. That
lowering differs from edition 2, whose rules section 3 keeps unchanged
(`docs/user/queries/traversal.md`: "Alternatives share the same
deduplication and shortest-distance search across all members", and set
semantics over endpoint pairs):

- With mutual edges a↔b, `$a <knows> $x` returns b twice instead of once,
  because the union is a bag.
- With `a knows b` and `b likes c`, `$a (knows | likes){2,2} $x` returns
  nothing instead of c. Each member's own `Expand` misses the mixed path, and
  the members' shortest distances are not shared.

The asset's `around` uses `$p (knows | authored) $thing`, whose members end
on different types. Edition 2 refuses that (`T5`), and the RFC's lowering
refuses it as a `Union` type mismatch. Its `$p *{1,3} $near` has no
declaration for `$near`, which edition 2 also refuses. Separately, its three
`count`s are counts over the cross product of the patterns' matches, which
is probably not what an author wants.

*Tests:* `tests/test_findings.py::F03_AlternationIsNotAUnion`,
`tests/test_corpus.py::Traversal::test_around_is_refused_finding_F3`,
`tests/test_desugar.py::test_alternation_is_a_union_of_two_expands`.
*Setting:* `Desugarer(alternation="rfc" | "selector")`.

*Proposed change:* `Expand(R, $x, sel, m..n, [$k])` takes an edge selector,
a set of `(edge type, direction)` pairs. Both rows of the table desugar to
one `Expand` over a selector. Fix the `around` example.

### 4. Expand's cardinality bound is wrong for `m < n`

**Section 2, Expand ("`≤ |R| · fanout^n`").** With set semantics over every
hop count in `m..n`, the reached set can exceed `fanout^n`. From the root of
a binary tree, `knows{1,2}` returns 6 rows; the table allows 1 · 2² = 4. The
stress run measured 14 such operator calls; the proposed bound held in every
one of 42,842 `Expand` calls.

*Test:* `tests/test_findings.py::F04_ExpandBound`.

*Proposed bound:* `≤ |R| · Σ_{h=m..n} fanout^h`, and `≤ |R| · |T′|`, where
`fanout` counts distinct neighbours, or parallel edges when `$k` binds.

### 5. "Only `min` and `max`" does not make a recursion monotone, and it refuses a monotone `exists`

**Section 2, Fix; section 3, Recursion.** The rule refuses `not` and every
aggregate except `min` and `max` over the recursive relation. It admits
three shapes that are not monotone:

- **A filter that reads a `max` (or `min`) column.**
  `longest($from) @budget(rounds = 20)` is `union(base, { from longest($from)
  $d < 3  $q knows $r … $d + 1 as d })` with `max($d)`, run over
  a→b→c→d→e plus a→c.
  - Semi-naive evaluation returns {b:1, c:2, d:3, e:3}; naive evaluation
    returns {b:1, c:2, d:3}.
  - The semi-naive answer is not a fixpoint: one more application of the
    body drops e.
- **An `Order`/`Cut` over the recursive relation** (an inner `limit 1` over
  `from cut($from)`). Semi-naive evaluation returns a set; naive (Kleene)
  iteration oscillates and never converges.
- **An aggregate over the recursive relation below the read's own
  `return`.** It is not distributive, so semi-naive evaluation sees only the
  delta.

Over 5,000 random recursions, 74 of the 817 that only the RFC's rule
admits gave different answers under naive and semi-naive evaluation.

The rule also refuses a monotone shape. `exists { from members($from) … }`
desugars to `count { … } > 0` (the derived-forms table), so it reads as a
`count` over the recursive relation. 461 random recursions were refused by
the RFC's rule and admitted by the proposed one; every one of the 2,801 it
admits gives the same answer under naive and semi-naive evaluation.

*Tests:* `tests/test_findings.py::F05_MonotonicityRule` (four cases).
*Setting:* `admit_fix(rule="rfc" | "proposed")`.

*Proposed rule*, added to the RFC's:

1. A recursive relation may be aggregated only by its own read's `return`
   (`min`, `max` or `distinct`).
2. Its `min`/`max` columns may not be read by a `Filter`, a `Group` key, an
   `Order` key or a `Cut` inside the cycle.
3. `Order`, `Cut`, `nearest` and `rrf` over the recursive relation are
   refused.
4. `exists` over it is admitted. Either name the semi-join in the core, or
   have the monotonicity check recognise `count { } > 0`.

### 6. Fix is over sets, its rows can be replaced, and its budget counts rounds, not rows

**Section 2, Fix.** "Rows added round by round until none is new" assumes
set semantics, but section 2 defines relations as bags.

- The asset's `reachable` without `distinct`, evaluated over bags on a
  cyclic graph, never converges.
- With `min`/`max`, rows are *replaced* by better values, not added. The
  fixpoint is least in a lattice order, not under inclusion.
- The bound "`≤ |domain|` of its reference columns, or the budget" compares
  a row count with a round count.
- `min` with a non-negative step over a finite graph converges in at most
  |domain| + 1 rounds, so the budget is not what makes `distance`
  terminate. Yet `distance` with `@budget(rounds = 4)` fails on a graph
  where the fixpoint exists at round 5.
- `max` with `+1` on a cycle never converges; the budget is what stops it
  (`E_FIX_BUDGET`).

*Tests:* `tests/test_findings.py::F06_FixSetsAndBudgets`,
`tests/test_rfc_fixtures.py::Recursion` (`distance` under the budget and its
overrun, `max` on a cycle, rounds reported). *Setting:* `fix_set`.

*Proposed change:*

- State that `Fix` evaluates over sets (full-row equality with null as a
  value), and that the read's `return` must be `distinct` or `min`/`max`.
- State the order: inclusion on the key columns, `≤`/`≥` on the
  `min`/`max` columns.
- Write the bound as rows ≤ the domain of the key columns, plus a separate
  round bound of the budget.
- Keep the budget mandatory for computed columns (it is what makes `max`
  safe), and say that it is a cost declaration, not only a safety stop.

### 7. Call: computed recursive arguments, textual substitution, and the plan budget

**Section 2, Call; section 5.**

- **Computed recursive arguments.** `count_down($n) = union(people,
  count_down($n - 1))` has no `not` and no aggregate, so the RFC's
  monotonicity check accepts it. It has no single-relation fixpoint: the
  parameter is invented value. The interpreter refuses a recursive call
  whose arguments are not the caller's own parameters passed through.
- **Textual substitution captures names.** "With its scalar arguments
  substituted" captures a variable when the argument names one the body
  binds. In `friends_of($h) = { $p: Person { handle: $h } $p knows $q }`,
  called as `from friends_of($p.handle)` from a scope with `$p` = erin, the
  substitution produces `$p: Person { handle: $p.handle }`, which matches
  every person. Evaluating the arguments in the caller's scope and running
  the body closed returns erin's friend only.
- **The plan budget.** The RFC does not say where it is declared or what
  its default is. It must count the expanded tree, not the shared DAG: a
  chain of 14 reads, each a `union` of two calls to the one before it,
  compiles to a small DAG and a tree of more than 2¹³ nodes. `let $docs`
  used twice (`hybrid`) is the same case.

*Tests:* `tests/test_findings.py::F07_CallRules`.

*Proposed change:* a recursive call passes its parameters through; `Call`
*binds* its arguments, evaluated in the caller's scope, into a closed body;
name the plan budget's declaration (`@budget(plan = n)` beside `rounds`) and
its default.

### 8. `Union` must say how nullability agrees; the RFC's own `optional` and `distance` need it

**Section 2, Union ("column types must agree by position"); derived forms,
`optional`.** `optional`'s second arm pads with nulls, so its columns are
nullable where the first arm's are not. `distance`'s base arm returns
`1 as d` (`I64`), while its recursive relation's `d` is `min($d)`
(`I64?`). Read strictly, "agree" refuses both.

*Tests:* `tests/test_findings.py::F08_UnionNullability` (both).
*Setting:* `union_nullability`.

*Proposed rule:* base types must be equal; the result is nullable when
either arm is. There is no upcast from node types to a common interface;
that would be a separate rule.

### 9. The ranking population: "exactly one node column" contradicts the RFC's examples and its rewriter

**Section 5 ("their population must carry exactly one node column");
section 6 (the `passages` example and the rewriter rows).**

- Section 6's `passages` passes `{ … return { $r, $p } }`, two node columns,
  to `bm25`.
- The rewriter writes populations that "return every variable P binds", so
  any edition-2 ranked query whose pattern binds two nodes is rewritten into
  a refused query.
- `Fuse` yields `R[$x, s]`, so the `rrf` row's claim that "every downstream
  reference still resolves" is false: `$p.name` after a fused population of
  `{ $p, $d }` is out of scope.

*Tests:* `tests/test_findings.py::F09_RankingPopulation`.
*Setting:* `score_population = "rfc" | "field"`.

*Proposed change:* resolve the ranked column as the unique node column whose
type declares `field` (setting `field`, which accepts `passages`), and refuse
when zero or two columns qualify. Have the `rrf` rewrite join the fused
`[$x, score]` back to the population on `$x`, or state that `rrf` drops the
other columns and have the rewriter re-match them.

### 10. Score and ApproxTopK: column type, membership, statistics, and `k` as rows or targets

**Section 2, Score and ApproxTopK; section 3, relation expressions;
section 4.**

- **Type.** The table types `s` as `F32`; `bm25_v1`'s numeric profile is
  `F64`, and `Fuse`'s is `F64`.
- **Membership.** The table says "an exact score per row". Section 4 makes
  membership the `terms` relation, so `bm25` drops the rows that don't match.
- **Statistics.** "The indexed table's statistics" reads as index-dependent;
  section 4's field corpus is not.
- **`k`.** The operators table and section 3 say `nearest` returns at most
  `k` *rows*; section 4 says at most `n` *targets*, and law 4 says fan-out
  rows carry the score.
  - Over a population with fan-out, `k: 1` returns d1 with both its rows
    (targets), or 1 row, whose choice is unspecified (rows).
  - The stress run measured 422 windows with more than `k` rows.

*Tests:* `tests/test_findings.py::F10_ScoreAndWindowContracts`. *Setting:*
`topk_unit`.

*Proposed change:* type `s` as `F64` for `bm25` (and say what `l2` is); state
that `Score(bm25)` keeps only members; replace "indexed table" with "field
corpus"; make `k` count distinct targets, with the bound "≤ k distinct
targets, every row of each".

### 11. `l2` is not the exact form of `nearest` on a cosine or dot field

**Section 4, vector retrieval; section 8's schema.** Section 4 says "the exact
top `n` is `l2` with `order { $distance, $x.id }` and `limit n`". `nearest`
uses the field's declared distance, and section 8 declares `embedding:
Vector(3, distance = "cosine")`. With documents (1,0,0), (10,1,0) and
(0.8,0.6,0) and the query (1,0,0):

- the exact `l2` top 2 is {x1, x3};
- `nearest`'s exact cosine top 2 is {x1, x2}.

*Test:* `tests/test_findings.py::F11_L2OnANonL2Field`.

*Proposed change:* one exact function whose metric is the field's (for
example `distance(rel, field, $q)`), so that A6 still holds and its name
does not lie on a cosine field; or refuse `l2` on a non-`l2` field and add
`cosine` and `dot` rows.

### 12. Fuse: ordered arms, rank direction, rank ties, and output shape

**Section 2, Fuse; section 4, Fusion.**

- **Arms are not ordered.** The signature takes "ordered scored relations",
  but `l2` and `bm25` produce unordered relations, and the direction (a
  distance ascends, a score descends) is implicit.
- **Rank ties.** Ranks are ordinal by score and then id, as section 4 says.
  So two targets identical in every arm get different fused scores.
- **Output shape.** The output has one row per target; "fan-out rows carry
  its score" suggests otherwise.
- **Column identity.** "The same node column" must say whether name or type
  counts. The interpreter requires both.

*Tests:* `tests/test_findings.py::F12_FuseRanks`, `tests/test_operators.py::FuseT`
(unequal arms with `weights`, a missing arm adds nothing, every refusal).

*Proposed change:* define an arm's rank by its producer: a ranking function
ranks by its own column in its kind's direction, and any other arm must be
an `Order`. State one output row per target, unordered. Either keep ordinal
ranks deliberately or use the mean rank of a tie.

### 13. Null as a group key against three-valued equality: derived difference fails

**Section 2, Group and Orthogonality ("Difference is derived (`not` on
full-row equality)").** `Group` and `distinct` treat null as a value, which
is right. A difference written as `not` over `=` uses three-valued equality,
so `R − R` keeps every row with a null: on the fixture, carol's null city
survives. Also, `return distinct { $p.city }` has 4 rows while
`count(distinct $p.city)` is 3 (as edition 2 documents); the RFC states
neither.

*Tests:* `tests/test_findings.py::F13_NullEqualityForDifference`.

*Proposed change:* name the "not distinct" equality that `Group`, `distinct`
and `Fix` use, and derive difference with it. State that `count(distinct x)`
ignores nulls.

### 14. A6 and the desugaring-uniqueness test fail on the RFC's own derived-forms table

**Section 1, A6; section 10, the desugaring-uniqueness test.** Desugaring 19
surface variants finds exactly five collisions, pairs of forms with one core
term:

1. `exists { p }` and `count { p } > 0`. The derived-forms table says so
   explicitly.
2. The braces `{ handle: $h }` and the filter `$p.handle = $h`.
3. A triple in schema order and the same triple written reversed.
4. `union(a, b, c)` and `union(union(a, b), c)`. This pair is not on the
   formatter's list.
5. `let $r = rel  from $r` and `from rel`.

Only the last fits A6's "literal and reference" exception. `not { p }` and
`count { p } = 0` do *not* collide (`¬(c > 0)` against `c = 0`), although
the formatter rewrites one to the other.

*Tests:* `tests/test_desugar.py::Uniqueness`.

*Proposed change:* restate A6 as "one *canonical* spelling per term, where
the formatter's list is the complete set of non-canonical forms". Make the
test check every collision against that list. Add the nested-`union` row to
the list, or make `union` binary. For the rest of the derived-forms table,
each row is tested in `tests/test_desugar.py::DerivedForms`, and the 5,000
random reads of the stress run desugar to exactly the term built directly.

### 15. Correlate needs a name for "the enclosing row" and a column rule

**Section 2, Correlate ("outer columns then inner") and derived forms.**

- Every correlated derived form (`count { }`, `optional`, a `from` with outer
  references) needs an inner term that *starts* from the current row. The
  core has no leaf for that row.
- `R + R′` has no rule for an inner column with an outer column's name.

*Tests:* `tests/test_operators.py::CorrelateT`, and every structural test in
`tests/test_desugar.py::DerivedForms`.

*Proposed change:* add an `Env` leaf (one row of the correlated bindings),
say that `Correlate` appends only the inner term's new columns, and make a
duplicate name a core-level `T68`.

### 16. `return`, `order` and `limit`: where the projection goes

**Section 2, derived forms ("`return` → `Project`"; "`order`, `limit` →
`Order`, `Cut`").** The table does not say where `Project` goes relative to
`Order` and `Cut`. `top_people` orders by `$p.age`, which it does not return,
so an `Order` above the projection cannot read its key. If the final
projection runs first, the identity tie-break of finding 1 has nothing left
to break on.

*Test:* `tests/test_findings.py::F16_ProjectBeforeOrder`.

*Proposed change:* the desugaring is
`Project(Cut(Order(Extend(R, items), keys + ties), k), items)`. `Extend`
adds the return items beside every match column, and the order keys may
read either.

### 17. Clause order inside `match`: patterns are order-free, `from` and `let` are not

**Section 2, derived forms ("composed in dependency order"); section 3, Scope
("it may not read a variable bound after it").** Edition 2's match is an
order-free conjunction; edition 3's `from` reads only what is written before
it. So `$d: Doc { id: "d1" }  from { match { $d cites $x } … }` correlates
(2 rows), and the same two clauses swapped do not (5 rows).

Separately, permuting patterns changes the core term but not the rows, so
"exactly one core term" needs a stated order.

*Tests:* `tests/test_findings.py::F17_ClauseOrder`,
`tests/test_desugar.py::test_clause_order_changes_the_term_not_the_rows_finding_F17`.

*Proposed change:* state that binders run in written order, that filters
and blocks wait for every match variable they name (a stable topological
order), and that `from` and `let` see only earlier text. The formatter
should then preserve the relative order of `from` and `let` among the other
clauses.

### 18. Writes: conflicting rows, `retire`, and the edge-delete rewrite

**Section 2, sinks; section 3, Writes fed by reads; section 6, the
rewriter.**

- **Conflicting rows.** "For each row, the entity `$x` names takes the
  assignments evaluated under that row" leaves a conflict open. Under
  `$p: Person { handle: "carol" }  $p knows $x  update $p set { age:
  $x.age }`, carol knows two people, so her age is 30 or null by physical
  row order. The RFC also does not say what happens when one entity is
  updated and deleted in one write, or when one key is upserted twice with
  different values.
- **The asset's edition-3 `retire` is not the edition-2 `retire`.** Its
  statements run once per row of `$p … $p $k:worksAt $c`. For a person with
  no employer there is no row, so edition 3 leaves their city while edition
  2 clears it.
- **The rewriter row `delete T where φ` → `$x: T  φ′  delete $x`** has no
  edition-3 spelling when `T` is an edge type: `delete WorksAt where @src =
  $h` becomes `$x: WorksAt`, and a binding takes a node type.

*Tests:* `tests/test_findings.py::F18_Writes`,
`tests/test_desugar.py::test_edition_2_delete_of_an_edge_type_has_no_edition_3_spelling_finding_F18`.
*Setting:* `write_conflicts`.

*Proposed change:*

- Treat two different values for one (entity, property), an update together
  with a delete of one entity, and two different upserts of one key as a
  typed refusal, and apply identical effects once.
- Write `retire` as two statement groups, or document the difference.
- Admit edge-type bindings `$k: WorksAt` (a scan of edges), or rewrite edge
  deletes as `$s $k:worksAt $d  $s.handle = $h  delete $k`.

### 19. Smaller surface points

- **Precedence (section 3): "`from` is the only form that multiplies rows".**
  Two bindings that share no variable multiply rows too.
- **Ordering and arithmetic:**
  - The asset spells a `return` alias in `order` three ways: `n` in
    `headcount`, `$s` in `weighted`, and `$headcount` for a `from` column.
    Pick one.
  - `Order` has a `nulls` argument that the surface cannot spell.
  - The arithmetic rules do not say what `F32` with `F64` gives (the
    interpreter picks `F64`).
- **`if(null, a, b)`.** It takes `b`; state that as the rule.

### 20. Where the RFC is right

The interpreter confirms these claims as stated:

- **The 100-and-11 fixture.** The `l2` form returns the ten documents at
  0.25 by id; the window form with `k: 100` returns none
  (`tests/test_rfc_fixtures.py::HundredAndEleven`).
- **The four population counterexamples of section 4,** including
  bit-identical `bm25_v1` scores when eligibility narrows
  (`PopulationCounterexamples`).
- **`distance` with `min` under `@budget(rounds = 50)`, and its overrun** as
  a typed error.
- **Non-monotone recursions refused with `T70`.**
- **`Group` with no keys** returns one row with `count` 0 and nulls.
- **A `Cut` never widens.** 5,000 random correlated inner cuts under outer
  filters never returned a row the inner cut did not.
- **`Union` refusal.** The typer matched an independent positional oracle on
  all 5,000 random pairs: 2,301 accepted, 2,591 refused with `TUNION`, and
  108 with an arm that was itself ill-typed.
- **`rrf`'s formula,** including a missing arm contributing nothing and
  unequal arms with `weights`.
- **The derived-forms table** gives one core term per surface form, with the
  exceptions of finding 14.
- **Rewriter IR equality for `delete T where φ` on a node type** holds
  (`test_edition_2_delete_where_is_delete_over_filter_over_scan`).

**Reachability at the start node.** `reachable` on a cyclic graph is not
`Expand{1,n}` with the bound removed: the fixpoint returns the start node
through a cycle, and `Expand` never does (edition 2 returns it only through
its own self-loop). Say so where the RFC calls `reachable` "the unbounded
case" (`Recursion::test_reachable_on_a_cyclic_graph_against_bounded_expand`).

## (b) What was built and how to run it

```text
docs/spikes/gq3-core/
  run.py              every test, then the stress harness; non-zero exit on any failure
  gq3/model.py        types, values, schema, the graph snapshot
  gq3/core.py         scalar expressions; the 13 operators; 3 sinks; Call; typer; evaluator
                      with a cardinality check per operator; Fix (naive and semi-naive);
                      the monotonicity check (rfc and proposed); Config
  gq3/search.py       analyzer (NFC, lowercase, simple tokens), terms(...), edit distance,
                      membership, bm25_v1 over the field corpus, l2/cosine/dot
  gq3/surface.py      surface AST of section 3; Desugarer following the derived-forms table;
                      Program: Call inlining with bound arguments, SCCs to Fix, plan budget
  gq3/fixtures.py     section 8's schema; the test graph; the 100-and-11 graph
  gq3/corpus.py       every edition-3 query of the before-and-after asset, hand-translated
  gq3/dsl.py          builders used by the translations
  gq3/stress.py       random graphs and random terms; the seven stress properties
  tests/              142 tests: operators, closure table, derived forms and uniqueness,
                      corpus, RFC fixtures, section 4 lexical contracts, one class per finding
```

Run it from `docs/spikes/gq3-core/`:

```bash
python3 run.py                      # 142 tests, then 50,000 stress terms (about 20 s)
python3 run.py --terms 5000 --seed 1
python3 -m pytest -q                # the tests, with a 2,000-term stress run
python3 -m gq3.stress --terms 50000 --seed 20261108
```

The stress run interleaves five kinds of term over a new random graph every
50 terms (2–6 people and documents, random parallel edges and self-loops,
nulls). At seed 20261108 it ran 50,000 terms in about 20 s:

| Property | Result |
|---|---|
| Every term terminates | 50,000 of 50,000; `Fix` has an internal stop that no term the proposed rule admits reached |
| Every operator within its bound | 453,025 checks, 0 violations of the stated or proposed bound; the RFC's `Expand` bound was exceeded 14 times (finding 4) and windows held more than `k` rows 422 times (finding 10) |
| `Order` is total | 28,685 of 28,685 accepted terms give the same rows in the same order across two physical orders under the proposed rule; 138 terms are nondeterministic under the RFC's rule (finding 1) |
| `Union` refuses mismatches | typer equal to a positional oracle on 5,000 of 5,000 random pairs |
| Fix: naive = semi-naive | 2,801 of 2,801 recursions the proposed rule admits; 74 of 817 recursions only the RFC's rule admits disagree (finding 5) |
| Desugaring = the direct core term | 5,000 of 5,000 random surface reads |
| `Cut` never widens | 5,000 of 5,000 |

A second seed (`--seed 1`) is also clean. No Rust crate was changed, so no
`cargo dev-test` run applies; nothing under
`crates/omnigraph-reference-engine/` was touched.

**Fidelity notes.** These are the places where the interpreter chooses
something the RFC leaves open. Each one has a `Config` setting and a
finding.

- `Expand` uses edition 2's shortest-distance semantics. Set semantics apply
  to every hop range; one row per edge only at `1..1` with `$k`.
- `Score` and `ApproxTopK` compute exact results. `ApproxTopK` carries
  `recall` and reports itself exhaustive.
- `Fuse` ranks each arm by its producer (finding 12).
- The surface is an AST, not a parser. The corpus translations are by hand,
  with the asset's text quoted above each one.

## (c) What was not done

- **No parser.** The grammar of section 7 is not exercised: the arithmetic
  precedence ladder, `$n -1`, the `from` lookahead, `let`/`from` as
  property names, and the statement lookahead.
- **No catalogue types** (`@Operator`), annotations other than
  `@budget(rounds)`, policy, `as commit` or `as unattached branch`
  semantics. A write runs as one snapshot-to-snapshot function.
- **No search-layer features beyond the core:**
  - Analyzer profiles are reduced to one, with no stemmer, stop words or
    folding.
  - There are no index states, so law 5 holds trivially.
  - There is no WAND path.
  - Raw-vector dimension checks are static only.
- **No cost model,** no estimates, and no separate structural totality lint.
  A1 is checked here only by running: the bound checks and the plan budget.
- **Integer widths are not tracked at run time.** Literals carry `I64`, so
  `I32` overflow is not detected at run time; the static type rules are
  implemented.
- **Limited recursion coverage.**
  - Mutual recursion is covered only for reads with identical parameter
    lists.
  - A `Fix` whose body reads an outer correlated variable was not tested.
  - The proposed monotonicity check tracks `min`/`max` columns by name, so a
    rename inside the cycle would hide one.
- **No rewriter.** The edition-2 forms appear only as the two
  `delete`/`update … where` statements, used to test the rewriter's IR
  equality.
