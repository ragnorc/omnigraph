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
