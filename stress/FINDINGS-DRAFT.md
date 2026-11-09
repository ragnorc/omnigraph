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
