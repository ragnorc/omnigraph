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
| S2 | should-fix | **fixed** (two notes, R2-N2 and R2-N3 below). `faultfs` on a local root, nine cases (refused and lost-answer acceptance, `running`, `publishing` and final writes; sticky final; no-op final): no wedge in any. Lookups answer 200 during and after a one-shot fault; fresh submissions 202; SIGTERM exits 0 and releases the lock (no `force-unlock` needed in any case); a finished no-op run whose final write failed stays `succeeded` (round 1: stored `interrupted`); a lost answer whose rename landed is read back as applied (`succeeded`). A refused `running` write resolves `interrupted` immediately (round 1: `accepted` until restart). During a sticky fault lookups answer 503 and recover as soon as it clears, without restart. | `s2_local_record_write_faults.json` |
| S3 | should-fix | **fixed.** The submission that straddles the deployment is refused 503 (nothing accepted) and the deployment takes 3.3 s (round 1: run accepted, uncancelled, deployment 12.1 s). `affects_held` (cancelled, reason names the deployment, 4.6 s), `unaffected_held`, `delete_publishing`, `delete_running` unchanged. | `s3_c4_deploy.jsonl` |
| S4 | should-fix | **fixed.** SIGTERM 1 s into the artifact scan: stop in 4.06 s, exit 0, no edge opens or artifact rename after SIGTERM, record `succeeded` with artifact `skipped(cancelled)` (round 1: 25 s grace, exit 2, record `publishing`). | `s4_sigterm_artifact.json` |
| S5 | should-fix | **fixed.** `routes_disabled` exits 1. All 17 CLI cases of round 1 match the documented exits, and no case submitted twice. | `s5_c6_cli.json` |
| S6 | should-fix | **fixed.** The 409 names each record, its graph and settlement branch, and its state ("the graph head of main is still the input head"; a walking record renders `walking: N commits covered, next page at <cursor>`, `recorded_operations.rs:757-784`, read in code, not reached by this harness); writes on another graph settle none; a write on `kg` settles all four `failed / not_committed`. | `s6_unresolved_cap.txt` |
| N2 | note | **fixed.** The span carries `nonce` and `commit`, a `recorded operation ended` event carries the state and timings, and the terminal record keeps the nonce. Cosmetic: the span prints `nonce` twice (R2-N4). | server logs under `/tmp/r2/*` (excerpt in R2-N4) |
| N8 | note | **fixed.** S3, run B leaves the position at `edge:Knows`; run A's late settlement (`succeeded`) keeps it; the next run starts at `edge:Knows`. | `n8_stale_resume_s3.txt` |
| N1, N4, N5, N6, N13 | notes | **unchanged** (as round 1): an evicted id with an over-ceiling hold answers 400, not 409 `operation_id_expired`; a refused acceptance answers `effects: unknown` while saying "nothing was accepted"; a truncated record answers 409 `operation_record_invalid` on lookup and submit and is never overwritten; `tables: []` and duplicate tables are accepted (202) and fail in the engine; an export during a run answers 413 `stream_export_slots: actual 2, limit 1`. | `evidence2/notes/c11_notes.json`, `s2_local_record_write_faults.json` |
| N7 | note | unchanged: a graph-scoped lookup after deletion answers 404. | `s3_c4_deploy.jsonl` (`delete_publishing`) |

## Campaign 2: S7 and the writer yield

### Method

