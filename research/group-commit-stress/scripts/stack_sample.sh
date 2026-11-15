#!/usr/bin/env bash
# Poor man's profiler for gVisor (no perf): N gdb samples of all thread stacks.
#   stack_sample.sh <pid> <samples> <gap_s> <out_prefix>
PID=$1; N=$2; GAP=$3; OUT=$4
for i in $(seq 1 $N); do
  gdb -p $PID -batch -ex "set pagination off" -ex "thread apply all bt 60" > ${OUT}-$i.txt 2>&1
  sleep $GAP
done
