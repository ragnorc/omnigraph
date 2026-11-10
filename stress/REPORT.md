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
