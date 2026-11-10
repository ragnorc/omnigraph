#!/usr/bin/env bash
# Round 2: S7 after the fixes, and heavier mixes on PR and fixed binaries.
set -u
cd "$(dirname "$0")"
log() { echo "$(date -u +%FT%TZ) $*" >> /tmp/q3/queue.log; }
run() { local name=$1; shift; log "start $name"; "$@" > "/tmp/q3/$name.out" 2>&1; log "end $name rc=$?"; }
export RUST_MIN_STACK=16777216
PR=/tmp/r2bin FIX=/tmp/r2fix
run fix_s3_2w       env OG_BIN_DIR=$FIX STRESS_S3_PREFIX=s3://omnigraph-stress/fx1 STRESS_PORT=13981 python3 c9_writer_yield.py /tmp/q3/fix_s3_2w 120 2
run fix_s3_2w_poll  env OG_BIN_DIR=$FIX STRESS_S3_PREFIX=s3://omnigraph-stress/fx2 STRESS_PORT=13982 POLLERS=4 POLL_MS=100 python3 c9_writer_yield.py /tmp/q3/fix_s3_2w_poll 120 2
run fix_az_2w       env OG_BIN_DIR=$FIX STRESS_AZ_PREFIX=az://omnigraph-stress/fx3 STRESS_PORT=13983 python3 c9_writer_yield.py /tmp/q3/fix_az_2w 120 2
run pr_s3_mix       env OG_BIN_DIR=$PR PHASES=control,optimize STRESS_S3_PREFIX=s3://omnigraph-stress/mx1 STRESS_PORT=13984 BRANCH_WRITERS=2 MUTATORS=1 POLLERS=2 python3 c9_writer_yield.py /tmp/q3/pr_s3_mix 90 6
run fix_s3_mix      env OG_BIN_DIR=$FIX PHASES=control,optimize STRESS_S3_PREFIX=s3://omnigraph-stress/mx2 STRESS_PORT=13985 BRANCH_WRITERS=2 MUTATORS=1 POLLERS=2 python3 c9_writer_yield.py /tmp/q3/fix_s3_mix 90 6
run pr_local_mix    env OG_BIN_DIR=$PR PHASES=control,optimize STRESS_PORT=13986 BRANCH_WRITERS=1 MUTATORS=1 POLLERS=2 python3 c9_writer_yield.py /tmp/q3/pr_local_mix 90 3
run fix_local_mix   env OG_BIN_DIR=$FIX PHASES=control,optimize STRESS_PORT=13987 BRANCH_WRITERS=1 MUTATORS=1 POLLERS=2 python3 c9_writer_yield.py /tmp/q3/fix_local_mix 90 3
run fix_local_2w    env OG_BIN_DIR=$FIX STRESS_PORT=13988 python3 c9_writer_yield.py /tmp/q3/fix_local_2w 120 2
log "queue done"
