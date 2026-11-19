# GQ 3 migration matrix: existing graphs under the GQ 3 binary

Measured 2026-11-19. Every graph here was written by today's released code
(OLD) and then opened, read, written, maintained, served and rolled back with
the GQ 3 binary of draft PR ModernRelay/omnigraph#1316 (NEW). Synthetic data
only. Nothing here changes `main`, `gq3/phase-0-front-end`, the RFC or #1316.

| | binary | commit | `omnigraph version` |
|---|---|---|---|
| OLD | `main` of ModernRelay/omnigraph | `fd4d61f6e7871c752644de3e50462884b7b058b3` | 0.15.1, internal-schema 17 (serves v14–v17), http-contract 0.14 |
| NEW | `gq3/phase-0-front-end` of ragnorc/omnigraph, fixed at the start | `a1b89621a518695f58ab4828205b36ad124bde45` | 0.15.1, internal-schema 17 (serves v14–v17), http-contract 0.14 |
| RFC | `docs/rfcs/2026-11-07-gq-3.md` on `rfc/gq-3` | `c963461b66c99474e7c258a629cb1062ef9a4f82` | |

Both binaries were built once, each in its own worktree, with the shipped
recipe (`.github/workflows/build-release-archives.yml`: `cargo build
--release --locked -p omnigraph-cli -p omnigraph-server -p
omnigraph-azure-admission --features omnigraph-server/aws`, `RUSTFLAGS=""`).
Both print the same strings from `omnigraph version` and `/healthz`, so
neither tells an operator which language a binary speaks.

