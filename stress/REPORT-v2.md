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

<!-- VERDICT -->

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

<!-- BODY -->
