# Group-commit stress log

Running log: hypotheses, commands, raw numbers, failures, verdicts. Newest
entries at the bottom. Raw results, histories and repro scripts live beside
this file (`results/`, `histories/`, `scripts/`, `logs/`).

## 2026-11-15 — setup

- Branch `research/group-commit-stress` (fork `ragnorc/omnigraph`) from
  `fork/research/write-throughput` at `ad39204a` (the prototype, closed
  draft #1401).
- Merged `fork/fix/per-branch-writer-state` (`e2836ce3`, PR #1407) as
  `5077eaa8`. Conflicts, all in `crates/omnigraph/src/db/omnigraph.rs`:
  - the `Omnigraph` struct field and its three constructors: the prototype
    kept the one-entry `merge_authority_cache` and added `group_commits`;
    #1407 replaced the slot with `branch_authorities`
    (`db/branch_authority.rs`). Resolved by taking `branch_authorities` and
    keeping `group_commits`.
  - the prototype's `install_branch_authority` (group commit installs the
    coordinator it published through as the handle's cached authority for a
    named branch) wrote the removed slot. Ported to
    `branch_authorities.install(GraphCoordinator::clone(coordinator), None)`,
    which keeps a newer entry of the same lifetime instead of overwriting it.
    The group's own `coordinator` slot is unchanged (the RFC leaves the
    per-branch cache's design to #1407).
  - `table_ops.rs` auto-merged: #1407's `commit_updates_on_branch_with_expected`
    (start from a copy of the cached entry only when it is the captured view,
    discard it on failure) and the prototype's new
    `commit_updates_with_coordinator` do not overlap.
- Merged `origin/main` (`a70e7b6a`) as `0cc5d359`: no conflicts.
- `cargo dev-build-tests` on the merged tree: clean (3 min 22 s, warm).
- Box: Modal sandbox, gVisor, 32 visible CPUs (`nproc` 2), 64 GB RAM, no
  swap. No `perf` (gVisor has no `perf_event_open`), no docker.
- RustFS `1.0.0-beta.12` (`rustfs-linux-x86_64-gnu-v1.0.0-beta.12.zip`,
  SHA256 `9b9a17fb…7fb8`, matches the release `SHA256SUMS`) and toxiproxy
  `v2.13.0` (`toxiproxy-server-linux-amd64` `bf529e8b…de89`, matches
  `checksums.txt`), the versions #1401 used and CI pins. `scripts/s3-up.sh`
  starts both: RustFS on `127.0.0.1:9000`, proxied on `127.0.0.1:19000` with
  +15 ms each way. Buckets `omnigraph-bench`, `omnigraph-stress`.
- Python 3.12 venv at `/srv/venv` with `aiohttp 3.14.5`, `boto3 1.44.3`,
  `psutil 7.2.2` for the HTTP driver and the checker.
- Release binaries: `CARGO_TARGET_DIR=/srv/target-rel
  CARGO_PROFILE_RELEASE_STRIP=none CARGO_PROFILE_RELEASE_DEBUG=line-tables-only
  cargo build --release --locked -p omnigraph-cli -p omnigraph-server`
  (the release profile: opt-level 2, thin LTO; line tables kept for
  symbolized backtraces). Group commit is the prototype's process switch
  `OMNIGRAPH_GROUP_COMMIT=1`, read per call, so the same binary runs both
  paths.

## Reading done

AGENTS.md, `docs/dev/invariants.md`, `docs/dev/lance.md`, `docs/dev/testing.md`,
`docs/dev/writes.md`, `docs/dev/recovery.md`, the RFC
(`docs/rfcs/2026-11-14-group-commit-with-known-key-admission.md` on
`fork/rfc/group-commit-known-keys`, all 974 lines), the prototype's
`REPORT.md` and `LOG.md`, and the prototype code: `db/group_commit.rs`
(1,448 lines), the `exec/mutation.rs` and `exec/staging.rs` entry points,
`table_store::known_keys`, `compose_insert_only`, the reprepare change.

## Code reading: what the prototype does that the RFC says it must not

Test plan, from the RFC's admission rules, eligibility and outcome table,
checked against the prototype before running anything. Each line is a
hypothesis a stress run or a focused repro must confirm or refute.

- **H1. The effect log can have holes, and admission walks over them.**
  `run_batch` treats the log as empty for its own admission when the log's
  last head is not the captured head (a commit the group did not make), but
  still *appends* its own effect (`LogChange::Push`) to the old log instead of
  replacing it. `admit_inner` walks the log backwards until an effect whose
  `parent` is the entry's capture, checking key overlap per effect, without
  checking that consecutive effects are contiguous (`log[i].head ==
  log[i+1].parent`). An entry captured before a foreign commit F and
  submitted after the next group batch is then admitted across F. RFC rule 2
  (explained interval) is violated. Consequences: a duplicate key when F
  inserted the entry's key (an ineligible insert, another actor's group,
  another handle or process), a dangling edge when F deleted an endpoint, and
  a composed commit that carries `omnigraph.insert_absence` falsely (merge's
  pure-insert proof and the change feed trust it).
- **H2. Groups are keyed per (handle, branch, actor)**, not per root and
  branch (RFC §The write group). Two actors, or two handles of one process,
  on one branch each see the other's batches as foreign: refusals, and with
  H1, duplicates.
- **H3. A dropped leader future strands its group** (the prototype's own
  doc says so). `leading` stays `true`; entries queued after the drain never
  get a `Lead`, and every later submission queues behind a leader that is
  gone: a permanent hang of that (branch, actor) group. Also a race in
  `hand_lead`: a `Lead` sent to a receiver that is dropped right after the
  send is lost.
- **H4. A follower whose caller dropped its future is still admitted and
  published** (RFC: it leaves the queue before admission).
