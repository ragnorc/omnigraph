#!/usr/bin/env bash
# Back-to-back CPU/wall re-measurement of the baseline and final binaries on a
# quiet box: the same scenarios, 20 repeats, alternating binaries per fixture
# so drift affects both alike. Usage: quiet_run.sh <baseline-bin> <final-bin>
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
base=$1; final=$2
mkdir -p "$here/results"
: > "$here/results/quiet_baseline.jsonl"; : > "$here/results/quiet_final.jsonl"
queries=(scan_name grouped topk hop1 hop1_limit hop1_count hop2_chain hop2_bfs hop3_bfs bound_edge no_friends popular)
args=()
for q in "${queries[@]}"; do args+=(--only "$q"); done
for f in n10k_f4 n100k_f4 n10k_f16_zipf; do
  "$base" --fixture "$here/fixtures/$f.gqt" --queries "$here/queries.txt" --label quiet_baseline --repeat 20 --warmup 3 "${args[@]}" >> "$here/results/quiet_baseline.jsonl"
  "$final" --fixture "$here/fixtures/$f.gqt" --queries "$here/queries.txt" --label quiet_final --repeat 20 --warmup 3 "${args[@]}" >> "$here/results/quiet_final.jsonl"
done
echo QUIET-DONE
