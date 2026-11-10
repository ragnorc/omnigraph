# Stress report v2: remote optimize at the new PR heads

**Under test:** `feat/optimize-served` at `1c67541c` (PR #1282), which contains
`feat/optimize-engine` at `761a4e66` (PR #1275). Both merged `origin/main`
(v0.15.0, with #1291 and #1319).
**System under test (SUT):** built from a clean worktree at `1c67541c` with
only two test-only harness targets added (`stress_served_child`,
`engine_optimize_stress`). No stress-branch fix is in it. Provenance and binary
hashes: `evidence-v2/sut_provenance.txt`. One clearly labeled instrumented
variant (handle cache 2 entries / 64 KiB instead of 64 / 64 MiB) was used only
in the runs marked *small-cache variant*.
**Stress branch:** `stress/optimize-1282-v2` on `https://github.com/ragnorc/omnigraph`,
cut from `stress/optimize-1282` (`f280bc4b`), with the PR head merged in so
that fixes apply to the code under test.
**Box:** `nproc` 2, gVisor, rebooted since v1. RustFS 1.0.0-beta.12 reinstalled
from the release zip (checksum matched `SHA256SUMS`), Azurite 3.35.0 from npm.
**Started** 2026-11-09 20:55 UTC.

## Verdict

**#1275 (`feat/optimize-engine` at `761a4e66`): not mergeable as it stands.**
The merge of `origin/main` did not touch `db/omnigraph/optimize.rs`, and the
blocking pipeline early exit (B1) reproduces byte for byte: runs with no bound
report `deferred(gate_hold_limit)` tables and an empty `resume_at`, and on a
1,030-table catalog each served run still optimizes one table. The artifact
refresh still ignores cancellation (S4). Both fixes, with regression tests,
are on the stress branch and merge cleanly with the new head.

**#1282 (`feat/optimize-served` at `1c67541c`): not yet.** Every v1
should-fix still reproduces unchanged (S1, S2, S3, S5, S6; their code is
byte-identical), S7 still reproduces on S3 and Azure (somewhat smaller: runs
and writes are faster at the new head, most likely from #1291's probe
placement), and this campaign found S8: lookups
queue the run's before-publish record write, which holds the writer gates, so
polling clients lengthen every run's writer stall (a median +1.8 s per run
with 8 pollers on S3). A fix for S8 is on the stress branch beside the v1
fixes. The merge itself held up: the new column-patch updates stayed correct
beside sequential and concurrent optimizes on local, S3 and Azure; cache
eviction under the 64-handle budget, and under a 2-handle instrumented
variant, never served a stale or missing row; `maintain` is enforced exactly
on the served path with no effects from 199,025 denials; and crash, kill and
chaos rounds on all three backends kept every invariant. New notes: direct
index commands record any `--as` actor (N16), back-to-back runs refuse ~45%
of a single writer's updates (N17, documented), and a history release from
main can add ~9 s to any publication, a run's included (N18).

**Stopping point.** A second full round (phase E, new seeds and shapes,
`evidence-v2/E/`) found nothing new: concurrent column patches on local, S3
and Azure matched the model; cache pressure over 300 tables and the
small-cache variant over 100 tables were clean, and both overshooting runs
overlapped a history release (N18); hammers on local (2,648 operations), S3
(808) and Azure (107) and seam-armed kills (20 rounds, 34 seams) and chaos
(local 20, S3 10) kept every invariant. Azure added S8 (4 pollers: excess up
to 3.5 s) and N17 (49% of updates refused) to the backends that show them.
Campaign time: 20:55 to 03:32 UTC.

## Findings by severity

| # | Severity | Status at the new heads | PR | Fix on the stress branch |
|---|---|---|---|---|
| B1 | **blocking** | reproduces, unchanged | #1275 | `23e1d66f` (+ test) |
| S1 | should-fix | reproduces, unchanged (S3) | #1282 | `fa5cd0ff` (+ test) |
| S2 | should-fix | reproduces, unchanged (local) | #1282 | `08d2a0e7` (part) |
| S3 | should-fix | reproduces, unchanged | #1282 | `8d02fc6e` |
| S4 | should-fix | reproduces, unchanged | #1275 | `331d8485` + `24614965` (+ test) |
| S5 | should-fix | reproduces, unchanged | #1282 | `de11e65d` (+ test) |
| S6 | should-fix | reproduces, unchanged | #1282 | `5a1bbeec` |
| S7 | should-fix | reproduces on S3 and Azure, somewhat smaller (#1291) | #1282 | `e189d893` (docs only) |
| **S8** | should-fix | **new**: lookups lengthen each run's writer stall | #1282 | `5b0f0dcb` (+ test) |
| N1, N2, N6, N8, N9, N11–N13, N15 | note | reproduce (N9 measured, N15 extended into S8) | — | N2 `53107bc9`, N8 `a6594475` |
| **N16** | note | **new**: direct index commands record any `--as` actor; docs disagree on the default actor | #1282 docs | — |
| **N17** | note | **new measurement**: back-to-back runs refuse 44–49% of a single writer's updates (documented behavior) | #1282 | — |
| **N18** | note (main) | **new**: a history release (~9 s on a 1,030-table catalog) runs inside a publication, a run's included | main | — |

## What changed between the heads

`git diff f6b2443c 1c67541c` touches 79 files. It does **not** touch
`db/omnigraph/optimize.rs`, `recorded_operations.rs`,
`omnigraph-cluster/src/operations.rs`, `omnigraph-storage/src/scoped.rs`, the
CLI's `served.rs` or `deployment.rs`: every prior finding's code is
byte-identical. The merge changed the write path (`exec/mutation.rs`,
`loader/mod.rs`, `exec/staging.rs`, `table_store.rs`): #1291 revalidates a
write under its gates against a manifest probe taken beside its staging puts
and admits each committed dataset to the handle cache; a new column-patch
update (`KnownPresentColumnPatch`, Lance `RewriteColumns`) attaches
per-fragment data files and withdraws touched fragments from indexes on the
assigned column. #1319 demotes a replaced pin instead of evicting it and
evicts only under budget pressure. The policy crate dropped `check_any`:
index builds and the full-text rebuild need `maintain`.


## Prior findings on the new heads

| # | Severity (v1) | Status at `1c67541c` | Evidence (`evidence-v2/`) | #1291 / #1319 effect |
|---|---|---|---|---|
| B1 | blocking | **reproduces**, unchanged | `A/B1_engine.txt` (planning 1, 2, 4, 8: 10, 9, 7, 3 tables `deferred` with no bound, `resume_at` null), `A/B1_served.txt`, `A/B1_big.txt` (1,030 tables: one table optimized per run, ~700 falsely deferred), campaign 1: 150 local and 29 S3 violations, all of this kind (`B/c1_local.txt`, `B/c1_s3.txt`, `D/`) | none (code unchanged) |
| S1 | should-fix | **reproduces**, unchanged | `A/S1_s3.jsonl`: all four writes; new submissions 503 for 20 s, lookups 503 (acceptance/running/publishing), SIGTERM exits 1, restart after 45.3 s | none |
| S2 | should-fix | **reproduces**, unchanged | `A/S2_local.jsonl`: lookups and submissions 503 until restart, SIGTERM exits 1, restart needs force-unlock, `running`-fault record stays `accepted`, no-op run ends `interrupted` | none |
| S3 | should-fix | **reproduces**, unchanged | `A/S3_race.jsonl`: run admitted at the cancel instant ends `succeeded` with no deployment reason; deployment 11.89 s | none |
| S4 | should-fix | **reproduces**, unchanged | `A/S4_artifact.txt`: SIGTERM during the artifact scan, 5 more edge opens, stop 25.05 s, exit 2, record left `publishing` | none |
| S5 | should-fix | **reproduces**, unchanged | `A/S5_cli.txt`: routes disabled (503, `effects: none`) exits 2, "may still run" | none |
| S6 | should-fix | **reproduces**, unchanged | `A/S6_cap.txt`: "four publications are unresolved; …" names no operation or graph | none |
| S7 | should-fix | **reproduces**, slightly smaller | `A/S7_*.json`; see below | #1291 shortened each run's hold; compounding unchanged |
| N1 | note | reproduces | `A/N_items.json`: expired id + over-ceiling hold → 400 (ceiling checked before the mark) | — |
| N2 | note | reproduces | `A/N_items.json`: terminal `nonce: null`; the server log names neither nonce nor commit | — |
| N6 | note | reproduces | `tables: []` and duplicates: 202, then `failed` | — |
| N8 | note | reproduces | `A/N8_s3.json`: B leaves `edge:Knows`, A's late settlement clears it, next run starts at the first table | — |
| N9 | note | reproduces, **measured** | `A/N_items.json`: an id 50 s ahead accepted (120 s refused); once evicted, every honest submission on the cluster answered 409 for 42.5 s (204 refusals) | — |
| N11 | note (pre-existing) | reproduces | `C/crash_local.jsonl`: historical read after `cleanup` → 500 "historical published dataset version … was reclaimed" | — |
| N12 | note (pre-existing) | reproduces | S3, two writers, no optimize: some writes exhaust 32 re-prepares and answer 409 (`A/S7_s3.json` `writes_other` 27 / 18) | — |
| N13 | note | reproduces | `A/N_items.json`: export during a parked run → 413 `stream_export_slots`; 200 after | — |
| N15 | note | reproduces, **extended into S8** | `C/bighold_*.json` | — |

The crash points (`c3_crash.py`, 21 scenarios on a local root) end exactly
as in v1 (`C/crash_local.jsonl`).

### S7 at the new heads (writer wait compounds across back-to-back runs)

Same harness and parameters as v1 (`c1_writer_wait.py`, phases control /
back-to-back optimize with `max_gate_hold_ms` 1000 / control):

| Root | Writes (control / optimize / control) | p99 s | Slowest write | Longest hold | v1 |
|---|---|---|---|---|---|
| local, 2 writers, 180 s | 1,912 / 1,661 / 1,940 | 0.82 / 0.72 / 0.83 | 1.7 s over 4 holds | 1.3 s | no effect |
| S3, 2 writers, 180 s | 941 / 186 / 614 | 0.36 / **9.0** / 0.39 | **11.8 s over 13 runs** (5.7 s held inside) | 0.84 s | 775 / 121 / 377; 14.0 s over 9 runs |
| S3, 1 writer, 90 s | 618 / 110 / 341 | 0.26 / 0.97 / 0.34 | 0.97 s | 0.61 s | 535 / 85 / 264; slowest 4.4 s |
| Azure, 2 writers, 120 s | 380 / 187 / 204 | 0.71 / **7.6** / 1.27 | **9.7 s over 13 runs** | 1.38 s | 157 / 77 / 121; 26 s over 14 runs |

With one writer every write in the optimize phase re-prepared exactly once
(109 re-prepares for 110 writes, none in the controls,
`A/S7_s3_one.json`), as in v1: the same mechanism. #1291 moved the
revalidation probe beside staging, which made runs and writes faster, but an
optimize publication still invalidates every write in flight, so with two or
more writers the wait still spans many runs and the docs' "a writer waits at
most the bound plus one rewrite" still fails on object storage. The v1 docs
correction (`e189d893`) is on this branch, not in the PR.

## New findings

### S8 (should-fix): lookups lengthen the writer stall of a run

- **What:** the run's before-publish record write (`transition`,
  `recorded_operations.rs:971` in the SUT) happens while the run holds the writer
  gates, and it waits for the operation-record gate. Every lookup
  (`find_authorized`, `:1187`) and every admission (`:702`) takes that same
  gate and, under it, reads, decodes and validates the whole record
  (`update`), after an unlocked full read for the resolution walk
  (`walk_unresolved` → `peek_operations`). On a large catalog the record is
  several MB (N15), so each queued lookup adds about a second to the writer
  stall. The writer-wait bound "max_gate_hold, plus one table's rewrite, plus
  the publication, plus the configuration clear" (RFC §1.2) thus depends on
  how many clients poll: a client that lowers `max_gate_hold` to protect
  writers gets a stall set by other clients' lookups.
- **Reproduction:** `stress/c12_big_hold.py OUT 30 40` on the 1,030-table
  catalog (`fixture_big`), no writers, 30 back-to-back full-catalog runs with
  `max_gate_hold_ms` 1000, `STRESS_POLLERS=N` clients looking up recent
  operations in a loop.
- **Evidence (`C/poll_*.json`, `C/bighold_*.json`):** excess of the gate hold
  over (hold + longest table), at a ~4.4 MB record:

  | Root | Pollers | Excess median / max (ms) | Longest hold | Lookup p50 |
  |---|---|---|---|---|
  | S3 | 0 | −173 / 215 | 1.24 s | — |
  | S3 | 4 | **824 / 1,859** | 2.88 s | 1.03 s |
  | S3 | 8 | **1,789 / 3,338** | 4.36 s | 2.10 s |
  | local | 4 | 753 / 2,595 | 3.60 s | 1.17 s |
  | S3, **stress-branch fix** | 4 | −46 / 479 | 1.50 s | 0.03 s |
  | Azure | 4 | 208 / 3,459 | 4.72 s | 0.89 s |

  The excess grows linearly with the pollers. Under the cache-pressure
  campaign on S3 (3 writers, 2 readers, one submitter polling its own run) a
  run held the writer gates **18.3 s** with a 2 s request (16.3 s over hold
  plus longest table, `B/cache_s3.json`). That outlier is not S8: it
  reproduced on the fixed build (9.7 s) and is N18 below.
- **Violates:** RFC §1.2 "The request bounds that wait"; RFC §3.5
  "Gate-hold time is its upper bound" is still true per run, but the request
  no longer bounds it. Pre-existing in #1282's code since v1 (not found then).
- **Fix (commit `5b0f0dcb`, stress branch):** a lookup whose answer no record
  access can change (a terminal operation, or an active one whose run is live
  in this process, with no ending owed) answers from an unlocked read and
  takes neither the gate nor the walk; everything else keeps the locked path.
  Unit test `only_a_settled_lookup_answers_without_the_record_gate`;
  `operation_routes` 3 passed; end to end the 4-poller S3 excess fell from a
  median 824 ms to −46 ms and lookups from 1.03 s to 34 ms. Admissions still
  take the gate (a busy client retrying 429s has the same effect, smaller);
  keeping per-table outcomes out of the retained list (N15) removes the size
  factor.

### Notes

- **N16 Direct index commands record any `--as` actor.** On a stopped cluster
  root, `omnigraph index build` and `index rebuild --kind full-text` accept
  `--as` for any string (a `change`-only writer, a reclaim-only actor, an actor
  the policy does not know) and publish a commit whose `actor_id` is that
  string; `optimize`, `cleanup` and `repair` refuse `--as`
  (`B/policy.json`, `direct`). This is documented ("Direct CLI access … does
  not load the server's Cedar policy; storage permissions are its trust
  boundary"), but the audit trail then carries an unverified maintenance
  actor, and the docs disagree on the default: maintenance.md says each
  command "acts as the operator configuration's `operator.actor`", policy.md
  says "The version-1 operator document has no actor field".
- **N17 Updates refused by optimize.** An update or delete prepared before an
  optimize publishes is refused `read_set_conflict` and not re-prepared
  (inserts are), as maintenance.md documents. Under back-to-back served runs
  this refuses **44% of a single writer's single-row updates locally (869 of
  1,956), 45% on S3 (72 of 159) and 49% on Azure (64 of 130)**, about one
  refusal per run, none without optimize (`C/upd_local.json`,
  `C/upd_s3.json`, `E/upd_azure.json`; the S3 second control is void, see
  `C/upd_s3_NOTE.txt`). Content-preserving maintenance thus costs
  writers refusals as well as waits (S7); spacing runs would bound both.
- **N9** (above): one id dated up to 60 s ahead closes admission on the whole
  cluster for up to that lead once it is evicted (42.5 s measured).
- **N18 History release inside the publication (pre-existing on main).** When
  a branch's in-manifest commit buffer reaches `HISTORY_RELEASE_BYTES`
  (256 KiB), the next publication writes the buffered commits to `__history`
  as blocks, one after another, while it holds the writer gates. On the
  1,030-table catalog that was 8 blocks of ~47 commits, **8.1–9.0 s locally**
  (debug build) and **~9.3 s on S3**, roughly every 380 commits (8 releases in
  10 minutes under the cache-pressure load). The run that carries a release
  overshoots its requested hold by that much: one run in ~114 per 10-minute
  run, 14.4 s locally, 15.6 s on the SUT and 9.7 s on the fixed build on S3,
  each overlapping a release exactly; every other release lands in a writer's
  publication and stalls every writer the same way
  (`D/history_releases.txt`, `D/*_runs.json`). The catalog crate is not in the
  PRs' diff (`git diff afc4be77 1c67541c -- crates/omnigraph-catalog` is
  empty). For #1282 it means the per-run writer-wait bound has an unbounded,
  catalog-sized term the request cannot control. Writing the blocks
  concurrently, or releasing outside the writer gates, would remove it.

## What held

Every campaign on the SUT kept the invariants from v1: no acknowledged write
lost, no double publish, nothing published by a failed, cancelled or
interrupted run, reads never blocked (they kept being answered through every
gate hold: 1,404 queries during the 17.6 s one), and every run's gate hold within
`max_gate_hold` + longest table + a publication that is small unless N18 or S8
applies. Read p99 stayed at 39 ms locally and 130 ms on S3; the slowest reads
(up to 3.1 s locally, 8.1 s on S3) cluster in gate holds, about two thirds of
the reads over 0.5 s (`D/cache_*`), which on this 2-core box fits CPU and I/O
contention from the rewrites rather than waiting on a gate.

- **Column-patch updates beside optimize (new code path).** Differential
  against a model (`c9_column_patch_optimize.py`): every title lookup through
  the `@index` column and every full scan matched the model after each
  update, load and optimize, and after a restart. Local 200 rounds (114
  updates, all column patches by the absence of new deletion files, 51
  optimizes), S3 60 rounds, Azure 60 rounds; with optimizes running
  concurrently (local 200 rounds and 266 runs, S3 60 / 97, Azure 60 / 174, and
  150 rounds on the small-cache variant). No `split of indexed and
  non-indexed data` compaction error and no stale index answer: Lance's
  planner kept withdrawn fragments apart, as maintenance.md says. The only
  refusals were the documented update conflicts (N17).
- **Maintenance policy.** Served optimize admits exactly the actors holding
  `maintain` on `main` (operator, maintainer); `maintain` on unprotected
  branches only, `change` only, `reclaim` only and `read` only are refused 403
  before acceptance with no record entry and no commit. 199,025 denied
  submissions in 120 s beside two writers: all 403, none in the record or
  history, 5,525 acknowledged rows intact (`B/policy.json`). The engine-level
  matrix is the PR's own `policy_engine_chassis`, which passes in the
  workspace run on the stress branch (whose engine policy code is the PR
  head's). Direct CLI: N16.
- **Cache pressure (#1319).** 1,030-table catalog, writers inserting and
  updating rows of 200 tables (local) or 120 (S3) against a 64-handle cache,
  readers checking every acknowledged row and value, back-to-back runs: no
  missing row, no stale value, also after a cold restart. Local: 4,529 writes,
  48,092 reads, 165 runs, 501 table compactions; S3: 1,091 writes, 25,336
  reads, 118 runs; two more S3 runs and a quiet local run in phase D the same.
  **Small-cache variant** (2 handles, 64 KiB): 3,182 writes, 28,434 reads, 116
  runs, no stale or missing value; a 10-minute hammer (1,994 operations, 4,022
  writes) clean.
- **Writers, optimize and reads under the new OCC placement (#1291).**
  20-minute hammers: local 3,340 operations / 6,521 writes, S3 1,029 / 1,262,
  Azure (15 min) 333 / 535, zero violations beyond the hammer's deliberate
  over-ceiling probe. Campaign 1 (embedded) on local and S3: every violation
  is B1's (152 of 152 counted by category). Logical equivalence before and
  after optimize: passed.
- **Crash, kill and chaos (reduced breadth).** 21 crash points end as in v1.
  Random kills: local 30 rounds (10,855 rows), with random seams 30 rounds (58
  seams, 9,977 rows), S3 15, Azure 20 (11 broken leases); chaos: local 30
  rounds (6 deployments), S3 15 (4), Azure 20 (5). Zero violations
  (`C/`).

## Could not test

- **Live S3 and live Azure**: RustFS and Azurite on loopback only; the
  latencies in S7, S8 and N18 are from a 2-core gVisor box with debug builds,
  so their size, not their presence, may differ in production.
- **A comparison build of the old head**: v1's binaries did not survive the
  reboot; old-head numbers come from v1's evidence, measured with the same
  harnesses and parameters.
- **A small handle-cache budget without a rebuild**: the budget is a
  constant, so the small-cache runs used a labeled instrumented variant.
- **An incremental full-text or scalar index fold over a column-patched
  fragment** (writes.md warns it must treat the fragment as moved): no
  OmniGraph writer adds delta segments today, so nothing reaches it.
- **Releases in S3's listing for the fixed build** were not inspected; the
  attribution of the fixed build's outlier to N18 rests on the identical
  pattern (one run per ten minutes, ~10 s) and the local and SUT evidence.
- `scripts/check-docs.py` (its pinned Python packages are not installed).

## Fix commits on the stress branch (not in the PRs)

The v1 fixes (`23e1d66f` B1, `8d02fc6e` S3, `de11e65d` S5, `08d2a0e7` S2
part, `fa5cd0ff` S1, `331d8485` + `24614965` S4, `5a1bbeec` S6, `53107bc9` N2,
`e189d893` S7 docs, `a6594475` N8) merged cleanly with the PR head
(`62f1f057`). New: `5b0f0dcb` S8.

Validation of the stress branch (PR head + v1 fixes + `5b0f0dcb`):
`cargo dev-nextest --no-fail-fast` ran 5,213 tests, 5,203 passed; the 10
failures are the same environmental ones as in v1 (8 `omnigraph-bench`
symlink and machine-identity probes,
`cli_cluster::managed_lifecycle_capture_and_retry_records_refuse_symlinks_without_reading_targets`,
`db::upgrade::tests::interrupted::unreadable_legacy_object_asks_for_a_rerun`;
`evidence-v2/nextest_stress_branch_summary.txt`), including the PR's
`policy_engine_chassis` maintain matrix. `cargo dev-clippy` and
`cargo fmt --all --check` are clean.

## How to reproduce

The v1 harnesses (see `REPORT.md`) take explicit binaries: `OG_BIN_DIR` (CLI,
server, Azure admission wrapper), `OG_CHILD_BIN` (seam-armed served child) and
the `engine_optimize_stress` binary. `phase_a.sh` reruns every prior
reproducer, `phase_b.sh`, `phase_c.sh` and `phase_d.sh` the new campaigns.
New harnesses: `c9_column_patch_optimize.py` (column-patch updates and
optimize against a model; `STRESS_CONCURRENT_OPT=1` runs optimizes
concurrently), `c10_policy.py` (maintenance policy, served and direct),
`c11_cache_pressure.py` (1,030-table catalog, acknowledged rows and values
under eviction), `c12_big_hold.py` (gate hold vs record size and lookup
pollers), `c_nitems.py` (N1, N2, N6, N9, N11, N13), and
`STRESS_WRITE_KIND=update` in `c1_writer_wait.py` (N17).

