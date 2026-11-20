#!/usr/bin/env python3
"""A graph without an applied policy refuses every action to every token,
reads included (c8dcb315). This probe serves two graphs, `g1` with a policy
that grants act-a everything and `g2` with none, and calls every route of the
OpenAPI document on g2 with act-a's token. Any 2xx that carries graph data is
a finding. It also checks MCP tools/call on g2 and that g1 still serves.

Usage: OG=... OGS=... nopolicy.py <openapi.json>"""
import json, os, shutil, signal, subprocess, sys, time, urllib.request, urllib.error

OG = os.environ.get("OG", "omnigraph")
OGS = os.environ.get("OGS", "omnigraph-server")
SPEC = json.load(open(sys.argv[1]))
D = "/tmp/og/nopolicy"
PORT = 18181
BASE = f"http://127.0.0.1:{PORT}"
TOK = "tok-a"


def http(method, path, body=None, headers=None, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    h = {"Authorization": "Bearer " + TOK, "Omnigraph-Http-Api": "0.14"}
    h.update(headers or {})
    for k, v in h.items():
        req.add_header(k, v)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except Exception as e:
        return None, repr(e)


SCHEMA = "node P {\n  slug: String @key\n  secret: String?\n  b: Blob?\n}\n"
POLICY = """version: 1
groups:
  ops: [act-a]
rules:
  - id: branch-actions
    allow:
      actors: { group: ops }
      actions: [read, export, change, maintain]
      branch_scope: any
  - id: graph-actions
    allow:
      actors: { group: ops }
      actions: [reclaim, invoke_query]
  - id: branch-to-branch
    allow:
      actors: { group: ops }
      actions: [branch_create, branch_merge, branch_delete]
      target_branch_scope: any
"""
Q = "query get_p($s: String) { match { $p: P { slug: $s } } return { $p.slug, $p.secret } }\n"


def main():
    shutil.rmtree(D, ignore_errors=True)
    os.makedirs(f"{D}/src/queries")
    os.makedirs(f"{D}/root")
    open(f"{D}/src/s.pg", "w").write(SCHEMA)
    open(f"{D}/src/queries/q.gq", "w").write(Q)
    open(f"{D}/src/policy.yaml", "w").write(POLICY)
    open(f"{D}/src/cluster.yaml", "w").write(
        "version: 1\nmetadata:\n  name: np\ngraphs:\n  g1:\n    schema: s.pg\n    queries: queries/\n"
        "  g2:\n    schema: s.pg\n    queries: queries/\npolicies:\n  gp:\n    file: policy.yaml\n    applies_to: [g1]\n")
    root = f"file://{D}/root"
    p = subprocess.run([OG, "cluster", "apply", "--target", root, "--config", f"{D}/src", "--json"], capture_output=True, text=True)
    if p.returncode:
        sys.exit("apply " + p.stdout[-800:] + p.stderr[-800:])
    # seed both graphs directly while the server is stopped
    for g in ("g1", "g2"):
        st = json.loads(subprocess.run([OG, "cluster", "status", "--target", root, "--json"], capture_output=True, text=True).stdout)
        lock = find(st, "lock_id")
        if lock:
            subprocess.run([OG, "cluster", "force-unlock", lock, "--target", root], capture_output=True, text=True)
        p = subprocess.run([OG, "mutate", "-e", "query m() { insert P { slug: \"k1\", secret: \"TOPSECRET\" } }",
                            "--target", root, "--graph", g, "--json"], capture_output=True, text=True)
        if p.returncode:
            print("seed", g, p.stdout[-400:], p.stderr[-400:])
    st = json.loads(subprocess.run([OG, "cluster", "status", "--target", root, "--json"], capture_output=True, text=True).stdout)
    lock = find(st, "lock_id")
    if lock:
        subprocess.run([OG, "cluster", "force-unlock", lock, "--target", root], capture_output=True, text=True)
    pc = subprocess.run([OG, "commit", "list", "--branch", "main", "--limit", "1", "--json", "--target", root, "--graph", "g2"], capture_output=True, text=True)
    g2_commit = json.loads(pc.stdout).get("head_commit_id") if pc.returncode == 0 else None
    st = json.loads(subprocess.run([OG, "cluster", "status", "--target", root, "--json"], capture_output=True, text=True).stdout)
    lock = find(st, "lock_id")
    if lock:
        subprocess.run([OG, "cluster", "force-unlock", lock, "--target", root], capture_output=True, text=True)
    e = dict(os.environ)
    e["OMNIGRAPH_SERVER_BEARER_TOKENS_JSON"] = json.dumps({"act-a": TOK})
    e["OMNIGRAPH_OPERATION_ROUTES"] = "true"
    log = open(f"{D}/server.log", "w")
    srv = subprocess.Popen([OGS, "--cluster", root, "--bind", f"127.0.0.1:{PORT}"], stdout=log, stderr=log, env=e)
    for _ in range(120):
        st, _ = http("GET", "/readyz")
        if st == 200:
            break
        time.sleep(0.5)
    else:
        srv.kill()
        sys.exit("server not ready; see " + f"{D}/server.log")
    # g1 must serve; find a commit id for path params
    st, out = http("POST", "/graphs/g1/query", {"query": Q, "name": "get_p", "params": {"s": "k1"}})
    print("g1 query", st, out[:200])
    st, out = http("GET", "/graphs/g1/commits?branch=main")
    commit = None
    try:
        commit = json.loads(out)["commits"][0]["graph_commit_id"]
    except Exception:
        commit = find(json.loads(out) if out.startswith("{") else {}, "graph_commit_id")
    findings = []
    bodies = {
        "query": {"query": Q, "name": "get_p", "params": {"s": "k1"}},
        "export": {"branch": "main"},
        "queries/get_p": {"params": {"s": "k1"}},
        "changes/baseline": {},
        "mcp": None,
    }
    for path, methods in SPEC["paths"].items():
        if not path.startswith("/graphs/{graph_id}"):
            continue
        for method in methods:
            if method not in ("get", "post", "head"):
                continue
            concrete = (path.replace("{graph_id}", "g2").replace("{commit_id}", commit or "x")
                        .replace("{query_name}", "get_p").replace("{operation_id}", "01JC0000000000000000000000")
                        .replace("{name}", "main").replace("{branch}", "main"))
            if "{" in concrete:
                concrete = concrete.split("{")[0].rstrip("/")
            body = None
            if method == "post":
                for key, b in bodies.items():
                    if concrete.endswith(key):
                        body = b
                if body is None:
                    body = {}
            q = ""
            if method == "get":
                q = "?branch=main&from=main&to=main&source=main&target=main&type=node&id=k1&property=b&type_name=P&selector=node%20P%20k1%20b"
            # mutating routes are probed too: a 2xx there is a finding as well
            st, out = http(method.upper(), concrete + q, body)
            leak = "TOPSECRET" in out or "k1" in out
            ok = st is not None and 200 <= st < 300
            line = f"{method.upper():5} {concrete:55} {st} {'LEAK' if leak else ''} {out[:150]!r}"
            print(line)
            if ok:
                findings.append(line)
    # MCP on g2
    st, out = http("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "p", "version": "0"}}},
                   {"Accept": "application/json, text/event-stream"})
    print("mcp init", st, out[:200])
    for tool, args in [("query", {"graph": "g2", "query": Q, "name": "get_p", "params": {"s": "k1"}}), ("query", {"graph": "g2", "name": "get_p", "params": {"s": "k1"}}),
                       ("schema", {"graph": "g2"}), ("branches", {"graph": "g2"}), ("access", {"graph": "g2"}), ("queries", {"graph": "g2"})]:
        st, out = http("POST", "/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool, "arguments": args}},
                       {"Accept": "application/json, text/event-stream"})
        leak = "TOPSECRET" in out
        print("mcp", tool, st, "LEAK" if leak else "", out[:200])
        if leak:
            findings.append(f"mcp {tool} leaks g2 data")
    # existence probing on g2: a real commit id against a made-up one
    for cid in (g2_commit, "01M7000000000000000000000Z", "hb1.01M7000000000000000000000Z.1.01M7000000000000000000000Z"):
        for method, path in (("POST", f"/graphs/g2/commits/{cid}/publish"), ("DELETE", f"/graphs/g2/commits/{cid}"),
                             ("GET", f"/graphs/g2/commits/{cid}"), ("GET", f"/graphs/g2/commits/{cid}/changes")):
            st, out = http(method, path, {} if method == "POST" else None)
            print("probe", method, path, st, out[:160])
    srv.send_signal(signal.SIGTERM)
    srv.wait(timeout=60)
    print("FINDINGS", json.dumps(findings, indent=1))


def find(o, key):
    if isinstance(o, dict):
        for k, v in o.items():
            if k == key and isinstance(v, str):
                return v
            r = find(v, key)
            if r:
                return r
    if isinstance(o, list):
        for v in o:
            r = find(v, key)
            if r:
                return r
    return None


if __name__ == "__main__":
    main()
