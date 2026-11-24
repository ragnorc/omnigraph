#!/usr/bin/env bash
# F2 (oracle 5; rollback, then upgrade again): GQ 2's `index build` rebuilds NEW's lagging NGRAM index
# `body_ngram` as a BTREE under the same name (the upgrade guide: an earlier binary "rebuilds a lagging
# one as a BTREE"). Once rows land after that, every NEW `index build` fails:
#   stage index batch on node:Doc ([Scalar { column: "body", family: NGram }]):
#   index batch produced duplicate index name 'body_ngram'
# as plain text on stderr under --json, and keeps failing (NEW's full-text rebuild and cleanup do not
# clear it; NEW's optimize, or GQ 2's own index build, does). Answers stay correct.
#
#   GQ2=/path/to/gq2/omnigraph NEW=/path/to/new/omnigraph bash index_build_duplicate_ngram.sh
# Found by upgrade-fuzz Part 3 (concurrent GQ 2 and NEW writers with maintenance); shrunk by hand.
set -uo pipefail
: "${GQ2:?set GQ2 to a GQ 2 omnigraph (v0.16.0 or GQ2-MAIN)}"; : "${NEW:?set NEW to the GQ 3 omnigraph}"
W=$(mktemp -d); export OMNIGRAPH_HOME="$W/home" RUST_MIN_STACK=16777216
printf 'node Doc {\n    k: String @key\n    body: String @index\n}\n' > "$W/s.pg"
rows() { python3 -c "
import json,sys
lo,hi,w=int(sys.argv[1]),int(sys.argv[2]),sys.argv[3]
for i in range(lo,hi): print(json.dumps({'type':'Doc','data':{'k':f'd{i}','body':f'{w} {i}'}}))" "$@"; }
rows 0 20 graph > "$W/d1.jsonl"; rows 20 30 river > "$W/d2.jsonl"; rows 30 40 harbor > "$W/d3.jsonl"
G="$W/g"
step() { printf '$ %s\n' "$*"; "$@" > "$W/out" 2> "$W/err"; rc=$?
  python3 -c "
import json,sys
try:
    j=json.load(open('$W/out')); print('  exit', $rc, [(i['property'], i['kind'], i['action']) for i in j.get('indexes', [])] if 'indexes' in j else '')
except Exception:
    print('  exit', $rc, 'no JSON on stdout; stderr:', [l.strip() for l in open('$W/err') if 'Error' in l or 'duplicate' in l][-2:])"; }
"$GQ2" init --schema "$W/s.pg" "$G" >/dev/null 2>&1
"$GQ2" load --data "$W/d1.jsonl" --mode merge --target "$G" --json >/dev/null 2>&1
step "$NEW" index build --target "$G" --json                    # NEW builds body_ngram (NGRAM)
"$NEW" load --data "$W/d2.jsonl" --mode insert --target "$G" --json >/dev/null 2>&1
step "$GQ2" index build --target "$G" --json                    # GQ 2 refreshes body_ngram as a BTREE
"$NEW" load --data "$W/d3.jsonl" --mode insert --target "$G" --json >/dev/null 2>&1
step "$NEW" index build --target "$G" --json                    # fails: duplicate index name 'body_ngram'
step "$NEW" index rebuild --kind full-text --target "$G" --json
step "$NEW" index build --target "$G" --json                    # still fails
