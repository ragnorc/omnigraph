# Spike: red-teaming the edition-3 surface as an agent meets it

**Branch:** `spike/gq3-redteam`, built on `spike/gq3-parser` (`0162a330`). This is throwaway prototype work.
**Specification reviewed:** `docs/rfcs/2026-11-07-gq-3.md` at `rfc/gq-3`, with its assets
`2026-11-07-gq-3-before-and-after.md` and `2026-11-07-gq-3-grammar-comparison.md`. I also read the
parser spike's report (`docs/spikes/gq3-parser.md`, cited below as **P-Fn**) and the core spike's report
(`spike/gq3-core:docs/spikes/gq3-core.md`, cited as **C-Fn**). This report does not repeat their findings.
It cites them where this corpus hits the same defect.
**Question:** suppose an agent composes edition-3 text from a schema, a short reference and its own
errors. Does the grammar refuse what the RFC refuses and accept what it accepts? When it refuses, does
the diagnostic lead to the fix?

**Instrument.** The corpus has 395 edition-3 queries in `docs/spikes/gq3-redteam/corpus/`: 275 in the
before-and-after asset's eight groups and 120 written from other languages' habits. There are also 26
one-spelling pairs in `pairs/`. A runner parses each file under `gq 3;` and compares the outcome with the
file's header. It then plays the agent. It takes the printed fix, applies it literally over the construct
the diagnostic points at, and parses the result. When the header names the fix I expected, it also
compares the trees of the two repaired texts. For every text the front door lowers, the runner also
records what today's type checker says against the RFC §8 schema. Section (b) tells how to run it. The
current table is `docs/spikes/gq3-redteam/results/results.md`. The fix audit is
`results/fix-audit.txt`, and the pair table is `results/pairs.md`.

**Headline numbers** (395 cases, 109 differ from their header):

| | count |
|---|---|
| Refusals | 154 |
| … with no fix printed | 118 (109 are the generic `Q001`, whose message lists pest rule names) |
| … with a fix printed | 36 |
| … whose fix, applied literally, parses with the intended meaning | 22 |
| … **whose fix, applied literally, does not parse** | **9** |
| … **whose fix parses but changes the meaning** | **5** |
| Texts the RFC refuses that the parser accepts, or reads with another tree | 19 |

An agent that trusts the diagnostic gets a working fix for 22 of 154 refusals (14%). For 14 of the
36 fixes that are printed (39%), following the fix breaks the query or changes what it means.

## (a) Findings

Each finding names the RFC section it bears on, gives the exact case (a file in `corpus/`), the
observed text and the proposed change. The agent-facing findings come first. Then come the grammar
ambiguities, the one-spelling pairs, and last what this corpus confirms or contradicts in the two
earlier spikes.

### Agent-facing: fixes that do not lead to the fix

