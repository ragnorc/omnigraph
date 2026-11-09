#!/usr/bin/env bash
# Campaign v2: after phase B, the small-cache instrumented variant, concurrent
# column patches, and phase C (crash, kill and chaos at reduced breadth).
set -u
cd "$(dirname "$0")"
C=/tmp/v2/C; mkdir -p $C
until grep -q 'phase B done' /tmp/v2/B/queue.log 2>/dev/null; do sleep 20; done
export OG_BIN_DIR=/tmp/sut-bin OG_CHILD_BIN=/tmp/sut-bin/stress_served_child RUST_MIN_STACK=16777216
log() { echo "$(date -u +%FT%TZ) $*" >> $C/queue.log; }
run() { local name=$1; shift; log "start $name"; "$@" > "$C/$name.out" 2>&1; log "end $name rc=$?"; }
# small-cache instrumented variant (2 handles, 64 KiB)
run small_cache env OG_BIN_DIR=/tmp/sut-smallcache python3 c11_cache_pressure.py $C/small_cache 600 60
run small_patch env OG_BIN_DIR=/tmp/sut-smallcache STRESS_CONCURRENT_OPT=1 python3 c9_column_patch_optimize.py $C/small_patch 150 21
run small_hammer env OG_BIN_DIR=/tmp/sut-smallcache python3 c2_served_hammer.py $C/small_hammer --duration 600 --writers 3 --submitters 4 --seed 81 --port 12311
# concurrent column patches on the SUT
run patch_conc_local env STRESS_CONCURRENT_OPT=1 python3 c9_column_patch_optimize.py $C/patch_conc_local 200 22
run patch_conc_s3 env STRESS_CONCURRENT_OPT=1 STRESS_S3_PREFIX=s3://omnigraph-stress/v2pc STRESS_PORT=13981 python3 c9_column_patch_optimize.py $C/patch_conc_s3 60 23
run patch_conc_azure env STRESS_CONCURRENT_OPT=1 STRESS_AZ_PREFIX=az://omnigraph-stress/v2pc STRESS_PORT=13982 python3 c9_column_patch_optimize.py $C/patch_conc_azure 60 24
# phase C
run crash_local python3 c3_crash.py $C/crash_local
run kills_local python3 c3_random_kills.py $C/kills_local 30 91
run kills_seams env CHAOS_SEAMS=1 python3 c3_random_kills.py $C/kills_seams 30 92
run kills_s3 env STRESS_S3_PREFIX=s3://omnigraph-stress/v2kills python3 c3_random_kills.py $C/kills_s3 15 93
run kills_azure env STRESS_AZ_PREFIX=az://omnigraph-stress/v2kills python3 c3_random_kills.py $C/kills_azure 20 94
run chaos_local python3 c7_chaos.py $C/chaos_local 30 95
run chaos_s3 env STRESS_S3_PREFIX=s3://omnigraph-stress/v2chaos python3 c7_chaos.py $C/chaos_s3 15 96
run chaos_azure env STRESS_AZ_PREFIX=az://omnigraph-stress/v2chaos python3 c7_chaos.py $C/chaos_azure 20 97
log "phase C done"
