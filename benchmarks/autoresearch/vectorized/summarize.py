#!/usr/bin/env python3
"""Render instrument results as markdown.

  summarize.py <label>                 one table per fixture: query, rows, allocs, bytes, cpu, wall, per-operator rows/batches
  summarize.py <before> <after>        side-by-side deltas of allocs, bytes and cpu per (fixture, query)
"""
import json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(label):
    rows = {}
    for line in (HERE / "results" / f"{label}.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        rows[(r["fixture"], r["query"])] = r
    return rows


def fmt(n):
    return f"{n:,}"


def ops(r):
    parts = []
    for op in r.get("first", {}).get("operators", []):
        if op.get("status") != "executed":
            continue
        b = op.get("batches")
        parts.append(f"{op['operator'].replace('Exec','')}{'['+str(op['ran'])+']' if isinstance(op.get('ran'), str) else ''} {op['rows']}r/{b if b is not None else '?'}b")
    return "; ".join(parts)


def single(label):
    rows = load(label)
    fixtures = sorted({f for f, _ in rows}, key=lambda s: (len(s), s))
    for f in fixtures:
        print(f"\n### {f}\n")
        print("| query | rows | allocs | bytes | cpu ms (mean) | wall ms (median, min–max) | operators rows/batches |")
        print("|---|---:|---:|---:|---:|---:|---|")
        for (fx, q), r in sorted(rows.items(), key=lambda kv: list(rows).index(kv[0])):
            if fx != f:
                continue
            if "error" in r:
                print(f"| {q} | error | {r['error'][:60]} | | | | |")
                continue
            print(f"| {q} | {fmt(r['first']['rows'])} | {fmt(r['allocs']['median'])} | {fmt(r['bytes']['median'])} | {r['cpu_us']['mean']/1000:.1f} | {r['wall_us']['median']/1000:.1f} ({r['wall_us']['min']/1000:.1f}–{r['wall_us']['max']/1000:.1f}) | {ops(r)} |")


def pair(before, after):
    a, b = load(before), load(after)
    print(f"| fixture | query | allocs {before} | allocs {after} | Δ | bytes {before} | bytes {after} | Δ | cpu ms {before} | cpu ms {after} | wall ms {before} | wall ms {after} |")
    print("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for key in a:
        if key not in b or "error" in a[key] or "error" in b[key]:
            continue
        x, y = a[key], b[key]
        da = (y["allocs"]["median"] - x["allocs"]["median"]) / max(x["allocs"]["median"], 1) * 100
        db = (y["bytes"]["median"] - x["bytes"]["median"]) / max(x["bytes"]["median"], 1) * 100
        print(f"| {key[0]} | {key[1]} | {fmt(x['allocs']['median'])} | {fmt(y['allocs']['median'])} | {da:+.1f}% | {fmt(x['bytes']['median'])} | {fmt(y['bytes']['median'])} | {db:+.1f}% | {x['cpu_us']['mean']/1000:.1f} | {y['cpu_us']['mean']/1000:.1f} | {x['wall_us']['median']/1000:.1f} | {y['wall_us']['median']/1000:.1f} |")


if __name__ == "__main__":
    if len(sys.argv) == 2:
        single(sys.argv[1])
    else:
        pair(sys.argv[1], sys.argv[2])
