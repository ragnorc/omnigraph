# Query fuzz: edition-2 queries against GQ 3 (#1316)

This directory fuzzes randomly drawn GQ 2 (edition 2) queries against the GQ 3
binary of draft pull request ModernRelay/omnigraph#1316. It looks for every way an
existing query breaks or changes behaviour: refusals, and queries that still
compile but return other rows, columns, types or errors. It is defect finding
only. Nothing here changes `main`, `gq3/phase-0-front-end` or the RFC, and all
data is synthetic.

Run on 2026-11-24, on one Modal dev box, against fixed commits:

| Binary | Commit | `omnigraph version --json` |
|---|---|---|
| EDITION-2: GQT runner and CLI built as the Cross-Edition Comparison job builds them (`cargo build --locked -p omnigraph-gqt -p omnigraph-cli --bin omnigraph-gqt --bin omnigraph`, debug) | `EDITION_2_SOURCE_COMMIT` 9ad7c8ef91594463636a3048ea20bbb557720cc6 | `0.16.1`, `http_contract` `0.14`, writes storage 18, serves 14 to 18, no `gq_language` |
| NEW: the same two binaries at #1316's head, `gq3/phase-0-front-end` | a74de14071cf125157eb147b7209a4357cba9071 | `0.16.1`, `http_contract` `0.14`, `gq_language` `3.0`, writes 18, serves 14 to 18 |
| Released v0.16.0 CLI (`scripts/install.sh`) | 00b8cca842f2bf009bebeefcf765672b5ff64b26 | `0.16.0`, channel `stable`, writes 17, serves 14 to 17 |

v0.16.0 answers what EDITION-2 answers. On 2,400 generated cases the two binaries
gave the same status, codes, rows and types; the only reported differences were
my comparator's own error and one reorder on a tied key
([results/v0.16.0-vs-edition-2.md](results/v0.16.0-vs-edition-2.md)).

Sanctioned differences come from the RFC's Compatibility and reversibility
section, on fork branch `rfc/gq-3` (3b164256); the upgrade guide
`docs/user/queries/upgrading-to-gq3.md` at a74de140; the `open:` rows of
`crates/omnigraph-gqt/cross-edition.tsv`; and the migration matrix's
vocabulary (fork branch `gq3/migration-matrix`, 913bc37b).

## What ran

