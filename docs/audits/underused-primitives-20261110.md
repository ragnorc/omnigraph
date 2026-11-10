# Audit: Lance, DataFusion and Arrow primitives Omnigraph underuses — scans and query execution

**Date:** 2026-11-10 (UTC)
**Omnigraph commit:** `2c4b2f12c87b41a58c1b32ab44678fcf34e66e2a` (main)
**Pinned libraries (Cargo.lock):** lance 13.0.0 (all lance-* crates), datafusion 54.0.0, arrow 58.4.1
**Focus:** scans and query execution (the read path from a table access down to Lance; the
execution operators; the Arrow kernels on that path)
**Previous runs:** 2026-11-04, whole engine (`docs/audits/underused-primitives-20261104.md` on
branch `audit/underused-primitives-20261104`); 2026-11-07, indexes and search
(`docs/audits/underused-primitives-20261107.md` on branch `audit/underused-primitives-20261107`).
Their finding IDs stay as they are.
**Status:** read-only audit. Nothing here is a decision. Maintainers triage each issue.

No focus was named for this run. The two earlier runs covered the whole engine, then indexes and
search. This run took the next area in the workflow's list, scans and query execution. Upstream
source was read from the crates.io sources at the pinned versions.

## Summary

Eleven new findings. Six issues were filed; three of them group small items. Two findings and one
weak candidate went to #724 as one evidence comment, because #724 already covers memory that the
query pool does not charge. Nothing was skipped as already covered. No finding asks to undo a fence
in `docs/dev/lance.md` or an invariant.

The execution path already uses Arrow kernels for filters, joins, take, interleave and JSON output,
and no site builds a filter predicate once per column. The gaps are three: a limit that Lance never
sees, filters that do not short-circuit, and graph-index lookups on a SipHash map of owned strings.

Top three:

1. **L-scan-limit-pushdown.** A `limit` over a scan without a filter only sets how many rows the
   engine gathers. Lance never gets `Scanner::limit`, so it cannot prune to the first fragments
   from manifest row counts. Size S.
2. **D-filter-preselect.** The in-memory filter evaluates every conjunct over every row, and `and`
   and `or` evaluate both sides in full. DataFusion's `BinaryExpr` short-circuits and evaluates the
   right side only on the rows still selected. On a selective filter this measured 9.9 to 1.7 ns
   per row. Size S to M.
3. **A-typeindex-dict.** The graph index maps node ids to dense ids through a std `HashMap<String,
   u32>` (SipHash, two owned copies of every id). It is used on every input row of every CSR
   traversal, and every destination string is rebuilt from `Vec<String>`. Arrow `take` over a
   `StringArray` and a faster hash measured 1.5 to 1.9 times faster per batch, 1.7 times faster to
   build and 7 to 11 times faster to load from the persisted artifact. Size M.

Three findings are measured with scratch benchmarks against the pinned crates. The others are from
reading code on both sides.

## Prior findings since the last run

Main has 24 commits since the 2026-11-07 run (`2d8effab`). State of earlier issues on 2026-11-10:

