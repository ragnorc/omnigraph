"""Rebuild a world from its seed (or from a schema and data file) and run texts on both binaries.

    replay.py --seed N [--schema S --data D] [--index-build] --text FILE [--text-new FILE]
              [--params JSON] [--name Q] [--kind read|write] [--dir DIR]

Prints EDITION-2's and NEW's observations (and NEW's on --text-new when given)
as JSON. Used by the shrinker and by the repro scripts.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gen  # noqa: E402
import run  # noqa: E402

E2 = "/src/bin/e2-omnigraph"
NEW = "/src/bin/new-omnigraph"


class Built:
    def __init__(self, d: Path, schema: str, data: str, index_build: bool, e2=E2, new=NEW):
        self.d = d
        d.mkdir(parents=True, exist_ok=True)
        (d / "schema.pg").write_text(schema)
        (d / "data.jsonl").write_text(data)
        w = type("W", (), {"index_build": index_build})()
        self.e2_bin, self.new_bin = e2, new
        self.e2 = run.setup_graph(e2, "e2", d, w, "overwrite")
        self.new = run.setup_graph(new, "new", d, w, "overwrite")

    @property
    def ok(self):
        return self.e2["ok"] and self.new["ok"]

    def run(self, which: str, text: str, name: str, params: dict, kind: str, tag: str):
        binary = self.e2_bin if which == "e2" else self.new_bin
        graph = Path(self.e2["graph"] if which == "e2" else self.new["graph"])
        work = self.d / "q"
        work.mkdir(exist_ok=True)
        return run.run_one(binary, graph, text, name, params, kind, work, tag)

    def lint_fix(self, text: str, review=False):
        work = self.d / "q"
        work.mkdir(exist_ok=True)
        return run.lint_fix(self.new_bin, text, self.d / "schema.pg", work, "lint", review)


def world_texts(seed: int):
    w = gen.draw_world(seed)
    return w.schema_text(), w.data_jsonl(), w.index_build


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int)
    ap.add_argument("--schema")
    ap.add_argument("--data")
    ap.add_argument("--index-build", action="store_true")
    ap.add_argument("--text", required=True)
    ap.add_argument("--text-new")
    ap.add_argument("--params", default="{}")
    ap.add_argument("--name")
    ap.add_argument("--kind")
    ap.add_argument("--dir")
    args = ap.parse_args()
    if args.schema:
        schema, data, ib = Path(args.schema).read_text(), Path(args.data).read_text(), args.index_build
    else:
        schema, data, ib = world_texts(args.seed)
    d = Path(args.dir or tempfile.mkdtemp(prefix="qf-replay-"))
    b = Built(d, schema, data, ib)
    if not b.ok:
        print(json.dumps({"setup": {"e2": b.e2, "new": b.new}}, indent=1))
        return 1
    text = Path(args.text).read_text()
    name = args.name or run.QNAME.findall(text)[0]
    kind = args.kind or ("write" if re.search(r"^\s*(insert|update|delete)\b", text, re.M) else "read")
    params = json.loads(args.params)
    out = {"e2": b.run("e2", text, name, params, kind, "e2"), "new": b.run("new", text, name, params, kind, "new")}
    if args.text_new:
        t2 = Path(args.text_new).read_text()
        n2 = run.QNAME.findall(t2)[0]
        out["new_on_text_new"] = b.run("new", t2, n2, params, kind, "new2")
    print(json.dumps(out, indent=1, ensure_ascii=False))
    if not args.dir:
        shutil.rmtree(d, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