| | |
|---|---|
| Fuzzing time | 4.6 hours of campaign (6,371 worlds), in chunks under 10 minutes each, resumed by seed |
| Seeds | 1000..=6148 (generator v1, 5,149 worlds) and 500000..=501221 (generator v2's compatible texts, 1,222 worlds) |
| Cases | 152,904: 119,433 reads and 33,471 writes, 24 per world |
| Accepted by EDITION-2 | 123,560 (80.8%). Of the cases drawn to be accepted, 123,234 of 137,891 (89.4%). Of the 15,013 drawn to be refused, EDITION-2 accepted 326 |
| Compiled unchanged by NEW and ran | 24,480 reads and 7,088 writes, all of them EDITION-2-accepted texts (oracle 1's population) |
| Worlds whose schema or data either binary refused | 0 |
| Existing corpus | 1,767 query declarations of the 510 edition-2 GQT cases at 9ad7c8ef, through oracles 2 to 4 |
| GQT path | the 18 repros and a sample of 150 generated cases as `.gqt` files, through `scripts/cross-edition.py run-generated` |

Every check that failed is one line of `results/checks.jsonl.gz`, which holds
the seed, case, oracle, signature and attribution. `results/checks-by-signature.tsv`
counts the checks. `results/findings-by-cause.md` groups them by cause, and
`results/summary.json` holds the totals. The raw observations (468 MB) stayed on
the box. Rebuild any case from its seed with `harness/replay.py`.

## Findings

Class **a**: intended and listed in the guide or a Compatibility bullet.
Class **b**: intended (the branch, its RFC or its table says so) but not listed
anywhere a user would read. Class **c**: defect. An oracle number refers to the
brief: 1 is unchanged text, 2 is refusals, 3 is fix correctness, 4 is fix
closure, 5 is edition-2 refusals NEW accepts, and 6 is integrity. A count is the
number of distinct generated cases unless it says checks.

### Defects (class c)

| # | Finding | Oracle | Cases | Smallest repro | EDITION-2 | NEW | Where |
|---|---|---|---|---|---|---|---|
| C1 | `lint --fix` never converges on a block that holds only filters, such as `count { $t.count > 2 } != 0` | 4 | 7,083 (4.6% of all cases; every `does_not_converge` seen) | [repro/dnc-filter-only-block](repro/dnc-filter-only-block), [repro/count-block-filter-only-hand](repro/count-block-filter-only-hand) | rows (`t1`) | `Q017`; `lint --fix` loops: `Q017` moves the filter into the block's own `where`, `Q014` deletes that `where`, 8 times each; `does_not_converge`, file unchanged | `omnigraph-compiler`: the `Q017` suggestion inside a block, and `Q014` |
| C2 | **A fix that compiles and changes the answer.** `update T set {…} where φ` (or `delete … where`) followed by `insert U {…}`: the `Q008` fix opens a group with `match` for the first statement and leaves the `insert` in that group, so it runs once per selected row | 3 | 685 | [repro/fix-insert-joins-update-group-hand](repro/fix-insert-joins-update-group-hand) (2 rows), [repro/fix-insert-joins-update-group](repro/fix-insert-joins-update-group) (0 rows) | updates 2 rows, inserts 1 `Note`; with 0 matching rows, inserts the `Person` anyway | the fixed text inserts 2 `Note`s (one per updated row); with 0 rows it inserts nothing. A keyed `insert` conflicts with itself (`key_conflict`, 29 cases), a `@unique` one fails (13). `affected` differs in 643 cases, graph state in 499 | `omnigraph-compiler`: the `Q008` fix, against the guide's row "one group per statement, each opening with its `match`" |
| C3 | `rrf` with an explicit third argument gets no edit, even `60`, which is the guide's own Rankings example | 2, 4 | 394 | [repro/rrf-explicit-k-no-edit](repro/rrf-explicit-k-no-edit) | runs | `T65` with no `suggestion`; `lint --fix` stops `unfixed` (`nearest` and `bm25` alone, or `rrf` without `k`, get the edit) | `omnigraph-compiler`: the `T65` edit for `rrf` |
| C4 | `rrf` ranking a binding that only a triple introduces (`$d about $t` with no `$d: Doc`) gets no edit | 2, 4 | 45 | [repro/rrf-triple-bound-binding-no-edit](repro/rrf-triple-bound-binding-no-edit) | runs | `T65` with no edit; `nearest` or `bm25` over the same binding gets one, and the guide says the edit "inserts the binding of every name a population's triple introduces" | `omnigraph-compiler`: the `T65` edit for `rrf` |
| C5 | Panic: `index out of bounds` in `grouped_order_key` | 6 (and 4: `lint` crashes) | 10 (7 on texts EDITION-2 accepts) | [repro/panic-grouped-order-key](repro/panic-grouped-order-key) (an unbound `$o3` in `return { $o3, $o1.name, count($o3) as a0 } order { $o1.name desc }`); [repro/panic-grouped-order-key-edition-2-text](repro/panic-grouped-order-key-edition-2-text) (an EDITION-2-accepted read; `lint --fix` panics in a later pass) | `T6`, or rows | the CLI, `lint` and the GQT worker crash (`crates/omnigraph-compiler/src/query/check/mod.rs:2772`); `query --json` prints the crash, not a coded error | `omnigraph-compiler`, `check/mod.rs` `grouped_order_key`: an order key's position among the written return items indexes `out_cols`, which omits a refused item |

### Intended but not listed (class b)

| # | Finding | Oracle | Cases | Smallest repro | EDITION-2 | NEW |
|---|---|---|---|---|---|---|
| B1 | A read named after a registry function (`query count()`, `query search()`) is refused with no edit. The guide lists `T74` but says every older form prints its fix. Where the body calls the function it is named after, the refusal is `T69` ("`search(…)` is a relation"), also with no edit | 2, 4 | 5,253 | [repro/read-named-after-function-no-edit](repro/read-named-after-function-no-edit), [repro/read-named-search-calls-itself](repro/read-named-search-calls-itself) | rows | `T74` / `T69` with no `suggestion`; `lint --fix` stops `unfixed` |
| B2 | Write groups select on one snapshot: a later group reading a type an earlier group changes is `T91`, with no edit | 4 | 2,678 | (cross-edition.tsv's `open:write-groups-one-snapshot`) | runs its statements in order | `T91`, `lint --fix` stops |
| B3 | `fuzzy(f, q, n)` with `n` > 2 is refused with no edit. RFC section 4 bounds `max_edits` to 0..=2; the map writes `max_edits: n` with no bound, and edition 2 accepted any u32 | 4 | 563 | [repro/fuzzy-max-edits-3-no-edit-hand](repro/fuzzy-max-edits-3-no-edit-hand) | runs | `T71` "`max_edits: 3` is outside 0..=2", no edit |
| B4 | A block that names a variable a later outer binding declares (`exists { $p knows $x }` … `$x: Company`) was independent in edition 2; GQ 3 resolves it to the outer binding | 2, 4 | corpus: 6 steps of `issue_659_compiler_scopes.gqt` | [repro/later-outer-name-in-block](repro/later-outer-name-in-block) | rows | `T5` (or `T68` for an edge binding), no edit |
| B5 | An empty list assigned to a non-String list property, which edition 2 typed `[String]` and refused, is accepted | 5 | 525 | [repro/empty-list-assignment-accepted](repro/empty-list-assignment-accepted) | `T3` | writes `[]` |
| B6 | A whole-number float literal assigned to an integer property, which edition 2 refused at run time, is stored (a fractional one stays refused in both) | 1 | 467 | [repro/whole-float-into-integer](repro/whole-float-into-integer) | `cannot convert Float(100.0) to Int32` | stores `100` |
| B7 | Two keyed inserts of one key in one write: edition 2 kept the last; GQ 3 refuses. The mutations guide names "a key the write staged earlier"; the Compatibility bullet and the guide's map name only an existing key | 1, 3 | 16 | [repro/keyed-insert-repeated-in-one-write](repro/keyed-insert-repeated-in-one-write) | 1 row, `n: 2` | `key_conflict`, nothing written; `lint --fix` leaves it |
| B8 | A float literal with an exponent (`4.6e+18`), a parse error in edition 2, is accepted | 5 | 4 | seed 3155 case 20 | `Q001` | runs |
| B9 | `cross-edition.py run-generated`: a worker panic is a run-level trace record, so the step reader saw no error and the step read as a success. Now reported as `current_panic` | harness | - | `results/repro-cross-edition-report.json` | - | - |

### Sanctioned (class a), with their counts

| Attribution | Oracle | Cases | What differs |
|---|---|---|---|
| `writes.keyed-insert` | 1, 3 | 3,282 | a keyed `insert` of an existing key (the unchanged text compiles: 1,955) fails with `key_conflict` and writes nothing |
| guide, Rankings: no edit under an aggregate or `distinct` | 4 | 3,220 | `T65` without an edit |
| `results.bm25-f64` | 3 | 1,717 | `bm25` column `Float32` becomes `Float64`; values equal within F32 |
| `results.limit-tie-rule` | 1, 3 | 911 | `limit` without `order` returns other rows (651 on unchanged text) |
| `results.distance-rename`, `results.bm25-score-rename`, guide Rankings (`._rrf` becomes `_score`) | 3 | 894, 856, 68 | unaliased ranking columns renamed |
| contract `D10` (tied sort keys) | 1, 3 | 652 | same rows in another order, or other rows at a `limit` cut. 92 of 98 cut cases were verified to have equal key sequences. The others order by a ranking or by a generated id, which differs between the two graphs |
| `results.hop-range-self-loop` | 3 | 405 | a hop range no longer returns its start through a self-loop (verified: the traversed edge has a self-loop in the data) |
| guide, Other changes: accepted spellings | 5 | 317 | upper-case keywords, `<>` and single quotes are accepted. 55 of 60 checked answer exactly what EDITION-2 answers for the canonical spelling. The other 5 also hold an empty list or a whole float (B5, B6) |
| `results.full-text-analysis` / `index-state` | 3 | 111 | `fuzzy`, `search` and `match_text` rows under `english_v1` and the exact scan (`café` folds to `cafe`, within 2 edits of `base`) |
| `results.integer-float-comparison` | 3 | 79 | an integer property compared with a whole-number float beyond 2^53, in reads and in a write's selection |
| `language.rrf-two-bindings` | 4 | 48 | `rrf` arms ranking two bindings: `T65`, no edit |
| `language.accepted-item-beside-aggregates` | 5 | 42 | `T9` lifted |
| `results.fusion-window` | 3 | 34 | `rrf` rows and scores change: the rewrite ranks the whole type, where edition 2 ranked the filtered candidates, so the fused score moves (for example 1/61 becomes 1/63) |
| `results.avg-exact-integer` | 1, 3 | 16 | `avg` over integers near 2^53 |

Also checked and found clean:

- **Oracle 1** (unchanged text): no unsanctioned difference in rows, order,
  columns, types or graph state among the 31,568 EDITION-2-accepted texts that NEW
  compiled and ran unchanged. Every difference there is `writes.keyed-insert`,
  B6, B7, the limit tie rule, `D10` ties or the `avg` rounding.
- **Oracle 6**: no hang, no answer that differed between two runs of NEW (every
  read was run twice), and no coded error missing under `--json` except C5's
  crash.
- **Oracle 4**: every fix that converged was closed (`lint --fix` on its own
  output changed nothing), and none wrote a source NEW refuses.

### Edition-2 forms that compile unchanged under GQ 3 with a different answer

- A keyed `insert` of an existing key: runs, then `key_conflict` (sanctioned).
- Two keyed inserts of one key in one write: `key_conflict` (B7, not listed).
- A whole-number float assigned to an integer property: stored where edition 2
  failed (B6, not listed).
- A read with `limit` and no `order`: other rows (sanctioned).
- A sorted read with ties on its keys: another order among the ties, or other
  rows at a `limit` cut (contract `D10`).
- `avg` over integers whose `F64` sum loses precision (sanctioned).

### Fixes that compile but change the answer

- **C2**, the only unsanctioned one: a statement after `update … where` or
  `delete … where` joins the fix's `match` group and runs once per selected row.
- Sanctioned changes in a fixed text's answer: the `bm25` type, the
  ranking-column renames, `rrf` scores and rows (fusion window), the hop-range
  self-loop, full-text analysis, and integer/float comparison.
- A keyed `insert` that `lint --fix` leaves in place, as the guide says it
  does: it still fails with `key_conflict`.

### The existing corpus through oracles 2 to 4

`harness/corpus.py` ran all 1,767 query declarations of the 510 edition-2
cases. EDITION-2 accepts 1,664 of them and NEW compiles 799 of those unchanged.
For the others, `lint --fix` fixed 839 and left 42 unfixed. No fix failed to
converge, every fix was closed, no fix wrote a refused source, and nothing
panicked. The unfixed edition-2-accepted steps:

| Code | Steps | Class |
|---|---|---|
| `T91` | 15 | b (B2) |
| `T65` | 5 | a: 3 under an aggregate or `distinct`; c: 2 are C4, an `rrf` over a binding only a triple introduces (`issue_762`, `issue_789`) |
| `T81` | 6 | 5 fixed with `--review-fixes`, as the guide says; 1 stops at another refusal |
| `T75` | 4 | a (`rrf` over two bindings) |
| `T5`, `T68` | 3 each | b (B4: `issue_659_compiler_scopes.gqt` steps 20, 21, 25 to 28) |
| `T42` | 3 | a (grouped order key on a meta-field) |
| `T74` | 2 | b (B1) |
| `T7` | 1 | b: cross-edition.tsv's `open:empty-list-without-context` |

The data is in `results/corpus.jsonl.gz`.

## Coverage

[results/coverage.md](results/coverage.md) counts every edition-2 form of the
brief: the cases containing it, and how many of those EDITION-2 accepted. Every
form was drawn at least 1,700 times and accepted at least 170 times, except
the forms edition 2 refuses outright (`mutation`, `optional`, upper-case
keywords). Parameters of all twelve types were drawn, as were unused
declarations.

58 of 946 pairs of read forms were never drawn together in an accepted case.
Most pairs are structural, because the generator draws at most one block, one
full-text predicate and one ranking per read, and treats `distinct` and
aggregates as exclusive:

- `count`, `exists`, `not` and aggregate blocks with each other.
- `search`, `match_text` and `fuzzy` with each other.
- `nearest`, `bm25` and `rrf` with each other.
- An aggregate with `distinct`.
- A ranking in `return` beside another order key.

The rest follow from the ranking forcing a `limit` and an `order`: a ranking or
a grouped meta-field key with `limit` but no `order`. The list is in the
coverage file.

Two forms in the brief are not edition 2. `mutation name() { … }` and
`optional { }` are parse errors (`Q000`) at `EDITION_2_SOURCE_COMMIT`, so they
were drawn only as edition-2 refusals: 2,421 and 366 cases. NEW refuses every
`mutation` with `Q009`, and `lint --fix` respells it: 1,648 of the 2,421
then ran, 444 failed at run time (keyed inserts and C2), and 329 stopped at
another refusal.

## How it works

- `harness/gen.py`: a seeded generator. `draw_world(seed)` draws 1 to 5 node
  types and 0 to 4 edge types, with self-edges and parallel edges. Properties
  are every scalar type, lists, vectors, Blob and enums, each nullable or
  required. Keys are single, compound or absent, and constraints include
  `@unique`, `@range`, `@card`, plain and body-level `@index`, and edge
  `@key(@src, @dst)`. The seed data holds nulls, empty strings, stop-word-only
  text, case and stem variants, NFC and NFD forms, integers past 2^53, tied
  values, self-loops and cycles. `draw_queries` follows edition 2's grammar
  (`query.pest` at 9ad7c8ef) and type rules, type-directed against the schema,
  and tags each query with the forms it holds. About 12% of generator v1's
  queries are mutated into edition-2 refusals. Seeds from 500000 draw generator v2's compatible texts:
  single-binding reads with comma lists, and node inserts, both of which NEW
  also parses. Seeds below 500000 draw exactly what v1 drew.
- `harness/run.py`: the campaign. Each world is initialized, loaded
  (`--mode overwrite`) and, in half the worlds, indexed (`index build`) by each
  binary. Each case then runs:
  1. EDITION-2 on the edition-2 text, with types taken from `explain`'s
     physical root.
  2. NEW on the same text, twice.
  3. NEW `lint --fix`, plus `--review-fixes` where the default stops, then NEW
     on the rewrite, then `lint --fix` on the rewrite again.

  A write runs on a fresh copy of each binary's graph, and the export after it
  is compared, as well as the reply. One JSONL file is written per world; a run
  resumes by seed.
- `harness/classify.py`: the six oracles, and the attribution of each
  difference to a Compatibility bullet, a guide row, an `open:` row of the
  table, or a named cause.
- `harness/report.py` writes the summary, the coverage file and the findings
  by cause.
- `harness/shrink.py`: a greedy shrinker that drops query lines, list items,
  conjuncts, schema types, properties, constraints and data rows while a
  predicate still holds. `harness/replay.py` rebuilds a world from its seed;
  `harness/peek.py` prints the cases behind a signature.
- `harness/corpus.py`: the existing corpus through oracles 2 to 4.
- `harness/emit_gqt.py`: writes a case as a `.gqt` file with schema, seed, the
  step and expectations blessed from EDITION-2's runner
  (`OMNIGRAPH_GQ_BLESS=1`), and the `lint --fix` rewrite as a sibling
  `.fixed.gqt`.
- `scripts/cross-edition.py run-generated` (added on this branch) runs a
  directory of such cases. The edition-2 text runs on both runners and the
  rewrite on the current one, compared step by step with the job's own
  `differences()`. For the 18 repros it reproduces every finding above, C5's
  panic included. For the 150-case sample it finds the same differences the
  CLI harness classified: the `bm25` type, the renames, the limit tie rule, C2
  and keyed inserts ([results/gqt-sample-cross-edition-report.json](results/gqt-sample-cross-edition-report.json)).

### Limits

- The bulk campaign compares through the CLI (`query --json`, `mutate --json`,
  `export`) on a local file graph. Column types come from `explain`, which
  gives the planner's types, such as `p: Person` for a node object, not the
  Arrow struct. The GQT path compares Arrow types, and on the sample found no
  type difference that `explain` hid.
- A case whose edition-2 text NEW cannot parse is refused when the GQT case
  loads, before any step runs. `run-generated` reports that as the refusal.
  EDITION-2's own runner cannot load a case whose text edition 2 cannot parse,
  so 19 of the 150 sampled cases did not bless; the CLI harness covers them.
- Index states covered are "none" and "after `index build`". The lagging,
  optimized and deleted states are the migration matrix's.
- Served (HTTP/MCP) paths and branches were not exercised.
- Generated ids differ between the two binaries' graphs. The classifier compares
  them as placeholders, so it cannot judge an order by a generated id; 3 checks
  are left for that reason.

## Reproduce

```bash
# binaries: build both commits with the CI recipe; see the table above
python3 harness/run.py --e2 BIN_E2 --new BIN_NEW --out DATA --seeds 1000:200000 --queries 24 --jobs 4 --budget 530
python3 harness/run.py --e2 BIN_E2 --new BIN_NEW --out DATA --seeds 500000:700000 --queries 24 --jobs 4 --budget 530
python3 harness/classify.py --data DATA --out CLS
python3 harness/report.py --data DATA --checks CLS/checks.jsonl --corpus DATA/corpus.jsonl --out results
python3 harness/corpus.py --base-ref 9ad7c8ef91594463636a3048ea20bbb557720cc6 --repo REPO --out DATA/corpus.jsonl
python3 repro/run.py                                   # every repro: EDITION-2, NEW, lint --fix, NEW on the fix
python3 harness/shrink.py --seed 1001 --qi 18 --predicate dnc --out /tmp/x
python3 ../scripts/cross-edition.py run-generated --edition-2-runner GQT_E2 --current-runner GQT_NEW \
    --cases repro/gqt --work /tmp/xe
```

The harness defaults to `/src/bin/e2-omnigraph`, `/src/bin/new-omnigraph` and
`/src/bin/e2-gqt`; `repro/run.py --e2 BIN --new BIN` overrides the CLIs.
