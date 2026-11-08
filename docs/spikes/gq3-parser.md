# Spike: the edition-3 grammar and parser of the GQ 3 RFC

**Branch:** `spike/gq3-parser` (from `main` `e7e8e03b`). This is throwaway prototype code.
**Specification reviewed:** `docs/rfcs/2026-11-07-gq-3.md` at `rfc/gq-3` `ab74f629` (sections 3, 6, 7 and 10), together with
its assets `2026-11-07-gq-3-before-and-after.md` and `2026-11-07-gq-3-grammar-comparison.md`. The
earlier spike's report (`spike/gq3-1202`, `docs/spikes/gq3-1202.md`) was also read; its tests and
its generator shape were reused, but not its rules.
**Question:** does the parser grammar of sections 3, 6 and 7 parse what the RFC says it parses,
and only that? Does every text keep one tree? Does every edition-2 text keep its meaning?

## (a) Findings

Each finding names the RFC section it bears on, the exact query or test, and the proposed change.
A finding marked **implemented** is a deviation that the spike's grammar
(`crates/omnigraph-compiler/src/query/query.pest`, rules marked `SPIKE-DEVIATION (Fn)`) applies,
so that the property test and the example tests can run green. Every implemented deviation has a
test that pins the behaviour it chose.

### Defects: the grammar as written refuses its own examples

**F1. An inline read is not reachable as a call argument** (section 7, `relation_expr`, `call`;
section 3, relation expressions).
- `relation_expr = inline_read | call | variable` admits `{ … }` only directly after `from` and
  `let $r =`.
- A call's positional argument is an `expr` (the call rule "as revised on 2026-11-01"), and no
  operand starts with `{`.
- So every ranking and union example in the RFC and the asset fails as written:
  `from nearest({ match { $d: Doc } return { $d } }, embedding, $q, k: 20)`,
  `from union({ … }, { … })`, and `from bm25({ … }, text, $q)` in section 6.
- **Change:** `positional = { !(ident ~ ":") ~ (inline_read | expr) }`. **Implemented.**
  Do not make `inline_read` an operand: that would let a filter clause begin with `{`, and the
  RFC's claim that "a clause cannot start with `{`" protects `inline_read`.
- Tests: `rfc_section_8_examples_parse`, `from_takes_an_inline_read_a_variable_and_a_call`.

**F2. The shape of a write body is stated two ways** (section 3, "Writes fed by reads"; section 6,
freeze list "Mutation body"; section 7, `mutation_body`).
- The prose says "zero or more `match` clauses followed by one or more statements". The freeze
  list says "`match` clauses → `insert …`".
- The grammar (`(!stmt_keyword ~ clause)* ~ mutation_stmt+`) and every example write the clauses
  bare: `query close_company(…) { $c: Company { … }  $p $k:worksAt $c  delete $k … }`.
- `query w() { match { $p: Person } delete $p }`, the shape a reader of the prose writes, is a
  generic `Q001`. It is also asymmetric with a read, whose clauses sit in `match { }`.
- **Change:** decide one. Either fix the two prose lines to "a read's clauses, written bare", and
  add a recognizer for `match { … }` before a statement whose fix removes the wrapper. Or make the
  body `match_clause? ~ stmt+` and rewrite the examples.
- Test: `one_keyword_and_the_body_shape_is_the_kind`.

**F3. Two asset examples write an inline read without `match`** (before-and-after asset §4
`colleagues_of_colleagues`, §5 `weighted`).
- They write `from { from colleagues($q.handle) return { $q as r } }` and
  `from { from l2($docs, embedding, $v) return { $d, $distance as vec } }`.
- `inline_read = "{" ~ read_body ~ "}"`, and `read_body` begins with `match_clause`, so both are
  refused (`expected kw_match`).
- **Change:** wrap the inner clauses in `match { }` in the asset. The alternative, a match-less
  inline read, would be a second spelling of the same read (A6).
