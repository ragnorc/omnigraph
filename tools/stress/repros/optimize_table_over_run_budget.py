#!/usr/bin/env python3
"""A served recorded optimize with only OMNIGRAPH_OPTIMIZE_RUN_SOURCE_BYTES set
never compacts a table whose source bytes pass that budget: the budget is
also the table byte ceiling (OptimizeLimits::table_source_bytes), so every
full-catalog run defers the table with `resource_limit`, and resume_at skips
it because only run-wide bounds set it. The user guide says successive bounded
runs reach every table and that resume_at names the first deferred table.

Usage: OG=... OGS=... optimize_table_over_run_budget.py"""
import json, os, shutil, subprocess, sys, time, urllib.request, urllib.error, secrets

OG, OGS = os.environ.get("OG", "omnigraph"), os.environ.get("OGS", "omnigraph-server")
D = "/tmp/og/repro-optbudget"
ROOT = f"{D}/root"
BASE = "http://127.0.0.1:18120"
H = {"Authorization": "Bearer tok", "Omnigraph-Http-Api": "0.14", "Content-Type": "application/json"}


def api(m, p, b=None):
    req = urllib.request.Request(BASE + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers=H)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def ulid():
    v = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    a = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    return "".join(a[(v >> (5 * i)) & 31] for i in reversed(range(26)))


shutil.rmtree(D, ignore_errors=True)
os.makedirs(f"{D}/src")
os.makedirs(ROOT)
open(f"{D}/src/g.pg", "w").write("node Small {\n  slug: String @key\n}\nnode Big {\n  slug: String @key\n  body: String\n}\n")
open(f"{D}/src/p.yaml", "w").write("version: 1\ngroups:\n  ops: [act]\nrules:\n  - id: all\n    allow:\n      actors: { group: ops }\n      actions: [read, change, maintain]\n      branch_scope: any\n")
open(f"{D}/src/cluster.yaml", "w").write("version: 1\nmetadata:\n  name: r\ngraphs:\n  g:\n    schema: g.pg\npolicies:\n  p:\n    file: p.yaml\n    applies_to: [g]\n")
subprocess.run([OG, "cluster", "apply", "--target", ROOT, "--config", f"{D}/src", "--json"], check=True, capture_output=True)
st = json.loads(subprocess.run([OG, "cluster", "status", "--target", ROOT, "--json"], capture_output=True, text=True).stdout)
lock = json.dumps(st)
import re
m = re.search(r'"lock_id": "([0-9A-Z]+)"', lock)
if m:
    subprocess.run([OG, "cluster", "force-unlock", m.group(1), "--target", ROOT, "--yes"], capture_output=True)
env = dict(os.environ, OMNIGRAPH_SERVER_BEARER_TOKENS_JSON='{"act":"tok"}', OMNIGRAPH_OPERATION_ROUTES="true",
           OMNIGRAPH_OPTIMIZE_RUN_SOURCE_BYTES="20000")
srv = subprocess.Popen([OGS, "--cluster", ROOT, "--bind", "127.0.0.1:18120"], stdout=open(f"{D}/server.log", "w"), stderr=subprocess.STDOUT, env=env)
try:
    for _ in range(120):
        try:
            urllib.request.urlopen(BASE + "/readyz", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    for i in range(30):  # 30 commits of about 1 KB each to Big, 30 tiny commits to Small
        api("POST", "/graphs/g/mutate", {"query": "query m($k: String, $b: String) { insert Big { slug: $k, body: $b } }", "params": {"k": f"b{i}", "b": "x" * 1000}})
        api("POST", "/graphs/g/mutate", {"query": "query m($k: String) { insert Small { slug: $k } }", "params": {"k": f"s{i}"}})
    for run in range(5):
        oid = ulid()
        api("POST", "/graphs/g/operations/optimize", {"operation_id": oid})
        for _ in range(120):
            s, out = api("GET", f"/graphs/g/operations/{oid}")
            op = out.get("operation", {}) if isinstance(out, dict) else {}
            if op.get("state") in ("succeeded", "failed", "cancelled", "interrupted"):
                break
            time.sleep(0.25)
        o = op.get("outcome", {})
        print(f"run {run}: state={op.get('state')} start_at={o.get('start_at')} resume_at={o.get('resume_at')}")
        for t in o.get("tables", []):
            print(f"   {t['type_key']}: {t['outcome']} {t.get('reason', '')} {t.get('detail', '')}")
finally:
    srv.terminate()
    srv.wait()
