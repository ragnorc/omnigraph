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
