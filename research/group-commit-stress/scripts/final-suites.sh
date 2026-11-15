#!/usr/bin/env bash
# Final suite rerun on the current tree: build, then run every suite with
# batching on (and writes + integration with it off).
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
RES=$ROOT/research/group-commit-stress/results/final
mkdir -p "$RES"
cd "$ROOT"
cargo dev-build-tests > "$RES/build-dev.log" 2>&1 || { echo "dev build failed"; exit 1; }
pick() { for b in $(ls -t "$ROOT"/target/debug/deps/$1-* | grep -v '\.d$'); do
  if [ "$("$b" --list 2>/dev/null | tail -1 | awk '{print $1}')" = "$2" ]; then echo "$b"; return; fi; done; }
W=$(pick writes 68); I=$(pick integration 713); M=$(pick detached_commit_matrix 32); F=$(pick failpoints 182)
cd "$ROOT/crates/omnigraph"
for gc in 1 0; do
  for pair in "writes:$W" "integration:$I"; do
    name=${pair%%:*}; bin=${pair#*:}
    env RUST_MIN_STACK=16777216 OMNIGRAPH_GROUP_COMMIT=$gc timeout 3600 "$bin" > "$RES/$name-gc$gc.log" 2>&1
    echo "[final] $name gc=$gc: $(grep -E '^test result' "$RES/$name-gc$gc.log" | tail -1)"
  done
done
env RUST_MIN_STACK=16777216 OMNIGRAPH_GROUP_COMMIT=1 timeout 3600 "$M" > "$RES/matrix-gc1.log" 2>&1
echo "[final] detached_commit_matrix gc=1: $(grep -E '^test result' "$RES/matrix-gc1.log" | tail -1)"
"$ROOT/research/group-commit-stress/scripts/per-test.sh" "$F" final-failpoints-gc1 180 OMNIGRAPH_GROUP_COMMIT=1 | sed 's/^/[final] failpoints gc=1: /'
cd "$ROOT/crates/omnigraph-dst" && OMNIGRAPH_GROUP_COMMIT=1 RUST_MIN_STACK=16777216 timeout 3600 cargo test --locked --no-fail-fast > "$RES/dst-gc1.log" 2>&1
echo "[final] dst gc=1: $(grep -E '^test result' "$RES/dst-gc1.log" | tr '\n' ' ')"
cd "$ROOT" && cargo test -p omnigraph-gqt --locked --no-run > "$RES/build-gqt.log" 2>&1
G=$(ls -t "$ROOT"/target/debug/deps/gq_logic_tests-* | grep -v '\.d$' | head -1)
cd "$ROOT/crates/omnigraph-gqt" && OMNIGRAPH_GROUP_COMMIT=1 RUST_MIN_STACK=16777216 timeout 3600 "$G" > "$RES/gqt-gc1.log" 2>&1
echo "[final] gqt gc=1: $(grep -E '^test result' "$RES/gqt-gc1.log" | tail -1)"
