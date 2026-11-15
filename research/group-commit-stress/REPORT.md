# Group commit under stress: report

**Branch:** `research/group-commit-stress` on the fork `ragnorc/omnigraph`:
the prototype `research/write-throughput` (`ad39204a`, closed draft #1401)
merged with the per-branch writer-state cache (PR #1407, `e2836ce3`) and
`origin/main` (`a70e7b6a`). **Date:** 2026-11-15. **Subject:** the RFC
"Group commit with known-key admission" (PR #1405,
`docs/rfcs/2026-11-14-group-commit-with-known-key-admission.md` on
`rfc/group-commit-known-keys`). **Status:** research. Every fix below is a
change to the prototype on this branch, not production code. Commands, raw
numbers and every intermediate verdict are in [`LOG.md`](LOG.md); histories,
summaries and logs are under `histories/`, `served/`, `mp/`, `results/`.

## Summary

**Breaking it.** Eleven defects in the prototype (plus a performance cliff
inherited from `main`'s keyed-write path and three operational findings),
five of them able to lose correctness or liveness on real binaries under
load: admission across a
hole in the effect log (duplicate keys, dangling edges); a dropped leader
that hangs its group forever; a refusal storm that never recovers after any
unexplained publication (S3 soak: 122 → 2.35 writes/s after the first
`optimize`); maintenance publications left unexplained; and refused entries
re-preparing outside the gates, so that with two actors or two handles
5–7% of writes (40% of fresh inserts in a hot-key mix) fail to the caller
after 32 attempts. One more lives in the
prototype's publisher trim, outside batching: a stale trusted image clears
the process's writer-epoch holder, after which writes are fenced. All are
reproduced by a focused test or a seeded run, root-caused and fixed on this
branch, and re-verified (7 focused repros, randomized and served runs,
crashes at every point of the batch path, cleanup afterwards). No
acknowledged write was ever lost or duplicated once the fixes were in.

**What the evidence says about the RFC.** Its correctness argument holds
where the prototype follows it: with a contiguous log, a per-branch group,
the gated second attempt and exact keys, every oracle stayed green across
crash points, kill -9, toxiproxy faults (resets, truncated acknowledgements,
stalls), cancellation, several processes, several actors and handles, Blob
values and maintenance alongside. Its throughput claims hold only for pure
inserts. Two results contradict it as written:

1. **Under a mixed workload the RFC's rules remove almost all of the gain.**
   An update or delete logged as `changed` for its table refuses every queued
   insert there, and each refused insert then prepares and publishes alone
   under the branch gate. On RustFS (+30 ms) with 70% fresh inserts and
   ~15% same-table updates and deletes: 3.2 writes/s, against 3.0 with group
   commit off (p50 19.6 s); at 1,024 writers every batch held one write.
   Logging the exact ids such writes touched (improvement 1) restores
   batching without giving up the two-attempt bound: 9.0 writes/s, 4.07
   writes/commit, p99 32 → 12 s on the same RustFS run.
2. **Maintenance and fragment growth are not side issues.** One fragment per
   write makes every per-write pass O(fragments); without maintenance the
   rate decays 27× in 40 s locally, and past 8,192 unindexed fragments every
   keyed insert becomes a full-table merge join (0 writes/s). Direct-path
   `optimize` and index builds never land under batching (0 of 16), the
   served `optimize` is refused outright with ≥64 writers (a hard-coded
   observer cap), and the RFC's soak cannot run on the served path as
   specified. With served `optimize` every minute the S3 soak held ~100
   writes/s for an hour, 362,706 writes verified, both RFC soak gates
   passed (§6), while RSS grew from 2.6 to 5.2 GB.

**Improving it.** Best three, each measured before and after on the same
instrument and seed: (1) exact footprints for writes outside the group,
**2.75× writes/s on RustFS** (3.3 → 9.0, p99 32.3 → 11.9 s) on the mixed
workload with the gated retry kept, and 1.0 → 15.6 writes/commit at 1,024
writers; (2) lifting the 8,192 probe cap when known keys cover the tail,
which removes a cliff to 0 writes/s (release, served: **27–36 → 126–136
writes/s** once past it); (3) memoizing the known-tail analysis per table
version, **2.0× mean writes/s** on pure inserts in the dev-build harness
(smaller in release). The H14 fix is a prerequisite rather than an
improvement (the S3 soak's minute-2 rate from 2.35 to ~114 writes/s,
sustained). Lance upstream asks with exact code locations are in §8.


## 1. Setup and method

- **Merges.** `research/group-commit-stress` = `ad39204a` (prototype) + PR #1407
  (`5077eaa8`) + `origin/main` (`0cc5d359`). The only conflicts were in
  `db/omnigraph.rs`: #1407 replaced the one-entry `merge_authority_cache` with
  the per-branch `branch_authorities` LRU; the prototype had added
  `group_commits` beside it and an `install_branch_authority` that wrote the
  old slot. Resolved by keeping both fields and porting the install to
  `branch_authorities.install(.., None)`, which keeps a newer entry of the
  same lifetime. `origin/main` merged cleanly.
- **Binaries.** `omnigraph` and `omnigraph-server` built with the release
  profile (opt-level 2, thin LTO, line tables kept): `/srv/bins/proto-unfixed`
  (the merge), then one build per fix wave (`proto-fixed` … `proto-fixed4`,
  and `proto-final` with improvements 2 and 3 on by default). The soak ran
  `proto-fixed3`: every fix H1–H14, without improvements 2 and 3.
  Group commit is the prototype's process switch `OMNIGRAPH_GROUP_COMMIT=1`.
- **Backends.** Local disk; RustFS `1.0.0-beta.12` behind toxiproxy `v2.13.0`
  at +15 ms each way (32 ms round trip, probed before and after every S3
  run: `scripts/proxy-rtt.sh`), the CI-pinned versions #1401 used, checksums
  verified. Azurite not run (no Node package cache for it on the box; the
  Azure path shares the engine code exercised here, not its provider).
- **Drivers.**
  1. `crates/omnigraph/tests/group_commit_stress.rs`: seven focused repros
     (seams park a write at a chosen point) and `gc_stress_harness`, a seeded
     embedded harness (`group_commit_stress/harness.rs`, `OG_GS_*`): fresh
     and hot-key inserts, two-row and two-table inserts, updates, deletes,
     Person upserts and deletes with edge cascade, `Knows` edges, `@unique`
     Tags; several actors or handles; cancellation by timeout; readers
     recording pinned snapshots; read-your-writes probes; optimize, index
     builds, cleanup, branch churn, schema apply alongside; several processes
     on one root (`OG_GS_URI`); a writer-epoch claim mid-load.
  2. `scripts/served_run.py` + `scripts/http_stress.py`: real
     `omnigraph-server` on a fresh cluster root (16 bearer-token actors),
     closed-loop HTTP writers, readers that record the exact
     `graph_commit_id` each read was served from (full rows or `count`),
     per-minute metrics (server RSS and CPU), recorded `optimize` operations,
     toxiproxy fault windows, kill -9 with lock release and restart, research
     crash points (`OMNIGRAPH_STRESS_CRASH`) with a restart watchdog.
  3. `scripts/mp_run.py`: P embedded harness processes on one root, CLI
     export and commit list, one combined check.
- **Oracles** (every run): every acknowledged receipt names a commit that
  `list_commits` holds at that version with the write's actor; main's lineage
  is one chain; no duplicate key (`Item.slug`, `Person.name`), no dangling
  edge, `@unique` held; a model replay of every acknowledged write in commit
  order equals the final state and every reader snapshot, except keys an
  in-doubt write touched (cancelled, disconnected, crashed), which may hold
  its effect at most once; no two writes of one commit share a key; the
  graph reopens and, after chaos, `cleanup` succeeds and changes no row.

## 2. Scenarios run

| scenario | where | outcome |
|---|---|---|
| Insert-only, 1–64 writers, local and S3 | embedded, served | batches of 13–28, 0 violations |
| Colliding keys (64 and 16 hot keys), same key in one batch and across batches | embedded, served | caught H1 (with parking), clean after fixes |
| Upserts, updates, deletes interleaved with batched inserts | embedded, served | caught H5/R1; clean after fixes |
| Edges with endpoints, endpoint deletes with cascade | embedded, served | caught H1 (dangling edge), H10 |
| `@unique` (non-key) and two-table writes | embedded, served | stay on the ordinary path; clean |
| Blob values (5 B to 300 KB, inline and dedicated v2) | focused repro | composed and read back exactly, before and after optimize |
| 2 and 4 actors; 2 handles in one process | embedded, served | H2, H5; clean after fixes |
| Cancellation (timeouts 1 ms–1.5 s, up to 50% of writes) | embedded | H3, H4; clean after fixes |
| kill -9 of the server (4× in 90 s) | served local | 0 violations |
| Crash points between detached commits and the CAS (8 aborts) | served local | 0 violations; cleanup reclaimed exactly 8 unpublished manifests |
| Resets, truncated acknowledgements, stalls, jitter (17 windows) | served S3 | 0 violations; lost-ack re-issue path exercised |
| Optimize, index builds, cleanup, branch create/merge/delete, schema apply alongside | embedded, served | H13, H14, maintenance starvation |
| Writer epoch claim mid-load; several processes on one root | DST, embedded | H12; 4 processes 0 violations; after a claim the other processes are fenced, the claimer keeps writing |
| Named branch deleted and recreated while an entry captured on it is parked | focused repro | rule 1 holds: refused, request reran on the new incarnation |
| Suites with batching forced on: `writes`, engine `integration`, `failpoints`, `detached_commit_matrix`, DST, GQT | dev build | §4 |
| Soak: 1 hour, S3, 56 writers, optimize every minute | served | §6 |

## 3. Bugs found

Severity: **critical** = wrong data acknowledged; **high** = liveness or
availability loss under ordinary load; **medium** = wrong outcome type, a
lost guarantee or a bounded stall; **low** = cosmetic or conservative.

| # | Severity | Defect | Reproduction | Root cause | Fix (commit) | RFC section |
|---|---|---|---|---|---|---|
| H1 | **critical** | Admission across a hole in the effect log: duplicate keys, dangling edges, false `omnigraph.insert_absence` certificates | `gc_log_hole_admits_an_entry_across_a_foreign_insert_of_its_key` (2 rows with slug `k`), `gc_log_hole_admits_an_edge_across_a_delete_of_its_endpoint` (edge to a deleted node), `gc_second_actor_batch_is_a_hole_in_the_first_actors_log` | A batch that found the log stale admitted by its own capture but appended its effect after the stale records; `admit_inner` walked back to the entry's capture without checking that consecutive records link | `next_log` resets a non-contiguous log; the walk follows contiguous links only (`285922cf`) | Admission rule 2; The effect log |
| H3 | **high** | A leader whose future is dropped hangs its group forever (every later write of that branch and actor waits) | `gc_dropped_leader_hands_the_lead_on`, `gc_leader_dropped_inside_its_publication`; harness h06 (50% cancellation: writers never finish); failpoints run hung at `branch_claim_owner_reads_a_write_whose_future_was_dropped_after_its_commit` | `leading` stayed set; a `Lead` sent to a follower dropped after the send was lost | `LeadGuard` (answers drained members: sent back before the first detached commit, uncertain after; clears the log; hands the lead on) and `Waiter` (passes on a pending `Lead`) (`285922cf`) | Outcomes per member |
| H14 | **high** | A refusal storm that never recovers after any publication the batch did not see while it waited for the gates | local served p03: 1,185 refusals in 25 s, 691 → 67 writes/s; S3 soak 2: 122 → 2.35 writes/s after the first optimize | `lead_group` copied the log before waiting for the gate; publications made meanwhile were missing, so the batch "did not reach the head", refused everything, and its zero-admission outcome cleared the live log | judge the batch by the log read under the gates; a batch that admitted nothing keeps the log (`76112d60`) | The effect log; Progress |
| H13 | **high** | `optimize` and index-build publications unexplained: every queued entry refused after each maintenance run | local served k00: 505 → 67 writes/s after the first optimize | Maintenance publishes through `table_ops::commit_updates_on_branch_with_expected` directly; nothing logged it | log them as row-preserving (`12184f03`) | Admission rule 2; Interactions |
| H5 | **high** | Refused entries re-prepare outside the gates: no progress bound; with ≥2 actors or handles 5–7% of writes fail to the caller with `ReadSetChanged` (HTTP 409) | harness h02 (4 actors, 7.2%), h03 (2 handles, 5.2%), h07 (4 actors, hot keys, 5.1% of all, 40% of fresh inserts); served s02–s05 (11%) | `PreEffectReprepares::prepare_again` skipped the gates for grouped attempts | gated second attempt publishing alone as a batch of one (`publish_alone_under_gates`), logged with its keys; publications outside the group logged with their tables (`285922cf`) | Progress: the #1331 bound |
| H12 | **medium–high** | The prototype's publisher trust (listing-free first attempt) fences the writer-epoch holder: a stale trusted image fails the epoch assertion, which clears the live holder; the fresh retry is `WriterFenced` and not retried | DST `dst_writer_claims_randomized_oracles_hold_and_replay` (seed 6, op 47), fails with batching off too; passes with `OMNIGRAPH_PUBLISH_TRUST_ACKNOWLEDGED=0` | `assert_writer_epoch` on an unverified image runs the refusal side effects (`WriterEpochHolders::clear`, `held()` drops a mismatched native ref) | on a trusted image, compare epoch and native ref without mutating the registry; any mismatch goes to a fresh load (`44008d15`) | Interactions: writer epoch; Measured (the trim the columns include) |
| H11 | **medium** | Every batch failure re-typed as `manifest_internal("group commit failed: …")` for every member, the leader included: `WriterFenced`, conflicts and injected refusals lose kind, details and HTTP status (409 → 500) | DST `dst_v11_fault_injection_atomicity_and_replay`, `dst_fault_storm_on_one_live_handle_keeps_writing`; failpoints `branch_claim_*` ("not a writer-fenced refusal: None") | `follower_error` rebuilt all errors | clone `ManifestError` and `KeyConflict` with their evidence (`44008d15`) | Outcomes per member |
| H10 | **medium** | An edge entry's footprint omitted its endpoint node tables | `gc_log_hole_admits_an_edge_across_a_delete_of_its_endpoint` after H5's per-table logging | admission checked only written tables | add each written edge table's endpoint tables (`285922cf`) | Eligibility ("endpoint node tables join the footprint") |
| H2 | **medium** | Groups keyed per (handle, branch, actor): one actor's batches are foreign to another's; with H1, duplicates | `gc_second_actor_batch_is_a_hole_in_the_first_actors_log`; h02, h03 | `group_key` included the actor | one group and log per branch, one actor per batch (`285922cf`); per root (shared across handles) still open | The write group |
| C1 | **medium (perf cliff)** | Past 8,192 fragments outside the id index every keyed insert becomes a full-table merge join: 0 writes/s | f11-liftcap0 (80–90 s window: 0.0 writes/s); stacks all Lance `filtered_read` | `stage_keyed_write` runs the absence probe only while `tail.fragments × ids ≤ 8,192` (#1225) | lift the cap when known keys answer the whole tail (improvement 2, `6d4a893b`) | Known keys |
| H4 | **low–medium** | A write cancelled before admission was still published | `gc_follower_dropped_before_admission_leaves_the_queue` | the leader drained entries whose caller had gone | closed entries leave the queue at drain and after the gates (`285922cf`) | Outcomes per member |
| H7 | **low** | Followers of an in-doubt batch got an internal error, not `manifest_publish_in_doubt` naming the batch's commit | code reading | `follower_error` | typed in-doubt for every member (`285922cf`) | Outcomes per member |
| O1 | **medium (operational)** | `omnigraph-server` caps concurrent write responses at 64 (`DEFAULT_WRITE_OBSERVERS`, no override); an `optimize` submission is a write, so with ≥64 writers it is always refused 429 | served s03, soak 1 | hard-coded constant in `operations.rs` | none (ran the soak at 56 writers) | Soak gate in `blocked_on` |
| O2 | **low (operational)** | Recorded-operation routes are off by default (`OMNIGRAPH_OPERATION_ROUTES`); a served graph has no index-build route | served s04 | by design | none | Interactions: maintenance |
| P1 | **low (pre-existing)** | Validation refusals (`@unique`, missing endpoint) answer 400 with disposition `effects: unknown` | served v01 with group commit **off** | validation errors carry no completion evidence | none | — |
| U1 | **low (upstream)** | Lance panics a worker when a fragment read task is cancelled at runtime shutdown | failpoints `cross_handle_branch_gate_serializes_post_effect_publish` teardown | `SpawnedTask::spawn(..).map(\|r\| r.unwrap())`, `lance-13.0.0/src/io/exec/filtered_read.rs:667` | none | — |

Hypotheses that did not produce a defect: Blob tables in batches (H6; the
composed values read back exactly, before and after optimize), the unbounded
queue (H8; server admission bounds in-flight writes at 64 per server and 16
per actor by default, and the embedded queue is bounded by the writers), and
known keys keyed by relative path (H9; not exercised with copied roots).


## 4. Existing suites with batching forced on

`OMNIGRAPH_GROUP_COMMIT=1`, dev build, `RUST_MIN_STACK=16777216`;
`failpoints` run one test per process with a 180 s timeout
(`scripts/per-test.sh`), because the unfixed prototype hung the binary.

| suite | unfixed prototype | fixed prototype |
|---|---|---|
| `writes` | 67 pass, 1 ignored | 67 pass, 1 ignored |
| engine `integration` | 704 pass, 9 ignored | see §4.1 final rerun |
| `detached_commit_matrix` | 24 pass, 7 fail | 24 pass, 7 fail (same classes) |
| `failpoints` | hung (H3) after 22 tests: 16 pass, 6 fail | 167 pass, 2 ignored, 13 fail |
| DST (`crates/omnigraph-dst`) | — | lib 54/55, scenarios 54/57 after H11/H12 (first pass: 6 failures, 2 real bugs) |
| GQT corpus | — | 477 pass |

Every failure on the fixed build falls into one of three classes; none is a
wrong outcome.

1. **The batch path has none of the ordinary path's seams** (harness
   limitation, and an RFC rollout obligation): a test arms
   `mutation.post_finalize_pre_publisher`, `mutation.post_table_commit` or
   `fork.before_classify`, the eligible write never crosses it, and the test
   sees a write that succeeded or a window that never fired.
   `failpoints`: `cross_branch_writers_overlap_inside_schema_gate`,
   `cross_handle_branch_gate_serializes_post_effect_publish`,
   `finalize_publisher_residual_does_not_drift_untouched_tables`,
   `fork_collision_with_live_concurrent_fork_reprepares`,
   `interrupted_write_leaves_main_writable_without_recovery_issue_554`,
   `optimize_cancelled_while_queued_for_the_gates_returns_without_them`,
   `read_capture_proceeds_while_writer_parked`,
   `rfc_0067_zero_age_cleanup_keeps_a_fresh_unpublished_detached_manifest`,
   `collector_keeps_live_staging_and_reclaims_it_once_the_head_moves`,
   `collector_keeps_pins_published_by_a_late_branch_in_another_process`;
   `detached_commit_matrix`: the `Insert` and `MultiTable` Kill cells at
   `PrePublish` and `PostDetached(1|2)` ("child finished before reaching the
   window"); DST: `collector_oracle_rejects_dead_staging_leak_and_saved_base_loss`,
   `dst_lever1_crash_during_recovery`, the `mutation.post_table_commit` probe
   of `dst_v11_fault_injection_atomicity_and_replay`. RFC §Failure checkpoints
   lists the seams owed (after queueing, after admission, after the composed
   detached commits and before the CAS, at the lead handoff); this run shows
   the existing suites cannot cover the batch path until they exist. The
   research crash points stood in for them (§5.4).
2. **Old behavior by design: the #1331 carry.** `failpoints`:
   `a_write_on_a_held_branch_is_carried_over_any_handles_optimize_issue_1331`,
   `insert_into_a_table_optimize_compacted_meanwhile_keeps_the_compaction_issue_1331`,
   `insert_overlapping_an_optimize_publication_does_not_reprepare_issue_1331`;
   `detached_commit_matrix` `CarriedInsert` cells. The insert is refused and
   prepared again instead of being carried over the row-preserving
   publication. The outcome is correct; the RFC (rule 2, "explained as
   row-preserving") wants the carry, and the H13 fix adds it for entries
   queued in the group, not for these single-write cases.
3. **The DST storage-cost golden** (`dst_bench_cost_count_golden`) fails with
   batching off too: the prototype's publisher trust and known keys change
   request counts, and it asserts the carry. Regenerating it with reasons is
   rollout work.

Real bugs these suites found: H11 (DST v11 fault universe, DST fault storm,
three `branch_claim_*` failpoints tests) and H12 (DST writer-claims universe).


## 5. Throughput and latency

All closed-loop: decision evidence, not claims (RFC 0039 Rule 1).

### 5.1 Pure inserts

| setup | writers | writes/s | writes/commit | p50 / p99 | notes |
|---|---:|---:|---:|---|---|
| embedded, dev build, local (f08) | 32 | 175.9 | 13.0 | 134 / 482 ms | 20 s |
| served, release, local (k01, first 10 s) | 64 | 503 | ~20 | 116 / 352 ms | decays without maintenance (§5.3) |
| served, release, RustFS +30 ms (soak 3) | 56 | ~100–124 | ~28 | ~500 / 700–1,200 ms | optimize every minute, §6 |

### 5.2 Mixed workloads: the RFC's rules lose the gain (R1)

RustFS +30 ms, served, 48 writers over 4 actors, 70% fresh inserts, ~15%
updates, deletes and upserts of present keys on the same table, persons and
edges, optimize every 30 s, 60 s (`served/r03*`):

| variant | writes/s | writes/commit | p50 / p99 | caller errors |
|---|---:|---:|---|---|
| group commit off | 3.0 | 1.00 | 23.0 / 26.3 s | 0 |
| fixed prototype, RFC rules (gated retry, `changed` per table) | 3.2 | 1.06 | 19.6 / 32.3 s | 0 |
| unfixed prototype (ungated retries, per-actor log) | 5.75 | 2.86 | 2.4 / 39.5 s | 24 × 409 |
| fixed, 32 ungated retries (no progress bound) | 8.0 | 3.47 | 5.5 / 32.1 s | 0 |
| fixed, RFC rules + exact footprints (final build) | **9.0** | **4.07** | **5.8 / 11.9 s** | 0 |

Locally (embedded, 32 writers, one actor, `f10`, `f01`): RFC rules 24.4
writes/s, 1.05 writes/commit, p50 1,372 ms; with exact footprints 35.2,
1.96, 768 ms; ungated 34.5, 2.08, 624 ms.

At 1,024 embedded writers (dev build, 90% fresh, 10% same-table updates,
deletes and upserts, 30 s, `f13-*`): RFC rules 57.3 writes/s, **1.0
writes/commit (max 1)**, p50 42.4 s, p99 125.8 s; with exact footprints 89.3
writes/s, 15.6 writes/commit (the 512-entry batch cap reached), p50 13.7 s,
p99 50.5 s. 0 violations both.

Mechanism: every write outside the group is logged `changed` for its table;
that refuses every queued insert into the table; each refused insert then
holds the branch gate through its whole preparation and publication
(~10 serial requests on S3) and publishes alone; batches behind it shrink to
one.

### 5.3 Decay with the fragment count (one fragment per write)

Local served, 48 writers, pure inserts, no maintenance (p02, p05): 695 →
292 → 235 → 198 → 159 writes/s at 5 s intervals with readers; 649 → 24
writes/s in 40 s without readers, server CPU 16–20 cores, the leader's
commit+publish from ~7 ms to 150–200 ms, RSS 1.2 → 2.4 GB. Past 8,192
uncovered fragments the rate fell to 0 (C1). Cause: per-write passes over
the fragment list (uncovered-set computation three times, known-key lookups
under a global mutex per fragment, the tail's data-file sizes) and per-batch
manifest builds and writes, all O(fragments), with every write adding one.

## 6. Soak

Served `omnigraph-server` (`proto-fixed3`: every fix, no improvements), RustFS
behind toxiproxy at 32 ms (probed every minute: 31.2–40.2 ms), one branch,
56 closed-loop writers as one actor (the server's per-actor cap raised; 56
leaves room under the hard-coded 64 write observers so `optimize` is
admitted), pure fresh inserts into `Item`, two readers reading `count(Item)`
once a second at the exact commit they were served from, recorded `optimize`
every minute (compaction plus index reconciliation; the served path has no
separate index-build route), 60 minutes. Full series:
`results/soak3-series.md`; gates: `results/soak3-gates.json`.

| minute | writes/s | p50 ms | p99 ms | max ms | server RSS MB | server CPU % | writes/batch | queue | known-key files |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 123.97 | 436.7 | 860.1 | 1027.7 | 2191 | 0.0 | 23.4 | 23 | 6049 |
| 2 | 113.73 | 445.7 | 648.4 | 5998.1 | 2427 | 202.6 | 27.8 | 20 | 12694 |
| 5 | 104.78 | 473.1 | 906.6 | 5862.5 | 2521 | 173.8 | 27.9 | 10 | 31705 |
| 10 | 100.36 | 504.4 | 978.7 | 6123.3 | 2621 | 152.9 | 27.9 | 30 | 63048 |
| 15 | 103.55 | 482.6 | 904.6 | 6298.6 | 2675 | 182.8 | 27.9 | 1 | 93472 |
| 20 | 101.73 | 504.0 | 766.8 | 6063.8 | 2823 | 170.9 | 27.9 | 22 | 125205 |
| 25 | 103.53 | 479.2 | 926.8 | 6441.2 | 3042 | 226.9 | 28.0 | 33 | 156509 |
| 30 | 102.16 | 491.7 | 689.3 | 911.4 | 3454 | 203.1 | 28.0 | 30 | 187707 |
| 35 | 99.73 | 483.6 | 852.9 | 6768.1 | 4231 | 167.0 | 28.0 | 26 | 218202 |
| 40 | 101.15 | 507.4 | 636.4 | 895.9 | 4676 | 172.8 | 28.0 | 33 | 248134 |
| 45 | 96.45 | 504.8 | 816.8 | 6252.6 | 4898 | 170.5 | 28.0 | 21 | 276720 |
| 50 | 103.6 | 508.0 | 674.7 | 880.0 | 5079 | 184.0 | 28.0 | 35 | 305652 |
| 55 | 94.63 | 525.4 | 888.6 | 6157.6 | 5163 | 146.7 | 28.0 | 31 | 334152 |
| 60 | 102.15 | 533.9 | 674.1 | 681.6 | 5219 | 165.6 | 28.0 | 25 | 362650 |

| gate (RFC `blocked_on`, adapted) | result |
|---|---|
| every acknowledged write verified | **362,706 of 362,706**, final export and model replay; 0 violations |
| batch atomicity at every observed commit | 6,233 `count` observations, each equal to the model at that commit |
| every maintenance run published | **54 of 54** optimize runs (median 6.6 s, flat over the hour) |
| writes/s, last 5 min ≥ 80% of first 5 | 94.5 / 111.3 = **0.85** (pass; minute 1 at 124 is an empty table) |
| peak RSS at 30 min ≤ 1.5× the 10-min mark | 3,454 / 2,621 MB = **1.32** (pass) |
| refusals, failed batches | 0, 0 |

Reading the curves:

- **Throughput** settles at ~100 writes/s by minute 4 and drifts ~7% lower
  over the hour; every batch holds ~28 writes (max 55), ~3.6 publications a
  second. p50 ~500 ms; p99 0.6–1.3 s; max ~6 s in the minutes an optimize ran:
  writers wait out its ~6.6 s gate hold, ~10% of each minute.
- **Maintenance keeps up** at this rate (compacting ~6,000 small fragments a
  minute takes a flat ~6.6 s) but only because it is the served path
  (captures under the gates) at < 64 writers; the direct path never lands
  under batching (§5, 0 of 16) and at 64 writers the served path is refused
  (O1).
- **Memory grows all hour**: 2.19 GB at minute 1, 2.62 at 10, 3.45 at 30, 5.22
  at 60, about 50 MB a minute after minute 30, slowing to ~25 MB a minute at
  the end. The engine's data session uses Lance's default caches (6 GiB index,
  1 GiB metadata, `lance_access.rs`), every optimize rebuilds the id index
  under a new UUID, and every batch writes a new table manifest, so the
  growth is consistent with caches filling toward their caps. The hour does
  not show the plateau; a longer soak or capped caches would. The RFC's
  30-versus-10 gate passes; a 60-versus-10 gate (1.99×) would not.
- **Known keys** grew one entry per write (362,650 files) and were never
  evicted: the 4 M-id bound was never reached, and entries for files that
  compaction replaced stay. At ~0.3 KB an entry that is ~100 MB here.
- **Queue depth** (writers waiting when a batch starts) 1–35, bounded by the
  56 writers.
- **Discarded soaks**: soak 1 (64 writers) had every optimize refused by the
  observer cap and a toxiproxy stall; soak 2 (56 writers, build 5) collapsed
  after its first optimize (H14: 122 → 2.35 writes/s). Both are evidence for
  O1 and H14.


## 7. Improvements tried

Each on the same instrument and seed, before and after; switches are on the
branch so both sides run from one binary.

| # | Idea | Hypothesis | Result | Verdict |
|---|---|---|---|---|
| 1 | **Exact footprints** for publications outside the group (`OMNIGRAPH_GROUP_COMMIT_EXACT_TOUCHED`) | An update or delete changes exactly the ids its staging folded or deleted; an insert-only entry only read its own ids' absence and its endpoints' existence | local mixed: 24.4 → **35.2 writes/s (+44%)**, 1.05 → 1.96 writes/commit, p50 1,372 → **768 ms**; S3 mixed: 3.3 → **9.0 writes/s (2.75×)**, 1.06 → 4.07 writes/commit, p99 32.3 → 11.9 s | **keep**; RFC change |
| 2 | **Lift the 8,192 probe cap** when the known keys answer the whole uncovered tail (`OMNIGRAPH_KNOWN_KEYS_LIFT_CAP`) | The cap guards against per-uncovered-fragment IN lists, which `fast_search` never plans when the tail is answered from memory | embedded, 80–90 s window: **0.0 → 44.5 writes/s**; release served (with 3), 25–40 s: **27–36 → 126–136 writes/s**, +17% writes over 40 s; no cliff | **keep**; RFC change (Known keys) |
| 3 | **Memoize the known-tail analysis per manifest version** (`OMNIGRAPH_KNOWN_KEYS_TAIL_CACHE`) | Every writer probing the same pin rebuilds the same uncovered set three times and takes the global known-keys mutex per fragment | embedded (dev build), 48 writers, pure inserts, 60 s: **112.1 → 224.7 writes/s (2.0×)**, last 10 s 59 → 134; in release the per-write saving is smaller relative to the rest (combined with 2 above) | **keep**; RFC change (Known keys) |
| 4 | H14 fix (log read under the gates) | — | S3 soak minute 2: **2.35 → ~114 writes/s** | required |
| 5 | Ungated retries before the gated one (`OMNIGRAPH_GROUP_COMMIT_UNGATED_RETRIES`) | Most refused entries would batch on a second ungated try | local: 1 retry no change (1.00 writes/commit); 32: 2.08 writes/commit, no bound; S3: 32 retries 8.0 writes/s vs exact footprints 9.0 | **discard**; exact footprints batch as well or better and keep the bound |
| 6 | Linger, pipelining, rebase-per-write, probe-free writer capture | from #1401 | not re-run | — |

## 8. Where the cost lives, and Lance

**Per publication on S3** (from #1401's trace, unchanged here): five serial
puts, table `.txn` → table detached manifest → `__manifest` data file →
`.txn` → version put-if-absent (~165 ms at 32 ms), plus the leader's capture.
Batches amortize them; the soak's ~3.7 batches/s × ~28 writes is that
arithmetic.

**Per write** (preparation, ungated, parallel): capture probe (none with the
writer epoch held), validation's index metadata (warmed by the leader), the
absence probe (indexed fragments via the id BTREE plus the known tail from
memory), the data file put. Locally the O(fragments) passes of §5.3 dominate
once the tail grows.

**Upstream asks, with locations** (Lance 13.0.0; still true in 14.0.2):

1. Detached commits write the transaction file before the manifest
   (`io/commit.rs:1197` `do_commit_detached_transaction`, ~line 1233, "we
   don't strictly need a transaction file"). For a detached commit nothing
   names the manifest until the caller publishes it, so the two puts can be
   concurrent, or the file skipped with a public
   `ManifestWriteConfig::with_transaction_file_disabled` (`pub(crate)` in
   14.0.2, `dataset.rs:4347`); the engine would read the inline transaction
   (moved to a new manifest field in 14.0.0, #9482). One serial round trip per
   table per batch.
2. Let the caller choose the detached version (today random inside the retry
   loop, `commit.rs` `rng().random::<u64>() | DETACHED_VERSION_MASK`), so the
   `__manifest` rows can be built while the table commit is in flight.
3. Populate the session caches after a detached commit as
   `record_successful_commit` (`commit.rs:1532`) does after a linear one; the
   prototype's index warm-up works around its absence.
4. Build the Blob v2 projection schema once per scan, not once per fragment
   read (`io/exec/filtered_read.rs:1484` → `public_blob_v2_binary_projection_schema`
   → `Schema::to_schema`; 14.0.2 line 1535): with one fragment per write it
   dominates scans.
5. Do not unwrap a cancelled fragment-read task (`filtered_read.rs:667`).
6. `FLAG_FRAGMENT_TREE` (present, marked unsupported in 14.0.2) is the format
   answer to manifests that grow with one fragment per write.

**Lance 14.0.2 relevance.** No composite operation exists (the
`Transaction.operation` oneof ends at `DataOverlay = 115`), so the RFC's
`CompositeOperation`/`AssertUniqueKeys` path is not available at 14.0.2.
Relevant improvements: #9915 (`read_version_transaction` from the held or
cached manifest: cheaper chain walks for the carry and the collector),
#9942 and #9653 (O(fragments) trims in index and scan planning, which bear on
§5.3), #9631 (commit retry budget after catch-up), #9482 (inline transaction
field).

## 9. RFC claims against the evidence

| Claim | Verdict | Evidence |
|---|---|---|
| Admission makes a batch equivalent to its members in order on `Hc`; the history is strictly serializable | **supported where implemented as written**; the prototype's deviations broke it | 0 violations after fixes in every run; H1 (log holes) and H10 (footprint) are exactly the RFC's rules 2 and 3 not implemented |
| No new durable state; the collector's rule does not change | **supported** | 8 aborts between detached commits and the CAS; `cleanup` reclaimed exactly 8 unpublished manifests, no row changed |
| Known keys change cost, never a result | **supported**; incomplete | no violation with known keys on; the 8,192 cap still sends inserts to the full merge join (C1) |
| A refused write finishes within two attempts in its process | **supported** after H5's fix; **but** the RFC's batching does not survive it under mixed load | R1 |
| Logging every publication stops the cascade after a sent-back write | **contradicted as specified** | H14: the cascade persists unless the leader reads the log under the gates and a zero-admission batch keeps it |
| `optimize` and index builds since the capture are explained | **not implemented in the prototype**; fixed (H13) | k00 |
| Maintenance under batches starves; the carry is needed | **confirmed** | 0 of 16 direct-path maintenance runs landed at ~120 writes/s |
| The soak gate (64 writers, optimize every 5 min, index build every minute) | **not runnable on the served path** | O1 (64 write observers), O2 (no served index build) |
| Blob tables must wait for the guard | **no defect found** | 96 Blob writes in 9 commits read back exactly, before and after optimize |
| Headline throughput (20–65× on one branch) | **pure inserts only** | soak ~100–124 writes/s at 56 writers on S3 vs ~3 writes/s baseline; mixed: 1.06× with the RFC rules |
| Lance 14's `CompositeOperation` with `AssertUniqueKeys` | **incorrect for 14.0.2** | §8 |
| `with_transaction_file_disabled` is `#[cfg(test)]` | `pub(crate)` in 14.0.2; still not public | §8 |

## 10. Recommended RFC changes

1. **The effect log** (§The effect log, rule 2): state that the walk follows
   contiguous links only (each record's `parent` is the previous record's
   `head`) and that a record that does not continue the log resets it.
   Matrix cell: the parked-entry repro of H1, with a foreign insert of the
   same key and with another actor's batch.
2. **Read the log under the gates** (§The write group step 2): the leader
   judges admission by the log as it is after taking the gates, and a batch
   that admits nothing never clears it (H14). Without this the gated second
   attempt turns any unexplained publication into a permanent storm.
3. **Exact footprints for publications outside the group** (§The effect log
   classes): add `touched(upserted ids, deleted ids)` per table for mutations,
   whose staging knows them; refuse an entry only when an upserted id is one
   it inserts or a delete hit a table it reads without writing. Keep
   `changed` for loads, merges and overwrites. Without it the RFC's batching
   is worth ~1.06× on a realistic mixed workload (R1).
4. **Maintenance publications in the log** as row-preserving records (H13),
   and make the maintenance carry (rollout step 4) a prerequisite of step 6.
5. **Known keys** (§Known keys): lift the `KEYED_WRITE_MAX_ROWS` tail cap when
   the known keys answer the whole uncovered tail (C1); compute the tail
   analysis once per manifest version (improvement 3); bound the map by bytes
   and drop entries for files no retained version lists (soak: ~6.5k files a
   minute, never evicted).
6. **Fragments** (§Bounds): name the per-write O(fragments) costs, require a
   maintenance cadence that keeps the unindexed tail bounded, and evaluate
   one fragment per table per batch (leader-side coalescing) before default
   on; the gate in `blocked_on` should include a no-maintenance decay check.
7. **Cancellation and outcomes** (§Outcomes per member): drop guards for the
   leader and for a follower holding a pending lead; closed entries leave the
   queue at drain and after the gates; every member gets the batch's typed
   error (H3, H4, H7, H11). Add matrix cells for each.
8. **Server admission**: the soak needs a maintenance lane outside the 64
   write observers, or the gate should say "fewer than 64 writers" (O1); a
   served index-build route or an explicit statement that optimize covers it
   (O2).
9. **Evidence section**: add the mixed-workload and no-maintenance numbers
   beside the pure-insert ones; correct the Lance 14 `CompositeOperation`
   sentence and the `with_transaction_file_disabled` visibility.
10. **Publisher trust** (the #837 trim in the measured configuration): an
    epoch assertion on an unverified image must not clear the holder (H12).
11. **Per root, not per handle** (§The write group): unchanged by these
    fixes; two handles of one process still see each other's batches as
    foreign (h03).


## Appendix: reproducing

```bash
# Branch
git fetch fork && git switch research/group-commit-stress

# Focused repros (7) and the embedded harness
cargo dev-test --test group_commit_stress --no-run
cd crates/omnigraph && RUST_MIN_STACK=16777216 \
  ../../target/debug/deps/group_commit_stress-* --test-threads=1 --nocapture
# randomized: label, then OG_GS_* knobs (module doc of harness.rs)
echo "$PWD/../../target/debug/deps/group_commit_stress-<hash>" > /tmp/gs_bin
research/group-commit-stress/scripts/gs-run.sh h02-actors4 \
  OG_GS_WRITERS=32 OG_GS_SECS=20 OG_GS_SEED=2 OG_GS_ACTORS=4

# RustFS + toxiproxy (+15 ms each way); proxy health
research/group-commit-stress/scripts/s3-up.sh
research/group-commit-stress/scripts/proxy-rtt.sh

# Real binaries (release profile, line tables kept)
CARGO_TARGET_DIR=/srv/target-rel CARGO_PROFILE_RELEASE_STRIP=none \
  CARGO_PROFILE_RELEASE_DEBUG=line-tables-only \
  cargo build --release --locked -p omnigraph-cli -p omnigraph-server

# Served runs (bootstrap, seed, index build, drive, export, check)
python research/group-commit-stress/scripts/served_run.py r03a-gated --backend s3 \
  --bins /srv/bins/proto-fixed2 --optimize-secs 30 -- --writers 48 --secs 60 \
  --actors 4 --seed 11 --mix "fresh=70,hot=10,pair=5,update=4,delete=3,person=3,knows=5"
#   faults:       --toxics reset,truncate,timeout,jitter --toxic-every 5 --toxic-window 2
#   kill -9:      --chaos-kill-secs 12
#   crash points: --watchdog 1 --server-env OMNIGRAPH_STRESS_CRASH=batch_committed:4
#   soak:         --optimize-secs 60 --server-env OMNIGRAPH_GROUP_COMMIT_STATS_SECS=60 \
#                 -- --writers 56 --secs 3600 --mix fresh=100 --reader-count 1

# Several processes on one root; a claim mid-load
python research/group-commit-stress/scripts/mp_run.py mp02-claim --procs 4 --writers 6 \
  --secs 30 --claim-at 10

# Suites with batching forced on
research/group-commit-stress/scripts/suites-on.sh <label> 1
research/group-commit-stress/scripts/per-test.sh <failpoints-binary> <label> 180 OMNIGRAPH_GROUP_COMMIT=1
(cd crates/omnigraph-dst && OMNIGRAPH_GROUP_COMMIT=1 cargo test --locked --no-fail-fast)
(cd crates/omnigraph-gqt && OMNIGRAPH_GROUP_COMMIT=1 cargo test --locked)
```

Switches on this branch (research only): `OMNIGRAPH_GROUP_COMMIT=1`,
`OMNIGRAPH_GROUP_COMMIT_EXACT_TOUCHED=1`, `OMNIGRAPH_GROUP_COMMIT_UNGATED_RETRIES=N`,
`OMNIGRAPH_KNOWN_KEYS_LIFT_CAP` (default on), `OMNIGRAPH_KNOWN_KEYS_TAIL_CACHE`
(default on), `OMNIGRAPH_GROUP_COMMIT_STATS_SECS=N`, `OMNIGRAPH_STRESS_CRASH=point:per_mille,…`,
plus the prototype's own (`OMNIGRAPH_KNOWN_KEYS`, `OMNIGRAPH_PUBLISH_TRUST_ACKNOWLEDGED`,
`OMNIGRAPH_GROUP_COMMIT_DEBUG`, `OMNIGRAPH_GROUP_COMMIT_TIMING`, …). Server
admission knobs used: `OMNIGRAPH_PER_ACTOR_INFLIGHT_MAX`,
`OMNIGRAPH_GLOBAL_INFLIGHT_MAX`, `OMNIGRAPH_INGRESS_INFLIGHT_MAX`,
`OMNIGRAPH_OPERATION_ROUTES=true`.

