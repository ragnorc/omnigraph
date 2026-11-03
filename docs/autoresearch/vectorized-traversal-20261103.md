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

(filled in at the end of the run)

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

## What each operator's cost should grow with

(filled in with the baseline)
