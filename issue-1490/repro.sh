#!/bin/bash
# usage: repro.sh <bindir> <outdir>
B=$1; S=$2; rm -rf "$S"; mkdir -p "$S"; PORT=18790
for v in under dash; do
  [ "$v" = under ] && gid=probe_under || gid=probe-dash
  mkdir -p "$S/$v/schemas" "$S/${v}-storage"
  printf 'version: 1\nstate: { backend: cluster, lock: true }\ngraphs:\n  %s:\n    schema: schemas/probe.pg\n' "$gid" > "$S/$v/cluster.yaml"
  printf 'node Thing {\n  key: String @key\n}\n' > "$S/$v/schemas/probe.pg"
  echo "== $gid: cluster apply"
  "$B/omnigraph" cluster apply --target "$S/${v}-storage" --config "$S/$v" --as act-operator --json > "$S/$v.apply.json" 2>"$S/$v.apply.err"; echo "exit $?"
  python3 -c 'import json,sys
try:
  d=json.load(open(sys.argv[1])); print("converged:", json.dumps(d).count("\"converged\": true")>0)
except Exception as e: print("no json")' "$S/$v.apply.json"
  tail -5 "$S/$v.apply.err"
  lock=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("lock_id",""))' "$S/${v}-storage/__cluster/lock.json" 2>/dev/null)
  [ -n "$lock" ] && "$B/omnigraph" cluster force-unlock --target "$S/${v}-storage" --yes "$lock" >/dev/null 2>&1
  echo "== $gid: server boot"
  timeout 20 "$B/omnigraph-server" --cluster "file://$S/${v}-storage" --bind 127.0.0.1:$PORT --require-all-graphs --unauthenticated > "$S/$v.server.log" 2>&1 &
  pid=$!
  for i in $(seq 1 40); do curl -sf http://127.0.0.1:$PORT/readyz >/dev/null && { echo "readyz ok"; break; }; kill -0 $pid 2>/dev/null || break; python3 -c 'import time;time.sleep(0.5)'; done
  kill $pid 2>/dev/null; wait $pid; echo "server exit $?"
  grep -E 'Error|graph_id|must match' "$S/$v.server.log" | head -4
  PORT=$((PORT+1))
done
