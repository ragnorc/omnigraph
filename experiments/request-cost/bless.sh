#!/bin/sh
# Fills expected.json for the named cases by blessing a direct-engine copy.
set -e
cd "$(dirname "$0")"
root=$(cd ../.. && pwd)
for name in "$@"; do
  BLESS_DIRECT=1 python3 gen.py
  i=0
  until RUST_MIN_STACK=16777216 OMNIGRAPH_GQ_BLESS=1 "$root/target/debug/omnigraph-gqt" "$name.gqt" >/tmp/bless.log 2>&1; do
    i=$((i+1)); grep -q "bless: expect rewritten" /tmp/bless.log || { cat /tmp/bless.log; exit 1; }
    [ $i -lt 20 ] || exit 1
  done
  python3 gen.py harvest "$name"
done
python3 gen.py
