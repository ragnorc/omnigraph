#!/usr/bin/env bash
# F5 (oracle 4, doc, minor): the upgrade guide (docs/user/queries/upgrading-to-gq3.md, Schemas) says
# `schema plan`, `schema apply` and `cluster plan` "name each one the schema holds, under
# `one_way_constructs`". A plan of the stored schema itself (no step) names none, though the stored
# schema holds `@index(fulltext, analyzer = ...)`; the same text after any one-step change lists both
# constructs again. (No harm on its own: a no-step apply publishes nothing.)
#
#   GQ2=/path/to/gq2/omnigraph NEW=/path/to/new/omnigraph bash zero_step_plan_lists_no_one_way.sh
set -uo pipefail
: "${GQ2:?}"; : "${NEW:?}"
W=$(mktemp -d); export OMNIGRAPH_HOME="$W/home" RUST_MIN_STACK=16777216
printf 'node Doc {\n    k: String @key\n    body: String @index\n}\n' > "$W/s0.pg"
printf 'node Doc {\n    k: String @key\n    body: String @index(fulltext, analyzer = "standard_v1")\n}\n' > "$W/s1.pg"
G="$W/g"; P='import json,sys; j=json.load(sys.stdin); print("steps", [s["kind"] for s in j.get("steps", [])], "one_way_constructs", [c["construct"] for c in j.get("one_way_constructs", [])])'
"$GQ2" init --schema "$W/s0.pg" "$G" >/dev/null 2>&1
echo -n "plan s1 over s0:   "; "$NEW" schema plan --schema "$W/s1.pg" --target "$G" --json 2>/dev/null | python3 -c "$P"
echo -n "apply s1:          "; "$NEW" schema apply --schema "$W/s1.pg" --target "$G" --json 2>/dev/null | python3 -c "$P"
echo -n "plan s1 again:     "; "$NEW" schema plan --schema "$W/s1.pg" --target "$G" --json 2>/dev/null | python3 -c "$P"
echo -n "GQ 2 opens it:     "; "$GQ2" snapshot --target "$G" --json 2>/dev/null | python3 -c 'import json,sys; j=json.load(sys.stdin); print(j.get("code"), j.get("error", "opened")[:90])'
