# Spike: remote optimize through the serving owner (RFC #1214)

**Branch:** [`ragnorc/omnigraph@spike/remote-optimize-1214`](https://github.com/ragnorc/omnigraph/tree/spike/remote-optimize-1214) (throwaway code; the findings are the deliverable). It is on the fork because `ModernRelay/omnigraph`'s `fork-only` ruleset refuses new branches.
**RFC under test:** `docs/rfcs/2026-10-29-remote-optimize-through-the-serving-owner.md` at `ba6e2b4a`
**Date:** 2026-11-01

## Verdict

**Correct with listed amendments.** The overall design holds under every
stress test that a single host can run. The serving owner executes the run.
The record is durable before any effect. The record stores the nonce, and
main's history settles a run after a restart. The run publishes once. The
gate hold is bounded. No finding changes this shape. Four findings need a
change to the RFC text before implementation:

1. Graph deletion strands unresolved records (§2.4, §3.4). After a deletion,
   the only exit the RFC allows can never happen. With four such records,
   the server refuses maintenance on every graph of the cluster, with no
   way out. This is a rule change, not a new mechanism. It is reproduced.
2. The acceptance window is not atomic (§3.3). A SIGTERM or a client
   disconnect can arrive after the durable acceptance write and before the
   run is registered. The accepted id then never runs. On SIGTERM the
   caller is also told `503`, `effects: none`.
3. The record's size bounds do not compose (§2.2). Four records at the
   per-record bound exceed the 4 MiB total. A large catalog can also make
   the before-publish write itself too large, and then every run fails.
4. The bounded resolution walk should use `CommitGraph::first_parent_page`
   and store its `next` parent as the cursor. Settlement is per graph. This
   agrees with both blocking points of the existing review on #1214.

The full list of amendments is under [Amendments](#amendments).

## What was built

The spike starts from the three stacked implementation drafts: #1204
(engine), #1205 (server record and routes) and #1206 (CLI). #1176
(control-object re-issue) is merged on top, and so is current `main`, for
`first_parent_page`. Together they already form an engine → HTTP → CLI
walking skeleton. The spike changed the parts that differ from the RFC
text, or that the RFC changed after the drafts were written:

| Change | Where |
|---|---|
| The HTTP request accepts only `maintenance_id`, `tables` and `max_gate_hold_ms`. The resource ceilings, the run byte budget and `resolution_walk_limit` (default 256) are serve settings (`OMNIGRAPH_MAINTENANCE_*`). #1205 still accepted six ceiling fields in the request body. | `omnigraph-server/src/maintenance.rs`, `omnigraph-cluster/src/maintenance.rs` |
| A bounded resolution walk, `Omnigraph::resolve_publication_page`. It tests the graph head first, then reads one `first_parent_page` of at most `limit` commits. It returns `Walking { next, covered }`, which the record stores (`resolution_next`, `resolution_covered`), and the next attempt resumes at `next`. | `omnigraph/src/db/omnigraph/resolution.rs` |
| Artifact under shutdown. The edge scan is raced against cancellation and abandoned when it fires. The write is skipped if a cancellation or shutdown is pending at the moment it would be issued. Once issued, it is still awaited. The drafts checked only before the scan. | `optimize.rs`, `persist_graph_index_artifact` |
| A revalidation refusal is labelled `BeforeEffect`. In the drafts it carried no evidence. An opt-in `with_spike_recapture_under_gates` captures authority again under the held gates. | `optimize.rs` |
| `OMNIGRAPH_SPIKE_PAUSE_AT=<point>[:<ms>]` pause points: `server.{before,after}_accept_write`, `server.after_running_write`, `optimize.after_table_effect`, `server.after_publishing_write`, `optimize.after_publication`, `optimize.before_artifact_write`, `server.before_terminal_write`. | `omnigraph/src/spike.rs` and call sites |

**Stubs and limits.**

- The pause points are a test instrument.
- Everything else is real: the engine, the production server in a child
  process, the cluster lock and the record. All of it runs on a local
  `file://` cluster root.
- The CLI's served leg is the existing system test from #1206. The spike
  adds only the direct-path lock check.
- MCP is out of scope, as the RFC says.

## Per-claim results

Test names are in `crates/omnigraph-server/tests/spike_remote_optimize.rs`
(S), `crates/omnigraph/tests/integration/spike_remote_optimize.rs` (E) and
`crates/omnigraph-cluster/tests/spike_record_bounds.rs` (C).

| Mechanism / claim | Result | Evidence |
|---|---|---|
| Request carries `tables` and `max_gate_hold` only; ceilings and budget are serve settings (§1.1, §3.1) | **Holds** (implemented as the RFC states; #1205 diverged) | spike server change; all S tests run with the trimmed request |
| `max_gate_hold` above the ceiling refused | **Holds**, with the review's optional point: the code answers 413, which `http_contract.rs` uses for body size; 400 fits better | existing `maintenance_routes::routes_refuse_*` |
| Durable record under a caller-chosen id, written before any effect (§2.1, §2.3) | **Holds** | S `spike_kill_at_each_step_resolves_from_history`: killed before the acceptance write → `unknown`, graph unchanged; killed after it → `interrupted` |
| Record keyed by `kind`, no event history (§2.2) | **Holds** (`MaintenanceKind::Optimize`; one file) | code |
| Record size bounds 32 / 1 MiB / 4 MiB (§2.2) | **Holds with a change** (amendment 3) | C `spike_per_record_and_total_bounds_do_not_compose`: 3 unresolved + 1 `publishing` at 1 MiB each = 4,194,386 bytes > 4,194,304, so the record is unwritable. Once one settles, the record still fails validation. C `spike_tables_per_record`: 156 B per plain table outcome (6,721 per MiB), 2,213 B with a 2 KiB failure detail (473 per MiB). 657 failed tables make the before-publish write fail validation, so nothing can publish. |
| Same id + same request → existing operation; 202 while non-terminal, 200 when terminal (§3.1) | **Holds** | S `spike_concurrent_submissions_and_lost_response`: 8 parallel POSTs, same id → 8 × 202, one operation. Existing route test: same id after terminal → 200 with an identical record. |
| Same id, different request → 409 | **Holds** | existing `served_optimize_keeps_a_record_a_caller_reads_by_id` |
| Different ids while one is active → 429 (§3.2) | **Holds** | S: 8 parallel new ids while one runs → 8 × 429 |
| Lookup by id after a lost response; never resubmit (§2.5, §4) | **Holds**; one window does not (amendment 2) | S: client timeout of 1 ms, then lookup → `found`, the run → `succeeded`, a retry under the same id → the identical record, one commit. A request dropped before the server reads it → `unknown`, and a retry is a fresh acceptance (one run). |
| Disconnection stops waiting, not execution (Observable contract) | **Blocked as built; holds with a change** (amendment 2) | S `spike_disconnect_between_acceptance_and_registration`: the handler is dropped on disconnect. A disconnect after the acceptance write and before registration → the accepted operation ends `interrupted` and never runs. A disconnect before the write → `unknown`. Once this probe also failed intermittently; not reproduced in 6 reruns. |
| Evicted ids answer `unknown` (§2.5) | **Holds as written**; optional amendment 7 | C `spike_an_evicted_id_is_admitted_again`: after 32 later operations, the same id and request is admitted as `New` and would run again |
| Restart resolution: `accepted`/`running` → `interrupted`; `publishing` by history; never re-executed (§2.4) | **Holds** | S kill matrix, 7 points, each followed by a restart: before accept → `unknown`; after accept, after running, mid-compaction (`after_table_effect`) → `interrupted`, head unchanged, rows intact; after the publishing write → `publishing`, then `failed`/`not_committed` once a later run publishes; after publication and before the terminal write → `succeeded` with `published_commit` = main's `hb1` head. A resubmission of the same id after restart returns the record and runs nothing. |
| `before_publish` callback once, before any manifest request; failure publishes nothing (§1.6) | **Holds** | existing E tests (`optimize_calls_before_publish_once…`, `optimize_publishes_nothing_when_before_publish_fails`); kill at `server.after_publishing_write` leaves the head unchanged |
| The record's conditional put fences an old owner (§3.6, "late record put") | **Holds** | S `spike_old_owner_is_fenced_by_the_record_after_a_takeover`: the old owner is paused mid-run; the operator force-unlocks and a successor starts and records `interrupted`. The old owner resumes, its before-publish write is refused, it publishes nothing and the record stays `interrupted`. |
| Single publication; finished tables publish once (§1.6) | **Holds** | existing E tests; the kill matrix shows exactly one commit (4 → 5) |
| Bounded resolution walk with `resolution_walk_limit`, resume below the stop (§2.4) | **Holds with a change** (amendment 4) | E `spike_paged_resolution_reaches_the_child_at_every_boundary`: the child sits 13 commits deep, partly released to `__history`. For limit L ∈ {1,2,3,4,5,6,12,13,14,256}, the walk takes exactly ⌈13/L⌉ attempts to reach `Committed`, or `NotCommitted` for a foreign nonce. A cursor taken before main grows still resolves. S: with limit 4 and 10 writes, three admission attempts settle all four records. |
| Settlement only by a move of the record's graph's main head (§2.4) | **Holds per graph** in code; the text must say so (amendment 4) | S `spike_unresolved_list_fills_and_only_its_own_graph_settles_it`: 4 unresolved on `knowledge` → a fifth admission is refused with 409, on `other` as well. Writes on `other` settle nothing. Writes on `knowledge` settle all four as `failed`/`not_committed`. |
| Unresolved list cap of 4 and its only exit (§2.4) | **Blocked as written** (amendment 1) | S `spike_deleted_graph_wedges_the_unresolved_list`: 4 unresolved on `knowledge`, then a deployment deletes `knowledge`. Writes on `other` follow, and admission on `other` is still 409 with 4 unresolved. No main head can move, and the RFC rules out an override. |
| Gate-hold bound with an exact per-table outcome; deferral and resume (§1.2, §1.3) | **Holds** | E `spike_many_tables_are_covered_by_successive_bounded_runs`, 48 tables with a 150 ms hold. Run 1 visits 32 tables (gate hold 212 ms, largest table 94 ms, a concurrent writer waits 240 ms) and its resume position is `node:T32`. Run 2 starts there, wraps and finishes, and its position clears. Every table is optimized exactly once, and no run visits a table twice. Existing E: `optimize_stops_at_the_gate_hold…`, ceilings, byte budget. |
| Writer wait ≤ `max_gate_hold` + one table + publication + clear (§1.2) | **Holds** at this scale | the measurement above. A single large table is bounded only by the ceilings (RFC unresolved question 1). |
| Writers on main racing an optimize: writes delay, never fail | **Holds** for writes; **holds with a change** for the run (amendment 6) | E `spike_writers_race_optimize_authority_capture`: 4 writers and 20 runs. All writes succeed. 4 runs are refused (`graph_head changed`), and before the spike that refusal carried no completion evidence. With a capture again under the held gates, 0 of 20 are refused. S `spike_writers_race_served_optimize`: all writes succeed; 0–1 of 10 runs are refused; writer p99 is 172–193 ms. |
| Concurrent direct runs on one standalone root fail closed (§3.6) | **Holds** | E `spike_two_direct_runs_on_one_standalone_root`: one publishes, the other refuses before any effect, and the rows are intact |
| A direct optimize on a served cluster root → `state_lock_held` (§3.6) | **Holds** | S `spike_cli_walking_skeleton` (CLI binary) |
| A foreign-ledger record is refused, never overwritten (§2.1) | **Holds** | S `spike_foreign_record_is_refused`: submit 409, lookup 409, the file is byte-identical |
| Artifact write skipped under a pending shutdown; an issued write is awaited (§1.8, §3.4) | **Holds with a change** (amendment 5) | S `spike_artifact_write_is_skipped_under_a_pending_shutdown`: SIGTERM after the scan and before the write → `succeeded` with its commit, artifact `skipped`/`cancelled`, the file is unchanged and the process exits 0. The drafts checked cancellation only before the scan. |
| Shutdown during the record write (§2.3, §3.4) | **Holds with a change** (amendment 2) | S `spike_shutdown_during_the_acceptance_write`: SIGTERM before and after the acceptance write. In both cases the acceptance still lands after shutdown began, registration then fails and the caller gets `503` with `effects: none`. After restart the id is `interrupted`. Exit 0 and a clean release. |
| Cancellation at safe boundaries; `publishing` is unchanged by a cancellation (§1.9) | **Holds** | existing E cancellation tests and route code; not stress-tested further |
| Served CLI: mint and print the id, wait, `--operation`, `--no-wait`, `unknown` → exit 2 (§4) | **Holds** | existing `system_local::local_cli_optimize_runs_served_maintenance` passes on this branch |
| `maintain` enforced; `admin` does not grant it (§1.1) | **Holds** | `policy_engine_chassis` 27/27 |

Baseline runs on this branch: engine `maintenance::` 65/65, engine
`failpoints optimize` 11/11, `policy_engine_chassis` 27/27, cluster
`maintenance` 9/9, server `maintenance_routes` 2/2 and its lib tests 2/2,
CLI `system_local::local_cli_optimize_runs_served_maintenance` 1/1. Spike
suites: S 11/11 (plus the ignored child helper), E 4/4, C 3/3. The workspace gate, Clippy, the DST golden
and `forbidden_apis` were not run. The spike's `std::env` read in the
engine would fail `forbidden_apis` on purpose.

## Amendments

1. **The exit when a graph cannot move.** §2.4 says the only exit from a
   full unresolved list is a move of the graph's main head. That fails for
   a graph that a deployment deletes, or deletes and re-adds under the same
   id: the new graph's history never meets the input head. The simplest
   rule that keeps "no override" is this: a deployment that deletes or
   replaces a graph refuses while the graph has unresolved maintenance
   records, and the operator settles them first with a write on that graph.
   The alternative is that the deletion, under the same lock, settles them
   with a distinct terminal reason (published unknown, graph gone). That
   adds a terminal meaning, so the refusal is preferable. Either way, the
   record should name the graph's root lifetime, not only its id, so a
   re-added graph never judges an old record.
2. **Accept and register as one unit.** §3.3 says the operation is
   "registered before it is spawned". The acceptance write itself also has
   to be inside that unit. Register (or spawn) the owned operation first,
   and let it perform the acceptance write. The HTTP handler then only
   awaits its answer, so neither a disconnect nor SIGTERM can separate the
   durable acceptance from execution. Today the server answers `503
   effects: none` for an id the record already holds. That breaks "the
   server either refuses, and nothing is accepted, or accepts". On object
   storage, a handler dropped during the conditional put also leaves the
   key unsettled, and the process does not know.
3. **Make the record bounds compose.**
   - Cap each table outcome's `detail` (for example at 512 bytes, keeping
     the code and measure).
   - Make the per-record bound × (unresolved cap + active + one terminal)
     fit inside the total, or exclude unresolved and active records from
     the total.
   - State what a catalog too large for one record does. One option is
     per-outcome counts after N tables. Otherwise the before-publish write
     fails every run.
4. **Resolution text.** Walk with `first_parent_page` on a fresh read of
   the graph's main, after the graph-head test. Store the page's `next`
   (the unvisited parent) as the cursor, not "the commit it stopped at".
   Say that a write on graph G settles only the records whose input head is
   on G (both points of the 2026-10-31 review). Optional: each admission
   and lookup advances every unresolved record by one page. After a write,
   the exit therefore needs ⌈depth / limit⌉ admission attempts. A time
   budget of several pages per attempt, or a 409 that names the walking
   state, would make this visible.
5. **Artifact.** Check for a pending shutdown or cancellation at the moment
   the write would be issued, not only before the scan, and abandon the
   scan when either arrives.
6. **Authority capture.** A refusal at revalidation must carry
   `BeforeEffect` (§1.7 says that every failure before the publication is
   labelled; this one was not). For the served path, capture again under
   the held gates when the capture taken before the gates has moved. No
   writer in the process can move main while the gates are held, and
   without the re-capture about one in five runs failed under write load.
7. *(Optional)* Refuse a `maintenance_id` whose ULID time is older than the
   oldest retained record (`maintenance_id_expired`). A late retry with an
   evicted id would then not run again. The RFC already says that
   `unknown` never authorizes a replay. This rule makes the server refuse
   that replay as well.
8. *(Optional, agrees with the review)* Answer a `max_gate_hold` above the
   ceiling with 400, not 413.

## Not verified

- **S3, in region:** request latency, the S3 lease fencing and the
  successor after one and a half leases, a late manifest put landing on a
  version that is still absent (the review's optional point), #1176's
  re-issue under S3's 409 answers, the cost of reading `__history` for
  released commits, the 576.8 s no-op planning figure, and artifact write
  throughput against the 25 s shutdown grace. Every test here ran on local
  `file://`, where a killed process cannot leave a put in flight.
- **Azure:** not tested.
- **Deployment cancellation** of a running optimize before its drain:
  checked by reading the code only.
- **Large tables near the ceilings** at production sizes, and peak memory.
  Only the drafts' small per-ceiling fixtures ran (they pass).
- **Collection of staging** left by interrupted or failed runs. The spike
  relied on the existing `detached_commit_matrix` and maintenance owners
  and did not re-run them.
- **A publisher attempt in flight** at the version after a
  configuration-only commit. The `HeadUnchanged` rule was checked by
  reading the code; this cannot be staged on a local filesystem.
- **The DST storage-cost golden**, the workspace gate and Clippy.

## Running the spike

```bash
cargo test -p omnigraph-server --test spike_remote_optimize -- --test-threads=1 --nocapture
cargo test -p omnigraph-engine --features failpoints --test integration spike_remote_optimize:: -- --nocapture
cargo test -p omnigraph-cluster --test spike_record_bounds -- --nocapture
```
