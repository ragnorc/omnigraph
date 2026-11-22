"""GQ 2 against GQ 3 on one machine: wall time, store requests and peak
memory of the same intent written in each edition, on the same graph.

OLD is the `main` commit #1316's head last merged; NEW is #1316's head, so
the two differ by GQ 3 alone. Every graph is written by OLD. Each query runs
with OLD's `omnigraph` in its edition-2 text and NEW's in its edition-3 text,
`MM_REPS` times per side (default 5), the sides interleaved, every
invocation a fresh process timed by wall clock and by `--stats`, as
`timing.py` does. Rows are compared with p22's comparator.

  shapes   the 56 catalog scenarios whose measured step holds GQ text:
           OLD's step text from OLD's benchmarks/, NEW's from NEW's, each
           on its fixture built once by OLD's engine (OLD's omnigraph-bench
           `dataset build` of OLD's fixture with OLD's workload's index needs)
  search   the search set on timing.py's 100,000-document corpus
  report   both tables as Markdown

Environment: MM_WORK, MM_OLD, MM_NEW (the two `omnigraph` binaries),
MM_OLD_BENCH (OLD's omnigraph-bench), MM_OLD_TREE and MM_NEW_TREE (the two
checkouts), MM_P22 (NEW's scripts/cross-edition.py).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

import yaml

import catalog
from gen import SEARCH_PG, search_corpus
from lib import WORK, BIN, og, must, arrow_observation, p22, copy_graph, append_jsonl, tree_digest

REPS = int(os.environ.get("MM_REPS", "5"))
OLD_TREE = Path(os.environ.get("MM_OLD_TREE", "/src/gqb/old"))
NEW_TREE = Path(os.environ.get("MM_NEW_TREE", "/src/gqb/new"))
OLD_BENCH = Path(os.environ.get("MM_OLD_BENCH", "/src/gqb/bin/old/omnigraph-bench"))
OUT = Path(os.environ.get("MM_OUT", str(WORK / "raw")))
# OLD's id of a scenario NEW renamed when it respelled it.
OLD_ID = {"e2e-rrf-exact-traversal": "e2e-rrf-traversal"}
OP_HEADER = re.compile(r"^--- (query|mutate|load|restart|branch|commit|settings|show|list)\b")


# ---- the catalog -----------------------------------------------------------

def scenarios(tree: Path) -> dict:
    doc = yaml.safe_load((tree / "benchmarks" / "benchmarks.yaml").read_text())
    return {s["id"]: s for s in doc["scenarios"]}


def has_gq(s: dict) -> bool:
    return "measured_step" in s and re.search(r"\bquery\s+\w+\s*\(", s["measured_step"]["text"]) is not None


def step_of(tree: Path, s: dict) -> dict:
    """The measured step: its header, body, name, branch and params, read
    from the workload file the scenario names."""
    text = s["measured_step"]["text"]
    wl = (tree / "benchmarks" / s["workload"]).read_text()
    at = wl.find(text)
    if at < 0:
        raise RuntimeError(f"{s['id']}: measured text not found in {s['workload']}")
    header, _, body = text.partition("\n")
    m = re.match(r"^--- (query|mutate)(.*)$", header)
    if not m:
        raise RuntimeError(f"{s['id']}: unexpected header {header!r}")
    kind, attrs = m.group(1), m.group(2).strip()
    branch = None
    if attrs:
        am = re.fullmatch(r"branch:\s*(\S+)", attrs)
        if not am:
            raise RuntimeError(f"{s['id']}: unsupported header attributes {attrs!r}")
        branch = am.group(1)
    # sections after the step, up to the next operation
    rest = wl[at + len(text):].splitlines()
    params = None
    i = 0
    while i < len(rest):
        line = rest[i]
        if OP_HEADER.match(line):
            break
        if line.strip() == "--- params":
            j = i + 1
            buf = []
            while j < len(rest) and not rest[j].startswith("--- "):
                buf.append(rest[j])
                j += 1
            params = json.loads("\n".join(buf).strip())
            i = j
            continue
        i += 1
    name = re.search(r"\bquery\s+(\w+)\s*\(", body).group(1)
    # A prefix step's session setting (`set history_release_bytes = 2048;`)
    # lives in the session, which a fresh process does not carry: put it in
    # the measured text, where the same statement applies it in-process.
    prefix = [ln.strip() for ln in wl[:at].splitlines() if re.match(r"^set \w+ = [^;]+;\s*$", ln.strip())]
    prefix = [ln for ln in dict.fromkeys(prefix) if ln not in body]
    body = "\n".join(prefix + [body.strip("\n")])
    return {"kind": kind, "branch": branch, "body": body, "name": name, "prefix_settings": prefix,
            "params": params, "header": header, "fixture": s["fixture"], "workload": s["workload"],
            "ordinal": s["measured_step"]["ordinal"]}


def pairs():
    old, new = scenarios(OLD_TREE), scenarios(NEW_TREE)
    out = []
    for sid, ns in new.items():
        if not has_gq(ns):
            continue
        os_ = old[OLD_ID.get(sid, sid)]
        out.append({"id": sid, "old_id": OLD_ID.get(sid, sid),
                    "old": step_of(OLD_TREE, os_), "new": step_of(NEW_TREE, ns),
                    "repetitions": ns.get("repetitions")})
    return out


# ---- fixtures --------------------------------------------------------------

def physical_digest(root: Path) -> dict:
    """omnigraph-bench's `PhysicalDigest` of a tree (reset.rs,
    `digest_inventory`): every entry under `root` sorted by its relative
    path, each hashed as kind tag, path length, path, length and contents."""
    import hashlib
    entries = []
    for path in root.rglob("*"):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise RuntimeError(f"symlink in fixture: {path}")
        entries.append((rel.encode(), path, path.is_dir()))
    entries.sort(key=lambda e: e[0])
    h = hashlib.sha256(b"omnigraph-bench-physical-tree-v1\0")
    files = nbytes = 0
    for rel, path, is_dir in entries:
        size = 0 if is_dir else path.stat().st_size
        h.update(b"d" if is_dir else b"f")
        h.update(len(rel).to_bytes(8, "little"))
        h.update(rel)
        h.update(size.to_bytes(8, "little"))
        if not is_dir:
            h.update(path.read_bytes())
            files += 1
            nbytes += size
    return {"files": files, "bytes": nbytes, "digest_sha256": h.hexdigest()}


PREBUILD = WORK / "raw-prebuild" / "fixture-prebuild.jsonl"


def build_fixture(p: dict) -> Path:
    """OLD's fixture with OLD's workload's index needs, built once by OLD's
    engine through OLD's omnigraph-bench (`gq2_vs_gq3.py fixtures`), then
    used from its never-opened template: its bytes are checked against the
    digest the build published. (The cache's own hit path re-checks a
    metadata witness that includes inode and ctime, which this gVisor box
    does not keep stable, so a hit is refused as corrupt.)"""
    cache = WORK / "fixture-cache"
    want = (p["old"]["fixture"], p["old"]["workload"])
    if PREBUILD.exists():
        failed = None
        for line in PREBUILD.read_text().splitlines():
            rec = json.loads(line)
            if (rec["fixture"], rec.get("workload")) != want:
                continue
            if rec.get("rc") != 0:
                failed = failed or rec
                continue
            m = json.loads(rec["full_out"])
            root = cache / m["key"] / "root"
            got = physical_digest(root)
            if got != m["handoff"]["physical"]:
                raise RuntimeError(f"template bytes changed: {got} != {m['handoff']['physical']}")
            return root
        if failed is not None:
            raise RuntimeError(f"OLD's dataset build of {want[0]} failed after {failed['secs']} s "
                               f"(rc={failed['rc']}): {failed['full_out'][-1500:]}")
    cache.mkdir(parents=True, exist_ok=True)
    fx = OLD_TREE / "benchmarks" / p["old"]["fixture"]
    wl = OLD_TREE / "benchmarks" / p["old"]["workload"]
    t0 = time.perf_counter()
    r = subprocess.run([str(OLD_BENCH), "dataset", "build", str(fx), "--queries", str(wl),
                        "--dataset-cache", str(cache), "--json"],
                       capture_output=True, text=True, timeout=4 * 3600, stdin=subprocess.DEVNULL)
    secs = time.perf_counter() - t0
    PREBUILD.parent.mkdir(parents=True, exist_ok=True)
    append_jsonl(PREBUILD, {"id": p["id"], "fixture": p["old"]["fixture"], "workload": p["old"]["workload"],
                            "rc": r.returncode, "secs": round(secs, 3), "full_out": r.stdout,
                            "err": r.stderr[-3000:]})
    if r.returncode != 0:
        raise RuntimeError(f"dataset build {fx.name}: rc={r.returncode} {r.stdout[-1500:]} {r.stderr[-1500:]}")
    root = cache / json.loads(r.stdout)["key"] / "root"
    if not (root / "__manifest").is_dir():
        raise RuntimeError(f"no template root at {root}")
    return root


def restore(root: Path) -> Path:
    """A copy of the never-opened template at the path the build wrote it
    at (`<key>/active`), which its shallow-branch references name."""
    return copy_graph(root, root.parent / "active")


# ---- running one step ------------------------------------------------------

def invoke(side: str, step: dict, graph: Path, stats: Path, home: Path):
    args = [step["kind"], "-e", step["body"], step["name"], "--target", graph, "--stats", stats]
    if step["kind"] == "query":
        args += ["--branch", step["branch"] or "main", "--format", "arrow"]
    else:
        if step["branch"]:
            args += ["--branch", step["branch"]]
        args += ["--json"]
    if step["params"] is not None:
        args += ["--params", json.dumps(step["params"])]
    if stats.exists():
        stats.unlink()
    r = og(side, *args, home=home, timeout=1800, binary=step["kind"] == "query")
    st = None
    try:
        st = json.loads(stats.read_text())
    except Exception:
        pass
    return r, st


def observation(step: dict, r):
    if step["kind"] == "query":
        return arrow_observation(r, re.search(r"\border\s*\{", step["body"]) is not None)
    obs = p22().Observation()
    if not r.ok:
        obs.error = r.message()
        return obs
    j = r.json() or {}
    obs.affected = (j.get("affected_nodes"), j.get("affected_edges"))
    return obs


def store_requests(st) -> int | None:
    if not st:
        return None
    total = 0
    for plane in ("manifest", "table"):
        total += sum(st["storage"][plane].values())
    return total


def order_only_same(o, n) -> bool:
    """Rows equal once float score columns are dropped, in the same order:
    how an F32 `bm25` score is held against an F64 one."""
    if o.rows is None or n.rows is None or o.columns != n.columns:
        return False

    def strip(rows):
        return [json.dumps({k: v for k, v in r.items() if not isinstance(v, float)}, sort_keys=True)
                for r in rows]
    return strip(o.rows) == strip(n.rows)


def compare(o, n) -> dict:
    diffs = p22().differences(o, n)
    kinds = sorted({k for k, _ in diffs})
    verdict = "same" if not diffs else "differ"
    if diffs and set(kinds) <= {"types", "rows"} and order_only_same(o, n):
        verdict = "same order (score F32 vs F64)"
    return {"verdict": verdict, "diffs": diffs}


def measure(label: str, steps: dict, graphs: dict, fresh_copy: Path | None, kind: str, extra: dict) -> dict:
    """`REPS` interleaved runs per side after one untimed warm-up run per
    side (the observation compared). A write runs each time on a fresh copy
    of `fresh_copy`, made outside the timing."""
    home = WORK / "home"
    scratch = WORK / "scratch" / label
    scratch.mkdir(parents=True, exist_ok=True)
    runs = {"old": [], "new": []}
    obs = {}
    for i in range(REPS + 1):
        for side in ("old", "new"):
            g = graphs[side]
            if fresh_copy is not None:
                g = restore(fresh_copy)
            r, st = invoke(side, steps[side], g, scratch / f"stats-{side}.json", home / side)
            if i == 0:
                obs[side] = observation(steps[side], r)
                continue
            runs[side].append({"secs": round(r.secs, 4), "ok": r.ok, "stats": st,
                               "msg": None if r.ok else r.message()[-600:]})
    summary = {}
    for side, rs in runs.items():
        ok = [x for x in rs if x["ok"]]
        secs = [x["secs"] for x in ok]
        st = [x["stats"] for x in ok if x["stats"]]
        summary[side] = {
            "ok": bool(rs) and all(x["ok"] for x in rs) and obs[side].error is None,
            "median_secs": statistics.median(secs) if secs else None,
            "min_secs": min(secs) if secs else None, "max_secs": max(secs) if secs else None,
            "median_stats_ms": statistics.median([s["elapsed_ms"] for s in st]) if st else None,
            "store_requests": sorted({store_requests(s) for s in st}) if st else None,
            "storage_sample": st[0]["storage"] if st else None,
            "peak_rss_mb": max(s["peak_rss_bytes"] for s in st if s.get("peak_rss_bytes")) / 2**20
            if any(s.get("peak_rss_bytes") for s in st) else None,
            "error": obs[side].error[-800:] if obs[side].error else (rs[0]["msg"] if rs and not rs[0]["ok"] else None),
        }
    cmp = compare(obs["old"], obs["new"]) if obs["old"].error is None and obs["new"].error is None else None
    rec = {"label": label, "kind": kind, **extra, "summary": summary, "compare": cmp,
           "row_counts": {s: (len(o.rows) if o.rows is not None else None) for s, o in obs.items()},
           "affected": {s: o.affected for s, o in obs.items()},
           "columns": {s: o.columns for s, o in obs.items()}, "types": {s: o.types for s, o in obs.items()},
           "rows_head": {s: (o.rows or [])[:10] for s, o in obs.items()},
           "runs": runs}
    return rec


# ---- part A1: catalog shapes -----------------------------------------------

def cmd_shapes(only: list[str]):
    out = OUT / "shapes.jsonl"
    done = set()
    if out.exists():
        done = {json.loads(l)["label"] for l in out.read_text().splitlines() if l.strip()}
    for p in pairs():
        if only and p["id"] not in only:
            continue
        if p["id"] in done:
            continue
        print(p["id"], flush=True)
        extra = {"scenario": p["id"], "old_scenario": p["old_id"], "old_step": p["old"], "new_step": p["new"]}
        try:
            root = build_fixture(p)
        except Exception as e:
            append_jsonl(out, {"label": p["id"], "kind": "fixture_failed", **extra, "error": str(e)[-3000:]})
            continue
        if p["old"]["kind"] == "query":
            g = restore(root)
            before = tree_digest(g)
            rec = measure(p["id"], {"old": p["old"], "new": p["new"]}, {"old": g, "new": g}, None, "read", extra)
            rec["graph_unchanged_by_reads"] = tree_digest(g) == before
        else:
            rec = measure(p["id"], {"old": p["old"], "new": p["new"]}, {"old": None, "new": None}, root,
                          "write", extra)
        rec["fixture_root"] = str(root)
        shutil.rmtree(root.parent / "active", ignore_errors=True)
        append_jsonl(out, rec)
        s = rec["summary"]
        print("  ", s["old"]["median_secs"], s["new"]["median_secs"], (rec["compare"] or {}).get("verdict"),
              s["old"]["error"] and s["old"]["error"][:200], s["new"]["error"] and s["new"]["error"][:200], flush=True)
        shutil.rmtree(WORK / "scratch" / p["id"], ignore_errors=True)


# ---- part A2: the search set -----------------------------------------------

def _pop(where: str = "") -> str:
    w = f" where {where}" if where else ""
    return f"let $pop = {{ match {{ $d: Doc }}{w} return {{ $d }} }}"


def search_set() -> list[dict]:
    qs = {q.name: q for q in catalog.SEARCHES}
    out = []

    def add(name, e2, e3, params, note=""):
        out.append({"name": name, "e2": e2, "e3": e3, "params": params, "note": note})

    def bm25(name, field, where, q):
        w2 = f"\n    $d.year > 2005" if where else ""
        m2 = f"match {{\n    $d: Doc{w2}\n  }}" if where else "match { $d: Doc }"
        add(name,
            f"query {name}($q: String) {{\n  {m2}\n  return {{ $d.slug, bm25($d.{field}, $q) as score }}\n"
            f"  order {{ bm25($d.{field}, $q) }}\n  limit 10\n}}",
            f"query {name}($q: String) {{\n  match {{ {_pop('$d.year > 2005' if where else '')}, "
            f"from bm25($pop, $d.{field}, $q) as $score }}\n  return {{ $d.slug, $score as score }}\n"
            f"  order {{ $score desc }}\n  limit 10\n}}",
            {"q": q})

    bm25("bm25_body", "body", False, "river stone")
    bm25("bm25_body_year", "body", True, "river stone")
    bm25("bm25_title", "title", False, "graph")
    bm25("bm25_title_year", "title", True, "graph")
    for n in ("s_match_text_body", "s_nearest", "s_rrf", "s_search_title", "s_fuzzy_default"):
        q = qs[n]
        add(n, q.e2, q.e3, q.params)
    # `search` over body, edition 2, against `match_terms` (edition 3)
    add("s_search_body",
        "query s_search_body($q: String) {\n  match {\n    $d: Doc\n    search($d.body, $q)\n  }\n"
        "  return { $d.slug }\n  order { $d.slug }\n}",
        "query s_search_body($q: String) {\n  match { $d: Doc }\n  where match_terms($d.body, terms($q, mode: any))\n"
        "  return { $d.slug }\n  order { $d.slug }\n}",
        {"q": "tariff harbor"})
    # limit 10 with and without order, on a scan and after a filter
    add("limit_scan",
        "query limit_scan() {\n  match { $d: Doc }\n  return { $d.slug }\n  limit 10\n}",
        "query limit_scan() {\n  match { $d: Doc }\n  return { $d.slug }\n  limit 10\n}", None,
        "no order: rows are the tie rule's")
    add("limit_scan_order",
        "query limit_scan_order() {\n  match { $d: Doc }\n  return { $d.slug }\n  order { $d.slug }\n  limit 10\n}",
        "query limit_scan_order() {\n  match { $d: Doc }\n  return { $d.slug }\n  order { $d.slug }\n  limit 10\n}",
        None)
    add("limit_filter",
        "query limit_filter() {\n  match {\n    $d: Doc\n    $d.year > 2005\n  }\n  return { $d.slug }\n  limit 10\n}",
        "query limit_filter() {\n  match { $d: Doc }\n  where $d.year > 2005\n  return { $d.slug }\n  limit 10\n}",
        None, "no order: rows are the tie rule's")
    add("limit_filter_order",
        "query limit_filter_order() {\n  match {\n    $d: Doc\n    $d.year > 2005\n  }\n  return { $d.slug }\n"
        "  order { $d.slug }\n  limit 10\n}",
        "query limit_filter_order() {\n  match { $d: Doc }\n  where $d.year > 2005\n  return { $d.slug }\n"
        "  order { $d.slug }\n  limit 10\n}", None)
    return out


def build_corpus(n: int) -> Path:
    root = WORK / "corpus" / f"n{n}"
    g = root / "graph"
    if (root / "built.json").exists():
        return g
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    (root / "schema.pg").write_text(SEARCH_PG)
    search_corpus(root / "docs.jsonl", n)
    h = WORK / "home" / "corpus-build"
    must(og("old", "init", "--schema", root / "schema.pg", g, home=h), "init")
    lines = (root / "docs.jsonl").read_text().splitlines()
    load_secs = 0.0
    for k in range(0, len(lines), 8000):
        part = root / f"docs.{k}.jsonl"
        part.write_text("\n".join(lines[k:k + 8000]) + "\n")
        r = must(og("old", "load", "--data", part, "--mode", "merge", "--target", g, "--json", home=h,
                    timeout=1800), "load")
        load_secs += r.secs
    b = must(og("old", "index", "build", "--target", g, "--json", home=h, timeout=1800), "index build")
    rec = {"n": n, "load_secs": round(load_secs, 3), "index_secs": round(b.secs, 3), "index_out": b.out[-3000:]}
    (root / "built.json").write_text(json.dumps(rec))
    append_jsonl(OUT / "search.jsonl", {"label": f"corpus-{n}", "kind": "corpus", **rec})
    return g


def cmd_search(n: int):
    g = build_corpus(n)
    out = OUT / "search.jsonl"
    done = {json.loads(l)["label"] for l in out.read_text().splitlines() if l.strip()} if out.exists() else set()
    graphs = {s: copy_graph(g, WORK / "corpus" / f"n{n}" / f"graph-{s}") for s in ("old", "new")}
    for q in search_set():
        label = f"{n}:{q['name']}"
        if label in done:
            continue
        steps = {"old": {"kind": "query", "branch": None, "body": q["e2"], "name": q["name"], "params": q["params"]},
                 "new": {"kind": "query", "branch": None, "body": q["e3"], "name": q["name"], "params": q["params"]}}
        rec = measure(label, steps, graphs, None, "search", {"query": q["name"], "n": n, "note": q["note"],
                                                            "e2": q["e2"], "e3": q["e3"], "params": q["params"]})
        append_jsonl(out, rec)
        s = rec["summary"]
        print(label, s["old"]["median_secs"], s["new"]["median_secs"], (rec["compare"] or {}).get("verdict"),
              s["old"]["error"] and s["old"]["error"][:200], s["new"]["error"] and s["new"]["error"][:200], flush=True)


def cmd_fixtures():
    """Build every pair's fixture once (the largest history last)."""
    ps = sorted(pairs(), key=lambda p: "100000" in p["old"]["fixture"])
    for p in ps:
        t0 = time.perf_counter()
        try:
            root = build_fixture(p)
            print(p["id"], "ok", round(time.perf_counter() - t0, 1), root, flush=True)
        except Exception as e:
            print(p["id"], "FAILED", str(e)[-600:], flush=True)


