# Autoresearch: one-row keyed writes read the whole table

Run `run-wf-autoresearch-modernrelay-omnigraph-20261109T0900Z-d8451b`, executor
`agent-devbox`, Claude Opus 5.5. Base: `ModernRelay/omnigraph` main
`373834b4c1c4b29cf0ddd64bee9565f98c60cc26`. Research branch:
`ragnorc/omnigraph@autoresearch/keyed-write-scans-20261109`.

## Result in one paragraph

The cost chosen is the storage a one-row keyed write reads. On main, a keyed
`insert`, an `update ... where <key> = v`, a Blob cell write and a
`load --mode merge` of one row each read the `id` column of every fragment of
the table, even when an id BTREE covers the table. The cost grows with the
table: at 262,144 rows a one-row update read 4.78 MB in 150 to 212 requests.
Two changes make it flat. H2: an update's match scan adds the derived
`__id = v` conjunct, as a delete already does, so it reads the index. H1: an
upsert first runs the exact-id probe that strict insert already runs; when the
rows are all new it stages the join-free absence-proven insert, and when they
are all present (and the index covers more fragments than it leaves) it stages
a known-present update. Lance's full-table merge join is then skipped. At
262,144 rows the same writes read 14 KB to 93 KB in 17 to 34 requests. No
result changes; the GQT corpus passes against the reference engine.

## Why this cost

The workflow asks for a cost users pay on every request, or one that grows
with something it should not. Keyed writes are the common write of an agent
graph (one node at a time, with `@key`). A keyed `insert` stages as `Upsert`
(`exec/mutation.rs`, the insert arm of `execute_mutation`), and so do
`load --mode merge` (`loader/mod.rs:1042`) and the Blob cell write
(`exec/blob_write.rs:376`). A write of one row should cost the same on a
table of 1,000 rows and of 1,000,000 rows when the key is indexed. It did not.
Issue #1248 reported the `use_index(false)` part of this by code reading and
one local probe; this run measured it, found the second cause (H2), and fixed
both.

## Map of the code path

- `TableStore::stage_keyed_write` (`table_store.rs:3595`) receives every keyed
  write. `StrictInsert` runs an exact-id probe, `preflight_strict_insert_ids`
  (`id IN (source ids)` through the BTREE), then
  `stage_absence_proven_strict_insert` (`table_store.rs:3813`): no join.
- Every other semantic goes to `stage_keyed_write_from_stream`
  (`table_store.rs:4141`). For `Upsert` it calls `builder.use_index(false)`
  (`table_store.rs:4178`) because Lance 13's indexed merge route omits the
  inserted-row key filter. Lance then takes `create_full_table_joined_stream`
  (lance 13.0.0 `dataset/write/merge_insert.rs:1533`): a hash join of the
  source against a scan of the whole table. That scan is the first full read.
