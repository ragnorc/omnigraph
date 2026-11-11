#!/usr/bin/env python3
"""Repro: while an export of a graph is in flight, branch create and branch
delete on that graph answer 413 payload_too_large ("resource limit exceeded
for stream_export_slots: actual 2, limit 1", action "fix"), and an export
started during a branch control is refused the same way.

Serves one local cluster graph. OG and OGS name the CLI and server binaries."""
import collections, json, os, re, shutil, signal, subprocess, threading, time, urllib.request, urllib.error

OG, OGS = os.environ["OG"], os.environ["OGS"]
D = "/tmp/og/repro-export-branch"
BASE = "http://127.0.0.1:18585"
POLICY = """version: 1
groups:
  ops: [act-a]
rules:
  - id: b
    allow:
      actors: { group: ops }
      actions: [read, export, change]
      branch_scope: any
  - id: t
    allow:
      actors: { group: ops }
      actions: [branch_create, branch_delete]
      target_branch_scope: any
"""


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, method=method)
    for k, v in (("Authorization", "Bearer tok-a"), ("Omnigraph-Http-Api", "0.14"), ("Content-Type", "application/json")):
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            r.read()
            return r.status, ""
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:400]


shutil.rmtree(D, ignore_errors=True)
os.makedirs(f"{D}/src")
os.makedirs(f"{D}/root")
open(f"{D}/src/g.pg", "w").write("node P {\n  slug: String @key\n  b: Blob?\n}\n")
open(f"{D}/src/p.yaml", "w").write(POLICY)
open(f"{D}/src/cluster.yaml", "w").write("version: 1\nmetadata:\n  name: r\ngraphs:\n  g:\n    schema: g.pg\npolicies:\n  p:\n    file: p.yaml\n    applies_to: [g]\n")
root = f"file://{D}/root"
subprocess.run([OG, "cluster", "apply", "--target", root, "--config", f"{D}/src"], check=True, capture_output=True)
m = re.search(r'"lock_id":\s*"([^"]+)"', subprocess.run([OG, "cluster", "status", "--target", root, "--json"], capture_output=True, text=True).stdout)
if m:
    subprocess.run([OG, "cluster", "force-unlock", m.group(1), "--target", root], check=True, capture_output=True)
e = dict(os.environ, OMNIGRAPH_SERVER_BEARER_TOKENS_JSON=json.dumps({"act-a": "tok-a"}))
srv = subprocess.Popen([OGS, "--cluster", root, "--bind", "127.0.0.1:18585"], stdout=open(f"{D}/server.log", "w"), stderr=subprocess.STDOUT, env=e)
for _ in range(120):
    try:
        if urllib.request.urlopen(BASE + "/readyz", timeout=2).status == 200:
            break
    except Exception:
        time.sleep(0.5)
# a few hundred rows with 2 KB Blobs, so an export takes a moment
for i in range(0, 300, 50):
    body = " ".join(f'insert P {{ slug: "k{j}", b: $b }}' for j in range(i, i + 50))
    st, out = call("POST", "/graphs/g/mutate", {"query": f"query m($b: Blob) {{ {body} }}", "params": {"b": "base64:" + "QUJD" * 512}})
    assert st == 200, out

# 1. one branch create with no export in flight succeeds
print("create alone:", call("POST", "/graphs/g/branches", {"name": "alone", "from": "main"}))
print("delete alone:", call("DELETE", "/graphs/g/branches/alone"))

# 2. one client exports in a loop; another creates and deletes branches
stop = time.time() + 20
counts, sample = collections.Counter(), {}


def exporter():
    while time.time() < stop:
        st, out = call("POST", "/graphs/g/export", {"branch": "main"})
        counts[("export", st)] += 1
        if st != 200:
            sample[("export", st)] = out


def brancher():
    i = 0
    while time.time() < stop:
        i += 1
        st, out = call("POST", "/graphs/g/branches", {"name": f"b{i}", "from": "main"})
        counts[("branch create", st)] += 1
        if st != 200:
            sample[("branch create", st)] = out
        st, out = call("DELETE", f"/graphs/g/branches/b{i}")
        counts[("branch delete", st)] += 1
        if st not in (200, 204, 404):
            sample[("branch delete", st)] = out


ts = [threading.Thread(target=exporter), threading.Thread(target=brancher)]
[t.start() for t in ts]
[t.join() for t in ts]
print(json.dumps({f"{k[0]} {k[1]}": v for k, v in sorted(counts.items())}, indent=1))
for k, v in sample.items():
    print(k, v)
srv.send_signal(signal.SIGTERM)
srv.wait(timeout=60)
