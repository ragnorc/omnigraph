# Cap-free key lookup under stress: report

**Branch:** `research/key-lookup-stress` on `ragnorc/omnigraph`, from
`research/group-commit-stress` at `155911f0`. **Date:** 2026-11-16.
**Subject:** RFC "Group commit with known-key admission" (PR #1405, at
`2f82b02b` on `rfc/group-commit-known-keys`), Part 2: "Footprint", "Key
lookups", "Known keys", rules 16 and 18, rollout step 1. **Status:**
research. Every change is to the prototype on this branch, not production
code.

## Summary

The two-part key lookup is implemented for every key space, the
`KEYED_WRITE_MAX_ROWS` tail cap is gone, the known keys cover every key
space with a byte budget and eviction, and `@unique` implies a BTREE.

- **Equivalence held.** 32 seeds × 300 random steps, plus focused tests:
  85,104 key-space comparisons over 2.39 M wanted keys, 0 differences from
  a whole-table scan. Every fragment shape the cell names was exercised.
- **Staleness held.** No old value was ever reported present, and no
  present key was hidden: not on the graph path, and not in the Lance-level
  delta-segment case where Lance itself returns the stale row.
- **Memory held.** In a 30-minute served soak with every key space written
  and a 4 MiB budget, the map never exceeded its budget, evicted 68k files,
  and 88,025 acknowledged writes verified with 0 violations.
- **Concurrency held.** 4 served runs on RustFS +30 ms with group commit
  on and `optimize` racing (3 on this branch, 1 on the prototype): 2,419
  writes, 2,186 acknowledged, all verified.
  0 violations, 0 false refusals, 0 false acceptances.
- **The cliff:** with the RFC as written, a warm process is flat (1.1
  requests and 4.2 KB per insert, from 100 to 20,000 tail fragments). A
  cold process is not. A restart, or files another process wrote, cost one
  request per uncovered fragment on every insert: 0.67 writes/s at a
  200,000-row tail, against main's 0.52. Removing the cap alone does not
  remove the cliff. Filling the map from the tail scan does: 6.9 writes/s,
  equal to warm. That fill is a change to rule 16, and it is on by default
  on this branch.
- **Defects:** three in my implementation, all found by the new tests and
  fixed. One more is in the RFC text: `@unique` cannot imply an index "as
  `@key` does", because that gives a String column a full-text index that
  no equality lookup can use.

## 1. What was built

Code: `crates/omnigraph/src/key_lookup.rs` (new); call sites in
`table_store.rs`, `validate.rs`, `exec/staging.rs`; `omnigraph-planner`
`cost.rs`; `omnigraph-compiler` `catalog/mod.rs`; `db/omnigraph/table_ops.rs`.

- **One lookup, one key space.** A `KeySpace` is a list of key columns and
  a holder column. The spaces are: ids; each `@unique` group (holder: the
  node id); an edge table's `src` and `dst` (holder: the edge id); and its
  id → `src` space (holder: `src`), which `@card` uses to find an edge's
  old source. The lookup returns, for each wanted key, the holders of every
  row that holds it. It replaces `first_existing_id`, `existing_id_count`,
  `unique_holders`, endpoint presence (`existing_ids`, `existing_int_keys`),
  `committed_edges_by_id` and `committed_source_counts`.
- **Part 1.** A filter `first IN (..)` on the space's first column only,
  with `fast_search`. It runs only when exactly one index that can answer an
  `IN` list keys that column (full-text and vector indexes are ignored), and
  that index is a BTREE in every segment, every segment reports its bitmap,
  and every fragment records `physical_rows`. Otherwise the whole table is
  the tail.
- **Part 2.** Uncovered fragments the map answers come from memory. All the
  others are read in one `with_fragments` scan that projects only the key
  and holder columns. Rows from both parts are matched against the wanted
  set in memory.
- **The known keys.** Keyed by the data file's resolved location, then by
  the space's field ids (not names). The writer records every space of its
  table: ids, `src`/`dst` and id → `src` for edges, and every `@unique`
  group, declared from the catalog at staging. A file answers only if the
  fragment has one data file that lists every field of the space, no
  deletion file and no overlay. A byte budget (default 256 MiB,
  `OMNIGRAPH_KNOWN_KEYS_BUDGET_BYTES`) evicts oldest first. A `Rewrite`
  this process commits drops the files it removed.
