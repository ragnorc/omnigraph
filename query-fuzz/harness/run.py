"""Run generated cases through EDITION-2 and NEW, and record what each answered.

For each world (seed): draw its schema and data, initialize one graph per
binary (`init`, `load --mode overwrite`, and `index build` when the world asks
for it), then for every drawn query:

1. EDITION-2 on the edition-2 text: rows, columns, types (`explain`), error.
2. NEW on the same text, unchanged, twice (determinism).
3. NEW `lint --fix` (and, where it stops at a fix for review, `--review-fixes`)
   on the text, NEW on the rewrite, then `lint --fix` again on the rewrite.

A write runs on a fresh copy of each binary's seeded graph, and the graph's
export after it is recorded beside the write's reply.

Every observation goes to `<out>/raw/<seed>.jsonl`, one line per query, plus a
`world` line; `classify.py` judges them. A campaign resumes: a seed whose file
exists is skipped.

    run.py --e2 BIN --new BIN --out DIR --seeds A:B [--queries N] [--jobs J] [--budget S]
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gen  # noqa: E402

CALL_TIMEOUT = 120
ENV = dict(os.environ, RUST_MIN_STACK="16777216", NO_COLOR="1")
ENV.pop("OMNIGRAPH_ENGINE", None)


def cli(binary: str, args: list[str], cwd: Path, timeout: int = CALL_TIMEOUT) -> dict:
    started = time.monotonic()
    try:
        proc = subprocess.run(
            [binary, *args], cwd=str(cwd), capture_output=True, text=True,
            timeout=timeout, env=ENV, stdin=subprocess.DEVNULL,
        )
        out = {"rc": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr[-4000:]}
    except subprocess.TimeoutExpired:
        out = {"rc": None, "stdout": "", "stderr": f"timeout after {timeout}s"}
    out["secs"] = round(time.monotonic() - started, 3)
    return out


def parse_json(text: str):
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # some commands print a line before the document
        i = text.find("{")
        if i > 0:
            try:
                return json.loads(text[i:])
            except json.JSONDecodeError:
                return None
        return None


PANIC = re.compile(r"panicked|RUST_BACKTRACE|stack overflow|SIGSEGV|fatal runtime error")


def observe(res: dict) -> dict:
    """A query, mutate or explain reply as an observation."""
    obs = {"secs": res["secs"]}
    if res["rc"] is None:
        obs["status"] = "timeout"
        return obs
    if PANIC.search(res["stderr"]) or PANIC.search(res["stdout"]):
        obs["panic"] = (res["stderr"] + res["stdout"])[-1500:]
    doc = parse_json(res["stdout"])
    if res["rc"] == 0 and isinstance(doc, dict) and "error" not in doc:
        obs["status"] = "ok"
        if "rows" in doc:
            obs["columns"] = doc.get("columns")
            obs["rows"] = doc.get("rows")
        else:
            obs["affected"] = [doc.get("affected_nodes"), doc.get("affected_edges")]
            obs["committed"] = doc.get("commit") is not None
        return obs
    obs["status"] = "error"
    if isinstance(doc, dict):
        diag = doc.get("diagnostic") or {}
        obs["error"] = doc.get("error")
        obs["code"] = diag.get("code") or doc.get("code")
        obs["error_kind"] = doc.get("code")
        obs["position"] = diag.get("position")
        obs["end"] = diag.get("end")
        obs["fix"] = diag.get("fix")
        sug = diag.get("suggestion")
        obs["suggestion"] = sug
        obs["further"] = [
            {"code": f.get("code"), "fix": f.get("fix"), "suggestion": f.get("suggestion"), "position": f.get("position")}
            for f in diag.get("further") or []
        ]
        if not obs["code"]:
            m = re.search(r"\b([QTPLE]\d{1,3})\b", obs["error"] or "")
            obs["code"] = m.group(1) if m else None
            obs["uncoded"] = True
    else:
        obs["error"] = (res["stderr"] or res["stdout"])[-1500:]
        obs["code"] = None
        obs["uncoded"] = True
        obs["unstructured"] = True
    return obs


def explain_types(binary: str, graph: Path, text: str, name: str, params: Path, cwd: Path):
    """The typed result columns of the physical plan's root, from `explain`."""
    m = re.search(r"\bquery\s+" + re.escape(name) + r"\s*\(", text)
    if not m:
        return None
    src = text[: m.start()] + "explain " + text[m.start():]
    f = cwd / "explain.gq"
    f.write_text(src)
    res = cli(binary, ["query", "--target", str(graph), "--query", str(f), "--params-file", str(params), "--json", name], cwd)
    doc = parse_json(res["stdout"])
    if not isinstance(doc, dict) or "rows" not in doc:
        return None
    for row in doc["rows"]:
        if row.get("tree") == "physical":
            try:
                detail = json.loads(row.get("detail") or "{}")
            except json.JSONDecodeError:
                continue
            if "columns" in detail:
                return detail["columns"]
    return None


