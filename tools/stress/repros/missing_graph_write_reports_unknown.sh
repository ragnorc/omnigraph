#!/usr/bin/env bash
# A direct write to a path that holds no graph (a typo, a graph not yet
# initialized) is refused before anything is written, but the CLI reports it
# as code outcome_unknown, disposition {"effects":"unknown","action":"reconcile"}
# and exits 2. The directory is never created. A read of the same path reports
# effects none. Expected (docs/user/operations/troubleshooting.md, "Failed
# data-write commands"): an error before the write submitted anything is
# effects none, with the refusal's own action, and exit 1.
# Usage: OG=<cli> repros/missing_graph_write_reports_unknown.sh
set -u
OG=${OG:-omnigraph}
D=$(mktemp -d); T=$D/missing/g.omni
printf 'node P {\n  slug: String @key\n}\n' > "$D/s.pg"
echo '{"type":"P","data":{"slug":"a"}}' > "$D/d.jsonl"
show() { python3 -c 'import json,sys
try:
  d=json.load(sys.stdin); print(d.get("code"), json.dumps(d.get("disposition")))
except Exception: print("no json")'; }
for args in "load --data $D/d.jsonl --mode append" \
            "mutate -e query_m" \
            "branch create x --from main" \
            "schema apply --schema $D/s.pg"; do
  if [ "$args" = "mutate -e query_m" ]; then
    out=$("$OG" mutate -e 'query m() { delete P where slug = "a" }' --target "$T" --json 2>/dev/null); rc=$?
  else
    # shellcheck disable=SC2086
    out=$("$OG" $args --target "$T" --json 2>/dev/null); rc=$?
  fi
  echo "${args%% *}: exit=$rc $(echo "$out" | show)"
done
out=$("$OG" query -e 'query q() { match { $p: P } return { $p.slug } }' --target "$T" --json 2>/dev/null); rc=$?
echo "query (read): exit=$rc $(echo "$out" | show)"
[ -e "$D/missing" ] && echo "path created" || echo "path still absent"
