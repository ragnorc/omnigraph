#!/usr/bin/env bash
# Smallest reproductions of the class (b) and (c) findings in
# migration-matrix/README.md. Each builds its graph with OLD and runs NEW on
# it; nothing outside a fresh temporary directory is touched.
#
#   OLD=/path/to/old/omnigraph NEW=/path/to/new/omnigraph \
#   OLD_SERVER=/path/to/old/omnigraph-server NEW_SERVER=/path/to/new/omnigraph-server \
#     bash repro.sh [name ...]
#
# Names: l2_lockout list_index_lockout fulltext_lockout stored_query_file
#        fuzzy_fix lint_json_fix index_build_advice stopword http_load_mode
#        cli_mode_json
set -uo pipefail
: "${OLD:?set OLD to the released omnigraph binary}"
: "${NEW:?set NEW to the GQ 3 omnigraph binary}"
export RUST_MIN_STACK=16777216
W=$(mktemp -d)
export OMNIGRAPH_HOME="$W/home"
say() { printf '\n=== %s\n' "$*"; }
run() { printf '$ %s\n' "$*"; "$@" 2>&1 | sed -e 's/\x1b\[[0-9;]*m//g' | grep -v '^target ' | head -"${LINES_MAX:-25}"; echo "[exit ${PIPESTATUS[0]}]"; }

doc_graph() { # $1 dir, $2 schema text
  mkdir -p "$1"; printf '%s\n' "$2" > "$1/s.pg"
  "$OLD" init --schema "$1/s.pg" "$1/g" >/dev/null 2>&1
  printf '%s\n' "$3" > "$1/d.jsonl"
  "$OLD" load --data "$1/d.jsonl" --mode merge --target "$1/g" --json >/dev/null 2>&1
}

l2_lockout() {
  say "an explicit l2 distance: NEW plans 0 steps, applies, and OLD can no longer open the graph"
  local d="$W/l2"
  doc_graph "$d" 'node Doc { slug: String @key  emb: Vector(2) }' '{"type":"Doc","data":{"slug":"a","emb":[0.5,0.5]}}'
  printf '%s\n' 'node Doc { slug: String @key  emb: Vector(2, distance = "l2") }' > "$d/next.pg"
  run "$NEW" schema plan --schema "$d/next.pg" --target "$d/g" --json
  run "$NEW" schema apply --schema "$d/next.pg" --target "$d/g" --json
  run "$OLD" snapshot --target "$d/g" --json
}

list_index_lockout() {
  say "@index on a list property (LABEL_LIST): NEW applies it, OLD refuses the whole graph with a parse error"
  local d="$W/list"
  doc_graph "$d" 'node T { slug: String @key  tags: [String] }' '{"type":"T","data":{"slug":"a","tags":["x"]}}'
  printf '%s\n' 'node T { slug: String @key  tags: [String] @index }' > "$d/next.pg"
  run "$NEW" schema apply --schema "$d/next.pg" --target "$d/g" --json
  run "$OLD" snapshot --target "$d/g" --json
}

fulltext_lockout() {
  say "the respelling the RFC prescribes, @index(fulltext): OLD then refuses every read and write, not only the index"
  local d="$W/ft"
  doc_graph "$d" 'node Doc { slug: String @key  title: String @index  n: I32 }' '{"type":"Doc","data":{"slug":"a","title":"graph","n":1}}'
  printf '%s\n' 'node Doc { slug: String @key  title: String @index(fulltext)  n: I32 }' > "$d/next.pg"
  run "$NEW" schema apply --schema "$d/next.pg" --target "$d/g" --json
  printf '%s\n' 'query n() { match { $d: Doc } return { $d.n } }' > "$d/q.gq"
  run "$OLD" query n --query "$d/q.gq" --target "$d/g" --format json
}

stored_query_file() {
  : "${NEW_SERVER:?set NEW_SERVER}"; : "${OLD_SERVER:?set OLD_SERVER}"
  say "one refused declaration makes every stored query of its file unavailable, each with that declaration's diagnostic"
  local d="$W/sq"; mkdir -p "$d/src/queries" "$d/root"
  printf '%s\n' 'node Doc { slug: String @key  n: I32 }' > "$d/src/schema.pg"
  cat > "$d/src/queries/q.gq" <<'EOF'
query fine() {
  match { $d: Doc }
  return { $d.slug }
}

query old_form() {
  match { $d: Doc { slug: "a" } }
  return { $d.n }
}
EOF
  cat > "$d/src/cluster.yaml" <<'EOF'
version: 1
state: { backend: cluster, lock: true }
graphs:
  g:
    schema: schema.pg
    queries: queries/
EOF
  "$OLD" cluster apply --target "$d/root" --config "$d/src" --json >/dev/null 2>&1
  printf '%s' '`fine` compiles under NEW on its own: '; printf '%s\n' 'query fine() { match { $d: Doc } return { $d.slug } }' > "$d/fine.gq"
  "$NEW" lint --schema "$d/src/schema.pg" --query "$d/fine.gq" --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])'
  LINES_MAX=60 run "$NEW" queries list --target "$d/root" --graph g --json
}

