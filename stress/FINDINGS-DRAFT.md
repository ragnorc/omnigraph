# Working notes (raw; REPORT.md is the deliverable)

## F-record-wedge (verified, local root)
One failed rename of __cluster/operations.json (LD_PRELOAD EIO, nothing applied) ->
storage scope marks the key unsettled ("could still land") -> every later write refused
for the process's life. Lookups of the op that need a write (pending ending, orphan
interrupt) answer 503; all submissions 503; SIGTERM exits 1 and keeps the lock.
Evidence: evidence/c5b_record_write_faults.json, c5a_final_refused_log.txt
RFC 2.3: "Admission refuses until a re-read settles it, and lookups read whatever landed."

## F-noop-interrupted (verified)
No-op succeeded run whose final write fails -> after restart stored `interrupted`.

## F-deploy-cancel-race (suspected, code reading)
deployment.rs:566 cancels live runs before prepare_deployment_transition().close();
a submission holding a GraphRequest admitted after the cancel is never cancelled;
drain waits for the full run (DRAIN_TIMEOUT 300s, gate hold ceiling 300s).

## Notes
- ceiling check (validate_new) precedes eviction-mark check, unlike RFC 3.2 order.
- terminal record from a report drops `nonce` (terminal_from_report uses run-local copy).
- span lacks nonce/commit (RFC 3.5).
- first lookup after restart of an active publishing record answers `publishing` (walk runs before orphan move).
- refused acceptance answers 503 with disposition effects=unknown/action=reconcile while saying "nothing was accepted".
- RFC evidence table rows largely without tests (cluster failpoints, boot_settings, record-fault injection).

## C3 crash campaign: all 15 points resolve per RFC (evidence/c3_crash_results.json)
## C4 deploy: race reproduced (evidence/c4_deploy_results.txt): run admitted after the
   deployment's cancel step runs uncancelled; deployment drain 12.06s vs 4.62s.
## C2 10-min hammer: 0 real violations; 1379 accepted, ~1336 evicted; all acked rows present.
## Pre-existing: historical read of a reclaimed commit -> 500 internal (query route), not in PR diff.
## Harness: ephemeral port range 16000-65535 on this box; servers bind < 16000.

## F-pipeline-early-exit (VERIFIED, deterministic) -- engine #1275
optimize.rs run_table_pipeline: loop top fills `planning` before the stage loop drains
`ready`; if the drained items are all Stat (no-op/unchanged/resource deferral/plan failure)
while unplanned tables remain, planning/ready/staging are all empty -> `break`.
Unvisited tables get DatasetOptimizeStats::deferred(key, GateHoldLimit) (line ~1314 `None =>`),
resume_at None. Repro: stress/engine_optimize_stress.rs::stress_repro_pipeline_exits_before_planning_the_tail
(12 tables, T00 rewrite held 500ms via OPTIMIZE_BEFORE_COMPACT, no gate hold):
planning 1 -> 10 deferred; 2 -> 9; 4 -> 7; 8 -> 3 deferred; resume_at None in all.
Served: keep_kind_state clears the resume position on succeeded+resume_at None.
Served demo (evidence/c1_served_early_exit_{prhead,fixedbin}.txt): PR head with
OMNIGRAPH_OPTIMIZE_PLANNING_CONCURRENCY=1 never gets past a leading no-op table: every run
reports the rest deferred(gate_hold_limit) with gate_hold 2ms of 60s and stores no position.
Fixed build resumes and compacts. Planning 8 (default) also loses the tail when a rewrite
outlasts 8 no-op plans (engine repro).

## Verified OK
- settle by FF/3-way merge (evidence/c3_settle_by_merge.json): failed/not_committed.
- reads never block during a real optimize, local + S3 (evidence/c1_reads_during_optimize_*.json).
- 4-unresolved cap across graphs (evidence/c2_unresolved_cap.json) behaves; BUT 409 text lacks
  ids/graphs/walking state (RFC 2.4 says the 409 reports resolution: walking per record).
- S3 crash campaign (evidence/c3_crash_results_s3.json) resolves as local.
## Harness artifact
- seam Hold blocks a tokio worker; on S3 this stalls HTTP connections (and lease renewal ->
  fence=Lapsed after 30s). Local unaffected. So S3 SIGTERM-under-hold exit 2 is an artifact.

## F-cli-503-none (VERIFIED) CLI served.rs classify(): every >=500 -> Unanswered, ignoring
disposition.effects=none. Routes disabled (503 effects none) -> CLI polls 5 lookups, exit 2,
"the operation may still run". Docs: refused before acceptance -> exit 1. evidence/c6_cli_results.json
## C6: 16/17 CLI cases match docs; never resubmitted (proxy logged <=1 POST in every case).
Big catalog at DEFAULT served settings (1,030 tables, 20 seeded; evidence/c5_big_catalog_*.json):
PR head: 1 table optimized per run, ~1,000 reported deferred(gate_hold_limit) with 98-375 ms of a
60 s hold, resume_at None every run -> 8 runs compact 8/20. Fixed: 1 run compacts 20/20.
## C5b bounds: body limit (413 above ~1 MiB) keeps requests inside the record bound; no wedge.
Damaged/foreign records -> 409 [operation_record_invalid|foreign], file never overwritten.

## F-s3-late-landing-wedge (VERIFIED, S3/RustFS + s3proxy late_forward)
Any record PUT (acceptance/running/publishing/final) whose answer is lost and whose copy
lands after the re-issues + read-back: process scope keeps the key unsettled with the old
precondition; every later record write (new precondition) refused -> all submissions 503
for the process life; lookups needing a write 503 (acceptance/running/publishing cases);
SIGTERM exit 1, lock kept, restart only after 45 s (lease x1.5). RFC 2.3: "Admission
refuses until a re-read settles it". Re-read happens (update_operations reads V') but
ClusterStore never calls settle_failed_precondition. evidence/c5_s3_lost_answer.txt

## F-artifact-scan-not-cancellable (VERIFIED, PR head) optimize.rs:1364 persist_graph_index_artifact
takes no cancellation; GraphIndex::build_counted never checks one; the write is issued without
checking for a pending shutdown. SIGTERM 1 s into a slow artifact scan: scan continued (5 more
edge opens), full 25 s grace consumed, exit 2, record left `publishing`, lock kept.
RFC 3.4/1.8: scan abandoned on shutdown/cancel/deployment drain; write skipped if shutdown pending.
evidence/c3_sigterm_artifact_prhead.json (scan slowed with faultfs open delay 5 s/edge file)
