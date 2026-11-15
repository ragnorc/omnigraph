#!/bin/bash
# Starts RustFS (RUSTFS_BIN) and a latency proxy, builds a graph on it once,
# then times the request-cost shapes through the proxy under each profile pin.
# usage: realstore.sh <persons> <reps> <delay_ms>
set -u
persons=$1 reps=$2 delay=$3
export AWS_ACCESS_KEY_ID=omnigraphci AWS_SECRET_ACCESS_KEY=omnigraphci-secret AWS_REGION=us-east-1
export AWS_ALLOW_HTTP=true AWS_S3_FORCE_PATH_STYLE=true
RUSTFS_ACCESS_KEY=omnigraphci RUSTFS_SECRET_KEY=omnigraphci-secret RUSTFS_ADDRESS=127.0.0.1:9000 \
  RUSTFS_CONSOLE_ENABLE=false "${RUSTFS_BIN:-rustfs}" /tmp/rustfs-data > /tmp/rustfs.log 2>&1 &
rustfs=$!
python3 -I "$(dirname "$0")/latency_proxy.py" 9100 9000 "$delay" &
proxy=$!
trap 'kill $rustfs $proxy 2>/dev/null; wait 2>/dev/null' EXIT
sleep 4
curl -s --aws-sigv4 "aws:amz:us-east-1:s3" --user omnigraphci:omnigraphci-secret -X PUT http://127.0.0.1:9000/omnigraph-exp -o /dev/null -w "bucket %{http_code}\n"
uri=s3://omnigraph-exp/g$persons
bin="$(cd "$(dirname "$0")/../../.." && pwd)/target/release/examples/request_cost_calibrate"
if [ ! -e /tmp/rustfs-built-$persons ]; then
  AWS_ENDPOINT_URL=http://127.0.0.1:9000 AWS_ENDPOINT_URL_S3=http://127.0.0.1:9000 $bin build $uri $persons && touch /tmp/rustfs-built-$persons
fi
AWS_ENDPOINT_URL=http://127.0.0.1:9100 AWS_ENDPOINT_URL_S3=http://127.0.0.1:9100 $bin shapes $uri $reps 12000 48000