- **The tail analysis** is computed once per table version and space. A
  memo is reused only while every file it counts as known is still in the
  map: no eviction below the memo's oldest handle, and no drop since.
- **The cap.** `stage_keyed_write` always runs the lookup for an upsert.
  All-new keys go to the absence-proven writer; all-present keys keep the
  known-present switch. The fall to Lance's full-table merge join is gone,
  except for a mixed batch.
- **Planner.** `coverage_validate_access` now chooses `Probe` (the two-part
  lookup) under `Partial` coverage. `Absent` keeps `Scan`, which the
  endpoint sweep needs.
- **`@unique` implies a BTREE** on its group's first column
  (`NodeType::unique_lookup_columns`, `EdgeType::unique_lookup_columns`).
  This is separate from `indices`, because a one-column `@index` or `@key`
  on a free-text String builds a full-text index (`PropType::index_kind`).
- **Research switches:** `OMNIGRAPH_KNOWN_KEYS=0` (no map);
  `OMNIGRAPH_KNOWN_KEYS_FILL_FROM_SCAN=0` (no fill, which is the RFC as
  written); `OMNIGRAPH_UNIQUE_IMPLIES_INDEX=0`; `OMNIGRAPH_WRITE_OBSERVERS`
  (a server override for O1, so `optimize` is admitted beside 64 writers);
  `OG_KL_TIMING`.

**Lance 13 `fast_search`, verified in the source.** `Scanner::fast_search`
sets `with_only_indexed_fragments()` only when the filter plan carries an
index query (`scanner.rs`). `FilteredReadExec::apply_index_to_fragment` and
`fragment_metadata_load` then skip every fragment outside
`EvaluatedIndex::applicable_fragments` (`filtered_read.rs`). For one column,
that set is `scalar_index_fragment_bitmap`: the union of the named index's
segment bitmaps (`scalar_logical.rs`). For an `AND` over two indexed
columns, it is the *intersection* of their coverages
(`fragments_covered_by_index_query`; Lance's own test
`test_fast_search_scalar_index_filter_coverage_cases`). That is why part 1
filters on one column and matches the rest of a composite group in memory.

## 2. Defects found