`stress/c9_writer_yield.py` keeps round 1's `c1_writer_wait.py` shape so
the numbers compare: phases control / optimize / control2 (120 s here; round
1 used 180 s on S3), writers loading five-row Person/Company batches on
main, one submitter that submits the next full-catalog optimize
(`max_gate_hold_ms` 1000) as soon as the previous one ends. Per write it
records the latency from first attempt to acknowledgement and how many
optimize publications landed inside it (each run's `published_commit`
timed by main's commit list, so the count is exact); per run
`writer_yield_ms`, `gate_hold_ms`, `queued_ms`, `compute_ms`; re-prepares
from the server's `omnigraph::loader` debug lines. Options: four lookup
pollers at 100 ms, branch writers (on `side`), a mutator, more writers.
`stress/c9_yield_holders.py` classifies every yield of 2 s or more by the
write that held it. `stress/engine_writer_yield_stress.rs` (harness only)
drives the deterministic cases through engine seams with one shared handle,
as the server holds one per graph.

### Numbers

Round 1 (PR head of round 1, no yield; `REPORT-round1.md` S7):

| Setup | optimize-phase writes | write p99 / max | runs | worst write |
|---|---|---|---|---|
| S3, 2 writers, 180 s | 121 | 12.8 s / 14.0 s | 120 | 14 s across 9 runs; 100 of 121 writes overlapped 2+ runs |
| Azure, 2 writers, 120 s | 77 | 26.0 s / 26 s | 74 | 26 s across 14 runs |
| S3, 1 writer, 90 s | 85 (control 535) | max 4.4 s | 84 | one run + one preparation |
| local, 2 writers | no effect (p99 0.70 s) | | 1,293 | |

Round 2, PR head (`evidence2/s7_pr/`, `evidence2/s7_mix/`) and with the two
stress-branch fixes below (`evidence2/s7_fix/`). Mix = 3 (local) or 6 (S3)
loaders + 1 mutator + branch writers + 2 pollers, 90 s phases.

| Build | Setup | control writes / p99 / max (s) | optimize writes / p99 / max (s) | runs | runs at the 10 s limit | yield p50 / max (ms) | gate hold p50 / max (ms) | writes by optimize publications inside them |
|---|---|---|---|---|---|---|---|---|
| PR | S3, 2 writers | 630 / 0.34 / 2.83 | 344 / 1.54 / 7.27 | 14 | 5 | 9431 / 10001 | 607 / 769 | {'0': 331, '1': 13} |
| PR | S3, 2 writers + 4 pollers | 595 / 0.39 / 10.46 | 330 / 12.16 / 12.86 | 14 | 8 | 10000 / 10004 | 612 / 801 | {'0': 317, '1': 9, '2': 4} |
| PR | S3, 1 writer | 645 / 0.34 / 0.37 | 176 / 1.39 / 1.43 | 88 | 0 | 444 / 636 | 541 / 936 | {'0': 88, '1': 88} |
| PR | Azure, 2 writers | 371 / 0.65 / 4.93 | 457 / 3.14 / 12.13 | 16 | 2 | 6943 / 10016 | 459 / 1302 | {'0': 440, '1': 15, '2': 2} |
| PR | Azure, 2 writers + 4 pollers | 250 / 0.99 / 16.01 | 334 / 3.93 / 15.29 | 12 | 4 | 9792 / 10002 | 620 / 1480 | {'0': 323, '1': 9, '2': 2} |
| PR | local, 2 writers | 1434 / 0.70 / 3.71 | 1552 / 0.41 / 0.69 | 627 | 0 | 57 / 528 | 69 / 422 | {'0': 300, '1': 1252} |
| PR | local, 2 writers + 4 pollers | 1452 / 0.68 / 2.98 | 1581 / 0.38 / 0.61 | 643 | 0 | 54 / 257 | 69 / 476 | {'0': 297, '1': 1284} |
| PR | local mix (90 s) | 968 / 2.19 / 4.21 | 1338 / 1.75 / 4.12 | 10 | 8 | 10000 / 10001 | 136 / 364 | {'0': 1302, '1': 36} |
| PR | S3 mix (90 s) | 349 / 8.66 / 14.26 | 183 / 14.99 / 17.60 | 9 | 8 | 10000 / 10001 | 641 / 871 | {'0': 171, '1': 9, '2': 3} |
| fix | S3, 2 writers | 592 / 0.40 / 12.30 | 327 / 4.91 / 12.34 | 15 | 9 | 10000 / 10552 | 571 / 1093 | {'0': 313, '1': 11, '2': 3} |
| fix | S3, 2 writers + 4 pollers | 581 / 0.39 / 6.66 | 318 / 1.45 / 13.77 | 14 | 8 | 10000 / 10001 | 617 / 882 | {'0': 305, '1': 10, '2': 3} |
| fix | Azure, 2 writers | 200 / 1.52 / 1.92 | 240 / 12.99 / 13.20 | 14 | 8 | 10001 / 10002 | 835 / 1908 | {'0': 225, '1': 12, '2': 3} |
| fix | local, 2 writers | 1456 / 0.72 / 3.02 | 1336 / 0.45 / 0.73 | 558 | 0 | 66 / 558 | 79 / 418 | {'0': 220, '1': 1116} |
| fix | local mix (90 s) | 1017 / 2.20 / 4.20 | 1436 / 1.14 / 2.22 | 158 | 0 | 359 / 1760 | 86 / 390 | {'0': 807, '1': 629} |
| fix | S3 mix (90 s) | 336 / 2.88 / 14.51 | 175 / 15.38 / 15.41 | 9 | 8 | 10000 / 10008 | 610 / 1143 | {'0': 162, '1': 10, '2': 3} |

The last column counts, per acknowledged main write, the optimize
publications that landed between its first attempt and its
acknowledgement (`{publications: writes}`). Yield holders
(`evidence2/yield_holders.txt`): on S3 and Azure, every yield of 2 s or more
(PR: 12/12, 12/12, 15/15, 11/11, 8/8; fix: 11/11, 11/11, 12/12, 8/8) was
held by a write that was in flight when the previous run published; on the
PR head's local mix, all 9 were held only by writes that started after it.

### What the numbers say

- **The claim holds per write.** No write saw more than two optimize
  publications in any configuration (round 1: up to 14). A write that sees
  two is either a starved write that outlasted the 10 s limit (its latency
  is 12–15 s, `max_writer_yield` plus a run, the documented exception) or
  the queued-writer race below (R2-S1). Slowest write on S3: 7.3–12.9 s
  (round 1: 14 s); on Azure 12–15 s (round 1: 26 s); write p99 on S3 1.5 s
  without pollers (round 1: 12.8 s). With one writer the writer and the
  runs alternate: every write re-prepares once, the run waits ~0.45 s for
  it, the slowest write is 1.43 s (round 1: 4.4 s); writes fall 3.7x against
  the control (round 1: 6.3x), because every write still prepares twice
  (#1331).
- **Optimize stalls on object storage with two or more writers (R2-N1).**
  5–9 of 14–15 runs on S3 and 2–8 of 12–16 on Azure wait the full 10 s: 14
  runs in 120 s against round 1's 120 in 180 s. A publication sends every
  write in flight back at once; the writers then send each other back (the
  pre-existing N12 conflict: in the control phases 17–58 writes exhaust
  their 32 attempts and answer 409), and the run waits for the one that
  keeps losing until the limit. Fix B does not change this (the starved
  write did start before the publication); #1331 is the remedy.
- **The PR head also waits for writers that only other writers sent back
  (R2-S2).** On a local root with three loaders and a mutator the PR head
  ran 10 optimize runs in 90 s, 8 of them at the 10 s limit, every long
  yield held only by writes that started after the previous publication.
  Fix B: 158 runs, none at the limit, yield p50 359 ms, and write p99 fell
  from 1.75 s to 1.14 s.
- **Pollers** (4 × 100 ms lookups) change nothing systematic: lookup p99 20
  ms local, 0.28 s Azure, and the p99 swing on S3 (1.5 s vs 12.2 s on the
  PR head, 4.9 s vs 1.45 s with the fix) follows how many starvation events
  a phase happened to contain.
- **Branch writers** (`side`) are untouched by main's runs: their p99 is
  the same with and without optimize (local 0.16–0.21 s; S3 1.3–1.5 s). One
  40.9 s branch write in the S3 mix is the two branch writers starving each
  other (an 18.4 s one in the control).
- **Invariants:** every phase of every run: no acknowledged row missing,
  record shape valid, every run `succeeded`, SIGTERM exit 0.

### Break attempts (engine probes, `engine_writer_yield_stress.rs`)

| Probe | Result |
|---|---|
| `max_writer_yield` 0 / 5 ms / 50 ms | the run waits 1.1 / 6.1 / 51 ms, publishes over the marked writer, which prepares once more and commits |
| limit longer than the gate-hold bound (30 s) | the run waits exactly until the writer returns (0.3 s), then takes the gates |
| marked writer's task aborted (dropped) | the waiting run stops at the drop (yield 308 ms of a 30 s limit) |
| marked writer panics | the mark drops on unwind; the run's yield ends at the panic (308 ms) |
| writer preparing again on a branch | a main run does not wait (7 µs) |
| reads during a yield | 0.47 ms; the yield holds no gate (a writer commits under the gates while the run yields) |
| writer queued behind another writer when the next run starts | **the run overtakes it** (R2-S1): yield 16 µs, the run publishes, the write prepares three times |
| run cancelled during its yield | **the run takes main's gates before it returns** (R2-N5): it returned 1.5 s after the cancel, only when the writer holding the gates let go; it published nothing |

### Served events during a yield (`c12_yield_events.py`, S3)

Two writers keep a run in its yield; the event lands ~1.5 s into it.

| Event | PR head | With Fix A |
|---|---|---|
| none | `succeeded` after a 2.5 s yield | `succeeded` after a 2.3 s yield |
| cancel | `cancelled` 82 ms after the request, gate hold 5 ms | (the run had finished before the cancel landed) |
| SIGTERM | exit 0 in 0.32 s, `cancelled` (shutting down), gate hold 4 ms | exit 0 in 0.27 s, gate hold 0 |
| SIGKILL | `interrupted` after restart, nothing published | same |
| deployment (schema change) | deployment 0.97 s, `cancelled` naming it, gate hold 3 ms | 1.0 s, gate hold 0 |

No invariant broke in any case: no acknowledged row lost, nothing published
by a non-succeeded run, no record left `running`.

## Campaign 3: CLI `--timeout` and `/access`

`stress/c10_cli_timeout.py` through the TLS fault proxy (a new
`stall_request` fault keeps the connection open and never forwards).
Evidence: `evidence2/cli/`.

| Case | `--timeout` | exit | wall time from process start | later `--operation <id>` |
|---|---|---|---|---|
| submission's answer held 20 s (the server ran it) | 3s | 2, "stopped waiting" + hint | 3.04 s | exit 0, `succeeded` |
| submission never forwarded, connection open | 3s | 2, same | 3.04 s | exit 2, "the server holds no record" (true: it never arrived) |
| every lookup's answer held 30 s | 3s | 2, same | 3.06 s | exit 0, `succeeded` |
| run parked 20 s, normal polling | 2s | 2, same | 2.04 s | exit 0, `succeeded` |
| lookups blackholed | 3s | 2, same | 3.04 s | exit 0, `succeeded` |
| `0s` | 0s | 2, same; nothing sent | 0.04 s | exit 2, no record |
| `200ms` | | 1: "invalid duration '200ms'" (R2-N6) | | |

Every case submitted at most once. The bound covers the whole served path
of a server target; for a managed target the resource discovery in
`GraphClient::from_target` runs before the deadline starts (R2-N7, code
reading only).

`/access` (`evidence2/cli/c10_cli_timeout.json`, cases `access*`): with the
fixture policy the operator sees `maintain` graph-wide and on `main` and
optimize is accepted; writer and reader see no `maintain` and are refused
403. With `maintain` granted only on protected branches (`main`), the
operator's graph-wide list omits it (as documented) and `?branch=main`
lists it, and optimize is accepted; a writer granted `maintain` only on
unprotected branches sees it for `?branch=feature`, is refused on main, and
no served operation can use that grant (R2-N8). Anonymous: 401.

## New findings

### R2-S1 (should-fix): the next run overtakes a writer the last run sent back that is still queued behind another writer

- **What:** the yield waits for writers holding a mark, and a writer takes
  its mark only when it sees `ReadSetChanged` (`loader/mod.rs:791`,
  `exec/mutation.rs:1335`, `exec/blob_write.rs:198`), which it learns only
  after it holds main's gates and revalidates (`exec/staging.rs:1208-1224`).
  A writer W that prepared before run A published, and that is queued at
  the gates behind another writer X when run B starts, holds no mark: B's
  yield passes at once, B queues behind W, W takes the gates, finds main
  moved, marks and leaves, and B publishes over it. W prepares three times
  and waits through A and B.
- **Reproduction:** `engine_writer_yield_stress --ignored --exact
  wy_writer_queued_behind_a_writer_is_overtaken_by_the_next_run` (one
  handle; W parked before its gates while A publishes; X parked under the
  gates after its table commit; B with `UnderGates` as the server asks).
  Result on the PR head: "W attempts 3, B writer_yield 16 µs, B published".
  Regression test in the owner: `failpoints.rs::optimize_waits_for_a_writer_still_queued_behind_another_after_a_publication`,
  red on the PR head (B's yield < 200 ms), green with Fix B.
- **Served evidence:** writes with two optimize publications inside them,
  2–4 per 120 s phase on S3 and Azure (table above). Most of them are
  starved writes past the 10 s limit, so this race is bounded to one extra
  run and rare in practice.
- **Violates:** RFC §1.2 "A writer that prepares again adds its own
  preparation, and no further runs."
- **Fix (verified, commit `301286a2`, Fix B):** see R2-S2. Reproduces on
  `origin/main`: no (the yield is new in #1282).

### R2-S2 (should-fix): the yield waits for writers that other writers sent back

- **What:** any `ReadSetChanged` marks the writer, including one caused by
  another writer's commit. With three or more concurrent writers on main a
  mark is almost always held, so every run waits the full `max_writer_yield`.
- **Reproduction:** `PHASES=control,optimize BRANCH_WRITERS=1 MUTATORS=1
  POLLERS=2 c9_writer_yield.py OUT 90 3` on a local root. PR head: 10 runs
  in 90 s, 8 at the 10 s limit, every long yield held only by writes that
  started after the last publication (`evidence2/s7_mix/pr_local_mix.json`,
  `evidence2/yield_holders.txt`). Deterministic:
  `failpoints.rs::optimize_does_not_wait_for_writers_another_writer_sent_back`
  waits the full 30 s limit on the PR head (red), 0 s with Fix B.
- **Violates:** RFC §1.2 / Contract ("a run waits for writers it sent back
  to prepare again") and `maintenance.md` ("for writes a run sent back").
- **Cause:** the mark records that a writer is preparing again, not why.
- **Fix (verified, commit `301286a2`, Fix B):** every write holds a ticket
  from its first preparation until it returns, stamped with the number of
  optimize publications on its branch when it started
  (`WriteQueueManager::writer_in_flight`); optimize bumps that number under
  its gates after it publishes (`optimize_published`); the yield waits for
  tickets older than the current number. That covers the writers the last
  run sent back whether they are preparing again or queued (R2-S1) and
  none other. Only such a ticket crosses `gate.writer_yield.release` and
  takes a DST turn on drop, as a marked writer did. Results: local mix 158
  runs (none at the limit, yield p50 359 ms, write p99 1.75 → 1.14 s); local
  two writers unchanged (558 runs, yield p50 66 ms); S3/Azure unchanged (the
  stall there is R2-N1). Verified: engine `write_queue::tests` (rewritten
  for the ticket), optimize failpoints including the PR's own
  `optimize_waits_for_a_writer_preparing_again_after_a_publication`,
  `maintenance::`, `detached_commit_matrix` (full), `writes`, Blob tests,
  server `operation_routes`, cluster `operations::tests`: 314 passed; DST
  pinned suite incl. the cost golden 55 + 2 + 56 passed; `cargo dev-clippy`
  and `cargo fmt --all --check` clean. RFC §1.2 and `maintenance.md` updated
  in the same commit. Not on `origin/main` (new in #1282).

### R2-N1 (note, measured; should be stated in the docs): on object storage with two or more writers most runs wait the full `max_writer_yield`

- **What:** one publication sends every write in flight back at once; they
  then send each other back (pre-existing N12: in control phases without
  optimize, 17–58 writes per phase exhaust their 32 attempts and answer
  409), and the run waits for the write that keeps losing until its limit.
  S3: 5–9 of 14–15 runs at 10 s; Azure 2–8 of 12–16; S3 mix 8 of 9. Each
  served optimize then takes ~10 s longer, and optimize runs about 8x less
  often than in round 1 (14 per 120 s vs 120 per 180 s). The writes are
  better off (p99 1.5 s vs 12.8 s on S3) and the bound holds as documented.
- **Why it is a note:** within the documented bound and caused by #1331's
  whole-branch authority; Fix B does not change it (11/11 long yields on
  S3 with Fix B still held by a write in flight across the publication).
  The user docs should say that under continuous concurrent writers a run
  typically waits the whole `max_writer_yield`. Not on `origin/main` (the
  yield is new); the starvation itself is.

### R2-N5 (note, fixed on the branch): a run cancelled during its yield still takes main's gates before it returns

- **What:** the yield ends on cancellation, but the run then reserves its
  export exclusion, captures authority, takes the shared schema permit and
  queues for main's branch and table gates; only the pipeline's first check
  sees the cancellation (`optimize.rs:913-972`). A cancel, SIGTERM or
  deployment drain that lands in the yield therefore also waits for the
  writers queued at the gates, and the run holds the gates for a moment.
- **Evidence:** probe `wy_cancelled_during_the_yield`: returned 1.5 s after
  the cancel, exactly when the writer holding the gates was released. On S3
  (`c12_yield_events.py`) the cancelled runs reported 3–5 ms of gate hold
  after the cancel; small because no writer held the gates long there.
- **Violates (weakly):** RFC §3.4's drain bound ("at most one table's
  rewrite, the publication, the configuration clear and an issued artifact
  write") gains one writer gate queue.
- **Fix (verified, commit `d389846c`, Fix A):** after the catalog is loaded
  under the shared schema permit, a cancelled run reports every table
  `deferred(cancelled)` without taking the branch or table gates. Regression
  test `failpoints.rs::optimize_cancelled_during_its_writer_yield_returns_without_the_gates`
  (red on the PR head: timed out after 5 s; green with the fix). On S3 the
  cancelled runs then report gate hold 0. A run cancelled while already
  queued at the gates still waits for them (pre-existing in #1275; racing
  the gate acquisition against the cancellation would close that too).

### Other notes

- **R2-N2 (S2 leftovers, docs/message).** On a local root a failed record
  write no longer wedges anything, but the 503's message and
  `recorded-operations.md:62` / the OpenAPI 503 description still say "a
  refusal that names an unsettled earlier request holds until the server
  restarts", which is no longer true on a local root (or, after S1, on
  S3). During a sticky fault lookups answer 503 with that sentence; they
  recover as soon as the fault clears. The server also logs
  `ERROR … outcome unknown on a mutable key; this key is now unsettled`
  and `WARN … publishing write is in doubt` for a local write that was then
  settled by its read-back as not applied.
- **R2-N3 (S2 leftover).** A refused `publishing` write on a local root
  leaves the run stored `interrupted` ("no live run held it") although the
  server knew the run stopped before publishing; `failed` with the record
  error would be more accurate. Nothing published.
- **R2-N4 (N2 cosmetic).** The span renders `nonce=…` twice
  (`recorded_operation{… nonce="X" nonce="X" commit=…}`).
- **R2-N6.** `--timeout 200ms` is refused ("invalid duration"): the shared
  `parse_duration_arg` (`omnigraph-cli/src/helpers.rs:511`) has no `ms`
  unit; pre-existing parser, also used by `--max-gate-hold`.
- **R2-N7.** For a managed target the CLI resolves the resource
  (`GraphClient::from_target` → `managed::data::resource`) before the
  `--timeout` deadline starts (`maintenance/mod.rs:58-66`), so a stalled
  control plane is not bounded by `--timeout`. Code reading only.
- **R2-N8.** `/access?branch=<non-main>` reports `maintain` when a rule
  allows it there, though no served operation uses `maintain` off main.
- **R2-N9 (harness observation, pre-existing).** With two engine handles on
  one root in one process, a mutation's revalidation under the shared gates
  compares against its own handle's coordinator, so a run published through
  the other handle is caught only at the publish CAS, after detached table
  commits. Correctness holds (the CAS refuses); the server uses one handle
  per graph. Seen while building the probes.
- **Round-1 notes still open:** N1, N4, N5, N6, N7, N13 (table above); N3,
  N9, N11, N12, N14, N15 not retested beyond what the campaigns touched
  (N12 is the cause of R2-N1).

## Campaign 4: reduced crash and chaos round

Round 1's `c3_random_kills.py` and `c7_chaos.py`, unchanged, plus the yield
events of campaign 2. On S3 and Azure with two or more writers a run spends
most of its life in the yield (R2-N1), so random kills during `running`
land in the yield or right after it most of the time; `c12_yield_events.py`
targets the yield itself. Evidence: `evidence2/chaos/`.

| Campaign (binaries) | Rounds | Events | Operations | Acknowledged rows | Violations |
|---|---|---|---|---|---|
| random kills, S3 (PR) | 20 | 12 SIGKILL, 8 SIGTERM | 39 | 2,735 | 0 |
| chaos, S3 (PR) | 15 | 5 deployments, 10 kills, cancels throughout | 51 | 2,171 | 0 |
| random kills, local (PR) | 25 | 18 SIGKILL, 7 SIGTERM | 51 | 11,843 | 0 |
| chaos, local (PR) | 20 | 6 deployments, 14 kills, cancels throughout | 70 | 8,029 | 0 |
| chaos, Azure (PR) | 10 | 4 deployments, 6 kills, lease breaks | 23 | 421 | 0 |
| random kills with 50 random seam faults, local (PR) | 25 | 19 SIGKILL, 6 SIGTERM | 53 | 10,785 | 0 |
| chaos, S3 (Fix A + B) | 10 | 1 deployment, 9 kills, cancels throughout | 44 | 1,511 | 0 |
| yield events, S3 (PR and Fix A + B) | 5 + 5 | cancel, SIGTERM, SIGKILL, deployment in the yield | 10 | | 0 |

Each harness checks after every restart: every acknowledged row present;
every operation a client saw resolves and a terminal state is never
rewritten; a run reported failed/cancelled/interrupted published nothing;
at most one commit answers a nonce; at most four unresolved, 32 terminal;
the eviction mark never moves back; a deployment that cancelled a run is
named in its record. After the local kill campaign, `cleanup --keep 5`
left `main` and every retained commit readable (`c8_cleanup_retained.txt`;
commits whose versions were reclaimed answer 500, round 1's N11).

The embedded campaign (`engine_optimize_beside_live_writers`, 90 s phases,
random `tables`, `start_at`, gate holds, ceilings, budgets, cancellation and
both authority modes, Fix A + B build): 189 runs, 120 published, beside
2,329 writes, 0 violations; every run visited each table once in order and
`resume_at` stayed consistent, which rechecks B1 with `tables` and
`start_at` subsets (`evidence2/chaos/c1_engine_90s_fixbuild.log`).

## Could not test

- **Live Azure, real S3 latency, in-region qualification:** RustFS and
  Azurite on loopback only, as in round 1. The stall of R2-N1 depends on
  how long a preparation takes against how often writers commit; real S3
  latency will change the proportions, not the mechanism.
- **`max_writer_yield` over HTTP:** it is not settable over HTTP (by design,
  RFC §1.2), so every served run used the default 10 s; 0, a few ms, and a
  limit longer than the gate-hold bound were tested at the engine API only.
- **A kill at an exact point inside the served yield:** the PR has no seam
  inside the yield that the served child can park on (the
  `gate.writer_yield.*` seams are observation-only). Kills, SIGTERM, cancel
  and deployment during the yield were placed by timing (`c12`, ~1.5 s into
  a yield the record and `writer_yield_ms` confirm), and random kills in
  campaign 4 land in or just after a yield most of the time on S3.
- **CLI `--timeout` on a managed target:** no managed control plane on the
  box; R2-N7 is from reading the code.
- **The S2 read-back on S3:** `settle_returned_local_write` runs only for
  the local codec by design; the S3 counterpart is S1's settle-on-read,
  exercised by the lost-answer cases.
- **Fix B alone:** the Fix B commit was not built and tested on its own;
  Fix A and Fix B were built, tested and measured together (the regression
  test of each was red on the PR head and green on the pair).
- **`origin/main` comparison builds:** not built. "Reproduces on main" is
  judged from the code: the writer yield, the CLI `--timeout` and the
  served optimize do not exist on main.
- **`scripts/check-docs.py`** ran (pinned packages installed in a venv):
  `Documentation OK (494 Markdown files checked)` on the docs commits.

## Commits on the retest branch

| Commit | What |
|---|---|
| `adc77033` | round-1 harnesses brought over, round-1 report moved to `REPORT-round1.md`, stress test targets declared, baseline and regression evidence |
| `01d14124` | harnesses `c9_writer_yield.py`, `c10_cli_timeout.py` (+ `tlsproxy.py` `stall_request`), `c11_notes.py`; PR-binary evidence |
| `34d73261` | harness only: `stress/engine_writer_yield_stress.rs` (target `engine_writer_yield_stress`) |
| `301286a2` | **Fix B** (R2-S1, R2-S2): the yield waits for writes in flight across the last optimize publication; two regression tests in `failpoints.rs`; RFC §1.2 and `maintenance.md` |
| `d389846c` | **Fix A** (R2-N5): a run cancelled in its yield returns before the gates; regression test in `failpoints.rs` |
| `7a79342b`, `fb000900`, `34a3e2a6` | after-fix and mix evidence, `c12_yield_events.py`, report |
| `53ef07f4` | docs: `maintenance.md` says a run under steady concurrent writes often waits the whole yield (R2-N1) |

## How to reproduce

As round 1 ("How to reproduce" in `REPORT-round1.md`): RustFS 1.0.0-beta.12
on 127.0.0.1:9000 (`omnigraphci` / `omnigraphci-secret`, bucket
`omnigraph-stress` via `s3util.py mb`), Azurite 3.35.0 on 127.0.0.1:10000
(container `omnigraph-stress` via `azutil.ensure_container`), a CA and a
127.0.0.1 server certificate under `/tmp/tls` (`ca.pem`, `cert.pem`,
`key.pem`) for the TLS proxy. Build with `cargo dev-build-tests`; copy
`omnigraph`, `omnigraph-server`, `omnigraph-azure-admission` and the
`stress_served_child-*` test binary (as `stress_served_child`) into one
directory and pass it as `OG_BIN_DIR` (the PR-head set was `/tmp/r2bin`, the
fixed set `/tmp/r2fix`). `run_queue2.sh`, `run_queue3.sh` and
`run_queue4.sh` are the exact campaign lists of this round;
`c9_summary.py` and `c9_yield_holders.py` summarize `c9_writer_yield.py`
output.
