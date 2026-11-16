#!/usr/bin/env bash
# Failure windows with mixed-actor batches: served release binaries on
# RustFS +30 ms, 64 writers over 16 actors (one token each) unless a cell says
# otherwise, insert-only mix; every run ends with the model check and both
# attribution checks (HTTP: commit get, feed, in-doubt attempts; engine: row-id
# coverage at each batch commit and at the head).
#   xactor-faults.sh <bins> <attr-bin> [secs] [cells]
set -uo pipefail
BINS=$1; ATTR_BIN=$2; SECS=${3:-90}
CELLS=${4:-"crash kill9 toxics cancel budget policy"}
HERE=$(cd "$(dirname "$0")" && pwd)
MIX=${XA_MIX:-fresh=80,fmulti=10,sknows=10}
COMMON_ENV=(--server-env OMNIGRAPH_PER_ACTOR_INFLIGHT_MAX=128 --server-env OMNIGRAPH_GLOBAL_INFLIGHT_MAX=512
            --server-env OMNIGRAPH_INGRESS_INFLIGHT_MAX=512 --server-env OMNIGRAPH_WRITE_OBSERVERS_MAX=512
            --server-env OMNIGRAPH_GROUP_COMMIT_STATS_SECS=10)
POLICY_ACTORS=$(python3 -c "print(','.join([f'act-{i}' for i in range(14)] + ['act-ro', 'act-unprot']))")
for cell in $CELLS; do
  label="xf-$cell"
  run=(); drive=(--writers 64 --secs "$SECS" --actors 16 --mix "$MIX" --reader-count 1 --readers 1)
  case $cell in
    crash)  run=(--watchdog 1 --server-env OMNIGRAPH_STRESS_CRASH=batch_admitted:3,batch_committed:3,batch_published:3) ;;
    kill9)  run=(--chaos-kill-secs 15 --optimize-secs 30) ;;
    toxics) run=(--toxics reset,truncate,timeout,jitter --toxic-every 5 --toxic-window 2) ;;
    cancel) drive+=(--cancel-pct 20 --cancel-ms 1500); run=(--optimize-secs 30) ;;
    budget) drive=(--writers 128 --secs "$SECS" --actors 64 --actor-style long --mix "$MIX" --reader-count 1 --readers 1) ;;
    policy) drive=(--writers 64 --secs "$SECS" --actor-names "$POLICY_ACTORS" --mix "$MIX" --reader-count 1 --readers 1) ;;
    *) echo "unknown cell $cell"; continue ;;
  esac
  echo "[faults] $label rtt=$("$HERE/proxy-rtt.sh")ms $(date -u +%H:%M:%S)"
  /srv/venv/bin/python "$HERE/served_run.py" "$label" --backend s3 --bins "$BINS" --attr-bin "$ATTR_BIN" \
    "${COMMON_ENV[@]}" "${run[@]}" -- "${drive[@]}" > "$HERE/../served/$label.out" 2>&1
  code=$?
  echo "[faults] $label exit=$code $(grep '^\[run\]' "$HERE/../served/$label.out" | tail -1 | cut -c1-300)"
  grep -o '"violations": [0-9]*' "$HERE/../served/$label/check.txt" 2>/dev/null | tail -1 | sed "s/^/[faults] $label model /"
  head -1 "$HERE/../served/$label/attr-http.txt" 2>/dev/null | cut -c1-600 | sed "s/^/[faults] $label attr-http /"
  grep -E 'attr-summary|test result' "$HERE/../served/$label/attr-engine.txt" 2>/dev/null | cut -c1-500 | sed "s/^/[faults] $label attr-engine /"
done
