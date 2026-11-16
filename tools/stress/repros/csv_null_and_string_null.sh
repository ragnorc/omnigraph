#!/usr/bin/env bash
# `query --format csv` writes a null cell and the string "null" the same way
# (bare null), and writes "" for an empty string. Read back with `load csv`,
# the null becomes the string "null" and the empty string becomes null.
# Usage: OG=<cli> repros/csv_null_and_string_null.sh
set -u
OG=${OG:-omnigraph}
D=$(mktemp -d); cd "$D"; mkdir src
printf 'node X {\n  slug: String @key\n  s: String?\n}\n' > x.pg
"$OG" init --schema x.pg ./a.omni >/dev/null 2>&1
"$OG" init --schema x.pg ./b.omni >/dev/null 2>&1
printf '%s\n' '{"type":"X","data":{"slug":"empty","s":""}}' '{"type":"X","data":{"slug":"nul"}}' '{"type":"X","data":{"slug":"word","s":"null"}}' > x.jsonl
"$OG" load --target ./a.omni --data x.jsonl --mode merge >/dev/null 2>&1
Q='query q() { match { $x: X } return { $x.slug as slug, $x.s as s } order { $x.slug asc } }'
echo "--- query --format csv"
"$OG" query --target ./a.omni -e "$Q" --format csv 2>/dev/null | tee src/x.csv
echo "--- after load csv of that output, as JSON rows"
(cd src && "$OG" mutate --target ../b.omni -e "load csv \"file://$PWD/x.csv\" into node:X mode append header" >/dev/null 2>&1)
"$OG" query --target ./b.omni -e "$Q" --json 2>/dev/null | python3 -c 'import json,sys; [print(r) for r in json.load(sys.stdin)["rows"]]'
