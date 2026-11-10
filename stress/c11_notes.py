#!/usr/bin/env python3
"""Round 2: brief rechecks of round-1 notes N1, N4, N5, N6, N13 on a local root.
Usage: c11_notes.py OUTDIR
"""
import json, shutil, signal, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import og
from c3_crash import Child, arm

out = Path(sys.argv[1]); shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
res = {}
root = og.make_root(out, "notes"); og.apply_cluster(root)
ctl = out / "ctl"; ctl.mkdir()
srv = Child(root, 14951, str(out / "child.log"), ctl, {"OMNIGRAPH_OPTIMIZE_MAX_GATE_HOLD_SECONDS": "60"}).start()
h = srv.http
for i in range(3):
    h.load("kg", json.dumps({"type": "Person", "data": {"name": f"s{i}", "bio": "b"}}))
# N6: requests that can only fail
for name, tables in (("empty_tables", []), ("duplicate_tables", ["node:Person", "node:Person"])):
    oid = og.ulid()
    s, b, _ = h.submit("kg", oid, tables=tables)
    term = h.wait_terminal("kg", oid, 60) if s in (200, 202) else None
    res[f"N6_{name}"] = {"submit": s, "error": (b or {}).get("error"), "terminal": term and (term["state"], term.get("reason"))}
# N1: fill the terminal list past 32 so the oldest ids are evicted, then
# resubmit an evicted id with an over-ceiling hold.
first = og.ulid()
h.submit("kg", first); h.wait_terminal("kg", first, 60)
for _ in range(34):
    oid = og.ulid(); h.submit("kg", oid); h.wait_terminal("kg", oid, 60)
s, b, _ = h.submit("kg", first)
res["N1_evicted_id_plain"] = (s, (b or {}).get("code"), (b or {}).get("error", "")[:160])
s, b, _ = h.submit("kg", first, hold_ms=10**9)
res["N1_evicted_id_over_ceiling"] = (s, (b or {}).get("code"), (b or {}).get("error", "")[:160])
# N13: an export while a run holds its export exclusion
arm(ctl, "optimize.before_compact", "hold")
for i in range(3):
    h.load("kg", json.dumps({"type": "Person", "data": {"name": f"q{i}", "bio": "b"}}))
oid = og.ulid(); h.submit("kg", oid)
time.sleep(1.5)
s, b, hd = h.call("POST", "/graphs/kg/export", {"branch": "main"}, token="op-secret")
res["N13_export_during_run"] = (s, (b or {}).get("code") if isinstance(b, dict) else None, str((b or {}).get("error") if isinstance(b, dict) else b)[:240], hd.get("retry-after"))
(ctl / "optimize.before_compact.release").write_text("")
h.wait_terminal("kg", oid, 60)
srv.kill(signal.SIGTERM)
# N5: a damaged record
p = Path(root) / "__cluster" / "operations.json"
good = p.read_text()
p.write_text(good[: len(good) // 2])
srv2 = og.Server(root, 14952, str(out / "server2.log")).start()
s, b, _ = srv2.http.lookup("kg", oid)
res["N5_damaged_lookup"] = (s, (b or {}).get("code"), (b or {}).get("error", "")[:200])
s, b, _ = srv2.http.submit("kg", og.ulid())
res["N5_damaged_submit"] = (s, (b or {}).get("code"), (b or {}).get("error", "")[:200])
res["N5_record_untouched"] = p.read_text() == good[: len(good) // 2]
srv2.kill(signal.SIGTERM)
(out / "result.json").write_text(json.dumps(res, indent=1, default=str))
print(json.dumps(res, indent=1, default=str))