- Test: `every_before_and_after_example_parses_in_its_edition` pins these as the only edition-3
  failures in the asset.

**F4. `null` as an assignment value has no production, in either edition** (section 6, freeze
list row "Null: `null` as an assignment value | works"; asset §6 `retire`).
- `mutation_assignment = ident ~ ":" ~ expr`, and `null` is a reserved word, so in operand
  position it is `reserved_property` and refused.
- Today's edition 2 refuses `update Person set { city: null } where handle = $h` with "`null` is
  a reserved word". The asset's edition-2 `retire` therefore does not parse, although the asset
  says its edition-2 column is checked against today's `query.pest`. Edition 2 cannot clear a
  property at all.
- The edition-3 `retire` and `close_company` (section 3 and section 8) do not parse either.
- **Change:** `mutation_assignment = ident ~ ":" ~ (null_value | expr) ~ ","?` with
  `null_value = kw_null ~ &("," | "}" | ident ~ ":")`. **Implemented.** Also mark the edition-2
  `retire` as "Not supported" in the asset; it is not one of the seven rewriter rows.
- Tests: `a_clause_after_a_statement_prints_the_order` (`set { city: null }`,
  `{ a: null  b: 1 }`, `null + 1` refused, `$p.a = null` in a read refused) and the asset test.

### Ambiguities: one text, two trees (most found by the property test)

**F5. Unary minus at the start of a whitespace-separated item continues the previous
expression** (section 3, "Added: arithmetic"; section 7, the ladder).
- `match` clauses have no separator and `return` items an optional comma. A `-` after whitespace
  is therefore always read as binary: `return { 0  -0 }` is one item, `0 - 0`.
- `match { $ok  -$x > 0 }` is one filter, `$ok - $x > 0`.
- After a clause that cannot continue (a binding, a triple), the same `-$x > 0` was a new clause.
  So the meaning of `-` depended on the kind of clause before it.
- The RFC's `-` analysis covers only the arrow. This was found by the property test; its minimal
  counterexample was `return { 0  -0 }`.
- **Change:**
  - A filter clause never begins with `-`. Write `(-$x) > 0` or `0 > $x`. **Implemented**
    (`filter3 = { !"-" ~ expr3 }`).
  - `queries fmt` and the rewriter always emit commas between `return` items.
  - Section 3 should state that a `-` after whitespace continues the expression before it.
  - A comma-required `return` would be cleaner, but edition 2 accepts newline-separated items in
    stored text.
- Test: `the_ladder_has_the_usual_precedence_and_associativity`. The printer parenthesises a
  leading `-` after a whitespace separator.

