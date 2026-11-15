#!/bin/sh
# Runs each named case under --measure for both models and keeps the TSVs:
#   [PROFILE="object_store latency_units=N"] ./measure.sh <label> <case>...
# PROFILE, when set, pins the cases' `# storage_profile:` header.
# Results land in results/<label>/<case>.<model>.{txt,tsv}.
set -e
cd "$(dirname "$0")"
root=$(cd ../.. && pwd)
label=$1; shift
mkdir -p "results/$label"
python3 gen.py
trap 'PROFILE= python3 gen.py' EXIT
for name in "$@"; do
  for model in unit s3-like; do
    art=$(mktemp -d)
    RUST_MIN_STACK=16777216 "$root/target/debug/omnigraph-gqt" "$name.gqt" \
      --measure --model "$model" --artifacts "$art" > "results/$label/$name.$model.txt" 2>&1 \
      || { tail -20 "results/$label/$name.$model.txt"; exit 1; }
    cp "$art"/cost/*.tsv "results/$label/$name.$model.tsv"
    rm -rf "$art"
    tail -1 "results/$label/$name.$model.txt"
  done
done
