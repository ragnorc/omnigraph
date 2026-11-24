#!/usr/bin/env python3
"""Run one repro directory (or all of them) on EDITION-2 and NEW and print what each answered.

    repro/run.py [DIR ...] [--e2 BIN] [--new BIN]

A repro directory holds schema.pg, data.jsonl, query.gq and params.json (and,
from the shrinker, case.json with the query's kind). The script builds one
graph per binary, runs the text on both, runs NEW's `lint --fix` (then
`--review-fixes` where it stops), runs NEW on the rewrite, and prints each
answer: status, code, rows or affected counts, and for a write the export.
Defaults: /src/bin/e2-omnigraph and /src/bin/new-omnigraph (see README.md).
"""

import json
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "harness"))
import replay  # noqa: E402
import shrink  # noqa: E402


def show(label, obs):
    if obs is None:
        return
    keep = {k: obs.get(k) for k in ("status", "code", "error", "columns", "rows", "affected") if obs.get(k) is not None}
    if isinstance(keep.get("error"), str):
        keep["error"] = " ".join(keep["error"].split())[:400]
    print(f"  {label}: {json.dumps(keep, ensure_ascii=False)}")
    if isinstance(obs.get("export"), list):
        print(f"  {label} export:")
        for line in obs["export"]:
            print("     ", line)


def run(d: Path):
    case_file = d / "case.json"
    meta = json.loads(case_file.read_text()) if case_file.exists() else {}
    query = (d / "query.gq").read_text()
    kind = meta.get("kind") or ("write" if re.search(r"^\s*(insert|update|delete)\b", query, re.M) else "read")
    params = json.loads((d / "params.json").read_text()) if (d / "params.json").exists() else {}
    case = shrink.Case((d / "schema.pg").read_text(), (d / "data.jsonl").read_text() if (d / "data.jsonl").exists() else "",
                       query, params, kind, meta.get("index_build", False))
    print(f"== {d.name}")
    print("\n".join("   | " + l for l in query.rstrip().splitlines()))
    o = shrink.observe(case)
    if o.get("setup_failed"):
        print("  setup failed:", o)
        return
    show("EDITION-2", o.get("e2"))
    show("NEW, unchanged", o.get("new"))
    fx = o.get("fix") or {}
    print(f"  NEW lint --fix: {fx.get('status')} applied={fx.get('applied')} unfixed={[u.get('code') for u in fx.get('unfixed') or [] if isinstance(u, dict)]}")
    if fx.get("status") is None and fx.get("raw"):
        print("    " + " ".join(fx["raw"].split())[:500])
    fr = o.get("fix_review")
    if fr:
        print(f"  NEW lint --fix --review-fixes: {fr.get('status')} applied={fr.get('applied')}")
    final = fr if fr and fr.get("status") == "fixed" else fx
    if final.get("text") and final["text"] != query:
        print("\n".join("   > " + l for l in final["text"].rstrip().splitlines()))
    show("NEW, fixed text", o.get("fixed"))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--e2" in sys.argv:
        replay.E2 = sys.argv[sys.argv.index("--e2") + 1]
    if "--new" in sys.argv:
        replay.NEW = sys.argv[sys.argv.index("--new") + 1]
    args = [a for a in args if a not in (replay.E2, replay.NEW)]
    dirs = [Path(a) for a in args] or sorted(p for p in HERE.iterdir() if (p / "query.gq").exists())
    for d in dirs:
        run(d)


if __name__ == "__main__":
    main()
