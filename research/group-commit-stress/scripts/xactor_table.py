#!/usr/bin/env python3
"""Throughput table for the cross-actor matrix: one row per served run.

    xactor_table.py served/xa-on-a1 served/xa-one-a1 ...

writes/s = acknowledged writes / window; p50/p99 over acknowledged writes;
writes/commit = acknowledged writes / distinct commits they name; mixed =
share of the run's multi-write commits whose writes have more than one actor;
attr = violations of the HTTP and engine attribution checks.
"""
import json
import os
import re
import sys


def pct(values, q):
    if not values:
        return 0.0
    values = sorted(values)
    return values[max(1, min(len(values), int(-(-q * len(values) // 1)))) - 1]


def row(path):
    label = os.path.basename(path.rstrip("/"))
    ops, secs = [], None
    for line in open(os.path.join(path, "history.jsonl")):
        d = json.loads(line)
        if d.get("type") == "config":
            secs = d["args"]["secs"]
        elif d.get("type") == "op":
            ops.append(d)
    ok = [o for o in ops if o.get("outcome") == "ok"]
    lat = [(o["completed_us"] - o["invoked_us"]) / 1000 for o in ok]
    commits = {o["commit"] for o in ok if o.get("commit")}
    batch = mixed = 0
    try:
        listed = json.load(open(os.path.join(path, "commits.json")))["commits"]
        for c in listed:
            if c.get("writes"):
                batch += 1
                if len({w.get("actor_id") for w in c["writes"]}) > 1:
                    mixed += 1
    except (OSError, ValueError, KeyError):
        pass
    attr = []
    for name in ("attr-http.txt", "attr-engine.txt"):
        try:
            text = open(os.path.join(path, name)).read()
        except OSError:
            attr.append("-")
            continue
        found = re.findall(r'"violations":\s*(\d+)', text)
        attr.append(found[-1] if found else "?")
    check = "?"
    try:
        text = open(os.path.join(path, "check.txt")).read()
        found = re.findall(r'"violations":\s*(\d+)', text)
        check = found[-1] if found else "?"
    except OSError:
        pass
    refused = sum(1 for o in ops if o.get("outcome") == "refused")
    in_doubt = sum(1 for o in ops if o.get("outcome") == "in_doubt")
    return (f"| {label} | {len(ok) / secs:.1f} | {pct(lat, 0.5):.0f} | {pct(lat, 0.99):.0f} | "
            f"{len(ok) / max(1, len(commits)):.2f} | {batch} | {mixed} | {refused}/{in_doubt} | {check} | {'/'.join(attr)} |")


print("| run | writes/s | p50 ms | p99 ms | writes/commit | batch commits | mixed-actor | refused/in-doubt | model | attr http/engine |")
print("|---|---|---|---|---|---|---|---|---|---|")
for path in sys.argv[1:]:
    try:
        print(row(path))
    except Exception as error:  # noqa: BLE001
        print(f"| {os.path.basename(path)} | error: {error} |")