- **H5. Refused entries re-prepare outside the gates** (RFC §Progress keeps
  the gated second attempt). With two actors or a steady foreign writer an
  entry can be refused repeatedly, up to `MAX_PRE_EFFECT_REPREPARES`, then the
  caller gets `ReadSetChanged`.
- **H6. Blob tables are eligible** (RFC eligibility rule 4 excludes them
  until the guard pins a Blob v2 column through composition).
- **H7. Followers get an in-doubt outcome as `manifest_internal("group
  commit failed: …")`** with the leader's evidence, not the typed
  `manifest_publish_in_doubt` naming the batch's commit (RFC outcome table).
  A server's once-per-in-doubt admission close may not trigger.
- **H8. The queue is unbounded** (RFC §Bounds).
- **H9. Known keys are keyed by the data file's relative path only**, not by
  resolved location (RFC §Known keys). Random UUID names make a collision
  between different files improbable; to check: a copied or restored root,
  shallow clones, legacy forks sharing files.

## 2026-11-15 — focused reproductions (`crates/omnigraph/tests/group_commit_stress.rs`)

New test binary (feature `failpoints`, `#[serial]`, every test sets
`OMNIGRAPH_GROUP_COMMIT=1`). Seams park a write at a chosen point:
`mutation.post_stage_pre_effect_gate` (after staging, before the group),
`mutation.post_table_commit` (an ordinary write holding the branch gate),
`publish.pre_merge` (a leader inside its publication).

```bash
cargo dev-test --test group_commit_stress --no-run
cd crates/omnigraph && RUST_MIN_STACK=16777216 \
  ../../target/debug/deps/group_commit_stress-* --test-threads=1 --nocapture
```

Results on the unfixed prototype (`results/repro-1.log`):

| test | RFC rule | result |
|---|---|---|
| `gc_log_hole_admits_an_entry_across_a_foreign_insert_of_its_key` | §Admission rule 2 | **FAIL**: 2 rows with slug `k` (A v4, F v5 ordinary insert of `k`, B v6, e admitted at v7) |
| `gc_second_actor_batch_is_a_hole_in_the_first_actors_log` | §The write group | **FAIL**: 2 rows with slug `k`; actor y's batch is foreign to actor x's log |
| `gc_log_hole_admits_an_edge_across_a_delete_of_its_endpoint` | §Admission rule 3 | **FAIL**: edge `a->b` published after `b` was deleted (dangling) |
| `gc_dropped_leader_hands_the_lead_on` | outcome table, dropped leader | **FAIL**: follower and a later write hang (15 s timeouts) |
| `gc_leader_dropped_inside_its_publication` | same, after the first effect | **FAIL**: follower and later write hang; neither `l` nor `f` landed |
| `gc_follower_dropped_before_admission_leaves_the_queue` | outcome table, cancelled before admission | **FAIL**: the cancelled write `f2` was published (first version of the test aborted f2 while a foreign commit refused it anyway; reworked to park the leader in `publish.pre_merge`) |
| `gc_blob_inserts_compose_and_read_back` | eligibility rule 4 | pass: 96 Blob writes (5 B to 300 KB, inline and dedicated Blob v2) in 9 commits, every byte equal before and after `optimize` |

H1, H2 (as a hole), H3 and H4 are confirmed. H6 (Blob tables eligible) is a
rule deviation without an observed defect: the composed Blob v2 values read
back exactly.

## 2026-11-15 — randomized harness (`group_commit_stress/harness.rs`)

`gc_stress_harness` (ignored, `instrument:`), `OG_GS_*` knobs (module doc).
Writers run a seeded op mix (fresh inserts, hot-key upserts, two-row
inserts, updates, deletes, Person upserts and deletes with edge cascade,
`Knows` edge inserts, two-table inserts, `@unique` Tag inserts), readers
record pinned snapshots every 150 ms, read-your-writes probes after 10% of
receipts. The checker: no duplicate keys, no dangling edges, `@unique`
held, receipts match `list_commits` (version, id, actor), lineage is one
chain, model replay in version order equals the final state and every reader
snapshot (keys an in-doubt op touched excluded), no two writes of one
commit share a key. `scripts/gs-run.sh <label> OG_GS_...=...`; histories in
`histories/<label>.jsonl`, summaries in `results/gs-runs.log`. Dev build
(workspace crates at opt-level 0), local disk.

| run | config | ok/s | commits | writes/commit (max) | caller-visible errors | violations |
|---|---|---:|---:|---|---|---|
| h01 | 32 writers, 1 actor, default mix, seed 1 | 32.5 | 281 | 2.08 (31) | 0 | 0 |
| h02 | + 4 actors, seed 2 | 26.8 | 288 | 1.63 (7) | **37 `ReadSetChanged`** (`group_log_does_not_reach_capture`) | 0 |
| h03 | + 2 handles, seed 2 | 28.9 | 269 | 1.89 (15) | **29 `ReadSetChanged`** | 0 |
| h04 | 10% cancelled, ≤40 ms budgets | 34.8 | 305 | 2.03 (31) | 120 cancelled | 0 |
| h05 | 15% cancelled, ≤1.5 s budgets | 33.5 | 275 | 2.19 (31) | 73 cancelled | 0 |
| h06 | 50% cancelled, ≤0.8 s budgets, seed 4 | 6.6 | 51 | 1.51 (27) | 49 cancelled | **liveness: writers hung** |

- **H5 confirmed under load.** With several actors or handles, inserts are
  refused on every attempt until `MAX_PRE_EFFECT_REPREPARES` runs out and the
  caller gets `ReadSetChanged`: 6% of writes at 4 actors, 5% at 2 handles. The
  #1349 promise (a write finishes in two attempts within its process) is
  gone: each group's batches are foreign to the others, and refused entries
  re-prepare outside the gates, so they race the next foreign batch again.