**F6. `$a * $b` is both the wildcard triple and a product** (section 7, "`arrow_traversal` …
shadows nothing"; section 3, arithmetic).
- `traversal` is tried before `filter`. So `$n * $m > 10`, a product of two `let` values, is read
  as the triple `$n * $m`, and the parse then fails at `>`. `$a * $a or 0` and `$a * $a.n = 0`
  fail the same way.
- The RFC names only the `-` collision.
- **Change:** a bare wildcard triple is a triple only when no operator follows it:
  `traversal = { !(variable ~ "*" ~ variable ~ continuation) ~ … }`, where `continuation` is `.`,
  an arithmetic, comparison or logic operator, `in` or `is`. **Implemented.**
- A product alone (`$a * $b` as a whole filter) stays the triple. As a filter it is never Boolean.
- Test: `a_product_of_two_variables_is_not_the_wildcard_triple`. Found by the property test.

**F7. `subquery_predicate` and `exists_block` duplicate the block value and read
`count { p } > 1 or $x` differently** (section 7, `clause`; section 3, `block_value` as an
operand).
- With `block_value` an operand, `count { p } > 2` and `exists { p }` have two trees each: the
  clause form and a filter over a block value. This is A6 at the parse level.
- `subquery_predicate`'s right side is a whole `expr`. So `count { $a: P } > 1 or $x` parses as
  `count { … } > (1 or $x)` (probe output:
  `BlockCompare { …, right: Or(Literal(1), Variable("x")) }`). The same text as a filter means
  `(count { … } > 1) or $x`.
- **Change:** drop `subquery_predicate` and `exists_block` from `clause`. A block in clause
  position is a filter. **Implemented.** The lowering into the edition-2 tree maps a filter whose
  top is a block comparison, or a bare `exists`, to today's node. Every edition-2 text keeps its
  tree (see P1).
- Edition 2's reading of `count { … } > 1 and $x` was ill-typed, so no accepted text changes.
- Test: `a_block_in_clause_position_is_a_filter`. Found by the property test (minimal:
  `exists { $a: Person }` as a filter).

**F8. Contextual keywords as edge names collide with whitespace-separated clauses** (section 7,
"`let` and `from` are contextual, word-bounded keywords: legal as property names and edge
names").
- `match { $ok  from $r }` means a Boolean filter, then a `from` clause. It parses as a triple
  `$ok from $r` over an edge named `from`, and silently, because nothing is left over.
- `$ok  false  $p: Person` (two well-typed filters, then a binding) parses as a triple over an
  edge named `false`. The same holds for `let` and `true`.
- **Change:** `let`, `from`, `true` and `false` are not bare edge names. They are written quoted
  (`$a "from" $b`), as the reserved `"in"` is. **Implemented** (`edge_ident3`). Property, alias
  and assignment positions are unaffected.
- Two related shapes are left as they are, because every reading other than the keyword's is
  ill-typed:
  - A bare-name filter `from` or `let` followed by a `$` clause is the keyword's clause
    (`from  $a: Person` is refused; `let  $a = 1` is a `let`).
  - A lone-variable filter, a lone string or name filter, then a `$` clause, is a triple over a
    quoted edge (`$a  ""  $b`).
- Tests: `let_and_from_are_property_edge_alias_and_assignment_names`,
  `a_bare_from_or_let_filter_before_a_variable_is_the_clause`. Found by the property test.

**F9. A generic call with implicit whitespace swallows a following parenthesised item**
(section 7, `operand … | call | …`; calls "as revised on 2026-11-01").
- With `call = name ~ "(" …` and pest's implicit whitespace, `return { n  (2026 - $p.age) as born }`
  reads `n (…)` as a call to `n`.
- Edition 2 has no generic call, so this is new.
- **Change:** the name touches its `(`: `call_head = ${ call_name ~ "(" }`. **Implemented.**
- Consequence: `from (1) = 1` is two clauses, and `now ()` with a space, legal in edition 2, is
  refused in edition 3 (no corpus text spells it so).
- Test: `from_takes_an_inline_read_a_variable_and_a_call`.

**F10. Numeric literals need a word boundary** (section 7, `float_lit`; today's `integer`).
- Edition 2's `integer = @{ ASCII_DIGIT+ }` has no boundary, so `return { 1e3 }` is two items: `1`
  and the bare name `e3`.
- The RFC's exponent float requires a fraction (`1.5e3`), so `1e3` falls into that hole.
- **Change:** end `integer` and `float_lit` at a word boundary. Edition 3 refuses `1e3` and
  `1.5e`. **Implemented.** Say in section 3 that an exponent needs a fraction.
- Test: `float_literals_take_an_exponent`.

### Recognizers and refusals

**F11. `where_stmt` misses `delete $x where φ`** (section 5, "a `where` on a statement is a parse
error whose fix prints the binding and filter"; section 7, `where_stmt`).
- As written, `where_stmt` takes only a type name and is tried last. For
  `$x: Person  delete $x where $x.age > 3`, `delete_stmt` takes `delete $x`, and `where` becomes a
  bare-name filter clause after the statement. The refusal is "clause after statement", and the
  `where` fix is never printed.
- **Change:** try `where_stmt` first, and let it take a variable too. Its fix is then
  `$x.age > 3  delete $x`. **Implemented.**
- Test: `delete_and_update_with_where_print_the_binding_and_filter`. That test also pins the
  type-name fixes: `delete WorksAt where @src = $h` gives `$x: WorksAt  $x.@src = $h  delete $x`;
  `update Person set {…} where handle = $h and age > 3` qualifies both bare names.

**F15. The arrow recognizer shadows only ill-typed arithmetic, which is right at the type level,
not at the grammar level** (section 7: "a bare identifier is not a value in a read, so no legal
arithmetic has that shape").
- A bare name is grammatically an operand, and so are a string literal and a bare meta-field.
  `$a - x - $b`, `$a - "s" - $b` and `$a - @id - $b` are therefore grammatical arithmetic that the
  recognizer takes.
- `$a < -x - $b` also matches the `<-` form.
- None of these is well typed, so the claim holds where it matters. Section 7 should state it as
  a typing claim.
- Followed by a comparison (`$a - x - $b > 0`), the recognized arrow leaves `> 0` with no clause
  to start. The refusal is then the generic `Q001` at `>`, not the triple fix.
- `$n - 1 > 0`, `$n -1 > 0`, `$n - $m > 0` and `$n-$m > 0` are arithmetic, exactly as the RFC says.
- Tests: `minus_is_arithmetic_and_the_arrow_is_refused`,
  `recognizers_shadow_only_ill_typed_text`.

**F16. The infix recognizer must exist, and must not take a following call** (section 6, freeze
list "infix operators … are errors with the call as the fix"; section 7 `comparison`).
- Section 7's `comparison` has no `contains` or `starts_with` branch. With it as written,
  edition 2's `$d.title contains "graph"` parses silently as three filter clauses: `$d.title`, the
  bare name `contains`, and `"graph"`. The fix is never printed.
- A recognizer (`infix_string_op`) is needed. But with clauses unseparated,
  `$p.active  contains($p.name, "a")` (a Boolean filter, then the edition-3 call, which is the
  text the rewriter emits) is then read as the infix form and refused with a wrong fix.
- **Change:** the recognizer, word-bounded and not touching `(`. **Implemented.**
- Test: `a_call_to_contains_after_a_boolean_filter_is_a_call`. Found by the property test.

**F19. The bare `in` edge cannot be a parse error** (section 3, refused respellings: "`$d in $x`
over an edge whose name is reserved | parse error | the quoted name").
- `$d in $x` is a legal membership filter. The RFC's own fix for `unwind` is `$x in $list`.
- The parser cannot tell a node on the right from a list. Only the type checker can, so the
  refusal and its quoted-name fix belong to a `T` code.
- The quoted form `$d "in" $x` parses as the traversal.
- Test: `an_edge_named_in_is_quoted_and_the_bare_form_is_a_membership_filter`.

**F20. The kind of a declaration really is known at parse time** (A7; section 5, "`as commit` …
on a read body is a type error"). The RFC is right here.
- `read_body` must begin with `match {`, and a write body cannot (`match` followed by `{` is
  neither a filter nor a statement), so `read_body | mutation_body` never needs backtracking past
  the first token.
- Because the kind is in the parse tree, the refusal of `as commit` and `as unattached branch` on
  a read can be a parse error (`Q011` here) rather than a type error. The RFC may keep it as a
  type error, but nothing needs the type checker for it.
- `mutation name(…) { … }` is refused with `query` as a machine-applicable edit (`Q009`), for both
  body shapes.
- Tests: `as_commit_is_a_write_body_clause`,
  `the_mutation_keyword_is_refused_with_query_as_the_fix`.

**F17. `date("…")` is both a literal production and a call** (section 7, `operand` order; the
comparison asset: "`date(…)`, `datetime(…)`: calls the compiler folds to literals").
- In the RFC's `operand`, `call` precedes `literal`, so `date("x")` is a call in an expression but
  a literal inside a list literal, which takes only literals. That gives two trees for one text.
- The spike orders `literal` before `call`, so `date("…")` is a literal everywhere and `date($s)`
  is a call.
- **Change:** state which in section 7. Found by the property test.
- Test: `a_date_call_is_the_date_literal`.

**F18. A recognizer built on a contextual keyword shadows a call of that name.**
- `let($a) in [1]`, a call to a function named `let`, is taken by the `let (…) in` recognizer.
- **Change:** state in section 5 that a registry row may not be named `let`, `from` or another
  clause keyword. This is the same rule as "a declared read may not shadow a registry name". Found
  by the property test at 100,000 cases.
- Test: `recognizers_shadow_only_ill_typed_text`.

### Productions the RFC leaves undefined, and other specification gaps

**F12. Section 7 delegates productions to "as revised on 2026-11-01", and this document does not
hold that revision.**
- These are undefined here: `call`, its positionals and options (the earlier spike's G1 lookahead
  `!(ident ~ ":")` is needed); `annotation`'s arguments (the spike uses the `.pg` `=` form, so that
  `@budget(rows = 1000, on_exceed = refuse)` parses); `param` and its default; `agg_distinct`
  (the spike admits `distinct` under every aggregate name and leaves T57 to the type checker, as
  G5 proposed); `kw_let`, `kw_optional`, `kw_exists`.
- A negative parameter default (`$k: I64 = -1`) needs unary minus, since "a negative number is
  never a token". The spike admits `"-"? ~ literal` there.
- `binding = variable ~ ":" ~ type_ref …`: in today's `query.pest`, `type_ref` is the parameter
  type rule (`String`, `[T]`, `Vector(n)`). A binding names a `type_name`, and the freeze list
  refuses `::`.
- For the one release in which `gq 2;` still selects edition 2, both grammars live in one file.
  The RFC's names `clause`, `operand`, `comparison`, `binding`, `query_decl`, `mutation_body`,
  `mutation_stmt`, `update_stmt`, `delete_stmt`, `float_lit` and `limit_clause` collide with
  edition 2's rules of other bodies. The spike suffixes the edition-3 rules with `3`.
- The rule-set pin of section 11, rule 1, needs to say which rule set it pins.
- **Change:** inline the 2026-11-01 productions into section 7, and name the edition-3 rule set.

**F13. Retaining unknown annotations makes a later typed annotation non-additive** (section 5,
"An unknown annotation is retained as metadata … a declaration keeps compiling when a later stage
types the name"; section 6, freeze list "Annotations"; section 11).
- `query bounded() @budget(rows = 1000, on_exceed = refuse) { … }` parses and compiles in B with
  no effect. When stage N types `@budget`, the same accepted text starts refusing reads.
- That contradicts the RFC's own additivity rule, that a later form "starts at a token edition 3
  refuses". `@budget` starts at a token edition 3 accepts.
- The asset's later-stage block, read as edition 3: `schema_types` (`$t @property $p`) and
  `bounded` parse; `retract` (`$c: Commit`, `returning`) and `namespaced` (`crm::Person`) are
  refused.
- **Change:** one of these.
  - Refuse unknown annotations in B, with an allow-list for pure metadata such as `@description`
    and `@example`.
  - Or state that a later stage may give meaning only to an annotation name B refused.
  - Or state that a typed annotation's first meaning must not change any result. (`@budget`
    cannot meet that.)
- Test: the asset test prints the four outcomes.

**F14. The asset's shared schema is fenced as `gq` but is `.pg` text in an unspecified schema
syntax.**
- It writes `node Person: Named`. The asset says "edition 3 writes the interface after a colon";
  today it is `implements Named`.
- Section 8 of the RFC uses it too, with `@index(fulltext, analyzer = …)` and
  `Vector(3, distance = "cosine")`.
- The interface spelling is a schema-grammar change that the break's seven rows, the freeze list
  and the compatibility section do not list.
- **Change:** list it, or drop it, and fence schema blocks as `pg`. The spike skips the block.

### Where the RFC is right

**P1. No silent meaning change when the default edition flips** (section 6, the edition line and
the edition window).
- The corpus is every `query` and `mutate` section of `crates/omnigraph-gqt/cases{,_slow}/**/*.gqt`,
  every `.gq` file under `docs/`, and every fenced `gq` block under `docs/` outside `rfcs/` and
  `spikes/`: 2,753 texts, 2,748 of which edition 2 accepts.
- Through the dispatcher, a text without `gq 3;` gives exactly the edition-2 result, accepted or
  refused, for all 2,753.
- Read as edition 3, 2,402 of the 2,748 parse unchanged. The 346 refusals are all rewriter rows
  with their fix printed: 293 `update`/`delete … where` (`Q008`) and 53 infix `contains` or
  `starts_with` (`Q012`). No text fails for any other reason.
- Of the 2,402, 2,229 lower into the edition-2 tree, and every one of them equals the tree
  edition 2 builds. The other 173 use a construct the prototype does not lower (see section (b)).
- Every shared example in the asset passes the same equality check.
- Tests: `every_edition2_text_in_the_repository_parses_as_before`,
  `the_edition2_corpus_read_as_edition_3`, `shared_examples_mean_the_same_in_both_editions`.

**P2. `let`/`from` lookahead and the statement lookahead work as stated** (section 7).
- `from` is the clause before `{`, `$` or an identifier touching `(`. Otherwise it is an
  identifier: `from = 1` and `from (1) = 1`.
- `let` and `from` are legal in every clause position: first, middle and last of a `match`; inside
  `not`, `optional`, `exists` and `count`; inside a block value; inside an inline read; in a write
  body.
- They are also legal as properties, binding keys, aliases, `order` keys, assignment names
  (`insert WorksAt { from: $h, to: $slug }`) and function names. Edge names are the exception
  (F8).
- `stmt_keyword` is needed and sufficient. `insert`, `update` and `delete` stay property names
  after a dot and as binding keys.
- Tests: `let_and_from_in_every_clause_position`,
  `let_and_from_are_property_edge_alias_and_assignment_names`.

**P3. The six reserved words are enough.**
- 75 other keywords of the grammar and registry are legal as a property after a dot, a binding
  key, an alias, an edge (`let`, `from`, `true`, `false` quoted, see F8), an insert assignment and
  an update assignment.
- The six reserved words are refused as an alias and as a bare edge. They are legal after a dot
  and quoted as an edge.
- Test: `the_reserved_set_is_the_six_words`.

**P4. The arithmetic ladder needs nothing beyond F5, F6 and F10.**
- Precedence and associativity follow the usual comparator order: `+` below `*`, left fold, unary
  binding tightest, comparison non-associative and below arithmetic, `not` below comparison.
- Exponent floats parse, and `signed_integer` stays schema-only.
- The property test round-trips 20,000 generated reads by default, and three runs of 100,000
  passed after the deviations above. That covers every traversal form, the full ladder, calls with
  options, blocks as values, `let`, `from` over inline reads, calls and `$r`, `optional`, `limit $k`
  and parameter defaults, with randomised separators.

## (b) What was built and how to run it

| Piece | Where | Status |
|---|---|---|
| Edition-3 grammar | `crates/omnigraph-compiler/src/query/query.pest`, below the `GQ edition 3` banner; entry rule `gq3_file` | Real, for sections 3, 6 and 7, with the deviations above. Edition-2 rules are untouched. |
| Edition line and dispatch | `parser.rs` `parse_query_diagnostic`, `parser3.rs` `edition_line` | `gq 3;` selects edition 3; `gq 2;` selects edition 2, with the line blanked so positions keep; any other number is `Q006` with `gq 3;` as the fix. No line means edition 2, unchanged. |
| Edition-3 tree | `ast3.rs` | A separate tree, so the shared edition-2 enums are untouched: the planner, the GQT runner and the frozen reference engine match them exhaustively. Every node derives `PartialEq`. |
| Builder and diagnostics | `parser3.rs` | Recognizers become typed errors with a printed fix: `Q007` arrow, `Q008` `where` on a statement, `Q009` `mutation`, `Q010` comparison in binding braces, `Q011` `as commit` on a read, `Q012` infix `contains`/`starts_with`, `\|\|`, `CASE`, `for`/`unwind`/`let … in`, `@if_commit`/`@valid`, `Q013` clause after a statement. The numbers are the prototype's own. |
| Lowering for the type checker | `bridge3.rs` | Edition-3 text whose nodes exist in edition 2 lowers into `ast::QueryFile` and type-checks unchanged. Every new node (`let`, `from`, `optional`, arithmetic, a block as a value, a generic or relation call, `limit $k`, a default, a non-legacy annotation, write clauses, `update $x`, `delete $x`, a catalogue edge) is refused with one placeholder, `T60`. |
| Printer | `print3.rs` | Minimal. Parenthesises only where precedence requires it; separators come from a `Style`. |
| Tests | `parser3_tests.rs` (31 tests), `parser3_proptest.rs` (1 property) | Section 10's parser tests, the asset test, the corpus walks, and the property. |
| Asset copy | `src/query/testdata/2026-11-07-gq-3-before-and-after.md` | A byte copy of the asset at `rfc/gq-3` `ab74f629`, because the RFC branch is not merged. |

Run:

```bash
cargo test -p omnigraph-compiler --lib parser3                    # all edition-3 tests, 20,000 property cases
PROPTEST_CASES=100000 cargo test -p omnigraph-compiler --lib parser3_proptest -- --nocapture
cargo test -p omnigraph-compiler --lib the_edition2_corpus -- --nocapture   # the corpus read as edition 3
cargo test -p omnigraph-compiler --lib every_before_and_after -- --nocapture # asset, with the later-stage outcomes
```

Verification on the branch:
- `cargo test -p omnigraph-compiler`: all 525 library tests pass, plus 2 others.
- `cargo dev-test -p omnigraph-compiler`: the alias carries `--workspace`, so this is the whole
  canonical workspace graph. Result: DEVTEST_RESULT.
- `cargo clippy -p omnigraph-compiler --all-targets --locked -- -D warnings`: CLIPPY_RESULT.

Only the compiler crate and `Cargo.lock` changed. The lock change adds the `proptest` dev-dependency,
which was already in the lock graph through the engine.

## (c) What I did not get to

- **Type checking of the new nodes.** This was out of scope: every new node is `T60`. As a
  consequence, none of section 5's codes (`T65`, `T68`, `T69`, `T70`, `T40` lifted) and none of
  the refusals that F19 moves to the type checker were built.
- **Schema declarations in the one grammar.** `declaration = schema_decl | query_decl` and
  F14's `node T: Iface` were not built; schema text still goes through `schema.pest`.
- **The rewriter, `queries fmt`, the boot rewrite and the rule-set pin test.**
- **The 173 corpus texts that parse as edition 3 but do not lower.** They hold positional `fuzzy`
  edit budgets and `nearest`/`bm25`/`rrf` in `order` or `return`, the rewriter rows that are type
  errors in edition 3. Telling them apart needs the registry.
- **A property over write bodies.** The generator builds reads only. Writes are covered by named
  tests, not generated ones.
- **The recognizers are partial.** `CASE` is recognised by its keyword alone; `END`, `coalesce` as
  a keyword, `having`, `retrieve` and `view` are generic `Q001` errors, with no fix printed.
- **The competence instrument and the earlier spike's G7** (a meta-field path) were not run or
  addressed.