# ---- report ----------------------------------------------------------------

def _ms(x):
    return "—" if x is None else (f"{x:.0f}" if x >= 100 else f"{x:.1f}")


def _req(v):
    return "—" if not v else "/".join(str(x) for x in v)


def _rows(rec) -> str:
    c = rec.get("compare")
    if c is None:
        return "error"
    v = c["verdict"]
    if v == "same":
        return "same"
    if v.startswith("same order"):
        return "same order; score F32→F64"
    kinds = sorted({k for k, _ in c["diffs"]})
    return "differ: " + ", ".join(kinds)


def table_a(path: Path, key: str) -> list[str]:
    head = ("| {k} | OLD median ms | NEW median ms | NEW/OLD | in-process ms OLD → NEW | store requests OLD / NEW "
            "| peak RSS MB OLD / NEW | rows |").format(k=key)
    out = [head, "|" + "---|" * 8]
    for line in path.read_text().splitlines():
        r = json.loads(line)
        if r["kind"] == "corpus":
            continue
        name = r.get("scenario") or r.get("query")
        if r["kind"] == "fixture_failed":
            out.append(f"| `{name}` | — | — | — | — | — | — | not run: fixture build failed (see below) |")
            continue
        o, n = r["summary"]["old"], r["summary"]["new"]
        ratio = n["median_secs"] / o["median_secs"] if o["median_secs"] and n["median_secs"] else None
        out.append("| `{}` | {} | {} | {} | {} → {} | {} / {} | {} / {} | {} |".format(
            name, _ms(o["median_secs"] and o["median_secs"] * 1e3), _ms(n["median_secs"] and n["median_secs"] * 1e3),
            f"{ratio:.2f}" if ratio else "—", _ms(o["median_stats_ms"]), _ms(n["median_stats_ms"]),
            _req(o["store_requests"]), _req(n["store_requests"]),
            _ms(o["peak_rss_mb"]), _ms(n["peak_rss_mb"]), _rows(r)))
    return out


