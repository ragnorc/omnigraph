#!/usr/bin/env python3
"""Summarizes measure TSVs into one row per query step.

usage: summarize.py results/<label> [<case>...]
Prints, per case and step: pass (after_seed / cold / warm), query name,
then requests, waves, KB read and simulated ms under each model, and the
plan choices.
"""
import collections
import pathlib
import re
import sys

def load(path):
    rows = collections.defaultdict(dict)
    for line in path.read_text().splitlines()[1:]:
        env, seed, slot, step, lineno, kind, metric, value = line.split("\t")
        if slot != "step" or kind != "query":
            continue
        rows[int(step)][metric] = value
    return rows

def query_names(case):
    text = (pathlib.Path(__file__).parent / f"{case}.gqt").read_text()
    names, ordinal, passes, current = [], 0, [], "after_seed"
    for line in text.splitlines():
        if line == "--- restart":
            ordinal += 1
            current = "cold"
        elif line == "--- query":
            ordinal += 1
        elif line.startswith("query "):
            names.append((ordinal, line.split()[1].split("(")[0], current))
            if current == "cold":
                pass
    # after the restart: the first full pass is cold, the next warm
    out = {}
    seen = collections.Counter()
    for ordinal, name, _ in names:
        seen[name] += 1
        out[ordinal] = (name, {1: "after_seed", 2: "cold", 3: "warm"}[seen[name]])
    return out

def summarize(directory, case):
    unit = load(directory / f"{case}.unit.tsv")
    s3 = load(directory / f"{case}.s3-like.tsv")
    names = query_names(case)
    out = []
    for step in sorted(unit):
        name, phase = names.get(step, ("?", "?"))
        u, s = unit[step], s3.get(step, {})
        out.append(dict(case=case, step=step, name=name, phase=phase,
                        requests=int(u.get("requests", 0)),
                        waves=int(u.get("waves", 0)),
                        kb=float(u.get("bytes_read", 0)) / 1000,
                        unit_ms=float(u.get("simulated_us", 0)) / 1000,
                        s3_ms=float(s.get("simulated_us", 0)) / 1000,
                        s3_waves=int(s.get("waves", 0)),
                        plan=u.get("plan", "")))
    return out

def main():
    directory = pathlib.Path(sys.argv[1])
    cases = sys.argv[2:] or sorted({p.name.split(".")[0] for p in directory.glob("*.unit.tsv")})
    for case in cases:
        print(f"\n## {case}")
        print("| step | query | pass | requests | waves | KB read | unit ms | s3-like ms | plan |")
        print("|---:|---|---|---:|---:|---:|---:|---:|---|")
        for r in summarize(directory, case):
            plan = re.sub(r"; scan \$\w+: (sequential|index_probe)", "", r["plan"])
            print(f"| {r['step']} | {r['name']} | {r['phase']} | {r['requests']} | {r['waves']} | {r['kb']:.0f} | {r['unit_ms']:.0f} | {r['s3_ms']:.1f} | {plan} |")

if __name__ == "__main__":
    main()
