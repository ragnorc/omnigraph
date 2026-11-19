"""Shared plumbing: running the two binaries, copying graphs, reading Arrow.

OLD and NEW are fixed binaries (see `bins/COMMITS` in the work directory).
Every invocation gets a hermetic `OMNIGRAPH_HOME`, `RUST_MIN_STACK`, a
timeout, and is recorded with its exit code, wall time and output.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORK = Path(os.environ.get("MM_WORK", "/src/mm/work"))
BIN = {
    "old": Path(os.environ.get("MM_OLD", "/src/mm/bin/old/omnigraph")),
    "new": Path(os.environ.get("MM_NEW", "/src/mm/bin/new/omnigraph")),
}
SERVER = {
    "old": Path(os.environ.get("MM_OLD_SERVER", "/src/mm/bin/old/omnigraph-server")),
    "new": Path(os.environ.get("MM_NEW_SERVER", "/src/mm/bin/new/omnigraph-server")),
}
P22 = Path(os.environ.get("MM_P22", "/src/wt/new/scripts/cross-edition.py"))

ENV = dict(os.environ)
ENV.update({"RUST_MIN_STACK": "16777216", "NO_COLOR": "1", "RUST_BACKTRACE": "1"})


@dataclass
class Run:
    side: str
    args: list
    rc: int
    out: str
    err: str
    secs: float
    raw: bytes = b""

    @property
    def ok(self) -> bool:
        return self.rc == 0

    def json(self):
        try:
            return json.loads(self.out)
        except Exception:
            return None

    def message(self) -> str:
        """The refusal text: a JSON `error` if stdout holds one, else stderr."""
        j = self.json()
        if isinstance(j, dict) and "error" in j:
            code = j.get("code")
            return f"[{code}] {j['error']}" if code else str(j["error"])
        text = (self.err or self.out).strip()
        lines = [ln for ln in text.splitlines() if not ln.startswith("target ")]
        return "\n".join(lines)[-1500:]

    def panicked(self) -> bool:
        return "panicked at" in self.err or self.rc in (101, -6, 134, -11, 139)

    def to_json(self) -> dict:
        return {"side": self.side, "args": [str(a) for a in self.args], "rc": self.rc,
                "secs": round(self.secs, 4), "out": self.out[-4000:], "err": self.err[-4000:]}


def og(side: str, *args, home: Path | None = None, timeout: int = 300, binary: bool = False,
       stdin: str | None = None) -> Run:
    env = dict(ENV)
    env["OMNIGRAPH_HOME"] = str(home or (WORK / "home" / side))
    Path(env["OMNIGRAPH_HOME"]).mkdir(parents=True, exist_ok=True)
    cmd = [str(BIN[side]), *[str(a) for a in args]]
    t0 = time.perf_counter()
    try:
        p = subprocess.run(cmd, capture_output=True, env=env, timeout=timeout,
                           input=stdin.encode() if stdin is not None else None,
                           stdin=None if stdin is not None else subprocess.DEVNULL)
        rc, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as e:
        rc, out, err = -999, e.stdout or b"", (e.stderr or b"") + b"\n[harness] TIMEOUT"
    secs = time.perf_counter() - t0
    raw = out if binary else b""
    return Run(side, list(args), rc, "" if binary else out.decode("utf-8", "replace"),
               err.decode("utf-8", "replace"), secs, raw)


def must(r: Run, what: str) -> Run:
    if not r.ok:
        raise RuntimeError(f"{what} failed (rc={r.rc}): {r.message()}\nargs={r.args}")
    return r


def copy_graph(src: Path, dst: Path) -> Path:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, symlinks=True)
    return dst


def tree_digest(root: Path) -> str:
    """A digest of every file under `root`: its path, size and modification
    time. A write that changes a file's bytes changes its size or time;
    `copytree` preserves both, so a copy digests like its source."""
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            st = p.stat()
            h.update(f"{p.relative_to(root)}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    return h.hexdigest()


def tree_listing(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


# ---- p22's comparison ------------------------------------------------------

def load_p22():
    spec = importlib.util.spec_from_file_location("cross_edition", P22)
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.modules["cross_edition"] = mod
    spec.loader.exec_module(mod)
    return mod


_P22 = None


def p22():
    global _P22
    if _P22 is None:
        _P22 = load_p22()
    return _P22


def arrow_observation(r: Run, ordered: bool):
    """A p22 `Observation` from an `--format arrow` run."""
    obs = p22().Observation()
    if not r.ok:
        obs.error = r.message()
        return obs
    import pyarrow.ipc as ipc
    try:
        table = ipc.open_stream(io.BytesIO(r.raw)).read_all()
    except Exception as e:  # an empty stdout is a refusal the CLI printed elsewhere
        obs.error = f"unreadable arrow stream: {e}; {r.err[-500:]}"
        return obs
    obs.columns = [f.name for f in table.schema]
    obs.types = [f"{f.type}{'' if f.nullable else ' not null'}" for f in table.schema]
    obs.rows = [jsonable(row) for row in table.to_pylist()]
    obs.ordered = ordered
    return obs


def jsonable(v):
    import datetime
    import decimal
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    if isinstance(v, float):
        if v != v:
            return "NaN"
        if v == 0.0 and str(v).startswith("-"):
            return "-0.0"
        return v
    if isinstance(v, (datetime.date, datetime.datetime, datetime.time)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return str(v)
    if isinstance(v, bytes):
        return "bytes:" + v.hex()
    return v


# ---- exports ---------------------------------------------------------------

def export_rows(side: str, graph: Path, branch: str | None = None) -> tuple[Run, list[dict]]:
    args = ["export", "--target", graph]
    if branch:
        args += ["--branch", branch]
    r = og(side, *args)
    rows = []
    if r.ok:
        for line in r.out.splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    return r, rows


def canonical_export(rows: list[dict], drop_unkeyed_ids: bool = True) -> list[str]:
    """Export rows in a form two graphs can be compared by: sorted, with the
    generated ids of unkeyed entities removed."""
    out = []
    for row in rows:
        row = json.loads(json.dumps(row))
        if drop_unkeyed_ids:
            t = row.get("type") or row.get("edge")
            if t in ("Loose", "Knows", "Tagged", "Cites"):
                row.pop("id", None)
                if isinstance(row.get("data"), dict):
                    row["data"].pop("id", None)
        out.append(json.dumps(row, sort_keys=True, ensure_ascii=False))
    return sorted(out)


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, sort_keys=True, default=str))


def append_jsonl(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(obj, sort_keys=True, default=str) + "\n")
