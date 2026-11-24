#!/usr/bin/env bash
# F7 (oracle 4, doc): the upgrade guide (upgrading-to-gq3.md, Schemas) and the RFC's Reverting bullet
# say that after a schema apply stores a one-way construct, "only the backup taken before the upgrade
# restores" an earlier binary's access. NEW applying a schema without the construct restores it too:
# `@index(fulltext, analyzer = ...)` respelled back to a plain `@index` (or the field dropped) is one
# step, publishes, and GQ 2 then opens, reads, writes and exports the graph; its full-text reads are
# refused until its own `index rebuild --kind full-text`, as after NEW's rebuild. (Not every construct
# can be taken back this way: removing `@index` from a list is refused, and `distance = "l2"` written
# back to an omitted distance is a no-step apply that publishes nothing.)
#
#   GQ2=/path/to/gq2/omnigraph NEW=/path/to/new/omnigraph bash one_way_undone_by_new_apply.sh
set -uo pipefail
: "${GQ2:?}"; : "${NEW:?}"
W=$(mktemp -d); export OMNIGRAPH_HOME="$W/home" RUST_MIN_STACK=16777216
printf 'node Doc {\n    k: String @key\n    body: String @index\n}\n' > "$W/s0.pg"
printf 'node Doc {\n    k: String @key\n    body: String @index(fulltext, analyzer = "standard_v1")\n}\n' > "$W/s1.pg"
printf '{"type":"Doc","data":{"k":"a","body":"graph running"}}\n{"type":"Doc","data":{"k":"b","body":"river"}}\n' > "$W/d.jsonl"
printf 'query s($q: String) {\n  match {\n    $d: Doc\n    search($d.body, $q)\n  }\n  return { $d.k }\n  order { $d.k }\n}\n' > "$W/q2.gq"
G="$W/g"; P='import json,sys; j=json.load(sys.stdin); print("applied", j.get("applied"), "steps", [s["kind"] for s in j.get("steps", [])], "one_way_constructs", [c["construct"] for c in j.get("one_way_constructs", [])])'
open() { echo -n "$1: "; "$GQ2" snapshot --target "$G" --json 2>/dev/null | python3 -c 'import json,sys; j=json.load(sys.stdin); print("opens" if "datasets" in j else j.get("error","")[:100])'
  echo -n "  GQ 2 search: "; "$GQ2" query s --query "$W/q2.gq" --params '{"q":"graph"}' --target "$G" --format json 2>/dev/null | python3 -c 'import json,sys; j=json.load(sys.stdin); print(j.get("row_count"), (j.get("error") or "")[:90])'; }
"$GQ2" init --schema "$W/s0.pg" "$G" >/dev/null 2>&1; "$GQ2" load --data "$W/d.jsonl" --mode merge --target "$G" --json >/dev/null 2>&1
"$GQ2" index build --target "$G" --json >/dev/null 2>&1
open "built by GQ 2"
echo -n "NEW apply @index(fulltext, analyzer): "; "$NEW" schema apply --schema "$W/s1.pg" --target "$G" --json 2>/dev/null | python3 -c "$P"
"$NEW" index build --target "$G" --json >/dev/null 2>&1
open "after the one-way apply and NEW's index build"
echo -n "NEW apply plain @index again: "; "$NEW" schema apply --schema "$W/s0.pg" --target "$G" --json 2>/dev/null | python3 -c "$P"
open "after the second apply"
"$GQ2" index rebuild --kind full-text --target "$G" --json >/dev/null 2>&1
open "after GQ 2's own full-text rebuild"
