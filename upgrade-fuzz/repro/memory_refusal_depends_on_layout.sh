#!/usr/bin/env bash
# F6 (oracle 1, pre-existing): near `query_memory_bytes`, whether one read answers or is refused
# depends on physical state, not content. A sorted range read over the same rows reports a different
# `actual` memory charge (the smallest limit that would admit it) on the loaded graph, after
# `index build` (a BTREE on the filtered property) and after `optimize` (fewer, larger fragments), so
# a limit between them answers in one state and refuses in another. Found at 100,000 rows with the
# default 150 MiB limit (upgrade-fuzz stress seed 9011: 53,999 rows on a graph built from the export,
# `resource limit exceeded for query_memory_bytes: actual 191148197` on the upgraded graph and on the
# GQ 2-built one, from GQ2-MAIN and NEW alike). Here at 4,000 rows with a lowered limit.
#
#   BIN=/path/to/omnigraph [EDITION=3|2] bash memory_refusal_depends_on_layout.sh
set -uo pipefail
: "${BIN:?set BIN}"; EDITION=${EDITION:-3}
W=$(mktemp -d); export OMNIGRAPH_HOME="$W/home" RUST_MIN_STACK=16777216
printf 'node T0 {\n    txt0: String\n    s2: U32\n    s3: DateTime @index\n}\n' > "$W/s.pg"
for part in 0 1 2 3; do python3 -c "
import json,random,sys
p=int(sys.argv[1]); r=random.Random(p)
for i in range(p*1000,(p+1)*1000):
    print(json.dumps({'type':'T0','data':{'txt0':' '.join(r.choice(['graph','river','stone','café','数据库']) for _ in range(r.randint(50,400))),'s2':i,'s3':f'20{10+i%15:02d}-01-01T00:00:00Z'}}))" $part > "$W/d$part.jsonl"; done
if [ "$EDITION" = 3 ]; then MODE=insert; printf 'query r($v: DateTime) {\n  match { $x: T0 }\n  where $x.s3 > $v\n  return { $x.txt0, $x.s2 }\n  order { $x.txt0, $x.s2 }\n}\n' > "$W/q.gq"
else MODE=merge; printf 'query r($v: DateTime) {\n  match {\n    $x: T0\n    $x.s3 > $v\n  }\n  return { $x.txt0, $x.s2 }\n  order { $x.txt0, $x.s2 }\n}\n' > "$W/q.gq"; fi
mk() { "$BIN" init --schema "$W/s.pg" "$1" >/dev/null 2>&1; for p in 0 1 2 3; do "$BIN" load --data "$W/d$p.jsonl" --mode $MODE --target "$1" --json >/dev/null 2>&1; done; }
ok_at() { OMNIGRAPH_QUERY_MEMORY_BYTES=$2 "$BIN" query r --query "$W/q.gq" --target "$1" --params '{"v":"2011-01-01T00:00:00Z"}' --format json 2>/dev/null \
  | python3 -c "import json,sys; sys.exit(0 if json.load(sys.stdin).get('row_count') is not None else 1)"; }
charge() { lo=100000; hi=400000000; while [ $((hi - lo)) -gt 20000 ]; do mid=$(((lo + hi) / 2)); if ok_at "$1" $mid; then hi=$mid; else lo=$mid; fi; done
  echo "$2: answers at a query_memory_bytes of $hi, refused at $lo"; echo "$hi" > "$1.need"; }
mk "$W/a"; charge "$W/a" "loaded"
cp -a "$W/a" "$W/b"; "$BIN" index build --target "$W/b" --json >/dev/null 2>&1; charge "$W/b" "after index build"
cp -a "$W/a" "$W/c"; "$BIN" optimize --target "$W/c" --json >/dev/null 2>&1; charge "$W/c" "after optimize"
L=$(sort -n "$W"/*.need | head -1)
echo "with query_memory_bytes=$L (the loaded graph's need) the same read, same rows:"
for st in a b c; do echo -n "  $st: "; OMNIGRAPH_QUERY_MEMORY_BYTES=$L "$BIN" query r --query "$W/q.gq" --target "$W/$st" --params '{"v":"2011-01-01T00:00:00Z"}' --format json 2>/dev/null | python3 -c "import json,sys; j=json.load(sys.stdin); print(j.get('row_count'), 'rows' if j.get('row_count') is not None else j.get('error','')[:100])"; done
