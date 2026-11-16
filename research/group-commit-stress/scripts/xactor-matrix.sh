#!/usr/bin/env bash
# Cross-actor throughput matrix: 64 writers on RustFS +30 ms, served release
# binaries, actor mixes 1/4/16/64 (one bearer token each) under three modes:
#   on      cross-actor batches (RFC rule 19)
#   one     one-actor batches (OMNIGRAPH_GROUP_COMMIT_ONE_ACTOR=1)
#   off     batching off (OMNIGRAPH_GROUP_COMMIT=0)
# Every run ends with the model check and both attribution checks.
#   xactor-matrix.sh <bins> <attr-bin> [secs] [modes] [actor mixes]
set -uo pipefail
BINS=$1; ATTR_BIN=$2; SECS=${3:-60}; MODES=${4:-"on one off"}; MIXES=${5:-"1 4 16 64"}
HERE=$(cd "$(dirname "$0")" && pwd)
MIX=${XA_MIX:-fresh=75,multi=10,knows=10,pair=5}
COMMON_ENV=(--server-env OMNIGRAPH_PER_ACTOR_INFLIGHT_MAX=128 --server-env OMNIGRAPH_GLOBAL_INFLIGHT_MAX=512
            --server-env OMNIGRAPH_INGRESS_INFLIGHT_MAX=512 --server-env OMNIGRAPH_WRITE_OBSERVERS_MAX=512
            --server-env OMNIGRAPH_GROUP_COMMIT_STATS_SECS=10)
for actors in $MIXES; do
  for mode in $MODES; do
    label="xa-${mode}-a${actors}"
    extra=()
    gc=1
    case $mode in
      one) extra=(--server-env OMNIGRAPH_GROUP_COMMIT_ONE_ACTOR=1) ;;
      off) gc=0 ;;
    esac
    echo "[matrix] $label rtt=$("$HERE/proxy-rtt.sh")ms $(date -u +%H:%M:%S)"
    /srv/venv/bin/python "$HERE/served_run.py" "$label" --backend s3 --bins "$BINS" --attr-bin "$ATTR_BIN" \
      --group-commit $gc "${COMMON_ENV[@]}" "${extra[@]}" \
      -- --writers 64 --secs "$SECS" --actors "$actors" --mix "$MIX" --reader-count 1 --readers 1 \
      > "$HERE/../served/$label.out" 2>&1
    code=$?
    echo "[matrix] $label exit=$code $(grep '^\[run\]' "$HERE/../served/$label.out" | tail -1 | cut -c1-400)"
    grep -E '^\{"ops"|"violations"' "$HERE/../served/$label/check.txt" 2>/dev/null | head -2 | cut -c1-300 | sed "s/^/[matrix] $label check /"
    head -1 "$HERE/../served/$label/attr-http.txt" 2>/dev/null | sed "s/^/[matrix] $label attr-http /"
    grep -E 'attr-summary|test result' "$HERE/../served/$label/attr-engine.txt" 2>/dev/null | cut -c1-500 | sed "s/^/[matrix] $label attr-engine /"
  done
done
