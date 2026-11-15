#!/usr/bin/env bash
# Median of 5 request round trips through the toxiproxy S3 endpoint, in ms.
for i in 1 2 3 4 5; do curl -s -o /dev/null -m 20 -w "%{time_total}\n" http://127.0.0.1:19000/; done | sort -n | sed -n 3p | awk '{printf "%.1f\n", $1*1000}'
