#!/usr/bin/env bash
# Cell 5: served run on RustFS +30 ms, group commit on, optimize every 30 s.
#   c5.sh <label> <bins> <writers> <secs>
cd "$(dirname "$0")/.."
/srv/venv/bin/python scripts/served_run.py "$1" --backend s3 --bins "$2" --optimize-secs 30 \
  --server-env OMNIGRAPH_WRITE_OBSERVERS=160 --server-env OMNIGRAPH_PER_ACTOR_INFLIGHT_MAX=64 \
  --server-env OMNIGRAPH_GLOBAL_INFLIGHT_MAX=160 --server-env OMNIGRAPH_INGRESS_INFLIGHT_MAX=256 \
  --server-env OMNIGRAPH_GROUP_COMMIT_STATS_SECS=30 \
  -- --writers "$3" --secs "$4" --actors ${ACTORS:-4} --seed 5 --reader-count 1 --metrics-secs 30 \
  --mix "${MIX:-fresh=30,ftag=15,dtag=5,ffollow=15,xfollow=4,fpair=10,npair=5,dpair=3,person=3,del_person=2,knows=4,hot=4}"