def _bench_runs(path: Path) -> dict:
    d = json.loads(path.read_text())
    out = {}
    for a in d.get("at", []):
        for run in a["output"]["runs"]:
            s = run["samples"]
            if s and s[0].get("concurrent"):
                c = out.setdefault(a["reference"], {"concurrent": [], "reps": len(s)})
                c["concurrent"] += [x["concurrent"] for x in s]
                continue
            el = sorted(x["elapsed_us"] for x in s)
            p50 = statistics.median_low(el)
            ps = next(x for x in s if x["elapsed_us"] == p50)
            calls = ps.get("logical_store_calls") or {}
            tot = sum(sum(v.values()) for v in calls.values() if isinstance(v, dict)) if calls else None
            out[a["reference"]] = {"reps": len(s), "min": el[0] / 1e3, "p50": p50 / 1e3, "max": el[-1] / 1e3,
                                   "calls": tot, "rss": max(x.get("peak_rss_bytes") or 0 for x in s) / 2**20}
    return out


def _bench_error(path: Path) -> str:
    d = json.loads(path.read_text())
    e = d.get("error") or (d.get("diagnostics") or [{}])[0]
    msg = re.sub(r"\x1b\[[0-9;]*m", "", e.get("message", "")).split("; server stdout")[0]
    s = e.get("gqt_settled_sample")
    extra = ""
    if s:
        calls = s["logical_store_calls"]
        tot = sum(sum(v.values()) for v in calls.values() if isinstance(v, dict))
        extra = f" (OLD's merge itself settled in {s['elapsed_us'] / 1e3:.0f} ms, {tot} Lance calls, before the check failed)"
    return f"`{e.get('code')}`: {msg[:300]}{extra}"


