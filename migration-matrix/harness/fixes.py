"""Respell edition-2 text mechanically with the fixes NEW prints.

NEW ships no `lint --fix` (the RFC's Compatibility section and "Stored
sources at upgrade" name one), and `lint --json` prints a parse refusal's
code and message without its fix or edits. The one machine-readable source
of the printed edits is the `diagnostic` a refused `query`/`mutate` returns
(`suggestion.edits`, and each `further` refusal's). This module does what
`lint --fix` is described to do with those: run the text on a scratch copy
of a graph of the same schema, apply every edit of the refusal and its
`further` list, and repeat until the text compiles (the run succeeds or
fails after compiling), no edit is printed, or the pass limit is reached.
Every pass is recorded.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from lib import og, copy_graph

MAX_PASSES = 8
COMPILE_CODE = re.compile(r"^[QT]\d+$")


def diagnostic_of(run) -> dict | None:
    j = run.json()
    if isinstance(j, dict):
        return j.get("diagnostic")
    return None


def all_diags(d: dict) -> list[dict]:
    out = [d]
    for f in d.get("further") or []:
        out.append(f)
    return out


def edits_of(d: dict) -> list[tuple[int, int, str, str]]:
    sug = d.get("suggestion") or {}
    app = sug.get("applicability", "")
    return [(int(e["start"]), int(e["end"]), e.get("replacement", ""), app) for e in sug.get("edits") or []]


def apply_edits(text: str, edits) -> str | None:
    data = text.encode()
    edits = sorted({(s, e, r) for s, e, r, _ in edits})
    for a, b in zip(edits, edits[1:]):
        if b[0] < a[1]:
            return None
    for start, end, repl in reversed(edits):
        if not (0 <= start <= end <= len(data)):
            return None
        data = data[:start] + repl.encode() + data[end:]
    return data.decode("utf-8", "replace")


def run_text(kind: str, graph: Path, work: Path, name: str, text: str, params: dict, i: int):
    q = work / f"{name}.{i}.gq"
    q.write_text(text)
    g = copy_graph(graph, work / "g")
    verb = "mutate" if kind == "write" else "query"
    args = [verb, name, "--query", q, "--target", g]
    args += ["--json"] if verb == "mutate" else ["--format", "json"]
    if params:
        args += ["--params", json.dumps(params)]
    return og("new", *args, home=work / "home")


def respell(kind: str, graph: Path, text: str, work: Path, name: str, params: dict) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    passes = []
    current = text
    for i in range(MAX_PASSES):
        r = run_text(kind, graph, work, name, current, params, i)
        d = diagnostic_of(r)
        code = (d or {}).get("code")
        if r.ok or not d or not code or not COMPILE_CODE.match(str(code)):
            passes.append({"pass": i, "status": "compiled", "rc": r.rc,
                           "run_error": None if r.ok else r.message()[-600:]})
            shutil.rmtree(work / "g", ignore_errors=True)
            return {"compiled": True, "text": current, "passes": passes}
        diags = all_diags(d)
        edits = [e for x in diags for e in edits_of(x)]
        passes.append({"pass": i, "codes": [x.get("code") for x in diags],
                       "expected": [x.get("expected") for x in diags][:6],
                       "fix": [x.get("fix") for x in diags][:6],
                       "applicability": sorted({e[3] for e in edits}),
                       "edit_count": len(edits)})
        if not edits:
            return {"compiled": False, "text": current, "passes": passes, "stuck": "no edit printed",
                    "stuck_codes": [x.get("code") for x in diags]}
        nxt = apply_edits(current, edits)
        if nxt is None:
            nxt = apply_edits(current, edits_of(diags[0]))
        if nxt is None or nxt == current:
            return {"compiled": False, "text": current, "passes": passes, "stuck": "edits overlap or change nothing"}
        current = nxt
    return {"compiled": False, "text": current, "passes": passes, "stuck": "pass limit"}


def lint_one(schema: Path, text: str, work: Path, name: str) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    q = work / f"{name}.gq"
    q.write_text(text)
    r = og("new", "lint", "--schema", schema, "--query", q, "--json", home=work / "home")
    j = r.json()
    return {"rc": r.rc, "json": j, "raw": None if j is not None else r.message()}


def lint_diagnostics(j: dict) -> list[dict]:
    out = []
    for res in j.get("results") or []:
        if res.get("status") not in (None, "ok"):
            out.append({k: res.get(k) for k in ("name", "status", "error", "diagnostics", "code")})
    for f in j.get("findings") or []:
        if f.get("severity") == "error":
            out.append(f)
    return out