- **H3 reproduced by random cancellation** (h06): a leader cancelled inside
  its batch leaves `leading` set; every later write of that (branch, actor)
  group waits forever; ordinary-path writes (updates, deletes, upserts of
  existing keys) keep going, which is why throughput fell to 6.6/s instead
  of 0. Budgets of ≤40 ms (h04) only ever cancel during preparation.
- Validation refusals (`@unique`, missing endpoint) carry no completion
  evidence and are `Manifest(...)` errors; the harness counts them in doubt
  (conservative) until classified.

## 2026-11-15 — served runs on real binaries (unfixed prototype, local disk)

Release binaries (`/srv/bins/proto-unfixed`, built from `0cc5d359`), a
cluster bundle with the harness schema and 16 actors
(`cluster/`), `scripts/served_run.py` (bootstrap a fresh root with
`cluster apply`, force-unlock, start `omnigraph-server`, seed, index build
through a recorded `optimize` operation, drive, export, check) and
`scripts/http_stress.py` (async closed-loop writers over HTTP, readers that
record the exact `graph_commit_id` each read was served from, per-interval
metrics with server RSS and CPU, and the same model checker in Python).

| run | config | ok/s | writes/commit | caller errors | violations | notes |
|---|---|---:|---:|---|---|---|
| s01 | 64 writers, 1 actor | 39 avg, decaying 58→24 | 1.69 | 107k 429s (driver spun) | 0 | per-actor in-flight cap 16 (`OMNIGRAPH_PER_ACTOR_INFLIGHT_MAX`) |
| s02 | 64 writers, 4 actors | 25.5, decaying 42→12 | 1.70 | 142 × 409 `group_log_does_not_reach_capture` | 0 | RSS 795→1,132 MB in 40 s |
| s03 | + optimize every 10 s | 23.5, decaying | 1.74 | 178 × 409 | 0 | every optimize refused 429 "server write observer cap 64 exceeded"; server CPU 12→21 cores |
| s04 | 48 writers, optimize every 10 s | 22.7, decaying | 1.60 | 191 × 409 | 0 | optimize refused 503: recorded-operation routes are off by default |
| s05 | + `OMNIGRAPH_OPERATION_ROUTES=true` | **41.9 flat** | 1.64 | 340 × 409 | 0 | every optimize landed (0.5–1 s); CPU ~11 cores; RSS +2.2 MB/s |

Findings:

- **The seed never built the id index in s01–s04** (the optimize route was
  off), so every insert took Lance's full-table merge join instead of the
  absence-proven writer: no exact ids, Bloom-filter admission, no known
  keys, and per-write CPU growing with the table. With the index and
  optimize every 10 s (s05) throughput stays flat for 60 s. The decay was
  maintenance starvation, not batching.
- **Maintenance starves at server admission** under write saturation: the
  global in-flight cap (64, shared with writes) refused every optimize with
  429 while 64 writers were active (s03). With group commit raising the
  write rate this matters more; RFC §Interactions talks about maintenance
  under batches at the gate, but the server admission lane comes first.
- **H5 is visible to HTTP clients**: 409 `conflict` on 11% of writes at 4
  actors.
- Validation refusals (`@unique`, missing endpoint) come back as 400
  `bad_request` with disposition `effects: unknown` (no completion
  evidence on the engine error). Checked below whether group commit is the
  cause.

## 2026-11-15 — suites with batching forced on

`OMNIGRAPH_GROUP_COMMIT=1`, dev build, `RUST_MIN_STACK=16777216`.

| suite | unfixed prototype | fixed prototype | failure classes |
|---|---|---|---|
| `writes` | 67 pass, 1 ignored | — | — |
| engine `integration` | 704 pass, 9 ignored | — | — |
| `detached_commit_matrix` | 24 pass, **7 fail** | — | (a) Kill cells for `Insert`/`MultiTable` at `PrePublish` and `PostDetached(n)`: "child finished before reaching the window"; the group path crosses neither `mutation.post_finalize_pre_publisher` nor `mutation.post_table_commit`, so no crash window of the batch path is covered (RFC §Failure checkpoints: new seams owed). (b) `CarriedInsert` #1331 cells: "the insert must be carried over the optimize publication" — the prototype refuses and re-prepares instead of explaining a row-preserving publication (RFC rule 2 requires it be explained). Harness limitation and RFC deviation, not wrong outcomes |
| GQT corpus | — | **477 pass** | batches of one through the group path |
| DST (`crates/omnigraph-dst`) | — | see below | |
| `failpoints` | stopped: hung at `branch_claim_owner_reads_a_write_whose_future_was_dropped_after_its_commit` (H3) after 28 min; 16 pass, 6 fail before it | per-test run in progress | |

DST with batching on, first pass on the fixed build: 6 failures.

| test | class | cause |
|---|---|---|
| `harness::collector_oracle_tests::collector_oracle_rejects_dead_staging_leak_and_saved_base_loss` | harness limitation | arms `mutation.post_table_commit` to strand staging; the group path does not cross it |
| `dst_lever1_crash_during_recovery` | harness limitation | crash at `mutation.post_table_commit` never fires |
| `dst_v11_fault_injection_atomicity_and_replay` | **real bug H11**, then harness limitation | "counted effect fired but an unrelated error escaped: group commit failed: injected failpoint triggered: graph_publish.before_commit_append": the group path re-typed the leader's own error as `manifest_internal`. After the fix, only its `mutation.post_table_commit` probe fails (not crossed) |
| `dst_fault_storm_on_one_live_handle_keeps_writing` | **real bug H11** | same; passes after the fix |
| `dst_bench_cost_count_golden` | old behavior by design | expects the #1331 carry and `main`'s request counts; also fails with batching off (publisher trust, known keys change costs) |
| `dst_writer_claims_randomized_oracles_hold_and_replay` | **real bug H12**, not batching | fails with batching off too; passes with `OMNIGRAPH_PUBLISH_TRUST_ACKNOWLEDGED=0` |

