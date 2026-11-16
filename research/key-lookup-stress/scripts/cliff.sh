#!/usr/bin/env bash
# Cell 3 (the cliff) and cell 4 (cost per key space) on release binaries,
# local disk. One frozen tail per length is built by the key-lookup binary
# (one 10-row insert per fragment, no maintenance) and copied; every binary
# then measures a cold process on a fresh copy of the same bytes. Warm
# (the measuring process wrote the tail itself) is measured during the
# builds of the key-lookup binary and of the prototype.
#   cliff.sh <kind> <checkpoints> <seed_rows> <out.jsonl>
set -uo pipefail
KIND=$1; CHECKPOINTS=$2; SEED=$3; OUT=$4
BINS=/srv/bins; WORK=/srv/klc/work/$KIND; TAILS=/srv/klc/tails/$KIND
mkdir -p "$WORK" "$TAILS"
common="KLC_KIND=$KIND KLC_SEED_ROWS=$SEED KLC_ROWS_PER_WRITE=10 KLC_MEASURE_WRITES=${MW:-40} KLC_MEASURE_SECS=${MS:-60} KLC_OUT=$OUT"
run() { # label bin env...
  local label=$1 bin=$2; shift 2
  echo "== $label" >&2
  env $common KLC_LABEL=$label "$@" timeout ${TO:-3600} "$BINS/$bin/klc" --ignored --nocapture key_lookup_cliff 2>&1 | grep -E '^\[klc\]|panicked' >&2
}
# 1. Build the tails with the key-lookup binary (warm measurements).
rm -rf "$WORK/new-build"
run new-warm new KLC_MODE=build KLC_URI=$WORK/new-build KLC_CHECKPOINTS=$CHECKPOINTS KLC_COPY_TO=$TAILS
# 2. Cold on a copy, every binary.
for n in ${CHECKPOINTS//,/ }; do
  for variant in main proto new new-nomap new-fill; do
    bin=$variant; extra=()
    case $variant in
      new-nomap) bin=new; extra=(OMNIGRAPH_KNOWN_KEYS=0) ;;
      new-fill) bin=new; extra=(OMNIGRAPH_KNOWN_KEYS_FILL_FROM_SCAN=1) ;;
    esac
    rm -rf "$WORK/cold"; cp -a "$TAILS/tail-$n" "$WORK/cold"
    run "$variant-cold" "$bin" KLC_MODE=measure KLC_URI=$WORK/cold KLC_TAIL_ROWS=$n KLC_PHASE=cold "${extra[@]}"
  done
done
# 3. The prototype builds its own tail (its warm case).
if [ "${PROTO_WARM:-1}" = 1 ]; then
  rm -rf "$WORK/proto-build"
  run proto-warm proto KLC_MODE=build KLC_URI=$WORK/proto-build KLC_CHECKPOINTS=$CHECKPOINTS
fi