| # | Severity | Defect | Found by | Fix |
|---|---|---|---|---|
| K1 | **high** (liveness against the cliff cell) | A cold process (restart, another process's files) reads one request per uncovered fragment on every insert, forever, until maintenance. Only the absence-proven writer records, so its own writes never make the old tail known. At a 200k-row tail: 0.67 writes/s, about 20,000 requests and 25 MB per insert, the same as main's merge join | cell 3 | Fill the map from the tail scan. Every scanned fragment the map could answer (one data file listing the space's fields, no deletion file, no overlay) has all its rows in that unfiltered scan, so its keys are recorded (`record_scanned`, attributed by `_rowaddr`). On by default. Re-verified: 12 more equivalence seeds with it on, cold equals warm. **RFC change** (rule 16) |
| K2 | **medium** (wrong counts) | A wanted key repeated in a lookup's key list got its holders reported once per repeat from the map, while the scans match against a set. A repeated `src` would double its `@card` count and give a false refusal; a repeated tuple would duplicate holders | `kl_btree_staleness_on_the_graph_path` | The known part deduplicates wanted keys (`known_part`) |
| K3 | **low** (cost; disabled the drop) | Map keys used `Dataset::uri()`, which is `file:///x` from one opener and `/x` from another, for the same root. Files recorded by writes never matched under `optimize`'s handles, so the `Rewrite` drop never fired (soak: entries for compacted files stayed) | `kl_rewrite_drops_known_files` | Normalize the root (`root_of`). After the fix, 20 of 20 compacted files dropped |
| R1 | **medium** (RFC text) | "`@unique` is to imply an index on its group's first column, as `@key` does." `@key`'s implied index on a free-text String key is full-text (`index_kind`), which an equality or `IN` lookup never uses. So `@unique email: String` would get no BTREE and no index part | code reading | A separate implied BTREE for `@unique`, whatever the column's index kind |

Other findings (not defects of this design):

- **The prototype's lift equals main whenever any tail fragment is
  unknown.** It lifts the cap only when the map answers the *whole* tail.
  Cold, its numbers are main's to the request (cells 3 and 4).
- **The warm decay is the manifest, not the lookup.** Requests and bytes
  read per insert are flat, but writes/s fall 111 → 7.3 from 100 to 20,000
  fragments. Bytes *written* per insert grow 85 KB → 775 KB by 5,000
  fragments: each detached commit rewrites a manifest that lists every
  fragment. Instrumented at 2,000 fragments, the lookup takes 3.2 ms of a
  17.8 ms write, 1.2 ms of it the per-version tail analysis. With one
  writer every write is a new version, so the memo never hits. The analysis
  costs about 0.6 µs per uncovered fragment.
- **Column patches occur only on tables with a Vector or Blob column**
  (`scan_update_matches` patches only when it can omit one). The random
  test adds a vector column so the two-file shape is exercised.
- **Pre-existing or environmental:**
  `db::upgrade::tests::interrupted::unreadable_legacy_object_asks_for_a_rerun`
  fails on this box because it runs as root (`chmod 000` still reads). P1
  and O1 from the group-commit report are unchanged; the harness
  reclassifies P1 refusals, and O1 has an override switch.

## 3. Cell 1: equivalence

`tests/key_lookup_stress.rs::kl_equivalence_randomized` runs a random
workload over: `Item` (`@key slug`, `@unique email`, a Vector column);
`Pair` (`@unique(a, b)`, nullable on both columns); `Person`; and
`Follows: Person -> Person @card(0..3)`. After every step it compares the
two-part lookup with a whole-table scan in every space of every table
(`key_lookup::differential_check`). The wanted keys are a sample of the
present keys, plus every value the model says is gone (old emails, deleted
ids and tuples, vacated sources), plus absent keys. It also checks the full
state against a model: no duplicate key, `@unique` held, no dangling edge,
`@card` held, and every refusal predicted.

| | total over 32 seeds × 300 steps |
|---|---|
| key-space comparisons / wanted keys | 85,104 / 2,388,799 |
| mismatches | **0** |
| fragments answered from the map / scanned in the tail | 105,884 / 101,976 |
| lookups that ran an index part | 75,088 |
| fragments with a deletion file / two data files (column patch) | 33,913 / 17,357 |
| restarts (map cleared, handle reopened) / map evictions to a random budget | 207 / 190 |
| child processes writing (files the map never knows) | 204 |
| `optimize` (compaction) / index folds mid-run | 290 / 285 |
| loads (another writer) / schema changes (added nullable column, renamed `@unique` column) | 369 / 64 |
| predicted refusals (duplicate email, duplicate tuple, 4th `@card` edge), all refused | 670 |

Seeds 100–119 ran with the scan fill off (the RFC as written) and seeds
200–211 with it on; all passed. Focused tests in the same binary:

- `kl_system_column_upgrade_keeps_known_keys`: the legacy `id` spelling is
  renamed to `__id` in place. The map answers the same 38 fragments before
  and after, because field ids are kept. A pre-upgrade email still refuses.
- `kl_rewrite_drops_known_files`: compaction drops 20 of 20 files.
- `kl_only_the_absence_proven_writer_records`: a source pin for rule 16.
- In-source `key_lookup::tests`: recording, an index over a known tail, a
  deletion file in a recorded fragment, eviction to a 2 KB budget.

Overlays are excluded by construction. The engine never writes one
(`forbidden_apis.rs`), and `answerable` refuses any fragment that has one;
no test produces one.

## 4. Cell 2: index staleness, and what Lance returns

`kl_btree_staleness_on_the_graph_path`: 40 rows are loaded and indexed
(id and the implied email BTREE), then updated, upserted, deleted, a
lagging tail added, compacted, folded, and updated and deleted again. At
every state the lookup equals the scan, old values read absent and new
values present. What Lance returns for `email IN (v)`, plain / with
`fast_search`:

| state | old value | new value |
|---|---|---|
| update of a covered row (the row moves to a new uncovered fragment; a deletion vector in the covered one) | `[]` / `[]` | found / `[]` (its fragment is in the tail; part 2 reads it) |
| upsert of a covered row | `[]` / `[]` | found / `[]` |
| delete in a covered fragment | `[]` / `[]` | — |
| lagging index (new rows after the build) | — | found / `[]` |
| after compaction (stable row ids remap the coverage) | `[]` / `[]` | found / found |
| after a fold | `[]` / `[]` | found / found |
| update and delete after the fold | `[]` / `[]` | found / `[]` |

With one segment per index, which is all OmniGraph writes, Lance masks an
updated or deleted row's old entry through the deletion vector. A moved row
is outside `applicable_fragments`, so `fast_search` skips it and the tail
scan finds it at its new value.

`kl_btree_staleness_delta_segment_lance_level` is the shape no graph writer
produces: a raw stable-row-id dataset, an email BTREE, an upsert that moves
row `a1` from `x1` to `x1-new`, then `optimize_indices(append)`:

- one segment: Lance returns `[]` for `email IN ('x1')`;
- **two segments: Lance returns `[("a1", "x1-new")]` for `email IN ('x1')`,
  with and without `fast_search`**. That is the moved row, carrying its new
  value, for its old value. The delta segment covers the new fragment, so
  the old segment's entry for the stable row id passes the union mask
  (lance#9817).

The two-part lookup matches returned values in memory, so `x1` reads absent
and agrees with the scan. A partly built or lagging index cannot hide a key:
the tail is the exact complement of the one BTREE's union coverage, and it
is always read, from memory or by scan.

## 5. Cell 3: the cliff

`tests/key_lookup_cliff.rs` is the same file in all three trees, built
`--release` (thin LTO). Local disk, one writer. The tail grows by 10-row
inserts, one fragment each, with no maintenance, over 10,000 seeded and
indexed rows. Each length is built once by this branch and copied, so every
binary measures the same bytes. *Warm* means the measuring process wrote
the tail. *Cold* is a fresh process on a copy, which is also the case of
another process's files. *main* is `a70e7b6a`, the base of the prototype;
*proto* is `155911f0`.

writes/s · requests per insert · KB read per insert:

| tail rows (fragments) | main cold | proto cold | proto warm | key-lookup warm | key-lookup cold, no fill (RFC as written) | key-lookup cold, fill (default) |
|---|---|---|---|---|---|---|
| 1k (101) | 50.8 · 121 · 148 | 50.9 · 121 · 148 | 128.6 · 1.1 · 4.1 | 111.3 · 1.1 · 4.1 | 60.1 · 101 · 125 | 102.4 · 3.6 · 7.2 |
| 10k (1,001) | 11.9 · 1,021 · 1,305 | 12.2 · 1,021 · 1,305 | 56.4 · 1.1 · 4.1 | 57.0 · 1.1 · 4.1 | 12.9 · 1,001 · 1,214 | 58.8 · 26 · 34 |
| 50k (5,001) | 2.7 · 5,021 · 6,131 | 2.6 · 5,021 · 6,131 | 15.3 · 1.1 · 4.1 | 26.2 · 1.1 · 4.1 | 2.5 · 5,001 · 6,040 | 22.6 · 126 · 155 |
| 200k (20,001) | **0.52** · 20,017 · 24,227 | **0.55** · 20,017 · 24,227 | not run | 7.3 · 1.1 · 4.1 | **0.67** · 20,001 · 24,141 | 6.9 · 501 · 608 |

Per-insert requests in the fill column average the first insert's full
scan over 40 inserts; every later insert reads 1.1 requests.
`OMNIGRAPH_KNOWN_KEYS=0` (no map at all) matches the no-fill cold column.
Timings ran beside builds and other cells on a 32-core box, so writes/s is
noisy (±20%); requests and bytes are exact. The proto-warm 50k point ran
beside a release build.

Reading it:

- Single-writer main never reaches exactly 0 writes/s on local disk, but
  at 20k fragments it is at 0.5 writes/s, and it falls linearly with the
  tail. On RustFS +30 ms, 20,000 requests per insert would be minutes per
  insert, which is the C1 cliff.
- The RFC's claim that a cold process costs "a bounded read, not the
  cliff" is bounded by the tail, but the bound *is* the cliff for one
  fragment per write. The two-part lookup reads the uncovered fragments'
  key column, about one request per fragment, which is what main's merge
  join reads. The only saving is the covered part.
- With the fill, the first cold insert pays one scan and the rest are warm.
  Nothing falls toward 0.
- Warm is flat in requests and bytes. The writes/s decay in every warm
  column is manifest rewriting (§2).

## 6. Cell 4: cost per key space

**`@unique` against table size** (100-fragment tail, local, one writer).
writes/s · requests per insert · KB read per insert:

| seeded rows | key-lookup warm, implied BTREE | key-lookup warm, no implied index | main cold | proto cold | key-lookup cold, BTREE, no fill |
|---|---|---|---|---|---|
| 10,000 | 112.4 · 1.1 · 4.1 | 52.7 · 121 · 334 | 32.8 · 240 · 520 | 31.6 · 240 · 520 | 38.9 · 201 · 322 |
| 100,000 | 107.6 · 1.1 · 4.1 | 37.6 · 121 · 1,653 | 26.0 · 240 · 1,839 | 23.8 · 240 · 1,839 | 37.9 · 201 · 322 |
| 1,000,000 | 114.1 · 1.1 · 4.1 | 10.7 · 121 · 15,734 | 9.1 · 240 · 15,922 | 9.8 · 240 · 15,922 | 39.5 · 201 · 323 |

With the implied BTREE, a `@unique` insert reads no committed rows outside
the tail: flat in the table's size, and 4.1 KB from memory when warm.
Without it, the check reads the whole group column, 15.7 MB per insert at
1 M rows.

**Edges under `@card(0..5)` against tail length** (60,000 seeded persons;
the edge tail grows by 10-edge inserts):

| edge tail (fragments) | main cold | proto cold | key-lookup warm | key-lookup cold, no fill | key-lookup cold, fill |
|---|---|---|---|---|---|
| 1k (101) | 16.9 · 121 · 164 | 16.2 · 121 · 164 | 41.2 · 1.1 · 11.6 | 16.9 · 101 · 138 | 36.6 · 3.6 · 7.5 |
| 10k (1,001) | 2.7 · 1,021 · 1,365 | 2.7 · 1,021 · 1,365 | 20.4 · 1.1 · 11.6 | 3.5 · 1,001 · 1,339 | 20.9 · 26 · 38 |
| 50k (5,001) | 0.57 · 5,018 · 6,702 | 0.57 · 5,018 · 6,702 | 9.9 · 1.1 · 11.8 | 0.74 · 5,001 · 6,679 | 8.1 · 126 · 171 |

The id, `src` (count) and id → `src` lookups of `Follows` and the endpoint
presence on `Person` all answer from the map or the index when warm.

The RFC's `write_cost.rs` curve and the DST storage-cost golden rows (step
1 done criteria) were **not** added. This driver measures the same quantity
on release binaries; the golden still needs regenerating with reasons.

## 7. Cell 5: concurrency

`scripts/served_run.py` and `scripts/http_stress.py`, copied from the
group-commit study and extended. The setup: real `omnigraph-server`,
RustFS 1.0.0-beta.12 behind toxiproxy 2.13.0 at +15 ms each way (32.5 ms
round trip measured), group commit on, served `optimize` (compaction plus
index reconciliation) every 30 s, a reader at the exact served commit, 300
s per run.

New kinds have predictable outcomes, so false refusals can be counted:

- must succeed: fresh `@key` items; fresh `@unique` labels; a source's
  1st to 3rd `@card(0..3)` edge to a person that is never deleted; fresh
  composite tuples; `(a, null)` tuples;
- must be refused: a seeded label, a seeded tuple, a source's 4th edge.

Persons are deleted alongside, racing endpoint lookups on `Knows`.

| run | writers / actors | ops | acknowledged | writes/commit (max) | violations | false refusals | false acceptances | predicted refusals |
|---|---|---|---|---|---|---|---|---|
| key-lookup | 64 / 4 | 604 | 536 | 1.11 (11) | 0 | 0 | 0 | 51 |
| key-lookup | 56 / 4 | 558 | 497 | 1.10 (12) | 0 | 0 | 0 | 45 |
| proto | 56 / 4 | 550 | 489 | 1.11 (11) | 0 | 0 | 0 | 44 |
| key-lookup | 64 / 1 | 707 | 664 | 1.36 (48) | 0 | 0 | 0 | 31 |

Every acknowledged receipt is in the commit list with its actor. Main's
lineage is one chain. The model replay equals the export and every reader
observation. There is no duplicate key, `@unique` and `@card` hold, there
is no dangling edge, and no in-doubt write lost.

Throughput is about 2 writes/s on both binaries, with p50 around 33 s. It
is bound by one publication per write: the prototype keeps `@unique` and
`@card` types ineligible, and batches one actor at a time. The lookup does
not move that: this branch at 56 writers matches the prototype (497 vs 489
acknowledged). In the single-actor run, the map answered 31,924 uncovered
fragments and the tail scans read 1,405. A lookup took 43 ms on average on
this backend (about 1.4 round trips).

## 8. Cell 6: memory

Setup: served `omnigraph-server` (this branch, scan fill on), local disk,
48 closed-loop writers as one actor, 30 minutes, served `optimize` every
60 s, and a reader recording `count(Item)` at its exact commit. Every key
space is written: fresh items, `@unique` labels (fresh and duplicate),
`@card(0..3)` edges, composite and null-holding tuples, persons with
deletes, and `Knows`. The known-key budget is cut to **4 MiB**
(`OMNIGRAPH_KNOWN_KEYS_BUDGET_BYTES`) so eviction runs all along. Data:
`results/c6-soak.log`, `served/c6-soak/`.

| minute | writes/s | p50 / p99 ms | server RSS MB | known-key files | known-key bytes / budget | evicted (cum.) | dropped by `Rewrite` (cum.) | fragments from map / scanned (cum.) |
|---:|---:|---|---:|---:|---|---:|---:|---|
| 1 | 55.7 | 811 / 1,769 | 901 | 5,724 | 3,475,203 / 4,194,304 | 0 | 172 | 3.5 M / 1,699 |
| 2 | 50.0 | 908 / 2,034 | 972 | 6,893 | 4,194,259 / 4,194,304 | 1,137 | 3,189 | 6.6 M / 3,279 |
| 5 | 51.2 | 891 / 2,108 | 1,138 | 6,910 | 4,193,623 | 9,012 | 11,787 | 16.6 M / 7,671 |
| 10 | 47.9 | 955 / 2,324 | 1,423 | 6,935 | 4,193,585 | 21,909 | 25,992 | 32.7 M / 15,335 |
| 15 | 47.8 | 954 / 2,736 | 1,661 | 6,920 | 4,194,253 | 34,021 | 39,386 | 47.2 M / 22,429 |
| 20 | 47.4 | 952 / 3,112 | 1,913 | 6,874 | 4,194,029 | 46,527 | 53,243 | 62.6 M / 29,917 |
| 25 | 40.4 | 1,114 / 3,214 | 2,187 | 6,868 | 4,193,992 | 58,585 | 66,596 | 77.0 M / 37,461 |
| 30 | 37.5 | 1,168 / 4,117 | 2,430 | 6,857 | 4,193,957 | 68,361 | 77,248 | 88.5 M / 43,139 |

- **Verified:** 97,301 writes, 88,025 acknowledged, every one in the
  commit list with its actor, the model replay equal to the export and to
  13,962 reader observations. **0 violations, 0 false refusals, 0 false
  acceptances**; 8,137 predicted refusals (2,893 duplicate labels, 1,955
  duplicate tuples, 3,289 fifth-edge `@card`) were all refused. All 30
  `optimize` runs succeeded. Batches held up to 20 writes, 1.12 on average
  (ineligible `@unique`/`@card` writes publish alone).
- **The budget held exactly:** the map's bytes never exceeded it (largest
  sample 4,194,295 of 4,194,304) after filling at minute 2. It stays about
  6,900 files while evicting 68k and dropping 77k compacted files. The
  map answered 99.95% of uncovered-fragment reads.
- **Eviction is correct:** no outcome differed from the model, with
  eviction every write from minute 2. The random equivalence test also
  evicts to random budgets 190 times (§3).
- **RSS still grows** (0.9 → 2.4 GB) while the map is flat at 4 MiB, so
  this is not the known keys. It matches the group-commit soak's reading
  (Lance session caches toward their defaults). The writes/s drift (55 →
  37) comes with it and with fragment growth between optimizes; this soak
  does not separate the two.

## 9. RFC text that must change

1. **Rule 16** — "Record known keys only from the absence-proven insert
   writer, only when its staged write produced exactly one data file, and
   for every key space of its table, from the rows it wrote." Also, under
   Known keys, Filling: "No other writer records." Add: *a key lookup's tail
   scan records each fragment it read whole that the map could answer (one
   data file listing every field of the space, no deletion file, no
   overlay), for the space it read*. Without this, a cold process stays at
   the cliff's cost (§5).
2. **Key lookups** — "A cold process, a restart and the files of another
   process therefore cost a bounded read, not the cliff." This is true only
   with item 1. Without it, the bound is one request per uncovered
   fragment, which equals main's merge join: 0.67 vs 0.52 writes/s at
   20,000 fragments.
3. **Key lookups** — "So `@unique` is to imply an index on its group's
   first column, as `@key` does." This should be *a BTREE on its group's
   first column, whatever the column's index kind*. As `@key` does, a
   free-text String gets a full-text index, which an equality lookup never
   uses (R1).
4. **Key lookups, part 1** — "Lance's `fast_search` restricts a filtered
   scan with an index query to the indexed fragments." Verified (§1). Add
   two things. Part 1 must filter on one column, because over an `AND` of
   indexed columns `fast_search` reads only the intersection of their
   coverages. And every returned row must be matched against the wanted
   keys by value, because with a delta segment Lance returns a moved row,
   at its new value, for its old value (§4).
5. **Known keys, Reading** — "when the fragment has exactly one data file
   that the map knows, and no deletion file and no overlay." Add: *and the
   file lists every field of the key space*. Key the spaces by field id, not
   column name, so a rename keeps entries and a drop and re-add does not
   inherit them. Normalize the root before keying ("Keying by resolved
   location"): Lance reports one local root as `file:///x` and as `/x`
   (K3).
6. **Known keys, two rules** — "Compute the tail analysis once per table
   version." Keep it, but note that with one writer every write is a new
   version, so the memo never hits. The analysis then costs about 0.6 µs
   per uncovered fragment per lookup (1.2 ms at 2,000).
7. **Throughput claim** — the remaining per-write decay with the fragment
   count is the table manifest rewritten on every detached commit (85 KB →
   775 KB written per 10-row insert from 100 to 5,000 fragments), not the
   lookup. The Fragments and maintenance section should say so.
8. **Step 1 done criteria** — the `write_cost.rs` curve and the DST golden
   rows are still owed (§6). Add a cold-fill row: the first cold insert's
   requests bounded by the tail, the next flat.

## Reproducing

```bash
git fetch fork && git switch research/key-lookup-stress
# Equivalence, staleness, upgrade, rewrite drop, source pin (dev build)
cargo test -p omnigraph-engine --locked --features failpoints --test key_lookup_stress --no-run
cd crates/omnigraph && OG_KL_SEED=7 OG_KL_STEPS=300 RUST_MIN_STACK=16777216 \
  ../../target/debug/deps/key_lookup_stress-* --test-threads=1 --nocapture
#   OMNIGRAPH_KNOWN_KEYS_FILL_FROM_SCAN=0 runs the RFC as written
cargo test -p omnigraph-engine --locked --features failpoints --lib key_lookup
# Release binaries: /srv/build-rel.sh <tree> <name>; the driver tests/key_lookup_cliff.rs
# Cells 3 and 4 (local): research/key-lookup-stress/scripts/cliff.sh, cell4.sh
# Cell 5 (RustFS +30 ms): scripts/s3-up.sh; scripts/c5.sh <label> <bins> <writers> <secs>
# Cell 6: scripts/served_run.py c6-soak --backend local --optimize-secs 60 \
#   --server-env OMNIGRAPH_KNOWN_KEYS_BUDGET_BYTES=4194304 -- --writers 48 --secs 1800 --actors 1 ...
```

Raw data: `results/` (`cliff-item.jsonl`, `cell4.jsonl`, `timing.jsonl`,
`c5-*.log`, `c6-soak.log`); served histories and checks under `served/`.
