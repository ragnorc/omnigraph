"""Plumbing shared by the fuzzer: the three binaries on the matrix's runner.

The migration matrix's `lib.py` runs two sides, `old` and `new`; this run
has three, so the fuzzer adds `rel` (GQ2-REL, the released v0.16.0),
`main` (GQ2-MAIN, the main commit #1316 last merged) and keeps `new` (NEW,
#1316's head), all through the matrix's own `og`, `Run` and p22 comparator.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MM = HERE.parents[1] / "migration-matrix" / "harness"
sys.path.insert(0, str(MM))
sys.path.insert(0, str(HERE))

os.environ.setdefault("MM_WORK", os.environ.get("UF_WORK", "/src/uf/work"))
os.environ.setdefault("MM_P22", os.environ.get("UF_P22", "/src/wt/new/scripts/cross-edition.py"))

import lib  # noqa: E402  (the matrix's runner)

BINDIR = Path(os.environ.get("UF_BIN", "/src/uf/bin"))
lib.BIN.update({
    "rel": BINDIR / "rel" / "omnigraph",
    "main": BINDIR / "main" / "omnigraph",
    "new": BINDIR / "new" / "omnigraph",
})
lib.SERVER.update({k: BINDIR / k / "omnigraph-server" for k in ("rel", "main", "new")})
WORK = Path(os.environ.get("UF_WORK", "/src/uf/work"))
GQ2 = ("rel", "main")
SIDES = ("rel", "main", "new")
STAMP_SERVES = {"rel": (14, 17), "main": (14, 18), "new": (14, 18)}


def sha(text: str | bytes) -> str:
    if isinstance(text, str):
        text = text.encode()
    return hashlib.sha256(text).hexdigest()[:16]


ANSI = re.compile(r"\x1b\[[0-9;]*m")


def clean(s: str) -> str:
    return ANSI.sub("", s or "")


def json_flag(args) -> bool:
    a = [str(x) for x in args]
    return "--json" in a or ("--format" in a and a[a.index("--format") + 1] in ("json",))


def integrity(r) -> list[str]:
    """Oracle 5 on one invocation: a panic, a hang, or a refusal under
    `--json` that carries no code."""
    out = []
    if r.rc == -999:
        out.append("hang")
    elif r.panicked() or "RUST_BACKTRACE" in r.err and "panicked" in r.err:
        out.append("panic")
    if r.rc not in (0, -999) and json_flag(r.args):
        j = r.json()
        if not isinstance(j, dict):
            out.append("uncoded:plain-text")
        else:
            coded = (j.get("code") or (j.get("diagnostic") or {}).get("code") or j.get("outcome")
                     or j.get("status") or j.get("findings") or j.get("results"))
            if not coded:
                out.append("uncoded:json-without-code")
    return out


def stamp_of(snapshot_run) -> int | None:
    j = snapshot_run.json()
    if isinstance(j, dict):
        return j.get("internal_schema_version")
    return None


def canonical_export(rows: list, unkeyed_types: set, keyed_edges: set) -> list[str]:
    out = []
    for row in rows:
        row = dict(row)
        t = row.get("type")
        e = row.get("edge")
        if (t is not None and t in unkeyed_types) or (e is not None and e not in keyed_edges):
            row.pop("id", None)
        out.append(json.dumps(row, sort_keys=True, ensure_ascii=False))
    return sorted(out)


def parse_jsonl(text: str) -> list:
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows
