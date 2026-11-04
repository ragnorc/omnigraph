# Audit: Lance, DataFusion and Arrow primitives Omnigraph underuses

**Date:** 2026-11-04 (UTC)
**Omnigraph commit:** `916e53a3692123c4fb6c8b44b06641d4b51244ae` (main)
**Pinned libraries (Cargo.lock):** lance 13.0.0 (all lance-* crates), datafusion 54.0.0, arrow 58.3.0, object_store 0.14.2
**Focus:** the whole engine
**Status:** read-only audit. Nothing here is a decision; each finding needs an owner to accept or reject it.

Upstream source was read from the crates.io sources at the pinned versions. Release notes and
issue states were read from GitHub on the audit date.

## Summary

The audit found 23 findings, ranked in 18 entries (the small ones are grouped). Most of them are small. None asks to undo a documented fence
in `docs/dev/lance.md` or an invariant. Omnigraph already uses its libraries well at the
architectural level: every filter on the main read path is a typed `Expr`, every dataset open
goes through one chokepoint with a shared session, and the DataFusion memory pool, metrics and
stream helpers are reused rather than copied. The gaps are local. They are per-row loops where an
Arrow kernel exists, storage calls whose answer is already in hand, and two operators
(`Sort`, the bounded edge expand's sizing) that missed a pattern the rest of the engine adopted.

Top three:

1. **Change-feed and merge row equality runs per row and per column**
   (`changes/row_compare.rs:559`). Arrow's `cmp::distinct` and `make_comparator` give the same
   answers 80 to 160 times faster in a micro-benchmark. Size M.
2. **Keyed updates read the whole target table.** `.gq update`, Blob cell writes and
   `load --mode merge` force Lance's full-scan merge-insert route (`table_store.rs:4014`). An
   update-only table can take the indexed route that branch merge already uses. Size S.
3. **The plan's `Sort` node does not use DataFusion's `SortExec`.** A sort without a limit is
   refused at the memory limit instead of spilling. A large top-k re-sorts its kept rows for every
   input batch. The engine already runs `SortExec` inside the bound edge expand. Size S to M.

Two findings were measured on a scratch branch with the DST storage-cost golden (see Evidence).
Three were timed with a scratch Arrow benchmark. The rest are from code inspection.

## Primitive maps

Status words: **used**, **used partly**, **reimplemented** (Omnigraph has its own copy),
**worked around** (deliberate, cited), **not used**. Paths are relative to the repository root;
`crates/omnigraph/src` is shortened to `engine-src/`.

### Lance 13.0.0

| Primitive | Status | Omnigraph references | Note |
|---|---|---|---|
| `DatasetBuilder` with session, store params, commit handler | used | `crates/omnigraph-core/src/instrumentation.rs:2108-2204` | One chokepoint opens every dataset. |
| `CommitHandler::resolve_version_location` with a known `ManifestLocation` | used partly | `crates/omnigraph-core/src/lance_clone.rs:52-66, 340-353` | Used only for a clone source. A pin's recorded manifest size and ETag are never fed back. See L1. |
| Session manifest cache (`Dataset::get_manifest`) | not used for graph tables | `crates/omnigraph-core/src/handle_cache.rs:82` | Lance skips the cache when the location has no size, which is always so for a detached version (`lance-13.0.0/src/dataset.rs:882-886`). See L1. |
| Zero-cache control session for `__manifest` | worked around | `crates/omnigraph-core/src/lance_access.rs:86-95` | Invariant 12: mutable-tip caches are hints. |
| `ObjectStoreRegistry`, providers, `WrappingObjectStore`, `IOTracker` | used | `crates/omnigraph-core/src/lance_access/scoped.rs`, `instrumentation.rs:93-130` | Custom counters exist where `IOTracker` sees too little (`crates/omnigraph/tests/helpers/cost.rs:86-90`). |
| `CommitBuilder` (detached, `max_retries(0)`, skip auto cleanup) | used | `engine-src/table_store.rs:1008-1025`, `crates/omnigraph-catalog/src/commit.rs:169-190` | Fences "Detached commits" and "Catalog publication". |
| Conflict resolution and rebase | worked around | `crates/omnigraph-catalog/src/commit.rs:180` | Version CAS by design. |
| `Branches`, `Tags`, `create_branch` | used | `crates/omnigraph-core/src/branch_control.rs`, `engine-src/db/omnigraph/collector.rs:955-990` | Fences "Branch ref reads", "Bound branch create". |
| `shallow_clone`, `restore` | worked around | none | Fences "Table forks", "Detached commits". |
| `cleanup_old_versions` | worked around (deny-listed) | `crates/omnigraph/tests/forbidden_apis.rs` | Fence "Cleanup". |
| `ObjectStore::remove_stream` (concurrent delete) | not used | `engine-src/db/omnigraph/collector.rs:361-409` | Serial deletes with a HEAD before each. See L4. |
| `Dataset::versions` pattern (list with sizes, then `read_manifest(path, Some(size))`) | not used | `crates/omnigraph-catalog/src/lib.rs:1203-1250` | `cleanup --older-than` does HEAD + GET per version. See L7. |
| `ManifestNamingScheme::parse_detached_version`, `list_detached_manifests` | reimplemented (small) | `engine-src/db/omnigraph/collector.rs:1295-1300, 1494-1523` | See L11. |
| `latest_version_id` / private upward probe | worked around | `crates/omnigraph-catalog/src/lib.rs:968-1008` | Fence "Catalog freshness probe". |
| `DatasetDelta` (`get_inserted_rows`, `get_updated_rows`, `get_deleted_row_ids`) | worked around | `crates/omnigraph/tests/lance_surface_guards.rs:3940-4040` | Detached version ids are not an ordered range. Fence "Row version stamps". |
| `Scanner::filter_expr` | used | `engine-src/engine/scan.rs:1019-1190`, `engine-src/blob.rs:1271-1278` | |
| `Scanner::filter(&str)` | used on 3 paths | `engine-src/db/omnigraph/export.rs:374`, `engine-src/db/omnigraph/table_ops.rs:815`, `engine-src/ordered_cursor.rs:73-77` fed by `engine-src/exec/merge.rs:311-345` | Deny-list item. See L8. |
| `Scanner::count_rows` with `CountPushdown` | used partly | `engine-src/engine/operators/metadata_count.rs:88-97`, `crates/omnigraph-planner/src/optimizer.rs:2156` | Unfiltered counts only. See L5. |
| `Scanner::with_row_addr_prefilter` | not used | `engine-src/engine/scan.rs:22-29, 645-696` | See L6. |
| `fast_search` | worked around | none | Invariant 7: skipping unindexed fragments changes results. |
| `nearest`, `maximum_nprobes`, `scan_stats_callback` | used | `engine-src/engine/scan.rs:343-560` | Fence "KNN probe budget". |
| `full_text_search` | used | `engine-src/engine/scan.rs:700-713` | Fences "Full-text analyzer", "Full-text eligibility placement". |
| `take`, `take_rows`, `TakeBuilder`, `_rowaddr IN` take shortcut | used | `engine-src/table_store.rs:4143`, `engine-src/ordered_cursor.rs:495-509`, `engine-src/engine/push/hydrate.rs:201` | |
| `FileFragment::open` + `FragmentReader::take` | used | `engine-src/engine/operators/hydrate.rs:735-772` | Fence "Hydration reads". |
| `read_blobs`, `take_blobs` | used | `engine-src/table_store.rs:2660-2670`, `engine-src/blob.rs:1643` | Fence "Blob batch reads". |
| BTREE, Inverted, IVF_FLAT indexes | used | `engine-src/table_store.rs:3149-3168, 5374-5400` | |
| NGram, LabelList, Bitmap, ZoneMap, BloomFilter indexes | not used | `crates/omnigraph-compiler/src/types.rs:242-261` | See L10. |
| `optimize_indices`, `merge_indices` | worked around | `engine-src/table_store.rs:3075-3200` | Fence "Index segments"; `merge_indices` is crate-private. |
| `plan_compaction`, `CompactionTask::execute` | used | `engine-src/table_store.rs:4563-4690` | `commit_compaction` replaced by a detached `Rewrite` (RFC 0067). |
| `InsertBuilder::execute_uncommitted` | used | `engine-src/table_store.rs:3776, 4759, 5206` | |
| `MergeInsertBuilder` (`execute_uncommitted`, `conflict_retries(0)`, dedupe `Fail`) | used | `engine-src/table_store.rs:3989-4050` | |
| `MergeInsertBuilder::use_index` | worked around, reason no longer applies | `engine-src/table_store.rs:4014-4020` | See L2. |
| `MergeInsertWriteMode::RewriteColumns` | not used | `engine-src/exec/mutation.rs:1705-1873` | See L13. |
| `DeleteBuilder::execute_uncommitted` | used | `engine-src/table_store.rs:3298` | |
| private `apply_deletions` (copied) | reimplemented, concurrency lost | `engine-src/table_store.rs:3346-3398` | See L9. |
| `UpdateBuilder`, `add_columns`/`alter_columns`/`drop_columns` | worked around | `engine-src/table_store.rs:4856-5046` | They commit linearly. |
| `plan_drop_columns` | not used | `engine-src/table_store.rs:4856-5015` | See L12. |
| `_rowid`, `_row_last_updated_at_version`, inline row lineage | used by change discovery, not by merge | `engine-src/changes/row_identity.rs:41-195`, `engine-src/exec/merge.rs:2098-2215` | See L14. |
| `LanceTableProvider`, `supports_filters_pushdown` | worked around | none | Invariant 9 and plan replay; a second planner. |

### DataFusion 54.0.0

| Primitive | Status | Omnigraph references | Note |
|---|---|---|---|
| `ExecutionPlan`, `PlanProperties`, one partition | used | `engine-src/engine/operators/shape.rs:57-71`, `engine-src/engine/context.rs:205-219` | One partition keeps operator order (deliberate). |
| `HashJoinExec` | reimplemented (justified) | `engine-src/engine/operators/hash_join.rs` | Reuses DataFusion's `create_hashes`. Has probe-first reads, a declared `id_lookup` fallback and a switch report. |
| `NestedLoopJoinExec` / cross join | reimplemented (justified) | `engine-src/engine/operators/cross_join/`, `pair_buffer.rs`, `contains_join/` | Charges pair growth to the pool before allocating. |
| `SortExec` (TopK, ExternalSorter, spill) | reimplemented for `Sort`; used inside bound-edge `Expand` | `engine-src/engine/operators/sort.rs:175-299`; `engine-src/engine/operators/expand_stream.rs:388-411` | See D1. |
| `FilterExec`, `ProjectionExec`, `GlobalLimitExec` | reimplemented (justified) | `engine-src/engine/operators/filter.rs`, `projection.rs`, `limit.rs` | `drained` report and output charging (PR #769). |
| `AggregateExec` + `sum`/`min`/`max`/`avg`/`count` UDAFs | used | `engine-src/engine/lower.rs:998-1150`, `engine-src/engine/exact_aggregate.rs` | |
| `GroupsAccumulator` | not used for correlated block aggregates | `engine-src/engine/operators/subquery_aggregate.rs:24-356` | See D3. |
| `array_has` scalar UDF | used in the pushed arm only | `engine-src/engine/scan.rs:1132`; in-memory copy at `engine-src/engine/expr.rs:329-409` | See A2. |
| `SortMergeJoinExec` | not used; unwired push executor has its own join | `engine-src/engine/push/join.rs` | See D4. |
| `SortPreservingMergeExec`, `CoalescePartitionsExec` | used (helper copied twice) | `engine-src/engine/operators/memory.rs:831-862`, `engine-src/table_store.rs:2819-2876` | Internal duplication only. |
| `StreamingTableExec`, `PartitionStream`, `DataSinkExec` | used | `engine-src/engine/operators/expand_stream.rs:388-395`, `engine-src/engine/operators/fragment_sink.rs` | |
| `MemoryPool`, `MemoryReservation`, `TrackConsumersPool<FairSpillPool>` | used and wrapped | `engine-src/engine/context.rs:451-578`, `engine-src/engine/operators/memory.rs` | The wrapper adds refusal and per-buffer charging; not a duplicate. |
| `DiskManager` and spill | used for DataFusion operators only | `engine-src/engine/context.rs:221-224` | Graph operators do not spill (documented). |
| Metrics (`MetricBuilder`, `BaselineMetrics`) | used | `engine-src/engine/operators/mod.rs:86-170` | `drained` is custom because `BaselineMetrics` sets `end_time` on drop. |
| Physical optimizer rules, `LogicalPlan`, logical optimizer | worked around | `crates/omnigraph-planner/src/` | Lowered tree equals the plan, node for node; replay needs plan-only decisions (invariant 9; RFC `2026-09-24-shared-expression-model.md:1032`). |
| `partition_statistics` | worked around | `crates/omnigraph-planner/src/cost.rs` | Statistics must be recorded plan input. |

### Arrow 58.3.0

| Primitive | Status | Omnigraph references | Note |
|---|---|---|---|
| `arrow_ord::cmp` (`eq`, `distinct`, ...) | used for filters; not used by the row comparator | `engine-src/engine/expr.rs:255-262`; `engine-src/changes/row_compare.rs:559` | See A1. |
| `arrow_ord::ord::make_comparator` | not used | none | Needed for nested equality in A1. |
| `lexsort_to_indices`, `sort_to_indices` | used | `engine-src/engine/operators/sort.rs:216-226` | |
| `arrow_row::RowConverter` | not used | none | Not a drop-in for persisted id keys (see "Deliberate"). |
| `take`, `filter_record_batch`, `concat_batches` | used | many | |
| `interleave_record_batch` | not used | `engine-src/exec/merge.rs:651-661` takes one row at a time | Note under A1. |
| `BatchCoalescer` | worked around | `engine-src/engine/operators/pair_buffer.rs`, `shape.rs:540-570` | Re-batching must be charged to the pool. |
| `Scalar` / `Datum` | not used | `engine-src/engine/expr.rs:293-298`, `engine-src/engine/typed_value.rs:119-191` | See A2. |
| `cast_with_options(safe: false)` | used | `engine-src/engine/typed_value.rs:287-303` | Invariant 9. |
| `ArrayData::get_slice_memory_size` | replaced by `WorkMemory::row_bytes`, except at two sites | `engine-src/engine/graph.rs:1090-1097`, `engine-src/table_store.rs:7581-7597` | See A3. |
| `arrow_csv` reader | used, with a `csv_core` pre-count | `engine-src/loader/csv.rs` | Pre-count admits memory first (deliberate). |
| `arrow_json` reader | worked around | `engine-src/loader/mod.rs:1746-1790` | Duplicate-member refusal (deliberate). |
| `arrow_json` writer, `arrow_ipc` | used | `crates/omnigraph-compiler/src/json_output.rs`, `result.rs` | |
| `arrow_string::like` | used | `engine-src/engine/expr.rs:375-376` | |

## Ranked findings

Ranked by expected gain against cost and risk. IDs: L = Lance, D = DataFusion, A = Arrow.

### 1. A1: change-feed and merge row equality is per row and per column (Arrow) — M

- **Now.** `rows_equal_by_column` (`engine-src/changes/row_compare.rs:528-562`) compares each
  non-Blob column with `column.to_data() != other.to_data()` on one-row slices, and it builds a
  `HashSet` of Blob names on every call. Callers: branch merge classification (three compares per
  id, `engine-src/exec/merge.rs:1721-1728, 2746-2769`), the change-feed candidate scan
  (`engine-src/changes/candidate_scan.rs:612`), the exact net diff
  (`engine-src/changes/enumerate.rs:205`) and row-identity pairs
  (`engine-src/changes/row_identity.rs:231-248`). The merge cursor also builds a new
  `BatchCursor` for every row, which clones the batch and converts every field with
  `lance::datatypes::Field::try_from` (`engine-src/changes/row_compare.rs:154-165, 204`;
  `engine-src/exec/merge.rs:586-600`).
- **Upstream.** `arrow_ord::cmp::distinct` (`arrow-ord-58.3.0/src/cmp.rs:182`) for flat columns,
  and `arrow_ord::ord::make_comparator` (`ord.rs:495`) for List, FixedSizeList and Struct. Float
  equality in both is bitwise (`arrow-array-58.3.0/src/arithmetic.rs:397-401`), the same as
  `ArrayData` equality: NaN equals itself and -0.0 differs from 0.0. A vectorized kernel
  already exists in the unwired `engine-src/engine/push/compare.rs:22-55`. Its fallback comment
  ("keeps a NaN equal to itself") assumes `distinct` does not; it does.
- **Gain.** Micro-benchmark, 8,192-row batches, about 1 % of rows differ, same result count in
  every variant:

  | Columns | per-row slice + `to_data` | `make_comparator` | `distinct` + comparator |
  |---|---|---|---|
  | 6 flat columns | 2,321 ns/row | 28 ns/row | 15 ns/row |
  | + `List<Utf8>` | 3,123 ns/row | 44 ns/row | 28 ns/row |
  | + 128-dim `FixedSizeList<f32>` | 3,910 ns/row | 470 ns/row | 441 ns/row |

  The baseline leaves out the per-row cursor and field conversion, so the production gap is
  larger. A full merge walk or exact diff over 1M rows would lose several CPU seconds per pass.
- **Cost and risk.** The walks visit rows one at a time over three cursors with different
  batches, so comparators must be cached per (batch, batch) pair, and cursors built once per
  batch. Blob columns keep their descriptor comparison with a payload tie-break (fence "Blob
  batch reads"). Add an equivalence test of the kernel against `ArrayData` equality over
  null-parent Struct and List rows.
- **Evidence.** Measured ratio (scratch benchmark outside the repository); production share not
  measured.

### 2. L2: keyed updates force Lance's full-scan merge-insert route (Lance) — S for updates

- **Now.** `stage_keyed_write_from_stream` calls `builder.use_index(false)` for every write that
  can insert (`engine-src/table_store.rs:4014-4020`). The comment gives the reason: Lance's
  indexed route does not return the inserted-row key filter. `.gq update`
  (`engine-src/exec/mutation.rs:1868`), Blob cell writes (`engine-src/exec/blob_write.rs:376`) and
  `load --mode merge` (`engine-src/loader/mod.rs:1042`) all stage as `Upsert`
  (`engine-src/exec/staging.rs:898`). The full-scan route joins the source against a scan of the
  whole target table (`lance-13.0.0/src/dataset/write/merge_insert.rs:2419-2530`).
- **Why the reason has narrowed.** Every graph-table commit is detached, and a detached commit
  skips Lance's conflict resolver (`lance-13.0.0/src/io/commit.rs:1197-1215`), which was the only
  Lance reader of that filter. The filter still matters to Omnigraph itself: the strict-insert
  certificate and the pure-insert merge proof read it (`engine-src/table_store.rs:7140-7200`). An
  update that matches only existing rows inserts nothing, so it needs no filter.
  `KnownPresentUpdate` already takes the indexed route for branch merge
  (`engine-src/exec/merge.rs:4197`).
- **Change.** Give update-only tables (no insert pending for the table) the `KnownPresentUpdate`
  semantics. Keep `Upsert` with possible inserts on the current route unless the certificate is
  rebuilt from the source ids, as `stage_absence_proven_strict_insert` does
  (`engine-src/table_store.rs:3739-3755`).
- **Gain.** Update cost through the id BTREE instead of a full scan and join. RFC 0023's
  measurement on an older Lance: a 32-row upsert on 1M rows took 29 ms forced against 10 ms
  indexed, with 238 MB against 179 MB RSS (`docs/rfcs/0023-key-conflict-fencing.md:1199-1203`).
- **Cost and risk.** Re-run `lance_surface_guards::row_identity_discovery_stamps_row_ids_and_rewrites_lance_13`
  for an indexed-route update: the row version stamp rule (fence "Row version stamps") is not
  pinned on that route. The "Index segments" whole-rebuild rule is already load-bearing for
  `KnownPresentUpdate`. Unverified: whether DataFusion 54's dynamic join filters already push
  source keys into the full-scan route, which would shrink the gain.
- **Evidence.** Code inspection. A scratch prototype that let all `Upsert`s take the indexed
  route and skipped the filter check ran the seeded DST cost scenario green; at DST's small
  table sizes it changed only `InsertLegacy` (22 → 20 `l.get`) and the setup/verify rows, and
  `UpdateV` stayed at 8. So DST cannot show the scale gain; it needs a large-table cost test.

### 3. D1: the plan's `Sort` node should run DataFusion's `SortExec` inside Omnigraph's operator (DataFusion) — S to M

- **Now.** `engine-src/engine/operators/sort.rs`. Without a fetch it drains the input,
  concatenates it, runs `lexsort_to_indices` and permutes; nothing spills, so an unbounded
  `order by` is refused at the pool limit (the regression PR #769 admitted). With a fetch,
  `top_k` (`:233-275`) concatenates the kept rows with each new batch and sorts again. For
  n = 1M, k = 100k, batch 8,192, that is about 13M row copies.
- **Upstream.** `SortExec::new(..).with_fetch(..)`
  (`datafusion-physical-plan-54.0.0/src/sorts/sort.rs:942-963, 1216-1270`): `TopK` keeps a heap
  of row-format keys (`topk/mod.rs:187-311`); without a fetch, `ExternalSorter` spills under the
  query's scratch quota. The engine already wraps exactly this in the bound-edge `Expand`
  (`engine-src/engine/operators/expand_stream.rs:388-411`). `TopK` itself cannot be built
  outside DataFusion (its filter type is private), so `SortExec::with_fetch` is the way in.
- **Gain.** Sorts without a limit spill instead of failing; large top-k becomes a heap. About
  90 lines replaced by about 30. The operator, its report counters and the hidden-column drop
  stay Omnigraph's.
- **Cost and risk.** `HardCapBatchSizeExec` under the sort refuses a single row wider than a
  quarter of the pool (the "Ordered walks" limit; only rows over 37.5 MiB). DataFusion's TopK
  updates a dynamic filter nobody reads (small cost). Release note: "a sort without a limit
  spills". Owners: `cases/v2/planner/top_k_sort_is_a_prefix_of_the_full_sort.gqt`,
  `sort_tiebreak_declared.gqt`, `engine-src/engine/report/tests.rs`,
  `tests/integration/engine_v2_memory.rs`.
- **Evidence.** Code inspection.

### 4. L1: pinned table opens throw away the manifest location the pin records (Lance) — M

- **Now.** Each registration row stores the pinned version's manifest path, size and ETag
  (`crates/omnigraph-core/src/metadata.rs:106-160`). No open path reads them;
  `TableVersionMetadata::manifest_size()` has no production caller. A detached version resolves
  with no size (`lance-table-13.0.0/src/io/commit.rs:1062-1073`), so Lance pays a HEAD plus a
  GET and skips its session manifest cache (`lance-13.0.0/src/dataset.rs:756-776, 882-886`).
  `TableHandleCache` (64 entries, `crates/omnigraph-core/src/handle_cache.rs:82`) hides this on
  warm reads. The collector reopens each traced version up to five times without that cache
  (`engine-src/db/omnigraph/collector.rs:306, 639, 1375, 1649, 1663, 1887`).
- **Upstream.** Return the recorded `ManifestLocation` from the wrapped
  `CommitHandler::resolve_version_location`, the seam Omnigraph already uses for clone sources
  (`crates/omnigraph-core/src/lance_clone.rs:52-66, 340-353`). With a size, Lance reads without a
  HEAD and caches the manifest under `(version, e_tag)`; a wrong size is detected and retried
  (`lance-13.0.0/src/dataset.rs:778-793, 887-901`).
- **Gain.** One HEAD less per cold pinned open. After a handle-cache eviction, a repeat open
  needs no manifest request. Collector reopens become cache hits. About 30-50 lines; no format
  change.
- **Cost and risk.** A cached manifest can outlive a `cleanup`; keep the
  `HistoricalVersionReclaimed` mapping (`crates/omnigraph-core/src/instrumentation.rs:2116-2141`).
  Older rows may hold a null size (`crates/omnigraph-catalog/src/history.rs:4156`) and must fall
  back. Do not mix a put-form ETag with a discovery-form ETag (fence "Local manifest identity").
  DST cost-golden lines move.
- **Evidence.** Code inspection.

### 5. A3: the bounded edge expand sizes rows with Arrow's `get_slice_memory_size`, which over-counts list columns (Arrow) — S

- **Now.** `engine-src/engine/graph.rs:1090-1097` computes
  `column.slice(row, 1).get_slice_memory_size()` for every edge row and attached column. PR #1241
  replaced this pattern elsewhere with `WorkMemory::row_bytes`
  (`engine-src/engine/operators/memory.rs:875-886`), whose comment says why: Arrow's slice keeps
  a list column's child buffer whole, so the upstream function counts the whole child for every
  row (`memory.rs:1187-1189`; `arrow-data-58.3.0/src/data.rs:554-556`).
- **Gain.** For edges with `List` or vector properties: correct sizes, so output batches stop
  shrinking to one row and the pool stops being over-charged, which can refuse a query that fits. It also drops one `ArrayData` allocation per column per row. About 10 lines: call
  `row_bytes` once per batch. The same over-count, milder, sits in `batch_slice_memory_size`
  (`engine-src/table_store.rs:7581-7597`) for `List` columns; there it only shrinks prefixes.
- **Cost and risk.** None known. Add a case with a list-typed edge property to
  `tests/integration/engine_v2_memory.rs`.
- **Evidence.** Code inspection of both sides; verified against the arrow-data source.

### 6. L4: cleanup sends a HEAD before every file it deletes, and deletes one at a time (Lance) — S

- **Now.** `delete_paths` (`engine-src/db/omnigraph/collector.rs:361-385`) calls
  `delete_object(.., None)`, which HEADs for the size before the DELETE (`:389-409`). The plan's
  own listing (`plan_orphans`, `:1406-1458`) already saw every file with its size; it keeps the
  sizes only for orphans, and the delete path ignores even those. Deletes run serially.
- **Upstream.** `lance_io::object_store::ObjectStore::remove_stream`
  (`lance-io-13.0.0/src/object_store.rs:1604-1637`) deletes up to `io_parallelism()` paths at
  once and treats "not found" as done. Lance's own cleanup takes sizes from its listing.
- **Gain.** One request less per freed file, and up to `io_parallelism` times less wall time per
  file group on cloud stores. The group order and oldest-first manifest deletes stay.
- **Cost and risk.** DST replay must stay identical under concurrent completion; use an ordered
  `buffered` stream if needed. Consider a delete rate cap for S3 `SlowDown`. The "never call
  `cleanup_old_versions`" fence is untouched.
- **Evidence.** Measured for the HEAD part. A 12-line prototype (record listed sizes, pass them to
  `delete_object`) moved the DST golden's `Cleanup l.get` from 147 to 138: nine HEADs saved for
  20 deletes, every other row unchanged. Concurrency not measured.

### 7. A2: in-memory `contains` and constants (Arrow and DataFusion) — S for `contains`, M for constants

- **Now.** `evaluate_contains_filter` (`engine-src/engine/expr.rs:329-409`) loops over rows and
  list items, slicing both sides and calling a comparison kernel per element. Every literal and
  parameter is expanded to a full-length array per batch (`engine-src/engine/expr.rs:293-298`,
  `engine-src/engine/typed_value.rs:119-191`).
- **Upstream.** DataFusion's `array_has` (`datafusion-functions-nested-54.0.0/src/array_has.rs`),
  which the pushed arm already uses (`engine-src/engine/scan.rs:1132`); null handling checked
  equal on both sides. Arrow `Scalar`/`Datum` (`arrow-array-58.3.0/src/scalar.rs:78, 128`) for
  constants in every `cmp` and `like` kernel.
- **Gain.** Micro-benchmarks: `contains` 12 times faster; `Utf8` equality against a constant
  from 49.0 to 3.1 ns/row; `Int64 >` from 1.36 to 0.90 ns/row. The pushed and in-memory arms
  would share one `contains` kernel. Applies to cross-binding filters, filters inside negation
  blocks and non-pushable residuals.
- **Cost and risk.** `evaluate_expr` must return a scalar-or-array value, like DataFusion's
  `ColumnarValue`; recorded casts then run on one value. Invariant 9 is unchanged.
  `arrow_ord::comparison::in_list` is not a drop-in (different null and float rules).
- **Evidence.** Measured ratio (scratch benchmark); how often filters reach this path is not
  measured.

### 8. L5: filtered `count(...)` reads the matching rows; Lance can count from the index (Lance) — M

- **Now.** The planner turns `count(binding)` into `MetadataCount` only without a filter
  (`crates/omnigraph-planner/src/optimizer.rs:2156`). A filtered count scans and projects the key
  columns of every match.
- **Upstream.** `Scanner::count_rows()` with `filter_expr`. Lance's `CountPushdown` rule
  (`lance-13.0.0/src/io/exec/count_pushdown.rs:77-260`) answers an exact scalar-index filter
  from index pages and deletion files, unions in a scan of unindexed fragments, and under stable
  row ids counts only rows whose current home is covered.
- **Gain.** Index pages plus deletion files instead of index pages plus a take of each match.
  The planner already proves the needed shape (`ScanAccess::IndexProbe { residual: None }`).
- **Cost and risk.** Planner rule, plan mirror and replay, explain, GQT plan cases. Correct only
  while Omnigraph never adds delta index segments (fence "Index segments"); add a surface guard
  for count-from-mask after an update moves an indexed row. Unverified: whether
  `CountPushdown` fires on the exact scanner settings Omnigraph uses.
- **Evidence.** Code inspection.

### 9. L6: a BM25 scan's full-text filter travels as an `id IN (...)` string list (Lance) — S to M

- **Now.** `search_filter_member_ids` (`engine-src/engine/scan.rs:645-696`) runs a first
  full-text pass, collects every member id as a `String`, and ANDs `id IN (members)` into the
  ranking scan. Lance then searches the id BTREE again to recover row ids it already had.
- **Upstream.** `Scanner::with_row_addr_prefilter` (`lance-13.0.0/src/dataset/scanner.rs:1627-1666`),
  which Omnigraph's own comment names (`engine-src/engine/scan.rs:22-29`).
- **Gain.** A row-id bitmap instead of N strings, a literal list and a second index search.
- **Cost and risk.** Valid only when the plan filters before scoring; under
  `filter_after_scoring` the fence "Full-text eligibility placement" requires the postfilter,
  so the current path stays for that case. Leave the rrf and nearest prefilter gates alone: they
  hold id strings, not row ids.
- **Evidence.** Code inspection.

### 10. L9: `stage_delete_rows` extends deletion files one fragment at a time (Lance) — S

- **Now.** `engine-src/table_store.rs:3358-3378` awaits `extend_deletions` serially per fragment.
- **Upstream.** The private `apply_deletions` it copies
  (`lance-13.0.0/src/dataset/write/delete.rs:52-96`) runs the same calls with
  `buffer_unordered(io_parallelism())`.
- **Gain.** Deletes over many fragments (cascades, predicate deletes) need about
  F / io_parallelism round trips instead of F. Request count unchanged.
- **Cost and risk.** Use ordered `buffered` so fragment order stays deterministic for DST.
- **Evidence.** Code inspection.

### 11. L8: three read paths still build SQL filter strings (Lance) — S

- **Now.** Export entity lookup (`engine-src/db/omnigraph/export.rs:374`, `format!` with quote
  escaping, no limit); vector trainability check (`engine-src/db/omnigraph/table_ops.rs:808-819`,
  counts every non-null vector to learn whether one exists); the merge candidate cursor's
  `KeyFilter::Sql` (`engine-src/ordered_cursor.rs:73-77`, built at `engine-src/exec/merge.rs:311-345`).
  `Expr` twins exist next to each (`engine-src/blob.rs:1271-1278`, `engine-src/exec/merge.rs:303-309`).
- **Upstream.** `Scanner::filter_expr`; `lance::index::vector::has_vectors_to_train`
  (`lance-13.0.0/src/index/vector.rs:515-587`) or `limit(1)`.
- **Gain.** Closes the deny-list item "ad-hoc SQL or `IN (...)` string generation" on read paths;
  about 60-80 lines go; export gets duplicate detection with `limit(2)`; the trainability check
  stops at the first vector.
- **Cost and risk.** The merge chunk's byte bound is measured on the SQL text and must be
  re-expressed per id.
- **Evidence.** Code inspection.

### 12. L7: `cleanup --older-than` sends HEAD + GET for each manifest whose size the listing already gave (Lance) — S

- **Now.** `read_versions` (`crates/omnigraph-catalog/src/lib.rs:1224-1250`) calls
  `checkout_version` per undecided version. `version_refs` drops the listed size
  (`lance-13.0.0/src/dataset.rs:2664-2678`).
- **Upstream.** List through `CommitHandler::list_manifest_locations` (sizes included), then
  `lance_table::io::manifest::read_manifest(store, path, Some(size))`: one GET, as
  `Dataset::versions` does (`lance-13.0.0/src/dataset.rs:2627-2643`).
- **Gain.** One request less per version read. Scales with branch history.
- **Cost and risk.** Keep the check that the body's version equals the listed one.
- **Evidence.** Code inspection.

### 13. L14: branch merge does not use the row-identity rule change discovery already has (Lance) — M

- **Now.** `lineage_side_candidates` (`engine-src/exec/merge.rs:2098-2215`) makes every live row
  of a rewritten fragment a candidate, so a merge across an `optimize` falls back to the full
  three-way walk. The fragment-identity RFC says this
  (`docs/rfcs/2026-10-26-change-discovery-by-fragment-identity.md:101-109`). Change discovery
  fixed the same problem with `row_identity_plan` (`engine-src/changes/row_identity.rs:41`),
  which reads Lance's inline `_rowid` and update stamps that compaction keeps.
- **Gain.** Merges across a compaction stay change-sized. The RFC measured the change-feed
  version: 277 requests / 6.9 MB down to 14 requests / 27 KB at 100K rows.
- **Cost and risk.** The "Row version stamps" fence holds only where the base is on that side's
  first-parent chain with no merge commit after it; merge needs the same test per side. Blob
  tables are excluded from the lineage route (`engine-src/exec/merge.rs:2226-2228`, reason not
  stated). Merge works in ids, row identity in `_rowid`s.
- **Evidence.** Code inspection.

### 14. L10: index kinds Lance's filter planner serves are never built (Lance) — M to L

- **Now.** `PropType::index_kind` (`crates/omnigraph-compiler/src/types.rs:242-261`) gives a
  free-text `String` only an inverted index, lists none. So `=`, `starts_with` and `contains` on
  free-text strings and `contains` on lists are full column scans, as the user guide says
  (`docs/user/queries/index.md:166-168`).
- **Upstream.** `lance-index-13.0.0/src/scalar/expression.rs` maps `starts_with` to a BTREE
  prefix search, `contains` to NGram, and `array_has` to LabelList. Omnigraph already pushes
  those exact `Expr` shapes.
- **Gain.** Index probes for these filters, with no new execution code.
- **Cost and risk.** Schema syntax to request a kind; `stage_index_fold` rebuilds every
  non-vector index as BTREE (`engine-src/table_store.rs:3162-3168`) and must learn kinds first;
  `plan_source/indexes.rs` classification; a surface guard per kind; check how Lance chooses
  between two indexes on one column.
- **Evidence.** Code inspection.

### 15. D3: correlated block aggregates hand-roll grouped accumulators (DataFusion) — M, decision first

- **Now.** `engine-src/engine/operators/subquery_aggregate.rs:24-356` folds row by row; `min` and
  `max` build a `ScalarValue` per row.
- **Upstream.** `create_groups_accumulator` on the same UDAFs the outer `Aggregate` uses; the
  correlation tag becomes `group_indices`.
- **Gain.** Vectorized grouped aggregates; about 150 lines; one implementation per aggregate.
- **Cost and risk.** The two arms differ today: block `min`/`max` skip NaN and keep the first
  signed zero (pinned by `extrema_skip_nan_and_preserve_first_signed_zero`); DataFusion orders
  floats by total order. Someone must decide which is intended before switching.
- **Evidence.** Code inspection.

### 16. Hygiene: dead strict-insert branch and stale "Lance key fence" wording (Lance) — S

- `stage_keyed_write` returns for `StrictInsert` before the merge-insert
  (`engine-src/table_store.rs:3563-3577`), so the `WhenMatched::Fail` arm (`:4003`) and the
  strict call to `validate_strict_insert_merge_stats` (`:4033`) cannot run. `docs/dev/merge.md:176`,
  `engine-src/exec/merge.rs:384-386, 4606-4610` and RFC 0023's route table still describe a
  Lance-level conflict on same-key inserts, which detached commits never run. That wording is
  what makes L2's `use_index(false)` look necessary.
- RFC 0030 says Lance has no deleted-row API; Lance 13 has `DatasetDelta::get_deleted_row_ids`
  (still not a fit, see "Deliberate").
- **Evidence.** Code inspection (the early return was checked by hand).

### 17. Small items — S each

- **L12.** Schema-apply drops could use Lance's public `plan_drop_columns`
  (`lance-13.0.0/src/dataset/schema_evolution.rs:1168`) instead of computing the projected schema
  (`engine-src/table_store.rs:4856-5015`). Gain: Lance-owned drop validation; today's output is
  probably identical.
- **L11.** The collector parses detached manifest names by hand
  (`engine-src/db/omnigraph/collector.rs:1295-1300`); use
  `ManifestNamingScheme::parse_detached_version`.
- **A4.** Unique and `@key` derivation resolves each column's type per row and allocates
  `Vec<String>` keys (`engine-src/loader/mod.rs:4041-4165`, `engine-src/validate/kernels.rs:321-473`).
  Resolve the renderer once per batch; cast single-column `Utf8`, integer and bool keys directly.
  Do not switch to `RowConverter` (see "Deliberate").
- **A5.** The JSONL loader builds a `String` per cell before `StringArray::from`
  (`engine-src/loader/mod.rs:3163-3172`); build from borrowed `&str`.
- **Internal.** The `HardCapBatchSizeExec` insertion and partition-merge helper is copied in
  `engine-src/engine/operators/memory.rs:831-862` and `engine-src/table_store.rs:2819-2876`.

### 18. Decide before wiring: the dormant push executor (DataFusion and Lance) — L

- `engine-src/engine/push/` (about 3,000 lines) is a second execution model for change-feed and
  merge plans, with its own full-outer join that holds the whole build side, refuses above
  4M keys and cannot spill (`push/join.rs`). Its `mod.rs:1-6` says no caller is wired. It also
  holds a second hydrator next to `ordered_cursor` (`push/hydrate.rs:75-268`), with a weaker
  memory bound. No RFC explains the design. Before wiring it, compare with DataFusion's
  `SortMergeJoinExec` over a spilling `SortExec`, which would keep one execution model and turn
  the 4M cap into spill.
- **L13**, also large: `MergeInsertWriteMode::RewriteColumns` would let an update of one scalar
  property leave Blob and vector columns unread and external Blob references intact
  (`engine-src/exec/mutation.rs:1753-1866`). It costs per-fragment index coverage and the change
  feed's fast path for that commit. Worth an experiment, not a direct change.

## Deliberate workarounds kept

Each of these looks like an underused primitive and is not. Do not undo them.

- **Engine collector instead of `cleanup_old_versions`.** Lance 13 does not trace detached
  manifests. Fence "Cleanup"; deny-listed in `forbidden_apis.rs`. Lance's per-manifest helpers
  are private; the public ones are already used.
- **Catalog freshness probe by name.** `latest_version_id` lists on ordered stores; the upward
  probe upstream is private. Fence "Catalog freshness probe".
- **Commit-handler wrapper** (request re-issue, definitive-missing override, local ETag quoting,
  clone-location reuse). Fences "Manifest request resolution", "Local manifest identity".
- **Zero-cache control session; `TableHandleCache`; `read_memo.rs`.** Invariant 12; Lance's
  manifest cache cannot hold detached versions; Lance 13 has no data-page cache.
- **No `DatasetDelta`, no version-window filters.** Detached version ids are not ordered and
  sibling stamps alias. Fence "Row version stamps"; fragment-identity RFC alternatives 5 and 6.
- **No `optimize_indices` / delta segments; whole index rebuilds.** Fence "Index segments"
  (lance#9817); `merge_indices` is crate-private.
- **Hand-built `Rewrite`, `Project`, `Merge`, `Delete` and strict-insert `Update` operations.**
  The builders commit linearly or are private; RFC 0067 requires detached commits; the strict
  insert carries Omnigraph's own certificate (RFC 0023).
- **`HydrateExec` per-row `FragmentReader::take`.** Fence "Hydration reads".
- **Ordered walks sort only `id`, `_rowid`, `_rowaddr`.** Fence "Ordered walks" (lance#9048,
  lance#9692).
- **ANN probe ladder, BM25 filter placement, untrained full-text segments, Blob read buffer.**
  Fences "KNN probe budget", "Full-text eligibility placement", "Full-text analyzer",
  "Blob batch reads".
- **No `fast_search`.** Invariant 7.
- **No `LanceTableProvider`, no DataFusion logical plan or optimizer, one partition.** Invariant 9
  and plan replay; RFC `2026-09-24-shared-expression-model.md`.
- **Own `Filter`, `Projection`, `Limit`, `CrossJoin`, `HashJoin` operators.** PR #769: DataFusion's
  versions cannot report `drained` and do not charge their output to the pool (invariant 11).
- **`SpillHeadroomPool` and the per-buffer charge registry.** DataFusion's `ArrowMemoryPool` cannot
  refuse a reservation.
- **Hand re-batching instead of `BatchCoalescer`.** Copies must be charged to the pool.
- **Canonical key strings instead of `RowConverter`.** The string is the persisted id of an `@key`
  row and must be stable across string encodings and ±0.
- **`serde_json` DOM for JSONL; `csv_core` pre-count for CSV.** Duplicate-member refusal; memory
  admitted before parsing.
- **Custom CSR topology, `RankFuseExec`, Aho-Corasick contains-join.** No upstream equivalent.

## Upstream changes since the pins

- **Lance 14.0.2** (latest stable, 2026-10-29) still uses DataFusion 54 and Arrow 58. It brings
  top-k late materialization (PR 9750), `CountPushdown` through custom nodes (PR 9716) and
  row-address moves for deferred compactions on stable-row-id tables (PR 9611). None changes a
  verdict above.
- **Lance 15** (betas only, DataFusion 55 and Arrow 59) closes two issues behind current fences:
  lance#9817 stale full-text and BTREE segment entries (PR 9872), which the "Index segments"
  whole-rebuild rule works around, and lance#9692 rechunked batch tails (PR 9852), cited by
  "Ordered walks". It also adds `ScalarIndex::lookup` for batched exact-match key lookups
  (PR 9875), which would help the `id IN` lookups in `hydrate_nodes`, the prefilter gates and
  merge candidates. At that bump, re-check the two fences and L5.
- **DataFusion 55.2.0** and **Arrow 60.1.0** are current; not assessed.

## Evidence

- **DST storage-cost golden**, run from `crates/omnigraph-dst` on a scratch branch that was
  then deleted (nothing pushed):
  - Baseline on main: passes.
  - L4 prototype (listed sizes passed to `delete_object`): `Cleanup l.get` 147 → 138; no other
    row moved.
  - L2 prototype (all `Upsert`s on the indexed route, filter check skipped): passes;
    `InsertLegacy l.get` 22 → 20, `_audit` 679 → 669, `_verify` 1407 → 1399; `UpdateV` unchanged
    at 8. This only shows the route runs; it is not a scale measurement.
- **Arrow micro-benchmarks** (A1, A2) ran in a scratch crate outside the repository against
  arrow 58.3.0; numbers are ratios on one machine, not production timings.
- Everything else is code inspection of both sides at the pinned versions.

## Could not verify

- How often production work reaches the hot paths: full merge walks and exact diffs (A1),
  in-memory residual filters (A2), filtered counts (L5), BM25 with a full-text filter (L6).
- L2 at scale, and whether Lance's indexed merge-insert route stamps
  `_row_last_updated_at_version` like the full-scan route.
- Whether DataFusion 54's dynamic join filters already shrink the full-scan merge-insert cost (L2).
- How often `TableHandleCache` misses in served workloads (L1), and whether every live pin row
  has a non-null manifest size.
- Whether concurrent deletes keep DST replay identical (L4, L9).
- Whether `CountPushdown` fires on the scanner configuration Omnigraph uses (L5).
- How Lance picks between two indexes on one column (L10).
- Whether the block-aggregate NaN and signed-zero behavior is required (D3).
- Why the merge lineage route excludes Blob tables (L14).
- Whether A3's over-count has caused user-visible memory refusals.

## Workflow feedback

- **Scope is too large for one pass.** "The whole engine" across three libraries is about
  115,000 lines of engine code plus the upstream crates. This run split it into five areas
  (Lance storage, Lance reads, writes and changes, DataFusion, Arrow) handled in parallel. The
  definition should name that split, or ask for one focus per run, so runs are comparable.
- **"Confirm with a test or prototype" needs a budget and a tool.** The DST storage-cost golden
  is the cheapest instrument (about two minutes warm) and counts HEADs under `l.get`. It cannot
  show scale effects because its tables are tiny. Say which instrument to use for storage
  claims, which for CPU claims, and how much time to spend.
- **Ranking rule is unstated.** "Gain against cost" with mostly unmeasured gains is a judgement.
  A fixed rubric (for example: guarantee or correctness first, then storage calls, then CPU,
  each weighted by how common the path is) would make runs comparable.
- **Upstream "open PRs since" is open-ended.** Lance merges dozens of PRs a week. Narrow it to
  issues cited in `docs/dev/lance.md` plus the release notes of newer stable releases. That is
  what this run did.
- **Prior-run dedup needs a stable key.** Findings should carry an id (for example
  `primitive@file`) that the next run can match, since line numbers move.
- **Output location.** `docs/audits/` does not exist in the repository, and `check-docs.py` may
  not expect it. If these reports should not live in the source tree, say where they go.
- **Box handling.** The definition says to start a box and that the box stops when the run ends;
  this run was given a started box. State which of the two the executor owns.
