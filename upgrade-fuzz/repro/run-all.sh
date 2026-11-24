#!/usr/bin/env bash
# Run every repro against the three binaries; output.txt is one run.
#   REL=.../v0.16.0/omnigraph MAIN=.../gq2-main/omnigraph NEW=.../gq3/omnigraph bash run-all.sh > output.txt
# The JSON cases (*.json) replay through the fuzz harness: `python3 ../harness/ufreplay.py NAME.json`.
set -uo pipefail
cd "$(dirname "$0")"
: "${REL:?}"; : "${MAIN:?}"; : "${NEW:?}"
for b in REL MAIN; do echo "=== nearest_after_optimize.sh BIN=GQ2-$b (edition 2)"; BIN=${!b} EDITION=2 bash nearest_after_optimize.sh; done
echo "=== nearest_after_optimize.sh BIN=NEW"; BIN=$NEW EDITION=3 bash nearest_after_optimize.sh
for b in REL MAIN; do echo "=== index_build_duplicate_ngram.sh GQ2=GQ2-$b NEW=NEW"; GQ2=${!b} NEW=$NEW bash index_build_duplicate_ngram.sh | sed -E 's#/tmp/tmp\.[A-Za-z0-9]+#<tmp>#g; s#'"${!b}"'#<GQ2>#g; s#'"$NEW"'#<NEW>#g'; done
for b in REL MAIN NEW; do echo "=== maintenance_json_plain_text.sh BIN=$b"; BIN=${!b} bash maintenance_json_plain_text.sh; done
echo "=== old_fulltext_not_reported_pending.sh GQ2=GQ2-REL NEW=NEW"; GQ2=$REL NEW=$NEW bash old_fulltext_not_reported_pending.sh
echo "=== zero_step_plan_lists_no_one_way.sh GQ2=GQ2-REL NEW=NEW"; GQ2=$REL NEW=$NEW bash zero_step_plan_lists_no_one_way.sh
echo "=== memory_refusal_depends_on_layout.sh BIN=GQ2-MAIN (edition 2)"; BIN=$MAIN EDITION=2 bash memory_refusal_depends_on_layout.sh
echo "=== memory_refusal_depends_on_layout.sh BIN=NEW"; BIN=$NEW EDITION=3 bash memory_refusal_depends_on_layout.sh
