#!/usr/bin/env python3
"""Campaign 5b: the record's bounds and damaged records.

  near_bound   a submission whose `tables` list makes the accepted record just
               under the 1 MiB per-operation bound; the run fails (unknown
               table names) and its terminal record adds a reason. Then: what
               do later lookups and submissions answer?
  corrupt      operations.json replaced by invalid JSON, then truncated JSON:
               what lookups/submissions answer, and recovery once restored.
  foreign      a record naming another ledger id: 409 operation_record_foreign.
Usage: c5_record_bounds.py OUTDIR [case ...]
"""
import json, os, shutil, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import og

GRAPH = "kg"


def server(d, port, env=None):
    root = og.make_root(d, d.name); og.apply_cluster(root)
    return og.Server(root, port, str(d / "server.log"), env or {}).start()


def near_bound(out):
    d = out / "near_bound"; d.mkdir()
    srv = server(d, 14801)
    h = srv.http
    res = {}
    lo, hi, best = 1000, 1_100_000, None
    # binary search the largest accepted request (each probe uses a fresh id)
    while lo <= hi:
        mid = (lo + hi) // 2
        names = ["x" * 1000] * (mid // 1004) + ["y" * (mid % 1004)]
        oid = og.ulid()
        s, b, _ = h.submit(GRAPH, oid, tables=names)
        if s in (200, 202):
            best = (mid, oid, s)
            lo = mid + 1
            # let it finish (it fails: unknown tables) before the next probe
            time.sleep(1.0)
        else:
            res.setdefault("refusals", {}).setdefault(str(s), (b or {}).get("error", "")[:200])
            hi = mid - 1
            if s == 409 and "unresolved" in json.dumps(b):
                break
        if len(res.get("probes", [])) > 40:
            break
        res.setdefault("probes", []).append((mid, s))
    res["largest_accepted_bytes"] = best and best[0]
    if best:
        time.sleep(2)
        s, b, _ = h.lookup(GRAPH, best[1])
        res["lookup_of_largest"] = (s, (b.get("operation") or {}).get("state") if isinstance(b, dict) and b.get("operation") else (b or {}).get("error", "")[:300])
        s, b, _ = h.lookup(GRAPH, og.ulid())
        res["lookup_other"] = (s, (b or {}).get("error", "")[:300] if s != 200 else b.get("lookup"))
        s, b, _ = h.submit(GRAPH, og.ulid())
        res["fresh_submit"] = (s, (b or {}).get("error", "")[:300] if s >= 400 else (b.get("operation") or {}).get("state"))
        rec = og.read_record(srv.root)
        found = next((o for o in og.record_ops(rec) if o["operation_id"] == best[1]), None)
        res["record_state_of_largest"] = found and found["state"]
        res["record_bytes"] = len(json.dumps(rec))
        srv.kill(15)
        srv2 = og.Server(srv.root, 14801, str(d / "server2.log"), {})
        try:
            srv2.start()
        except RuntimeError:
            og.unlock(srv.root); srv2.start()
        s, b, _ = srv2.http.lookup(GRAPH, best[1])
        res["after_restart_lookup"] = (s, (b.get("operation") or {}).get("state") if isinstance(b, dict) and b.get("operation") else b)
        srv2.kill(15)
    else:
        srv.kill(15)
    return res


def corrupt(out):
    d = out / "corrupt"; d.mkdir()
    srv = server(d, 14802)
    h = srv.http
    oid = og.ulid(); h.submit(GRAPH, oid); h.wait_terminal(GRAPH, oid)
    path = Path(srv.root) / "__cluster" / "operations.json"
    good = path.read_bytes()
    res = {}
    for name, bad in [("not_json", b"not a record"), ("truncated", good[: len(good) // 2]), ("empty", b""),
                      ("wrong_version", good.replace(b'"version":1', b'"version":2'))]:
        path.write_bytes(bad)
        s1, b1, _ = h.lookup(GRAPH, oid)
        s2, b2, _ = h.submit(GRAPH, og.ulid())
        res[name] = {"lookup": (s1, (b1 or {}).get("error", "")[:160] if s1 != 200 else "ok"),
                     "submit": (s2, (b2 or {}).get("error", "")[:160] if s2 >= 400 else "accepted"),
                     "file_unchanged": path.read_bytes() == bad}
        path.write_bytes(good)
    s, b, _ = h.lookup(GRAPH, oid)
    res["restored_lookup"] = (s, (b.get("operation") or {}).get("state"))
    srv.kill(15)
    return res


def foreign(out):
    d = out / "foreign"; d.mkdir()
    srv = server(d, 14803)
    h = srv.http
    oid = og.ulid(); h.submit(GRAPH, oid); h.wait_terminal(GRAPH, oid)
    path = Path(srv.root) / "__cluster" / "operations.json"
    rec = json.loads(path.read_text())
    rec["ledger_id"] = og.ulid()
    path.write_text(json.dumps(rec))
    before = path.read_bytes()
    s1, b1, _ = h.lookup(GRAPH, oid)
    s2, b2, _ = h.submit(GRAPH, og.ulid())
    res = {"lookup": (s1, (b1 or {}).get("error", "")[:200]), "submit": (s2, (b2 or {}).get("error", "")[:200]),
           "file_unchanged": path.read_bytes() == before}
    srv.kill(15)
    return res


def main():
    out = Path(sys.argv[1]); shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
    cases = sys.argv[2:] or ["near_bound", "corrupt", "foreign"]
    results = {}
    for c in cases:
        results[c] = globals()[c](out)
        print(c, json.dumps(results[c], indent=1, default=str)[:3000], flush=True)
    (out / "results.json").write_text(json.dumps(results, indent=1, default=str))


if __name__ == "__main__":
    main()