**H12, root cause.** The prototype's publisher trust (the publisher's first
attempt uses its own last acknowledged `__manifest` image with no listing,
the #837 trim the RFC's measured columns include) loaded an image from
before a claim of the branch. `assert_writer_epoch` failed on it (held 1,
read 0) and, as every refusal does, cleared the process's holder entry
(`WriterEpochHolders::clear`; `held()` also drops an entry whose native ref
differs from the loaded chain). The trusted-retry rule then reloaded fresh,
found the branch at epoch 1 with nothing held, and returned `WriterFenced
{ held: None, current: 1 }`, which is not retried. Fix: on a trusted image,
compare the image's epoch and native ref with the held entry without
mutating the registry (`WriterEpochHolders::peek`, `native_matches`) and go
straight to a fresh load on any mismatch. DST writer-claims passes with
batching off and on.

**H11.** `follower_error` rebuilt every non-`ReadSetChanged` batch failure as
`manifest_internal("group commit failed: …")` for every admitted member,
the leader's own entry included: the kind, the details (`WriterFenced`,
`PreconditionFailed`, conflicts) and the HTTP status were lost. Fix: clone
`ManifestError` (it is `Clone`) and `KeyConflict` with their evidence.

## 2026-11-15 — fixes in the prototype (`285922cf`, `44008d15`)

