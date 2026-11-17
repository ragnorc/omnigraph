"""Run one Omnigraph query the way PR #1416's clone route does: the product
binary, one process per step, over an empty graph shallow clone of a frozen
base (--target <empty clone> --base <base>), with --stats for Lance calls and
peak RSS. Stdlib only."""
import json, os, shutil, subprocess, tempfile, time, hashlib

CLONES = os.environ.get("OG_CLONES", "/src/clones")
os.makedirs(CLONES, exist_ok=True)

def run_query(binary, base, qfile, name, params, settings=(), timeout=600):
    clone = tempfile.mkdtemp(dir=CLONES)
    stats = os.path.join(clone + ".stats.json")
    cmd = [binary, "query", "--target", clone, "--base", base, "--query", qfile, name,
           "--params", json.dumps(params), "--json", "--stats", stats]
    for s in settings:
        cmd += ["--set", s]
    env = {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/root"), "LANG": "C.UTF-8"}
    t0 = time.perf_counter_ns()
    p = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env)
    wall_ms = (time.perf_counter_ns() - t0) / 1e6
    out = {"wall_ms": wall_ms, "exit": p.returncode}
    try:
        doc = json.loads(p.stdout)
    except Exception:
        doc = {"error": "unparseable stdout", "stdout": p.stdout[-2000:].decode("utf-8", "replace"),
               "stderr": p.stderr[-2000:].decode("utf-8", "replace")}
    out["doc"] = doc
    try:
        st = json.load(open(stats))
        out["stats"] = {"elapsed_ms": st["elapsed_ms"], "peak_rss_bytes": st["peak_rss_bytes"],
                        "manifest": st["storage"]["manifest"], "table": st["storage"]["table"]}
    except Exception as e:
        out["stats"] = None
    # the clone must stay empty apart from its marker: the measured step is a read
    leftover = [f for f in os.listdir(clone) if f != "__clone"]
    out["clone_written"] = leftover
    shutil.rmtree(clone, ignore_errors=True)
    try: os.remove(stats)
    except FileNotFoundError: pass
    return out

def rows_digest(doc, ordered=True):
    rows = doc.get("rows")
    if rows is None:
        return None
    enc = [json.dumps(r, sort_keys=True) for r in rows]
    if not ordered:
        enc.sort()
    return hashlib.sha256("\n".join(enc).encode()).hexdigest()[:16]

def calls(stats):
    if not stats: return None
    return sum(stats["manifest"].values()) + sum(stats["table"].values())
