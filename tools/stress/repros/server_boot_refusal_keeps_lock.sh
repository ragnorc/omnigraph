#!/usr/bin/env bash
# A server that refuses to start after it took the cluster lock (an unreadable
# or other-writable bearer tokens file, a port in use) exits 1 and leaves
# __cluster/lock.json behind, naming its dead pid. On a local root the next
# start refuses with state_lock_held until `cluster force-unlock`.
# Usage: OG=<cli> OGS=<server> repros/server_boot_refusal_keeps_lock.sh
set -u
OG=${OG:-omnigraph}; OGS=${OGS:-omnigraph-server}
D=$(mktemp -d); cd "$D"; mkdir root
printf 'node P {\n  slug: String @key\n}\n' > g.pg
printf 'version: 1\nmetadata:\n  name: lockrepro\ngraphs:\n  g:\n    schema: g.pg\n' > cluster.yaml
lock() { "$OG" cluster status --target ./root --json 2>/dev/null | python3 -c 'import json,sys,re; print((re.findall(r"\"lock_id\": \"([^\"]+)\"", sys.stdin.read()) or ["none"])[0])'; }
"$OG" cluster apply --target ./root --config . --json >/dev/null 2>&1
"$OG" cluster force-unlock "$(lock)" --target ./root --yes >/dev/null 2>&1
echo "lock before start: $(lock)"
echo '{"act-a":"secret"}' > tokens.json; chmod 666 tokens.json
OMNIGRAPH_SERVER_BEARER_TOKENS_FILE=$PWD/tokens.json timeout 30 "$OGS" --cluster ./root --bind 127.0.0.1:18777 >boot1.log 2>&1
echo "refused start exit=$? : $(grep -o 'bearer tokens file.*mode 666)' boot1.log)"
echo "lock after refused start: $(lock)"
chmod 600 tokens.json
OMNIGRAPH_SERVER_BEARER_TOKENS_FILE=$PWD/tokens.json timeout 10 "$OGS" --cluster ./root --bind 127.0.0.1:18777 >boot2.log 2>&1
echo "corrected start exit=$? : $(grep -o 'state_lock_held' boot2.log | head -1)"
