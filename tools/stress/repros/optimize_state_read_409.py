#!/usr/bin/env python3
"""Repro: while the object store resets connections, the recorded-operation
routes answer a failed read of __cluster/state.json with 409 conflict.

Needs RustFS on 127.0.0.1:9000 (bucket omnigraph-ci, keys omnigraphci /
omnigraphci-secret) and toxiproxy-server on 127.0.0.1:8474. OG and OGS name
the CLI and server binaries."""
import json, os, shutil, signal, subprocess, sys, time, urllib.request, urllib.error

OG, OGS = os.environ["OG"], os.environ["OGS"]
ROOT = f"s3://omnigraph-ci/stress/repro409-{int(time.time())}"
D = "/tmp/og/repro409"
BASE = "http://127.0.0.1:18282"


def env(endpoint):
    e = dict(os.environ)
    e.update({"AWS_ACCESS_KEY_ID": "omnigraphci", "AWS_SECRET_ACCESS_KEY": "omnigraphci-secret", "AWS_REGION": "us-east-1",
              "AWS_ALLOW_HTTP": "true", "AWS_S3_FORCE_PATH_STYLE": "true", "AWS_ENDPOINT_URL": endpoint, "AWS_ENDPOINT_URL_S3": endpoint})
    return e


def http(method, url, body=None, auth=True):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if auth:
        req.add_header("Authorization", "Bearer tok-a")
        req.add_header("Omnigraph-Http-Api", "0.14")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return None, repr(e)


def ulid():
    import secrets
    v = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    a = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    return "".join(a[(v >> (5 * i)) & 31] for i in range(25, -1, -1))


shutil.rmtree(D, ignore_errors=True)
os.makedirs(D)
open(f"{D}/g.pg", "w").write("node P {\n  slug: String @key\n}\n")
open(f"{D}/p.yaml", "w").write("version: 1\ngroups:\n  ops: [act-a]\nrules:\n  - id: r\n    allow:\n      actors: { group: ops }\n      actions: [read, change, maintain]\n      branch_scope: any\n")
open(f"{D}/cluster.yaml", "w").write("version: 1\nmetadata:\n  name: r\ngraphs:\n  g:\n    schema: g.pg\npolicies:\n  p:\n    file: p.yaml\n    applies_to: [g]\n")
direct = env("http://127.0.0.1:9000")
p = subprocess.run([OG, "cluster", "apply", "--target", ROOT, "--config", D, "--json"], capture_output=True, text=True, env=direct)
assert p.returncode == 0, p.stderr[-800:]
st = subprocess.run([OG, "cluster", "status", "--target", ROOT, "--json"], capture_output=True, text=True, env=direct).stdout
lock = json.loads(st).get("admission", {}).get("lock_id") if False else None
import re
m = re.search(r'"lock_id":\s*"([^"]+)"', st)
if m:
    subprocess.run([OG, "cluster", "force-unlock", m.group(1), "--target", ROOT], capture_output=True, text=True, env=direct)
http("DELETE", "http://127.0.0.1:8474/proxies/s3r", auth=False)
http("POST", "http://127.0.0.1:8474/proxies", {"name": "s3r", "listen": "127.0.0.1:9101", "upstream": "127.0.0.1:9000"}, auth=False)
e = env("http://127.0.0.1:9101")
e["OMNIGRAPH_SERVER_BEARER_TOKENS_JSON"] = json.dumps({"act-a": "tok-a"})
e["OMNIGRAPH_OPERATION_ROUTES"] = "true"
srv = subprocess.Popen([OGS, "--cluster", ROOT, "--bind", "127.0.0.1:18282"], stdout=open(f"{D}/server.log", "w"), stderr=subprocess.STDOUT, env=e)
for _ in range(120):
    if http("GET", BASE + "/readyz", auth=False)[0] == 200:
        break
    time.sleep(0.5)
print("baseline submit", *http("POST", BASE + "/graphs/g/operations/optimize", {"operation_id": ulid()}))
time.sleep(2)
# every response from the store is cut after 0 bytes: the reads fail, briefly
http("POST", "http://127.0.0.1:8474/proxies/s3r/toxics", {"name": "cut", "type": "reset_peer", "stream": "downstream", "toxicity": 1.0, "attributes": {"timeout": 0}}, auth=False)
oid = ulid()
print("submit under faults", *http("POST", BASE + "/graphs/g/operations/optimize", {"operation_id": oid}))
print("lookup under faults", *http("GET", BASE + f"/graphs/g/operations/{oid}"))
print("cancel under faults", *http("POST", BASE + f"/graphs/g/operations/{oid}/cancel", {}))
print("mutate under faults", *http("POST", BASE + "/graphs/g/mutate", {"query": "query m() { insert P { slug: \"a\" } }", "params": {}}))
http("DELETE", "http://127.0.0.1:8474/proxies/s3r/toxics/cut", auth=False)
time.sleep(1)
print("lookup after faults", *http("GET", BASE + f"/graphs/g/operations/{oid}"))
srv.send_signal(signal.SIGTERM)
try:
    srv.wait(timeout=60)
except subprocess.TimeoutExpired:
    srv.kill()
print("server exit", srv.returncode)
