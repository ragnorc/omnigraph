#!/usr/bin/env bash
# Run the embedded group-commit stress harness once.
#   gs-run.sh <label> [KEY=VALUE ...]   (OG_GS_* settings, e.g. OG_GS_ACTORS=4)
# Writes the history to histories/<label>.jsonl and prints the summary and
# any violations. The binary path is cached in /tmp/gs_bin.
set -uo pipefail
LABEL=$1; shift
ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
OUT=$ROOT/research/group-commit-stress/histories/$LABEL.jsonl
rm -f "$OUT"
BIN=$(cat /tmp/gs_bin)
cd "$ROOT/crates/omnigraph"
env RUST_MIN_STACK=16777216 OG_GS_OUT="$OUT" "$@" timeout "${GS_TIMEOUT:-1800}" "$BIN" --ignored --nocapture gc_stress_harness 2>&1 \
  | grep --line-buffered -E "gs-summary|gs-violation|gs-maint|test result|panicked" \
  | sed "s/^/[$LABEL] /" | tee -a "$ROOT/research/group-commit-stress/results/gs-runs.log" | cut -c1-1500
