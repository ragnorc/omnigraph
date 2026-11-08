# Spike: the edition-3 grammar and diagnostics as adopted on 2026-11-08

**Branch:** `spike/gq3-grammar2`, built on `spike/gq3-redteam` (`f882e85b`). This is throwaway prototype work.
**Specification:** `docs/rfcs/2026-11-07-gq-3.md` at `rfc/gq-3` `d79b6ea7`: section 7 (the full edition-3
rule set and the decisions the prototype forced), section 5 (diagnostics), section 3 (refused
respellings) and the decision log entry of 2026-11-08. Its assets were read at the same commit. The
two earlier reports are cited as **P-Fn** (`docs/spikes/gq3-parser.md`) and **R-n**
(`docs/spikes/gq3-redteam.md`).
**Brief:** apply section 7 and section 5 to the parser; drive the red-team corpus's mismatches
toward zero; keep edition 2 unchanged and the parse-print-parse property green at 100,000 cases.

## (a) Findings

Each finding names the RFC section, what was observed (the query or test), and the proposed change.
Rules this grammar applies differently from section 7 are marked `SPIKE-DEVIATION (Gn)` in
`query.pest`; the `Gn` numbers are given in the findings below.

### The numbers

**1. The red-team corpus goes from 109 mismatches to 6. 151 of 178 refusals now print a
suggestion that reparses to the intended text** (§10 Evidence; `blocked_on`, third item).

| | start (red-team runner, 395 cases) | final parser, original corpus (395) | final parser, amended corpus (397) |
|---|---|---|---|
| mismatches with the header | 109 | 43 | **6** |
| … of which the 2026-11-08 decisions contradict the header | – | 37 | 0 |
| refusals | 154 | 198 | 178 |
| generic `Q001` | 109 | 6 | 6 |
| a fix or suggestion printed | 36 (1 a suggestion) | 178 suggestions | 159 suggestions |
| … reparses to the intended text | **22** | 163 | **151** |
| … reparses to another text | 5 | 2 | 2 |
| … does not parse | 9 | 13 | 6 |

- **How the columns were measured.** The red team applied the fix text over a span it guessed. Here
  the runner applies the diagnostic's `suggestion` edits. It then accepts the result as intended in
  any of these cases:
  - the repaired text is the original with one span replaced by the header's fix (whitespace and
    `//` header lines ignored);
  - its tree equals the tree of the header's fix spliced over the refusal's construct;
  - it is equal up to a renaming of variables (a fix that invents a fresh name).

  The two right-hand columns run the same final code. The middle column is the red team's text
  untouched. The right column is that corpus with the amendments below.
- **Amendments.** 37 headers now contradict a 2026-11-08 decision. Examples: `$n -1` is refused, the
  ranked column is `$d.embedding`, unknown annotations are refused, an open bound is a type-level
  door, and the asset now wraps inner reads in `match`. The generator (`make_corpus.py`) fixes them
  in an `AMENDMENTS` table. It keeps every case as written and adds an `amended:` header that says
  why. Two cases were added: `1-31` and `1-32`, for the bare-name refusal `Q018`.
- **The 6 that do not parse** are all `expect: refuse` texts written whole in another language
  (`9-01` Cypher `MATCH … RETURN`, `9-46` SQL `SELECT`, `9-62` Gremlin, `9-65` SPARQL, `9-97`
  `batch`, `9-106` `AS` as a table alias). The first habit's fix applies, and the next error
  remains.
- **The 6 remaining mismatches** are findings 8, 9 and 11, plus `7-12` (schema declarations, part
  c). Each is listed with its reason in `known-mismatches.txt`, which only shrank.
- **The intermediate runs** while working gave 48, then 10, then 6 mismatches. Those runs used a
  runner bug, since fixed: it found the fix text inside the case's own `// expect:` header line. So
  their fix counts were too high, and only the final numbers above are reliable.

### Section 7 as written does not parse its own examples

