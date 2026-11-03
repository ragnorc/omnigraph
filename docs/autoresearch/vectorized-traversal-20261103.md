# Autoresearch: vectorized execution on the read path, 2026-11-03

Query family: the whole read path was mapped; the prototype family is
**traversal** (expand, destination join, multi-hop BFS), the shape the map
shows has the most per-row work.

- Branch: `autoresearch/vectorized-traversal-20261103` on the fork `ragnorc/omnigraph`
- Base commit: `36d3ea08c5289b40653a26752cd0e6c8ef85e6af` (main, 2026-11-03,
  "perf(mutation): a node delete scans each table once ...", #1232)
- Box: Modal dev box (gVisor, 2 cores), Claude Fable 5.1 (`claude-fable-5-1`)
- Prior runs of this workflow: none (first run). The other autoresearch
  branches on the fork (`branch-ops-20261102`, `node-delete-20261102`)
  measured storage requests of writes and merges, not query execution; no
  hypothesis here repeats theirs.
- Pinned mechanics read at the Cargo.lock versions: arrow 58.3.0
  (`arrow-ord` `lexsort_to_indices`, `arrow-select` `take`/`concat`),
  DataFusion 54.0.0 (`AggregateExec`, `HashJoinExec` hashing), Lance 13.0.0
  (scanner batch sizing, `take`).

## Result

The read path is already batch-at-a-time Arrow everywhere except the
traversal: scans, filters, projections, sorts and aggregates run on Arrow
kernels end to end, and the per-row work the map found sits at three
boundaries of the traversal, where dense ids become strings, strings are
hashed back into rows, and per-source or per-edge state is kept in heap
objects. Five patches at those boundaries are kept (H1, H4, H2, H5, H3); the
dense-id redesign was prototyped end to end on the single-hop shape and is
reported as a proposal with its numbers (H7), not kept.

Counters, baseline → final, medians of 20 back-to-back runs on a quiet box
(`results/quiet_*.jsonl`, `summarize.py quiet_baseline quiet_final`):

| scenario (100k people, fanout 4) | rows | allocations | bytes | CPU ms | wall ms |
|---|---:|---:|---:|---:|---:|
| hop1 (`$p knows $q`) | 399,995 | 1,364,078 → 664,378 (−51%) | 189.5 MB → 159.4 MB (−16%) | 636 → 480 | 422 → 260 |
| hop1_limit (`limit 100`) | 100 | 276,544 → 127,018 (−54%) | −8% | 137 → 90 | 84 → 52 |
| hop1_count (`$p.team, count($q)`) | 10 | 1,733,713 → 734,391 (−58%) | −21% | 575 → 496 | 318 → 248 |
| hop2_chain (two expands) | 1,599,436 | 9,183,184 → 3,587,570 (−61%) | 946.9 MB → 694.4 MB (−27%) | 3,162 → 3,193 | 1,348 → 1,476 |
| bound_edge (`$p $e:knows $q`, `$e.weight`) | 400,000 | 82,976,399 → 17,779,906 (−79%) | 16.6 GB → 3.8 GB (−77%) | 26,279 → 9,935 | 12,141 → 6,165 |
| no_friends (`not { $p knows $q }`) | 1,856 | 588,225 → 88,228 (−85%) | −35% | 101 → 66 | 84 → 51 |
| popular (`count { $p knows $q } > 4`) | 37,175 | 588,370 → 88,379 (−85%) | −34% | 98 → 72 | 82 → 61 |
| scan_name, grouped, topk | | unchanged (±0.0%) | unchanged | ±15% noise | |

| scenario (10k people, fanout 4) | allocations | bytes | CPU ms | wall ms |
|---|---:|---:|---:|---:|
| hop2_bfs (`knows{2,2}`) | 519,142 → 278,732 (−46%) | −2% | 298 → 178 | 184 → 102 |
| hop3_bfs (`knows{3,3}`) | 1,674,487 → 922,138 (−45%) | +1% | 1,264 → 622 | 646 → 339 |
| bound_edge | 1,656,322 → 650,281 (−61%) | −75% | 735 → 211 | 251 → 141 |

On the skewed fixture (10k people, 16 edges each, Zipf destinations) the
same changes give −30% to −70% allocations on the traversals and −56% on
bound edges; the non-traversal queries are unchanged on every fixture.

What the traversal's cost grows with now, against what it should:

- a single hop grows with rows touched and output batches, no longer with
  rows × columns (H1) or with a string allocation per destination row (H2);
  the 256-row output chunk is now the largest per-row term left;
- a multi-hop walk grows with edges examined, not with three pool charges
  per edge and a `String` per pair (H3); it still holds every source's
  frontier, so the pool, not the walk, bounds the 100k-row BFS;
- a bound-edge or unindexed edge probe grows with edge rows read, no longer
  with keys × fragments (H5);
- a correlated block's verdict grows with outer rows, no longer with six
  allocations per row (H4).

The main finding: vectorization of the operators themselves is done; what is
left of per-row work is the *identity representation* between operators.
Carrying dense ids end to end (H7) removes the hashing and the string
rebuilds (CPU −10 to −20% and bytes −5% at 100k) but, at 256-row chunks, adds
an Arrow object per chunk per consumer that costs more allocations than it
saves (+5 to +9%); it pays off only with larger traversal chunks and
consumers that accept dense ids, which is the design proposal below.

## Execution map: where columnar data becomes row data

A read runs as parse → typecheck → typed IR → planner (`omnigraph-planner`:
logical tree, `predicate_pushdown`, `aggregate_pushdown`,
`projection_pushdown`, `late_materialization`, physical plan, acceptance) →
`engine/lower.rs` (one DataFusion `ExecutionPlan` operator per plan node)
→ `engine/run.rs` (one `SessionContext`, one partition, 8,192-row batches,
collected into a `QueryResult` of `RecordBatch`es) → JSON at the CLI/HTTP
boundary. Rows between operators are flat Arrow `RecordBatch`es whose
columns are named `<binding>.<property>`; there is no factorized or
dictionary representation between operators, and node identities are
`Utf8` strings end to end.

Per operator, what runs on batches and what falls back to rows:

| Operator (file) | Columnar end to end? | Per-row work on the critical path | Boundary |
|---|---|---|---|
| `ScanExec`, pipelined (`operators/scan/pipelined.rs`, `engine/scan.rs`) | Yes. Lance decodes batches of at most 8,192 rows under a byte target; `prefix_batch` and `conform` rename columns (schema-only); kept batches gather under one charge and are concatenated once (`WorkMemory::concat`, one copy) until they reach the session's batch rows. | None. A `RuntimeFilter` sieve (`contains` join) walks each text with an Aho-Corasick automaton: per row by necessity. | Lance → Arrow at the scanner; no row form. |
| `ScanExec`, dependent `id_lookup` (`operators/scan/input.rs`) | Per 256-row slice: one Lance read `id IN (slice ids)`. | Per slice: a `HashSet<&str>` dedupe, a `Vec<Expr>` of `lit` per id for the `IN` list, a `HashMap<&str,u32>` of the destination rows, two `take`s and an `hconcat`. The per-row hash work is on strings. | Arrow ids → `String` list → DataFusion `Expr` → Lance filter → Arrow. |
| `FilterExec` (`operators/filter.rs`, `engine/expr.rs`) | Yes: `arrow_ord::cmp`, `and_kleene`/`or_kleene`, `arrow_string::like`, `filter_record_batch`. | A literal or parameter operand is materialized as a full-length constant array per batch (`typed_literal_to_array(rows)`, `ScalarValue::to_array_of_size`) instead of a `Datum` scalar. List membership (`evaluate_contains_filter`) is per row and per element: a one-row `slice` and a `cmp::eq` call per element. | None for scalar predicates; `contains` on a list property is a row loop. |
| `ProjectionExec` (`operators/projection.rs`, `adapters.rs::GqProjectionExpr`) | Yes: column clones, `StructArray::try_new` for a bare `$p`, kernels for expressions. | None. | None. |
| `ExpandExec`, single unbound named hop (`operators/single_hop.rs`) | Partly. Source columns reach the output through one `take` per chunk (`align_sources`); the walk over CSR slices is a tight loop over `u32`. | Per input row: `csr.src_idx.to_dense(&str)` (a `HashMap<String,u32>` lookup, hashing the id string) and `memory.slice_bytes(&batch, row, 1)`, which calls `column.to_data()` on every column of the batch for that one row (an `ArrayData` clone per column per row, each with its own heap allocations) to estimate the row's bytes. Per output row: the destination id is copied out of the CSR dictionary (`Vec<String>`) into a fresh `StringArray`. Output chunks hold at most `EXPAND_OUTPUT_ROWS` (256) rows. | Dense `u32` ids inside the walk → `Utf8` strings at the chunk boundary, re-hashed by the consumer. |
| `ExpandExec`, indexed start (`single_hop.rs` `Source::Indexed`, `graph.rs::scan_neighbor_map`) | The edge probe is one Lance scan per input batch (`key IN (batch ids)`). | Per edge row read: two `intern` calls (a `HashMap<String,u32>` lookup, a `String` copy on first sight) and a `HashMap<u32,Vec<u32>>` entry push; per input row an `intern` and the same `slice_bytes`. | Arrow edge batch → per-row `String` interner and `HashMap` → dense walk → `Utf8` chunk. |
| `ExpandExec`, multi-hop BFS (`graph.rs::execute_expand_bfs`) | No. The input is drained whole (`Breaker`); pairs are emitted in `batch_rows` chunks and sliced to 256 rows (`expand_stream.rs::emit_pairs`). | Per source row: two `HashSet<u32>`s (`visited`, `seen_dst`) and a frontier `Vec`. Per neighbor examined: `memory.check()` or `charge_traversal(1)`, up to three `entries::<u32>(1)` pool charges (atomics behind a lock), two hash-set probes and inserts. Per emitted pair: `dst_id.to_string()` into a `Vec<String>`, later copied again into a `StringBuilder`. | Dense ids inside the BFS → `Vec<String>` → `StringArray` → re-hashed by the join. |
| `ExpandExec`, bound edge (`graph.rs::produce_bound_edge_pairs`, `expand_stream.rs::emit_bound`) | Pairs are produced as batches of `(source ordinal, destination id, edge columns)` and sorted by DataFusion's `SortExec` per input batch, then `take`n in 256-row chunks. | The pair production reads the edge table per source window; sorting is DataFusion's row-format sort. | Arrow throughout, with a per-batch blocking sort. |
| `HashJoinExec` (`operators/hash_join.rs`) | The build is drained into one batch; the probe is joined per batch with two `take`s and an `hconcat`. | Build: `row_by_id: HashMap<String,u32>` with `id.to_string()` per build row (one heap allocation per destination row, and a second hash of a key the expand had as a dense index moments earlier). Probe: per row `HashMap<String,u32>::get(&str)` (hash + compare). | The second string-hash boundary of a traversal: dense `u32` → `Utf8` (expand) → string hash (join). |
| `AntiJoinMaskExec` (`operators/anti_join.rs`, `subquery_aggregate.rs`) | The outer input is drained into one batch; the bulk CSR degree mask is a vectorized walk; the correlated inner tree runs over tagged rows. | `SubqueryAggregate::absorb` folds inner rows one at a time (`counts[tag] += 1`, `fold(column,row)` building a `ScalarValue` per row for value aggregates); `keep_mask` builds a `ScalarValue` per outer row. `key_set_mask` hashes `Utf8` keys per row. | Arrow → per-row scalars → `BooleanArray`. |
| `CrossJoinExec`, `ContainsJoinExec` (`operators/cross_join`, `contains_join`) | Pairs go through a `PairBuffer`; filters are evaluated with the kernels above. | The contains join tests texts per row against the automaton by necessity. | Arrow throughout. |
| `SortExec` (`operators/sort.rs`) | Yes: `lexsort_to_indices` then one `take` per column (`permute`); top-k merges each batch with the kept rows (`concat` + sort with limit). | The GQ sort always carries at least two keys (the order key and the identity tie-break), so arrow takes the `sort_fixed_column::<N>` comparator path (a dynamic-dispatch comparison per pair) rather than the single-column `sort_to_indices` fast path or the row format DataFusion uses. | None. |
| `LimitExec` | Yes (slices). | None. | None. |
| `AggregateExec` (DataFusion) | Yes: DataFusion's vectorized group-by over the row format. Arguments are `GqProjectionExpr`s evaluated per batch; integer `sum` casts to `Decimal128` for `ExactIntegerUdaf`. | None beyond DataFusion's own accumulators. | None. |
| `HydrateExec` (`operators/hydrate.rs`) | No: one `FragmentReader::take` decode task per deferred row, each row copied at its own size, then `interleave_once` into the output. | Per row by Lance's contract (a decoded row may slice a buffer of the whole read; the engine charges each row). | Lance per-row take → Arrow. |
| `MetadataCountExec`, `CatalogueExec` | Yes. | None. | None. |
| Memory accounting (`operators/memory.rs`) | Per batch: `hold()` walks every buffer of every column through `to_data()` and registers each by allocation identity in a `HashMap` under a lock. | Per batch, not per row, except the `slice_bytes` use above. Its cost is proportional to columns × batches, so small output chunks (256 rows) pay it 32 times more often than scan batches (8,192 rows). | — |
| Graph index build (`graph_index/mod.rs::GraphIndex::build`) | No: `get_or_insert(&str)` per edge endpoint builds the `HashMap<String,u32>` dictionaries, then `CsrIndex::build`. One-time per table version, cached. | Per edge row, two string hashes and up to two `String` copies. | Arrow edge columns → `Vec<String>` dictionary + CSR. |
| Result boundary (`exec/query_doors.rs`, `omnigraph_compiler::result::QueryResult`) | Batches are returned as Arrow; `to_json_bytes`/`to_json_lines` convert per row at the CLI/HTTP edge. | Per row by the output format's nature; outside the engine. | Arrow → JSON. |

Three boundaries carry the traversal's per-row cost:

1. **Expand input: `Utf8` source ids → dense `u32`** by a per-row `HashMap<String,u32>` lookup, plus a per-row `ArrayData` clone for byte accounting (`single_hop.rs`).
2. **Expand output: dense `u32` → `Utf8` destination ids** rebuilt from the dictionary per chunk (`single_hop.rs::Walk::flush`, `expand_stream.rs::emit_pairs`, and in the BFS a `String` per pair before that).
3. **Hash join: `Utf8` → string hash** again on both sides, with a `String` allocation per build row (`hash_join.rs::build_side`).

Everything else on the read path is batch-at-a-time Arrow already.

## Method

- Instrument: `crates/omnigraph-gqt/src/bin/vectorized_instrument.rs`, a
  counting global allocator (allocations and bytes requested, process-wide)
  around `Session::query_inspected`, with the run's own report
  (`actual_rows` and the new `output_batches` per operator). Primary score:
  allocations and bytes, median of 10 measured runs after 2 warm-ups; the
  counts are deterministic to within a few units. Secondary: process CPU time
  (`CLOCK_PROCESS_CPUTIME_ID`, which ticks at 10 ms on this gVisor box, so the
  mean over the 10 runs is reported) and wall time (median and range),
  indicative only; several runs overlapped with release builds on the same 2
  cores, so CPU and wall are compared only in the final quiet re-run.
- Scenarios: `benchmarks/autoresearch/vectorized/` (`gen.py` writes the
  fixtures; `queries.txt` holds 18 read shapes; `run_grid.sh` and
  `summarize.py` run and render). Dimensions swept: rows (1k, 10k, 100k
  people), fanout (1, 4, 16 edges per person), skew (uniform versus Zipf 1.25
  destinations), row width (a 27-byte versus 1,080-byte `bio`), node
  fragments (3 versus 100 commits), edge fragments (10 versus 100 commits),
  hops (1, 2 as a chain of two expands, 2 and 3 as a BFS), selectivity
  (pushed `age >= 90`, a destination filter, an in-memory two-binding filter,
  a `limit 100`), bound edges, and correlated blocks (`not {}`, `count {} >
  4`). Every fixture is built by the GQT seed generator from the same seed.
- Builds: release profile with the workspace's `RUSTFLAGS` cleared, as the
  benchmark commands do; one binary per hypothesis, snapshotted before the
  next change.
- Gates per kept change: the owning unit tests, the engine's `engine_v2`
  integration module, GQT parity against the frozen reference engine
  (`cargo test -p omnigraph-gqt`), the DST scenarios and cost golden, `cargo
  fmt`, both Clippy graphs. Results are in the Gates section.

## What each operator's cost should grow with

The expected shape, then what the baseline measured (allocations per output
row at 10k people, fanout 4, and at 100k):

| Operator | Should grow with | Measured (baseline) |
|---|---|---|
| Scan | batches × columns; Lance's decode is per page | 0.55 allocations per row scanned (Lance's own decode path; `scan_name` 100k: 58,967 allocations, 13 batches); flat in fragments |
| Filter, Projection | batches × columns | no row term visible: `filter_pushed` and `scan_name` differ by a constant |
| Sort (top-k and full) | rows log rows in comparisons; allocations per batch | +0.5 allocations per row over the scan |
| Aggregate | groups, plus DataFusion's per-batch state | 1.6 allocations per row (`grouped` 100k: 157,509) |
| Single-hop expand + hash join | rows touched (sources + edges) and output batches; allocations per batch | **3.4 allocations per output row** (`hop1` 100k: 1,364,110 for 399,995 rows), bytes 474 per row; rows per batch 255 |
| Multi-hop BFS + hash join | rows touched per hop | **3.2 allocations per output row** (`hop2_bfs` 10k: 519,174 for 160,110 rows); the 100k and fanout-16 cases refuse with `query_memory_bytes` (the per-source sets and emitted strings exceed the 150 MiB pool) |
| Bound-edge expand | edges matched | **27 → 41 → 207 allocations per output row** as the graph grows from 1k to 100k people, and 3 KB → 41 KB per row: grows with the *number of source keys* and with the *number of edge fragments* (10 → 100 edge commits at 10k people: 1.66M → 8.15M allocations) |
| Anti-join (bulk CSR degree) | outer rows (one array lookup each) | **5.9 allocations per outer row** (`no_friends` 100k: 588,247) |

