#!/usr/bin/env bash
# Run every test of one binary in its own process with a per-test timeout.
#   per-test.sh <binary> <label> [timeout_s] [extra env KEY=VALUE ...]
# Output: results/pertest-<label>.tsv (name, status, seconds) and a log per failure.
set -uo pipefail
BIN=$1; LABEL=$2; TMO=${3:-180}; shift 3 || true
ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
RES=$ROOT/research/group-commit-stress/results
OUT=$RES/pertest-$LABEL.tsv; LOGS=$RES/pertest-$LABEL; mkdir -p "$LOGS"; : > "$OUT"
cd "$ROOT/crates/omnigraph"
"$BIN" --list 2>/dev/null | grep ': test$' | sed 's/: test$//' | while read -r name; do
  start=$(date +%s)
  env RUST_MIN_STACK=16777216 "$@" timeout "$TMO" "$BIN" --exact "$name" --test-threads=1 > "$LOGS/cur.log" 2>&1
  code=$?; secs=$(( $(date +%s) - start ))
  if grep -q "^test result: ok. 1 passed" "$LOGS/cur.log"; then st=pass
  elif grep -q "^test result: ok. 0 passed; 0 failed; 1 ignored" "$LOGS/cur.log"; then st=ignored
  elif [ $code -eq 124 ]; then st=timeout; cp "$LOGS/cur.log" "$LOGS/$name.log"
  else st=fail; cp "$LOGS/cur.log" "$LOGS/$name.log"; fi
  printf "%s\t%s\t%s\n" "$name" "$st" "$secs" >> "$OUT"
done
echo "done: $(cut -f2 "$OUT" | sort | uniq -c | tr '\n' ' ')"