**2. `where_stmt` cannot be reached, because `operand3` has no bare name** (§7 `operand3`,
`where_stmt`; §5 "a `where` on a statement is a parse error whose fix prints the binding and
filter").
- **The defect.** In §7, `where_stmt`'s predicate is an `expr3`, and §7's `operand3` admits no bare
  identifier (2026-11-08). So `delete Person where handle = $h` fails at `handle`, and the recognizer
  never matches. It exists for exactly the 293 edition-2 corpus texts that §6's rewriter rows cover
  (P-P1).
- **What the spike did (G1, G5).** A bare name is the last `operand3` alternative, `bare_name_rec`.
  The builder refuses it at the word (`Q018`), with two exceptions: inside the `where_stmt`
  predicate, which the fix qualifies (`$x.handle`, and `from`/`to` as `$x.@src`/`$x.@dst`), and as a
  whole `order` key (an alias). The set of accepted texts equals section 7's. The parse reaches the
  foreign word, which gives the "parse error at the word" that §5 asks for.
- **Change:** §7 should say that `operand3` carries a bare name as a recognizer, refused outside the
  `where_stmt` predicate and outside a whole `order` key. The alternative is a separate predicate
  ladder for `where_stmt`.

**3. The RFC's own `terms($q, mode: any)` does not parse** (§4 the lexical query; §6 `queries fmt`
"`bm25` over `terms(q, mode: any)`"; §7 `option`).
- `option = { ident ~ ":" ~ expr3 }`, and `any` is a bare name, so the option value fails.
- **G6:** a whole option value may be a bare word, the option's enumerated value
  (`mode: any | all`). Corpus `5-13` and `5-14` pin it.
- **Change:** `option = { ident ~ ":" ~ (expr3 | enum_word) }`. The registry row types the word, and
  an unknown word is a type error that lists the admitted names.

**4. The kind of a declaration is not known at its first token once a write body that opens with
`match {` is recognised** (A7; §7 "a read body starts with `match {`, which a write body cannot").
- `query w() { match { $p: Person } delete $p }` must be refused with the wrapper removed (§5).
  It shares its opening `match {` with a read. The parser can tell the two apart only at the token
  after the block's `}`: `return` for a read, or a statement.
- The grammar is `read_body3 ~ &"}" | mutation_body3 ~ &"}" | swapped_read_rec`.
- **Change:** restate the rule. "The kind is decided by the token after the first `match { … }`
  block, with no backtracking beyond it." The `match {` recognizer for a write body depends on that.

**5. Statement words collide with edge names in a write body, as `let` and `from` did** (§7
`edge_ident3` and the decision "`let`, `from`, `true` and `false` are never bare edge names").
- **Found by the property test**, once it generated writes. The text is
  `query q() { insert Person { name: 0 }  $a  update $a set { name: 0 } }`. Its second group
  parses `$a update $a` as a triple over an edge named `update`, then fails at `set`.
- **The silent case.** `$ok  delete $p` (a Boolean filter, then a statement) parses as a triple over
  `delete`. Nothing is refused, and the delete disappears.
- **G8:** `insert`, `update` and `delete` are written quoted when used as edge names, as `"in"` is.
  The statement reading wins in a write body. Recognizers print the quoted fix:
  - in braces (`stmt_edge_rec`), where no statement can stand;
  - in a write body, for `$a insert $b` and for `$p update $q` without `set`.
- P-P3's claim that the statement words are legal edges is withdrawn.
- Pinned by `the_reserved_set_is_the_six_words` and corpus cases `2-16` and `6-31`.
- **Change:** add the three words to §7's quoted list and to the refused-respellings row.

**6. Adjacent write groups with no clause between them read as one group** (§2 "A write is a
sequence of groups"; §6 rewriter "a body of several statements | one group per statement";
§7 `write_group`).
- `write_group = (clauses)* ~ statements+`. So `insert A { … }  insert B { … }` is one group with two
  statements, and never two groups. Two trees (one group, or two) print as one text.
- Only an `insert` can stand without clauses, and an insert reads nothing, so the effects are the
  same. But the rewriter's "one group per statement" is not what the grammar reads back for two
  consecutive inserts.
- **Change:** state that a group after the first opens with a clause, and that consecutive
  statements are one group.
- The property generator follows that rule, and 100,000 cases pass with writes included.

### Diagnostics

**7. The `$n -1` fix guesses subtraction; a gap rule gets the author's meaning in every corpus case**
(§3 arithmetic: "`$n -1` … is refused with `$n - 1` as the fix").
- **As written.** The rule refuses the silent merge (R7). But the printed fix makes the merge
  explicit where the author meant two items or two clauses:
  - `1-13`: `return { $p.age` then a new line with `-$p.age as neg }`;
  - `1-22` and `1-29`: `let $x = $p.age  -1 < $x`.