## Baseline

`summarize.py baseline` renders the complete table from
`results/baseline.jsonl` (8 fixtures × 18 queries). The rows that drive the
hypotheses, at 100k people and fanout 4 (10k for the BFS, which does not fit
the pool at 100k):

| query | rows | allocations | bytes | cpu ms | wall ms | operators rows/batches |
|---|---:|---:|---:|---:|---:|---|
| scan_name | 100,000 | 58,967 | 18,084,643 | 33 | 31 | Scan 100000r/13b; Projection 100000r/13b |
| grouped | 10 | 157,509 | 53,159,554 | 61 | 58 | Scan 100000r/13b; Aggregate 10r |
| topk | 10 | 111,186 | 41,160,123 | 69 | 47 | Scan 13b; Projection 13b; Sort 10r/1b; Limit |
| hop1 | 399,995 | 1,364,110 | 189,546,537 | 562 | 342 | Scan 100000r/13b; Expand[csr] 399995r/1569b; Scan 100000r/13b; HashJoin 399995r/1569b; Projection 1569b |
| hop1_limit | 100 | 276,448 | 54,571,432 | 114 | 86 | Scan 8256r/1b; Expand 768r/3b; Scan 100000r/13b; HashJoin 256r/1b; Limit |
| hop2_chain | 1,599,436 | 9,182,929 | 946,910,455 | 3,607 | 1,627 | two Expand[csr] + HashJoin pairs, 7010 output batches |
| hop2_bfs (10k) | 160,110 | 519,174 | 57,132,553 | 338 | 203 | Expand[csr] 160110r/626b; HashJoin 626b |
| hop3_bfs (10k) | 637,986 | 1,674,443 | 197,707,524 | 1,326 | 739 | Expand[csr] 2493b; HashJoin 2493b |
| bound_edge | 400,000 | 82,978,220 | 16,558,028,192 | 30,716 | 13,252 | Scan 13b; Expand 400000r/1569b; Scan 13b; HashJoin 1569b |
| no_friends | 1,856 | 588,247 | 57,510,488 | 120 | 90 | Scan 13b; AntiJoinMask 1856r/1b |

