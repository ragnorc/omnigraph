#!/usr/bin/env bash
# F3 (oracle 5): under --json, `index build` and `index rebuild` report a refusal as colored plain
# text on stderr with no JSON and no code (exit 1), where `snapshot --json` returns coded JSON for the
# same refusal. The fuzz saw the same shape from `index build`, `index rebuild`, `optimize` and
# `cleanup` for every refusal reason it met: a missing branch, write contention ("write authority
# 'graph_head:main' changed during preparation", "collector branch 'main' changed during capture"),
# an older binary's whole-graph refusal after a one-way apply, the LABEL_LIST fold of a GQ 2 binary
# ("A btree index can only be created on a non-nested field"), and F2's duplicate index name.
# Same in GQ2-REL (v0.16.0), GQ2-MAIN and NEW.
#
#   BIN=/path/to/omnigraph bash maintenance_json_plain_text.sh
set -uo pipefail
: "${BIN:?set BIN to an omnigraph binary}"
W=$(mktemp -d); export OMNIGRAPH_HOME="$W/home" RUST_MIN_STACK=16777216
printf 'node Doc {\n    k: String @key\n    body: String @index\n}\n' > "$W/s.pg"
"$BIN" init --schema "$W/s.pg" "$W/g" >/dev/null 2>&1
for c in "snapshot --branch nope" "index build --branch nope" "index rebuild --kind full-text --branch nope"; do
  # shellcheck disable=SC2086
  "$BIN" $c --target "$W/g" --json > "$W/out" 2> "$W/err"; rc=$?
  printf '$ omnigraph %s --json -> exit %s, stdout: %s, stderr: %s\n' "$c" "$rc" \
    "$(tr -d '\n' < "$W/out" | cut -c1-90)" "$(grep -v '^target' "$W/err" | tr -d '\n' | sed 's/\x1b\[[0-9;]*m//g' | cut -c1-90)"
done
