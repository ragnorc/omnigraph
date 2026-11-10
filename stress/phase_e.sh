#!/usr/bin/env bash
# Campaign v2, round 2 (phase E): the same campaign types with new seeds and
# shapes, to see whether a full round finds anything new. PR-head binaries.
set -u
cd "$(dirname "$0")"
E=/tmp/v2/E; mkdir -p $E
export OG_BIN_DIR=/tmp/sut-bin OG_CHILD_BIN=/tmp/sut-bin/stress_served_child RUST_MIN_STACK=16777216
log() { echo "$(date -u +%FT%TZ) $*" >> $E/queue.log; }
run() { local name=$1; shift; log "start $name"; "$@" > "$E/$name.out" 2>&1; log "end $name rc=$?"; }
run patch_conc_local env STRESS_CONCURRENT_OPT=1 python3 c9_column_patch_optimize.py $E/patch_conc_local 150 31
run patch_conc_s3 env STRESS_CONCURRENT_OPT=1 STRESS_S3_PREFIX=s3://omnigraph-stress/v2E STRESS_PORT=13941 python3 c9_column_patch_optimize.py $E/patch_conc_s3 40 32
run patch_conc_azure env STRESS_CONCURRENT_OPT=1 STRESS_AZ_PREFIX=az://omnigraph-stress/v2E STRESS_PORT=13942 python3 c9_column_patch_optimize.py $E/patch_conc_azure 40 33
run upd_azure env STRESS_WRITE_KIND=update STRESS_AZ_PREFIX=az://omnigraph-stress/v2Eupd STRESS_PORT=13943 python3 c1_writer_wait.py $E/upd_azure 120 1
run poll_azure_4 env STRESS_POLLERS=4 STRESS_AZ_PREFIX=az://omnigraph-stress/v2Epoll STRESS_PORT=13944 python3 c12_big_hold.py $E/poll_azure_4 20 30
run cache_local_300 env STRESS_PORT=13945 STRESS_SEED=23 python3 c11_cache_pressure.py $E/cache_local_300 600 300
run small_cache_100 env OG_BIN_DIR=/tmp/sut-smallcache STRESS_PORT=13946 STRESS_SEED=29 python3 c11_cache_pressure.py $E/small_cache_100 600 100
run hammer_local python3 c2_served_hammer.py $E/hammer_local --duration 900 --writers 4 --submitters 4 --seed 74 --port 12321
run hammer_s3 env STRESS_S3_PREFIX=s3://omnigraph-stress/v2Ehammer python3 c2_served_hammer.py $E/hammer_s3 --duration 900 --writers 3 --submitters 3 --seed 75 --port 12322
run hammer_azure env STRESS_AZ_PREFIX=az://omnigraph-stress/v2Ehammer python3 c2_served_hammer.py $E/hammer_azure --duration 600 --writers 3 --submitters 3 --seed 76 --port 12323
run kills_seams env CHAOS_SEAMS=1 python3 c3_random_kills.py $E/kills_seams 20 99
run chaos_local python3 c7_chaos.py $E/chaos_local 20 98
run chaos_s3 env STRESS_S3_PREFIX=s3://omnigraph-stress/v2Echaos python3 c7_chaos.py $E/chaos_s3 10 97
log "phase E done"
