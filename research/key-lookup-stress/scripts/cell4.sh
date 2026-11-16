#!/usr/bin/env bash
# Cell 4: cost per key space.
#   unique: table size sweep (seed rows), 100-fragment tail, with and without
#           the implied @unique BTREE; cold on copies for main and the prototype.
#   edge:   tail sweep under @card(0..5), 60k seeded persons.
set -uo pipefail
OUT=$1
BINS=/srv/bins
run() { local label=$1 bin=$2; shift 2; echo "== $label" >&2
  env KLC_ROWS_PER_WRITE=10 KLC_MEASURE_WRITES=${MW:-40} KLC_MEASURE_SECS=${MS:-60} KLC_OUT=$OUT KLC_LABEL=$label "$@" \
    timeout ${TO:-3600} "$BINS/$bin/klc" --ignored --nocapture key_lookup_cliff 2>&1 | grep --line-buffered -E '^\[klc\]|panicked' >&2; }
for seed in ${SEEDS:-10000 100000 1000000}; do
  W=/srv/klc/work/unique-$seed; T=/srv/klc/tails/unique-$seed; rm -rf $W $T; mkdir -p $W $T
  run new-warm new2 KLC_KIND=unique KLC_SEED_ROWS=$seed KLC_MODE=build KLC_URI=$W/b KLC_CHECKPOINTS=1000 KLC_COPY_TO=$T
  run new-noindex-warm new2 KLC_KIND=unique KLC_SEED_ROWS=$seed KLC_MODE=build KLC_URI=$W/n KLC_CHECKPOINTS=1000 OMNIGRAPH_UNIQUE_IMPLIES_INDEX=0
  for v in main proto new2; do rm -rf $W/c; cp -a $T/tail-1000 $W/c
    run $v-cold $v KLC_KIND=unique KLC_SEED_ROWS=$seed KLC_MODE=measure KLC_URI=$W/c KLC_TAIL_ROWS=1000; done
  rm -rf $W
done
ET=${EDGE_TAILS:-1000,10000,50000}
W=/srv/klc/work/edge; T=/srv/klc/tails/edge; rm -rf $W $T; mkdir -p $W $T
run new-warm new2 KLC_KIND=edge KLC_SEED_ROWS=60000 KLC_MODE=build KLC_URI=$W/b KLC_CHECKPOINTS=$ET KLC_COPY_TO=$T
for n in ${ET//,/ }; do for v in main proto new2 new2-fill; do bin=$v; extra=()
  [ $v = new2-fill ] && { bin=new2; extra=(OMNIGRAPH_KNOWN_KEYS_FILL_FROM_SCAN=1); }
  rm -rf $W/c; cp -a $T/tail-$n $W/c
  run $v-cold $bin KLC_KIND=edge KLC_SEED_ROWS=60000 KLC_MODE=measure KLC_URI=$W/c KLC_TAIL_ROWS=$n "${extra[@]}"; done; done
