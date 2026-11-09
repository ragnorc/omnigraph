# Stress report: remote optimize (PR #1275 engine + PR #1282 served)

**Under test:** `feat/optimize-served` at `f6b2443c` (PR #1282), stacked on
`feat/optimize-engine` at `5667fddb` (PR #1275).
**Stress branch:** `stress/optimize-1282` on `https://github.com/ragnorc/omnigraph`
(this report, the harnesses, the evidence, and eight fix commits, each
separate and labeled).
**Box:** `nproc` 2, 64 GiB, gVisor, local disk. S3 backend: RustFS
1.0.0-beta.12 (the release CI pins; official binary, checksum matched the
release `SHA256SUMS`) on 127.0.0.1:9000. Azure backend: Azurite 3.35.0
(npm, the version CI pins as a container) on 127.0.0.1:10000.
**Started** 2026-11-09 00:32 UTC.

Every number below comes from a file under `stress/evidence/`, produced by a
harness in `stress/`. `FINDINGS-DRAFT.md` holds the raw running notes.

---

## Verdict

**I would not merge #1275 as it stands; #1282 is close but should take the
should-fix items below first.** The engine's table pipeline exits early
whenever the turns it drains stage nothing (no-op tables) while a rewrite is
in flight. The tables it never planned are reported
`deferred(gate_hold_limit)` though no bound was reached, and `resume_at` is
empty, so the served path *clears* the graph's resume position. At the
default served settings on a 1,030-table catalog, each run optimized one
table and falsely deferred ~1,000. With planning concurrency 1 a served graph
never gets past a leading no-op table. This defeats the RFC's central promise
(bounded runs that cover a large catalog over successive runs) and
misreports every affected table. The fix is three lines and is on the stress
branch with a regression test (red before, green after).

Everything else held up well on local, S3 (RustFS) and Azure (Azurite)
roots under ~8,000 recorded operations, ~4,700 embedded runs, 80 crash and
kill points, 120 random kill rounds and 115 chaos rounds, lease loss, late manifest and record
puts, deployments and deletions, and one-hour soaks: no acknowledged write
was lost, no run published twice, no failed/cancelled/interrupted run
published anything, reads never blocked, and each run's gate hold stayed
within the RFC's bound on every backend. The should-fix issues are liveness
and contract gaps, and six of the seven have fixes on the stress branch:
one lost-and-late record write on S3 (or one failed write on a local root)
disables recorded operations until a restart that then waits out the lease;
SIGTERM during the adjacency-artifact scan waits out the whole grace and
exits 2; a deployment can miss cancelling a run admitted at its cancel
instant; the CLI exits 2 instead of 1 on a refusal the server marks
`effects: none`; the full-list 409 does not say which graph needs a write.
The seventh is documented, not fixed: on object storage a client that loops
optimize makes one write wait through many runs (14 s across 9 runs on S3,
each run within its own bound; 6x fewer writes), contrary to the docs'
per-writer bound.

---

## Findings by severity

| # | Severity | Status | Area | One line |
|---|---|---|---|---|
| B1 | **blocking** | verified, fixed on branch | engine #1275 | Pipeline ends with tables never planned; false `deferred(gate_hold_limit)`, no `resume_at`, served position cleared |
| S1 | should-fix | verified (S3/RustFS), fixed on branch | storage/#1282 | A record put whose answer is lost and that lands late wedges recorded operations until restart; restart waits a lease and a half |
| S2 | should-fix | verified (local), partly fixed on branch | storage/server #1282 | One failed record write on a local root: submissions 503 until restart, lookups 503, SIGTERM keeps the lock; a finished no-op run becomes `interrupted` |
| S3 | should-fix | verified, fixed on branch | server #1282 | Deployment cancels live runs before closing admission; a run admitted in between runs uncancelled and the drain waits for all of it |
| S4 | should-fix | verified, fixed on branch | engine #1275 | Adjacency-artifact refresh ignores cancellation: SIGTERM during its scan uses the full grace, exits 2, record left `publishing` |
| S5 | should-fix | verified, fixed on branch | CLI #1282 | A 5xx refusal with `effects: none` (routes off, draining) exits 2 and says the run "may still run"; docs say exit 1 |
| S6 | should-fix | verified, fixed on branch | server #1282 | The 409 for four unresolved publications names no operation, graph or walking state (RFC §2.4 says it reports each record's walk) |
| S7 | should-fix | verified (S3), not fixed | server/engine #1282 | Back-to-back runs: one write waits through many runs' gate holds (14 s across 9 runs on S3, per-run bound ≤ 3.9 s); writes drop 6x; the docs' "a writer waits at most the bound plus one rewrite" fails |
| N1–N15 | note | see below | | ordering, record content, status codes, evidence gaps, pre-existing issues |

### B1 (blocking): the table pipeline exits before planning the rest of the catalog

- **Where:** `crates/omnigraph/src/db/omnigraph/optimize.rs:1262` (PR head):
  the loop ends when `planning`, `ready` and `staging` are all empty. The fill
  of `planning` at the top of the loop runs *before* the turns drain `ready`.
  When the drained turns stage nothing (an `unchanged` plan, a
  `resource_limit` deferral, a planning failure) and no rewrite is in flight,
  all three queues are empty while tables are still unplanned, and the loop
  `break`s. Unvisited tables then get the default
  `DatasetOptimizeStats::deferred(key, DeferReason::GateHoldLimit)` with
  `resume_at = None` (`optimize.rs:1314`, `None => (None, GateHoldLimit)`).
  The comment above the check ("Filling planning above leaves it non-empty
  while any table is unplanned") is false.
- **When it bites:** whenever a rewrite outlasts the planning of the next
  `planning` tables and those are no-ops — the normal shape of a large,
  mostly compacted catalog, the RFC's target workload. With planning
  concurrency 1 it bites at the first no-op table even without a rewrite.
- **Served consequence:** `OperationRecord::keep_kind_state`
  (`crates/omnigraph-cluster/src/operations.rs:772`) clears the graph's
  resume position on a `succeeded` run with no `resume_at`, so the next run
  restarts at the first key and pays the no-op prefix again.
- **Violates:** RFC §1.3 ("Once either is reached, that table and every later
  one are reported deferred … A run that stops on neither bound clears the
  position … Over a sequence of runs, every table is planned, including the
  tail"), §1.5 (each table gets exactly one *true* outcome), and the record's
  meaning of `gate_hold_limit`.
- **Evidence:**
  - Deterministic engine repro, 12 tables, first table's rewrite parked 500 ms
    with `optimize.before_compact`, **no gate hold set**: planning 1 → 10
    tables `deferred`; 2 → 9; 4 → 7; 8 → 3; `resume_at` `None` every time
    (`engine_optimize_stress.rs::stress_repro_pipeline_exits_before_planning_the_tail`).
  - Embedded campaign: 35 of ~400 full-catalog runs in 20 s (planning 1–3)
    reported tail deferrals with gate holds of 2–66 ms against limits of
    17–258 ms (`evidence/c1_engine_resume_violations_prhead.txt`).
  - Served, PR-head binaries, `OMNIGRAPH_OPTIMIZE_PLANNING_CONCURRENCY=1`:
    every run visits only `edge:Authored` (a no-op) and reports the other five
    tables `deferred(gate_hold_limit)` after 1–2 ms of a 60 s hold; no
    position is ever stored; nothing is ever compacted
    (`evidence/c1_served_early_exit_prhead.txt`; fixed build:
    `…_fixedbin.txt`, resumes and compacts).
  - Served, PR-head binaries, **default settings** (planning 8, 60 s hold),
    1,030-table catalog with 20 seeded tables: each of 8 runs optimized exactly
    one table and reported 651–1,015 tables `deferred(gate_hold_limit)` with
    98–375 ms of gate hold; `resume_at` empty each time; 8 of 20 tables
    compacted after 8 runs. Fixed build: one run, 20 of 20, 656 ms
    (`evidence/c5_big_catalog_prhead.json`, `…_fixed.json`).
  - Direct `omnigraph optimize` (PR-head CLI, no `--max-gate-hold`), same
    catalog: with `OMNIGRAPH_MAINTENANCE_CONCURRENCY=1` it optimized 1 table
    and reported 1,028 `deferred(gate_hold_limit)`; at the default 8 (8
    rewrites at once) it optimized all 20, as did the fixed build at both
    settings (`evidence/c1_direct_cli_concurrency.txt`). The maintenance guide
    suggests lowering that variable to bound Blob memory, which is exactly the
    setting that triggers it on the direct path. The served path rewrites one
    table at a time, so it is exposed at every planning concurrency.
- **Fix (commit `23e1d66f`):** keep `unplanned` peekable and `continue` the
  loop when it is empty but `stop` is `None` and tables remain. Regression
  test in its owner, `failpoints.rs::optimize_plans_the_whole_catalog_when_no_op_turns_drain_beside_a_rewrite`,
  red before (planning 1, 2, 8) and green after; engine optimize owners
  (`maintenance::`, optimize failpoints, `detached_commit_matrix`): 108 passed.
  I would also make the `None` arm unreachable for unvisited tables (a
  `debug_assert!`), so a future regression cannot mislabel tables silently.
  With the fix, the RFC's coverage promise holds on the same catalog: with a
  100 ms gate hold each run plans 133–218 tables and stores the next
  position, the position advances and wraps, and 7 runs compact all 20 seeded
  tables (`evidence/c5_coverage_fixed.json`).
- **Pre-existing?** No: the pipeline is new in #1275.

### S1 (should-fix): a late-landing record put wedges recorded operations on S3

- **Where:** the process's storage scope keeps
  `__cluster/operations.json` unsettled after a conditional put whose answer
  was lost and whose re-issues and read-back saw the old version
  (`crates/omnigraph-storage/src/scoped.rs:152`). When the lost copy lands
  later, every following write of the record carries a different
  precondition and the scope refuses it for the rest of the process. The
  cluster store's reads (`ClusterStore::read_operations`,
  `crates/omnigraph-cluster/src/store.rs:1140`, via
  `read_text_versioned_if_exists_bounded`,
  `crates/omnigraph-storage/src/lib.rs:2372`) never tell the scope what they
  observed, although `scoped.rs` documents that "the owner's read of the key
  settles it once the precondition no longer holds there".
- **Reproduction:** `stress/c5_s3_lost_answer.py OUT late:0 late:1 late:2 late:3`
  with `ARM_SECONDS=8`: `s3proxy.py` resets the client on each copy of one
  record PUT and forwards it 4 s later; RustFS accepts the first copy (200),
  the rest fail (412).
- **Evidence (`evidence/c5_s3_lost_answer.txt`):** for the acceptance,
  `running`, `publishing` and final writes alike: every new submission 503
  for 20 s and until restart ("a prior request … has an unknown outcome and
  could still land"); lookups 503 in the acceptance, `running` and
  `publishing` cases (they must write to settle the orphan); SIGTERM exits 1
  and keeps the S3 lock; the next server starts only after 45.5 s (a lease
  and a half). After that restart everything resolves correctly
  (`interrupted`, `succeeded`, `failed`).
- **Violates:** RFC §2.3 "Acceptance in doubt … If the record lands late, it
  shows accepted with no live run, and the next admission or lookup resolves
  it to interrupted" and "Terminal in doubt … Admission refuses until a
  re-read settles it, and lookups read whatever landed"; the user docs'
  promise is weaker ("holds until the server restarts"), but restart then
  costs the lease wait.
- **Fix (commit `fa5cd0ff`):** the bounded versioned read settles an
  unsettled *conditional* request its observed generation proves unable to
  apply (if-match on another ETag or a gone object; create over an object).
  Unconditional requests stay unsettled. Unit test
  `scoped::tests::a_read_that_observes_another_generation_settles_the_key`;
  storage+cluster+server suites 855 passed; the repository's S3 suites
  against RustFS 16 passed. After the fix the same four scenarios resolve as
  the RFC says, submissions are accepted at once, SIGTERM exits 0 and restart
  is immediate.
  With the fix, `docs/user/operations/recorded-operations.md`'s "a refusal
  that names an earlier request with an unknown outcome holds until the
  server restarts" holds only for local roots (S2) and should say so.
- **Backends:** S3 only. The same four cases on Azurite with the PR-head
  binaries resolved without a wedge although a late copy landed (201) each
  time (`evidence/c5_az_lost_answer_prhead.jsonl`); S3's 409 for a
  conditional put racing an in-flight copy, which the storage layer records
  as unresolved, is the likely difference (not traced).
- **Pre-existing?** The scope rule is from #1176 (on main); its effect on the
  operation record is new. The same rule likely affects `state.json` writes of
  served deployments on main (not tested).

### S2 (should-fix): one failed record write on a local root

- **Where:** the local emulation of `write_text_if_match` is an
  unconditional put (`crates/omnigraph-storage/src/lib.rs:2476`); a failed
  put leaves the key unsettled with no precondition (`scoped.rs:152`,
  `Some(None) => true`), so the scope refuses every later write of the record
  for the life of the process. Lookups write the record when they settle an
  orphan or re-apply a known ending (`find_authorized` → `update`,
  `crates/omnigraph-server/src/recorded_operations.rs:1177`), so they fail too.
- **Reproduction:** `stress/c5_record_write_faults.py OUT final_refused
  running_refused publishing_refused noop_final_refused` (an `LD_PRELOAD`
  shim fails one rename onto `operations.json` with EIO; nothing applied).
- **Evidence (`evidence/c5b_record_write_faults.json`,
  `c5a_final_refused_log.txt`):** lookups of the operation 503 and every new
  submission 503 until restart; SIGTERM exits 1 and keeps the lock, so the
  restart needs `cluster force-unlock`; a **no-op run that finished
  `succeeded` and whose final write failed is stored `interrupted`** after
  the restart; when the failed write was `running`, the record stays
  `accepted` with no live run until the restart (lookups say
  `in_progress: false`). The graph itself is unaffected and nothing published
  wrongly.
- **Violates:** RFC §2.3 (as S1); the invariants "no record stays `accepted`
  or `running` with no live run" and "no finished run is stored
  interrupted". (The RFC's own text allows `interrupted` for a record
  left `running` with no live run, so that part is a contract gap more than a
  code bug.)
- **Fix (partial, commit `08d2a0e7`):** a lookup whose record write is
  refused or in doubt answers what landed, preferring the ending the process
  holds; lookups now return 200 with the known outcome during and after the
  fault. Still open: submissions stay refused until restart on a local root,
  and the in-memory ending is lost on restart. A proper fix makes the local
  emulation register its precondition (so a re-read can settle it, as in S1)
  or treats a failed local rename as definitely not applied.
- **Pre-existing?** The scope behavior is on main; its effect on the record is
  new.

### S3 (should-fix): a deployment can miss cancelling a run admitted at its cancel instant

- **Where:** `crates/omnigraph-server/src/deployment.rs:566` cancels live
  recorded runs under the record gate, then prepares and closes the affected
  graphs' admission (`:581`). A submission that already holds its graph
  request (past the middleware) and takes the record gate after the cancel is
  accepted and starts; nothing cancels it, and the drain waits for the whole
  run. A request in its pre-gate window (`walk_unresolved`'s record read, or
  queued on the record gate behind the deployment's cancel) is enough.
- **Reproduction:** `stress/c4_deploy.py OUT race`: the run's record reads are
  delayed 1.5 s with the shim so the submission straddles the deployment.
- **Evidence (`evidence/c4_deploy_results.txt`):** the run ended `succeeded`
  with no deployment reason; the deployment took 12.06 s versus 4.62 s when
  the run was cancelled (`affects_held`). With the 300 s gate-hold ceiling a
  run can outlast the 300 s `DRAIN_TIMEOUT` and make the deployment fail.
- **Violates:** RFC §3.4 "A deployment that affects the graph requests
  cancellation before its drain. Maintenance then holds a configuration
  change for at most one table's rewrite, the publication, the configuration
  clear and an issued artifact write. The record's terminal reason names the
  deployment."
- **Fix (commit `8d02fc6e`):** cancel after `close()`, and have the owned
  operation accept nothing (503, "closing for a deployment; nothing was
  accepted") when its graph is no longer `Ready` when it takes the record
  gate. After: the late submission is refused and the deployment takes 3.3 s;
  `affects_held`, `unaffected_held`, `delete_running` unchanged; server suite
  574 passed. No in-tree regression test (the race needs the shim).
- **Pre-existing?** No.

### S4 (should-fix): the artifact refresh ignores cancellation

- **Where:** `persist_graph_index_artifact` (`optimize.rs:1364`) takes no
  cancellation; it is skipped only if a cancellation was requested *before*
  it starts (`:1084`). Its size pre-check (opens and counts every edge
  table), the scan in `GraphIndex::build_counted` (`:1430`) and the write
  (`:1455`) all run to the end after a shutdown or deployment cancels the run.
- **Reproduction:** `stress/c3_sigterm_artifact.py OUT /tmp/prhead 5000`: a
  node-only optimize builds the missing artifact; every edge data-file open is
  delayed 5 s; SIGTERM 1 s into the scan.
- **Evidence:** PR head: 5 more edge opens after SIGTERM, the server waited
  the whole 25 s grace, exited **2**, kept the lock and left the record
  `publishing` (`evidence/c3_sigterm_artifact_prhead.json`).
- **Violates:** RFC §3.4 "SIGTERM requests cancellation … An artifact scan in
  progress is abandoned … The write is issued only if no shutdown is pending
  at that moment … The server then writes the terminal record and releases as
  today", and §1.8 ("The full edge scan starts only if no cancellation,
  shutdown or deployment drain is pending. The scan reads only, so it may be
  abandoned."). The same gap makes a deployment drain wait for the scan.
- **Fix (commit `331d8485`):** split the refresh into a read-only preparation
  raced against the run's cancellation and a write issued only when none is
  pending (still awaited once issued); a cancellation arriving in the tail is
  reported as arriving late. After: stop in 4 s, exit 0, record `succeeded`,
  artifact `skipped(cancelled)` (`evidence/c3_sigterm_artifact_fixed.json`);
  optimize owners + `operation_routes` 111 passed. Regression test (commit
  `24614965`): a new seam `optimize.artifact_scan` and
  `failpoints.rs::optimize_cancelled_during_the_artifact_scan_writes_no_artifact`,
  red on the pre-fix refresh (`Refreshed`), green after.
- **Pre-existing?** The artifact write is old; the cancellation contract is
  new in #1275.

### S5 (should-fix): the CLI exits 2 on a refusal the server marks `effects: none`

- **Where:** `crates/omnigraph-cli/src/commands/maintenance/served.rs:65`
  maps every status ≥ 500 to "unanswered" without reading the disposition.
- **Evidence:** with the routes disabled the server answers 503
  `{"effects":"none"}`; the CLI polls five lookups, prints "the operation may
  still run" and exits 2 (`evidence/c6_cli_results.json`, case
  `routes_disabled`). The docs' table: refused before acceptance → 1.
- **Fix (commit `de11e65d`):** a ≥500 refusal with `effects: none` is a
  refusal before acceptance. Unit test
  `served::tests::a_server_error_that_took_no_effect_is_a_refusal_before_acceptance`
  (red before); end to end the case exits 1.

### S6 (should-fix): the full-unresolved-list 409 says nothing actionable

- **Where:** `recorded_operations.rs:759`: "four publications are unresolved;
  a write on their settlement branches settles them".
- **Evidence:** `evidence/c2_unresolved_cap.json` (four doubtful runs on `kg`
  with main unchanged): the 409 names no operation id, no graph, no walking
  state. Behavior is otherwise exactly as specified: the fifth submission is
  refused on every graph, a write on `other` settles none, a write on `kg`
  settles all four `failed / not_committed` and admission reopens.
- **Violates:** RFC §2.4 "A lookup of such a record, and the 409 a full list
  answers, report `resolution: walking` for each such record with its cursor
  and how many commits its walk has covered". The operator cannot tell which
  graph needs the settling write without reading `operations.json`.
- **Fix (commit `5a1bbeec`):** the refusal lists each unresolved record with
  its graph, settlement branch and walk state; server suite 574 passed.

### S7 (should-fix): back-to-back runs compound the writer wait on object storage

- **What:** each run keeps its own gate hold within the RFC §1.2 bound, but a
  client that submits the next optimize as soon as the previous one ends
  (what a loop over `omnigraph optimize` does) makes a single write on main
  wait through many runs. The user docs promise per writer:
  "a writer waits at most the bound plus one rewrite"
  (`docs/user/operations/maintenance.md:84`).
- **Reproduction:** `stress/c1_writer_wait.py OUT 180 2` with
  `STRESS_S3_PREFIX=s3://…` (fixed build, RustFS): phases control /
  back-to-back optimize (`max_gate_hold_ms` 1000) / control, two writers of
  five-row Person/Company loads. Each run's gate-hold interval is placed at
  `accepted_at + queued_ms` for `gate_hold_ms`; for every write it counts the
  runs whose holds overlap it.
- **Evidence (`evidence/c1_writer_wait/`):** S3, 180 s phases: control 775
  writes, p99 0.58 s; optimize 121 writes (120 runs, gates held 50% of the
  time, longest hold 3.9 s), p50 1.65 s, p99 12.8 s, max 14.0 s; control 377
  writes, p99 0.77 s. The slowest write overlapped 9 runs' holds with 6.8 s
  of held time inside its 14 s; 100 of 121 writes overlapped two or more
  runs. A 90 s repeat: p99 0.34 / 10.2 / 2.3 s, slowest write 10.2 s across
  11 runs. Local root, same harness: no effect (p99 0.70 s with optimize
  versus 0.77 / 0.82 s; 1,293 runs, gates held 51%). Under the campaign 2
  mix on S3 (20 min each, `evidence/c2_s3_20min_{opt,ctl}.json`), node-load
  p99 rose from 3.6–6.1 s to 13.7–24.8 s and total writes fell from 1,665
  to 989; the 60-minute S3 soak had write p99 37.7 s, the 30-minute Azure
  soak 59 s (edge loads are slow without optimize too: p99 66 s in the
  control, pre-existing).
- **Violates:** with two or more concurrent writers, the per-writer sentence in `maintenance.md:84`; the RFC's
  observable contract ("The request bounds that wait (§1.2)") holds only per
  run. Nothing limits how soon the next run may take the gates.
- **Cause (measured with one writer, inferred for two):** a load prepares
  outside the gates, waits for the run, then finds main moved by the run's
  publication and re-prepares (`crates/omnigraph/src/loader/mod.rs:760`, up
  to 32 times). With **one** writer (`c1_writer_wait.py OUT 90 1`,
  `result_s3_one_writer.json`) every write in the optimize phase re-prepared
  exactly once (84 re-prepares for 85 writes, none in the controls) and the
  slowest write took 4.4 s, one run's hold plus a preparation: the
  per-writer bound holds, but the writer moves in lock-step with the runs
  (85 writes beside 84 runs in 90 s, against 535 writes in the control).
  With **two** writers the second write's re-preparation is invalidated
  again by the first write's commit (the pre-existing writer-writer
  conflict, N12: ~0.94 re-prepares per write and some writes exhausting all
  32 attempts with no optimize at all); by then the next back-to-back run
  holds the gates, so the wait compounds (~2.9 re-prepares per write with
  optimize, `reprepares_s3b.txt`). An optimize publication changes no
  logical rows, but it is a read-set change for every write in flight.
- **Proposed fix:** state the bound per run in the docs and RFC and say
  that successive runs add up; in the server, space recorded runs per graph
  (for example, admit the next optimize only after writers queued during the
  previous run have taken the gates, or after a pause no shorter than the
  last gate hold). Letting a write accept a head moved only by a
  compaction commit would remove the re-prepare, but it touches the
  single-authority invariant and needs its own RFC.
- **Pre-existing?** The gates and re-prepare loop are on main; a client
  could not loop optimize through the serving process before #1282.

### Notes

- **N1 Admission order.** The ceiling check (`validate_new`,
  `recorded_operations.rs:736`) runs before the eviction-mark check (`admit`,
  `:742`); an expired id with an over-ceiling hold answers 400, not 409
  `operation_id_expired` (RFC §3.2 lists the mark at step 2, the ceiling at
  step 6). Seen 32 times in campaign 2; harmless.
- **N2 Record content.** A terminal record written from the report
  (`terminal_from_report`, `:1125`) omits the `nonce` the `publishing` write
  stored (only records settled by the walk keep it), and the run's span
  (`:792`) carries the operation id, kind and graph but not the nonce or the
  commit (RFC §2.2 per-operation fields, §3.5 span). No server log line names
  a run's nonce or published commit, so logs cannot be joined to the record
  by commit. **Fixed on the branch (`53107bc9`):** the span records `nonce`
  and `commit` when known, one `recorded operation ended` event carries the
  state and timings, and the terminal record keeps the nonce;
  `operation_routes::recorded_optimize_keeps_a_record_a_caller_reads_by_id`
  now asserts the nonce and that the published commit ends with it (red
  before).
- **N3 First lookup after restart.** An active `publishing` record left by a
  killed process is moved to the unresolved list by the first access but
  walked only on the next one (walks are computed before the orphan move), so
  the first lookup answers `publishing` even when history already answers.
- **N4 Dispositions.** A refused acceptance answers 503 with
  `effects: unknown, action: reconcile` while its message says "nothing was
  accepted".
- **N5 Damaged record.** Invalid, truncated, empty or wrong-version
  `operations.json` answers 409 `[operation_record_invalid]` on every route
  (undocumented for GET/cancel); the file is never overwritten
  (`evidence/c5_record_bounds.json`). A 503 would describe a server-side
  storage problem better.
- **N6 Requests that can only fail.** `tables: []` and duplicate table names
  are accepted (202) and then fail in the engine; they could be 400 at
  admission.
- **N7 Deleted graph.** After a deletion the record keeps the operation
  (`unsettled`), but the graph-scoped lookup route answers 404, so it can only
  be read from the file or after re-adding the graph id.
- **N8 Late settlement and the resume position (S3, partly reproduced).**
  `settle(Committed)` calls `keep_kind_state`
  (`crates/omnigraph-cluster/src/operations.rs:691`, body at `:772`). The
  record a late settlement completes is the `publishing` record, which keeps
  the tables but no `resume_at` (only the live path's report sets it), so the
  settlement does not restore an old position: it *clears* the graph's
  position (`kind_state.optimize.remove`). Reproduced on RustFS
  (`stress/c2_stale_resume_s3.py`, evidence
  `stress/evidence/c2_stale_resume_s3.json`): run A optimized `edge:Authored`
  and deferred the rest (`gate_hold_limit`), its manifest PUT landed after the
  owner fenced; the successor settled it `succeeded` with `resume_at: null`,
  so A's own position (the next table) was lost. When a newer run B has left a
  position before A settles, the settlement erases B's position too
  (follows from the code; the rerun that bounds B is in the evidence if it
  completed, see below). Effect: the next full-catalog run starts from the
  first table. The position is a hint, so nothing is lost but rotation;
  repeated in-doubt runs on a large catalog would keep restarting the
  rotation. Fix: store `resume_at` in the `publishing` record (it is known
  when the run publishes) or skip `keep_kind_state` for a settlement whose
  run is older than the one that set the current position.
- **N9 Future-dated ids.** An id up to 60 s ahead is accepted; once evicted it
  moves the mark ahead of honest ids for up to that lead (documented).
- **N10 Evidence gaps.** Most rows of the RFC's evidence table have no test on
  this branch: there is no record-write fault injection, no restart or
  deletion journey in `boot_settings` or cluster `failpoints.rs`, no
  deployment-race test; `operation_routes` has two journeys. The DST golden
  does have `Optimize` rows.
- **N11 Pre-existing, out of scope.** A historical query at a commit whose
  table versions `cleanup` reclaimed answers 500 `internal` ("historical
  published dataset version … was reclaimed"); the typed error exists but the
  query route maps it to 500. Not in the PR diff (checked by `git diff`, not
  on a main build).
- **N12 Pre-existing writer conflicts.** Under concurrent writers, mutations
  and edge loads are refused "write authority 'graph_head:main' changed
  during preparation" at the same rate with or without optimize (control runs
  in campaign 1).
- **N13 Exports during a served run.** While a recorded optimize holds its
  export exclusion (entry to return, including the artifact scan), an export
  is refused 413 "resource limit exceeded for stream_export_slots: actual 2,
  limit 1". The exclusion is in the RFC; the 413 slot wording does not tell
  the client that maintenance holds it or when to retry, and with a 60 s
  default gate hold the window is long. Also, on a server target
  `--operation` silently ignores `--tables`/`--max-gate-hold`.
- **N14 Lookup cost with unresolved records.** Every lookup and admission
  walks every unresolved record from a fresh read of its branch; with four
  unresolved (head unchanged) a lookup on loopback RustFS took 201 ms median
  versus 5 ms with none (`evidence/c2_unresolved_cap_s3_cost.json`). On real
  S3 and with the CLI polling every 500 ms per waiting client, this is the
  first place request cost will show.
- **N15 Record size costs under the gates.** With a 1,030-table catalog each
  terminal record is 100–200 KB, so the record fills to ~3.5 MB. Every access
  decodes, validates (serializing each operation and the terminal list),
  changes and re-encodes it: on this box (debug builds) a lookup took 602 ms
  versus 27 ms with one operation, an acceptance 922 ms, and a publishing
  run's gate hold grew from 390 ms to 1,089 ms because the before-publish
  write happens under the writer gates (`evidence/c5_record_size_cost.txt`).
  Release builds will be faster, but the cost scales with catalog size × 32
  and sits inside the writer-wait bound. Options: keep per-table outcomes
  out of the retained terminal list (or in their own object), and compute
  sizes once per write (`push_terminal` and `validate` re-serialize the
  terminal list repeatedly, `operations.rs:796`, `:809`, `:814`).

---

## What held (verified)

- **Crash points (campaign 3).** 15 points each on local and S3 roots: SIGKILL
  at the authority capture, inside a table rewrite, after a table effect,
  before the manifest commit, inside the publication (before and after the
  manifest commit), at the configuration clear; SIGTERM inside a rewrite and
  after the manifest commit; panics; injected failures; publications with
  unknown outcome. Every record resolved as the RFC says (`interrupted` before
  `publishing`; `succeeded` or `failed / not_committed` from history after
  it); no lost rows; the next run succeeded
  (`evidence/c3_crash_results.json`, `c3_crash_results_s3.json`).
- **Random seam faults and publish-path failures.** 30 rounds with 58
  random fail-once seams (optimize, publication, publisher, mutation) armed
  under load, plus kills: zero violations. Failing each publish step once:
  after the manifest commit the run ends `succeeded` naming its commit; before
  it the record stays `publishing` until main moves, then `failed /
  not_committed` (`evidence/c3_random_seam_faults.json`,
  `c3_publish_path_failures.txt`).
- **Random kills.** 30 rounds local (11,347 acknowledged rows) and 20 rounds on
  S3 of random SIGKILL/SIGTERM under writers and submitters: no violation
  (`evidence/c3_random_kills_*.json`).
- **Azure Blob (Azurite 3.35.0, fixed build).** Every writer under
  `omnigraph-azure-admission` (`--mode job` for bootstrap and unlock through
  the served child's `stress_cluster_admin`, since the CLI reaches `az://`
  only through a registered target; `--mode server` for every server). A
  clean SIGTERM releases the Blob lease and keeps the cluster lock (as
  documented); SIGKILL strands both; every restart broke the lease (only
  after the harness saw the old process exit) and force-unlocked. Random
  kills: 40 rounds, 41 recoveries, 29 broken leases, 2,486 acknowledged
  rows, zero violations (`evidence/c3_random_kills_azure.json`). Chaos: 30
  rounds, 10 deployments through the server, 142 operations, 1,743
  acknowledged rows, zero violations; deployment-cancelled runs name the
  deployment (`evidence/c7_chaos_azure.log`). Lost and late record writes
  through the fault proxy (`c5_s3_lost_answer.py` with `STRESS_AZ_PREFIX`):
  acceptance, `running`, `publishing` and final writes all resolved, fresh
  submissions were accepted at once, SIGTERM exited 0
  (`evidence/c5_az_lost_answer_*.jsonl`). The same four late-landing cases
  with `ARM_SECONDS=8` on the **PR head** also resolved (a late copy landed
  201 each time): S1 does not reproduce on Azurite (see S1).
- **Chaos (campaign 7, PR head).** 60 rounds mixing writers, submitters,
  cancels, 18 schema-changing deployments through the server during load, and
  42 random SIGKILL/SIGTERM restarts: zero violations over 1,211 operations
  and 19,147 acknowledged rows; runs cancelled by a deployment name it
  (`evidence/c7_chaos_60_prhead.json`). On S3: 25 rounds, 8 deployments, 17
  kills, zero violations (`evidence/c7_chaos_s3_prhead.json`).
- **Lease loss and the previous owner's late manifest put (S3).** Freezing
  RustFS with a 4 s lease: the owner fenced and exited 2 within ~5.5 s, the
  successor started after ~6.5 s and resolved the record. With beats dropped
  and the manifest PUT delayed: the late put landed → `succeeded` naming
  exactly that commit; the successor wrote first → the late put conflicted →
  `failed / not_committed`, no commit answers the nonce
  (`evidence/c3_lease_loss_*.json`, `c3_late_manifest_*.json`). The old
  owner's final record put delayed past the takeover did not overwrite the
  successor's settled record (`evidence/c3_late_record_s3.json`).
- **Deep resolution walk (S3).** The late commit 300 commits deep (released
  from main's buffer), walk limit 16, the branch growing by 10 mid-walk:
  resolved in exactly ⌈300/16⌉ + 1 = 20 lookups (the extra one is N3),
  `visited` advancing 16 per page, `succeeded` naming the late commit
  (`evidence/c3_deep_walk_s3.json`).
- **Logical equivalence (embedded).** After three published optimizes over a
  history with updates, deletes and a branch, every row of every table
  (all non-Blob columns and `_rowid`) and every Doc's Blob bytes are equal on
  main, the branch and five historical commits
  (`engine_optimize_stress.rs::stress_optimize_preserves_logical_state`). The
  RFC lists this as an `integration/maintenance.rs` test; none exists there.
- **Budget, ceilings and resume (served, fixed build).** The first table with
  work always runs; a table past the remaining budget is
  `deferred(byte_budget)` with every later table and becomes `resume_at`; the
  next run starts there; a `resource_limit` deferral neither stops the run
  nor moves the position; coverage completed in three runs
  (`evidence/c5_budget_fixed.json`).
- **Settlement by merges.** Fast-forward and three-way merges into main write
  a new main commit; a doubtful record settles `failed / not_committed`
  (`evidence/c3_settle_by_merge.json`).
- **Served hammer (campaign 2).** Local, 10 min: 1,379 accepted operations,
  2,767 writes, ~1,336 evictions, eviction mark monotone, record bounds held,
  evicted ids refused `operation_id_expired` and never re-run, repeats answer
  the record, mismatches 409, all acknowledged rows present. S3, 10 min: 547
  operations, 515 evictions, same result
  (`evidence/c2_run1_summary.json`, `c2_s3_run_summary.json`).
- **Soak and cleanup (fixed build).** 30 minutes of campaign 2 on a local
  root: 4,183 operations, 7,209 writes, 4,151 evictions, zero violations,
  26,142 acknowledged Person rows all present. Then `cleanup --keep 5` on that
  root: main, the live branches and every retained commit read
  (`evidence/c2_soak_30min_fixed.json`, `c8_cleanup_retained.json`).
  On S3: 60 minutes, 1,880 operations, 2,078 writes, 1,848 evictions seen,
  zero violations (`evidence/c2_soak_60min_s3_fixed.json`). On Azure: 30
  minutes, 394 operations, 671 writes, zero violations
  (`evidence/c2_soak_30min_azure_fixed.json`). The only flagged lines in both
  are the hammer's deliberate over-ceiling probe (400, as specified).
- **Engine beside writers (campaign 1, fixed build).** Two runs: 2,683 runs
  (1,340 published) beside 3,905 writes with 4-minute phases (its only
  violations were harness artifacts, since fixed), and 1,205 runs (610
  published) beside 1,772 writes with 90-second phases and zero violations
  (`evidence/c1_engine_summary_fixed.txt`). Random bounds, ceilings, budgets,
  cancellation and both authority modes, plus schema applies: every run
  visited each table once in order, `resume_at` consistent, cancelled runs
  never published, `UnderGates` never refused (only `BeforeGates` runs were,
  for a moved head, as the RFC expects), every error carried
  `BeforeEffect`/`Unpublished`, acknowledged rows intact after `cleanup`. A
  third run also checked values: each writer updates only its own rows, and
  every row's last acknowledged age survived 609 published optimizes beside
  1,729 writes (`evidence/c1_engine_values_fixed.txt`).
- **Campaign 1 on S3 (fixed build).** The embedded campaign on a RustFS
  root (`STRESS_ROOT_URI`), 60-second phases: 690 runs (255 published)
  beside 468 writes, gate hold ≤ 0.52 s and at most 0.28 s over
  (`max_gate_hold` + longest table), zero violations, acknowledged rows
  intact after `cleanup` (`evidence/c1_engine_s3_fixed.txt`). Write p99
  there was 13–14 s against sub-second holds (S7).
- **Writer wait and reads.** Gate hold minus (`max_gate_hold` + longest table)
  ≤ 64–86 ms across thousands of runs (publication + clear); one unexplained
  1.8 s outlier under CPU contention did not recur. Writes with optimize had
  lower p99 than without (3.3 s vs 8.1 s locally: compaction helps writers).
  On S3, writer p99 is 12.2 s without optimize and 18.4 s with back-to-back
  optimizes (273 runs in 3 min); the max moved ~2 s, inside one gate hold.
  Per run the bound holds on S3 and Azure too: gate hold minus (bound +
  longest table) ≤ 0.9 s on S3 and 1.3 s on Azure over the soaks' last 32
  runs. Per write it does not under back-to-back runs (S7).
  Reads never blocked during a real optimize: p99 7 ms local / 79 ms S3 inside
  a run versus 12 / 82 ms outside (`evidence/c1_reads_during_optimize_*.json`,
  `c2_s3_control_*.json`).
- **SIGTERM during an issued artifact write.** A write already in flight at
  SIGTERM is awaited within the grace (8 s delay: exit 0, `refreshed`); one
  that outlasts the 25 s grace ends with exit 2, the lock kept and the record
  `publishing` for the next owner to settle, as RFC §3.4 says
  (`evidence/c3_sigterm_artifact_write_*.json`).
- **Deployments and deletion (campaign 4).** An affecting deployment cancels a
  running run (reason names it); an unaffected one does not; deleting a graph
  with an unresolved record, a running run, an active `publishing` orphan,
  after a failure past `Started` (offline reconcile), and with the root
  already absent on resume: every record ends `unsettled / graph_deleted`
  (`evidence/c4_*`).
- **Record faults and bounds (campaign 5).** Damaged and foreign records are
  refused and never overwritten; the route's ~1 MiB body limit (413) keeps a
  request within the 1 MiB per-operation bound; a 1,030-table catalog lists
  1,024 outcomes plus `more_tables`, ~200 KB per operation.
- **CLI (campaign 6).** 16 of 17 cases match the documented exits; the CLI
  never submitted twice under lost requests, lost or truncated responses,
  gateway 504s, unanswered lookups, timeouts and `--no-wait`
  (`evidence/c6_cli_results.json`).

## Baseline (PR head, before any change)

| Suite | Result |
|---|---|
| engine `maintenance::` + `failpoints` filtered to `optimize` | 78 passed |
| `detached_commit_matrix` with `OMNIGRAPH_MATRIX=full` | 29 passed, 1 skipped |
| server `operation_routes`, cluster `operations::tests`, CLI `served::tests`, `system_local::local_cli_optimize_runs_a_recorded_operation` | 20 passed |
| DST pinned suite (`crates/omnigraph-dst`, `cargo test --locked`), incl. `dst_bench_cost_count_golden` | 55 + 2 + 56 passed (33 ignored) |
| `cargo test -p omnigraph-gqt --locked` | 462 cases + 52 + 14 + 29 passed |

The known `telemetry`/`operations` flake did not appear under nextest.
Logs: `stress/baseline/`.

## Could not test

- **Live Azure**: Azurite (npm, 3.35.0) on loopback only; Azure lease loss,
  RBAC fencing and delayed accepted writes are outside what an emulator
  shows. No seam-armed or slow-proxy timing scenarios were run on Azure
  beyond the lost-answer cases.
- **Real S3 latency and in-region qualification**: RustFS on loopback only;
  the RFC's `max_gate_hold` default, ceilings, `artifact_write_limit` against
  the 25 s grace, and per-writer latency on a serving host remain unqualified.
- **S3 runs parked at a seam**: the seam `Hold` blocks a tokio worker; on S3
  that stalls the parked worker's HTTP connections and lease beats, so a
  server parked longer than the lease fences itself (exit 2) before the
  harness kills it. The S3 crash points therefore measure resolution after a
  fenced owner, which they did correctly; the S3 timing scenarios (lease
  loss, late manifest and record puts, lost answers) used the S3 proxy
  instead of seams.
- **CLI exit 4** (same id, different request): the CLI mints its own ids; the
  mapping is unit-tested only.
- **A second recorded kind or admission scope**: only `optimize` exists.
- **Origin/main comparison builds**: building main cold on 2 cores was not
  affordable; "pre-existing" judgements are by `git diff`.

## Fix commits on the stress branch (not part of the PRs)

| Commit | Fixes |
|---|---|
| `23e1d66f` | B1 engine pipeline early exit (+ regression test) |
| `8d02fc6e` | S3 deployment cancel race |
| `de11e65d` | S5 CLI exit on `effects: none` (+ unit test) |
| `08d2a0e7` | S2 (part) lookups answer what landed |
| `fa5cd0ff` | S1 storage settle-on-read (+ unit test) |
| `331d8485` | S4 artifact refresh honors cancellation |
| `5a1bbeec` | S6 full-list refusal names each record |
| `24614965` | S4 regression test (new seam `optimize.artifact_scan`) |
| `53107bc9` | N2 span fields and terminal nonce (+ assertion in `operation_routes`) |
| `3741a341`, `5d721853` | rustfmt and clippy over the fixes and harness |

Validation of the fix commits together (`5d721853`): `cargo dev-nextest`
(the canonical workspace graph) ran 5,200 tests: 5,185 passed. Of the 15
failures, 5 (`parity_matrix`) were killed by a concurrent stress harness and
pass in isolation; the other 10 (8 `omnigraph-bench` symlink and
machine-identity probes, `cli_cluster::managed_lifecycle_capture_and_retry_records_refuse_symlinks_without_reading_targets`,
`db::upgrade::tests::interrupted::unreadable_legacy_object_asks_for_a_rerun`)
fail identically on the PR-head sources on this box (gVisor, root) and are
environmental. `cargo dev-clippy` and `cargo fmt --all --check` are clean; the
DST cost golden and `forbidden_apis`/seam guards pass.

## How to reproduce

All harnesses drive the real `omnigraph` and `omnigraph-server` binaries
(`cargo dev-build-tests`) or, for exact crash points, a seam-armed copy of the
production server (`served_child.rs`, the `stress_served_child` test target of
`omnigraph-server`). PR-head binaries were kept in `/tmp/prhead` for the served
demonstrations; pass a binary directory where scripts take one, or set
`OG_BIN_DIR`. Python harnesses use only the standard library.

| File | What it is |
|---|---|
| `og.py`, `invariants.py` | HTTP client, cluster apply/unlock, server lifecycle, record reader (local or S3), invariant checks against main's first-parent history |
| `fixture*/` | `kg` (Person/Company/Doc with full-text, vector, Blob; Knows/WorksAt/Authored) and `other`; variants for deployments; a 1,030-table catalog |
| `served_child.rs` | Production `serve()` with engine and cluster seams armed at runtime (`hold`, `fail_once`, `fail_always`, `panic`) |
| `engine_optimize_stress.rs` | Campaign 1 and the B1 repro (`engine_optimize_stress` test target of `omnigraph-engine`; `STRESS_SECONDS`, `STRESS_OPTIMIZERS`) |
| `faultfs.c` | `LD_PRELOAD` shim: fail, delay or lose the answer of renames onto a path; delay opens of a path |
| `tlsproxy.py` | TLS fault proxy for the CLI's server target (drop/truncate/504/delay) |
| `s3proxy.py`, `s3util.py` | HTTP fault proxy between the server and RustFS (`drop_response`, `late_forward`, `drop_request`, `slow`, rule file); stdlib SigV4 client |
| `c2_served_hammer.py`, `c2_unresolved_cap.py`, `c2_stale_resume.py`, `c2_stale_resume_s3.py` | Campaign 2 |
| `c3_crash.py`, `c3_random_kills.py`, `c3_settle_by_merge.py`, `c3_sigterm_artifact.py`, `c3_lease_loss.py`, `c3_late_manifest.py`, `c3_late_record.py`, `c3_deep_walk.py`, `c3_sigterm_artifact_write.py` | Campaign 3 |
| `c4_deploy.py`, `c4_delete_orphan.py` | Campaign 4 |
| `c5_record_write_faults.py`, `c5_record_bounds.py`, `c5_big_catalog.py`, `c5_s3_lost_answer.py`, `c5_budget.py` | Campaign 5 |
| `c6_cli.py` | Campaign 6 |
| `c7_chaos.py`, `c8_cleanup_retained.py` | Mixed chaos rounds; cleanup retention check |
| `c1_served_early_exit.py`, `c1_reads_during_optimize.py` | Targeted scenarios |
| `azutil.py` | Stdlib SharedKey client for Azurite (public emulator key); with `STRESS_AZ_PREFIX=az://container/prefix`, `og.py` wraps every server and admin job in `omnigraph-azure-admission` and recovers the lease and lock between restarts (`og.prepare_restart`) |

This box's ephemeral port range is 16000–65535, so every server binds below
16000. For S3, run RustFS on 127.0.0.1:9000 with keys
`omnigraphci`/`omnigraphci-secret`, create the bucket with
`s3util.py mb omnigraph-stress`, and set
`STRESS_S3_PREFIX=s3://omnigraph-stress/<prefix>`.