def export(binary: str, graph: Path, cwd: Path):
    res = cli(binary, ["export", "--target", str(graph)], cwd)
    if res["rc"] != 0:
        return {"error": res["stderr"][-800:]}
    return sorted(line for line in res["stdout"].splitlines() if line.strip())


QNAME = re.compile(r"\b(?:query|mutation)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")


def run_one(binary: str, graph: Path, text: str, name: str, params: dict, kind: str, cwd: Path, tag: str, types=True) -> dict:
    qf = cwd / f"{tag}.gq"
    qf.write_text(text)
    pf = cwd / f"{tag}.params.json"
    pf.write_text(json.dumps(params))
    if kind == "read":
        res = cli(binary, ["query", "--target", str(graph), "--query", str(qf), "--params-file", str(pf), "--json", name], cwd)
        obs = observe(res)
        if obs["status"] == "ok" and types:
            obs["types"] = explain_types(binary, graph, text, name, pf, cwd)
        return obs
    g = cwd / f"{tag}.graph"
    if g.exists():
        shutil.rmtree(g)
    shutil.copytree(graph, g, symlinks=True)
    res = cli(binary, ["mutate", "--target", str(g), "--query", str(qf), "--params-file", str(pf), "--json", name], cwd)
    obs = observe(res)
    obs["export"] = export(binary, g, cwd)
    shutil.rmtree(g, ignore_errors=True)
    return obs


def lint_fix(binary: str, text: str, schema: Path, cwd: Path, tag: str, review: bool) -> dict:
    f = cwd / f"{tag}.gq"
    f.write_text(text)
    args = ["lint", "--fix", "--query", str(f), "--schema", str(schema), "--json"]
    if review:
        args.insert(2, "--review-fixes")
    res = cli(binary, args, cwd)
    doc = parse_json(res["stdout"]) or {}
    fix = doc.get("fix") or {}
    out = {
        "rc": res["rc"],
        "status": fix.get("status") or doc.get("status"),
        "applied": fix.get("applied"),
        "applied_for_review": fix.get("applied_for_review"),
        "passes": fix.get("passes"),
        "stopped_at_pass": fix.get("stopped_at_pass"),
        "unfixed": [
            {"code": u.get("code"), "message": (u.get("message") or "")[:300], "suggestion": u.get("suggestion")}
            for u in (fix.get("unfixed") or [])
        ] if isinstance(fix.get("unfixed"), list) else fix.get("unfixed"),
        "results": [
            {"name": r.get("name"), "status": r.get("status")} for r in doc.get("results") or []
        ],
        "findings": [
            {"code": x.get("code"), "severity": x.get("severity"), "message": (x.get("message") or "")[:300]}
            for x in doc.get("findings") or []
            if x.get("code") != "L201"
        ],
        "text": f.read_text(),
    }
    if PANIC.search(res["stderr"]):
        out["panic"] = res["stderr"][-1500:]
    if not doc:
        out["raw"] = (res["stdout"] + res["stderr"])[-1500:]
    return out


def setup_graph(binary: str, label: str, d: Path, world: gen.World, load_mode: str) -> dict:
    g = d / f"graph-{label}"
    if g.exists():
        shutil.rmtree(g)
    r1 = cli(binary, ["init", "--schema", str(d / "schema.pg"), str(g)], d)
    if r1["rc"] != 0:
        return {"ok": False, "stage": "init", "error": (r1["stderr"] + r1["stdout"])[-1500:]}
    if (d / "data.jsonl").read_text().strip():
        r2 = cli(binary, ["load", "--data", str(d / "data.jsonl"), "--mode", load_mode, "--target", str(g)], d)
        if r2["rc"] != 0:
            return {"ok": False, "stage": "load", "error": (r2["stderr"] + r2["stdout"])[-1500:]}
    if world.index_build:
        r3 = cli(binary, ["index", "build", "--target", str(g)], d)
        if r3["rc"] != 0:
            return {"ok": False, "stage": "index", "error": (r3["stderr"] + r3["stdout"])[-1500:]}
    return {"ok": True, "graph": str(g), "export": export(binary, g, d)}