**R1. A fix is prose, a template or a fragment. The diagnostic gives no span, so the agent cannot apply it** (A5, "Every
refusal is a typed code with the fix that makes the text legal"; §5, the four-field diagnostic).
- The fix is a free string, and the diagnostic carries only a start position. Only `Q009` (`mutation`)
  carries a machine-applicable edit. For every other code the agent must guess what the fix replaces.
  The runner uses the innermost recognized construct at the position, which is the most generous
  guess.
- By code:

  | code | printed fixes | parse with the intended meaning | fail |
  |---|---|---|---|
  | `Q007` arrow | 7 | 7 | 0 |
  | `Q009` `mutation` | 3 | 3 | 0 |
  | `Q002`, `Q006` | 2 | 2 | 0 |
  | `Q008` `where` on a statement | 8 | 6 | 2 change the meaning (R3) |
  | `Q010` comparison in braces | 4 | 1 | 3 do not parse (R2) |
  | `Q012` respellings | 10 | 3 | 4 do not parse, 3 change the meaning |
  | `Q011` `as commit` on a read | 1 | 0 | 1 does not parse |
  | `Q013` clause after a statement | 1 | 0 | 1 does not parse |

- **Change (RFC §5 and the code):** state that every fix is a `suggestion` of byte-range edits, with
  the human text derived from them. The compiler already has a field for this (`Suggestion { edits }`).
  Add a test that for every refusal code applies the suggestion, reparses, and compares the tree with a
  pinned expected tree. This corpus already does that over 36 cases.

**R2. Six recognizers print prose or a template, and three print a broken rewrite** (§3, refused
respellings; §5).
- `Q013` (`6-12-clause-after-statement`): `delete $p  $p: Person { handle: $h }` prints *"move every
  binding, traversal and filter above the first `insert`, `update` or `delete`"*. The RFC says the fix
  "prints the order". It should print `$p: Person { handle: $h }  delete $p`.
- `Q011` (`6-14-as-commit-on-read`) prints *"remove `as commit`, or end the body in `insert`, `update` or
  `delete`"*. It should be one edit that deletes the clause.
- `Q012` `for … in` / `unwind … as` / `let … in` / `let ($a, $b) in` (`9-13`, `9-14`, `9-16`, `9-17`)
  all print *"`from <relation>` for rows, or `$x in $list` against a binding"*.
  - Over a relation (`let $x in { … }`) the mechanical fix exists: `from { … }`. Print it.
  - Over a list it does not. In `unwind $list as $x  $p: Person { city: $x }` the intended text is
    `$p: Person  $p.city in $list`, which moves the test into another clause. The RFC's own fix text,
    "`$x in $list` against a binding", leaves `$x` unbound. **RFC change:** say that this refusal has
    no mechanical fix, and print the shape (`<binding>.<prop> in $list`).
- `Q012` `CASE` (`9-19`) prints the template `if(condition, then, else)`, and `||` (`9-20`) prints
  `concat(a, b)`. Applied literally, both parse and mean something else: `if` over bare names
  `condition`, `then` and `else`. Print the operands instead: `if($p.age > 65, "senior", "active")`, and
  `concat($p.name, " ", $p.handle)` with the chain flattened.
  - The `CASE` recognizer only works by accident. Lowercase `case when … then … else … end` reaches it
    only because every following word parses as a bare-name `return` item. Uppercase `CASE WHEN`
    (`9-18`, the SQL habit) is a generic `Q001` "expected kw_as, kw_and, …".
- `Q010` (`9-73`, `9-74`, `9-92`) prefixes the whole brace body with `$p.`:

  | written | printed fix | parses |
  |---|---|---|
  | `$p: Person { city: "Berlin", age > 30 }` | `$p: Person  $p.city: "Berlin", age > 30` | no |
  | `$p: Person { age: > 30 }` | `$p: Person  $p.age: > 30` | no |
  | `$p: Person { handle: $1 }` | `$p: Person  $p.handle: $1`, message "`handle: $1` is a comparison" | no |

  Split the list instead. Keep each `key: value` in the braces, qualify each comparison
  (`$p: Person { city: "Berlin" }  $p.age > 30`), and read `key: <op> v` as `$p.key <op> v`. Fire only
  on a comparison; `$1` is a bad variable, not a comparison (R5).

**R3. Two `Q008` fixes parse but change meaning, and one infix fix is wrong for lists** (§6, the
rewriter rows; §3, writes).
- `6-34-where-fix-captures-param`: `query w($x: String) { delete Person where handle = $x }` prints
  `$x: Person  $x.handle = $x  delete $x`. The new binding shadows the parameter, so the filter
  compares a node's handle with itself. The RFC's rewriter row also writes `$x` literally. **Change:**
  the row and the fix use a name not in scope.
- `6-35-where-fix-bare-in-assignment`: `update Person set { name: handle } where age > 3` prints
  `$x: Person  $x.age > 3  update $x set { name: handle }`. It qualifies the predicate but not the
  assignments. (Edition 2 refuses bare names in `set` with `T45`, so this does not affect the rewriter.
  It does affect an agent, because edition 3 invites computed values such as `age: age + 1`.)
- `9-25-infix-contains-list`: `$d.tags contains "rust"` prints `contains($d.tags, "rust")`. The RFC's
  rewriter row for a list is `"rust" in $d.tags`, and the parser cannot know the type. **Change:** refuse
  infix `contains` in the type checker, where the left operand's type picks between the two rows. Or
  print both candidates and say which one applies to a list.

**R4. Nineteen texts the RFC refuses are accepted, or read with a tree the author did not write**
(§7: "a bare identifier is not a value in a read"; §3 refused respellings). Most of these are not
refused until the type checker, and its message then names a construct the agent never wrote:

| case | written | parsed as | what the agent finally sees |
|---|---|---|---|
| `9-06` | `$p: Person  where $p.age > 30` | `$p: Person` · `where` · `$p.age > 30` | T41 "a filter must be Boolean, got aggregate" |
| `9-32` | `detach delete $p` | filter `detach`, then `delete $p` | (write: T60 here; nothing names `detach`) |
| `9-87` | `return { distinct $p.city }` | items `distinct`, `$p.city` | T36 "`distinct` cannot be projected … an alias is resolved in `order`" |
| `9-89` | `$p.name as "Name"` | items `$p.name`, `as`, `"Name"` | T36 "`as` cannot be projected" |
| `9-111` | `optional $p worksAt $c` | filter `optional`, then the triple | T41 "got aggregate" |
| `9-113` | `exists $p knows $q` | filter `exists`, then the triple | T41 "got aggregate" |
| `9-114` | `where not $p knows $q` | `where` · `not $p` · `knows` · `$q` | T41 "got aggregate" |
| `9-112` | `not $p knows $q` | `not $p` · `knows` · `$q` | T41 "`not` needs a Bool operand, got node" |
| `2-14` | `$a not $b` (edge named `not`) | `$a` · `not $b` | T41 "a filter must be Boolean, got node" |
| `9-115` | `return { n = $p.name }` | one comparison item | T7 "… got aggregate and String" |
| `9-63` | `$p.out(knows)` | `$p.out` · `(knows)` | T6 "no property `out`" |
| `7-24` | `$x: Colleagues($h)` (withdrawn view binding) | binding · `($h)` | T1 "unknown node type" |
| `4-10` | `from (union(…))` | filter `from` · `(union(…))` | T60 |
| `4-20` | `from colleagues ($h)` | `from` · `colleagues` · `$h` | T41 "got aggregate" |
| `6-33` | a write body opening with the bare word `match` | filter `match` | T60 |
| `2-08` | `$d in $x` (reserved edge) | membership filter (P-F19) | T7 "`in` needs a list on the right, got node `Collection`"; no quoted-edge fix |
| `2-27` | `$a knows{1,}` (open bound, a later form) | accepted | T15 "unbounded traversal is disabled", which is honest |
| `2-09`, `2-10` | edges named `from` and `let` (see R6) | `$a` · `from $b`; `$a` · `let` · `$b` | T60; T41 |

- Two root causes cover 17 of the 19.
  - **Bare identifiers as operands.** The edition-3 `operand` keeps `expr_ident`. So every keyword
    from another language, written lowercase, is a legal filter or return item.
  - **Juxtaposition.** Clauses and return items have no separator, so a misplaced word starts a new
    item instead of failing.
- **Change (§7, and the code):**
  1. Take `expr_ident` out of `operand` in `match` and `return`. Keep it only in `ordering`, where a
     bare name is a return alias, and in the `where_stmt` recognizer. The RFC's own sentence (§7)
     already says that a bare identifier is not a value in a read, and the type checker refuses every
     one of these texts anyway. With this change each becomes a parse error at the foreign word.
  2. Add a short keyword table that prints the fix for each foreign word: `where` (drop it), `detach`
     (drop it), `distinct` (`return distinct`), `with` and `call` (`from`), and `optional`, `exists` or
     `not` without a brace (the block form).
  3. At clause start, `from` not followed by `{`, `$` or a call, `let` not followed by `$`, and
     `optional`/`not`/`exists` not followed by `{` are parse errors, not bare filters. A bare filter
     with those names is ill-typed, so no legal text is lost.

**R5. 109 of the 154 refusals are the generic `Q001`. Its message lists pest rule names, and several
name the wrong construct** (A5; §5).
- Examples of rule names as the message: `'Berlin'` → "expected unary"; `$p.city <> "Berlin"` →
  "expected unary"; `2e3` → "expected unary" (P-F10 refuses it but prints nothing); `$a -[:knows]-> $b`
  → "expected literal3"; `count(*)` → "expected kw_distinct, not_expr3, call_args"; Cypher `WITH` →
  thirteen rule names including `arrow_open` and `undirected_edge3`.
- Messages that point the agent at the wrong construct:
  - `limit 10 offset 5` (`9-09`) → "expected kw_order". Following it writes an `order` after `limit`,
    which is also illegal.
  - `having …` (`9-48`, `9-49`) → "expected kw_limit, kw_order"; `retrieve { … }` (`9-50`) → "expected
    kw_return"; a write body wrapped in `match { }` (`6-07`, P-F2) → "expected kw_return". Each pushes
    the agent toward the read form.
  - `$p.city = null` and `!= null` (`6-29`, `9-40`) → `Q005` "`null` is a reserved word; a property of
    that name is written `$p.null`". The fix is `is null` / `is not null`.
  - `$p.city not in [...]` (`9-57`) → `Q005` "`in` is a reserved word; a property of that name is
    written `$p.in`". The fix is `not $p.city in [...]`.
  - `$p: Person { handle: $1 }` (`9-92`) → `Q010` "`handle: $1` is a comparison".
  - A malformed edition line (`gq 3` without `;`, `GQ 3;`, a `set` before it; `8-09`, `8-10`, `8-11`)
    silently selects edition 2. The error then lists edition-2 rule names (`query_decl`,
    `missing_query_keyword`) and never mentions the edition line.
- The RFC recognizes edition 2's habits and its own withdrawn drafts (arrows, `where`, `mutation`, infix
  `contains`). It does not recognize the other languages' habits that the comparison asset lists as
  precedent. By habit, cases that differ from their header: SQL 29 of 43, Cypher 27 of 37, Gremlin 5 of
  6, GQL 4 of 4, Datalog 4 of 6, SPARQL 3 of 5, GraphQL 3 of 7, GQ edition 2 4 of 13. The Cypher arrow
  agents write most, `-[:KNOWS]->` (`9-03`, `9-101`), is not the one the arrow recognizer takes.
