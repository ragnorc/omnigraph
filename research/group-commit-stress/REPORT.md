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

**Breaking it.** Fourteen defects in the prototype, five of them able to
lose correctness or liveness on real binaries under load: admission across a
hole in the effect log (duplicate keys, dangling edges); a dropped leader
that hangs its group forever; a refusal storm that never recovers after any
unexplained publication (S3 soak: 122 → 2.35 writes/s after the first
`optimize`); maintenance publications left unexplained; and refused entries
re-preparing outside the gates, so that with two actors or two handles
6–40% of writes fail to the caller after 32 attempts. One more lives in the
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
   commit off (p50 19.6 s). Logging the exact ids such writes touched
   (improvement 1) restores batching without giving up the two-attempt
   bound.
2. **Maintenance and fragment growth are not side issues.** One fragment per
   write makes every per-write pass O(fragments); without maintenance the
   rate decays 27× in 40 s locally, and past 8,192 unindexed fragments every
   keyed insert becomes a full-table merge join (0 writes/s). Direct-path
   `optimize` and index builds never land under batching (0 of 16), the
   served `optimize` is refused outright with ≥64 writers (a hard-coded
   observer cap), and the RFC's soak cannot run on the served path as
   specified. With served `optimize` every minute the S3 soak held ~100
   writes/s for an hour (§6).

**Improving it.** Best three, each measured on the same instrument:
exact footprints (+44% writes/s, p50 −44% on a mixed workload with the gated
retry kept), lifting the probe cap when known keys cover the tail (removes
the 0 writes/s cliff), and the H14 fix itself (the S3 soak's minute-2 rate
from 2.35 to ~114 writes/s, sustained). Lance upstream asks with exact code
locations are in §8.


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
  (the merge), then one build per fix wave (`proto-fixed` … `proto-fixed4`).
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
| Writer epoch claim mid-load; several processes on one root | DST, embedded | H12; 4 processes 0 violations |
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
| H5 | **high** | Refused entries re-prepare outside the gates: no progress bound; with ≥2 actors or handles 5–40% of writes fail to the caller with `ReadSetChanged` (HTTP 409) | harness h02 (4 actors, 6%), h03 (2 handles, 5%), h07 (4 actors, hot keys, 40% of fresh inserts); served s02–s05 (11%) | `PreEffectReprepares::prepare_again` skipped the gates for grouped attempts | gated second attempt publishing alone as a batch of one (`publish_alone_under_gates`), logged with its keys; publications outside the group logged with their tables (`285922cf`) | Progress: the #1331 bound |
| H12 | **medium–high** | The prototype's publisher trust (listing-free first attempt) fences the writer-epoch holder: a stale trusted image fails the epoch assertion, which clears the live holder; the fresh retry is `WriterFenced` and not retried | DST `dst_writer_claims_randomized_oracles_hold_and_replay` (seed 6, op 47), fails with batching off too; passes with `OMNIGRAPH_PUBLISH_TRUST_ACKNOWLEDGED=0` | `assert_writer_epoch` on an unverified image runs the refusal side effects (`WriterEpochHolders::clear`, `held()` drops a mismatched native ref) | on a trusted image, compare epoch and native ref without mutating the registry; any mismatch goes to a fresh load (`44008d15`) | Interactions: writer epoch; Measured (the trim the columns include) |
| H11 | **medium** | Every batch failure re-typed as `manifest_internal("group commit failed: …")` for every member, the leader included: `WriterFenced`, conflicts and injected refusals lose kind, details and HTTP status (409 → 500) | DST `dst_v11_fault_injection_atomicity_and_replay`, `dst_fault_storm_on_one_live_handle_keeps_writing`; failpoints `branch_claim_*` ("not a writer-fenced refusal: None") | `follower_error` rebuilt all errors | clone `ManifestError` and `KeyConflict` with their evidence (`44008d15`) | Outcomes per member |
| H10 | **medium** | An edge entry's footprint omitted its endpoint node tables | `gc_log_hole_admits_an_edge_across_a_delete_of_its_endpoint` after H5's per-table logging | admission checked only written tables | add each written edge table's endpoint tables (`285922cf`) | Eligibility ("endpoint node tables join the footprint") |
| H2 | **medium** | Groups keyed per (handle, branch, actor): one actor's batches are foreign to another's; with H1, duplicates | `gc_second_actor_batch_is_a_hole_in_the_first_actors_log`; h02, h03 | `group_key` included the actor | one group and log per branch, one actor per batch (`285922cf`); per root (shared across handles) still open | The write group |
| C1 | **medium (perf cliff)** | Past 8,192 fragments outside the id index every keyed insert becomes a full-table merge join: 0 writes/s | f11-liftcap0 (80–90 s window: 0.0 writes/s); stacks all Lance `filtered_read` | `stage_keyed_write` runs the absence probe only while `tail.fragments × ids ≤ 8,192` (#1225) | lift the cap when known keys answer the whole tail (improvement 2) | Known keys |
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

<!-- SECTIONS2 -->