def run_world(seed: int, args) -> dict:
    out_file = Path(args.out) / "raw" / f"{seed}.jsonl"
    if out_file.exists():
        return {"seed": seed, "skipped": True}
    world = gen.draw_world(seed)
    base = Path(args.scratch) / f"w{seed}"
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True)
    (base / "schema.pg").write_text(world.schema_text())
    (base / "data.jsonl").write_text(world.data_jsonl())
    lines = []
    head = {
        "record": "world", "seed": seed, "gen_version": gen.GEN_VERSION, "schema": world.schema_text(), "data": world.data_jsonl(),
        "index_build": world.index_build,
    }
    e2 = setup_graph(args.e2, "e2", base, world, "overwrite")
    new = setup_graph(args.new, "new", base, world, "overwrite")
    head["e2_setup"] = {k: v for k, v in e2.items() if k != "graph"}
    head["new_setup"] = {k: v for k, v in new.items() if k != "graph"}
    lines.append(head)
    if e2["ok"] and new["ok"]:
        g2, g3 = Path(e2["graph"]), Path(new["graph"])
        work = base / "q"
        work.mkdir()
        for qi, q in enumerate(gen.draw_queries(world, seed, args.queries)):
            rec = {"record": "case", "seed": seed, "qi": qi, "kind": q.kind, "intent": q.intent,
                   "forms": sorted(q.forms), "name": q.name, "text": q.text, "params": q.params}
            try:
                rec.update(run_case(q, g2, g3, base, work, args))
            except Exception as exc:  # harness fault: record it, never stop the campaign
                rec["harness_error"] = repr(exc)[:2000]
            lines.append(rec)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_file.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines))
    tmp.rename(out_file)
    if not args.keep:
        shutil.rmtree(base, ignore_errors=True)
    return {"seed": seed, "cases": len(lines) - 1, "e2_ok": e2["ok"], "new_ok": new["ok"]}


def run_case(q: gen.Query, g2: Path, g3: Path, base: Path, work: Path, args) -> dict:
    rec = {}
    rec["e2"] = run_one(args.e2, g2, q.text, q.name, q.params, q.kind, work, "e2")
    rec["new"] = run_one(args.new, g3, q.text, q.name, q.params, q.kind, work, "new")
    if q.kind == "read" and rec["new"]["status"] == "ok":
        again = run_one(args.new, g3, q.text, q.name, q.params, q.kind, work, "new2", types=False)
        rec["new_again"] = {k: again.get(k) for k in ("status", "columns", "rows", "code")}
    # lint --fix, then --review-fixes where the default stops
    fx = lint_fix(args.new, q.text, base / "schema.pg", work, "fix", review=False)
    rec["fix"] = fx
    final = fx
    if fx["status"] not in ("fixed", "unchanged"):
        fr = lint_fix(args.new, q.text, base / "schema.pg", work, "fixr", review=True)
        rec["fix_review"] = fr
        if fr["status"] == "fixed":
            final = fr
    if final["status"] == "fixed" and final["text"] != q.text:
        m = QNAME.findall(final["text"])
        name = m[0] if len(m) == 1 else q.name
        rec["fixed_name"] = name
        rec["fixed"] = run_one(args.new, g3, final["text"], name, q.params, q.kind, work, "fixed")
        again = lint_fix(args.new, final["text"], base / "schema.pg", work, "fix2", review=final is not fx)
        rec["closure"] = {k: again[k] for k in ("status", "applied", "rc", "unfixed")}
        rec["closure"]["changed"] = again["text"] != final["text"]
        if again["text"] != final["text"]:
            rec["closure"]["text"] = again["text"]
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--e2", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--scratch", default="/tmp/qfuzz")
    ap.add_argument("--seeds", required=True, help="A:B, half-open")
    ap.add_argument("--queries", type=int, default=24)
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--budget", type=int, default=540, help="seconds before no new world starts")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    a, b = (int(x) for x in args.seeds.split(":"))
    deadline = time.monotonic() + args.budget
    done = 0
    cases = 0
    pending = iter(range(a, b))
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        running = set()
        def submit():
            for s in pending:
                if (Path(args.out) / "raw" / f"{s}.jsonl").exists():
                    continue
                running.add(pool.submit(run_world, s, args))
                return True
            return False
        for _ in range(args.jobs):
            submit()
        while running:
            finished, _ = concurrent.futures.wait(running, return_when=concurrent.futures.FIRST_COMPLETED)
            for f in finished:
                running.discard(f)
                r = f.result()
                done += 1
                cases += r.get("cases", 0)
                if time.monotonic() < deadline:
                    submit()
    last = max([int(p.stem) for p in (Path(args.out) / "raw").glob("*.jsonl") if a <= int(p.stem) < b] or [a - 1])
    print(json.dumps({"worlds": done, "cases": cases, "last_seed_written": last}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
