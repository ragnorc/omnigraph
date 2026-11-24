#!/usr/bin/env bash
# F4 (oracle 4, doc): docs/user/search/analyzers.md says an earlier release's full-text index on a
# plain `@index` String is left in place and that "an index pass ... reports it pending once rows
# outpace it". NEW's `index build` never reports it: after NEW writes rows past GQ 2's full-text
# index, NEW's pass reports `pending_indexes: []` (twice), while GQ 2's own pass lists both fields.
# (The earlier matrix's defect 4 was the opposite: NEW advised the one-way rebuild. The code no longer
# advises it; the guide still says it does.)
#
#   GQ2=/path/to/gq2/omnigraph NEW=/path/to/new/omnigraph bash old_fulltext_not_reported_pending.sh
set -uo pipefail
: "${GQ2:?}"; : "${NEW:?}"
W=$(mktemp -d); export OMNIGRAPH_HOME="$W/home" RUST_MIN_STACK=16777216
printf 'node Doc {\n    k: String @key\n    body: String @index\n}\n' > "$W/s.pg"
python3 -c "
import json
for i in range(20): print(json.dumps({'type':'Doc','data':{'k':f'd{i}','body':f'graph running {i}'}}))" > "$W/d1.jsonl"
python3 -c "
import json
for i in range(20,30): print(json.dumps({'type':'Doc','data':{'k':f'd{i}','body':f'river stone {i}'}}))" > "$W/d2.jsonl"
G="$W/g"; P='import json,sys; j=json.load(sys.stdin); print([(i["property"],i["kind"],i["action"]) for i in j["indexes"]], "pending:", [(p["property"], p["reason"][:70]) for p in j["pending_indexes"]])'
"$GQ2" init --schema "$W/s.pg" "$G" >/dev/null 2>&1
"$GQ2" load --data "$W/d1.jsonl" --mode merge --target "$G" --json >/dev/null 2>&1
echo -n "GQ 2 index build: "; "$GQ2" index build --target "$G" --json 2>/dev/null | python3 -c "$P"
"$NEW" load --data "$W/d2.jsonl" --mode insert --target "$G" --json >/dev/null 2>&1
echo -n "NEW index build after NEW's rows: "; "$NEW" index build --target "$G" --json 2>/dev/null | python3 -c "$P"
echo -n "NEW index build again: "; "$NEW" index build --target "$G" --json 2>/dev/null | python3 -c "$P"
echo -n "GQ 2 index build on the same graph: "; "$GQ2" index build --target "$G" --json 2>/dev/null | python3 -c "$P"
