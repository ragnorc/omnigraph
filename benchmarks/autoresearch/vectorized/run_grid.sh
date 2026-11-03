#!/usr/bin/env bash
# Run every fixture of the grid through the release instrument and keep one
# JSONL file per label: benchmarks/autoresearch/vectorized/results/<label>.jsonl
# Usage: run_grid.sh <label> [--repeat N] [--only query]... [fixture-stem...]
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
label=$1; shift
bin=${INSTRUMENT_BIN:-"$here/../../../target/release/vectorized_instrument"}
mkdir -p "$here/results"
out="$here/results/$label.jsonl"
: > "$out"
args=()
fixtures=()
while [ $# -gt 0 ]; do
  case "$1" in
    --repeat|--warmup|--only) args+=("$1" "$2"); shift 2;;
    *) fixtures+=("$1"); shift;;
  esac
done
if [ ${#fixtures[@]} -eq 0 ]; then
  fixtures=(n1k_f4 n10k_f4 n100k_f4 n10k_f1 n10k_f16 n10k_f4_wide n10k_f4_frag100 n10k_f16_zipf)
fi
for f in "${fixtures[@]}"; do
  "$bin" --fixture "$here/fixtures/$f.gqt" --queries "$here/queries.txt" --label "$label" "${args[@]}" >> "$out"
done
echo "wrote $out"
