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
