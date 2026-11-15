#!/usr/bin/env bash
# Run the engine suites with the prototype's group commit forced on.
#   suites-on.sh <label> [GROUP_COMMIT=1]
# Binaries are the current dev build; logs go to results/suites-<label>-<suite>.log.
set -uo pipefail
LABEL=$1; GC=${2:-1}; BINS=${3:-}; ONLY=${4:-writes,integration,detached_commit_matrix,failpoints}
ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
RES=$ROOT/research/group-commit-stress/results
cd "$ROOT/crates/omnigraph"
pick() { if [ -n "$BINS" ]; then ls "$BINS"/$1-* | head -1; return; fi; for b in $(ls -t "$ROOT"/target/debug/deps/$1-* | grep -v '\.d$'); do
  if [ "$("$b" --list 2>/dev/null | tail -1 | awk '{print $1}')" = "$2" ]; then echo "$b"; return; fi; done; }
run() { local name=$1 bin=$2; shift 2
  case ",$ONLY," in *",$name,"*) ;; *) return;; esac
  echo "[suites] $name: $bin" >&2
  env RUST_MIN_STACK=16777216 OMNIGRAPH_GROUP_COMMIT=$GC timeout ${SUITE_TIMEOUT:-5400} "$bin" "$@" > "$RES/suites-$LABEL-$name.log" 2>&1
  echo "[suites] $name exit=$? $(grep -E '^test result' "$RES/suites-$LABEL-$name.log" | tail -1)" >&2; }
run writes "$(pick writes 68)"
run integration "$(pick integration 713)"
run detached_commit_matrix "$(pick detached_commit_matrix 32)"
run failpoints "$(pick failpoints 182)" --test-threads=1