**Contents:** [Summary](#summary) · [How it ran](#how-it-ran) ·
[Differences](#differences) · [Rollback](#rollback) ·
[Defects](#defects-most-severe-first) · [Search timings](#search-timings) ·
[Serving](#serving) · [Not covered](#not-covered) ·
[Reproducing](#reproducing)

## Summary

All 112 graphs (2 schemas × 7 index states × 8 histories, every schema
feature in each) were built by OLD and run through NEW: per graph about
70 NEW operations, each followed by OLD's probes, and about 80 read
comparisons; plus a served cluster and search timings. No
NEW command panicked, lost data or changed a row the RFC does not name.

* **Reading an OLD graph** works on every graph: NEW opens it read-only and
  read-write; `snapshot`, `schema show`, branch and commit lists and
  `export` match OLD byte for byte, and historical and branch reads return
  OLD's rows. Of the catalogue's 55 edition-2 reads and searches 49 are
  refused as written, each with a
  printed fix; applying those fixes mechanically reproduces the hand
  respelling's answer in all 7,336 paired reads. Every row difference is
  one the Compatibility section lists (table rows 3–16), most of them
  OLD's index-state-dependent full-text answers becoming exact.
* **Writing** with NEW leaves graphs OLD reads; the write and load changes
  are the listed ones (keyed `insert` now fails on an existing key and
  `replace` means what it meant; `from:`/`to:`, `append`, `merge` refused).
* **One-way steps:** besides the listed ones (NEW's full-text rebuild,
  which OLD can itself undo by rebuilding, and `cosine`/analyzer schemas),
  three schema applies lock OLD out of the entire graph with no way back
  but the backup: an explicit `distance = "l2"`, which NEW plans as zero
  steps and documents as stored like an omitted one (defect 1); `@index` on
  a list (defect 2); and plain `@index(fulltext)`, the respelling the RFC
  itself prescribes (class b).
* **Serving:** a NEW server on an OLD cluster serves it, but makes every
  stored query in a file unavailable when one of them fails, with the
  wrong diagnostic (defect 3).
* **Tooling around the break:** NEW's `index build` tells operators of
  partially indexed OLD graphs to run the one-way full-text rebuild
  (defect 4); `fuzzy(f, q, n)` gets a wrong fix (defect 5); `lint --json`
  carries no fixes and `lint --fix` does not exist (defect 6).
* **Cost:** every lexical read is 2–10× slower at 100,000 rows (listed).


## How it ran

`harness/` holds everything; `results/` holds the generated tables, and
`results/raw/` the serving and timing records and NEW's refusal and
mechanical respelling of every catalogue declaration (`prep-*.json`). The
per-graph records (82 MB) are not committed; `ops.py run` regenerates them.

1. `build.py` builds every graph with OLD from three dimensions (below):
   `init`, `load --mode merge` of the synthetic rows (`gen.py`), the schema
   evolution, the history recipe, the index-state recipe, and `cleanup`
   last for the cleanup history. A proposal history runs its recipe after
   the index state, since an index pass publishes on `main` and would expire
   a pending proposal.
2. `ops.py prepare` runs each edition-2 declaration of the catalogue
   (`catalog.py`: 36 reads, 21 searches, 20 writes) through NEW once per
   schema: its refusal, and a mechanical respelling made by applying the
   byte-range edits NEW prints until the text compiles (what the RFC's
   `omnigraph lint --fix` is described to do; NEW ships no such flag, and
   `lint --json` drops the edits, so the edits are read from the refusal
   `query`/`mutate --json` returns). Every catalogue entry also carries a
   respelling written by hand from the RFC's respelling map.
3. `ops.py run` does, for every graph:
   * **read-only phase** on one copy whose files are digested before and
     after: `snapshot`, `schema show`, `branch list`, `commit list` per
     branch and `--unpublished`, `upgrade --check`, each with both binaries
     and compared; every catalogue read with OLD's edition-2 text against
     NEW's hand respelling and NEW's mechanical respelling, compared by
     p22's comparator (`scripts/cross-edition.py` `Observation` and
     `differences`, loaded from the NEW commit: column names, Arrow column
     types, rows as a multiset unless both sides order them, the error and
     its code), plus a positional comparison with floats within F32
     precision so that a renamed or re-typed score column is told apart
     from moved rows; the same reads at historical snapshots (first commit,
     a deleted branch's commit, a merge commit, pending proposals) and on
     every live branch; export with both binaries;
   * **write phase**, each operation on a fresh copy of the graph: the
     operation with NEW, then OLD's probes on the result (`snapshot`, six
     reads including full-text, `bm25`, `nearest` and `rrf`, `export`, a
     write, `index build`, `optimize`; and, when a full-text read fails,
     OLD's own `index rebuild --kind full-text` and the reads again),
     compared with the same probes on the untouched graph. The operations:
     a read-write open (a write that selects nothing); every write
     statement in its hand respelling, its mechanical respelling and the
     alternative statements (`insert`, `upsert`, `replace`, edition 2's
     `from:`/`to:`), each compared by NEW's export with OLD's edition-2
     write on another copy; `load` in every NEW mode and in OLD's `append`
     and `merge` on new keys, existing keys and an overwrite batch, each
     compared with OLD's counterpart mode; `load --branch --from`; eight
     schema applies (the same schema again, the evolution, `@index(fulltext)`,
     `@index(fulltext, analyzer = "english_v1")`, `distance = "cosine"`,
     an explicit `distance = "l2"`, `@index` on a list, an added type), each
     followed by NEW's `index build` and compared with OLD applying the same
     text; branch create, write, merge and delete; NEW merging each OLD branch
     into `main` (a NEW merge over an OLD base) compared with OLD's merge;
     NEW deleting each OLD branch; a branch from a deleted branch's commit;
     publishing and dropping OLD's proposals; `index build`,
     `index rebuild --kind full-text`, `optimize`, `cleanup --keep 1`,
     `upgrade`; then every search again on NEW's index pass;
   * **round trip**: OLD's export loaded into a graph NEW creates from the
     same `.pg`, NEW's export loaded into a graph OLD creates, all three
     exports compared.
4. `serve.py` builds an OLD cluster and walks it through NEW (see
   [Serving](#serving)); `timing.py` times search on larger corpora;
   `analyze.py` writes `results/`.

### Dimensions and the combinations that ran

| dimension | values |
|---|---|
| schema | `base`: one schema holding every feature the brief names (below); `evolved`: the same after OLD's `schema apply` of an added type, an added property, a renamed property (`@rename_from`) and a dropped one |
| index state | `none` (loaded, never indexed); `complete` (`index build`); `tail` (rows loaded after the build); `deletes` (rows deleted after the build: deletion files); `optimized` (build, tail, deletes, `optimize`); `fts_rebuilt` (build, then `index rebuild --kind full-text`, full-text and vector indexes built); `lagging` (build, then updates to indexed text and vectors) |
| history | `one` (one data commit); `many` (24 more commits); `branches` (a live branch, a deleted one, one deleted and recreated); `merge` (a merged and deleted branch, and a diverged live branch for NEW to merge); `fork_deleted` (a branch forked from a branch since deleted); `proposal` (a pending `as commit` mutation and a pending `merge --as-commit`); `tags` (the merge history plus a Lance native tag on `__manifest`, written with pylance 13.0.0, since the CLI has no tag command, and the engine's own merge-input tags); `cleanup` (12 more commits, a live branch, then `cleanup --keep 1`) |
| serving | one OLD cluster of two graphs with edition-2 stored queries and Cedar policies, booted by each binary with each boot setting (see [Serving](#serving)) |

Every schema feature is a property or type of the one schema, not an
alternative to another, so a graph carries all of them and every
(feature × index state × history) cell ran. The base schema
(`harness/gen.py`, `BASE_PG`) declares: every scalar type nullable and
required (`String`, `Bool`, `I32`, `I64`, `U32`, `U64`, `F32`, `F64`,
`Date`, `DateTime`, an enum); `[String]` and `[I32]?` lists; `Vector(8)`
with `@index`, a nullable vector and an `@embed` target (a declared
distance does not exist in edition 2; NEW's applies of `cosine` and an
explicit `l2` are in the write phase); a managed `Blob` and a nullable one;
`@key` single (`Person.email`), compound (`Pair`) and absent (`Loose`);
a keyed edge with `@card(0..1)` (`WorksAt`), unkeyed edges with parallel
edges and a self-loop (`Knows`), an edge `@unique(@src, @dst)` (`Cites`) and
an empty edge type (`Tagged`); `@unique`, `@range`; `@index` on a String
(edition 2's full text) and on `Bool`, `I64`, `F64`, `Date` and an enum;
`@key` with (`Doc.slug`) and without (`Person.email`) `@index`. Text values
draw on a vocabulary chosen to separate analyzers: letter case, stems,
stop words, NFC and NFD, hyphens and apostrophes.

The full product is 2 × 7 × 8 = 112 graphs, and all 112 were built by OLD and run through NEW with no harness error. The per-graph
record is `results/coverage.md`.

Skipped, and why:

* **an external Blob reference**: OLD admits one only through a server whose
  graph's `external_blobs` rule names an `s3://` or `az://` base (a
  server-safe base refuses `file://`, and direct CLI writes are deny-only);
  this box has no object store. Managed Blobs, empty and non-empty, ran.
* **serving × index state × history**: the cluster journey ran once, on a
  `base` graph with a complete index pass and a one-commit history; served
  graphs run the same engine as the standalone matrix, whose 112 graphs
  cover the storage side.
* **`@embed` with a text query**: needs an embedding provider; the vector
  side ran with explicit vectors.
* **S3, Azure, MCP, managed clusters**: not in this environment.

## Differences

Every difference between OLD and NEW the run found, grouped by kind. The
full generated table (84 signatures, each with the graphs it occurred on) is
`results/differences.md`, with examples in `results/differences.json`.
Classes: **(a)** intended and listed in the RFC's Compatibility and
reversibility section (bullet named); **(b)** intended by the RFC but not
listed there; **(c)** a defect.

Counts are graphs out of 112. "Mechanical" is the text made by applying
NEW's printed fixes; wherever it compiled it answered exactly as the hand
respelling did (7,336 paired reads).

| # | class | graphs | operation | OLD | NEW | RFC bullet |
|---|---|---|---|---|---|---|
| 1 | a | 112 | edition-2 text under NEW: 49 of 55 reads and 14 of 20 writes of the catalogue, as written | answers | refused before execution: `Q017` (a filter in `match`), `Q010` (property braces), `Q016` (missing commas), `Q012` (`not { }`), `Q008` (`update`/`delete T where`), `T65` (a ranking in `order`), `T11` (`from:`/`to:`), `T6` (`search`/`fuzzy`/`match_text`); each with a fix and byte-range edits, except three: `fuzzy(…, n)` (row 28), a stop-word query (row 21, refused only at run time) and `search` over a field with no index (`T27` in both editions) | Language |
| 2 | a | 112 | the 12 catalogue declarations whose edition-2 text also parses in GQ 3 (6 reads, 6 writes) | answers | same answers, except keyed inserts (row 9) | Language |
| 3 | a | 112 | `$s.i64 = 9007199254740992.0` | 1 row | 2 rows (`9007199254740993` compares equal as `F64`) | Results: integer vs whole-number float |
| 4 | a | 112 | `$a knows{1,2} $b` from a node with a self-loop | 3 rows, the start node included | 2 rows | Results: hop range and self-loop |
| 5 | a | 112 | `avg` / `sum` over `I64` extremes | `avg` 300240004308172.8 | 300240004308120.44 (exact) | Results: exact integer `avg` |
| 6 | a | 8 (evolved, optimized) | `limit 5` without `order` | the first rows of the compacted scan (`s00500`…) | the smallest identities (`s00000`…) | Results: `limit` tie rule |
| 7 | a | 112 | `bm25` score column | `float` (F32) | `double` (F64); values equal within F32 precision wherever OLD's index covered every row | Results: `bm25_v1` F64 |
| 8 | a | 112 | an unaliased `bm25` / `nearest` in `return` | `d._score` / `d._distance` | `_score` / `_distance` | Results (`_score`), respelling map (`_distance`) |
| 9 | a | 112 | keyed `insert` on an existing key (node, compound key, keyed edge, Blob row), edition-2 text unchanged | replaces the whole row | `key_conflict` naming `upsert` and `replace`, nothing written; `replace` leaves exactly OLD's graph | Writes and loads |
| 10 | a | 112 | `upsert` on an existing key (new statement) | — (edition 2's `insert` replaced) | keeps the omitted properties (`age`, Blob `ext`) | Writes and loads: new statements |
| 11 | a | 112 | `insert Knows { from: …, to: … }` | inserts | `T11`, fix `@src:`/`@dst:` | Writes and loads |
| 12 | a | 112 | `update` of an edge | `T16` | updates | Writes and loads: edge `update` is new |
| 13 | a | 112 | `load --mode append` / `merge` (CLI), `mode merge` (`load csv`), `"mode": "merge"` (HTTP) | loads | refused naming `insert` / `replace`; the CLI's refusal is a usage error, see defect 7 | Writes and loads |
| 14 | a | 112 | `load --mode upsert` (new mode) | — | sets named properties | Writes and loads |
| 15 | a | 112 | `search(…)` under `not` | `T38` | rows | Language: `T38` lifted |
| 16 | a | 64 (index states none, tail, deletes, optimized, lagging) | `bm25` scores and ranks, `fuzzy` and `rrf` rows | depend on index state: scores count deleted and superseded rows; rows written after the index build are missed (`fuzzy` finds 17 of 19 after updates; `fuzzy(…, 1)` finds 0 of 17 on a never-trained index); ranks at the `limit` boundary move | the exact answer, the same on every index state | Results: rows changed where edition 2's answer depended on index state |
| 17 | a | 112 | NEW `index build` on an OLD graph | — | builds BTREE, NGRAM and BITMAP indexes edition 2 never built; OLD's full-text indexes stay, unread | Schema: plain `@index` builds BTREE and NGRAM |
| 18 | a | 112 | stored edition-2 queries on a NEW server | served | unavailable (HTTP 409 `stored_query_unavailable` with the diagnostic), graph still served; strict boot blocks the graph | Language |
| 19 | a | — | NEW `cluster apply` / `cluster plan` of the edition-2 source | — | refused (`query_parse_error`) | Language |
| 20 | a | 112 | rollback after NEW's `index rebuild --kind full-text`, `distance = "cosine"`, `@index(fulltext, analyzer = …)` | — | OLD refuses (see [Rollback](#rollback)) | Reverting |
| 21 | b | 112 | a lexical query of stop words only (`"the"`) | 0 rows | `T20` refusal, at run time for a parameter value, with no fix | Section 4 "Empty text"; no Compatibility bullet |
| 22 | b | 112 | rollback after NEW applies plain `@index(fulltext)`, the respelling the RFC prescribes | — | OLD refuses every read, write, export and index pass on the graph, not only the index | Reverting names the index, not the graph; NEW's upgrade doc names only an analyzer or scorer |
| 23 | b | — | HTTP `POST /graphs/{id}/load` with no `mode` | response `"mode": "merge"` | `"mode": "replace"`, same contract version 0.14 | Wire says the contract does not change |
| 24 | c | 112 | NEW `schema apply` of an explicit `distance = "l2"` | — | plan 0 steps, apply publishes, OLD can no longer open the graph | defect 1 |
| 25 | c | 112 | NEW `schema apply` of `@index` on a list | — | OLD can no longer open the graph; parse error, no feature gate | defect 2 |
| 26 | c | — | a stored-query file holding one refused declaration | — | every query of the file unavailable, each with that declaration's diagnostic | defect 3 |
| 27 | c | 64 (index states none, tail, optimized, lagging) | NEW `index build` on an OLD graph whose full-text indexes do not cover every row | — | lists edition 2's full-text indexes as pending and prints `index rebuild --kind full-text`, the one-way step | defect 4 |
| 28 | c | 112 | `fuzzy($d.t, $q, 1)` under NEW | — | fix `match_terms(<field>, terms(<text>, mode: any, max_edits: 2))`, no edit | defect 5 |
| 29 | c | — | `lint --json` of edition-2 text | — | code and message only; no fix, no edit | defect 6 |
| 30 | c | — | `load --mode merge --json` | — | plain-text usage error, exit 64 | defect 7 |


What did **not** differ, on every graph: `snapshot`, `schema show`,
`branch list`, `commit list` (per branch and unpublished) and
`upgrade --check` answered byte-identical JSON; NEW's `export` equals OLD's;
OLD's export loads into a graph NEW creates and back with no row changed;
NEW's read-only commands changed no file; every read the RFC does not name
(traversal, multi-hop, undirected, edge bindings and parallel edges, blocks,
aggregates, `distinct`, compound keys, node objects, meta fields, vectors,
dates, `U64`, `limit` without `order` on these graphs, historical and branch
reads) returned the same columns, Arrow types and rows; every write the RFC
does not name left the same graph; NEW merging an OLD branch over an OLD base
left the same graph as OLD's merge; `load csv` `merge` (OLD) and `replace`
(NEW) left the same rows. Both binaries refused the same historical reads with the same message:
a snapshot whose versions `cleanup` reclaimed, and a deleted branch's
commit.

## Rollback

After each NEW operation OLD opened the result. "All probes ok" means
`snapshot`, six reads (scan, traversal, full text, `bm25`, `nearest`,
`rrf`), `export`, a write, `index build` and `optimize` all worked.

| NEW operation | graphs | OLD afterwards | class |
|---|---|---|---|
| every read-only command (`snapshot`, `schema show`, `branch list`, `commit list`, `upgrade --check`, every read, historical and branch reads, `export`) | 112 | unaffected: no file changed | — |
| a write that selects nothing (read-write open); `upgrade` (`already_current`) | 112 | unaffected: no file changed | — |
| every write statement (`insert`, `upsert`, `replace`, `update` of a node and an edge, `delete` of nodes and edges, multi-statement, Blob, scalar extremes) | 112 | all probes ok; OLD reads what NEW wrote, NEW's and OLD's exports agree | — |
| `load` in `insert`, `upsert`, `replace`, `overwrite`; `load --branch --from` | 112 | all probes ok | — |
| `branch create`, write, `branch merge`, `branch delete`; NEW merging an OLD branch; NEW deleting an OLD branch | 112 graphs; 98 (graph, OLD branch) pairs each | all probes ok | — |
| `commit publish` and `commit drop` of OLD's proposals | 14 | all probes ok | — |
| `index build` | 112 | all probes ok; OLD's next `index build` builds its full-text indexes beside NEW's BTREE/NGRAM | — |
| `optimize`, `cleanup --keep 1` | 112 | all probes ok | — |
| `schema apply`: the same schema again, the evolution (added type, added property, rename, drop; the 56 `base` graphs), an added type | 112 | all probes ok | — |
| `index rebuild --kind full-text` | 112 | `search`, `bm25` and `rrf` over a rebuilt field refused: `full-text index 'title_idx' requires rebuild: analyzer certificate payload is malformed`. Every other probe ok. OLD's own `index rebuild --kind full-text` restored its lexical reads on 112 of 112 graphs | a (Reverting), though the RFC's "restore the backup" is more than needed |
| `schema apply` `distance = "cosine"` | 112 | everything refused, `schema feature 'vector-distance' … is unknown to this build`; no OLD-side recovery | a (Reverting: vector distances) |
| `schema apply` `@index(fulltext, analyzer = "english_v1")` | 112 | everything refused, `schema feature 'full-text-analysis' …`; no OLD-side recovery | a (Reverting: the analyzer default; NEW's doc names it) |
| `schema apply` plain `@index(fulltext)` | 112 | everything refused, the same feature; no OLD-side recovery | b (row 22) |
| `schema apply` explicit `distance = "l2"` | 112 | everything refused with a parse error of the stored schema; no OLD-side recovery | c (defect 1) |
| `schema apply` `@index` on a list | 112 | everything refused with a parse error of the stored schema; no OLD-side recovery | c (defect 2) |
| NEW server on an OLD cluster (no apply) | 1 cluster | OLD server boots and serves everything afterwards | — |
| NEW `cluster apply` of the GQ 3 registry | 1 cluster | OLD server blocks the graph whose stored queries it cannot parse (`invalid_stored_queries`; `--require-all-graphs` exits); OLD's `cluster apply` of the edition-2 source restores it | a (Language) |

`schema apply` is main-only in both binaries, so on the 84 graphs with a
live branch other than `main` the harness deleted those branches with NEW
before the apply (OLD's side of the comparison did the same). Every one-way
step above is one the RFC lists except the rows marked b and c. The RFC's
summary says "an index the new binary builds is one-way"; only full-text
indexes are: OLD keeps reading after NEW builds BTREE, NGRAM and BITMAP indexes.


The full generated table is `results/rollback.md`.

## Defects, most severe first

Each has a script in `repro/repro.sh` (named in brackets) that builds the
smallest graph with OLD and runs NEW on it; `repro/output.txt` is one run.

### 1. An explicit `distance = "l2"` locks OLD out of the graph, through a zero-step apply [`l2_lockout`]

**Class c.** `Vector(2, distance = "l2")` on a graph whose vector was
`Vector(2)`: NEW's `schema plan` reports `"step_count": 0, "steps": []`,
`schema apply` reports `"applied": true, "step_count": 0` and publishes
(`graph_manifest_version` 2 → 3). From then on OLD refuses every command on
the graph, `snapshot` included:

```text
schema evolution is locked down in phase 1: schema-contract source is not a valid accepted
schema definition: parse error:  --> 1:36 | node Doc { slug: String @key  emb: Vector(2, distance = "l2") } …
expected core_type; manual coordination is required
```

NEW's own upgrade guide (`docs/user/queries/upgrading-to-gq3.md`, Schemas)
says "an explicit `l2` is stored as an omitted one" and lists only a non-`l2`
distance among the schemas older binaries refuse. 112 of 112 graphs. OLD
cannot recover; restoring the backup is the only way back, for a change the
plan described as nothing.

Cause: the `schema_contract` row stores the raw `.pg` source beside the IR
(`crates/omnigraph/src/db/schema_state.rs`, `SchemaContractRow { source, ir,
head }`), and OLD recompiles that source on every open
(`validate_schema_contract_row` → `compile_schema_source`). NEW records an
explicit `l2` as absent in the IR (`crates/omnigraph-compiler/src/catalog/schema_ir.rs`,
"records the distance l2, which accepted IR records as absent"), so no
feature name warns an older binary, and the plan compares IRs, so it reports
no step for a change it then publishes. Any GQ 3-only spelling that maps to
an edition-2 IR has the same effect.

### 2. `@index` on a list property locks OLD out of the graph with a parse error [`list_index_lockout`]

**Class c.** Section 4 adds LABEL_LIST indexes for lists, and NEW's
`schema apply` of `tags: [String] @index` succeeds (`add_constraint`
`Index`). OLD then refuses every command: `schema-contract source is not a
valid accepted schema definition: parse error: @index is not supported on
list property T.tags`. Unlike `@index(fulltext)` and non-`l2` distances, the
IR carries no feature name for it (`schema_ir.rs` declares
`full-text-analysis` and `vector-distance` only), so the refusal is an
unexplained parse error rather than "unknown to this build; upgrade", and
neither the RFC's Compatibility section nor NEW's upgrade guide lists it
among the one-way schema changes. 112 of 112 graphs; same cause as
defect 1.

### 3. One refused declaration makes every stored query of its file unavailable, with the wrong diagnostic [`stored_query_file`]

**Class c.** The RFC: "A served graph whose stored query fails to compile
stays served: that query alone is unavailable". NEW makes unavailable every
query in the `.gq` file that holds a refused declaration, each reporting the
first refusal of the file. In the serving journey 11 of 11 stored queries of
graph `kg` were unavailable, though `q_scalars_all` and `q_limit_no_order`
compile under NEW as written and `w_insert_new_key` compiles too (its file
mate `w_update_node` does not); all 11 carried `q_traverse`'s `Q010` at
line 3 (writes: `w_update_node`'s `Q008`). `queries list`, `queries
validate`, `GET /graphs`, the `/readyz` count and every invocation (HTTP
409) report it. An operator following the documented upgrade ("rewrite the
failing queries from their printed fixes") is told that queries which need
no rewrite fail, and is given another query's edits for them.

Cause: `QueryRegistry::load` in `crates/omnigraph-server/src/queries.rs`
parses `spec.source`, the whole file, once per query, and assigns the file's
parse error to each query of the file.

### 4. NEW's `index build` steers the operator into the one-way full-text rebuild [`index_build_advice`]

**Class c.** On an OLD graph whose full-text indexes do not cover every row
(index states none, tail, optimized, lagging: 64 of 112 graphs, i.e. any
graph written since its last index pass), NEW's first `index build` lists
OLD's full-text indexes under `pending_indexes` with `run omnigraph index
rebuild --kind full-text --target <target> --branch main`, including for
plain `@index` and `@key` Strings, which NEW says need no full-text index
and whose full-text index no NEW read uses. Following the advice is the one
step that makes OLD refuse lexical reads (see Rollback), and the rebuild's
own warning ("Full-text indexes were rebuilt with each property's analyzer
profile …") does not say older binaries will refuse them. A second NEW
`index build` no longer lists them, so the advice appears once. The pending
reasons come from the engine's index reconciliation (`omnigraph-engine`,
the `pending_indexes` of `index build`).

### 5. `fuzzy(field, q, n)` gets a wrong fix and no edit [`fuzzy_fix`]

**Class c.** The respelling map maps `fuzzy($x.t, $q, n)` to
`match_terms($x.t, terms($q, mode: any, max_edits: n))`. For the three-
argument call NEW prints `fix: match_terms(<field>, terms(<text>, mode: any,
max_edits: 2))` and no `suggestion`: a placeholder whose budget is 2 where
the source asked for 1, so a literal application widens the match.
`removed_lexical_call` in
`crates/omnigraph-compiler/src/query/check/mod.rs` recognises the budget
only as an option (`max_edits:`), not as edition 2's positional third
argument, and falls back to the placeholder with `max_edits: 2`. This is the
one catalogue entry whose mechanical respelling stops for want of an edit
(the other two stops are refusals both editions make or row 21).

### 6. `lint --json` drops the fix and edits; `lint --fix` does not exist [`lint_json_fix`]

**Class c.** The RFC (section 6) says `queries validate`, `cluster plan` and
`lint` report an edition-2 form "with that diagnostic", and its Compatibility
and "Stored sources at upgrade" sections name `omnigraph lint --fix` as what
applies the printed fixes. NEW's `lint --json` reports a parse refusal as
`{"severity", "code", "message"}` only, no `fix` and no `suggestion`, while
the same text run with `query --format json` carries both; and NEW has no
`--fix` flag (its upgrade guide no longer mentions one). The harness had to
read the edits from `query`/`mutate` refusals instead.

### 7. `load --mode append|merge --json` refuses with a plain-text usage error [`cli_mode_json`]

**Class c, minor.** With `--json`, the refusal is clap's text on stderr and
exit 64, with no JSON and no code, where `load csv … mode merge` returns the
coded `Q004` diagnostic with a machine-applicable edit and the HTTP route a
coded 400. A script that reads `--json` output gets nothing to parse.

### Undocumented breaks (class b), for completeness

* **A lexical query of stop words is a refusal** [`stopword`]: OLD answers
  `search($d.title, "the")` with 0 rows; NEW refuses `T20: the lexical query
  `the` has no searchable terms under its field's analyzer`, at run time,
  for a parameter value, with no fix. Section 4 states it ("Empty text: a
  query with no searchable terms is a type error"); the Compatibility
  section does not. A search box fed "the" or "a" now errors.
* **`@index(fulltext)` locks OLD out of the whole graph** [`fulltext_lockout`]:
  the RFC tells authors to respell a field that keeps full-text search as
  `@index(fulltext)`; applying it sets the `full-text-analysis` feature, and
  OLD then refuses every read, write, export and index pass, not only the
  index. The Reverting bullet speaks of a full-text index NEW builds, and
  NEW's guide lists only `@index(fulltext, analyzer = …)` or a scorer among
  the schemas older binaries refuse.
* **The HTTP load response's `mode`** [`http_load_mode`]: the same request
  (no `mode`) answers `"mode": "merge"` from OLD and `"mode": "replace"` from
  NEW, under the same contract version 0.14.

### RFC statements the binary does not meet, with no OLD/NEW difference

* "Keyed writes": "a repeated `upsert` or `replace` leaves no empty commit".
  NEW publishes a commit for each repeat (3 identical `replace`s, 3 commits;
  2 `upsert`s, 2 commits), as OLD did for a repeated keyed `insert`. NEW's
  user guide states NEW's behaviour.
* Reverting: "once one exists the supported rollback is restoring the
  backup". After NEW's `index rebuild --kind full-text`, OLD's own
  `index rebuild --kind full-text` restored its lexical reads on 112 of 112
  graphs; a backup is needed only after the schema changes above.
* Summary: "an index the new binary builds is one-way": only full-text
  indexes are.
* NEW and OLD print identical `version` output (0.15.1, internal-schema 17,
  http-contract 0.14).


## Search timings

`timing.py` (`results/timing.jsonl`, `results/timing.md`). Corpora of
20,000 and 100,000 `Doc` rows (titles of 2–6 words, bodies of 8–24, from the
same vocabulary; 8-dimensional vectors), loaded and indexed by OLD
(`index build`: full-text, vector and scalar indexes; 0.3 s and 0.9 s). Each
query ran 5 times per side, the sides interleaved, every invocation a fresh
process timed by wall clock and by `--stats` (store requests are GETs and
HEADs of the manifest and table planes; `--stats` reports no bytes). "NEW
after NEW index build" is NEW on a copy after its own `index build`. The box
ran nothing else.

| corpus | query | OLD median s | NEW median s | NEW after NEW index build median s | NEW/OLD | OLD rows | NEW rows | rows differ | OLD store reads | NEW store reads | OLD peak RSS MB | NEW peak RSS MB |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 20000 | s_search_title | 0.081 | 0.114 | 0.111 | 1.4 | 6032 | 6032 | no | 7 | 5 | 291 | 292 |
| 20000 | s_match_text_body | 0.091 | 0.229 | 0.234 | 2.5 | 10130 | 10130 | no | 7 | 5 | 297 | 293 |
| 20000 | s_fuzzy_default | 0.078 | 0.124 | 0.121 | 1.6 | 4234 | 4234 | no | 7 | 5 | 289 | 292 |
| 20000 | s_bm25_title | 0.071 | 0.149 | 0.143 | 2.1 | 10 | 10 | types; rows | 7 | 5 | 291 | 293 |
| 20000 | s_bm25_filtered | 0.079 | 0.325 | 0.324 | 4.1 | 10 | 10 | types; rows | 7 | 5 | 295 | 297 |
| 20000 | s_nearest | 0.065 | 0.067 | 0.065 | 1.0 | 10 | 10 | no | 8 | 8 | 286 | 289 |
| 20000 | s_rrf | 0.115 | 0.182 | 0.185 | 1.6 | 10 | 10 | no | 10 | 8 | 308 | 301 |
| 100000 | s_search_title | 0.169 | 0.393 | 0.41 | 2.3 | 30467 | 30467 | no | 7 | 5 | 312 | 304 |
| 100000 | s_match_text_body | 0.328 | 1.077 | 1.155 | 3.3 | 50568 | 50568 | no | 7 | 5 | 372 | 307 |
| 100000 | s_fuzzy_default | 0.144 | 0.438 | 0.446 | 3.0 | 21373 | 21373 | no | 7 | 5 | 311 | 302 |
| 100000 | s_bm25_title | 0.131 | 0.553 | 0.544 | 4.2 | 10 | 10 | types; rows | 7 | 5 | 326 | 304 |
| 100000 | s_bm25_filtered | 0.145 | 1.376 | 1.399 | 9.5 | 10 | 10 | types; rows | 7 | 5 | 333 | 308 |
| 100000 | s_nearest | 0.079 | 0.086 | 0.079 | 1.1 | 10 | 10 | no | 8 | 8 | 292 | 296 |
| 100000 | s_rrf | 0.282 | 0.619 | 0.642 | 2.2 | 10 | 10 | no | 10 | 8 | 438 | 333 |

Rows are equal on every query; the `bm25` difference is the score type
(`float` → `double`) with the same identities, order and values within F32
precision. NEW is 1.4–4.1× slower at 20,000 rows and 2.2–9.5× at 100,000 on
every lexical query (`match_terms`, `bm25`, and `rrf` through its `bm25`
arm), growing with the corpus, and NEW's own index pass does not change it;
`nearest` is unchanged. This is the cost the RFC's Cost bullet lists (an
exact scan of the field on every call until stage L, class a). NEW issues
fewer store requests (5 against 7) because it reads the column instead of
the index; the elapsed time is the scan.


## Serving

`serve.py` (`results/serve.jsonl`). One OLD cluster: graphs `kg` and `kg2`
with the base schema and data, an OLD index pass, Cedar policies for the
graphs and the cluster scope, bearer-token auth. `kg` stores edition-2
queries in two files (`reads.gq`: 9 reads and searches; `writes.gq`: 2
writes); `kg2` stores 3 reads whose edition-2 text also parses in GQ 3.

| step | result |
|---|---|
| OLD server, default and `--require-all-graphs` | both graphs ready, every stored query answers |
| NEW `queries validate` on the OLD root | exit 1, 11 breakages for `kg` (all of them, see defect 3), each with code, position, fix and edits; `kg2` ok |
| NEW `queries list --graph kg` | 0 queries, 11 unavailable, each with a diagnostic |
| NEW `cluster validate` / `cluster plan` of the edition-2 source | refused, `query_parse_error` with the diagnostic and its `further` refusals |
| NEW server, default boot | serving; `/readyz` `ready: true`, `"unavailable_query_count": 11`; `GET /graphs` lists the unavailable queries with diagnostics; every `kg` stored query answers 409 `stored_query_unavailable`; inline GQ 3 reads answer on both graphs; `kg2`'s stored reads answer, rows equal to OLD's except `avg` (row 5); the boot log prints each diagnostic with its edits |
| NEW server, `OMNIGRAPH_STRICT_STORED_QUERIES=1` | `kg` blocked (`invalid_stored_queries`, every route 503 `graph_unavailable`), `kg2` served; `/readyz` `degraded` |
| NEW server, strict and `--require-all-graphs` | refuses to start |
| NEW server, default and `--require-all-graphs` | starts and serves, as default (unavailable queries do not count as a failed graph) |
| OLD server after NEW's servers ran | boots and serves everything: NEW's servers wrote nothing OLD refuses |
| NEW `cluster apply` of the edition-2 source | refused, nothing written |
| the documented upgrade: GQ 3 source, NEW `cluster validate`, `cluster plan`, direct `cluster apply`, NEW server | every stored query served; 7 of 9 reads answer exactly as OLD did before the upgrade, `s_bm25_title` differs as row 7 says and `q_not_block` has one more row, the person OLD's own `w_insert_new_key` invocation added between the two probes; `w_insert_new_key` (now a strict `insert`) answers 409 `conflict` because OLD's earlier invocation created the key |
| OLD after NEW's apply | `queries validate` reports every `kg` query broken (`Q001 expected clause`); the OLD server blocks `kg` and serves `kg2`; with `--require-all-graphs` it exits |
| OLD `cluster apply` of the edition-2 source, then OLD server | everything served again: the registry switch is reversible by re-applying the old source |
| managed Blob through `GET /graphs/kg/blob` and `blob stat` | same bytes and ETag from both binaries wherever `kg` was served (503 where strict boot or OLD after NEW's apply blocked it) |
| `access`, `cluster status` | NEW reads OLD's policies and ledger unchanged |


## Not covered

* External Blob references, `@embed` text queries, S3/Azure, MCP and
  managed clusters (see [How it ran](#how-it-ran)).
* `as unattached branch` snapshots and `on` chains: not in the brief's
  history list.
* The serving dimension ran on one graph shape, not crossed with index state
  and history.
* Timings are from one 32-core box with other work stopped; they are
  evidence, not a benchmark (see [Search timings](#search-timings)).
* The respelling map's structural rows that this catalogue does not
  exercise: a triple written against its edge's direction (`T82`), two
  unconnected components (`T81`), a fusion over two bindings (`T75`), a
  ranking under an aggregate.


## Reproducing

```bash
# the two binaries, built as above, and pylance 13.0.0 + pyarrow in a venv
export MM_WORK=/path/to/work MM_OLD=.../old/omnigraph MM_NEW=.../new/omnigraph \
       MM_OLD_SERVER=.../old/omnigraph-server MM_NEW_SERVER=.../new/omnigraph-server \
       MM_P22=/path/to/gq3-checkout/scripts/cross-edition.py RUST_MIN_STACK=16777216
cd migration-matrix/harness
MM_JOBS=8 python3 build.py "$MM_WORK/graphs"          # 112 graphs with OLD, ~1 min
python3 ops.py prepare "$MM_WORK/graphs"               # NEW's refusals and respellings
MM_JOBS=24 python3 ops.py run "$MM_WORK/graphs"        # ~70 min at 24 workers on 32 cores
python3 serve.py                                       # the cluster journey, ~1 min
python3 timing.py 20000 100000                         # search timings
python3 analyze.py                                     # results/*.md
OLD=... NEW=... OLD_SERVER=... NEW_SERVER=... bash ../repro/repro.sh   # every (b) and (c)
```

`build.py` expects the pylance venv at `/src/mm/venv` for the tag history
(edit `h_tags` to point elsewhere). `repro/output.txt` is one run of
`repro.sh`.