- **Change:**
  1. Map rule names to construct names in `pest_error_to_diagnostic`.
  2. Add recognizers, each with an edit, for the top habits in this corpus: single quotes, `<>`, `==`,
     `&&`, `!`, uppercase keywords (`WHERE`, `ORDER BY`, `DESC`, `LIMIT`, `AS`, `IS NOT NULL`, `CASE`,
     `COALESCE`, `COUNT(DISTINCT`), `count(*)`, `-[:e]->`, `(a:T)`, `?x`, `--` and `#` comments,
     `return x` without braces, `offset`/`skip`, `group by`, `having`.
  3. Give an edition-line near miss a `Q006` with `gq 3;` as the fix.
  4. State in §5 that a later form's refusal names the stage. `offset` should say paging is stage P,
     not "expected kw_order".

**R6. The RFC accepts texts the parser refuses or reads differently. Each is known, but the agent-facing
cost is new.**
- Edges named `from` and `let` (`2-09`, `2-10`; §7 says legal, P-F8 makes them quoted-only). The
  unquoted text is not refused: it is silently re-read as two or three clauses (R4), and nothing prints
  `$a "from" $b`. **Change:** the RFC adopts P-F8, and the reading where a bare-name `from`/`let`
  clause follows a lone variable becomes a parse error with the quoted edge as the fix.
