#!/bin/sh
# Part B of gq2-vs-gq3: the catalog scenarios with no GQ text in their
# measured step, run by NEW's omnigraph-bench over one local store with each
# commit's `omnigraph` (`--at <commit>=<binary>`), the catalog's repetitions.
# Usage: gq2_vs_gq3_partb.sh <NEW checkout> <bench> <store> <out dir> <old sha>=<old omnigraph> <new sha>=<new omnigraph> [scenario...]
set -u
tree=$1 bench=$2 store=$3 out=$4 old=$5 new=$6
shift 6
ids=${*:-"branch-merge-d50-process-cold branch-merge-d50-warm branch-merge-d50-history64 finbench-disjoint-merge repeated-deletion-recreation-branches tiny-branch tiny-load tiny-restart very-long-history-1000-reopen very-long-history-10000-reopen very-long-history-100000-reopen very-long-history-32-reopen e2e-merge-all-changed e2e-merge-all-new e2e-merge-diverged-updates e2e-mixed-load e2e-branch-create e2e-branch-create-from e2e-branch-list e2e-branch-delete e2e-branch-adopt-untouched e2e-branch-adopt-written branch-merge-d50-as-commit concurrent-writes"}
mkdir -p "$out" "$store"
cd "$tree" || exit 2
for id in $ids; do
  [ -s "$out/$id.json" ] && continue
  start=$(date -u +%FT%TZ)
  timeout 3600 "$bench" run "$id" --store "$store" --at "$old" --at "$new" --json \
    > "$out/$id.json" 2> "$out/$id.stderr" < /dev/null
  rc=$?
  echo "$id rc=$rc start=$start end=$(date -u +%FT%TZ)" | tee -a "$out/runs.log"
done
