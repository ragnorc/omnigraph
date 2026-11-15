#!/usr/bin/env python3
"""Before/after table of two measure runs, one row per query step.

usage: compare.py results/<before> results/<after> [case...] [--passes warm,cold]
"""
import re
import sys
import pathlib
from summarize import summarize

def condense(plan):
    plan = plan.strip('"')
    parts = []
    for item in plan.split("; "):
        m = re.match(r"expand \$(\w+) (\w+) \$(\w+): (\w+)(?: ran (\w+))?", item)
        if m:
            src, edge, dst, mode, ran = m.groups()
            mode = {"indexed_scan": "idx", "csr": "csr"}[mode]
            ran = {"indexed_scan": "idx", "csr": "csr", None: "-"}[ran]
            parts.append(f"{src}>{dst} {mode}" + ("" if ran == mode else f"→{ran}"))
            continue
        m = re.match(r"hash join \$(\w+)", item)
        if m:
            parts.append(f"hj ${m.group(1)}")
            continue
        m = re.match(r"scan \$(\w+): id_lookup", item)
        if m:
            parts.append(f"lookup ${m.group(1)}")
            continue
        m = re.match(r"anti join \$(\w+): (\w+)", item)
        if m:
            parts.append(f"anti {m.group(2)}")
    return ", ".join(parts)

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    passes = {"warm", "cold"}
    for a in sys.argv[1:]:
        if a.startswith("--passes="):
            passes = set(a.split("=", 1)[1].split(","))
    before, after = pathlib.Path(args[0]), pathlib.Path(args[1])
    cases = args[2:] or sorted({p.name.split(".")[0] for p in after.glob("*.unit.tsv")})
    for case in cases:
        b = {r["step"]: r for r in summarize(before, case)}
        a = {r["step"]: r for r in summarize(after, case)}
        print(f"\n### {case}\n")
        print("| query | pass | plan before | plan after | requests | waves | KB read | unit ms | s3-like ms |")
        print("|---|---|---|---|---:|---:|---:|---:|---:|")
        for step in sorted(a):
            x, y = b[step], a[step]
            if y["phase"] not in passes:
                continue
            pb, pa = condense(x["plan"]), condense(y["plan"])
            same = pb == pa
            def pair(k, fmt="{:.0f}"):
                vb, va = fmt.format(x[k]), fmt.format(y[k])
                return vb if vb == va else f"{vb} → {va}"
            print(f"| {y['name']} | {y['phase']} | {pb} | {'same' if same else pa} | {pair('requests')} | {pair('waves')} | {pair('kb')} | {pair('unit_ms')} | {pair('s3_ms', '{:.1f}')} |")

if __name__ == "__main__":
    main()