Two facts of the map are visible in the numbers: every traversal leaves the
expand in 256-row batches (`1569b` for 400k rows) while scans run at 8,192,
so every downstream operator pays its per-batch work 32 times more often; and
the per-row terms are all on the traversal path.

## Hypotheses

One change per attempt, each committed separately on the branch; counters are
medians over 10 runs; "kept" means every targeted counter dropped, no other
scenario rose, and the gates passed.

### H1. Size expand rows in one pass per column — kept

- Where: `operators/memory.rs` (`WorkMemory::row_bytes`), `operators/single_hop.rs`, `operators/pair_buffer.rs`.
- What: the single-hop expand and the pair buffer estimated each input row's bytes with `slice_bytes(batch, row, 1)`, one `ArrayData` clone per column per row (two heap allocations each). `row_bytes` reads the same numbers off each column's layout in one pass (a constant per fixed-width column, offset deltas for variable-width ones, children summed for structs; a unit test pins equality with the per-row slice for every layout, sliced batches included).
- Counters (baseline → H1): `hop1` 100k 1,364,110 → 764,011 allocations (−44%), bytes −17%; `hop1_count` 100k −52%; `hop2_chain` 100k 9,182,929 → 3,783,621 (−59%); `hop1_limit` 10k −58%; every single-hop scenario on every fixture dropped 10–66%; BFS, bound-edge, scans, sorts and aggregates unchanged (±0.1%).
- CPU and wall: within noise during the overlapped run; see the quiet re-run.
- Gates: unit test `row_bytes_equals_the_per_row_slice_for_every_layout`; the rest in the Gates section.
- Source: none needed; the map found it.

