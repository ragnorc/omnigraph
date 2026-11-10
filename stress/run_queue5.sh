#!/usr/bin/env bash
set -u
cd "$(dirname "$0")"
mkdir -p /tmp/q5
log() { echo "$(date -u +%FT%TZ) $*" >> /tmp/q5/queue.log; }
run() { local name=$1; shift; log "start $name"; "$@" > "/tmp/q5/$name.out" 2>&1; log "end $name rc=$?"; }
export RUST_MIN_STACK=16777216
run soak_az_pr  env OG_BIN_DIR=/tmp/r2bin STRESS_AZ_PREFIX=az://omnigraph-stress/soak5 timeout 2700 python3 c2_served_hammer.py /tmp/q5/soak_az_pr --duration 1800 --writers 3 --submitters 3 --seed 91 --port 12295
run soak_s3_fix env OG_BIN_DIR=/tmp/r2fix STRESS_S3_PREFIX=s3://omnigraph-stress/soak6 timeout 2700 python3 c2_served_hammer.py /tmp/q5/soak_s3_fix --duration 1800 --writers 3 --submitters 3 --seed 92 --port 12296
log "queue done"