| bug | fix |
|---|---|
| H1 log holes | `next_log` resets the log when a record does not continue it; `admit_inner` walks contiguous links only (`group_log_hole`) |
| H2 per-actor groups | one group and one log per branch; a batch drains the first open entry's actor, the rest keep their places |
| H3 dropped leader | `LeadGuard` answers every drained member (sent back before the batch's first detached commit, uncertain after), clears the log, hands the lead on; `Waiter` passes on a `Lead` left in a dropped follower's channel |
| H4 cancelled follower published | entries whose receiver closed leave the queue at drain and again after the batch's gates |
| H5 progress bound | a refused grouped attempt prepares again under the writer gates and publishes alone as a batch of one under them (`publish_alone_under_gates`), logged with its exact keys; publications of the handle outside the group are logged with their tables `changed` |
| H7 typed in-doubt | followers get `manifest_publish_in_doubt(batch commit id, …)` |
| H10 edge footprint | admission adds each written edge table's endpoint node tables to the entry's footprint |
| H11 error typing | above |
| H12 publisher trust vs writer epoch | above |

Repro tests after the fixes (`results/repro-3-fixed.log`): 7 of 7 pass. The
edge repro surfaced H10 only after H5's per-table logging let entries be
admitted across a logged publication on another table.

Harness on the fixed build (`results/gs-runs.log`):

| run | config | ok/s | writes/commit (max) | caller errors | p50 / p99 ms | violations |
|---|---|---:|---|---|---|---|
| f01 | 32 writers, mixed, 1 actor | 28.4 | 1.04 (20) | 0 | 1,282 / 2,676 | 0 |
| f02 | + 4 actors | 27.3 | 1.00 (2) | 0 | 1,115 / 3,614 | 0 |
| f03 | + 2 handles | 26.1 | 1.02 (10) | 0 | 1,324 / 2,969 | 0 |
| f06 | 50% cancelled ≤0.8 s | 27.5 | 1.00 | 439 cancelled | 923 / 2,226 | 0, no hang |
| f08 | 32 writers, inserts only | **175.9** | 13.0 (31) | 0 | 134 / 482 | 0 |
| f01-ungated1 | `UNGATED_RETRIES=1` | 28.2 | 1.00 (1) | 0 | 1,068 / 2,991 | 0 |
| f01-ungated32 | `UNGATED_RETRIES=32` (prototype's rule) | 34.5 | 2.08 (31) | 0 | 624 / 3,837 | 0 |

**RFC-level finding: under a mixed workload on one table, the gated second
attempt removes batching.** Every update, delete or upsert of a present key
is a publication outside the group; logged as `changed` for its table (RFC
§The effect log: "`changed`, for anything else"), it refuses every queued
insert into that table; each refused insert then prepares again holding the
branch gate and publishes alone, serially. Writes per commit fall from 2.08
to 1.04 and p50 doubles; throughput falls 18%. The prototype's ungated
re-preparation keeps batching but has no progress bound (h02: `ReadSetChanged`
to callers). The remedy that keeps both is finer classification: an update
or delete of keyed rows changes exactly those ids, so it refuses only the
entries whose inserted ids or edge endpoints it touched (see Improvements).

## 2026-11-15 — RustFS (+30 ms RTT) served comparisons, faults, maintenance

Mixed workload (70% fresh inserts, ~15% updates/deletes/upserts of present
keys on the same table, persons, edges), 48 writers over 4 actors, optimize
every 30 s, 60 s window, proxy RTT checked before and after each run
(32–33 ms; `scripts/proxy-rtt.sh`):

| run | variant | writes/s | writes/commit | p50 / p99 | caller errors |
|---|---|---:|---:|---|---|
| r03c | group commit off | 3.0 | 1.00 | 23.0 / 26.3 s | 0 |
| r03a | fixed, RFC gated retry | 3.2 | 1.06 | 19.6 / 32.3 s | 0 |
| r03d | unfixed prototype | 5.75 | 2.86 | 2.4 / 39.5 s | 24 × 409 |
| r03b | fixed, 32 ungated retries | 8.0 | 3.47 | 5.5 / 32.1 s | 0 |

(r01/r01b–d, the same configuration earlier, agree within noise.) Under a
realistic mix the RFC design gains ~nothing on an object store: every write
outside the group refuses the queued inserts on its table and each refused
insert then holds the branch gate for its whole preparation and publication
(~10 serial requests).

**Faults** (r02: resets, truncated responses, stalls, jitter in 17 windows of
2 s at 30% toxicity, 32 writers, 2 actors, 90 s): 0 violations. The
conditional-put re-issue path fired on lost acknowledgements of detached
table manifests ("manifest request outcome unknown; re-issuing the identical
conditional request", then "version held after a re-issue"): every write
still landed once.

**Infrastructure note.** toxiproxy degraded at one point (63 s per request
through the proxy, 12 ms direct); restarting it restored 32 ms. Soak 1 was
discarded for it; every later S3 run records the proxy RTT before and after.

**Write observer cap.** `omnigraph-server` admits at most 64 concurrent write
responses (`DEFAULT_WRITE_OBSERVERS`, hard-coded; the env knobs
`OMNIGRAPH_PER_ACTOR_INFLIGHT_MAX`, `OMNIGRAPH_GLOBAL_INFLIGHT_MAX`,
`OMNIGRAPH_INGRESS_INFLIGHT_MAX` do not raise it). An `optimize` submission
is a write: with 64 writers in flight it is refused 429 every time. The RFC's
soak (64 writers + maintenance) cannot run on the served path as specified.

**H13 (fixed in `12184f03`): optimize and index-build publications were not in
the group's log.** Local served run, 64 writers, pure inserts: 505 writes/s
until the first optimize landed, then every queued entry was refused.

**H14 (fixed): a refusal storm that never recovers.** Even with H13 fixed the
storm persisted (p03: 1,185 refusals in 25 s, 691 → 67 writes/s after the
first optimize). Cause: `lead_group` copied the log when it drained the queue,
before waiting for the branch gate; publications made meanwhile (refused
writers publishing alone under the gate, the optimize) were missing from the
copy, so the batch "did not reach the head", refused everything, and its
zero-admission outcome `LogChange::Clear` wiped the live log, including those
records. Every later batch repeated it. Soak 2 (S3, 56 writers) showed it on
real binaries: 122 writes/s in minute 1, 2.35 writes/s (p50 14.7 s) after the
first optimize at 60 s. Fix: read the log under the gates; a batch that
admitted nothing keeps the log.

**Maintenance starvation under batches (RFC §Interactions, confirmed).**
Embedded harness, 48 writers, pure inserts, ~120 writes/s at 17.8
writes/commit, `optimize` every 6 s and `ensure_indices` every 4 s on the
direct path: **0 of 10 index builds and 0 of 6 optimize runs landed**, every
one refused "write authority 'graph_head:main' changed during preparation"
(captured before its gates). The served optimize captures under the gates
(`AuthorityCapture::UnderGates`) and lands; index builds have no served route.
The RFC's maintenance carry (rollout step 4) is required, not optional, once
batching is on.

**Unindexed-tail decay without maintenance.** Local served, 48 writers, pure
inserts, no maintenance (p02): 695 → 292 → 235 → 198 → 159 writes/s at 5 s
intervals; batch time grows with the fragment count (one fragment per write).

## 2026-11-15 — improvement 1: exact footprints for publications outside the group

Hypothesis: the mixed-workload collapse comes from logging every write outside
the group as `changed` for its whole table. A keyed update or delete touches
exactly the ids its staging folded (pending batches' id column) or deleted
(`MutationStaging::deleted_ids`); an insert-only entry read only the absence
of its own ids and the existence of its edges' endpoints. So it is refused
only when an upserted id is one it inserts, or when a delete hit a table it
reads without writing. Updates and deletes of other ids explain nothing away
because they change nothing the entry read.

Prototype (`OMNIGRAPH_GROUP_COMMIT_EXACT_TOUCHED=1`): `StagedMutation.touched`
computed in `stage_all_with_concurrency`, carried to the publisher call in a
task-local (`group_commit::EXACT_TOUCHED`), recorded as `upserted`/`deleted`
per table in the log record; `admit_inner` checks overlap. Tables whose ids
were not read (loads, merges) stay `changed`.

Same harness, same seed (local, dev build, 32 writers, default mixed mix,
gated retry, 20 s):

| | writes/s | writes/commit | p50 | p99 | violations |
|---|---:|---:|---:|---:|---|
| exact off (f10-mixed-exact0) | 24.4 | 1.05 | 1,372 ms | 3,660 ms | 0 |
| exact on (f10-mixed-exact1) | **35.2** | **1.96** | **768 ms** | 2,861 ms | 0 |
| reference: ungated retries, no bound (f01-mixed-ungated32) | 34.5 | 2.08 | 624 ms | 3,837 ms | 0 |

All 7 repros pass with it on. Verdict: keep; it recovers the batching the
gated retry lost while keeping the two-attempt bound. RFC change: classify
publications outside the group by exact touched ids where the writer knows
them, not `changed` per table.

## 2026-11-15 — crashes: kill -9 and crash points

- **c01** (local served, build 6, 32 writers, 2 actors, mixed, 90 s): the
  server killed -9 four times at random (mean 12 s), admission lock
  force-unlocked and the server restarted each time (~0.35 s). 1,516
  acknowledged, 168 in doubt (128 disconnects mid-request). **0 violations**:
  every acknowledged write present once, in-doubt writes at most once, the
  graph reopened cleanly each time.
- **c02** (local served, build 7 with research crash points
  `OMNIGRAPH_STRESS_CRASH=batch_admitted:4,batch_committed:4,batch_published:4`,
  `std::process::abort()` at the point, a watchdog restarting the server):
  8 aborts, all at `batch_committed`, i.e. after the batch's detached table
  commits and before its `__manifest` compare-and-swap. 1,749 acknowledged,
  293 in doubt, 209 refused (connection refused while restarting). **0
  violations.** Afterwards `omnigraph cleanup --keep 1 --confirm` on the root
  reclaimed exactly **8 unpublished manifests** in `node:Item` (one per
  abort) plus old versions, reported no foreign versions and no errors, and
  the graph exported byte-identically before and after (1,407 rows). The
  collector's existing rule handles batch staging, as RFC §Failure
  checkpoints argues. (The time-based roll in `crash_point` never selected
  the other two points in this run: gVisor's coarse `subsec_nanos`; c01's
  random kills cover them statistically.)

## 2026-11-15 — improvement 2: lift the probe cap when known keys cover the tail

Observation (local served, pure inserts, no maintenance): throughput falls
off a cliff once a table has more than 8,192 fragments outside the id index
(one fragment per write): stacks are all Lance `filtered_read::read_fragment`
tasks. `stage_keyed_write` runs the exact-id absence probe only while
`tail.fragments × source_ids ≤ KEYED_WRITE_MAX_ROWS` (#1225, Lance plans the
probe's IN list once per uncovered fragment); past it, every keyed insert is
Lance's full-table merge join (`use_index(false)`): a scan of every
fragment's id column per write, staged without exact ids (Bloom-only group
admission) and recording no known keys, so the tail never becomes known
again.

Change (`OMNIGRAPH_KNOWN_KEYS_LIFT_CAP`, on): when the known keys answer every
uncovered fragment, the probe scans only the indexed fragments
(`fast_search`, which Lance 13 honours for scalar index queries:
`scanner.rs:3697`, `with_only_indexed_fragments`), so the cap does not apply.

Embedded harness, 48 writers, pure inserts, no maintenance, 90 s, writes/s
per 10 s:

| | 0–10 | 10–20 | 20–30 | 30–40 | 40–50 | 50–60 | 60–70 | 70–80 | 80–90 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| cap (f11-liftcap0) | 266 | 138 | 100 | 84 | 69 | 62 | 62 | 47 | **0.0** |
| lifted (f11-liftcap1) | 216 | 111 | 79 | 72 | 68 | 60 | 53 | 54 | **44.5** |

Verdict: keep: it removes the cliff. The steady decay before it remains:
several per-write and per-batch passes are O(fragments) (uncovered-set
computation, tail scans of fragment metadata, the detached manifest the
leader writes and every writer decodes), and every write adds a fragment.
Only maintenance (or fewer fragments per write) bounds it.

Also seen in the stacks: Lance builds the Blob v2 projection schema once per
fragment read (`filtered_read.rs:1484` → `public_blob_v2_binary_projection_schema`
→ `Schema::to_schema`, allocating every field name), a per-fragment cost that
dominates scans of many small fragments; hoisting it to the plan is an
upstream ask.

## 2026-11-15 — several processes on one root; a writer-epoch claim mid-load

`scripts/mp_run.py`: `omnigraph init` + seed + `index build` on a local
root, P harness processes (`OG_GS_URI`, distinct key prefixes), CLI export and
`commit list --json`, one combined check (`http_stress.py check`).

- **mp01**: 4 processes × 8 writers, mixed, 30 s: 578 acknowledged, 0
  violations; writes per commit 1.0 (each process's publications are foreign
  to the others, as the RFC expects across processes); 3 writes exhausted the
  32-attempt cross-process bound (`ReadSetChanged` to the caller), the
  existing ordinary-path behavior across processes.
- **mp02**: as mp01, 6 writers each, process 0 claims `main`'s writer epoch at
  10 s (`claim_branch_writer`): claimed at epoch 1 in 404 ms; processes 1–3
  then had every write refused `WriterFenced` ("nothing was published"), 438
  in all; process 0 kept writing (396 acknowledged). **0 violations.**
  (`WriterFenced` carries no completion evidence, so the harness counts those
  refusals as in doubt; the message says nothing was published.)
- **Pre-existing, not batching (v01):** with group commit **off**, validation
  refusals (`@unique`, missing endpoint) also answer 400 with disposition
  `effects: unknown`.

## 2026-11-15 — Lance 13.0.0 and 14.0.2 code read for the upstream asks

- Detached commit (`lance-13.0.0/src/io/commit.rs:1197`
  `do_commit_detached_transaction`): awaits `write_transaction_file` (line
  ~1233, commented "We don't strictly need a transaction file") before
  building and writing the manifest; picks the random detached version inside
  its retry loop (`rng().random::<u64>() | DETACHED_VERSION_MASK`); returns at
  `Ok(location)` without `record_successful_commit` (`commit.rs:1532`, called
  only from the linear path at 1780/1812/1882), so no transaction, manifest or
  index-metadata cache entry is made. 14.0.2: same sequence (line ~1336), same
  cache gap (`record_successful_commit` at 1646, called at 1941/1973/2046).
- `ManifestWriteConfig::with_transaction_file_disabled`: `pub(crate)` in
  14.0.2 (`dataset.rs:4347`); still no public switch.
- `CompositeOperation` / `AssertUniqueKeys`: **absent from Lance 14.0.2**; the
  `Transaction.operation` oneof in `lance-table-14.0.2/protos/transaction.proto`
  ends at `DataOverlay = 115`. The RFC's "Lance 14's `CompositeOperation`"
  sentence needs correcting.
- `FLAG_FRAGMENT_TREE` exists in `lance-table-14.0.2/src/feature_flags.rs:92`
  and is marked unsupported (`mark_supported(.., false)`, line 345).
- Per-fragment Blob v2 projection schema rebuild still present in 14.0.2
  (`filtered_read.rs:1535`).
- 14.0.0 release notes items that bear on this work: inline transaction moved
  to a new manifest field (#9482), `read_version_transaction` served from the
  held or cached manifest (#9915), index paths reading the manifest version
  without summarizing every fragment (#9942), scan plans reusing the fragment
  lookup (#9653), commit retry budget started after catch-up (#9631).

## 2026-11-15 — improvement 3: memoize the known-tail analysis per manifest version

Observation: each keyed insert computes `key_column_uncovered_fragments`
three times (each builds `BTreeSet`s of every fragment id: O(F log F)),
`key_column_uncovered_tail` once more, and takes the global known-keys mutex
once per uncovered fragment, twice; 48 writers probing the same pin repeat
identical work on an immutable table version.

Change (`OMNIGRAPH_KNOWN_KEYS_TAIL_CACHE`, on): `table_store::known_tail`
computes, once per manifest file (`ds.uri()` + `manifest_location().path`),
whether the known keys answer every uncovered fragment and the union of their
ids; `known_tail_members` answers from the union in O(ids); an all-new upsert
with a known tail skips the tail's metadata scan. Same answer as before by
construction (the union is exactly the per-fragment sets the loop consulted;
a version is immutable; a known-keys entry is a fact about its file).

Embedded harness, 48 writers, pure inserts, no maintenance, 60 s, cap lift on
for both, writes/s per 10 s (the soak was running beside it on S3):

| | 0–10 | 10–20 | 20–30 | 30–40 | 40–50 | 50–60 | mean | writes/commit |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| memo off (f12-tailmemo0) | 258 | 115 | 91 | 78 | 66 | 59 | 112.1 | 23.1 |
| memo on (f12-tailmemo1) | 415 | 250 | 199 | 194 | 152 | 134 | **224.7** | 23.9 |

0 violations both. Verdict: keep (2.0× mean, 2.3× in the last window). The
remaining decay is the per-batch O(fragments) manifest work and the rest of
§5.3; only maintenance or fewer fragments per write remove it.

## 2026-11-15 — improvements 2 + 3 on real binaries

Local served, release, 48 writers, pure inserts, no maintenance, no readers,
40 s, writes/s per 5 s (`served/i01-*`):

| build | 0–5 | 5–10 | 10–15 | 15–20 | 20–25 | 25–30 | 30–35 | 35–40 | total | writes/commit |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| build 7 (no cap lift, no memo) | 698 | 323 | 257 | 207 | 162 | **27** | **36** | **33** | 8,760 | 13.6 |
| final (cap lift + memo) | 757 | 290 | 223 | 198 | 170 | **126** | **136** | **134** | 10,217 | 14.5 |

0 violations both. In release the memo's per-write saving is smaller than in
the dev-build harness (2.0×): the O(F log F) passes cost less relative to the
rest; the cliff removal (3.7–4.6× in the last 15 s) is the visible gain.

## 2026-11-15 — soak 3: one hour on RustFS (build 6)

`served_run.py soak3-s3-w56 --backend s3 --bins /srv/bins/proto-fixed3
--optimize-secs 60 --server-env OMNIGRAPH_PER_ACTOR_INFLIGHT_MAX=128
--server-env OMNIGRAPH_GLOBAL_INFLIGHT_MAX=160 --server-env
OMNIGRAPH_INGRESS_INFLIGHT_MAX=160 --server-env
OMNIGRAPH_GROUP_COMMIT_STATS_SECS=60 -- --writers 56 --secs 3600 --actors 1
--mix fresh=100 --reader-count 1 --read-interval 1`; proxy RTT probed each
minute (31.2–40.2 ms). Series: `results/soak3-series.md`; gates:
`results/soak3-gates.json` (`scripts/soak_report.py`).

- 362,706 acknowledged, all verified (final export + model), 0 violations;
  12,987 commits, 27.9 writes/commit (max 55); p50 504 ms, p99 1,068 ms;
  6,233 `count` observations all equal to the model at their commit.
- writes/s: minute 1 124, then ~100–104, last 5 minutes 94.5 (0.85 of the
  first 5).
- optimize: 54 of 54 published, 6.1–7.4 s each, flat; writers' max latency
  ~6 s in those minutes.
- RSS: 2,191 MB (1 min), 2,621 (10), 3,454 (30), 5,219 (60): 1.32× at 30/10,
  1.99× at 60/10; growth slowing to ~25 MB/min at the end. Lance default
  caches (6 GiB index, 1 GiB metadata) are the likely sink; not proven.
- known-key files 362,650 (one per write, never evicted); queue 1–35.
- During minutes ~28–45 local measurements ran on the same box (improvement
  3, i01): minute 43's dip to 90 writes/s and p99 6.1 s coincide with them.

## 2026-11-15 — RustFS mixed workload, final build: exact footprints

`served/r04a-final-changed`, `served/r04b-final-exact`: same config as r03
(48 writers, 4 actors, 70% fresh inserts, ~15% same-table updates and
deletes), final build (cap lift and memo on), gated retry.

| | writes/s | writes/commit | p50 / p99 | violations |
|---|---:|---:|---|---|
| `changed` per table (RFC as written) | 3.3 | 1.06 | 20.0 / 32.3 s | 0 |
| exact footprints | **9.0** | **4.07** | **5.8 / 11.9 s** | 0 |

2.75× and the p99 cut by 63%, beating the unbounded ungated variant (8.0).

## 2026-11-15 — 1,024 writers (embedded, dev build)

90% fresh, 10% same-table updates/deletes/upserts, 30 s:

| | writes/s | writes/commit (max) | p50 | p99 | violations |
|---|---:|---|---:|---:|---|
| RFC rules (f13-w1024) | 57.3 | 1.0 (1) | 42.4 s | 125.8 s | 0 |
| exact footprints (f13-w1024-exact) | 89.3 | 15.6 (512, the cap) | 13.7 s | 50.5 s | 0 |

## 2026-11-15 — one writer on RustFS (final build)

`served/w1-gc-off`, `served/w1-gc-on`: 1 writer, pure inserts, 30 s, no
readers. Off: 3.73 writes/s, p50 268 ms, p99 277 ms. On: 4.20 writes/s, p50
238 ms, p99 251 ms. 0 violations. A batch of one costs no more (the leader's
capture from its own last publication and the index warm-up save requests).

## 2026-11-15 — final suite rerun (final build)

`scripts/final-suites.sh` (`results/final/`): `writes` 67/67 and engine
`integration` 704/704 with batching on and off; `detached_commit_matrix` 24
pass, 7 fail (5 seam, 2 carry); `failpoints` 167 pass, 2 ignored, 13 fail
(10 seam, 3 asserting the ordinary path's re-preparation or carry counter;
`insert_overlapping_an_optimize_publication_does_not_reprepare_issue_1331`
now passes thanks to H13, `insert_still_reprepares_over_a_row_change_after_optimize_issue_1331`
now fails because the group path admits an entry the other table's insert
did not touch); DST lib 54/55, scenarios 54/57 (seams, the cost golden);
GQT 477/477.

RustFS-configured engine tests (`integration … s3`, RustFS direct): 23 pass,
2 fail, identically with batching on and off:
`durable_head_lookup_cost::s3_durable_head_lookup_matrix_is_correct_and_observable`
and `checkpoint_retention_cost::s3_checkpoint_retention_matrix_is_exact_and_records_the_current_no_go`.
Not classified against `origin/main` in this pass.

## 2026-11-16 — cross-actor batches: implementation

RFC #1405 at `2f82b02b` (Part 2 rules 6 and 19, "The batch", "Attribution").
Changes (commit `7f59b2f1` and later):

- `drain_batch` takes queued entries in arrival order whatever their actors,
  up to `MAX_BATCH_ENTRIES` (512) and the write list's byte budget
  (`HISTORY_RELEASE_BYTES / 2`, `OMNIGRAPH_GROUP_COMMIT_WRITE_LIST_BYTES` for
  tests); once one entry does not fit, every later one keeps its place.
  `OMNIGRAPH_GROUP_COMMIT_ONE_ACTOR=1` restores one-actor batches for the
  comparison.
- The write list is persisted in the commit record. Research encoding: the
  existing `actor_id` column holds `"\u{1}gcw1" + JSON {"a": common|null,
  "w": [{"a": actor, "r": [[table, start, end], ...]}]}` for a commit of two
  or more writes, so it counts toward the record's byte budget and survives
  history release without a format change. `GraphCommit::decoded()` turns it
  into `actor_id` (the common actor or none) plus `writes` at every outward
  door: `list_commits`, receipts, HTTP `CommitOutput.writes`, the change
  feed's cause.
- Ranges: the leader records each member's staged data files before
  composing, and after each table's detached commit reads the inline row-id
  sequence of every one of those fragments from the committed manifest
  (`fragment_row_ranges`); a member's ranges must be one consecutive run whose
  width equals the rows it wrote, or the batch fails with `Unpublished`
  evidence before its publication.
- Change feed and commit get: `attribute_batch_inserts` looks up the
  `_rowid` of every row a batch commit inserted, at the commit's own
  snapshot, and gives the row the actor (`actor_id`) and the write-list index
  (`write`) of the range that holds it. HTTP `EntityChangeOutput` carries
  both.
- Server research knob: `OMNIGRAPH_WRITE_OBSERVERS_MAX` raises the hard-coded
  64 write observers so served `optimize` is admitted beside 64 writers (O1).

Oracle (`tests/group_commit_stress/attribution.rs`, used by the focused
tests, the embedded harness and `gc_attribution_offline` for served roots;
`scripts/attr_http_check.py` over HTTP): per batch commit, the common actor
rule; one range per table per write, non-empty, disjoint across all commits;
commit get reports every row with a write index and that write's actor, and
each (write, table) has exactly as many rows as its range is wide; at the
commit's snapshot every row with an id in the ranges is a row commit get
reports for that write; each acknowledged write matches exactly one entry
with its actor and exactly its rows; entries that match no acknowledged
write match an in-doubt one; the feed from the beginning reports the same
(write, actor) per row as commit get; at the head, every surviving row whose
id lies in a batch range is a row that write inserted. HTTP side also: every
in-doubt write names its batch's attempted commit; if that commit is
published each such write is in its list, if not none of its rows exist.

## 2026-11-16 — focused tests (debug build)

`gc_mixed_actor_batch_attributes_every_row`: 7 writes of 5 actors (one
without an actor; node, two-table and edge inserts) in one commit, exact;
again after an update and a delete of batched rows and an `optimize`.
`gc_write_list_budget_splits_batches_of_long_actor_ids`: 12 writers with
300-byte actor ids, budget 1,400 bytes: batches of 2 (711–715 bytes), all
published in arrival order, exact. `gc_cross_actor_conflicts_in_one_batch`:
act-a and act-b insert the same absent key into one batch: act-b is sent
back and publishes alone (as an update of the key) under its own actor; no
write list names act-b; two Tags with one `@unique` label: one publishes,
the other gets the typed `@unique violation` (on the ordinary path: non-key
`@unique` tables never batch in the prototype).

Defects found here:

- D1: the publisher's `debug_assert_eq!(lineage_intent.actor_id, actor_id)`
  fired for the first mixed batch (debug builds panic; release unaffected).
  Fixed: the group hands the publisher the recorded form.
- D5: the write-list size estimate left out the common actor the header
  carries. Fixed: exact JSON lengths, the header counts the first entry's
  actor, and an encoded list over the budget fails the batch before its
  publication.
- D2–D4 on the pipelined path (`OMNIGRAPH_GROUP_COMMIT_PIPELINE=1`, an
  inherited #1401 experiment, off by default): it published with the first
  member's actor and no write list (rule 19: a mixed batch credited to one
  member); its drain ignored the budget and closed callers (the new
  post-encode guard refused a 3,823-byte list against a 1,400-byte budget);
  it had no drop guard, so a dropped embedded leader hung the group
  (`gc_dropped_leader_hands_the_lead_on` timed out). Fixed: one
  `build_write_list` and one `drain_batch` for both paths, and a `LeadGuard`
  on the pipelined lead that answers every drained member and hands the lead
  on only if it has not handed it already. All 11 focused tests pass with
  pipelining on and off.