| Finding | Issue | State | What changed |
|---|---|---|---|
| L-merge-delete-rowid | #1295 | closed | Fixed by #1301 (merge deletes by the `_rowid` its walk read). |
| L-vector-metric-pin | #1297 | closed | Fixed by #1299 (rank `nearest` by L2, use only an exact IVF_FLAT index). |
| L13 merge-insert `RewriteColumns` | #1264 | closed | #1272 patches an update's assigned columns instead of rewriting Blob and vector rows. |
| L-coverage-one-source | #1298 | open, partly done | #1300 judges index coverage by the union of an index's segments. |
| BM25 limit (#793 note) | #793 | closed (RFC 0048 merged) | No code change on the read path yet. |
| All other findings from both runs | #1247-#1250, #1252-#1260, #1262, #1263, #1294, #1296, #1095, #748 | open | No change in the audited code. |

Two other commits touch this area: #1291 write-allocates the dataset cache, and #1319 makes
`TableHandleCache` evict under budget pressure. Neither changes a finding here.

## Primitive maps

Status words: **used**, **used partly**, **reimplemented** (Omnigraph has its own copy),
**worked around** (deliberate, cited), **not used**. `E/` is `crates/omnigraph/src/`. Upstream
paths are relative to each crate's `src/` at the pinned version.

### Lance 13.0.0 (read path)

| Primitive | Status | Omnigraph references | Upstream | Note |
|---|---|---|---|---|
| `batch_size`, `batch_size_bytes` | used | `E/engine/scan.rs:270-271`, `E/engine/graph.rs:1880-1881`, `E/ordered_cursor.rs:216, 503` | `dataset/scanner.rs:1799, 1815` | |
| `batch_readahead` | used partly | `E/engine/scan.rs:273` (2, streaming node scans only) | `dataset/scanner.rs:1882` | Other engine reads keep the default (CPU count). See the #724 comment. |
| `fragment_readahead` | not used | none (hydration has its own `FRAGMENTS_AHEAD`) | `dataset/scanner.rs:1892`; default `io/exec/filtered_read.rs:603-606` | See the #724 comment. |
| `io_buffer_size` | used partly | `E/engine/scan.rs:274` (64 MiB, streaming node scans only) | `dataset/scanner.rs:1850` | Edge scans, ranked scans, id lookups, ordered walks and the topology build use the default. |
| `limit` / `offset` and metadata range planning | used partly | `E/table_store.rs:298` (validation and Blob reads only); query `Limit` is a gather size only, `E/engine/lower.rs:355-366, 641-643` | `dataset/scanner.rs:1956, 3110-3155, 3159-3200` | See L-scan-limit-pushdown. |
| `project`, `with_row_id`, `with_row_address` | used | `E/table_store.rs:236-240, 393` | `dataset/scanner.rs:1564, 2303, 2310` | |
| `materialization_style` | catalog only | `omnigraph-catalog` `state.rs:1692` | `dataset/scanner.rs:1685` | Catalog use is the "Packed catalog record" fence. The engine's `HydrateColumns` works above Lance and does not copy it. |
| `scan_in_order`, `use_stats` | not used | none | `dataset/scanner.rs:1920, 2445` | No effect on v2.2 filtered reads. |
| `strict_batch_size` | catalog only | `omnigraph-catalog` `lib.rs:440` | `dataset/scanner.rs:1945` | Engine gathering runs after the runtime-filter sieve (#887); not a duplicate. |
| `with_fragments`, `filter_expr`, `prefilter` | used | `E/table_store.rs:294`, `E/engine/scan.rs:263-268` | `dataset/scanner.rs:1502, 1622, 1776` | String filters are L8 (#1257). |
| `count_rows` | used | `E/engine/operators/metadata_count.rs:95` | `dataset.rs:1705` | Filtered count is L5 (#1254). |
| `create_plan` | used | `E/table_store.rs:2864`; `plan_source/scan_access.rs` reads Lance's plan nodes | `dataset/scanner.rs:3256` | Good use: index choice is read from Lance, not re-derived. |
| `aggregate` (new in 13) | not used | none | `dataset/scanner.rs:1785, 2727-2766` | A plain `AggregateExec` over the read; no gain. |
| `with_row_addr_prefilter` | not used | noted as future work at `E/engine/scan.rs:21-28` | `dataset/scanner.rs:1663` | L6 (#1255), L-index-tail-cost (#1294). |
| `take_rows`, `TakeBuilder`, `_rowaddr IN` take | used | `E/engine/push/hydrate.rs:199`, `E/ordered_cursor.rs:493-506` | `dataset.rs:1773-1784`, `dataset/scanner.rs:1408-1420` | |
| `FragmentReader::take` | used | `E/engine/operators/hydrate.rs:746` | | Fence "Hydration reads". |
| Session caches, `DatasetBuilder` | used | `omnigraph-core` `lance_access.rs:57-65`, `instrumentation.rs:2174-2200` | `session.rs:124`, `dataset.rs:324` | Cache sizes are #702; pinned-open location is L1 (#1250). |
| `LanceExecutionOptions` | ordered scans only | `E/table_store.rs:2828-2836` | `lance-datafusion` `exec.rs:282` | Query plans run under the engine's own `TaskContext`. |
| `scan_stats_callback`, `include_deleted_rows` | used | `E/engine/scan.rs:429`, `E/table_store.rs:5009` | `dataset/scanner.rs:1669, 1830` | Fence "KNN probe budget"; #721. |
| `fast_search` | not used | none | `dataset/scanner.rs:2203` | Deliberate, invariant 7. |
| `with_index_segments`, `query_parallelism`, `materialization_readahead_bytes`, `disable_scoring_autoprojection` | not used | none | `dataset/scanner.rs:1514, 1902, 1871, 2331` | No gain today. |

### DataFusion 54.0.0 (execution)

| Primitive | Status | Omnigraph references | Upstream | Note |
|---|---|---|---|---|
| `PhysicalExpr`, `ColumnarValue` | used partly | `E/engine/adapters.rs:49-63`; in-memory filters use their own evaluator, `E/engine/expr.rs:140-300` | `physical-expr` `planner.rs:115` | Own evaluator is deliberate (RFC `2026-09-24-shared-expression-model.md:739-748, 1032`). |
| `BinaryExpr` short-circuit and pre-selection | not used | `E/engine/expr.rs:160-179`, `E/engine/operators/filter.rs:25-38`, `E/engine/operators/pair_buffer.rs:221` | `physical-expr` `expressions/binary.rs:273-330, 729-830, 880` | See D-filter-preselect. |
| `NotExpr`, `IsNullExpr`, `CastExpr` | reimplemented with Arrow kernels | `E/engine/expr.rs:180-196`, `E/engine/typed_value.rs:287-337` | `expressions/not.rs`, `is_null.rs`, `cast.rs` | Deliberate: the same RFC and invariant 9 (explicit casts). |
| `InListExpr` | not used | `E/engine/graph.rs:1890-1906` (hash sieve) | `expressions/in_list.rs:197-216` | Not a better fit: it builds one `ScalarValue` per key. |
| Interval analysis, statistics, `PruningPredicate` | not used | planner has no DataFusion dependency (`docs/dev/execution.md:623`) | `physical-expr` `analysis.rs:164` | Deliberate, invariant 9. Lance does the pruning. |
| `SessionConfig` `batch_size`, `target_partitions`, `sort_spill_reservation_bytes` | used | `E/engine/context.rs:215-218` | `execution` `config.rs` | |
| `SessionConfig` `spill_compression` | default (uncompressed) | `E/engine/context.rs:215-218`; asserted in `E/engine/operators/expand_stream/tests.rs:420-428` | `common` `config.rs:551` | See D-spill-report. |
| `SpillManager`, `DiskManager` quota | used inside `SortExec` and `AggregateExec` only | `E/engine/operators/expand_stream.rs:411`, `E/engine/lower.rs:1146`, `E/engine/context.rs:219-235` | `physical-plan` `spill/spill_manager.rs:40` | Graph breakers do not spill (documented, `docs/dev/execution.md:550-553`). |
| `SpillMetrics` (`spill_count`, `spilled_bytes`) | read only by a test probe | `omnigraph-core` `instrumentation.rs:623-666`; not in `E/engine/run.rs:121-137` | `physical-expr-common` `metrics/baseline.rs:275` | See D-spill-report. |
| `RecordBatchStreamAdapter`, `RecordBatchReceiverStreamBuilder` | used | `E/engine/operators/shape.rs:24, 153`, `E/engine/graph.rs:1910` | `physical-plan` `stream.rs:239, 411` | |
| `GlobalLimitExec`, `LimitStream`, `with_fetch` | reimplemented, fetch declared | `E/engine/operators/limit.rs:77-83` | `physical-plan` `limit.rs` | Deliberate (#769). Sort fetch is D1 (#1249). |
| `JoinHashMapU32`, `create_hashes` | mirrored, used | `E/engine/operators/hash_join.rs:137-183` | `physical-plan` `joins/join_hash_map.rs:139`; `common` `hash_utils.rs:1100` | Keep: unique key; #1241 (H2) copied the design on purpose. |
| `GroupValues` / `GroupValuesBytes` | not used | `E/engine/operators/anti_join.rs:411-427` | `physical-plan` `aggregates/group_values/mod.rs:90, 134` | See D-keyset-groups. |
| `AggregateExec`, `GroupsAccumulator` | used for the outer `Aggregate` | `E/engine/lower.rs:1146`, `E/engine/exact_aggregate.rs:72-86` | `physical-plan` `aggregates/mod.rs:727` | Block aggregates are D3 (#1260). |
| `common::collect` | test only | own `drain` at `E/engine/operators/mod.rs:172` | `physical-plan` `common.rs:43` | Own version charges each batch. |
| `MemoryReservation`, metrics (`BaselineMetrics`) | used and wrapped | `E/engine/operators/memory.rs:681-760`, `shape.rs:128, 270` | | |

### Arrow 58.4.1 (execution)

| Primitive | Status | Omnigraph references | Upstream | Note |
|---|---|---|---|---|
| `filter_record_batch` | used | `E/engine/operators/filter.rs:123`, `pair_buffer.rs:225`, `E/engine/graph.rs:1907` | `arrow-select` `filter.rs:229` | One predicate per batch; no per-column filter calls. |
| `take`, `take_record_batch` | used | `E/engine/operators/memory.rs:880, 966`, `E/ordered_cursor.rs:346, 527` | `arrow-select` `take.rs:88, 1110` | `ordered_cursor.rs:346` is A-ordered-interleave. |
| `interleave` | used | `E/engine/operators/memory.rs:1040` | `arrow-select` `interleave.rs:71` | |
| `interleave_record_batch` | not used | `E/ordered_cursor.rs:325-349` | `arrow-select` `interleave.rs:562` | See A-ordered-interleave. |
| `concat_batches` | used | `E/engine/operators/memory.rs:817`, `E/ordered_cursor.rs:344` | `arrow-select` `concat.rs:530` | |
| `lexsort_to_indices` | used | `E/engine/operators/sort.rs:225`, `rank_fuse.rs:145` | `arrow-ord` `sort.rs:930` | Top-k re-sort is D1 (#1249). |
| `cmp`, `like`, `boolean` kernels | used | `E/engine/expr.rs:193, 375-376, 406` | `arrow-ord` `cmp.rs`, `arrow-string` `like.rs` | Per-element `contains` is A2 (#1253). |
| `GenericByteArray::from_iter_values` from `Vec<String>` | used | `E/engine/operators/single_hop.rs:515`, `E/engine/graph.rs:512` | `arrow-array` `array/byte_array.rs:217` | See A-typeindex-dict. |
| `StringDictionaryBuilder` | not used | `E/graph_index/mod.rs:28-36, 233-246` | `arrow-array` `builder/generic_bytes_dictionary_builder.rs:302` | See A-typeindex-dict. |
| `ArrayData::slice` per pick for sizing | used per pick | `E/engine/operators/memory.rs:986-1018` | `arrow-data` `data.rs:593` | See A-hydrate-estimate. |
| `BooleanBuffer::collect_bool`, `set_indices_u32` | not used | `E/engine/graph.rs:1903`, `E/engine/operators/scan/runtime_filter.rs:67, 93` | `arrow-buffer` `buffer/boolean.rs:157, 620` | Measured 1.22 times faster; the hash probe dominates. Not filed. |
| `arrow_json` writer | used | `omnigraph-compiler` `json_output.rs:5, 24` | `arrow-json` `writer/mod.rs` | No hand-written JSON. |
| `RowConverter`, `BatchCoalescer` | not used | none | `arrow-row`, `arrow-select` `coalesce.rs:133` | Deliberate (see below). |

## Ranked findings

Ranking rule: a guarantee or refused work first, then storage calls, then CPU, each weighted by how
common the path is.

### 1. L-scan-limit-pushdown: a query limit over a scan without a filter never reaches Lance (Lance) — S

**Issue:** see "Filed issues".

- **Now.** `limit_over_scan` (`E/engine/lower.rs:355-366`) finds a `Limit` directly over the
  projection over a scan and passes its rows only as the gather size (`E/engine/lower.rs:641-643`,
  `E/engine/operators/scan/pipelined.rs:180`). `NodeRead::configure` (`E/engine/scan.rs:255-281`)
  never calls `Scanner::limit`. The engine stops by dropping the stream.
- **Upstream.** `Scanner::limit` (`dataset/scanner.rs:1956`). With no filter and no ordering,
  `plan_scan_range` (`:3159-3200`) takes `plan_metadata_scan_range` (`:3110-3155`), which keeps only
  the fragments that hold the first k live rows, using manifest row and deletion counts.
- **Gain.** Without the limit, the first poll starts up to `fragment_readahead` fragments (2 ×
  I/O parallelism: 16 locally, 128 on object stores) and schedules their reads, up to the 64 MiB
  I/O buffer, before the engine stops. With it, `limit 10` reads one fragment's first pages. Rows
  are the same: fragments are read in order, and a `limit` without `order` may return any rows.
- **Cost and risk.** Pass the limit only when the scan has no filter, no runtime filter and no
  ranking. Lance pushes nothing when a filter exists, but a runtime filter is applied by the engine
  after Lance, so a Lance limit there would drop rows. DST storage-cost lines and GQT `--measure`
  baselines for such queries would change.
- **Evidence.** Code inspection. #1257's measurements show `limit(1)` with a filter still reads
  20 MB, which matches `:3160-3162`.

### 2. D-filter-preselect: the in-memory filter does not short-circuit (DataFusion) — S to M

- **Now.** `conjoined_mask` (`E/engine/operators/filter.rs:25-38`) evaluates every conjunct over the
  whole batch and ANDs the masks. `evaluate_filter` (`E/engine/expr.rs:160-179`) evaluates both
  sides of `and` and `or` in full. Cross-join and contains-join residuals use the same function
  (`E/engine/operators/pair_buffer.rs:221`).
- **Upstream.** `BinaryExpr::evaluate` (`physical-expr` `expressions/binary.rs:273-330`) with
  `check_short_circuit` (`:729-830`) and `pre_selection_scatter` (`:880`): an all-false left side
  returns at once, an all-true one evaluates only the right side, and when 20 % or fewer rows pass,
  the right side runs only on the selected rows.
- **Gain (measured).** 8,192 rows, `a < c AND s LIKE '%12345%'`:

  | Case | Today | `BinaryExpr` | Pre-selection on the columns the right side reads |
  |---|---|---|---|
  | 5 % pass, 2 columns | 9.9 ns/row | 1.7 ns/row | 1.7 ns/row |
  | 50 % or 100 % pass | 9.9 ns/row | 10.0-10.3 ns/row | 10.5 ns/row |
  | 5 % pass, 32 columns | 9.9 ns/row | 12.5 ns/row | 1.7 ns/row |

  DataFusion filters every column before the right side, which is slower on wide batches. So do it
  inside Omnigraph's evaluator, filtering only the columns the right side reads (the planner has
  them, `crates/omnigraph-planner/src/optimizer.rs:1209`). Do not switch to `PhysicalExpr`.
- **Cost and risk.** A recorded cast runs with `safe: false` and can fail on an out-of-range value
  (`E/engine/typed_value.rs:292-335`). With pre-selection it would no longer fail on rows an earlier
  conjunct removed. No test pins this; the intended semantics need a decision. Open PR #1316 (GQ 3)
  touches `expr.rs` but adds no short-circuit; rebase after it.
- **Evidence.** Measured with a scratch benchmark on the pinned crates; result counts matched. How
  often production queries reach this path is not measured.

### 3. A-typeindex-dict: graph-index id lookups use a SipHash map of owned strings (Arrow) — M

- **Now.** `TypeIndex` (`E/graph_index/mod.rs:12-75`) is a std `HashMap<String, u32>` plus a
  `Vec<String>`. `get_or_insert` (`:28-36`) allocates two `String`s per new id; the build interns
  two ids per edge row (`:233-246`); `from_ids` (`:60-74`) clones every id when the persisted
  artifact loads (`E/graph_index/persist.rs:470-503`). Every input row of every CSR traversal looks
  up its id (`E/engine/operators/single_hop.rs:328-330`, `E/engine/graph.rs:1296-1303, 1648-1650`),
  and every emitted pair's destination column is built from `Vec<String>` with `from_iter_values`
  (`single_hop.rs:515-519`, `graph.rs:495-515`).
- **Upstream.** Keep `dense_to_id` as one `StringArray` and build destination columns with `take`
  (`arrow-select` `take.rs:88`). Intern at build time with
  `StringDictionaryBuilder<UInt32Type>::append` (`arrow-array`
  `builder/generic_bytes_dictionary_builder.rs:302`), which assigns keys in first-seen order, as
  today. Look ids up through a `hashbrown::HashTable<u32>` that compares through the array, the
  pattern `E/engine/operators/hash_join.rs:132-183` already uses.
- **Gain (measured, uuid-shaped ids, 8,192-row batch, best of 5).**

  | Step | 100k ids | 1M ids |
  |---|---|---|
  | Source id lookup, current | 413 µs | 712 µs |
  | Same map with a fast hasher | 212 µs | 391 µs |
  | `HashTable<u32>` over a `StringArray` | 240 µs | 473 µs |
  | Destination column, current | 107 µs | 206 µs |
  | Destination column, `take` | 58 µs | 107 µs |
  | Cold build (500k / 5M edges), current | 217 ms | 3.32 s |
  | Cold build, `StringDictionaryBuilder` | 127 ms | 1.97 s |
  | Artifact load, current | 26.6 ms | 421 ms |
  | Artifact load, `StringArray` and table | 2.4 ms | 60 ms |

  Dictionary memory at 1M ids: about 132 MB to about 80 MB. Most of the lookup gain comes from the
  hasher; switching only the hasher is an S-sized first step.
- **Cost and risk.** Dense-id order must stay first-seen so persisted artifacts stay compatible. The
  indexed-start interner grows during a walk (`single_hop.rs:343-347`, `graph.rs:1734-1735`) and
  needs a builder-backed variant or can stay. Ids over 2 GiB per type need `LargeStringArray`. This
  keeps `Utf8` output and is not H7 from #1241 (dictionary-encoded output, not kept).
- **Evidence.** Measured with a scratch benchmark; end-to-end share not measured.

### 4. D-correlated-fold-streams: a correlated block holds its whole inner output before folding (DataFusion) — S

- **Now.** The per-row correlated path drains the inner plan into a `Vec`, each batch charged
  (`E/engine/operators/anti_join.rs:366-372`), then `absorb_inner_batches`
  (`E/engine/operators/subquery_aggregate.rs:455-479`) folds the batches one at a time. The key-set
  path in the same file already folds as batches arrive (`anti_join.rs:428-448`).
- **Upstream.** The streaming fold `AggregateExec` uses; no new dependency.
- **Gain.** Peak memory goes from the whole inner output (outer rows times fan-out) to one batch.
  Queries the pool refuses today with `query_memory_bytes` would run.
- **Cost and risk.** Folding stays in batch order, so `min`/`max` keep the first signed zero. The
  `FilledSlot` guard must live until the stream ends.
- **Evidence.** Code inspection.

### 5. D-spill-report: spills are invisible in the execution report, and uncompressed (DataFusion) — S

- **Now.** The report row (`E/engine/run.rs:121-137`) has polls, output rows, `drained` and the
  switch. `SpillMetrics` from `AggregateExec` and from the `SortExec` inside the bound-edge `Expand`
  (`E/engine/operators/expand_stream.rs:411`) are read only by a test probe (`omnigraph-core`
  `instrumentation.rs:623-666`). The nested sort is not a plan child, so its metrics do not reach
  the `Expand` row. Separately, `spill_compression` is left at `Uncompressed`
  (`E/engine/context.rs:215-218`); a test asserts the default as setup
  (`expand_stream/tests.rs:420-428`).
- **Upstream.** `MetricsSet::spill_count`, `spilled_bytes`, `spilled_rows` (`physical-expr-common`
  `metrics/mod.rs:245`); `SessionConfig` `spill_compression` (`common` `config.rs:551`), with lz4
  and zstd already compiled in through `arrow-ipc`.
- **Gain.** A spilling query becomes visible (and D1's spilling sort, once it lands). Compressed
  spills use less of the 100 GiB scratch quota.
- **Cost and risk.** Report and explain rows gain fields; replay trace comparison must ignore them.
  Compression ratio and CPU cost are not measured.
- **Evidence.** Code inspection.

### 6. Small items — S each

- **A-ordered-interleave.** `next_ordered_batch` (`E/ordered_cursor.rs:325-349`), on the export
  path, copies each chunk twice: `concat_batches` then `take_record_batch`, on chunks up to 64 MiB.
  `interleave_record_batch` (`arrow-select` `interleave.rs:562`) does it in one copy from the
  `(batch, row)` pairs it already has. Measured: 1,561 to 624 µs on 8 × 1,024 rows with a
  128-dimension vector column; peak drops from 3 to 2 chunk copies. `row_id_at`
  (`ordered_cursor.rs:585-586`) also allocates two `String`s per row to check key order; compare
  `&str`.
- **A-hydrate-estimate.** `interleave_estimate` (`E/engine/operators/memory.rs:986-1018`) calls
  `to_data()` on every one-row batch per column and one `ArrayData::slice` per pick, at least once
  per hydration window and again on each halving (`E/engine/operators/hydrate.rs:459-463,
  553-558`). Measured: the estimate costs 1,598 µs against 545 µs for the interleave it prices;
  summing per-row sizes computed once at fetch time with the existing `row_bytes`
  (`memory.rs:849`) costs 37 µs. Numbers must stay equal to `slice_memory_size`.
- **D-keyset-groups.** The key-set block groups outer keys in a std `HashMap<&str, u32>`, row by row
  (`E/engine/operators/anti_join.rs:411-427`; same pattern in `E/engine/graph.rs:940-952`).
  `create_hashes` plus a `HashTable`, as in `hash_join.rs:178-183`, measured 43.3 to 23.6 ns per
  row (100k outer rows, 1M inner); DataFusion `GroupValues` 27.9. After #1260's float decision,
  `GroupValues` with `GroupsAccumulator` would let this fold lower to `AggregateExec`, which spills.

### Sent to #724 as evidence (not filed separately)

#724 (process admission control) already says memory outside the query pool, including scanner
buffers, must be bounded. The comment adds:

- **L-scan-default-buffers.** Only the streaming node scan sets `batch_readahead` and
  `io_buffer_size`. The streamed edge scan (`E/engine/graph.rs:1871-1883`), ranked scans
  (`E/engine/scan.rs:409`), dependent lookups (`E/engine/operators/scan/input.rs:290-305`),
  `search_filter_member_ids` (`E/engine/scan.rs:670-690`), ordered-cursor scans
  (`E/ordered_cursor.rs:198-222, 493-506`) and the topology build keep Lance's defaults: read-ahead
  of CPU count batches and a 2 GiB I/O buffer on object stores.
- **L-scan-fragment-readahead.** No engine read sets `fragment_readahead`; `ScanTuning` does not
  expose it (`E/table_store.rs:226-395`). The default is 2 × I/O parallelism.
- **L-topology-build-collect (weak).** `GraphIndex::build` (`E/graph_index/mod.rs:212-221`) collects
  every batch of each edge table, uncharged, before interning. The persisted artifact (#544) makes
  this the cold path.

## Deliberate workarounds kept

Each of these looks like an underused primitive and is not. Do not undo them.

- **Per-row `FragmentReader::take` in hydration.** Fence "Hydration reads".
- **Ordered walks sort only `id`, `_rowid`, `_rowaddr` and use `_rowaddr IN` takes; own
  spill-bounded runtime for ordered scans.** Fence "Ordered walks"; invariant 11.
- **`AllEarly` materialization and whole-record projection in the catalog.** Fence "Packed catalog
  record".
- **Engine gathering of streamed batches** (`E/engine/operators/scan/pipelined.rs:166-222`). Runs
  after the runtime-filter sieve (#887); `strict_batch_size` cannot be combined with a byte limit.
- **No `fast_search`.** Invariant 7. **No `LanceTableProvider`, no DataFusion logical plan,
  statistics or interval analysis, one partition.** Invariant 9; RFC
  `2026-09-24-shared-expression-model.md`; `docs/dev/execution.md:623`.
- **Own expression evaluator, `Not`, `IsNull` and casts.** Same RFC (`:739-748, 1032`); invariant 9
  needs explicit casts.
- **Own `Filter`, `Projection`, `Limit`, `CrossJoin`, `HashJoin`; own `drain` and `empty()`.**
  PR #769: report `drained` and charge output to the pool (invariant 11).
- **The hash join's own `HashTable<(u64, u32)>` with `create_hashes`.** #1241 (H2) copied
  DataFusion's design on purpose for a unique key.
- **Engine-side sieve instead of a `key IN` pushdown**, priced by `PUSHDOWN_KEYS_MAX`
  (`E/engine/graph.rs:1852-1869`; #1241, H5).
- **Pool-admitted take and interleave, hand re-batching instead of `BatchCoalescer`, `batch_bytes`
  instead of `get_record_batch_memory_size`.** Copies must be charged per buffer (#935, #769).
- **Canonical key strings instead of `RowConverter`.** Persisted id stability across string
  encodings and ±0.
- **Graph breakers do not spill.** Documented in `docs/dev/execution.md:550-553`.
- **Dictionary-encoded expand output.** H7 in #1241 was prototyped and not kept (allocations rose
  6-9 % at 256-row chunks). A-typeindex-dict keeps `Utf8` output.

## Upstream changes since the pins

- **Lance:** 14.0.2 is the latest stable; 15.0.0 is at beta 16. The earlier notes stand: at the 15
  bump, recheck the "Index segments" and "Ordered walks" fences, L5, and `ScalarIndex::lookup` for
  `id IN` lookups. Not assessed for this area beyond that.
- **DataFusion:** 55.2.0 is out; Omnigraph follows Lance's pin, so no bump is possible on its own.
  Not assessed.
- **Arrow:** 60.1.0 is out (pin 58.4.1, following Lance). Not assessed.

## Evidence

- Scratch benchmarks (out of repo, release builds against the pinned crates only): D-filter-preselect
  (filter short-circuit and pre-selection), D-keyset-groups (key interning), A-typeindex-dict
  (lookups, destination columns, build, artifact load), A-ordered-interleave, A-hydrate-estimate,
  and `collect_bool` (not filed). Synthetic data; ahash stood in for DataFusion's `create_hashes`.
- All other findings are code inspection on both sides at the commits above. The Omnigraph
  workspace was not built and no GQT or DST case was run.
- Coverage: GitHub issue and PR search by file, function and primitive name, plus a local search of
  all issue and PR titles and bodies (the search API rate limit ran out partway; comments were
  searched only for #724 and the issues named here).

## Could not verify

- I/O saved by L-scan-limit-pushdown on a real graph, and how often a query takes the shape
  `Limit` over projection over a scan without a filter.
- How often production queries reach the in-memory filter, the correlated block and the key-set
  block, and the end-to-end share of D-filter-preselect, D-keyset-groups and A-typeindex-dict.
- The peak memory removed by D-correlated-fold-streams on a real graph.
- Spill compression ratio and CPU cost.
- Whether report and explain rows can gain spill fields without a version bump.
- How often persisted graph-index artifacts exist in deployments (decides how much the load gain in
  A-typeindex-dict matters).

## Workflow feedback

- The focus list in the workflow inputs worked as a rotation. Remaining areas not yet taken alone:
  storage and versioning, and branches.
- The search API rate limit is shared by parallel readers; a local dump of issues and PRs was the
  reliable fallback.

## Filed issues

To be filled in after filing.
