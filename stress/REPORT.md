# Stress report, round 2: remote optimize (PR #1275 + PR #1282) after the fixes

**Under test:** `feat/optimize-served` at `0f2a80fa` (PR #1282), which contains
`feat/optimize-engine` at `0315d292` (PR #1275), merge base with main
`2c4b2f12`.
**Retest branch:** `stress/optimize-1282-retest` on
`https://github.com/ragnorc/omnigraph` (this report, the round-1 harnesses,
the new harnesses and evidence under `stress/evidence2/`).
**Round 1:** `stress/REPORT-round1.md` (moved from `stress/REPORT.md`).
**Box:** `nproc` 2, 64 GiB, gVisor. RustFS 1.0.0-beta.12 (official binary,
checksum matched the release `SHA256SUMS`) on 127.0.0.1:9000; Azurite 3.35.0
(npm) on 127.0.0.1:10000.
**Started** 2026-11-10 20:05 UTC.

_Work in progress; sections are filled as campaigns finish._

## Decisions taken alone

- The box had no `fork` remote; I added `fork` = `https://github.com/ragnorc/omnigraph.git`.
- The stress test targets (`stress_served_child`, `engine_optimize_stress`)
  are re-declared in the two crates' `Cargo.toml` on the retest branch, as
  round 1 did; they are harness-only.

## Baseline (new head `0f2a80fa`, before any change)

Run with the repository's own commands on this box (`cargo dev-build-tests`
built every target in 4 min from the warm target; `cargo dev-nextest` for
the workspace owners). Logs: `evidence2/baseline/`.

| Suite | Round 1 | Round 2 |
|---|---|---|
| engine `maintenance::` | (78 with optimize failpoints) | 70 passed |
| engine `failpoints` filtered to `optimize` (incl. `optimize_waits_for_a_writer_preparing_again_after_a_publication`) | | 15 passed |
| engine `write_queue::tests` | n/a | 19 passed |
| `detached_commit_matrix`, `OMNIGRAPH_MATRIX=full` | 29 passed, 1 skipped | 29 passed |
| server `operation_routes` + `recorded_operations::tests`, cluster `operations::tests`, CLI `served::tests`, `system_local::local_cli_optimize_runs_a_recorded_operation` | 20 passed | 3 + 3 + 14 + 4 + 1 = 25 passed |
| DST pinned suite (`crates/omnigraph-dst`, `cargo test --locked`), incl. `dst_bench_cost_count_golden` | 55 + 2 + 56 (33 ignored) | 55 + 2 + 56 passed (33 ignored) |
| `cargo test -p omnigraph-gqt --locked` | 462 cases + 52 + 14 + 29 | 467 cases + 52 + 14 + 29 passed |

All green. The known server telemetry/span flake was not in these
selections; see "Could not test".

## Campaign 1: regression check of the round-1 findings

Every reproduction is round 1's harness, unchanged except where stated, run
against the frozen PR-head binaries (`/tmp/r2bin`, built from `0f2a80fa`).
Evidence: `evidence2/regression/`.

| # | Round 1 | Round 2 | Evidence |
|---|---|---|---|
| B1 | blocking | **fixed.** Engine repro: planning 1/2/4/8 all plan the 12 tables (round 1: 10/9/7/3 falsely deferred). Served, planning 1: the resumed run plans all six tables, compacts both seeded ones and clears the position. Served, default settings, 1,030-table catalog: one run compacted 20 of 20 seeded tables (537 ms gate hold, round 1: 1 table per run); the next run is all `unchanged`. | `b1_engine_repro.txt`, `b1_served_early_exit.txt`, `b1_big_catalog.json` |
| S1 | should-fix | **fixed.** S3 (RustFS), late-landing record PUT at acceptance, `running`, `publishing` and final writes, plus never-landing at `publishing`/final: fresh submissions accepted at once (202), lookups 200, SIGTERM exits 0, restart in 0.4–0.5 s (round 1: 503 until restart, then 45.5 s), every record resolves (`interrupted` / `failed` / `succeeded` as the RFC says). Azure (Azurite) same four late cases: all resolve. | `s1_s3_lost_answer.jsonl`, `s1_az_lost_answer.jsonl` |
| S2 | should-fix | **fixed** (with two notes, R2-N1 and R2-N2 below). `faultfs` on a local root, nine cases (refused and lost-answer acceptance, `running`, `publishing` and final writes; sticky final; no-op final): no wedge in any. Lookups answer 200 during and after a one-shot fault; fresh submissions 202; SIGTERM exits 0 and releases the lock (no `force-unlock` needed in any case); a finished no-op run whose final write failed stays `succeeded` (round 1: stored `interrupted`); a lost answer whose rename landed is read back as applied (`succeeded`). A refused `running` write resolves `interrupted` immediately (round 1: `accepted` until restart). During a sticky fault lookups answer 503 and recover as soon as it clears, without restart. | `s2_local_record_write_faults.json` |
| S3 | should-fix | **fixed.** The submission that straddles the deployment is refused 503 (nothing accepted) and the deployment takes 3.3 s (round 1: run accepted, uncancelled, deployment 12.1 s). `affects_held` (cancelled, reason names the deployment, 4.6 s), `unaffected_held`, `delete_publishing`, `delete_running` unchanged. | `s3_c4_deploy.jsonl` |
| S4 | should-fix | **fixed.** SIGTERM 1 s into the artifact scan: stop in 4.06 s, exit 0, no edge opens or artifact rename after SIGTERM, record `succeeded` with artifact `skipped(cancelled)` (round 1: 25 s grace, exit 2, record `publishing`). | `s4_sigterm_artifact.json` |
| S5 | should-fix | **fixed.** `routes_disabled` exits 1. All 17 CLI cases of round 1 match the documented exits, and no case submitted twice. | `s5_c6_cli.json` |
| S6 | should-fix | **fixed**, one gap (R2-N3). The 409 names each record, its graph and settlement branch, and its state ("the graph head of main is still the input head"); writes on another graph settle none; a write on `kg` settles all four `failed / not_committed`. | `s6_unresolved_cap.txt` |
| N2 | note | **fixed.** The span carries `nonce` and `commit`, a `recorded operation ended` event carries the state and timings, and the terminal record keeps the nonce. Cosmetic: the span prints `nonce` twice (R2-N4). | server logs under `/tmp/r2/*` (excerpt in R2-N4) |
| N8 | note | **fixed.** S3, run B leaves the position at `edge:Knows`; run A's late settlement (`succeeded`) keeps it; the next run starts at `edge:Knows`. | `n8_stale_resume_s3.txt` |
| N1, N4, N5, N6, N13 | notes | **unchanged** (as round 1): an evicted id with an over-ceiling hold answers 400, not 409 `operation_id_expired`; a refused acceptance answers `effects: unknown` while saying "nothing was accepted"; a truncated record answers 409 `operation_record_invalid` on lookup and submit and is never overwritten; `tables: []` and duplicate tables are accepted (202) and fail in the engine; an export during a run answers 413 `stream_export_slots: actual 2, limit 1`. | `evidence2/notes/c11_notes.json`, `s2_local_record_write_faults.json` |
| N7 | note | unchanged: a graph-scoped lookup after deletion answers 404. | `s3_c4_deploy.jsonl` (`delete_publishing`) |