- A space before a call's `(` (`4-20`, `4-21`; P-F9). The RFC's lookahead is "an identifier followed by
  `(`". The parser requires the two to touch. `from colleagues ($h)` is then silently three filters, and
  `from nearest ({ … })` is "expected not_expr3". **Change:** recognize `name (` (with whitespace) in
  clause and operand position and print the touching form. Reading it as two items is never what was
  meant.
- A filter that begins with `-` (`1-22`, `1-26`; P-F5) → "expected clause3", with no fix. The fix is
  `(-1) < $x`.
- The asset's `colleagues_of_colleagues` and `weighted` (`4-04`, `5-10`; P-F3, C-F2) are still in the
  asset and still refused, with "expected kw_match".
- A schema declaration in the one grammar (`7-12`; §7) is not built. The parser spike says so.
- RFC §6 says that a file without the edition line is read "in the compiler's current edition, which
  this release makes 3". The prototype reads it as edition 2 (P-P1). That is the right choice for a
  prototype, but it means this corpus could not test the no-line default.

### Grammar ambiguities

**R7. P-F5's fix is incomplete: the RFC's own `$n -1` rule silently merges items** (§3: "`$n -1` and
`$n - 1` are one subtraction"; §7, the parser test pins `$n -1 > 0`).
- `1-13`: `return { $p.age` *(newline)* `-$p.age as neg }` is one item, `$p.age - $p.age as neg`.
  Edition 2 admits newline-separated items in stored text.
- `1-29`: `let $x = $p.age  -1 < $x` becomes one clause. The let's value is the comparison
  `$p.age - 1 < $x`, and the filter disappears.
- P-F5 refuses a filter that *begins* with `-`. But the ladder takes the `-` before any clause
  boundary, so the refusal never sees these two texts.
- **Change (RFC §3):** refuse a binary `-` with whitespace before it and none after (`$n -1`, `x  -y`),
  with the fix `$n - 1`, instead of pinning it as a subtraction. That removes both silent merges and
  P-F5's leading-minus rule, without requiring commas. If the RFC keeps `$n -1`, then it should require
  commas between `return` items in edition 3; the rewriter can insert them.

**R8. A chained comparison reads as an undirected traversal** (§7 `undirected_edge`). `2-26`:
`$x < y > $z` parses as `$x <y> $z`. It can be caught only at type check, because edge `y` does not
exist. The refusal there would name an unknown edge, not a chained comparison. (The prototype stops
earlier, with `T60` for the `let`.) `18 <= $p.age < 65` (`9-96`) is a
generic `Q001`. **Change:** give the non-associative comparison a recognizer whose fix is
`a < b and b < c`, tried after the traversal fails.

**R9. A later form that starts at a token edition 3 accepts is not additive** (§5: "a form a later
stage adds starts at a token edition 3 refuses"). P-F13 showed this for `@budget`. This corpus finds
the same for:
- `knows{1,}` (`2-27`; the open upper bound, §2 Expand's "one later change"), refused only by the type
  checker's T15;
- `$p.@type_id` (`7-18`; T6 at type check);
- the catalogue edges `@property` and the types `@Type`/`@Property` (`2-22`, `7-14`);
- any unknown annotation (`7-03`).

Each is honest today. But the RFC's rule says a later stage's form begins at a token the grammar
refuses, and these do not. **Change:** either state that a type-level refusal is an acceptable door
(and pin each with a test, as `T15` is), or make the grammar refuse the open bound and the unknown
meta-field.

### One spelling per meaning

**R10. Sixteen pairs of accepted texts mean the same thing, give two trees, and are on neither the
formatter's list nor A6's exception** (A6; §6 `queries fmt`; C-F14 found pairs 1 to 5). In
`results/pairs.md`, four pairs are on the formatter list (braces/filter, `exists`/`count > 0`,
`not`/`count = 0`, a reversed triple). Two are A6's literal-and-reference exception (`let $r = rel` then
`from $r`; `limit 10` against `$k = 10`). Two give one tree (commas against whitespace, redundant
parentheses). One is not a pair (`p23`: `optional`'s derived union cannot be written, because a read has
no null literal, so `optional` is irreducible at the surface). The rest:

| pair | the two texts | the fix |
|---|---|---|
| `p05` | `union(a, b, c)` / `union(union(a, b), c)` | fmt flattens (C-F14) |
| `p07` | `$p: Person  $p knows $q` / reversed | C-F17; fmt keeps `from`/`let` order, sorts the rest |
| `p08` | `order { x asc }` / `order { x }` | fmt drops `asc` (the tree keeps `Some(false)` against `None`) |
| `p09` | `not { p }` / the filter `not exists { p }` | **the derived-forms table's own core term**; fmt rewrites the filter |
| `p10` | `exists { p }` / `count { p } >= 1` | add `>= 1` beside `> 0` |
| `p11` | two filter clauses / one `and` | fmt splits top-level `and`, or A6 says conjunction is one term |
| `p12` | `return { $p }` / `return { $p as p }` | fmt drops an alias equal to the name |
| `p14` | `$p <knows> $q` / `$q <knows> $p` | fmt orders the ends |
| `p15` | `(knows \| worksAt)` / `(worksAt \| knows)` | fmt sorts alternatives |
| `p16` | `k: 20, recall: 0.9` / `recall: 0.9, k: 20` | fmt writes the registry order |
| `p17` | `bm25(…, $q)` / `bm25(…, terms($q, mode: any))` | §4 makes the String form mean the other; pick one canonical form |
| `p18` | `terms($q)` / `terms($q, mode: all, max_edits: 0)` | fmt drops options equal to their default |
| `p19` | `rrf(…, k: 60)` / `rrf(…, k: 60, weights: [1.0, 1.0])` | same |
| `p24` | two write statements in either order | §3 says statements see one snapshot, so the order means nothing; fmt sorts, or A6 lists it |
| `p25` | `count { p } > 2` / `let $n = count { p }  $n > 2` | different core terms (a Project), same rows; not an A6 violation, only a style choice |
| `p26` | `from friends($h)` / the body inlined | `Call` inlines, so the terms are equal after expansion; A6 should exempt a declared read |

**Change:** take C-F14's restatement of A6 ("one canonical spelling; the formatter's list is the
complete set of non-canonical forms"). Then add these rows to the §6 list, or name each as an
exemption. `p09` matters most, because the RFC's own table defines `not { p }` as the filter `p09`'s
second text spells.

### What this corpus confirms or contradicts in the earlier spikes

**R11. Confirmed as stated:**
- P-F7: a block in clause position is a filter (`3-12`).
- P-F11: `delete $p where φ` prints `φ  delete $p` (`6-10`).
- P-F16: `contains(…)` after a Boolean filter is a call (`1-30`).
- P-F20: the kind is known at parse time (`6-13`, `9-102`).
- P-P2 and P-P3: 70 cases put each of `from let union insert update delete in as distinct asc desc
  optional exists not` in five positions (a property after a dot, a binding key, an alias ordered by,
  a parameter, an update assignment). All 70 behave as the RFC says: `in` and `not` are refused as
  aliases, and every other word in every position is accepted.
- The section-7 pins (`$n - 1 > 0`, `$n -1 > 0`, `$n-1 > 0`, `$n - $m > 0`, `$a -knows- $b`) hold. So
  do every arrow fix and every `Q008` fix over a type with no name collision.

**R12. Extended or contradicted:**
- P-F5: contradicted in part (R7). Its rule does not make a `-` mean the same after every clause.
- P-F8 and P-F9: each closes an ambiguity, but each opens a silent misreading (R4, R6). They need
  recognizers, not only the restriction.
- P-F10: confirmed, but the refusal is "expected unary" with no fix.
- P-F13: extended to four more later forms (R9).
- P-F19: confirmed. The type checker's actual text (T7 "`in` needs a list on the right, got node
  `Collection`") does not print the quoted-edge fix that P-F19 moves to it.
- P-F2: confirmed. The diagnostic, "expected kw_return", is the worst one available (R5).
- C-F18: the rewriter's `delete WorksAt where @src = $h` → `$x: WorksAt  $x.@src = $h  delete $x`
  parses (`6-11`), because `binding` takes any type name. Whether a binding over an edge type is legal
  is still the open question C-F18 asks.
- The type-check column shows T40 still refusing `count { … } >= $x.min_docs` (`3-07`). That is
  expected, because the prototype lowers into edition 2's checker and does not lift T40.

## (b) What was built and how to run it

| Piece | Where |
|---|---|
| Corpus, 395 cases, one `.gq` file each, with headers `task`, `kind`, `expect`, `habit` (adversarial), `rfc`, optional `shape` / `returns` (the printed tree the RFC implies), `later` (the refusal the RFC assigns to a later stage) and `note` | `docs/spikes/gq3-redteam/corpus/` |
| One-spelling pairs, 26 files, two texts split by `// ---`, header `covered: fmt \| a6 \| none \| same-tree \| n/a` | `docs/spikes/gq3-redteam/pairs/` |
| Generator: the cases as data; regenerates both directories | `docs/spikes/gq3-redteam/make_corpus.py` |
| Runner | `crates/omnigraph-compiler/src/query/parser3_redteam.rs` |
| Pinned mismatches, one line per case with the observed difference | `docs/spikes/gq3-redteam/known-mismatches.txt` |
| Last run's output | `docs/spikes/gq3-redteam/results/` (`results.md`, `fix-audit.txt`, `pairs.md`) |

Corpus by kind: lookups and filters 58, traversal 30, aggregation 33, shapes of a result 38, ranking
and search 28, writes 49, catalogue and declarations 24, statements 15, adversarial 120. The
adversarial cases by habit: SQL 43, Cypher 37, GQ edition 2 13, GraphQL 7, Gremlin 6, Datalog 6,
SPARQL 5, GQL 4. The 275 non-adversarial cases include:
- every asset example;
- each RFC example (§3 `close_company` and `reachable`, §6 `passages`, §8 `big_employers`, `cited_near`
  and `retire`) once more, rewritten in a form the RFC says is equivalent (reordered clauses, a `let`
  population, statements swapped);
- every row of §3's refused-respellings table;
- the later forms of the freeze list and §11.

The runner, for each case:
1. Parses with `parse_query3` when the text has an edition-3 line, and through the front door
   otherwise. `gq 3;` is prepended unless the header says `raw: yes`.
2. Compares the outcome with `expect`, and for accepted cases compares the printed tree with
   `shape`/`returns`.
3. For a refusal: applies the `suggestion` edits if there are any. Otherwise it puts the `fix` text over
   the innermost recognized construct that contains the diagnostic's position. It reparses, and compares
   the tree with the tree the header's expected fix gives.
4. Runs the front door (parse and lowering) and, when that lowers, today's type checker against the
   RFC §8 schema written in edition-2 `.pg`. This is the "front door" column, which shows what the
   agent finally sees.

Run:

```bash
# Pinned: green while the mismatch set equals known-mismatches.txt (part of the normal suite)
cargo test -p omnigraph-compiler --lib redteam_corpus_matches_its_pinned_mismatches

# The instrument: prints the table, fix audit and pair table; fails on any mismatch
GQ3_REDTEAM_OUT=$PWD/docs/spikes/gq3-redteam/results \
  cargo test -p omnigraph-compiler --lib redteam_corpus_strict -- --ignored --nocapture

# After editing cases
python3 docs/spikes/gq3-redteam/make_corpus.py
```

Verification on the branch:
- `cargo test -p omnigraph-compiler --locked`: 524 library tests pass, with the strict instrument
  ignored, plus 2 others.
- `cargo clippy -p omnigraph-compiler --all-targets --locked -- -D warnings -W clippy::dbg_macro` and
  `cargo fmt` are clean.
- `cargo dev-test -p omnigraph-compiler` (the whole workspace graph) was run with
  `RUST_MIN_STACK=16777216`; the result is recorded below.

The parser spike's edition-2 corpus walk collects every `.gq` file under `docs/`. One change was needed
in its owner test: the walk now skips `docs/spikes/gq3-redteam`, which holds edition-3 text. Nothing
under `crates/omnigraph-reference-engine/` changed, and no edition-2 behaviour changed.

DEV-TEST-RESULT

## (c) What I did not get to

- **No live agent.** This corpus was written by me as the agent, from the RFC, the asset and the
  habits listed in the brief. It is not the M0 competence instrument. First-try validity and repair
  turns per task are still unmeasured.
- **Type-level refusals.** The RFC refuses `T65`, `T68`, `T69`, `T70`, the positional-option error, an
  unknown function, `T17` and others at type check. The prototype refuses every new node with the
  placeholder `T60`. Those cases carry a `later:` header and parse-accept. Whether their fixes lead
  anywhere could not be measured.
- **The proposals are not prototyped.** That covers R4's removal of bare identifiers, R5's habit
  recognizers and R7's spacing rule. The corpus would measure each: the R4 change alone should flip
  most of the 19 silent cases to refusals at the right word.
- **Meaning is checked only where the header names an expected fix.** Of the 22 fixes counted as
  parsing correctly, those without an expected fix were checked only for parsing.
- **No property test over refusals.** The runner checks the fixes of hand-written cases only.
  Generating near-miss texts from accepted ones (drop a brace, uppercase a keyword, swap `:`/`=`)
  would be the next step.
