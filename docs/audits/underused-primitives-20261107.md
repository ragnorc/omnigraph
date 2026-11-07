# Audit: Lance, DataFusion and Arrow primitives Omnigraph underuses — indexes and search

**Date:** 2026-11-07 (UTC)
**Omnigraph commit:** `2d8effabe1900f63c30ecbf918c8fcfe3080f025` (main)
**Pinned libraries (Cargo.lock):** lance 13.0.0 (all lance-* crates), datafusion 54.0.0, arrow 58.4.1
**Focus:** indexes and search (scalar indexes and keyed reads; vector and full-text search; index build and upkeep)
**Previous run:** 2026-11-04, whole engine, `docs/audits/underused-primitives-20261104.md` on branch
`audit/underused-primitives-20261104`. Its findings keep their IDs (L1 to L14, D1 to D4, A1 to A5).
**Status:** read-only audit. Nothing here is a decision. Maintainers triage each issue.

No focus was named for this run. The previous run covered the whole engine and asked for one
area per run, so this run took indexes and search. Upstream source was read from the crates.io
sources at the pinned versions.

## Summary

Seven new findings. Five are filed as issues (two small ones share an issue). Two more are new
evidence on existing issues, so they went there as comments. None asks to undo a fence in
`docs/dev/lance.md`.

Most predicates already reach Lance's scalar-index planner in a shape it can use: `=`, ranges,
`IN`, `IS NULL` and `starts_with` all become BTREE queries. The gaps are about cost and about
bounds, not about which predicates are pushed down.

Top three:

1. **L-index-tail-cost.** The planner chooses between an id lookup and a hash join without
   looking at index coverage. Lance answers covered fragments from the index and scans only the
   uncovered tail, but each 256-id slice of an id lookup is its own Lance read and scans that tail
   again. Without a BTREE, each slice reads the whole `id` column. Size M.
2. **L-merge-delete-rowid.** Branch-merge deletes find their rows with `id IN (...)`, although the
   merge walk already read each target row's `_rowid`. Lance turns `_rowid IN (...)` into a direct
   take with no index and no scan. Size S to M.
3. **L-fts-build-bounds.** Full-text index builds use Lance's default workers and per-worker memory
   (2 GiB each, about half the CPUs). No Omnigraph budget covers them. Size S.

All findings come from reading code on both sides, except the `fuzzy()` evidence, which was run.

## Prior findings since the last run

The 2026-11-04 run audited `916e53a3` and filed #1247 to #1264. Main has 19 commits since its
recheck at `e750a6be`. Their state on 2026-11-07:

| Finding | Issue | State | What changed |
|---|---|---|---|
| A3 bounded edge expand row sizing | #1251 | closed | Fixed by #1268 (`WorkMemory::row_bytes` per batch). |
| Hygiene: dead strict-insert arm, stale wording | #1261 | closed | Fixed by #1265. |
| L4 cleanup HEAD before each delete | #1252 | open, partly done | #1266 passes the listed size, so the HEAD is gone. Deletes are still one at a time. |
| Small items (L11, A5 and three more) | #1262 | open, partly done | #1267 did L11 (Lance's detached-manifest parser) and A5 (borrowed JSONL strings). |
| L14 merge across `optimize` | #1258 | open | #1229 merged the lineage-candidate delta it follows on. |
| A1, L2, D1, L1, A2, L5, L6, L9, L8, L10, D3, D4, L13; L7 on #1095 | #1247-#1250, #1253-#1257, #1259, #1260, #1263, #1264, #1095 | open | No change in the audited code. |

Two other commits touch this area. #1286 splits pushed filters that test one nullable column
twice, because Lance 13's DataFusion `ExprSimplifier` folds them and loses nulls. #1231 makes a
write's committed lookup choose probe or scan by BTREE coverage, and counts partial coverage as
absent. Neither is a finding here; L-index-tail-cost is the read-path counterpart of #1231.

## Primitive maps

Status words: **used**, **used partly**, **reimplemented**, **worked around** (deliberate,
cited), **not used**. `engine-src/` is `crates/omnigraph/src/`. Only primitives that bear on
indexes and search are listed.

### Lance 13.0.0

