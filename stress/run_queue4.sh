#!/usr/bin/env bash
# Round 2 campaign 4: reduced random kills and chaos (PR binaries), then the
# same on the fixed binaries locally.
set -u
cd "$(dirname "$0")"
log() { echo "$(date -u +%FT%TZ) $*" >> /tmp/q4/queue.log; }
run() { local name=$1; shift; log "start $name"; "$@" > "/tmp/q4/$name.out" 2>&1; log "end $name rc=$?"; }
export RUST_MIN_STACK=16777216
run kills_s3_pr     env OG_BIN_DIR=/tmp/r2bin STRESS_S3_PREFIX=s3://omnigraph-stress/k2 python3 c3_random_kills.py /tmp/q4/kills_s3_pr 20 71
run chaos_s3_pr     env OG_BIN_DIR=/tmp/r2bin STRESS_S3_PREFIX=s3://omnigraph-stress/ch2 python3 c7_chaos.py /tmp/q4/chaos_s3_pr 15 72
run kills_local_pr  env OG_BIN_DIR=/tmp/r2bin python3 c3_random_kills.py /tmp/q4/kills_local_pr 25 73
run chaos_local_pr  env OG_BIN_DIR=/tmp/r2bin python3 c7_chaos.py /tmp/q4/chaos_local_pr 20 74
run chaos_az_pr     env OG_BIN_DIR=/tmp/r2bin STRESS_AZ_PREFIX=az://omnigraph-stress/ch3 python3 c7_chaos.py /tmp/q4/chaos_az_pr 10 75
run kills_seams_local_pr env OG_BIN_DIR=/tmp/r2bin CHAOS_SEAMS=1 python3 c3_random_kills.py /tmp/q4/kills_seams_local_pr 25 76
run chaos_s3_fix    env OG_BIN_DIR=/tmp/r2fix STRESS_S3_PREFIX=s3://omnigraph-stress/ch4 python3 c7_chaos.py /tmp/q4/chaos_s3_fix 10 77
log "queue done"
