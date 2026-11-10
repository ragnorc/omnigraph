#!/usr/bin/env bash
# Round 2 S7 campaigns, one at a time (two cores). Output under /tmp/q2/<name>.
set -u
cd "$(dirname "$0")"
mkdir -p /tmp/q2
log() { echo "$(date -u +%FT%TZ) $*" >> /tmp/q2/queue.log; }
run() {
  local name=$1; shift
  log "start $name"
  "$@" > "/tmp/q2/$name.out" 2>&1
  log "end $name rc=$?"
}
export RUST_MIN_STACK=16777216 OG_BIN_DIR=/tmp/r2bin
S=${S:-120}
run wy_s3_2w        env STRESS_S3_PREFIX=s3://omnigraph-stress/wy1 STRESS_PORT=13971 python3 c9_writer_yield.py /tmp/q2/wy_s3_2w $S 2
run wy_s3_2w_poll   env STRESS_S3_PREFIX=s3://omnigraph-stress/wy2 STRESS_PORT=13972 POLLERS=4 POLL_MS=100 python3 c9_writer_yield.py /tmp/q2/wy_s3_2w_poll $S 2
run wy_s3_1w        env STRESS_S3_PREFIX=s3://omnigraph-stress/wy3 STRESS_PORT=13973 python3 c9_writer_yield.py /tmp/q2/wy_s3_1w $S 1
run wy_az_2w        env STRESS_AZ_PREFIX=az://omnigraph-stress/wy4 STRESS_PORT=13974 python3 c9_writer_yield.py /tmp/q2/wy_az_2w $S 2
run wy_az_2w_poll   env STRESS_AZ_PREFIX=az://omnigraph-stress/wy5 STRESS_PORT=13975 POLLERS=4 POLL_MS=100 python3 c9_writer_yield.py /tmp/q2/wy_az_2w_poll $S 2
run wy_local_2w     env STRESS_PORT=13976 python3 c9_writer_yield.py /tmp/q2/wy_local_2w $S 2
run wy_local_2w_poll env STRESS_PORT=13977 POLLERS=4 POLL_MS=100 python3 c9_writer_yield.py /tmp/q2/wy_local_2w_poll $S 2
log "queue done"
