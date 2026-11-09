#!/usr/bin/env bash
# Long campaigns run back to back (one at a time: the box has two cores).
# Each writes under /tmp/q/<name>; a line per campaign goes to /tmp/q/queue.log.
# Usage: run_queue.sh
set -u
cd "$(dirname "$0")"
mkdir -p /tmp/q
log() { echo "$(date -u +%FT%TZ) $*" >> /tmp/q/queue.log; }
run() {
  local name=$1; shift
  log "start $name"
  "$@" > "/tmp/q/$name.out" 2>&1
  log "end $name rc=$?"
}
export RUST_MIN_STACK=16777216
run ww_azure env STRESS_AZ_PREFIX=az://omnigraph-stress/ww STRESS_PORT=13954 python3 c1_writer_wait.py /tmp/q/ww_azure 120 2
run chaos_s3 env STRESS_S3_PREFIX=s3://omnigraph-stress/chaosq python3 c7_chaos.py /tmp/q/chaos_s3 40 31
run kills_seams_local env CHAOS_SEAMS=1 python3 c3_random_kills.py /tmp/q/kills_seams_local 80 41
run chaos_azure env STRESS_AZ_PREFIX=az://omnigraph-stress/chaosq python3 c7_chaos.py /tmp/q/chaos_azure 60 51
run soak_s3_2h env STRESS_S3_PREFIX=s3://omnigraph-stress/soak2 python3 c2_served_hammer.py /tmp/q/soak_s3_2h --duration 7200 --writers 3 --submitters 3 --seed 61 --port 12195
bin=$(ls -t ../target/debug/deps/engine_optimize_stress-* | grep -v '\.d$' | head -1)
run c1_local_long env STRESS_SECONDS=300 "$bin" --ignored --exact engine_optimize_beside_live_writers --nocapture
log "queue done"