### H4. Evaluate a correlated block's predicate over all outer rows at once — kept

- Where: `operators/subquery_aggregate.rs` (`BoundComparison::holds_all`, `keep_mask`), `graph.rs::bulk_anti_join_mask`.
- What: the anti-join compared each outer row's count as a one-row array (`to_array_of_size(1)`, a cast chain, `compare_arrays`, six allocations per row). The counts, or the finalized aggregates, now form one column and the bound predicate runs once over it (the constant right operand broadcast once with `take`); a null verdict reads `false` as before.
- Counters (H1 → H4): `no_friends` 100k 588,265 → 88,240 allocations (−85%), bytes −35%; `popular` 100k −85%; 10k fixtures −78%; 1k −43%; no other query moved.
- CPU and wall: `no_friends` 100k 106 → 64 ms CPU, 98 → 50 ms wall in the overlapped run.
- Gates: the module's five unit tests (two caught a wrong null-mask conversion on the first attempt), the rest in the Gates section.
- Source: DuckDB and Velox selection vectors (the predicate as one vector operation over the block's results); the map.

### H2. Hash the join's ids in bulk, keep the build side in a borrowing table — kept

- Where: `operators/hash_join.rs`; `hashbrown` 0.17 (DataFusion's own version) added to the engine's dependencies.
- What: the destination hash join copied every build id into a `HashMap<String, u32>` and hashed each probe id through it. Build and probe ids are now hashed in bulk with DataFusion's `create_hashes` (one `u64` per row), and the build side is a `hashbrown::HashTable<(hash, row)>` that compares through the Arrow column, the shape of DataFusion's `JoinHashMap`; the first row of a duplicate id still wins, nulls still never match.
- Counters (H4 → H2): `hop1` 100k 763,841 → 664,507 allocations (−13%; one per destination row), `hop1` 10k fanout 1 −21%, `hop2_chain` 100k −5%; bytes +0.4% to +2.5% on the BFS cases (the probe's hash buffer, 8 bytes per row per batch, replaces the build's strings; the BFS cases have a large probe and a 10k-row build). Stated and accepted: the allocation count is the targeted counter and the byte change is a buffer the operator could reuse across batches.
- CPU and wall: `hop1` 100k 544 → 466 ms CPU, 311 → 240 ms wall in the overlapped run.
- Gates: `engine_v2` integration module (14 tests) on the H1+H4+H2 tree; the rest in the Gates section.
- Sources: DataFusion's `create_hashes` and `JoinHashMap` (SIGMOD 2024 paper and the pinned 54.0.0 source); DuckDB `JoinHashTable::Hash`; Birler et al. 2024 for the build-side layout (not borrowed).

### H5. Sieve an unindexed edge scan's keys in the engine — kept

- Where: `graph.rs::scan_edges_stream` (bound-edge producer, indexed single hop, indexed BFS hops).
- What: every edge probe pushed `key IN (keys)` into Lance. Lance 13 (`io/exec/filtered_read.rs`) clones that `Expr` for every fragment and plans it to a physical expression per fragment, so a scan over an edge table whose key column has no usable BTREE paid a copy and a hash set of every key per fragment: the bound-edge expand's allocations grew with keys × fragments (10 → 100 edge commits at 10k people: 1.66M → 8.15M; 1k → 100k people: 27 → 207 allocations per output row). Where `key_column_index_coverage` reports no usable index the scan now reads the projected columns unfiltered and the engine keeps the rows whose key is in one `HashSet` of the keys (null keys drop, as the `IN` list dropped them); an indexed key column still pushes the list, which is what makes it index lookups.
- Counters (H2 → H5): `bound_edge` 100k 82,880,858 → 17,777,858 allocations (−79%), 16.5 GB → 3.7 GB allocated (−77%); 10k fanout 4: −61% / −75%; wide rows −82% / −87%; 100 edge fragments at 10k: 1,764,746 → 768,538 (and 802,916 on the edge-fragment sweep fixture against 8,154,608 before, so the fragment term is gone); `hop1` on the 1k fixture, whose plan takes the indexed start, −61%; every other scenario unchanged.
- CPU and wall: `bound_edge` 100k 28.3 → 8.8 s CPU, 13.1 → 5.7 s wall; 10k 675 → 250 ms CPU.
- Gates: the traversal integration tests (36) ran on the tree with H5 and H3 applied; the rest in the Gates section.
- Source: Lance 13.0.0 `filtered_read.rs` (read at the pinned version); the per-fragment sweep fixture `n10k_f4_efrag100`.

### H3. Multi-hop BFS without per-source sets, per-edge pool charges or per-pair strings — kept

- Where: `graph.rs::execute_expand_bfs`, `destination_column`; `expand_stream.rs::emit_pairs`; `ahash` added to the engine's dependencies.
- What: the BFS kept one `HashSet<u32>` visited set and one emitted set per source row, charged the pool up to three times per neighbour examined (`entries::<u32>(1)`: a pool lock each), and copied every emitted destination id into a `String` and then again into a `StringBuilder`. Now: both sets are one table each over `(source << 32) | node` keys; a node's neighbours are admitted once per node, before its adjacency slice is walked; emitted destinations are dense `u32`s resolved to one `Utf8` column from the walk's dictionary at chunk emission. Emission order, set semantics, hop bounds, the mid-traversal switch to CSR (the sets are translated as before) and the budgeted path's per-neighbour work charge are unchanged.
- Counters (H5 → H3): `hop2_bfs` 10k fanout 4 509,945 → 278,749 allocations (−45%), bytes −3%; `hop3_bfs` 10k 1,667,740 → 922,772 (−45%); fanout 1 −32%; the 1k fixture −40% / −43%; single hops, chains, bound edges, scans and blocks unchanged.
- CPU and wall: `hop2_bfs` 10k 336 → 178 ms CPU, 219 → 98 ms wall; `hop3_bfs` 10k 1,257 → 805 ms CPU, 712 → 461 ms wall (overlapped run).
- Not fixed: the BFS at 100k people (1.6M pairs) and at fanout 16 still refuses with `query_memory_bytes` at the 150 MiB pool, as before; the breaker drains its input and retains every source's frontier, so the bound is on the shape, not on the per-edge work.
- Gates: the traversal integration tests (36) on the tree with H5 and H3; the rest in the Gates section.
- Sources: Ligra (frontier as integer ids, visited as one dense structure), Kuzu's recursive query execution (dense per-vertex arrays), DuckPGQ (bitset lanes per source).

## Redesign proposal: dense destination ids through the traversal pipeline (H7, prototype)

The patches above climb to the best version of the current design, in which
an expand turns dense ids into strings at its output and the next operator
hashes those strings back into rows. The per-row work that is left follows
from that choice, not from any one operator: strings are rebuilt per chunk,
hashed per probe row, and copied per `take`. The alternative the prior art
converges on (Kuzu's `internalID_t`, Gupta et al.'s positional ids, DuckPGQ's
dense rowids and "perfect join", Velox's value ids) is to keep node identity
dense inside the pipeline and resolve it to the string at the edges.

Omnigraph's invariants rule out persisting an integer identity (identity is
the accepted schema's `@key`/`__id`; a derived index may not become a
logical precondition). What they allow is a *per-snapshot* dense id: the
graph index's `TypeIndex` is already a dictionary from `__id` to a dense
`u32`, keyed on the edge tables' physical identity and version, so a dense
id is valid for exactly the snapshot a query reads. The prototype makes that
dictionary the value array of an Arrow `DictionaryArray<UInt32, Utf8>` and
emits the expand's destination id column in that encoding:

- `graph_index::TypeIndex::id_array` is the ids in dense order as one shared
  `StringArray`, built on first use (one allocation per type per index).
- `ExpandExec` declares `<dst>.__id: Dictionary(UInt32, Utf8)`. A CSR walk
  (single hop or BFS) emits its dense destinations as the dictionary's keys
  with no copy; an indexed walk encodes its interner's strings per chunk
  (one `cast`); a bound edge's destination, which comes off the edge table as
  `Utf8`, is encoded per chunk. The planner's `Expand` properties carry only
  the input schema (pipeline-node schemas are conservative), so the typed
  plan structure is unchanged and the column type is the engine's one
  declaration.
- `HashJoinExec` recognizes a dictionary probe: once per distinct value
  array it looks every dictionary value up in the build table (one bulk hash
  of the dictionary, N lookups), then every probe row is one array index,
  with no hashing and no string comparison; a probe over another value array
  (an interner's chunk) falls back to the bulk hash path.
- Every consumer that needs the strings (`id_lookup` candidates, the key-set
  anti-join, a projected `@id`, `count($var)`, a second expand's source ids,
  the bound-edge producer, the bulk anti-join) takes them through one
  `id_strings` cast per batch, so no operator sees a row loop added. The
  memory accounting charges a dictionary row as its key and the shared value
  array once, by allocation identity.

The prototype is the patch `vectorized-traversal-20261103-dense-ids.patch`
beside this report (895 lines over twelve engine files; it applies on the
branch head and compiles), with its measurements in `results/h7.jsonl` (first
build) and `results/h7b.jsonl` (corrected build). It is not in the engine:
its targeted counter did not drop.

What it measured (H3 → prototype, medians of 10, the overlapped runs):

| fixture | query | allocations | bytes | CPU ms | wall ms |
|---|---|---:|---:|---:|---:|
| 100k, fanout 4 | hop1 | 664,648 → 708,015 (+6.5%) | 159.4 MB → 152.1 MB (−4.6%) | 528 → 445 | 270 → 222 |
| 100k, fanout 4 | hop1_count | 734,455 → 782,634 (+6.6%) | −4.2% | 479 → 383 | 245 → 209 |
| 100k, fanout 4 | hop1_dst_filter | +5.0% | −3.7% | 628 → 527 | 252 → 216 |
| 100k, fanout 4 | hop2_chain | 3,587,752 → 3,870,663 (+7.9%) | 694.4 MB → 659.2 MB (−5.1%) | 2,782 → 2,530 | 1,255 → 1,088 |
| 10k, fanout 4 | hop1 | +5.9% | −4.0% | 46 → 43 | 34 → 29 |
| 10k, fanout 4 | hop3_bfs | +9.1% | −4.9% | 805 → 660 | 461 → 369 |
| 10k, fanout 16, Zipf | hop1 | +7.6% | −6.5% | 101 → 152 | 64 → 88 |
| every fixture | bound_edge | +0.8% to +2.3% | +1.6% to +7.1% | ±5% | ±10% |
| every fixture | scans, sorts, aggregates, blocks | unchanged | unchanged | | |

Reading: the per-row hashing of the join is gone (the CPU and wall gains at
100k, where the probe is 400k rows over a 100k dictionary), and the per-chunk
string rebuild is gone (the bytes). The allocation count rises because the
pipeline is still 256-row chunks and each chunk now carries one more Arrow
object (the dictionary array) at the expand, the join's keys, and a cast at
every consumer that still wants strings; at 10k rows and on the skewed
fixture that per-chunk cost outweighs the hashing saved. The bound-edge
expand pays a per-chunk encode of its `Utf8` destination for nothing. So the
dense-id design pays off only together with larger chunks (H6 below, not
attempted) and with consumers that take dense ids rather than casting.

The first build also found a rule the design needs: a chunk's size must
exclude the shared dictionary. The batch sender measured chunks with
`get_array_memory_size`, which counts the dictionary's value array (1.5 MB at
100k ids) in every chunk, halved every chunk down to one row (399,995
one-row batches; `hop1_count` 905,845,164 allocations and 53 GB, 62 s), and
the 150 MiB pool refused `hop1`. The corrected build sizes a
dictionary-bearing chunk by its own buffers (`results/h7b.jsonl`).

What a full implementation still needs:

1. The planner should declare the encoding on the `Expand` node (a typed
   decision, printed by `explain`), and plan acceptance should check it, so a
   replayed plan cannot disagree with the operator; today the engine's
   `expand_output_schema` is the one declaration.
2. The hash join's dense path should be a declared switch on the `HashJoin`
   node (`probe: dense | hashed`) with its own report row, as `fallback:
   id_lookup` is.
3. The destination scan's rows could be *ordered by dense id* when the
   destination table is the dictionary's own type, which makes the build side
   an identity map and removes the build-side hashing too (DuckPGQ's perfect
   join); that needs the scan to carry the dictionary or the planner to pin
   the node table's row order, which it does not today.
4. A chain of expands could carry the dictionary through the join (the join
   output's `<dst>.__id` from the destination table is `Utf8` again), so the
   second expand's source lookup becomes an array index; the dependent scan's
   `id IN (...)` path would take dense ids as well.
5. The bound-edge producer should read the edge table's `__dst` as a
   dictionary over the same `TypeIndex` instead of encoding per chunk.
6. The GQT `--- expect plan` and explain documents should print the encoding;
   the storage cost golden is unaffected (no storage call changes).

## Wall clock

No qualified benchmark environment was available: the box is gVisor with 2
cores, which `omnigraph-bench` refuses, so no benchmark record was produced.
The quiet re-run (baseline binary and final binary alternated per fixture,
20 repeats after 3 warm-ups, nothing else running) is the timing evidence
above; its CPU column is the mean of 10 ms ticks and its wall column the
median. Across the three quiet fixtures every traversal scenario's wall
median fell except `hop2_chain` at 100k (1,348 → 1,476 ms, within its
run-to-run range of several hundred milliseconds); the largest wall gains are
`bound_edge` (−49% at 100k), `hop3_bfs` (−47%), `hop2_bfs` (−44%) and `hop1`
(−38% at 100k). These are indicative, not records.

## Gates

(filled in from the gate run)

## Not attempted, with the reason

- **H6. Expand chunk size.** Every traversal leaves the expand in chunks of
  `EXPAND_OUTPUT_ROWS` (256) rows while scans run at 8,192, so each
  downstream operator pays its per-batch work (the pool's per-batch
  registration of every buffer, the Arrow object graph, the join's two
  `take`s, the projection) 32 times more often, and the prototype above shows
  that per-chunk cost is now what dominates the traversal's allocation count.
  Every cited engine sits above 256 (X100 1,024, DuckDB 2,048, Velox 1,024,
  DataFusion and Lance 8,192; Kersten et al. find only vectors below 64 or
  above 64K rows costly). The constant also bounds the two-batch producer
  channel and the memory the docs promise for a `Limit` that stops early, so
  changing it is a documented-contract change with several owners
  (`execution.md` names 256 in four places); it needs its own run with the
  memory tests, not an afterthought here.
- **Sort through the row format**, **the per-row list `contains`**, **scalar
  literals as `Datum`s**: measurable but off the traversal path, and small
  next to the terms above.
- **Lance's own 0.55 allocations per row scanned** (`scan_name`): inside the
  substrate's decode, and not a graph-engine boundary.

## What could not be verified

- Wall-clock on the qualified benchmark environment: this box is gVisor with
  2 cores, which the benchmark harness refuses, so every CPU and wall number
  here is from the instrument and indicative only. The quiet re-run below
  is the cleanest timing this run has; no number here is a benchmark record.
- The process CPU clock ticks at 10 ms on this host, so per-run CPU is a
  mean over repeats, not a median.
- The BFS at 100k people and at fanout 16 still refuses with
  `query_memory_bytes` at the 150 MiB pool before and after H3, so its
  counters at those sizes are not measured.
- S3 and Azure object-store suites were not run (no configured backend on
  the box); nothing here changes a storage call, and the DST storage-cost
  golden is in the gates.
- The dense-id prototype was measured, not qualified: it ran the instrument's
  scenarios, not the GQT corpus.

## Run record (for the orchestrator)

- Workflow: Autoresearch: vectorized execution in the Omnigraph query engine
  (`wf-autoresearch-modernrelay-omnigraph-vectorized-execution`), first run.
- Query family: the whole read path mapped; prototype family `traversal`.
- Base commit `36d3ea08c5289b40653a26752cd0e6c8ef85e6af`; branch
  `autoresearch/vectorized-traversal-20261103`; report
  `docs/autoresearch/vectorized-traversal-20261103.md`.
- Hypotheses: 7 measured (H1, H4, H2, H5, H3 kept; H7 prototype as a patch,
  its first build dropped for the chunking defect; H6 not attempted), of a
  budget of 8.
- Started 2026-11-03 about 08:00 UTC on the box; wall time about 7 hours.

## Sources

Found by a literature and source survey run for this report (each entry was
opened and read unless marked *listing*); the hypothesis each shaped is in
parentheses.

- Boncz, Zukowski, Nes. *MonetDB/X100: Hyper-Pipelining Query Execution.* CIDR 2005. https://www.cidrdb.org/cidr2005/papers/P19.pdf. Vector size 1024 by default, "all values between 128 and 8K work well" until intermediates leave the cache; selection vectors so filters never copy. (H6: expand chunk size)
- Kersten, Leis, Kemper, Neumann, Pavlo, Boncz. *Everything You Always Wanted to Know About Compiled and Vectorized Queries But Were Afraid to Ask.* VLDB 2018. https://www.vldb.org/pvldb/vol11/p2209-kersten.pdf. Vectors below 64 and above 64K rows "decrease performance significantly"; a gather through a selection vector costs more than a dense pass. (H6, the map's "flatten once per operator" reading)
- Neumann. *Efficiently Compiling Efficient Query Plans for Modern Hardware.* VLDB 2011. https://www.vldb.org/pvldb/vol4/p539-neumann.pdf. (context: compiled alternative, not borrowed)
- Leis, Boncz, Kemper, Neumann. *Morsel-Driven Parallelism.* SIGMOD 2014. https://db.in.tum.de/~leis/papers/morsels.pdf. Morsel (~100K rows, scheduling) and vector (cache) sizes are separate knobs. (H6)
- Jin, Salihoglu et al. *KÙZU Graph Database Management System.* CIDR 2023. https://www.cidrdb.org/cidr2023/papers/p48-jin.pdf; `internalID_t { offset, tableID }` https://github.com/kuzudb/kuzu/blob/89f0263cc7a1fd9c396d2c4953747a013556a7f9/src/include/common/types/types.h. Node identity is a dense offset; the string key is looked up once at the plan's entry; adjacency is CSR over offsets. (the dense-id redesign)
- Gupta, Mhedhbi, Salihoglu. *Columnar Storage and List-based Processing for Graph DBMSs.* VLDB 2021. https://www.vldb.org/pvldb/vol14/p2491-gupta.pdf. Positional vertex ids index property columns directly; list groups keep intermediates unflattened. (the dense-id redesign, factorized expand)
- Mhedhbi, Salihoglu. *Optimizing Subgraph Queries by Combining Binary and Worst-Case Optimal Joins.* VLDB 2019. https://www.vldb.org/pvldb/vol12/p1692-mhedhbi.pdf. (context)
- ten Wolde, Singh, Szárnyas, Boncz. *DuckPGQ: Efficient Property Graph Queries in an Analytical RDBMS.* CIDR 2023. https://www.cidrdb.org/cidr2023/papers/p66-wolde.pdf. A per-query CSR over dense rowids built by relational operators; a "perfect join" turns a hash join on an almost-dense domain into an array lookup; multi-source BFS as bitset lanes. (the dense-id redesign, H3)
- Groß, ten Wolde, Boncz. *Adaptive Factorization Using Linear-Chained Hash Tables.* CIDR 2025. https://vldb.org/cidrdb/papers/2025/p21-gro.pdf. (context: a join hash table's chains as adjacency)
- Birler, Kemper, Neumann. *Simple, Efficient, and Robust Hash Tables for Join Processing.* DaMoN 2024. https://db.in.tum.de/~birler/papers/hashtable.pdf. Build side in a CSR layout, "up to 20x in graph processing queries". (H2, context)
- Kuzu. *Robust Recursive Query Parallelism in Graph DBMSs.* VLDB 2025. https://arxiv.org/abs/2508.19379. Frontier extension over dense per-vertex arrays, not per-source sets. (H3)
- Shun, Blelloch. *Ligra.* PPoPP 2013. https://www.cs.cmu.edu/~jshun/ligra.pdf. The frontier is a set of integer ids; visited is a dense bitmap. (H3)
- Raasveldt, Mühleisen. *DuckDB: an Embeddable Analytical Database.* SIGMOD 2019. https://mytherin.github.io/papers/2019-duckdbdemo.pdf; `DEFAULT_STANDARD_VECTOR_SIZE 2048` https://github.com/duckdb/duckdb/blob/15ee9801d5935e00bb1fa0b1c20d98c728d94cf2/src/include/duckdb/common/vector_size.hpp#L16; `JoinHashTable::Hash` over a whole chunk https://github.com/duckdb/duckdb/blob/15ee9801d5935e00bb1fa0b1c20d98c728d94cf2/src/execution/join_hashtable.cpp#L393. No per-row key allocation; `string_t` keys hash in place. (H2, H6)
- Qiao, Zhang. *Data Chunk Compaction in Vectorized Execution.* SIGMOD 2025. https://doi.org/10.1145/3709676 (*listing*; the merged change https://github.com/duckdb/duckdb/pull/14956 was read). Joins and filters emit many small chunks; compaction restores full vectors. (H6)
- Pedreira et al. *Velox: Meta's Unified Execution Engine.* VLDB 2022. https://www.vldb.org/pvldb/vol15/p3372-pedreira.pdf; `DictionaryVector` https://github.com/facebookincubator/velox/blob/f6433b5744fc0486ea0587437da54e8f07d8bb57/velox/vector/DictionaryVector.h#L30; `VectorHasher::computeValueIds` https://github.com/facebookincubator/velox/blob/f6433b5744fc0486ea0587437da54e8f07d8bb57/velox/exec/VectorHasher.h#L130. Dictionary vectors as zero-copy indices over a base; value ids for array-indexed joins. (the dense-id redesign)
- Lamb et al. *Apache Arrow DataFusion: A Fast, Embeddable, Modular Analytic Query Engine.* SIGMOD 2024. https://andrew.nerdnetworks.org/pdf/SIGMOD-2024-lamb.pdf; `create_hashes` https://github.com/apache/datafusion/blob/7d7d6b1338c3a40963968c1a47b20a6650166382/datafusion/common/src/hash_utils.rs#L1289; `JoinHashMap` over `hashbrown::HashTable<(u64, u32)>` (datafusion-physical-plan 54.0.0 `joins/join_hash_map.rs`, read at the pinned version). Bulk hashing of whole arrays into a `u64` buffer; a hash table of `(hash, row)` that borrows nothing. (H2)
- Kuiper, Raasveldt, Mühleisen. *These Rows Are Made for Sorting.* ICDE 2023. https://hannes.muehleisen.org/publications/ICDE2023-sorting.pdf. Normalized keys for multi-column sorts. (not attempted: the sort is not on the traversal path)
- Hao, Lamb. *Using StringView / German Style Strings to Make Queries Faster.* DataFusion blog 2024. https://datafusion.apache.org/blog/2024/09/13/string-view-german-style-strings-part-1/. (found, not used)
- Lance 13.0.0 `rust/lance/src/io/exec/filtered_read.rs` (read at the pinned version): the pushed filter `Expr` is cloned per fragment (`filters.insert(fragment_id, Arc::new(f))`) and planned to a physical expression per fragment (`Planner::new(read_schema).create_physical_expr(filter)`). (H5)
- Lance `take` by row address and the scanner's `BATCH_SIZE_FALLBACK = 8192` https://github.com/lancedb/lance/blob/2545efae06b118d81d9df4d8be3bbc4c66f24650/rust/lance/src/dataset/scanner.rs#L77. (context)
- Found, not used: Neumann, Freitag, *Umbra* (CIDR 2020); Engelke, Schwarz, *Query Compilation Without Regrets* (SIGMOD 2024, *listing*); Wagner, Kohn, Boncz, Leis, *Incremental Fusion* (ICDE 2024); Boncz, *Making Data Management Better with Vectorized Query Processing* (SIGMOD 2024 keynote, *listing*); Jin, Salihoglu, *GRainDB* (VLDB 2022); Wang et al., *Gunrock* (TOPC 2017); Davis, *SuiteSparse:GraphBLAS* (TOMS 2019, *listing*); Lee et al., *Joins on Encoded and Partitioned Data* (VLDB 2014, *listing*); Lee, Park, Kim, *Chimera* (VLDB 2025, abstract); Neo4j store-format documentation.
