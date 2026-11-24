"""Run every query of the edition-2 GQT corpus through oracles 2 to 4.

The Cross-Edition Comparison job compares rows of the corpus's two texts; it
does not check that NEW's refusals of the edition-2 text carry a fix, or that
`lint --fix` on that text converges and is closed. This reads every `.gqt`
case at EDITION_2_SOURCE_COMMIT, takes each `--- query` / `--- mutate` step
whose body is query declarations, and runs, against the case's schema:

- EDITION-2 `lint` (does edition 2 accept it?),
- NEW `lint` on the unchanged text (the refusal: code, position, fix),
- NEW `lint --fix`, `--review-fixes` where it stops, and `lint --fix` again
  on the rewrite (closure), and NEW `lint` on the rewrite (it compiles).

    corpus.py --base-ref SHA --repo DIR --out FILE.jsonl
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run  # noqa: E402

E2 = "/src/bin/e2-omnigraph"
NEW = "/src/bin/new-omnigraph"


def sections(text: str):
    out, cur, body = [], None, []
    for line in text.splitlines():
        if line.startswith("--- "):
            if cur is not None:
                out.append((cur, "\n".join(body).strip("\n")))
            cur, body = line[4:].strip(), []
        elif cur is not None:
            body.append(line)
    if cur is not None:
        out.append((cur, "\n".join(body).strip("\n")))
    return out


def lint(binary, schema: Path, text: str, d: Path, tag: str):
    f = d / f"{tag}.gq"
    f.write_text(text)
    res = run.cli(binary, ["lint", "--query", str(f), "--schema", str(schema), "--json"], d)
    doc = run.parse_json(res["stdout"]) or {}
    findings = [x for x in doc.get("findings") or [] if x.get("severity") == "error"]
    ok = doc.get("status") == "ok" and not findings
    diags = [{"code": x.get("code"), "message": (x.get("message") or "")[:240], "fix": x.get("fix"),
              "edits": bool((x.get("suggestion") or {}).get("edits")),
              "applicability": (x.get("suggestion") or {}).get("applicability"),
              "position": bool(re.match(r"line \d+, column \d+", x.get("message") or ""))} for x in findings]
    return {"ok": bool(ok), "diagnostics": diags[:8],
            "panic": "panicked" in res["stderr"], "raw": None if doc else (res["stdout"] + res["stderr"])[-600:]}


def one(item):
    case, idx, schema_text, body = item
    d = Path(tempfile.mkdtemp(prefix="qf-corpus-"))
    try:
        (d / "schema.pg").write_text(schema_text)
        rec = {"case": case, "step": idx, "text": body}
        rec["e2"] = lint(E2, d / "schema.pg", body, d, "e2")
        rec["new"] = lint(NEW, d / "schema.pg", body, d, "new")
        fx = run.lint_fix(NEW, body, d / "schema.pg", d, "fix", False)
        rec["fix"] = {k: fx[k] for k in ("status", "applied", "unfixed", "passes", "rc")}
        final = fx
        if fx["status"] not in ("fixed", "unchanged"):
            fr = run.lint_fix(NEW, body, d / "schema.pg", d, "fixr", True)
            rec["fix_review"] = {k: fr[k] for k in ("status", "applied", "unfixed", "passes", "rc")}
            if fr["status"] == "fixed":
                final = fr
        rec["fixed_text"] = final["text"] if final["text"] != body else None
        if final["status"] == "fixed" and final["text"] != body:
            rec["fixed_lint"] = lint(NEW, d / "schema.pg", final["text"], d, "fixedlint")
            again = run.lint_fix(NEW, final["text"], d / "schema.pg", d, "fix2", final is not fx)
            rec["closure"] = {"status": again["status"], "changed": again["text"] != final["text"], "applied": again["applied"]}
        if "panicked" in str(fx.get("raw")):
            rec["fix"]["panic"] = True
        return rec
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-ref", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args()
    git = lambda *a: subprocess.run(["git", "-C", args.repo, *a], check=True, capture_output=True, text=True).stdout
    paths = [p for p in git("ls-tree", "-r", "--name-only", args.base_ref, "--", "crates/omnigraph-gqt/cases").splitlines() if p.endswith(".gqt")]
    items = []
    for p in paths:
        text = git("show", f"{args.base_ref}:{p}")
        secs = sections(text)
        schema = next((b for h, b in secs if h == "schema"), None)
        if not schema:
            continue
        idx = 0
        for h, b in secs:
            if h.split()[0] in ("query", "mutate"):
                idx += 1
                body = b.strip()
                if re.match(r"^(query|mutation)\s+\w+\s*\(", body):
                    items.append((p.removeprefix("crates/omnigraph-gqt/cases/"), idx, schema, body + "\n"))
    print(f"{len(paths)} cases, {len(items)} declaration steps", file=sys.stderr)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool, open(args.out, "w") as fh:
        for rec in pool.map(one, items):
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
