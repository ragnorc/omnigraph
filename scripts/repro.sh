#!/usr/bin/env bash
# On an s3:// cluster root whose prefix has a space, the cluster's own objects
# go under the percent-encoded spelling (my%20data/) and the graph's tables
# under the decoded one (my data/). Removing the graph then answers complete
# and deletes nothing, and declaring the graph again is refused with
# deployment_outcome_unknown. The same steps on a plain prefix delete the
# graph and re-add it.
# Needs RustFS (or any S3 store) on 127.0.0.1:9000 with bucket omnigraph-ci.
# Usage: OG=<cli> repros/s3_root_encoded_prefix_removal.sh
set -u
OG=${OG:-omnigraph}
export AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID:-omnigraphci} AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY:-omnigraphci-secret}
export AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1 AWS_ALLOW_HTTP=true AWS_S3_FORCE_PATH_STYLE=true
export AWS_ENDPOINT_URL=${AWS_ENDPOINT_URL:-http://127.0.0.1:9000} AWS_ENDPOINT_URL_S3=${AWS_ENDPOINT_URL:-http://127.0.0.1:9000}
D=$(mktemp -d); cd "$D"; mkdir with without
printf 'node X {\n  slug: String @key\n}\n' > with/g.pg
printf 'version: 1\nmetadata:\n  name: repro\ngraphs:\n  g:\n    schema: g.pg\n' > with/cluster.yaml
printf 'version: 1\nmetadata:\n  name: repro\ngraphs: {}\n' > without/cluster.yaml
unlock() { lid=$("$OG" cluster status --target "$1" --json 2>/dev/null | python3 -c 'import sys,re; m=re.findall(r"\"lock_id\": \"([^\"]+)\"", sys.stdin.read()); print(m[0] if m else "")'); [ -n "$lid" ] && "$OG" cluster force-unlock "$lid" --target "$1" --yes >/dev/null 2>&1; }
apply() { "$OG" cluster apply --target "$1" --config "$2" --json 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("status") or ("refused: " + (d.get("error") or "; ".join(x["code"] + ": " + x["message"][:160] for x in d.get("diagnostics") or []))[:200]))'; unlock "$1"; }
keys() { uvx --from awscli aws --endpoint-url "$AWS_ENDPOINT_URL" s3api list-objects-v2 --bucket omnigraph-ci --prefix "$1/" --query 'Contents[].Key' --output text 2>/dev/null | tr '\t' '\n' | sed "s|^$1/||" | awk -F/ '{print "    " $1 "/" $2 "/" $3 "/" $4}' | sort | uniq -c; }
T=repro-$(date +%s)
for prefix in plain "my data"; do
  R="s3://omnigraph-ci/$T/$prefix/root"
  echo "== root $R"
  echo "apply with g:    $(apply "$R" with)"
  echo "apply without g: $(apply "$R" without)"
  echo "keys after removal:"; keys "$T" | grep -F "$(printf '%s' "$prefix" | cut -c1-2)"
  echo "apply with g:    $(apply "$R" with)"
done
