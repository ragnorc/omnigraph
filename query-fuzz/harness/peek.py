"""Print the cases behind one signature: peek.py DATA CHECKS SIGNATURE-REGEX [N] [--sanction S]."""

import json
import re
import sys
from pathlib import Path


def load_case(data, seed, qi):
    world = None
    for line in open(Path(data) / "raw" / f"{seed}.jsonl"):
        rec = json.loads(line)
        if rec["record"] == "world":
            world = rec
        elif rec["qi"] == qi:
            return world, rec
    return world, None


def short(obs, keys=("status", "code", "columns", "types", "rows", "affected", "error", "fix")):
    if not obs:
        return None
    out = {k: obs.get(k) for k in keys if obs.get(k) is not None}
    if "rows" in out and len(json.dumps(out["rows"])) > 600:
        out["rows"] = json.dumps(out["rows"])[:600] + "…"
    if isinstance(out.get("error"), str):
        out["error"] = out["error"][:300]
    return out


def main():
    data, checks, pat = sys.argv[1:4]
    n = int(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4].isdigit() else 2
    sanction = sys.argv[sys.argv.index("--sanction") + 1] if "--sanction" in sys.argv else None
    show_world = "--world" in sys.argv
    shown = 0
    for line in open(checks):
        c = json.loads(line)
        if not re.search(pat, c["signature"]):
            continue
        if sanction is not None and str(c["sanction"]) != sanction:
            continue
        world, case = load_case(data, c["seed"], c["qi"])
        print("=" * 100)
        print(f"seed {c['seed']} qi {c['qi']} o{c['oracle']} {c['signature']} [{c['sanction']}] {c['detail'][:300]}")
        if show_world:
            print(world["schema"])
            print(world["data"][:1500])
        print(case["text"], json.dumps(case["params"]))
        print("E2   ", json.dumps(short(case["e2"]), ensure_ascii=False)[:1500])
        print("NEW  ", json.dumps(short(case["new"]), ensure_ascii=False)[:1500])
        fx = case.get("fix") or {}
        print("FIX  ", fx.get("status"), fx.get("applied"), json.dumps(fx.get("unfixed"))[:600])
        if fx.get("text") != case["text"]:
            print(fx.get("text"))
        if case.get("fix_review"):
            print("FIXR ", case["fix_review"].get("status"), case["fix_review"].get("applied"))
        if case.get("fixed"):
            print("FIXED", json.dumps(short(case["fixed"]), ensure_ascii=False)[:1500])
        if case["kind"] == "write":
            for side in ("e2", "new", "fixed"):
                if case.get(side) and isinstance(case[side].get("export"), list):
                    print(f"EXPORT {side}:", "\n   ".join(case[side]["export"])[:1200])
        if case.get("closure"):
            print("CLOSURE", case["closure"])
        shown += 1
        if shown >= n:
            break


if __name__ == "__main__":
    main()