fuzzy_fix() {
  say "fuzzy(field, q, n): the printed fix is a placeholder with max_edits 2, and no edit"
  local d="$W/fz"
  doc_graph "$d" 'node Doc { slug: String @key  title: String @index }' '{"type":"Doc","data":{"slug":"a","title":"zebra"}}'
  printf '%s\n' 'query f($q: String) { match { $d: Doc } where fuzzy($d.title, $q, 1) return { $d.slug } }' > "$d/q.gq"
  LINES_MAX=40 run "$NEW" query f --query "$d/q.gq" --params '{"q":"zebre"}' --target "$d/g" --format json
}

lint_json_fix() {
  say "lint --json drops a parse refusal's fix and edits, which the same text's query refusal carries"
  local d="$W/lj"
  doc_graph "$d" 'node Doc { slug: String @key  n: I32 }' '{"type":"Doc","data":{"slug":"a","n":1}}'
  printf '%s\n' 'query q() { match { $d: Doc  $d.n > 0 } return { $d.slug } }' > "$d/q.gq"
  run "$NEW" lint --schema "$d/s.pg" --query "$d/q.gq" --json
  LINES_MAX=40 run "$NEW" query q --query "$d/q.gq" --target "$d/g" --format json
}

index_build_advice() {
  say "NEW index build on an OLD graph with no index pass tells the operator to run the one-way full-text rebuild"
  local d="$W/ib"
  doc_graph "$d" 'node Doc { slug: String @key  title: String @index }' '{"type":"Doc","data":{"slug":"a","title":"graph"}}'
  LINES_MAX=40 run "$NEW" index build --target "$d/g" --json
  run "$NEW" index rebuild --kind full-text --target "$d/g" --json
  printf '%s\n' 'query s($q: String) { match { $d: Doc  search($d.title, $q) } return { $d.slug } }' > "$d/q.gq"
  run "$OLD" query s --query "$d/q.gq" --params '{"q":"graph"}' --target "$d/g" --format json
}

stopword() {
  say "a lexical query of stop words: OLD answers no rows, NEW refuses with T20 and no fix"
  local d="$W/sw"
  doc_graph "$d" 'node Doc { slug: String @key  title: String @index }' '{"type":"Doc","data":{"slug":"a","title":"the graph"}}'
  "$OLD" index build --target "$d/g" --json >/dev/null 2>&1
  printf '%s\n' 'query s($q: String) { match { $d: Doc  search($d.title, $q) } return { $d.slug } }' > "$d/old.gq"
  printf '%s\n' 'query s($q: String) { match { $d: Doc } where match_terms($d.title, terms($q, mode: any)) return { $d.slug } }' > "$d/new.gq"
  run "$OLD" query s --query "$d/old.gq" --params '{"q":"the"}' --target "$d/g" --format json
  run "$NEW" query s --query "$d/new.gq" --params '{"q":"the"}' --target "$d/g" --format json
}

http_load_mode() {
  : "${NEW_SERVER:?set NEW_SERVER}"; : "${OLD_SERVER:?set OLD_SERVER}"
  say "the HTTP load response reports mode replace where OLD reported merge, for the same request and contract version"
  local d="$W/hl"; mkdir -p "$d/src" "$d/root"
  printf '%s\n' 'node Doc { slug: String @key }' > "$d/src/schema.pg"
  printf '%s\n' 'version: 1' 'state: { backend: cluster, lock: true }' 'graphs:' '  g:' '    schema: schema.pg' > "$d/src/cluster.yaml"
  "$OLD" cluster apply --target "$d/root" --config "$d/src" --json >/dev/null 2>&1
  for side in OLD NEW; do
    local bin; bin=$([ $side = OLD ] && echo "$OLD_SERVER" || echo "$NEW_SERVER")
    OMNIGRAPH_UNAUTHENTICATED=1 "$bin" --cluster "file://$d/root" --bind 127.0.0.1:18299 --unauthenticated >"$d/$side.log" 2>&1 &
    local pid=$!; for _ in $(seq 50); do curl -fs http://127.0.0.1:18299/healthz >/dev/null && break; sleep 0.2; done
    printf '%s: ' $side
    curl -s -X POST http://127.0.0.1:18299/graphs/g/load -H 'Omnigraph-Http-Api: 0.14' -H 'content-type: application/json' \
      --data '{"data":"{\"type\":\"Doc\",\"data\":{\"slug\":\"x\"}}"}' | python3 -c 'import json,sys; d=json.load(sys.stdin); print("mode =", d.get("mode"), d.get("error",""))'
    kill $pid; wait $pid 2>/dev/null
    "$NEW" cluster status --target "$d/root" --json 2>/dev/null | python3 -c 'import json,sys; l=json.load(sys.stdin).get("lock_id"); print(l or "")' | xargs -r -I{} "$NEW" cluster force-unlock {} --target "$d/root" >/dev/null 2>&1
  done
}

cli_mode_json() {
  say "load --mode merge --json: the refusal is a usage error on stderr (exit 64) with no JSON, unlike load csv's coded Q004"
  local d="$W/cm"
  doc_graph "$d" 'node Doc { slug: String @key }' '{"type":"Doc","data":{"slug":"a"}}'
  run "$NEW" load --data "$d/d.jsonl" --mode merge --target "$d/g" --json
}

ALL=(l2_lockout list_index_lockout fulltext_lockout stored_query_file fuzzy_fix lint_json_fix index_build_advice stopword http_load_mode cli_mode_json)
for name in "${@:-${ALL[@]}}"; do "$name"; done
echo; echo "work directory: $W"
