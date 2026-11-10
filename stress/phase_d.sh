#!/usr/bin/env bash
# Campaign v2, phase D: follow-ups after phase C. S8 attribution on S3
# (cache pressure on the PR head vs the stress branch with the S8 fix), a quiet
# local cache run for slow-read correlation, and the campaign 1 rerun that
# counts violation categories.
set -u
cd "$(dirname "$0")"
D=/tmp/v2/D; mkdir -p $D
until grep -q 'phase C done' /tmp/v2/C/queue.log 2>/dev/null; do sleep 20; done
export OG_BIN_DIR=/tmp/sut-bin OG_CHILD_BIN=/tmp/sut-bin/stress_served_child RUST_MIN_STACK=16777216
log() { echo "$(date -u +%FT%TZ) $*" >> $D/queue.log; }
run() { local name=$1; shift; log "start $name"; "$@" > "$D/$name.out" 2>&1; log "end $name rc=$?"; }
run cache_s3_sut env STRESS_S3_PREFIX=s3://omnigraph-stress/v2cacheD STRESS_PORT=13961 python3 c11_cache_pressure.py $D/cache_s3_sut 600 120
run cache_s3_fixed env OG_BIN_DIR=/tmp/fixed-v2-bin STRESS_S3_PREFIX=s3://omnigraph-stress/v2cacheDf STRESS_PORT=13962 python3 c11_cache_pressure.py $D/cache_s3_fixed 600 120
run cache_local_quiet env STRESS_PORT=13963 python3 c11_cache_pressure.py $D/cache_local_quiet 600 200
run c1_local_categories env STRESS_SECONDS=120 /tmp/sut-bin/engine_optimize_stress --ignored --exact engine_optimize_beside_live_writers --nocapture
log "phase D done"
