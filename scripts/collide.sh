#!/usr/bin/env bash
# Before/after evidence for #1527: apply a cluster at roots spelled with a
# space, a non-ASCII letter, a percent escape, and two roots that differ only
# in escaping (aAb and a%41b); list every key the bucket holds under the run.
set -u
OG=${OG:-omnigraph}
export AWS_ACCESS_KEY_ID=omnigraphci AWS_SECRET_ACCESS_KEY=omnigraphci-secret AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1 AWS_ALLOW_HTTP=true AWS_S3_FORCE_PATH_STYLE=true AWS_ENDPOINT_URL=http://127.0.0.1:9000 AWS_ENDPOINT_URL_S3=http://127.0.0.1:9000
D=$(mktemp -d); cd "$D"; mkdir with without
printf 'node X {\n  slug: String @key\n}\n' > with/g.pg
printf 'version: 1\nmetadata:\n  name: repro\ngraphs:\n  g:\n    schema: g.pg\n' > with/cluster.yaml
printf 'version: 1\nmetadata:\n  name: repro\ngraphs: {}\n' > without/cluster.yaml
unlock() { lid=$("$OG" cluster status --target "$1" --json 2>/dev/null | python3 -c 'import sys,re; m=re.findall(r"\"lock_id\": \"([^\"]+)\"", sys.stdin.read()); print(m[0] if m else "")'); [ -n "$lid" ] && "$OG" cluster force-unlock "$lid" --target "$1" --yes >/dev/null 2>&1; }
apply() { "$OG" cluster apply --target "$1" --config "$2" --json 2>/dev/null | python3 -c 'import json,sys
d=json.load(sys.stdin); print(d.get("status") or ("refused: " + (d.get("error") or "; ".join(x["code"] + ": " + x["message"] for x in d.get("diagnostics") or []))[:400]))'; unlock "$1"; }
keys() { uvx --from awscli aws --endpoint-url "$AWS_ENDPOINT_URL" s3api list-objects-v2 --bucket omnigraph-ci --prefix "$1/" --query 'Contents[].Key' --output text 2>/dev/null | tr '\t' '\n' | grep -v '^None$' | sed "s|^$1/||" | awk -F/ '{print "    " $1 "/" $2 "/" $3}' | sort | uniq -c; }
T=evidence-$(date +%s)
for prefix in "caf$(printf '\xc3\xa9')" "50%25off"; do
  R="s3://omnigraph-ci/$T/$prefix"
  echo "== root $R"
  echo "apply with g:    $(apply "$R" with)"
  echo "apply without g: $(apply "$R" without)"
  echo "apply with g:    $(apply "$R" with)"
done
echo "== two roots: s3://omnigraph-ci/$T/aAb then s3://omnigraph-ci/$T/a%41b"
echo "apply aAb with g:   $(apply "s3://omnigraph-ci/$T/aAb" with)"
echo "apply a%41b with g: $(apply "s3://omnigraph-ci/$T/a%41b" with)"
echo "keys under $T/ (count per first three segments):"; keys "$T"