def table_b(raw: Path, old: str, new: str) -> list[str]:
    new_cat = scenarios(NEW_TREE)
    out = ["| scenario | measured step | reps | OLD p50 ms (min–max) | NEW p50 ms (min–max) | NEW/OLD "
           "| Lance calls at p50 OLD / NEW | peak RSS MB OLD / NEW | outcome |", "|" + "---|" * 9]
    ids = [x.split(" ")[0] for x in (raw / "partb" / "runs.log").read_text().splitlines()]
    for sid in dict.fromkeys(ids):
        sc = new_cat[sid]
        lines = sc.get("measured_step", {}).get("text", "").strip().splitlines()
        if not lines:
            step = "a `--- concurrent measured` block"
        elif len(lines) == 1 or lines[0].startswith("--- load"):
            step = lines[0]
        else:
            step = lines[1]
        f = raw / "partb" / f"{sid}.json"
        runs = _bench_runs(f)
        alone = raw / "partb-new-only" / f"{sid}.json"
        if old in runs and new in runs and "concurrent" not in runs[old]:
            o, n = runs[old], runs[new]
            out.append(f"| `{sid}` | `{step}` | {o['reps']} | {o['p50']:.1f} ({o['min']:.1f}–{o['max']:.1f}) | "
                       f"{n['p50']:.1f} ({n['min']:.1f}–{n['max']:.1f}) | {n['p50'] / o['p50']:.2f} | "
                       f"{o['calls']} / {n['calls']} | {o['rss']:.0f} / {n['rss']:.0f} | both ran |")
            continue
        outcome = "fails: " + _bench_error(f).replace("|", "\\|").replace("\n", " ")
        nr = _bench_runs(alone).get(new) if alone.exists() else None
        if nr and "concurrent" in nr:
            cells = "; ".join(
                f"W={c['sweep'].get('W')}: writer {c['labels'][0]['acked_per_second_millis'] / 1000:.1f}/s "
                f"p50 {c['labels'][0]['service_time_us']['p50'] / 1000:.0f} ms"
                for c in nr["concurrent"])
            out.append(f"| `{sid}` | {step} | 1 per point | — | NEW alone: {cells} | — | — | — | {outcome} |")
        elif nr:
            out.append(f"| `{sid}` | `{step}` | {nr['reps']} | — | NEW alone: {nr['p50']:.1f} ({nr['min']:.1f}–{nr['max']:.1f}) "
                       f"| — | — / {nr['calls']} | — / {nr['rss']:.0f} | {outcome} |")
        else:
            out.append(f"| `{sid}` | `{step}` | {sc.get('repetitions', 1)} | — | — | — | — | — | {outcome} |")
    return out


def cmd_report(raw: Path):
    print("\n".join(table_a(raw / "shapes.jsonl", "scenario")))
    print()
    print("\n".join(table_a(raw / "search.jsonl", "query")))
    print()
    print("\n".join(table_b(raw, os.environ["MM_OLD_SHA"], os.environ["MM_NEW_SHA"])))


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "shapes":
        cmd_shapes(sys.argv[2:])
    elif cmd == "search":
        cmd_search(int(sys.argv[2]) if len(sys.argv) > 2 else 100000)
    elif cmd == "report":
        cmd_report(Path(sys.argv[2]))
    elif cmd == "fixtures":
        cmd_fixtures()
    elif cmd == "pairs":
        for p in pairs():
            print(json.dumps(p))
    else:
        raise SystemExit(f"unknown command {cmd}")
