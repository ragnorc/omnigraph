#!/bin/sh
# Build a frozen base with one binary: init, overwrite load, index build, optimize.
# Usage: make_base.sh <omnigraph> <schema.pg> <data.jsonl> <base-dir> <stats-dir>
set -eu
bin=$1 schema=$2 data=$3 base=$4 stats=$5
mkdir -p "$stats"
rm -rf "$base"
"$bin" init --schema "$schema" --target "$base" >/dev/null 2>&1
"$bin" load --data "$data" --mode overwrite --target "$base" --set write_max_bytes=8589934592 --json --stats "$stats/load.json" >"$stats/load.out" 2>&1
"$bin" index build --target "$base" --json --stats "$stats/index.json" >"$stats/index.out" 2>&1
"$bin" optimize --target "$base" --json --stats "$stats/optimize.json" >"$stats/optimize.out" 2>&1
python3 -c 'import json,sys
for s in ("load","index","optimize"):
    d=json.load(open(sys.argv[1]+"/"+s+".json")); print(s, round(d["elapsed_ms"]/1000,1),"s", round(d["peak_rss_bytes"]/2**20),"MiB")' "$stats"
du -sh "$base"
