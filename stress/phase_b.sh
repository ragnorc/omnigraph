#!/usr/bin/env bash
# Campaign v2, phase B: what the merge changed (OCC placement, cache eviction,
# column-patch updates, maintenance policy), on the frozen PR-head binaries.
set -u
cd "$(dirname "$0")"
B=/tmp/v2/B; mkdir -p $B
export OG_BIN_DIR=/tmp/sut-bin OG_CHILD_BIN=/tmp/sut-bin/stress_served_child RUST_MIN_STACK=16777216
log() { echo "$(date -u +%FT%TZ) $*" >> $B/queue.log; }
run() { local name=$1; shift; log "start $name"; "$@" > "$B/$name.out" 2>&1; log "end $name rc=$?"; }
run patch_local python3 c9_column_patch_optimize.py $B/patch_local 200 10
run patch_s3 env STRESS_S3_PREFIX=s3://omnigraph-stress/v2patch STRESS_PORT=13991 python3 c9_column_patch_optimize.py $B/patch_s3 60 11
run patch_azure env STRESS_AZ_PREFIX=az://omnigraph-stress/v2patch STRESS_PORT=13992 python3 c9_column_patch_optimize.py $B/patch_azure 60 12
run policy env STRESS_SECONDS=120 python3 c10_policy.py $B/policy
run cache_local python3 c11_cache_pressure.py $B/cache_local 900 200
run cache_s3 env STRESS_S3_PREFIX=s3://omnigraph-stress/v2cache STRESS_PORT=13993 python3 c11_cache_pressure.py $B/cache_s3 600 120
run hammer_local python3 c2_served_hammer.py $B/hammer_local --duration 1200 --writers 3 --submitters 4 --seed 71 --port 12301
run hammer_s3 env STRESS_S3_PREFIX=s3://omnigraph-stress/v2hammer python3 c2_served_hammer.py $B/hammer_s3 --duration 1200 --writers 3 --submitters 4 --seed 72 --port 12302
run hammer_azure env STRESS_AZ_PREFIX=az://omnigraph-stress/v2hammer python3 c2_served_hammer.py $B/hammer_azure --duration 900 --writers 3 --submitters 4 --seed 73 --port 12303
run c1_local env STRESS_SECONDS=120 /tmp/sut-bin/engine_optimize_stress --ignored --exact engine_optimize_beside_live_writers --nocapture
run c1_s3 env AWS_ACCESS_KEY_ID=omnigraphci AWS_SECRET_ACCESS_KEY=omnigraphci-secret AWS_REGION=us-east-1 AWS_ENDPOINT_URL=http://127.0.0.1:9000 AWS_ENDPOINT_URL_S3=http://127.0.0.1:9000 AWS_ALLOW_HTTP=true AWS_S3_FORCE_PATH_STYLE=true STRESS_SECONDS=60 STRESS_ROOT_URI=s3://omnigraph-stress/v2c1/$(date +%s)/g.omni /tmp/sut-bin/engine_optimize_stress --ignored --exact engine_optimize_beside_live_writers --nocapture
run c1_logical /tmp/sut-bin/engine_optimize_stress --ignored --exact stress_optimize_preserves_logical_state --nocapture
log "phase B done"
