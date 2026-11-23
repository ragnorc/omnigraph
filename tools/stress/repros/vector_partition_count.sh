#!/usr/bin/env bash
# index build fails for good with "the statistics of vector index 'v_idx' name
# no partition count" after load, optimize, update of the vector column.
# Usage: OG=<cli> repros/vector_partition_count.sh [schema-file]
set -u
OG=${OG:-omnigraph}
SCH=${1:+$(readlink -f "$1")}; D=$(mktemp -d); cd "$D"
if [ -n "$SCH" ]; then cp "$SCH" s.pg; else
printf 'node D {\n  slug: String @key\n  n: I64?\n  s: String?\n  b: Blob?\n  v: Vector(4)? @index\n}\n' > s.pg; fi
"$OG" init --schema s.pg ./g.omni >/dev/null 2>&1
printf '{"type":"D","data":{"slug":"k2","v":null}}\n{"type":"D","data":{"slug":"k5","v":[1.0,-2.0,-2.0,-1.0]}}\n' > l.jsonl
"$OG" load --data l.jsonl --mode merge --target ./g.omni >/dev/null 2>&1
"$OG" optimize --target ./g.omni --yes >/dev/null 2>&1
"$OG" mutate -e 'query m($v: Vector(4)?) { update D set { v: $v } where slug in ["k5","k2"] }' --params '{"v":[2.0,1.0,0.5,0.0]}' --target ./g.omni >/dev/null 2>&1
for i in 1 2; do
  "$OG" index build --target ./g.omni 2>&1 | sed 's/\x1b\[[0-9;]*m//g' | grep -E "0:|vector (built|refreshed)" | head -1
done
