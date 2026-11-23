#!/usr/bin/env bash
# After a nullable property is made required, every read of that property at a
# commit before the promotion fails with code internal: "stored node:P.b has
# Int32, nullable true; declared Int32, nullable false". This holds when the
# old commit stored no null, and for a whole-node `return { $p }`. Reads of the
# other properties at that commit succeed.
# Usage: OG=<cli> repros/promoted_property_history_read.sh
set -u
OG=${OG:-omnigraph}
D=$(mktemp -d); G=$D/g.omni
printf 'node P {\n  slug: String @key\n  b: I32?\n}\n' > "$D/s1.pg"
printf 'node P {\n  slug: String @key\n  b: I32\n}\n' > "$D/s2.pg"
"$OG" init --schema "$D/s1.pg" "$G" >/dev/null 2>&1
printf '%s\n' '{"type":"P","data":{"slug":"a","b":1}}' '{"type":"P","data":{"slug":"c","b":3}}' > "$D/d.jsonl"
C1=$("$OG" load --data "$D/d.jsonl" --mode append --target "$G" --json 2>/dev/null \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["commit"]["graph_commit_id"])')
echo "commit before promotion: $C1"
"$OG" schema apply --schema "$D/s2.pg" --target "$G" --json >/dev/null 2>&1 && echo "promotion applied"
for ret in '$p.slug' '$p.slug, $p.b' '$p'; do
  out=$("$OG" query -e "query x() { match { \$p: P } return { $ret } }" --snapshot "$C1" --target "$G" --json 2>/dev/null)
  echo "return { $ret } at $C1: $(echo "$out" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("rows") if "rows" in d else (d.get("code"), d.get("error")))')"
done
