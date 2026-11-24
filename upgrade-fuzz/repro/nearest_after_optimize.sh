#!/usr/bin/env bash
# F1 (oracle 1/5): after a vector index build, a delete and `optimize`, `nearest(k)` returns fewer
# than k rows although k live rows exist: the true nearest rows, in order, cut short. A graph rebuilt
# from the same export answers k rows. `index build` reports nothing to do; there is no vector rebuild.
# The same on GQ2-REL (v0.16.0), GQ2-MAIN and NEW, so not introduced by GQ 3.
#
#   BIN=/path/to/omnigraph [EDITION=3|2] bash nearest_after_optimize.sh
# Found by upgrade-fuzz seed 1168 (Part 2, oracle 1 and the fresh-graph check).
set -euo pipefail
: "${BIN:?set BIN to an omnigraph binary}"
EDITION=${EDITION:-3}
W=$(mktemp -d); export OMNIGRAPH_HOME="$W/home" RUST_MIN_STACK=16777216
printf 'node T0 {\n    s2: I32\n    v0: Vector(2) @index\n}\n' > "$W/s.pg"
python3 -c "
import json
for i in range(10): print(json.dumps({'type':'T0','data':{'s2': (i if i%2 else -i-1), 'v0':[i/10, 1-i/10]}}))" > "$W/d.jsonl"
if [ "$EDITION" = 3 ]; then
  MODE=insert
  cat > "$W/q.gq" <<'Q'
query near($v: Vector(2)) {
  match { let $pop = { match { $x: T0 } return { $x } }, from nearest($pop, $x.v0, $v, k: 5) as $d }
  return { $x.s2, $d as dist }
  order { $d }
  limit 5
}
query del() {
  match { $x: T0 } where $x.s2 > 0
  delete $x
}
Q
else
  MODE=merge
  cat > "$W/q.gq" <<'Q'
query near($v: Vector(2)) {
  match { $x: T0 }
  return { $x.s2, nearest($x.v0, $v) as dist }
  order { nearest($x.v0, $v) }
  limit 5
}
query del() {
  delete T0 where s2 > 0
}
Q
fi
G="$W/g"
count() { "$BIN" query near --query "$W/q.gq" --target "$G" --params '{"v":[0.0,1.0]}' --format json 2>/dev/null \
  | python3 -c "import json,sys; j=json.load(sys.stdin); print('$1:', j['row_count'], 'rows', [r['x.s2'] for r in j['rows']])"; }
"$BIN" init --schema "$W/s.pg" "$G" >/dev/null 2>&1
"$BIN" load --data "$W/d.jsonl" --mode $MODE --target "$G" --json >/dev/null 2>&1
"$BIN" index build --target "$G" --json >/dev/null 2>&1
"$BIN" mutate del --query "$W/q.gq" --target "$G" --json >/dev/null 2>&1
count "after delete (5 live rows)"
"$BIN" optimize --target "$G" --json >/dev/null 2>&1
count "after optimize"
"$BIN" index build --target "$G" --json 2>/dev/null | python3 -c "import json,sys; j=json.load(sys.stdin); print('index build:', len(j['indexes']), 'indexes,', len(j['pending_indexes']), 'pending')"
count "after index build"
"$BIN" export --target "$G" 2>/dev/null > "$W/e.jsonl"
"$BIN" init --schema "$W/s.pg" "$W/fresh" >/dev/null 2>&1
"$BIN" load --data "$W/e.jsonl" --mode $MODE --target "$W/fresh" --json >/dev/null 2>&1
G="$W/fresh" count "fresh graph from the export"
