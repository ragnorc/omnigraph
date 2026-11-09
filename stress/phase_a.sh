#!/usr/bin/env bash
# Campaign v2, phase A: rerun every prior finding's reproducer against the
# PR heads (binaries frozen in /tmp/sut-bin). One at a time; each writes
# /tmp/v2/A/<name> and a line to /tmp/v2/A/queue.log.
set -u
cd "$(dirname "$0")"
A=/tmp/v2/A; mkdir -p $A
export OG_BIN_DIR=/tmp/sut-bin OG_CHILD_BIN=/tmp/sut-bin/stress_served_child RUST_MIN_STACK=16777216
log() { echo "$(date -u +%FT%TZ) $*" >> $A/queue.log; }
run() { local name=$1; shift; log "start $name"; "$@" > "$A/$name.out" 2>&1; log "end $name rc=$?"; }
run B1_engine /tmp/sut-bin/engine_optimize_stress --ignored --exact stress_repro_pipeline_exits_before_planning_the_tail --nocapture
run B1_served python3 c1_served_early_exit.py $A/B1_served /tmp/sut-bin
run B1_big python3 c5_big_catalog.py $A/B1_big /tmp/sut-bin
run S1_s3 env ARM_SECONDS=8 python3 c5_s3_lost_answer.py $A/S1_s3 late:0 late:1 late:2 late:3
run S2_local python3 c5_record_write_faults.py $A/S2_local final_refused running_refused publishing_refused noop_final_refused
run S3_race python3 c4_deploy.py $A/S3_race race
run S4_artifact python3 c3_sigterm_artifact.py $A/S4_artifact /tmp/sut-bin 5000
run S5_cli python3 c6_cli.py $A/S5_cli routes_disabled
run S6_cap python3 c2_unresolved_cap.py $A/S6_cap
run S7_local python3 c1_writer_wait.py $A/S7_local 180 2
run S7_s3 env STRESS_S3_PREFIX=s3://omnigraph-stress/v2ww STRESS_PORT=13951 python3 c1_writer_wait.py $A/S7_s3 180 2
run S7_s3_one env RUST_LOG=info,omnigraph::loader=debug STRESS_S3_PREFIX=s3://omnigraph-stress/v2ww STRESS_PORT=13953 python3 c1_writer_wait.py $A/S7_s3_one 90 1
run S7_azure env STRESS_AZ_PREFIX=az://omnigraph-stress/v2ww STRESS_PORT=13954 python3 c1_writer_wait.py $A/S7_azure 120 2
run N8_s3 python3 c2_stale_resume_s3.py $A/N8_s3
log "phase A done"
