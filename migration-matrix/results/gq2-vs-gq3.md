# GQ 2 against GQ 3 on one machine

Measured 2026-11-22. The same intent, written once in edition 2 and once in
GQ 3, timed on the same graph with the two binaries that differ by GQ 3
alone. Wall time, store requests and peak memory; no AWS, no change to the
benchmarks-aws repository, #1316 or `main`. Synthetic data only.

**Contents:** [Commits and machine](#commits-and-machine) ·
[How it ran](#how-it-ran) · [Part A: query shapes](#part-a-query-shapes) ·
[Part A: the search set](#part-a-the-search-set) ·
[Part B: scenarios with no GQ text](#part-b-scenarios-with-no-gq-text) ·
[What failed](#what-failed) · [Where GQ 3 costs more, and why](#where-gq-3-costs-more-and-why) ·
[Reproducing](#reproducing)

## Commits and machine

| | commit | what it is | `omnigraph version --json` |
|---|---|---|---|
| OLD | `84dbead889727260d0d547b3bbe33e079bfba4ec` | ModernRelay/omnigraph `main`, the second parent of `c8d6d416`, the newest merge of origin `main` into #1316's head | 0.16.1, `commit: null`, http-contract 0.14, internal schema writes 18 (serves 14–18) |
| NEW | `cf133eae7a9665afb63f117dac3a315c3d0c1b3b` | head of draft PR ModernRelay/omnigraph#1316 (`gq3/phase-0-front-end` on ragnorc/omnigraph) when the run started, after the `bm25` memory fallback (`424c3066`, `711553b7`, `6433bf3d`) | the same strings plus `query_language: gq3` |
| RFC | `d1bd54ca24a523a3aac0f9ebcd1556d80f81df18` | `docs/rfcs/2026-11-07-gq-3.md` on `rfc/gq-3` | |

Both binaries were built once, each in its own worktree, with the shipped
recipe: `RUSTFLAGS="" cargo build --release --locked -p omnigraph-cli -p
omnigraph-server -p omnigraph-azure-admission --features
omnigraph-server/aws` (OLD 10 min 2 s; NEW 11 min 10 s, its `target/` seeded
with a copy of OLD's so only changed crates recompiled). `omnigraph-bench`
was built from each tree with `RUSTFLAGS="" cargo build --release --locked
-p omnigraph-bench`: OLD's builds the fixtures of Part A, NEW's runs Part B.
Neither binary stamps a commit, so `--at` admits each under its ref (a
development build "is admitted under any ref"). The full documents are in
`raw/gq2-vs-gq3/versions.json`.

**Machine.** A Modal sandbox under gVisor (kernel `4.19.0-gvisor`, x86_64):
**32 logical CPUs** (cgroup CPU quota 32; `nproc` prints 2 only because the
environment sets `OMP_NUM_THREADS=2`), 64 GiB of memory, CPU model not
exposed (`/proc/cpuinfo` says `unknown`), the graphs on the sandbox's overlay
root filesystem. Every measurement ran alone, with no build or other work on
the box. `raw/gq2-vs-gq3/machine.txt` holds the facts and the timeline.

**Two local patches to the bench harness, never pushed.** On Linux,
`omnigraph-bench` accepts only XFS on a directly mounted NVMe device, and its
machine probe needs `/proc/self/status` `Cpus_allowed_list`, CPU topology
ids and cgroup v2, none of which gVisor provides. Unpatched, NEW's harness
refuses Part B's command with `gqt_prepare_failed: scratch device 0:27 is not
XFS` (`raw/gq2-vs-gq3/partb-unpatched-harness-refusal.json`), and OLD's harness
refuses `dataset build` the same way. The patches
(`bench-local-patch-old.diff`, `bench-local-patch-new.diff`) record the box
instead of refusing it: every record says `filesystem: "unqualified (scratch
device 0:27 is not XFS)"`, `probe: "unqualified-local-patch"` and
`cpu_model: "unqualified: …"`, while its plan still declares the catalog's
`xfs`/`nvme-ssd` backend, so none of these records is a qualified point.
Nothing that times or counts was changed.

## How it ran

**Part A** uses the matrix harness's timing approach (`timing.py`), in
`harness/gq2_vs_gq3.py`. Every graph is written by OLD. Each query runs with
OLD's `omnigraph` in its edition-2 text and NEW's in its GQ 3 text, five
timed runs per side, the sides interleaved, after one untimed run per side
that also gives the rows compared. Every invocation is a fresh release CLI
process with `--stats`, timed by wall clock; the tables give the median wall
time, the median in-process time `--stats` reports (`elapsed_ms`, from target
resolution to the last byte of output), the store requests `--stats` counts
(every logical call of the `__manifest` and table planes; the invocation is
the measured step), and the highest peak RSS. Rows are compared with p22's
comparator (`scripts/cross-edition.py` at NEW: column names, Arrow types,
rows as a multiset unless both sides order them). An F32 `bm25` score against
an F64 one is compared by order only: the rows with the score dropped must be
equal and in the same order; the scores themselves also agree within F32
precision (largest relative difference 1.3e-7).

* **The catalog's query shapes.** The 56 of the 80 scenarios of
  `benchmarks/benchmarks.yaml` whose measured step holds GQ text. The
  edition-2 step comes from OLD's `benchmarks/`, the GQ 3 step from NEW's;
  OLD's `e2e-rrf-traversal` is NEW's `e2e-rrf-exact-traversal`. Each runs on
  its own fixture, built once by OLD's engine: OLD's `omnigraph-bench dataset
  build <OLD fixture> --queries <OLD workload>`, so the index requirements are
  edition 2's. The CLI cannot build these fixtures itself (generated seeds,
  loops of up to 100,000 mutations), so this is OLD's code through OLD's
  harness rather than OLD's `omnigraph` binary. Each fixture's never-opened
  template is checked against the byte digest its build published
  (`omnigraph-bench-physical-tree-v1`, recomputed by the script), then
  restored at the path it was built at, which its shallow-branch references
  name. Reads share one restored copy, whose files are digested before and
  after (no read changed a file); a write runs on a fresh copy every time,
  made outside the timing. Two things the CLI cannot carry are handled this
  way: a prefix step's session setting (`set history_release_bytes = 2048;`
  in the threshold-crossings workloads) is put in front of the measured text,
  where it applies in-process; and the `# traversal: csr` / `indexed` pins
  have no CLI form, so the two `e2e-traversal-selective-*` rows run the same
  command.
* **The search set** on `timing.py`'s 100,000-document corpus (`gen.py`
  `SEARCH_PG`, `search_corpus`), loaded with OLD's `load --mode merge` in
  chunks of 8,000 and indexed by OLD's `index build` (full-text, vector and
  BTREE indexes) in 0.5 s. The queries are in `search_set()`.

**Part B** runs `omnigraph-bench run <scenario> --store <local path> --at
84dbead8…=<OLD omnigraph> --at cf133eae…=<NEW omnigraph> --json` with NEW's
patched harness and NEW's catalog, the catalog's repetitions, from the NEW
checkout, one scenario at a time (`harness/gq2_vs_gq3_partb.sh`). Each base is
built by NEW's harness, and each step of the workload is one child of the
ref's `omnigraph` over a clone of it. The `--at` route stops a scenario at
its first failing ref, and OLD runs first, so each scenario OLD failed was
run again with NEW alone (`--at cf133eae…=<NEW omnigraph>`) to show NEW's side.
`concurrent-writes` needs `--server-product`, which starts each ref's
`omnigraph-server` beside its `omnigraph`.

## Part A: query shapes

54 of the 56 ran; both editions returned the same rows on all 54. Medians of
five runs per side, in milliseconds; "in-process" is the `--stats` elapsed
time, without process start. Raw records: `raw/gq2-vs-gq3/shapes.jsonl`
(every run, both texts, the first ten rows of each side) and
`fixture-builds.jsonl` (OLD's fixture builds).

| scenario | OLD median ms | NEW median ms | NEW/OLD | in-process ms OLD → NEW | store requests OLD / NEW | peak RSS MB OLD / NEW | rows |
|---|---|---|---|---|---|---|---|
| `equal-current-narrow-one-batch` | 47.4 | 46.5 | 0.98 | 20.7 → 20.1 | 6 / 6 | 271 / 276 | same |
| `equal-current-wide-four-batches` | 48.6 | 46.8 | 0.96 | 22.3 → 22.7 | 9 / 9 | 272 / 278 | same |
| `equal-current-wide-one-batch` | 45.9 | 43.5 | 0.95 | 19.6 → 19.3 | 6 / 6 | 271 / 276 | same |
| `hot-table-idle-base` | 66.0 | 64.4 | 0.98 | 38.1 → 37.5 | 11 / 11 | 274 / 281 | same |
| `hot-table-idle-branches-4-aged` | 70.0 | 67.0 | 0.96 | 40.9 → 39.0 | 11 / 11 | 274 / 280 | same |
| `hot-table-idle-branches-4` | 69.9 | 75.9 | 1.09 | 41.4 → 46.5 | 11 / 11 | 274 / 280 | same |
| `hot-table-idle-tables-4` | 73.0 | 69.9 | 0.96 | 42.4 → 41.0 | 11 / 11 | 276 / 281 | same |
| `identity-lifecycle` | 74.5 | 70.8 | 0.95 | 46.1 → 44.1 | 15 / 15 | 275 / 280 | same |
| `nearest-ranks-updated-embedding` | 58.6 | 55.8 | 0.95 | 29.0 → 28.5 | 14 / 14 | 273 / 278 | same |
| `nearest-skips-deleted-rows-and-fills-limit` | 53.6 | 50.2 | 0.94 | 25.4 → 25.2 | 11 / 11 | 271 / 276 | same |
| `parallel-tables-1` | 67.5 | 64.3 | 0.95 | 39.1 → 38.9 | 11 / 11 | 276 / 280 | same |
| `parallel-tables-2` | 73.6 | 66.7 | 0.91 | 45.5 → 42.2 | 14 / 14 | 278 / 283 | same |
| `parallel-tables-4` | 89.3 | 87.9 | 0.98 | 59.1 → 60.5 | 20 / 20 | 283 / 288 | same |
| `repeated-deletion-recreation-rows` | 68.6 | 56.8 | 0.83 | 39.0 → 31.5 | 10 / 10 | 276 / 277 | same |
| `threshold-crossings-after` | 71.9 | 66.3 | 0.92 | 42.6 → 38.8 | 13 / 13 | 275 / 279 | same |
| `threshold-crossings-at` | 75.5 | 67.3 | 0.89 | 47.2 → 41.0 | 19 / 19 | 276 / 278 | same |
| `threshold-crossings-before` | 66.9 | 60.5 | 0.90 | 39.5 → 34.9 | 13 / 13 | 276 / 277 | same |
| `threshold-crossings-production-control` | 74.4 | 64.5 | 0.87 | 46.8 → 35.7 | 13 / 13 | 275 / 279 | same |
| `tiny-mutate` | 65.5 | 56.6 | 0.86 | 36.6 → 29.8 | 10 / 10 | 274 / 279 | same |
| `tiny-mutate-unattached` | 63.8 | 57.7 | 0.90 | 37.6 → 31.1 | 16 / 16 | 276 / 277 | same |
| `tiny-post-reopen` | 44.1 | 41.7 | 0.95 | 18.6 → 18.9 | 6 / 6 | 271 / 276 | same |
| `tiny-read` | 45.6 | 43.6 | 0.96 | 19.0 → 18.8 | 6 / 6 | 271 / 276 | same |
| `very-long-history-1000-read` | 52.8 | 52.9 | 1.00 | 26.4 → 27.6 | 6 / 6 | 272 / 278 | same |
| `very-long-history-1000-write` | 91.8 | 82.5 | 0.90 | 60.2 → 56.1 | 11 / 11 | 281 / 285 | same |
| `very-long-history-10000-read` | 95.3 | 91.3 | 0.96 | 64.7 → 62.9 | 6 / 6 | 272 / 277 | same |
| `very-long-history-10000-write` | 134 | 129 | 0.97 | 103 → 102 | 11 / 11 | 278 / 284 | same |
| `very-long-history-100000-read` | — | — | — | — | — | — | not run: fixture build failed (see below) |
| `very-long-history-100000-write` | — | — | — | — | — | — | not run: fixture build failed (see below) |
| `very-long-history-32-read` | 46.5 | 46.6 | 1.00 | 20.3 → 20.3 | 6 / 6 | 271 / 276 | same |
| `very-long-history-32-write` | 74.4 | 70.2 | 0.94 | 44.3 → 41.4 | 11 / 11 | 275 / 280 | same |
| `very-long-history-32-write-unattached` | 65.9 | 66.5 | 1.01 | 39.0 → 39.9 | 17 / 17 | 275 / 282 | same |
| `unattached-chain-32-write` | 68.3 | 65.6 | 0.96 | 40.0 → 39.6 | 17 / 17 | 276 / 280 | same |
| `e2e-branch-first-write` | 70.5 | 61.9 | 0.88 | 42.4 → 35.7 | 13 / 13 | 275 / 277 | same |
| `e2e-nearest-prefilter` | 50.7 | 49.4 | 0.97 | 24.6 → 24.7 | 10 / 10 | 271 / 276 | same |
| `e2e-nearest-nprobes-one` | 52.3 | 50.8 | 0.97 | 25.1 → 24.6 | 13 / 13 | 271 / 276 | same |
| `e2e-rrf-exact-traversal` | 62.3 | 53.1 | 0.85 | 34.0 → 26.7 | 17 / 13 | 273 / 277 | same |
| `e2e-query-scan` | 44.3 | 43.4 | 0.98 | 18.3 → 18.4 | 5 / 5 | 271 / 276 | same |
| `e2e-query-wide-scan` | 44.5 | 41.7 | 0.94 | 18.0 → 18.3 | 5 / 5 | 271 / 276 | same |
| `e2e-query-filter` | 46.7 | 47.2 | 1.01 | 20.3 → 21.0 | 5 / 5 | 271 / 276 | same |
| `e2e-query-lookup` | 47.7 | 45.6 | 0.96 | 20.1 → 20.5 | 5 / 5 | 271 / 276 | same |
| `e2e-query-count` | 43.8 | 41.2 | 0.94 | 16.9 → 16.7 | 5 / 5 | 271 / 276 | same |
| `e2e-query-grouped` | 47.1 | 45.0 | 0.96 | 21.0 → 20.2 | 5 / 5 | 271 / 276 | same |
| `e2e-query-top-people` | 46.2 | 43.5 | 0.94 | 19.6 → 18.6 | 5 / 5 | 271 / 276 | same |
| `e2e-query-friends` | 47.3 | 50.1 | 1.06 | 22.6 → 24.2 | 7 / 7 | 271 / 277 | same |
| `e2e-query-filtered-friends` | 51.0 | 50.3 | 0.99 | 24.3 → 25.2 | 7 / 7 | 271 / 276 | same |
| `e2e-query-no-friends` | 46.7 | 43.0 | 0.92 | 20.2 → 19.2 | 6 / 6 | 271 / 276 | same |
| `e2e-query-count-bare` | 41.3 | 39.0 | 0.94 | 16.0 → 15.3 | 5 / 5 | 271 / 276 | same |
| `e2e-query-destination-projection` | 49.3 | 47.3 | 0.96 | 23.7 → 23.4 | 9 / 9 | 271 / 277 | same |
| `e2e-query-grouped-fanout` | 53.8 | 51.3 | 0.95 | 26.8 → 25.0 | 9 / 9 | 271 / 276 | same |
| `e2e-query-destination-search` | 61.8 | 52.7 | 0.85 | 33.7 → 26.3 | 15 / 9 | 272 / 276 | same |
| `e2e-traversal-hop1` | 51.2 | 47.5 | 0.93 | 23.9 → 24.0 | 9 / 9 | 271 / 276 | same |
| `e2e-traversal-hop2` | 51.5 | 52.5 | 1.02 | 25.3 → 24.8 | 11 / 10 | 271 / 276 | same |
| `e2e-traversal-hop3` | 56.8 | 51.7 | 0.91 | 29.1 → 25.3 | 12 / 10 | 271 / 276 | same |
| `e2e-traversal-selective-csr` | 57.5 | 54.2 | 0.94 | 30.5 → 29.1 | 13 / 13 | 271 / 276 | same |
| `e2e-traversal-selective-indexed` | 57.6 | 54.4 | 0.94 | 30.3 → 29.1 | 13 / 13 | 271 / 276 | same |
| `tiny-mutate-as-commit` | 62.3 | 55.5 | 0.89 | 35.0 → 30.2 | 16 / 16 | 276 / 277 | same |


Not run: `very-long-history-100000-read` and `-write`. OLD's `dataset build`
of `fixtures/very_long_history_100000.gqt` stopped at the fixture's own
`timeout_ms: 600000` (`fixture_build_timeout: dataset whole-file timeout
exceeded` after 600.7 s, when its graph held 16,363 `__manifest` versions of the
100,002 commits the fixture writes;
the 10,000-commit fixture took 424 s). The catalog was not edited to give it
longer, so neither edition has a graph to run on. The second record for that
fixture in `fixture-builds.jsonl` (`dataset_cache_corrupt`) is the cache
refusing the first build's quarantined state, not a second cause.

At these sizes (at most a few hundred rows, or a 10,000-commit history of
one row) NEW is as fast as OLD or a little faster: wall time 0.83× to 1.09×,
in-process time within a few milliseconds either way. NEW never issues more
store requests than OLD; it issues fewer on four reads: `e2e-rrf-exact-traversal`
(17 → 13) and `e2e-query-destination-search` (15 → 9), whose lexical calls
read the column instead of the full-text index, and `e2e-traversal-hop2`
(11 → 10) and `-hop3` (12 → 10). NEW's peak RSS is 1 to 7 MB higher on every
row, the same at process start, before any graph work: about 271 MB for OLD
and 276 MB for NEW on the smallest read.

## Part A: the search set

The 100,000-document corpus. Raw records: `raw/gq2-vs-gq3/search.jsonl`.

| query | edition 2 | GQ 3 |
|---|---|---|
| `bm25_body`, `bm25_title` | `return { $d.slug, bm25($d.F, $q) as score } order { bm25($d.F, $q) } limit 10` | `match { let $pop = { … }, from bm25($pop, $d.F, $q) as $score } … order { $score desc } limit 10` |
| `…_year` | the same with `$d.year > 2005` in `match` | the same with `where $d.year > 2005` inside `$pop` |
| `s_match_text_body` | `match_text($d.body, $q)` | `where match_terms($d.body, terms($q, mode: any))` |
| `s_search_title`, `s_search_body` | `search($d.F, $q)` | `where match_terms($d.F, terms($q, mode: any))` |
| `s_fuzzy_default` | `fuzzy($d.title, $q)` | `match_terms($d.title, terms($q, mode: any, max_edits: 2))` |
| `s_nearest` | `order { nearest($d.emb, $v) } limit 10` | `from nearest($pop, $d.emb, $v, k: 10) as $distance` |
| `s_rrf` | `order { rrf(nearest(…), bm25($d.title, $q)) } limit 10` | `from rrf(nearest($all, …, k: 10), bm25($all, $d.title, $q), k: 60) as $score` |
| `limit_scan`, `limit_filter` | `return { $d.slug } limit 10`, with and without `$d.year > 2005` | the same, `where` for the filter |
| `limit_scan_order`, `limit_filter_order` | the same with `order { $d.slug }` | the same |

| query | OLD median ms | NEW median ms | NEW/OLD | in-process ms OLD → NEW | store requests OLD / NEW | peak RSS MB OLD / NEW | rows |
|---|---|---|---|---|---|---|---|
| `bm25_body` | 105 | 714 | 6.82 | 73.3 → 682 | 7 / 5 | 320 / 294 | same order; score F32→F64 |
| `bm25_body_year` | 94.8 | 710 | 7.49 | 66.2 → 680 | 7 / 5 | 320 / 294 | same order; score F32→F64 |
| `bm25_title` | 87.2 | 268 | 3.08 | 57.4 → 236 | 7 / 5 | 314 / 289 | same order; score F32→F64 |
| `bm25_title_year` | 85.6 | 278 | 3.24 | 55.6 → 247 | 7 / 5 | 316 / 288 | same order; score F32→F64 |
| `s_match_text_body` | 162 | 710 | 4.37 | 131 → 680 | 7 / 5 | 356 / 294 | same |
| `s_nearest` | 62.7 | 64.8 | 1.03 | 34.0 → 37.5 | 8 / 8 | 279 / 284 | same |
| `s_rrf` | 156 | 343 | 2.20 | 124 → 310 | 10 / 8 | 426 / 330 | same |
| `s_search_title` | 102 | 270 | 2.65 | 72.6 → 241 | 7 / 5 | 298 / 292 | same |
| `s_fuzzy_default` | 93.8 | 299 | 3.19 | 63.5 → 270 | 7 / 5 | 298 / 291 | same |
| `s_search_body` | 159 | 702 | 4.41 | 128 → 670 | 7 / 5 | 361 / 295 | same |
| `limit_scan` | 47.4 | 96.3 | 2.03 | 20.2 → 67.0 | 5 / 5 | 271 / 283 | differ: rows |
| `limit_scan_order` | 84.2 | 83.9 | 1.00 | 53.8 → 55.6 | 5 / 5 | 277 / 282 | same |
| `limit_filter` | 56.6 | 113 | 1.99 | 28.2 → 82.4 | 5 / 5 | 274 / 284 | differ: rows |
| `limit_filter_order` | 99.3 | 100 | 1.01 | 68.9 → 70.3 | 5 / 5 | 278 / 283 | same |


Rows are the same on every query but the two `limit` reads without `order`,
where they differ as the RFC says they will (Results: the `limit` tie rule;
GQ 3 returns the smallest identities, edition 2 the first rows of its scan).
The `bm25` rows are the same documents in the same order, with the score
column `float` → `double`.

## Part B: scenarios with no GQ text

The 24 catalog scenarios whose measured step holds no GQ text. "Lance calls"
is the measured step's logical store calls at the p50 repetition, from its
`--stats` document. Peak RSS is what the bench records for the measured
child. Most of these scenarios have one repetition in the catalog
(`defaults.repetitions: 1`), so a single sample stands for each side. Raw
records: `raw/gq2-vs-gq3/partb/` (one JSON and stderr per scenario, and
`runs.log`) and `raw/gq2-vs-gq3/partb-new-only/`.

| scenario | measured step | reps | OLD p50 ms (min–max) | NEW p50 ms (min–max) | NEW/OLD | Lance calls at p50 OLD / NEW | peak RSS MB OLD / NEW | outcome |
|---|---|---|---|---|---|---|---|---|
| `branch-merge-d50-process-cold` | `branch merge "bench-source" into "bench-target"` | 5 | — | NEW alone: 662.5 (635.1–674.2) | — | — / 1631 | — / 553 | fails: `gqt_verification_failed`: step 3 (query): query failed: parse error: expected clause (OLD's merge itself settled in 643 ms, 1631 Lance calls, before the check failed) |
| `branch-merge-d50-warm` | `branch merge "bench-source" into "bench-target"` | 5 | — | NEW alone: 627.2 (618.9–657.5) | — | — / 1631 | — / 553 | fails: `gqt_prepare_failed`: step 2 (query): query failed: parse error: expected clause |
| `branch-merge-d50-history64` | `branch merge "bench-source" into "bench-target"` | 5 | — | NEW alone: 713.5 (690.5–720.9) | — | — / 1633 | — / 553 | fails: `gqt_verification_failed`: step 3 (query): query failed: parse error: expected clause (OLD's merge itself settled in 667 ms, 1633 Lance calls, before the check failed) |
| `finbench-disjoint-merge` | `branch merge "bench-source" into "bench-target"` | 5 | — | — | — | — | — | fails: `gqt_prepare_failed`: registered dataset requires exactly one matching --fixture ID=BUNDLE |
| `repeated-deletion-recreation-branches` | `branch create "recycled" from main` | 1 | 62.5 (62.5–62.5) | 62.9 (62.9–62.9) | 1.01 | 24 / 24 | 547 / 552 | both ran |
| `tiny-branch` | `branch create "child" from main` | 1 | 50.4 (50.4–50.4) | 54.9 (54.9–54.9) | 1.09 | 24 / 24 | 547 / 552 | both ran |
| `tiny-load` | `--- load generate: v1 seed: 0 mode: insert` | 1 | — | NEW alone: 67.9 (67.9–67.9) | — | — / 26 | — / 552 | fails: `worker_protocol_error`: worker failure carried invalid rejected-operation evidence: gqt_prepare_failed: GQT sample evidence disagrees with the admitted operation |
| `tiny-restart` | `--- restart` | 1 | — | — | — | — | — | fails: `invalid_scenario`: unsupported_environment: the omnigraph binary has no command for step 1 (a measured restart: every step is its own process) |
| `very-long-history-1000-reopen` | `--- restart` | 1 | — | — | — | — | — | fails: `invalid_scenario`: unsupported_environment: the omnigraph binary has no command for step 1 (a measured restart: every step is its own process) |
| `very-long-history-10000-reopen` | `--- restart` | 1 | — | — | — | — | — | fails: `invalid_scenario`: unsupported_environment: the omnigraph binary has no command for step 1 (a measured restart: every step is its own process) |
| `very-long-history-100000-reopen` | `--- restart` | 1 | — | — | — | — | — | fails: `invalid_scenario`: unsupported_environment: the omnigraph binary has no command for step 1 (a measured restart: every step is its own process) |
| `very-long-history-32-reopen` | `--- restart` | 1 | — | — | — | — | — | fails: `invalid_scenario`: unsupported_environment: the omnigraph binary has no command for step 1 (a measured restart: every step is its own process) |
| `e2e-merge-all-changed` | `branch merge "source" into "main"` | 1 | 97.1 (97.1–97.1) | 102.5 (102.5–102.5) | 1.06 | 93 / 89 | 547 / 552 | both ran |
| `e2e-merge-all-new` | `branch merge "source" into "main"` | 1 | 71.4 (71.4–71.4) | 66.1 (66.1–66.1) | 0.93 | 53 / 53 | 547 / 552 | both ran |
| `e2e-merge-diverged-updates` | `branch merge "source" into "main"` | 1 | 124.6 (124.6–124.6) | 116.6 (116.6–116.6) | 0.94 | 113 / 109 | 547 / 552 | both ran |
| `e2e-mixed-load` | `--- load generate: v1 seed: 0 mode: replace branch: main` | 1 | — | NEW alone: 83.9 (83.9–83.9) | — | — / 27 | — / 552 | fails: `worker_protocol_error`: worker failure carried invalid rejected-operation evidence: gqt_prepare_failed: GQT sample evidence disagrees with the admitted operation |
| `e2e-branch-create` | `branch create "child" from "main"` | 1 | 62.8 (62.8–62.8) | 63.6 (63.6–63.6) | 1.01 | 24 / 24 | 548 / 553 | both ran |
| `e2e-branch-create-from` | `branch create "child" from "source"` | 1 | 69.4 (69.4–69.4) | 65.8 (65.8–65.8) | 0.95 | 40 / 40 | 548 / 553 | both ran |
| `e2e-branch-list` | `branch list` | 1 | 53.1 (53.1–53.1) | 53.1 (53.1–53.1) | 1.00 | 11 / 11 | 548 / 553 | both ran |
| `e2e-branch-delete` | `branch delete "victim"` | 1 | 72.4 (72.4–72.4) | 74.8 (74.8–74.8) | 1.03 | 40 / 40 | 548 / 553 | both ran |
| `e2e-branch-adopt-untouched` | `branch merge "source" into "target"` | 1 | 85.6 (85.6–85.6) | 73.0 (73.0–73.0) | 0.85 | 69 / 69 | 548 / 554 | both ran |
| `e2e-branch-adopt-written` | `branch merge "source" into "target"` | 1 | 83.7 (83.7–83.7) | 75.3 (75.3–75.3) | 0.90 | 69 / 69 | 548 / 553 | both ran |
| `branch-merge-d50-as-commit` | `branch merge "bench-source" into "bench-target" as commit` | 5 | — | NEW alone: 655.5 (643.1–663.7) | — | — / 1631 | — / 553 | fails: `gqt_verification_failed`: step 3 (query): query failed: parse error: expected clause (OLD's merge itself settled in 573 ms, 1631 Lance calls, before the check failed) |
| `concurrent-writes` | a `--- concurrent measured` block | 1 per point | — | NEW alone: W=1: writer 17.4/s p50 55 ms; W=2: writer 16.6/s p50 114 ms; W=4: writer 17.0/s p50 227 ms; W=8: writer 16.5/s p50 463 ms; W=16: writer 16.5/s p50 926 ms | — | — | — | fails: `gqt_prepare_failed`: generated load Chunk rows 0..4096 on main: invalid request body: Failed to deserialize the JSON body into the target type: mode: unknown variant `insert`, expected one of `overwrite`, `append`, `merge` at line 1 column 44 |

Where both refs ran (11 scenarios: branch create, list, delete, adopt and
merge on small fixtures), the two are within single-sample noise (0.85× to
1.09×) and issue the same Lance calls, except two merges where NEW issues
four fewer (`e2e-merge-all-changed` 93 → 89, `e2e-merge-diverged-updates`
113 → 109). Of the merges OLD could not finish because their checks are
GQ 3, OLD's merge itself still settled before the check failed, with the
same Lance calls as NEW's (1,631; 1,633 with 64 history commits), in 573 to
667 ms against NEW's 619 to 721 ms over five repetitions. That one sample per
scenario is diagnostic, not a valid one, so no ratio is given.

## What failed

Nothing panicked and no run returned wrong rows. Every failure:

| what | side | error | cause |
|---|---|---|---|
| Part A `very-long-history-100000-read`, `-write` | fixture | `fixture_build_timeout` after 600.7 s | the fixture's own 600 s budget; OLD's engine wrote 16,363 `__manifest` versions of 100,002 commits in it (above) |
| Part B `branch-merge-d50-process-cold`, `-history64`, `-as-commit` | OLD | `gqt_verification_failed: step 3 (query): query failed: parse error: expected clause` | the verification query after the merge is GQ 3 text; OLD parses edition 2 |
| Part B `branch-merge-d50-warm` | OLD | `gqt_prepare_failed: step 2 (query): … parse error: expected clause` | its 24 warm-up reads are GQ 3 text |
| Part B `tiny-load` (`mode: insert`), `e2e-mixed-load` (`mode: replace`) | OLD | `worker_protocol_error: worker failure carried invalid rejected-operation evidence: gqt_prepare_failed: GQT sample evidence disagrees with the admitted operation` | the measured load names a GQ 3 load mode; OLD's CLI refuses it as a usage error (`invalid value 'insert' for '--mode <MODE>' [possible values: overwrite, append, merge]`, exit 64, no JSON), which the harness reports as a protocol error instead of the refusal |
| Part B `concurrent-writes`, as given | both | `invalid_scenario: unsupported_environment: the omnigraph binary has no command for step 1 (concurrent)` | the block runs against a server; the scenario needs `--server-product` |
| Part B `concurrent-writes`, with `--server-product` | OLD | `gqt_prepare_failed: generated load Chunk rows 0..4096 on main: invalid request body: … mode: unknown variant 'insert', expected one of 'overwrite', 'append', 'merge'` | the seed load sends `"mode": "insert"` to OLD's server |
| Part B `tiny-restart`, `very-long-history-{32,1000,10000,100000}-reopen` | both | `invalid_scenario: unsupported_environment: the omnigraph binary has no command for step 1 (a measured restart: every step is its own process)` | `--at` runs every step as its own process, so a measured reopen has no command; not a GQ difference |
| Part B `finbench-disjoint-merge` | both | `gqt_prepare_failed: registered dataset requires exactly one matching --fixture ID=BUNDLE` | its FinBench source bundle is not on this box |

The catalog was not edited for any of them. NEW alone ran every scenario OLD
failed (the "NEW alone" cells), so each failure on OLD is GQ 3 text or a GQ 3
load mode reaching an edition-2 binary.

## Where GQ 3 costs more, and why

Only two kinds of read cost more under NEW in this run, both on the 100,000-row
corpus, and both are costs the RFC and the developer guide already state.

**Lexical search: 2.2× to 7.5× in wall time, 2.5× to 10.3× in process.**
`bm25` over `body` takes 682 ms in process against OLD's 73 ms, `match_terms`
over `body` 680 ms against 131 ms, the same field's `search` 670 ms against
128 ms; over the shorter `title` (2–6 words against 8–24) the same calls take
236 to 270 ms, and `rrf` through its `bm25` arm 310 ms against 124 ms. The
RFC's Cost bullet (`docs/rfcs/2026-11-07-gq-3.md` at `d1bd54ca`): "Until stage
L, `bm25` and `match_terms` run as an exact scan of the field on every call,
and `bm25` also reads the type's whole field corpus for its statistics". In
NEW's `docs/dev/execution.md` (operator table, `Filter` and `Score` rows) the
exact lexical scan "reads no index: it streams the field and the identity at
the pinned snapshot, normalizes each value to NFC, tokenizes it with the
profile's own Lance pipeline" and keeps the identities that match; a `bm25`
`Score` makes "one streamed pass of the exact lexical scan over the type's
whole field corpus". So the cost follows the text in the field, not the rows
returned or the index: `body` costs about three times `title`; the
`year > 2005` filter saves nothing, since the statistics pass reads the whole
corpus whatever the population; `bm25` costs what one `match_terms` pass
costs, as the Cost bullet says scoring during the corpus pass was meant to
achieve; and NEW issues fewer store requests (5 against 7), because it reads
the column instead of the index. Peak memory is lower under NEW (288 to
295 MB against OLD's 298 to 361 MB; `rrf` 330 against 426 MB). The matrix's
run of 2026-11-19 (another box, 32 cores) measured `bm25` over `title` at
4.2× on the same corpus size; this run measures 3.1×, and the one-pass
scoring of `e96d99f8` landed between the two.
Nearest-neighbour search is unchanged (1.03×, the same 8 requests).

**`limit` without `order`: 2.0× in wall time, about 3× in process** (20 → 67 ms on
a scan, 28 → 82 ms after a filter), with the same store requests and 10 to
12 MB more peak memory, and different rows by design. The Cost bullet ends:
"A read with `limit` and no `order` reads every qualifying row (Results)";
`docs/dev/execution.md`, section "A `limit` without `order`", explains that it
"plans the `Sort` with no written key and the limit as its `fetch`: a
streaming top-k over every qualifying row … It reads every row below it,
where edition 2 stopped its scan at the first rows; its cost is a sorted
`limit`'s". That is what this run shows: with `order`, both editions take the
same time (54 → 56 ms and 69 → 70 ms in process). The section's own
measurement on the same corpus (24.3 → 72.5 ms and 26.6 → 88.1 ms) agrees.

Everything else measured costs the same or less: the 54 catalog shapes at
their fixture sizes, the branch and merge scenarios of Part B, and
`nearest`. The extra store requests that `docs/dev/execution.md` ("Store
requests edition 3 adds over edition 2") lists for some respelled steps on
one-fragment tables did not appear in any step measured here.

## Reproducing

```bash
# OLD and NEW worktrees, each built with the shipped recipe and
# `RUSTFLAGS="" cargo build --release --locked -p omnigraph-bench`, both
# benches with bench-local-patch-*.diff applied on a box the harness refuses
export MM_WORK=/path/to/work MM_OLD=…/old/omnigraph MM_NEW=…/new/omnigraph \
       MM_OLD_BENCH=…/old/omnigraph-bench MM_OLD_TREE=/path/to/old MM_NEW_TREE=/path/to/new \
       MM_P22=/path/to/new/scripts/cross-edition.py RUST_MIN_STACK=16777216
cd migration-matrix/harness              # Python with pyyaml and pyarrow
python3 gq2_vs_gq3.py fixtures           # OLD builds every fixture once (≈ 20 min, then the 600 s timeout)
python3 gq2_vs_gq3.py shapes             # Part A, the catalog shapes (≈ 20 min)
python3 gq2_vs_gq3.py search 100000      # Part A, the search set (≈ 10 min)
sh gq2_vs_gq3_partb.sh /path/to/new …/new/omnigraph-bench /path/to/store $MM_WORK/raw/partb \
   84dbead889727260d0d547b3bbe33e079bfba4ec=$MM_OLD cf133eae7a9665afb63f117dac3a315c3d0c1b3b=$MM_NEW
MM_OLD_SHA=84dbead889727260d0d547b3bbe33e079bfba4ec MM_NEW_SHA=cf133eae7a9665afb63f117dac3a315c3d0c1b3b \
  python3 gq2_vs_gq3.py report $MM_WORK/raw   # the three tables
```

The NEW-alone reruns and `concurrent-writes` with `--server-product` are the
same `omnigraph-bench run` with one `--at`, or with `--server-product` added;
`partb/runs.log` and `partb-new-only/runs.log` list every invocation.