| Primitive | Status | Omnigraph references | Note |
|---|---|---|---|
| BTREE build (`create_index_builder`, `ScalarIndexParams::default`) | used | `engine-src/table_store.rs:3201-3203, 5608-5612` | Whole rebuild (fence "Index segments"). |
| `load_indices`, `IndexDetails` kind | used | `engine-src/engine/plan_source/indexes.rs:14-80`, `crates/omnigraph-core/src/dataset_index.rs:105-268` | Kind read from the type URL suffix. |
| `unindexed_fragments` / `indexed_fragments` | reimplemented three times | `plan_source/indexes.rs:86-118`, `dataset_index.rs:132-212`, `engine-src/table_store.rs:3073-3110` | The copies disagree. See L-coverage-one-source. |
| Index-aware filter split (`ScalarIndexExec`, `FilteredReadExec`, covered plus uncovered tail) | used by Lance, not priced by the planner | `engine-src/engine/plan_source/scan_access.rs:23-176`; `crates/omnigraph-planner/src/cost.rs:116-130, 231-234` | See L-index-tail-cost. |
| `use_scalar_index`, `prefilter` | used | `engine-src/engine/scan.rs:261-268` | Fence "Full-text eligibility placement". |
| BTREE `IsIn`, `IsNull`, `LikePrefix`, ranges through `Expr` | used | `engine-src/engine/scan.rs:1235-1355` | All pushed shapes can use the index. |
| `with_fragments` | used | `engine-src/ordered_cursor.rs:211, 501`; `engine-src/table_store.rs:835` | |
| `_rowid` / `_rowaddr IN` take shortcut | used partly | `engine-src/ordered_cursor.rs:495`, `engine-src/changes/row_identity.rs:191`; not `engine-src/exec/merge.rs:4462` | See L-merge-delete-rowid. |
| `take_rows` | used; one row per call at two sites | `engine-src/table_store.rs:4184, 4280` | See L-blob-take-rows. |
| `RowAddrTreeMap` delete by address | used for mutations only | `engine-src/exec/staging.rs:995-1007` | L9 (#1256) known. |
| `with_row_addr_prefilter` | not used | `engine-src/engine/scan.rs:22-29` | L6 (#1255) known. |
| Filtered `CountPushdown` | not used | none | L5 (#1254) known. |
| NGram, LabelList, Bitmap, ZoneMap, Bloom filter | not used | none | L10 (#1259) known. |
| `nearest`, `use_index(false)`, `maximum_nprobes`, `scan_stats_callback` | used | `engine-src/engine/scan.rs:400-420` | Fence "KNN probe budget". |
| `distance_metric` on the query | not used | `engine-src/engine/scan.rs:402-413` | See L-vector-metric-pin. |
| `VectorIndexDetails` (metric, compression) | not used | `plan_source/indexes.rs:66-84`, `engine-src/table_store.rs:3052-3071` | See L-vector-metric-pin. |
| IVF_FLAT build | used, one partition | `engine-src/table_store.rs:5631`, `crates/omnigraph/tests/forbidden_apis.rs:4240` | #485, #486. |
| IVF_PQ, IVF_SQ, RQ, HNSW, `refine_factor`, `ef` | not used (deliberate) | `forbidden_apis.rs:4235-4273` | The exactness promise rests on IVF_FLAT. |
| `FullTextSearchQuery`, `MatchQuery`, `BooleanQuery` (Must) | used | `engine-src/engine/scan.rs:217, 700-713, 928` | |
| `new_fuzzy`, `max_expansions`, `prefix_length` | used partly | `engine-src/engine/scan.rs:946-948` | #748, #749, RFC #792. New evidence below. |
| `PhraseQuery`, `Boost`, `MultiMatch`, `Operator::And` | not used | none | No query feature needs them; indexes are built without positions. |
| Full-text `limit` and block-max WAND | not used (deliberate, RFC 0047) | `engine-src/engine/scan.rs:217-219` | Covered by RFC 0048 (#793). Evidence below. |
| `create_index_builder(..).execute_uncommitted`, `replace`, `name`, `train(false)` | used | `engine-src/table_store.rs:3190-3207, 4437-4443, 5605-5636` | |
| `InvertedIndexParams::memory_limit_mb`, `num_workers`; `CreateIndexBuilder::progress` | not used | `engine-src/table_store.rs:4437, 5622` | See L-fts-build-bounds. |
| `optimize_indices`, delta segments | worked around | `engine-src/table_store.rs:3116-3215` | Fence "Index segments". |
| `index_statistics` | used once | `engine-src/table_store.rs:3064` | Partition count; errors fall back to 1. |
| Session index and metadata caches | used at Lance defaults | `crates/omnigraph-core/src/lance_access.rs:59-64` | #702. |

### DataFusion 54.0.0

| Primitive | Status | Omnigraph references | Note |
|---|---|---|---|
| `ExprSimplifier` inside Lance's filter preparation | worked around | `engine-src/engine/scan.rs` (null split, #1286) | Fence "Pushed filter simplification". |
| Rank fusion | reimplemented (no upstream equivalent) | `engine-src/engine/operators/rank_fuse.rs` | Kept. |
| `SortExec` top-k for ranked reads | used through Lance's full-text plan | Lance `scanner.rs:5217` | Omnigraph passes no limit; see the RFC 0048 note. |

### Arrow 58.4.1

No Arrow primitive bears on this area beyond those mapped in the previous run. Distance math runs
inside Lance (`lance-linalg`); Omnigraph computes no distances itself.

## Ranked findings

Ranked by: a guarantee or bound first, then storage calls, then CPU, each weighted by how common
the path is. IDs: L = Lance, D = DataFusion, A = Arrow, plus a short slug that stays stable
across runs.

### 1. L-index-tail-cost: access paths are chosen without index coverage, and each id-lookup slice rescans the uncovered tail — M

**Issue:** #TBD-tail

- **Now.** `choose_access_path` picks an id lookup or a hash join from row counts and build bytes
  only (`crates/omnigraph-planner/src/cost.rs:116-130`). The scan-access pass records whether an
  `__id` BTREE exists afterwards, and only explain shows it
  (`crates/omnigraph-planner/src/scan_access.rs:42-51`). The index-facts RFC says so: "today that
  scan depends silently on the BTREE existing"
  (`docs/rfcs/2026-10-08-index-facts-as-planner-input.md:437-444`).
- An id lookup resolves each input batch in slices of at most 256 ids, one Lance read per slice
  (`engine-src/engine/operators/scan/input.rs:83-101, 287-316`). Each read answers covered
  fragments from the BTREE and scans the `id` column of every uncovered fragment. With no BTREE,
  every slice reads the whole `id` column.
- The expand cost model prices anything short of full coverage as a full edge scan on every hop
  (`cost.rs:231-234`).
- Partial coverage is the normal state between maintenance runs: ordinary writes do not build the
  BTREEs (`engine-src/db/omnigraph/table_ops.rs:441, 461`; #246).
- **Upstream.** Lance splits a filtered read into covered and uncovered fragments and scans only the
  uncovered ones with the filter (`lance-13.0.0/src/dataset/scanner.rs:7022-7180`,
  `io/exec/filtered_read.rs:951`). `unindexed_fragments(name)` returns the tail
  (`lance-13.0.0/src/index.rs:3967-3981`).
- **Why it is this way.** The index-facts RFC made coverage a plan input with two states (indexed
  or degraded) and left access-path costing open. No fence or invariant requires it. Rows returned
  do not depend on it (invariant 7).
- **Gain.** Price an access path as probe cost plus uncovered rows. A lookup over a table with a
  large tail, or with no BTREE, would switch to one scan and a hash join instead of one tail scan
  per 256 ids. The uncovered row count comes from pinned fragment metadata, so the plan can record
  it (invariant 9).
- **Cost and risk.** Cost-model change, plan mirror, explain and GQT plan cases. Results do not
  change.
- **Evidence.** Inspected. The gain grows with the tail size; the DST tables are too small to show
  it.

### 2. L-merge-delete-rowid: branch-merge deletes search by `id IN (...)` although the walk holds `_rowid` — S to M

**Issue:** #TBD-rowid

- **Now.** `commit_staged_delete_chunks` stages each chunk as `id IN (ids)` through Lance's delete
  (`engine-src/exec/merge.rs:4454-4478`; ids collected near `:1988`). Each chunk searches the
  BTREE and scans the uncovered tail's `id` column, or the whole column with no index. The target
  rows the three-way walk reads already carry `_rowid` and `_rowaddr`
  (`engine-src/ordered_cursor.rs:500-509`). Mutation deletes already delete by captured address
  (`engine-src/exec/staging.rs:995-1007`).
- **Upstream.** `DeleteJob` runs its filter through the scanner with `with_row_id`
  (`lance-13.0.0/src/dataset/write/delete.rs:269-330`). The scanner turns `_rowid IN (...)` into a
  direct take, with no index and no scan (`TakeOperation::try_from_expr`,
  `lance-13.0.0/src/dataset/scanner.rs:1337-1427, 3333-3345`), and maps row ids to addresses with
  the row-id index.
- **Why it is safe.** Graph tables use stable row ids. The rows being deleted are not updated in
  the same merge. The change feed reads deleted ids from `record_deleted_ids`, not from the
  predicate (`engine-src/changes/candidate_scan.rs:253-262`).
- **Gain.** A delete chunk reads no data columns. The per-chunk byte plan becomes a count of
  integers instead of SQL text.
- **Risk.** The `_rowid` must come from the target-side read: sibling detached commits can hand
  out the same new row ids, so ids from the base or source side are unsafe. Pin the upstream
  shortcut with a `lance_surface_guards` test before relying on it.
- **Evidence.** Inspected.

### 3. L-fts-build-bounds: full-text index builds run at Lance's default workers and memory — S

**Issue:** #TBD-fts

- **Now.** Every full-text build uses `InvertedIndexParams::default()`
  (`engine-src/table_store.rs:5622` for full builds, `:4437` for untrained ones). It sets neither
  `memory_limit_mb` nor `num_workers`, and passes no progress callback.
- **Upstream.** The defaults are `LANCE_FTS_NUM_SHARDS` workers (about half the compute CPUs) and
  `LANCE_FTS_PARTITION_SIZE` = 2048 MiB per worker
  (`lance-index-13.0.0/src/scalar/inverted/builder.rs:48-96`). `memory_limit_mb` is a total that
  Lance divides across workers (`builder.rs:91-96`). The params and `CreateIndexBuilder::progress`
  are public (`lance-index-13.0.0/src/scalar/inverted/tokenizer.rs:199-212`,
  `lance-13.0.0/src/index/create.rs:145`).
- **Why it is this way.** No comment or history explains it. Omnigraph's only memory setting covers
  queries. `memory_limit_mb` is not serialized with the index, so the RFC 0043 analyzer certificate
  is untouched.
- **Gain.** A declared memory bound for `index build`, `optimize` and full-text rebuilds
  (invariant 11: resource use is bounded and observable), and build progress for operators. On a 16-vCPU
  host the default can hold about 16 GiB of postings, which no budget sees.
- **Cost and risk.** A few lines and a setting. A lower limit changes the number of index
  partitions; show BM25 scores equal before and after in the search tests.
- **Evidence.** Inspected.

### 4. L-vector-metric-pin: the L2 and exhaustive-search promises depend on who built the index — S

**Issue:** #TBD-metric

- **Now.** `nearest` never sets `distance_metric` (`engine-src/engine/scan.rs:402-413`). The index
  facts classify any vector index as vector without reading its details
  (`engine-src/engine/plan_source/indexes.rs:66-84`). The fold rebuilds a lagging vector index as
  `ivf_flat(partitions, L2)` whatever it was (`engine-src/table_store.rs:3193`), with the partition
  count parsed from `index_statistics` JSON and errors turned into 1 (`:3052-3071`).
- **Upstream.** With no metric on the query, Lance uses the index's metric; the flat path and
  `use_index(false)` default to L2 (`lance-13.0.0/src/dataset/scanner.rs:6203-6233, 6294,
  6355-6360`). With `distance_metric` set (`:2229`), Lance skips an index whose metric differs and
  searches flat. The index's metric and compression are in `VectorIndexDetails`
  (`lance-index-13.0.0/protos/index.proto:191`).
- **Why it is this way.** RFC 0047 and `forbidden_apis.rs` (`vector_indexes_are_ivf_flat`) rest
  exactness on "every index the engine builds is IVF_FLAT". That checks the engine's constructors,
  not the index on disk.
- **Gain.** An index Omnigraph did not build (a patched fork, a tool writing to the same table)
  cannot make the indexed pass and the flat rescan rank by different metrics in one query
  (invariant 7), and "every partition searched" cannot claim exactness over a compressed index.
- **Cost and risk.** Small. Likelihood is low; this is defense in depth.
- **Evidence.** Inspected.

### 5. Small items — S each

**Issue:** #TBD-small (both items)

- **L-coverage-one-source.** Three coverage calculations disagree.
  `key_column_index_coverage` takes the first BTREE segment it finds (`.find`) and treats an
  unknown bitmap as covered (`crates/omnigraph-core/src/dataset_index.rs:132-183`). The planner's
  index facts union all segments and treat unknown as not covered
  (`engine-src/engine/plan_source/indexes.rs:86-118`). `has_unindexed_fragments` checks each
  segment alone (`dataset_index.rs:196-212`). The runtime check decides bound-edge pushdown when a
  saved plan carries no coverage (`engine-src/engine/graph.rs:823-831, 1858-1870`). Lance's
  `unindexed_fragments` already unions the segments (`lance-13.0.0/src/index.rs:3967-3981`). Gain:
  one definition, matching the plan. Cost decisions only; results do not change.
- **L-blob-take-rows.** Two proven-insert Blob paths call `take_rows(&[row_id], full schema)` once
  per row (`engine-src/table_store.rs:4184` for payloads, `:4280` for descriptor selection, which
  needs only descriptors). `Dataset::take_rows(&ids, projection)` batches the read. Payloads can
  still be materialized one row at a time to keep the per-row bound. The update path at
  `table_store.rs:6075` already batches its take.

## New evidence on existing issues

- **`fuzzy()` expansion cap (#748).** Comment: #TBD-c748. `build_fts_query` sets neither
  `max_expansions` nor `prefix_length` (`engine-src/engine/scan.rs:946-948`). Lance's 50-term
  expansion budget is shared across the query's words
  (`lance-index-13.0.0/src/scalar/inverted.rs:118-175`). Measured with a scratch `.gqt` on
  `omnigraph-engine`, local filesystem: with 76 indexed titles one edit from `kat` and one titled
  `qqq`, `fuzzy(title, "kat qqq", 1)` returns exactly 50 rows and misses `qqq`; `fuzzy(title,
  "qqq", 1)` finds it.
- **BM25 never passes its limit to Lance (RFC 0048, #793).** Comment: #TBD-c793. The ranking query
  is `FullTextSearchQuery::new(text).with_column(prop)` with no limit
  (`engine-src/engine/scan.rs:217-219`), so Lance scores and sorts every match and fetches each
  match's `id`. Lance takes a limit from the query or the scanner
  (`lance-13.0.0/src/dataset/scanner.rs:4799-4813`) and uses it for block-max WAND pruning
  (`lance-index-13.0.0/src/scalar/inverted/wand.rs:2671`). This is deliberate under RFC 0047
  ("BM25 supplies its full matching population"), and RFC 0048 already discusses caps, so it was
  not filed. A possible window that keeps the contract: request limit + 1 and fall back to the full
  scan on a tie at the boundary or a short result, only for queries where nothing above the scan
  drops rows.

## Deliberate workarounds kept

These look like underused primitives and are not. Do not undo them.

- **Whole index rebuilds, no `optimize_indices` delta segments.** Fence "Index segments"
  (lance#9817).
- **ANN probe ladder, no `fast_search`.** Fence "KNN probe budget"; invariant 7.
- **Full-text filters placed as prefilters only under full coverage.** Fence "Full-text eligibility
  placement".
- **Analyzer certificates on every full-text segment.** Fence "Full-text analyzer"; RFC 0043.
- **IVF_FLAT only.** The exactness promise of RFC 0047; `forbidden_apis.rs`.
- **Null split of pushed filters.** Fence "Pushed filter simplification" (#1286).
- **Partial coverage counts as absent for write-side lookups.** #1231 chose it to bound memory;
  L-index-tail-cost is about reads and does not undo it.
- **Omnigraph's own coverage code.** It must refuse on unknown coverage, which Lance's helper
  errors on; L-coverage-one-source asks for one copy, not for Lance's.

## Dropped after reading both sides

- Count-first endpoint validation through `CountPushdown`: it relies on `id` being unique, which
  would weaken an integrity check.
- Index-only existence probes projecting only `_rowid`: they win only when a conflict exists.
- Cast-wrapped comparisons: Lance's simplifier already unwraps integer widening
  (`scalar_index_coercion.gqt`).
- Row-address prefilter for the search gates: the gates' candidates are id strings from the CSR;
  L6 covers it.
- `use_stats` and materialization style: Lance's defaults are right for the V2 file format.
- One-partition IVF_FLAT and session cache sizes: #485, #486 and #702.

## Upstream changes since the pins

- **Lance:** 14.0.2 is still the latest stable; 14.0.3 is a release candidate and 15.0.0 is at
  beta 13. The previous run's notes stand: at the 15 bump, recheck the "Index segments" and
  "Ordered walks" fences and L5, and look at `ScalarIndex::lookup` for the `id IN` lookups in
  L-index-tail-cost and L-merge-delete-rowid.
- **DataFusion:** no new release assessed; the pin is unchanged at 54.0.0.
- **Arrow:** the pin moved to 58.4.1, a patch release. No verdict changes.

## Could not verify

- How large uncovered tails get in served graphs between maintenance runs (L-index-tail-cost).
- That Lance's `DeleteBuilder` takes the `_rowid IN` shortcut at run time; read in source, not run
  (L-merge-delete-rowid).
- Peak memory of a default full-text build on a large table (L-fts-build-bounds).
- That a limited BM25 scan gives bit-identical `_score` values to the unlimited one (#793 note).

## Workflow feedback

- One focus per run kept the audit readable. The definition could list the focus rotation so
  unattended runs do not pick one ad hoc.
- The DST storage-cost golden cannot show the effects in this area, because its tables are too
  small to have an uncovered tail. A large-table instrument for index coverage would let the next
  run measure L-index-tail-cost.