- `update` first finds its rows: `scan_update_matches` (`table_store.rs:6276`)
  or `scan_with_pending`, with the filter `<key> = v`. The key property (`id`
  in the GQT cases, `slug` in the DST case) is a user column. The id BTREE is
  on the system column `__id`, so Lance has no index for the filter and reads
  the key column of every fragment. That is the second full read. A node
  delete already adds the derived `__id = v` conjunct
  (`delete_key_id_conjunct`, from the node-delete run, PR #1232); update did
  not.
- Since #1272 an update that assigns some columns stages
  `KnownPresentColumnPatch`, which already takes the indexed merge route. But
  its match scan is still the full read of the key column, and a narrow table
  picks the whole-row rewrite (`UpdateWriteCost::mode`), which stages `Upsert`.

Expected growth: with a covering id BTREE, a one-row keyed write should grow
with the index's depth (log of rows) and with the fragments the index does not
cover, never with the covered fragments. Measured on main: it grew linearly
with the covered fragments (one GET of the `id` column each) and with the rows
(bytes).

## Instruments

- GQT `--measure` (DST in-memory store), unit model for exact counts and the
  `s3-like` model (17 ms per request plus bytes at 50 MiB/s) for modeled time.
  Cases under `/tmp` on the box, reproduced here in shape: a `Person
  { id: String @key, name: String, v: I64 }` table seeded with N rows in
  4,096-row commits, `# traversal: auto` so the seed builds the BTREE, then
  seven steps: column-patch update, whole-row update, keyed insert, column
  patch again, read by key, merge-load of one present row, merge-load of one
  new row. N = 1,024, 16,384, 65,536 and 262,144. A second case adds 64
  one-row commits the index does not cover (the state between `optimize`
  runs), then a keyed insert, a whole-row update, a merge of one present row
  and a merge of 4,096 present rows.
- The DST storage-cost golden. New labeled scenario
  `keyed_write_table_size_cost_rows`: the four one-row writes at 2 and 16
  fragments.
- Lesson applied from the 2026-11-05 run: start from a byte counter. The DST
  golden counts calls only, and its tables are too small to show the growth.

## Baseline (main, indexed table)

Bytes read per step:

| step | 1,024 | 16,384 | 65,536 | 262,144 |
|---|---|---|---|---|
| update, column patch | 40,557 | 294,826 | 1,181,164 | 4,782,278 |
| update, whole row | 49,470 | 303,254 | 1,187,646 | 4,783,976 |
| keyed `insert` | 27,340 | 140,116 | 531,516 | 2,108,519 |
| update, column patch again | 49,814 | 303,598 | 1,187,990 | 4,784,591 |
| merge-load, one present row | 17,536 | 130,312 | 521,712 | 2,088,712 |
| merge-load, one new row | 27,340 | 140,116 | 531,516 | 2,108,917 |

Store requests per step:

| step | 1,024 | 16,384 | 65,536 | 262,144 |
|---|---|---|---|---|
| update, column patch | 19 | 28 | 64 | 212 |
| update, whole row | 22 | 28 | 52 | 150 |
| keyed `insert` | 18 | 21 | 33 | 83 |
| update, column patch again | 22 | 28 | 52 | 150 |
| merge-load, one present row | 13 | 16 | 28 | 76 |
| merge-load, one new row | 18 | 21 | 33 | 84 |

The read by key, by comparison, was flat (39 KB to 82 KB) because the read
planner already narrows a key filter to `__id` (`scan_access::narrow_key`).

## Hypotheses

| id | change | counters | gates | outcome |
|---|---|---|---|---|
| H2 | `update` adds the derived `__id = v` conjunct to its match scan when the predicate has `<key> = v` and the table has an id BTREE; the delete helper is renamed `key_id_conjunct` and shared | 65,536 rows: column patch 1,181,164 → 580,371 bytes, 64 → 41 requests; whole row 1,187,646 → 540,980 bytes, 52 → 37 requests | see Gates | kept (`a2dd6610`) |
| H1a | an `Upsert` with an id BTREE probes its ids first; all new → absence-proven insert, all present → known-present update, mixed → join | with H2, at 65,536 rows: keyed insert 531,516 → 4,937 bytes, 33 → 14 requests; whole row 1,187,646 → 29,431 bytes, 52 → 24; merge present 521,712 → 14,522, 28 → 17 | — | refined into H1b |
| H1a, tail case | same, with 64 uncovered one-row fragments | whole-row update 98 → 162 requests (s3-like 372 → 444 ms); bytes fell 375,488 → 211,416 | — | found the regression below |
| H1b | H1a, bounded: probe only while (uncovered fragments × source ids) ≤ 8,192 (the ids of one keyed batch; #1225's IN-list blow-up); switch an all-present upsert to the known-present update only while the BTREE covers more fragments than it leaves | table below | see Gates | kept (`4bd23715`, `320cd78d`) |

Why H1a regressed under a large tail: after the probe, Lance's indexed merge
route reads each uncovered fragment again, and misses the cache the full join
gets from the match scan that read the same `id` pages a moment earlier. With
64 uncovered fragments and 4 covered ones, that second pass costs 67 GETs. The
join reads every fragment once. So the switch pays only when the covered part
dominates. The all-new route has no such pass: it skips the merge entirely,
and the probe reads at most what the join would.

Why the IN-list bound: #1231 measured that an `__id IN (...)` probe on a table
the BTREE does not cover copies the list into each uncovered fragment's plan
(3,400 fragments × 8,000 ids: 11 GB). The strict-insert preflight already runs
this probe with no bound; H1b does not widen that exposure, and its upsert
probe stays within one batch's ids. A merge of 4,096 rows over a 65-fragment
tail therefore keeps the join (measured: unchanged at 23 requests).

## After (H1b + H2), indexed table

| step | bytes, 1,024 → 262,144 rows | requests, 1,024 → 262,144 rows |
|---|---|---|
| update, column patch | 39,271 → 93,360 | 28 → 34 |
| update, whole row | 28,668 → 43,664 | 21 → 28 |
| keyed `insert` | 9,170 → 19,173 | 16 → 18 |
| update, column patch again | 36,190 → 45,891 | 24 → 30 |
| merge-load, one present row | 19,728 → 14,522 | 14 → 17 |
| merge-load, one new row | 9,170 → 76,255 | 16 → 28 |

(The 1,024-row figures are from the s3-like run of the final code; the unit
and s3-like models count the same requests and bytes.) What still grows is
the index's own pages and the uncovered tail, as expected.

Tail case (16,384 rows plus 64 uncovered one-row fragments), main → H1b + H2:

| step | requests | bytes read | s3-like ms |
|---|---|---|---|
| keyed insert | 22 → 18 | 149,530 → 19,217 | 282 → 282 |
| whole-row update | 98 → 98 | 375,488 → 268,693 | 372 → 426 |
| merge one present row | 24 → 25 | 150,460 → 154,539 | 319 → 337 |
| merge 4,096 present rows | 23 → 23 | 150,589 → 150,589 | 302 → 302 |

Unindexed table (no `# traversal` header): every step at every size is
identical to main, requests and bytes.

DST storage-cost golden, `l.get` at 2 → 16 fragments of 8 rows:

| label | main | this branch |
|---|---|---|
| `KeyedInsert` | 10 → 25 | 9 → 10 |
| `KeyedUpdate` | 13 → 41 | 11 → 11 |
| `MergeLoadPresent` | 10 → 24 | 9 → 9 |
| `MergeLoadNew` | 10 → 24 | 7 → 5 |

One existing line moved: `InsertLegacy` `l.get` 15 → 13 (its keyed insert
takes the absence-proven writer). The one extra GET of `KeyedInsert` at 16
fragments is also on main (main grows +15 there against +14 for
`MergeLoadNew`); it is the first write after the reopen and I did not trace
it further. `MergeLoadNew` at 2 fragments reads 7 because the two writes
before it leave as many fragments uncovered as covered, so the present-row
merge keeps the join there.

## Timing

No qualified benchmark environment was available, and the box is gVisor with
2 cores, so there is no wall-clock claim. Modeled time (GQT `s3-like`) at
262,144 rows, main → branch: column patch 370 → 460 ms, whole row 390 → 354,
keyed insert 318 → 282, column patch again 372 → 354, merge present 194 →
212, merge new 337 → 373. At 1,024 to 65,536 rows the first column patch is
about 72 ms slower, the others within ±53 ms.

So this change lowers cost (requests, which S3 bills, and bytes) and makes it
flat, but it does not lower modeled latency. The model lets every request of
a scan run at once and has no per-connection throughput limit or queue, so a
full scan's 150 to 212 parallel GETs cost one round trip in it. The index
lookup is a chain of dependent reads (lookup page, then data pages), which
the model charges in full. The first update on a handle pays the cold index
load; later ones pay about one more round trip than the scan. Real stores cap
concurrent requests per client, so the scan's time grows with the table and
the index's does not. I expect the crossover in real latency to come at a
smaller size than the model shows, but that is not measured.

## Redesign proposal (not built)

1. Split a mixed upsert into its two halves: the probe already knows which ids
   exist, so the absent rows could stage as an absence-proven insert and the
   present rows as a known-present update, two Lance transactions on one
   table in one graph publication, as branch merge already does
   (`exec/merge.rs`, `(&staged.inserts, StrictInsert), (&staged.updates,
   KnownPresentUpdate)`). That removes the join for `load --mode merge` of
   mixed batches too. It needs the staging layer to carry two staged writes
   per table outside merge.
2. Keep the uncovered tail small. Every write leaves one uncovered fragment,
   and both the probe and the indexed merge read the tail. A reconciler that
   runs `index build` past a tail threshold (proposed by the 2026-11-02
   branch-ops and node-delete runs) would keep these writes on the cheap path.
   It must stay an explicit reconciler (invariant 7, deny-list: no index work
   inline on a content write).
3. Upstream: lance#10606 (merged 2026-11-02, in 15.0.0 betas) tracks inserted
   keys on the indexed route, which would let every `Upsert` take it and drop
   `use_index(false)`. lance#9876 (open) joins indexed sources through
   `ScalarIndex::lookup`, which would remove the second pass over the tail.
   Both need a Lance bump past 13.0.0.

## Gates

Run locally on the research branch in debug, `RUST_MIN_STACK=16777216`,
2026-11-09 between 09:35 and 09:54 UTC.

- `cargo fmt --all --check`: clean.
- `cargo test -p omnigraph-gqt --locked`: 559 passed, 0 failed (the 464-case
  corpus, with reference-engine parity, plus the runner suites).
- `cargo test --locked` in `crates/omnigraph-dst`: 113 passed, 0 failed,
  including the cost golden.
- `omnigraph-engine` with `failpoints`: lib and the integration, writes,
  Lance surface guard, detached-commit, failpoint, forbidden-API, search,
  schema-apply and bound-storage binaries: 1,740 passed, 2 failed. One was
  `write_cost::keyed_insert_routes_through_fenced_adapter_only`, which
  asserted that a keyed insert on the indexed standard graph pays one merge
  join; it now asserts the fenced insert and no join (`05222da8`), and the
  `write_cost` module passes 19/19. The other,
  `db::upgrade::tests::interrupted::unreadable_legacy_object_asks_for_a_rerun`,
  makes a file unreadable with mode `000`; the box runs as root, which reads
  it anyway. The upgrade path is untouched by this change.
- `omnigraph-core`, `omnigraph-catalog`, `omnigraph-planner`: 495 passed.
- Clippy, `-D warnings -W clippy::dbg_macro`: workspace, workspace with
  `$FAILPOINT_FEATURES`, and GQT: clean.
- `scripts/check-agents-md.sh`: passes. `scripts/check-docs.py`: fails only
  on the release-note snapshot. v0.15.0 was tagged at this run's base, and
  `changelog.d/release.json` still targets v0.15.0, so any new note fails
  until main retargets the next release (as #1140 did after v0.14.0).
- Not run locally: the rest of the canonical workspace graph (server, CLI,
  cluster), RustFS/S3 and Azure jobs, and `typos` (not installed). CI runs
  them on the pull request.
- New test `upsert_skips_the_merge_join_when_the_id_btree_decides_its_rows_issue_1248`
  (`table_store/staged_tests.rs`) pins the four routes; on main it fails,
  because every upsert takes the join.

## Lessons taken from past runs

- 2026-11-05 (failed): start from a byte counter, not the call-count golden.
  Done; the byte column showed the growth, the golden could not.
- 2026-11-06, PR #1285 (closed after review): it added a second reader of
  Lance's private manifest layout. This run uses only public Lance APIs and
  the engine's existing write adapters; no format dependency is added.
- Node-delete run (2026-11-02): the derived-id conjunct already existed for
  delete. H2 reuses it instead of writing a second one.
- Branch-ops and node-delete runs: writes do not build indexes, so a tail of
  uncovered fragments is the normal state. The tail case was measured for
  that reason, and it found the H1a regression.
- #1231: an IN-list probe over uncovered fragments can exhaust memory. H1b
  bounds its probe.
- Vectorized run: debug-build stack overflows; tests ran with
  `RUST_MIN_STACK=16777216`.
- Issue #1294's comment: DST tables are too small for scale effects. The
  scale evidence here is GQT at up to 262,144 rows; the DST golden pins only
  the fragment dimension.

## Sources

- Issue #1248 and its reproduction comment (`use_index(false)` on every
  upsert); issue #1294 (index tail cost on reads); PR #1231 (probe vs scan by
  coverage, IN-list memory); PR #1232 (keyed delete through the id index);
  PR #1272 (column-patch update).
- Lance 13.0.0 source: `dataset/write/merge_insert.rs`
  (`create_joined_stream`, `create_full_table_joined_stream`,
  `create_indexed_scan_joined_stream`), `dataset/scanner.rs` (covered and
  uncovered fragments of a filtered read).
- lance#10606 (merged), lance#9876 (open), checked with `gh` on 2026-11-09.
- Apache Hudi, Indexing (hudi.apache.org/docs/indexes): an index lets an
  upsert avoid joining against the entire dataset to find which files to
  rewrite; Hudi tags each incoming record as insert or update by index lookup
  before writing. H1 is the same split, using the id BTREE Lance already has.
- Added this run: the Hudi indexing page and lance#10606/#9876 as primary
  sources for keyed upsert routing.

## What could not be verified

- Real object-store latency and wall clock (no S3 run, no qualified
  benchmark).
- Peak memory of the probe at the bound (8,192 id copies) was not measured;
  the bound is argued from #1231's measurement, not tested at scale.
- The extra first-write GET of `KeyedInsertFragments16` was not traced.
- Served path (`omnigraph-server`) was not measured separately; it calls the
  same engine.
