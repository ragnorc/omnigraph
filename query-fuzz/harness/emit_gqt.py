"""Write generated cases as `.gqt` files the cross-edition comparison runs.

Each case becomes `<name>.gqt`, its edition-2 text with the schema, the seed
data, the step and its expectations blessed from EDITION-2's GQT runner
(`OMNIGRAPH_GQ_BLESS=1`, the direct-engine environment), and, when NEW's
`lint --fix` rewrote it, `<name>.fixed.gqt`: the same case holding the
rewrite. `scripts/cross-edition.py run-generated` runs the first on both
runners and the second on the current one.

    emit_gqt.py --data DIR --out DIR (--seed S --qi I ... | --sample N | --repro DIR)
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import peek  # noqa: E402

E2_GQT = "/src/bin/e2-gqt"

RUNNER = """--- runner
timeout_ms: 10000
environments:
  - target: omnigraph-engine
    storage: local-filesystem
"""


def seed_section(data: str) -> str:
    """The seed as compact JSONL (the runner reads each line as one record)."""
    out = []
    for line in data.splitlines():
        if line.strip():
            out.append(json.dumps(json.loads(line), ensure_ascii=False, separators=(",", ":")))
    return "\n".join(out)


def expect_block(kind: str, text: str, obs: dict | None) -> str:
    if kind == "write" and obs and obs.get("status") == "error":
        code = obs.get("code") or ""
        needle = code if re.match(r"^[QTP][0-9]+$", code) else (obs.get("error") or "error").split(":")[-1].strip()[:40]
        return f"--- expect error: {needle}\n"
    if kind == "write":
        if obs and obs.get("status") == "ok" and obs.get("affected"):
            n, e = obs["affected"]
            return f"--- expect affected: nodes={n} edges={e}\n"
        return "--- expect ok\n"
    if obs and obs.get("status") == "error":
        code = obs.get("code") or ""
        needle = code if re.match(r"^[QTP][0-9]+$", code) else (obs.get("error") or "error").split(":")[-1].strip()[:40]
        return f"--- expect error: {needle}\n"
    ordered = re.search(r"\border\s*\{", text) is not None
    rows = (obs or {}).get("rows") or []
    body = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
    return f"--- expect {'ordered' if ordered else 'unordered'}\n{body}\n\n--- expect shape\n"


def case_text(schema: str, data: str, kind: str, text: str, params: dict, obs: dict | None, notes: list[str]) -> str:
    head = "# issue: none\n" + "".join(f"# notes: {n}\n" for n in notes) + "\n" + RUNNER + "\n"
    out = head + "--- schema\n" + schema.rstrip() + "\n\n"
    seed = seed_section(data)
    out += "--- seed\n" + (seed + "\n" if seed else "") + "\n"
    out += ("--- mutate\n" if kind == "write" else "--- query\n") + text.rstrip() + "\n\n"
    if params:
        out += "--- params\n" + json.dumps(params, ensure_ascii=False) + "\n\n"
    out += expect_block(kind, text, obs)
    return out


def bless(path: Path, tries: int = 4) -> bool:
    path = path.resolve()
    env = dict(os.environ, RUST_MIN_STACK="16777216")
    for _ in range(tries):
        res = subprocess.run([E2_GQT, str(path)], capture_output=True, text=True, env=env, cwd=str(path.parent))
        if res.returncode == 0:
            return True
        env["OMNIGRAPH_GQ_BLESS"] = "1"
        subprocess.run([E2_GQT, str(path)], capture_output=True, text=True, env=env, cwd=str(path.parent))
        env.pop("OMNIGRAPH_GQ_BLESS")
    res = subprocess.run([E2_GQT, str(path)], capture_output=True, text=True, env=env, cwd=str(path.parent))
    return res.returncode == 0


def emit(out: Path, name: str, schema, data, kind, text, params, e2obs, fixed_text, notes):
    out.mkdir(parents=True, exist_ok=True)
    p = out / f"{name}.gqt"
    p.write_text(case_text(schema, data, kind, text, params, e2obs, notes))
    blessed = bless(p)
    if fixed_text:
        f = out / f"{name}.fixed.gqt"
        f.write_text(case_text(schema, data, kind, fixed_text, params, e2obs, notes + ["the `omnigraph lint --fix` rewrite of the edition-2 text, expectations from EDITION-2"]))
    return blessed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/src/qfuzz-data")
    ap.add_argument("--out", required=True)
    ap.add_argument("--case", action="append", default=[], help="SEED:QI")
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--sample-seed", type=int, default=1)
    ap.add_argument("--repro", help="a repro directory written by shrink.py (case.json)")
    args = ap.parse_args()
    out = Path(args.out)
    picks = [tuple(int(x) for x in c.split(":")) for c in args.case]
    if args.sample:
        rng = random.Random(args.sample_seed)
        files = sorted((Path(args.data) / "raw").glob("*.jsonl"), key=lambda p: int(p.stem))
        while len(picks) < args.sample:
            f = rng.choice(files)
            n = sum(1 for _ in open(f)) - 1
            if n > 0:
                picks.append((int(f.stem), rng.randrange(n)))
    ok = 0
    for seed, qi in picks:
        world, rec = peek.load_case(args.data, seed, qi)
        fx = rec.get("fix") or {}
        fr = rec.get("fix_review") or {}
        fixed_text = fx["text"] if fx.get("status") == "fixed" and fx.get("text") != rec["text"] else (
            fr["text"] if fr.get("status") == "fixed" and fr.get("text") != rec["text"] else None)
        notes = [f"query-fuzz gen v1, world seed {seed}, case {qi}; the edition-2 text, expectations blessed from EDITION-2"]
        if world["index_build"]:
            notes.append("the generated world ran `omnigraph index build` after its load; this case does not")
        ok += emit(out, f"s{seed}_q{qi}", world["schema"], world["data"], rec["kind"], rec["text"], rec["params"], rec["e2"], fixed_text, notes)
    if args.repro:
        c = json.loads((Path(args.repro) / "case.json").read_text())
        oc = json.loads((Path(args.repro) / "outcome.json").read_text())
        fx = oc.get("fix_review") if (oc.get("fix_review") or {}).get("status") == "fixed" else oc.get("fix") or {}
        fixed_text = fx.get("text") if fx.get("status") == "fixed" and fx.get("text") != c["query"] else None
        name = Path(args.repro).name.replace("-", "_")
        ok += emit(out, name, c["schema"], c["data"], c["kind"], c["query"], c.get("params", {}), oc.get("e2"), fixed_text,
                   [f"query-fuzz repro `{Path(args.repro).name}`, shrunk from world seed {c.get('origin', {}).get('seed')} case {c.get('origin', {}).get('qi')}"])
    print(json.dumps({"cases": len(picks) + (1 if args.repro else 0), "blessed": ok}))


if __name__ == "__main__":
    main()
