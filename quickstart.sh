#!/bin/bash
# usage: smoke.sh <omnigraph-binary> <workdir>
set -euo pipefail
OG="$1"; W="$2"; rm -rf "$W"; mkdir -p "$W"; cd "$W"
cat > schema.pg <<'X'
node Source {
  slug: String @key
  title: String
}

node Claim {
  slug: String @key
  statement: String
}

edge Supports: Source -> Claim
X
cat > evidence.jsonl <<'X'
{"type":"Claim","data":{"slug":"lower-latency","statement":"The migration reduced request latency."}}
{"type":"Source","data":{"slug":"load-test","title":"Load test report"}}
{"type":"Source","data":{"slug":"production-metrics","title":"Production metrics"}}
{"edge":"Supports","from":"load-test","to":"lower-latency"}
{"edge":"Supports","from":"production-metrics","to":"lower-latency"}
X
cat > queries.gq <<'X'
query sources_for_claim($claim: String) {
  match {
    $source: Source
    $claim_node: Claim { slug: $claim }
    $source supports $claim_node
  }
  return { $source.title as source }
  order { source asc }
}
X
cat > benchmark.jsonl <<'X'
{"type":"Source","data":{"slug":"independent-benchmark","title":"Independent benchmark"}}
{"edge":"Supports","from":"independent-benchmark","to":"lower-latency"}
X
set -x
"$OG" --version
"$OG" init --schema schema.pg graph.omni
"$OG" load --data evidence.jsonl --mode overwrite --target ./graph.omni
"$OG" query sources_for_claim --query queries.gq --params '{"claim":"lower-latency"}' --format table --target ./graph.omni
"$OG" branch create review/add-benchmark --target ./graph.omni
"$OG" load --data benchmark.jsonl --mode append --branch review/add-benchmark --target ./graph.omni
"$OG" query sources_for_claim --query queries.gq --params '{"claim":"lower-latency"}' --branch review/add-benchmark --target ./graph.omni
"$OG" branch merge review/add-benchmark --into main --target ./graph.omni
"$OG" query sources_for_claim --query queries.gq --params '{"claim":"lower-latency"}' --format table --target ./graph.omni
"$OG" branch list --target ./graph.omni
"$OG" version --target ./graph.omni --json 2>/dev/null || "$OG" version --json