- **What the suggestion does instead (needs_review).** It looks at the gap before the `-`. A line
  break, or two or more spaces (GQ's clause separator), means a new item or clause:
  - in `return`, insert a comma: `$p.age, -$p.age`;
  - in a `match`, parenthesise the sign: `$p.age  (-1) < $x`.

  After one space it is a subtraction: `1-11`'s `$n -1 > 0` becomes `$n - 1 > 0`. All four now
  match their intended text.
- **Change:** §3 should either adopt the gap rule or say that the suggestion is needs_review and the
  message names both readings.

**8. §5 and §7 disagree on where infix `contains` is refused** (§5 "Infix `contains` is refused at
type check, not parse"; §7 "the rules that exist only to print a fix are … the infix `contains` and
`starts_with` recognizer").
- The spike keeps the parse refusal (`Q012`, needs_review). Its suggestion is `contains(a, b)`, and
  its message names `b in a` for a list.
- `9-25` (`$d.tags contains "rust"`) stays a mismatch: the suggestion is the String form.
- **Change:** pick one.
  - Keep it at parse with the String suggestion. That is cheap, and wrong for every list.
  - Or move it to the type checker as §5 says. Then §7's recognizer list drops it, and the
    edition-3 tree needs an infix node for the checker to refuse.

**9. Of the six reserved words, only `is` and `null` between two variables are parse errors** (§3
refused respellings; §6 "Reserved words … a grammar test pins the set").
- `$a and $b`, `$a or $b`, `$a in $b` and `$a not $b` are legal expressions. `$a not $b` is two
  filters, `$a` and `not $b`.
- So a bare edge named `not` (corpus `2-14`) cannot be refused at parse, just as the RFC already
  says for `in` (`2-08`, `T7`).
- Test: `reserved_words_between_two_variables`.
- **Change:** extend the `$d in $x` row to `not`, `and` and `or`. Each is a type-level refusal
  (`T7` or `T41`) with the quoted edge as the fix.

**10. Every refusal code prints a suggestion that reparses, except where no edit can make the text
legal** (§5 "Every fix is a `suggestion` of byte-range edits, the human text derived from it").
- **The test.** `every_refusal_code_has_a_corpus_case_whose_suggestion_reparses` covers `Q002`,
  `Q005` to `Q021`.
- **The fix text** is the construct the diagnostic points at, with the suggestion applied. It reads
  `remove `…`` for a deletion, and an edit outside the construct is printed after it.
- **The pinned trees.** `redteam_suggestions_reparse_to_their_pinned_trees` pins every corpus
  refusal's code and repaired tree in `fixed-trees.txt`.
- **A design point for §10's test.** A pinned tree only pins what the parser prints today. The
  independent oracle is the header's intended text. The corpus test therefore compares against both:
  `fixed-trees.txt` catches regressions, and the header catches wrong fixes.

**11. Recognizers that still print no mechanical fix, and why** (§5; A5).
- **No GQ form now.** `=~`, a backticked name, `$1`, `$x is T` and `$x: $t`. The last two are stage
  Poly, and their message names it.
- **The fix would restructure the read.**
  - `having` (`9-48`, `9-49`): it becomes a `from` over the grouped read plus a filter, with aliases
    the parser would have to choose. The rewriter's machinery could do it.
  - `retrieve` (`9-50`): a withdrawn form whose arguments need rereading.
  - `$p.knows.name` (`9-79`): two edits in two clauses, and an invented variable.
- **Only the author knows.**
  - the items of a missing `return` (`9-83`; the suggestion `return { $p }` is a needs_review guess);
  - which binding a stray `{ handle: $h }` belongs to (`1-27`, `1-28`);
  - `null` as a return item (`9-109`);
  - `limit -1` (`9-86`).
- **The generic `Q001` that remain** (6): a schema declaration (`7-12`, not built), a statement
  after a query (`8-12`), and whole GraphQL, Datalog and Kusto texts (`9-58`, `9-61`, `9-68`,
  `9-119`).
- **Change:** §5 should read "a suggestion whenever one edit set makes the text legal; otherwise the
  fix names the form". The test should pin which codes may omit the suggestion.

**12. Some habits are lexical, and their edits must compose with the structural recognizer** (§5
"a short table recognizes the habits agents bring from other languages").
- **Lexical habits.** A grammar rule cannot hold these without admitting them: single quotes, `<>`,
  `==`, `&&`, `!`, uppercase keywords, `?x`, `$P`, `--` and `#` comments, `};` and `a::b`.
  `habits3.rs` runs over a refused source, applies their edits, and reparses.
- **Composition.** If the reparse is refused with a suggestion, the two edit sets are composed into
  one suggestion over the original source by byte provenance:
  - `ORDER BY $p.age DESC` → `order { $p.age desc }`;
  - `where not $p knows $q` → `not { $p knows $q }`;
  - `$d.title LIKE '%graph%'` → `contains($d.title, "graph")`;
  - `exists { MATCH (p)-[:KNOWS]->(q) }` → `exists { $p knows $q }`.
- **Grammar recognizers** (`_rec` rules) cover the rest of the table and the RFC's list: `where`,
  `filter`, `with`, `call`, `detach`, `distinct` before an item, `optional`, `exists` or `not`
  without a brace, `count(*)`, `-[:e]->`, `(a:T)`, `return x` without braces, `offset`, `skip`,
  `group by`, `having`, `CASE`, `||`, `BETWEEN`, `LIKE`, `STARTS WITH`, `not in`, `in (…)`,
  `= null`, `set $p.x = v`, `CREATE`/`MERGE`, `delete $p, $q`, and binding and assignment
  respellings.
- **Change:** §5 should describe the table as two parts, grammar recognizers and a lexical pass,
  whose suggestions compose.

**13. A space before a call's parenthesis is refused, but `date ("…")` is still the date literal**
(§7 decisions: "a call's name touches its `(`"; "`date("…")` is the literal").
- `date_lit` is edition 2's non-atomic rule, so `date ("2025-01-01")` parses as the literal.
- Test: `a_date_literal_with_a_space_is_still_the_literal`.
- **Change:** make `date_lit3` atomic, or name the exception.

**14. The registry-keyword rule lists `union`, which is itself a registry row** (§5: "A registry row
is never named a clause keyword (`let`, `from`, `union`, `optional`, `exists`, `not`)"; the same
section: "`distance`, `bm25`, `nearest`, `rrf` and `union` are registry rows").
- `call_name` excludes `let`, `from`, `optional` and `exists`. `not` is reserved.
- `exists($p.city)` is refused (`Q019`) with `$p.city is not null` as the fix (`9-37`). `from(1)` and
  `let(1)` are refused too.
- **Change:** drop `union` from the list.

**15. `for $x in $v` is ambiguous at parse** (§3 refused respellings: "`for $x in rel` → `from rel`"
and "`for $x in $list` → `$x in $list`").
- With a variable on the right, the parser cannot tell a relation from a list.
- `let … in` is always a relation, so the fix is `from rel`. `for $x in { … }` also gets `from`.
  `for $x in $v` and `unwind` get `$x in $v`, which leaves `$x` unbound (R2).
- These suggestions are needs_review.
- **Change:** mark the `for … in $v` row as a type-level choice, or say that its suggestion assumes
  a list.

### Where the RFC is right

**16. Every other 2026-11-08 decision implemented as written, and holds** (§7 decisions; §5).
Each item below is pinned by a test or a corpus case.
- Removing the leading-minus rule is safe: `$p: Person  - -$p.age < 0` parses (`1-26`).
- A chained comparison is refused with the `and` form (`9-96`), and does not take `$x < y > $z`,
  which stays an undirected traversal (`2-26`).
- `match {` at a write body is refused with the wrapper removed (`6-07`).
- `name (` is refused with the touching form (`4-20`, `4-21`).
- `let`, `from`, `true` and `false` between two lone variables are refused with the quoted edge
  (`2-09`, `2-10`).
- These parse as written: write groups (`write_group+`), `null` as an assignment value, a binding
  over an edge type (`6-11`), and `date("…")` as the literal.
- An edition-line near miss is refused with `gq 3;`: `gq 3`, `GQ 3;`, a `set` before it, and `gq 4;`
  (`8-07` to `8-11`).
- Unknown annotations are refused, except the metadata allow-list.
- A later form names its stage, as §5 asks:
  - `offset` and `skip` name stage P;
  - `returning`, `assert { }`, `@if_commit` and `@valid` name W1;
  - `@budget(rows = …)` names N;
  - `a::b` names the namespaces RFC;
  - `$x: $t` and `is T` name Poly;
  - the `Commit` and `Branch` parameter kinds are named as a later change;
  - `batch` is named as a request shape.
- The open bound `{1,}` passes the parser as §5's type-level door.
- The before-and-after asset of 2026-11-08 parses whole: 0 failures, against 3 before. Every `gq`
  block of the RFC parses (`rfc_section_8_examples_parse`, which now reads them from a copy).

**17. Edition 2 is unchanged** (§6 the edition line and the edition window).
- `every_edition2_text_in_the_repository_parses_as_before` passes over the 2,753 stored texts.
- An edition-line near miss is detected only for a source that opens with `gq`, which no edition-2
  text does.

**18. Four GQT `expect error` cases change code under edition 3, which the rewriter's fixed map must
carry** (§6 rewriter: "an `expect error` code … that moves is rewritten by the tool from its fixed
map"; §7's count "2,402 parse unchanged under edition 3").
- These four cases now refuse at parse with `Q018` (a bare name):
  - `bare_node_projection_object_fields` (was `T8`);
  - `expression_type_errors` (was `T41` or `T7`);
  - `issue_640_search_score_projection` (was `T36`);
  - `issue_659_edge_selection_contract` (was an edge-selection error).
- The edition-2 corpus read as edition 3: 2,398 texts parse (was 2,402), 2,225 lower, and 2,225
  are equal (`the_edition2_corpus_read_as_edition_3`).
- **Change:** §7's number becomes 2,398, and the rewriter's map gains the four rows.

**19. The property test, under the new rules** (§10 "the parse-print-parse property over 100,000
generated reads").
- **Result.** 100,000 cases pass, and the generator now writes write bodies as well (groups,
  `insert`, `update`, `delete`, `null` values, `as commit`).
- **New ambiguities it found under these rules:** finding 5 (statement words as edges) and finding 6
  (the group boundary). It also found two bugs in the spike's own recognizers, both fixed:
  - `return { false = 0 }` was read as SQL's `alias = value`;
  - `not $a * $a` was read as a brace-less `not` over the wildcard triple. That text is ill-typed,
    and the generator filters it like the F6 product.
- **Generator rules.** Bare names are drawn only where the grammar admits them: a whole `order` key
  and a whole option value. The generator skips the refused shapes `$a from $b` and
  `$a true $b`.

**20. An implementation fact the rewriter and the fix test both depend on.** A pest pair's span ends
after the implicit whitespace that a trailing repetition skipped. Every byte-range suggestion derived
from a pair (a construct, an operand's end, a lookahead-bounded recognizer) must trim it. Untrimmed,
a fix of the form "insert before the next token" landed after the whitespace, the `-` gap rule
measured nothing, and a quoted-edge fix swallowed the newline before the statement.

## (b) What was built and how to run it

| Piece | Where |
|---|---|
| Edition-3 grammar, section 7's rules with the `_rec` recognizers and the deviations G1 to G8 | `crates/omnigraph-compiler/src/query/query.pest`, below the `GQ edition 3` banner |
| Builder, refusals as `Refusal3` (the diagnostic plus the construct span; every fix a suggestion, the `fix` text derived from it), construct-named parse errors | `crates/omnigraph-compiler/src/query/parser3.rs` (`parse_query3_detailed`, `refusal`, `derive_fix`, `grammar_error`, `edition_near_miss`) |
| Lexical habit pass with provenance-composed edits | `crates/omnigraph-compiler/src/query/habits3.rs` |
| Edition-line near miss in the front door; a suggestion for `gq N;` | `parser.rs` `parse_query_diagnostic` |
| Write groups in the tree | `ast3.rs` `WriteBody3 { groups }`, `WriteGroup3`; `print3.rs`, `bridge3.rs` |
| New codes `Q014` to `Q021` (prototype numbers) | `codes.rs` |
| Red-team runner: suggestions applied, intent checked against the header, pinned repaired trees, per-code coverage | `parser3_redteam.rs`; `docs/spikes/gq3-redteam/fixed-trees.txt`, `known-mismatches.txt` (6 lines), `results/` (with `mismatches.txt`) |
| Corpus amendments (the 2026-11-08 decisions), two new cases | `docs/spikes/gq3-redteam/make_corpus.py` (`AMENDMENTS`, `ranked_column`) |
| Property generator with write bodies | `parser3_proptest.rs` |
| Asset copy refreshed from `rfc-gq-3`; RFC `gq` blocks copied | `src/query/testdata/2026-11-07-gq-3-before-and-after.md`, `2026-11-07-gq-3-rfc-examples.md` |

Run:

```bash
cargo test -p omnigraph-compiler --lib parser3                    # all edition-3 tests (20,000 property cases)
PROPTEST_CASES=100000 cargo test -p omnigraph-compiler --lib parser3_proptest -- --nocapture
GQ3_REDTEAM_OUT=$PWD/docs/spikes/gq3-redteam/results \
  cargo test -p omnigraph-compiler --lib redteam_corpus_strict -- --ignored --nocapture   # the instrument
GQ3_REDTEAM_BLESS=1 cargo test -p omnigraph-compiler --lib redteam_suggestions_reparse   # re-pin repaired trees
GQ3_REDTEAM_ROOT=<copy of the original docs/spikes/gq3-redteam> GQ3_REDTEAM_OUT=<dir> \
  cargo test -p omnigraph-compiler --lib redteam_corpus_strict -- --ignored --nocapture   # the middle column
CASE=9-63-gremlin-method cargo test -p omnigraph-compiler --lib debug_one_case -- --ignored --nocapture
python3 docs/spikes/gq3-redteam/make_corpus.py                    # regenerate the corpus after editing cases
```

Verification on the branch:
- `cargo test -p omnigraph-compiler --locked`: 528 library tests pass (2 instruments ignored), plus
  2 others.
- 100,000 property cases pass.
- `cargo clippy -p omnigraph-compiler --all-targets --locked -- -D warnings -W clippy::dbg_macro` is
  clean, and so is `cargo fmt`.
- `cargo dev-test -p omnigraph-compiler`: the result is below (the workspace graph, with
  `RUST_MIN_STACK=16777216`).
- Nothing under `crates/omnigraph-reference-engine/` changed, and no edition-2 behaviour changed.

Result of `RUST_MIN_STACK=16777216 cargo dev-test -p omnigraph-compiler --no-fail-fast` (the whole
canonical workspace graph): 5,186 passed and 10 failed. Every compiler test passed. The 10 failures
are in crates this branch does not change, and none reaches the query parser. All are facts of this
sandbox (gVisor, running as root), the same ones the parser and red-team spikes attributed:
- 5 symlink-refusal tests: three `omnigraph-bench` archive tests, the bench projection test, and
  `cli_cluster::managed_lifecycle_capture_and_retry_records_refuse_symlinks_without_reading_targets`.
- 4 `omnigraph-bench` tests whose message is "`/proc/self/status` contains no `Cpus_allowed_list`":
  `machine::captures_a_complete_identity_on_supported_hosts`, two `real_graph_run` tests, and
  `runner::warm_and_post_reopen_start_from_identical_frozen_bytes_and_verify_exact_content`.
- `db::upgrade::tests::interrupted::unreadable_legacy_object_asks_for_a_rerun`, a mode-000 file that
  root still reads.

## (c) What I did not get to

- **Type-level refusals.** These stay type-level: infix `contains` over a list, a reserved-word edge
  (`in`, `not`, `and`, `or`), the open bound, `@type_id` and the catalogue names. The prototype's
  lowering still refuses every new node with `T60`. None of section 5's `T` codes are built, so
  corpus cases `2-08`, `2-14` and `9-25` cannot be settled.
- **Schema declarations in the one grammar** (`7-12`, and §6's "the schema kinds in the same
  grammar").
- **Mechanical fixes that restructure a read**: `having`, `retrieve`, a property path through an
  edge, and moving a `return` before `match` with an `order` between them. These need the rewriter's
  machinery rather than local edits.
- **The rule-set pin of §11 rule 1.** The recognizers add about 70 rules. A pin would need to tell
  recognizers (`_rec`) from productions, and this spike names them that way so that a pin could.
- **The one-spelling pairs.** `results/pairs.md` is unchanged. Its `covered:` headers predate §6's
  2026-11-08 `fmt` list, which now names most of them.
- **A property over refusals.** That would generate near misses of accepted text and check that
  each suggestion reparses. The corpus is still hand-written.
- **A live agent.** The competence instrument (M0) was not run.
