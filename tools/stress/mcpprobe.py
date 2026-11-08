#!/usr/bin/env python3
"""MCP endpoint journey against a local server: every documented contract of
docs/user/operations/mcp.md that a client can observe without OIDC. Prints one
line per check and a FAIL line for each mismatch.

Usage: mcpprobe.py <base_url> <graph> <token>"""
import json, sys, threading, urllib.request, urllib.error

BASE, G, TOK = sys.argv[1], sys.argv[2], sys.argv[3]
fails = []
_id = [0]


def post(body, headers=None, raw=None):
    data = raw if raw is not None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + "/mcp", data=data, method="POST")
    h = {"Authorization": "Bearer " + TOK, "Content-Type": "application/json",
         "Accept": "application/json, text/event-stream"}
    h.update(headers or {})
    for k, v in h.items():
        if v is None:
            continue
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            txt = r.read().decode()
            return r.status, parse(txt)
    except urllib.error.HTTPError as e:
        txt = e.read().decode()
        return e.code, parse(txt)


def parse(txt):
    txt = txt.strip()
    if txt.startswith("data:") or "\ndata:" in txt:
        datas = [l[5:].strip() for l in txt.splitlines() if l.startswith("data:")]
        txt = datas[-1] if datas else ""
    try:
        return json.loads(txt)
    except Exception:
        return txt


def rpc(method, params=None, headers=None):
    _id[0] += 1
    return post({"jsonrpc": "2.0", "id": _id[0], "method": method, "params": params or {}}, headers)


def tool(name, args):
    st, out = rpc("tools/call", {"name": name, "arguments": args})
    res = out.get("result", {}) if isinstance(out, dict) else {}
    return st, out, res


def check(cond, label, detail=""):
    print(("ok   " if cond else "FAIL ") + label + ("" if cond else f" :: {str(detail)[:500]}"), flush=True)
    if not cond:
        fails.append(label)


st, out = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "probe", "version": "0"}})
check(st == 200 and "result" in out, "initialize", out)
st, out = rpc("tools/list")
tools = {t["name"]: t for t in out.get("result", {}).get("tools", [])}
check(len(tools) >= 20, f"tools/list lists {len(tools)} tools", sorted(tools))
ro = {n for n, t in tools.items() if t.get("annotations", {}).get("readOnlyHint")}
for n in ["mutate", "load", "branches_create", "branches_delete", "branches_merge", "blobs_clear", "commits_publish", "commits_drop"]:
    check(n in tools and n not in ro, f"{n} is listed and not readOnly", tools.get(n, {}).get("annotations"))
for n in ["query", "schema", "snapshot", "diff", "changes_poll", "blobs_stat"]:
    check(n in ro, f"{n} is readOnly")

# reads
st, out, res = tool("query", {"graph": G, "query": "query q() { match { $p: P } return { $p.slug } limit 3 }"})
check(not res.get("isError") and res.get("structuredContent", {}).get("rows") is not None, "query tool reads rows", res)
head = res.get("structuredContent", {}).get("graph_commit_id")
st, out, res = tool("query", {"graph": G, "query": 'query m() { insert P { slug: "mcp-x" } }'})
check(res.get("isError") and res.get("structuredContent", res).get("status", None) in (400, None), "query tool refuses insert", res)
st, out, res = tool("schema", {"graph": "nope"})
check(res.get("isError"), "unknown graph is an error", res)
sc = res.get("structuredContent") or {}
check(sc.get("status") == 404, "unknown graph answers 404", res)

# conditional writes
for bad in ["", "not-a-commit"]:
    st, out, res = tool("mutate", {"graph": G, "query": 'query m() { insert P { slug: "mcp-cond" } }', "if_graph_commit": bad})
    sc = res.get("structuredContent") or {}
    check(res.get("isError") and sc.get("status") == 400, f"if_graph_commit={bad!r} refused with 400", res)
st, out, res = tool("query", {"graph": G, "query": 'query q() { match { $p: P { slug: "mcp-cond" } } return { $p.slug } }'})
check(res.get("structuredContent", {}).get("rows") == [], "refused conditional writes wrote nothing", res)
st, out, res = tool("mutate", {"graph": G, "query": 'query m() { insert P { slug: "mcp-a" } }'})
check(not res.get("isError"), "unconditional mutate", res)
if head:
    st, out, res = tool("mutate", {"graph": G, "query": 'query m() { insert P { slug: "mcp-stale" } }', "if_graph_commit": head})
    sc = res.get("structuredContent") or {}
    check(res.get("isError") and sc.get("status") == 412, "stale if_graph_commit answers 412", res)

# branches
st, out, res = tool("branches_create", {"graph": G, "name": "mcpb"})
check(not res.get("isError"), "branches_create", res)
st, out, res = tool("mutate", {"graph": G, "query": 'query m() { insert P { slug: "mcp-on-b" } }', "branch": "mcpb"})
check(not res.get("isError"), "mutate on branch", res)
st, out, res = tool("branches_merge", {"graph": G, "source": "mcpb"})
check(not res.get("isError"), "branches_merge", res)
st, out, res = tool("branches_delete", {"graph": G, "name": "mcpb"})
check(not res.get("isError"), "branches_delete", res)

# load
data = "\n".join(json.dumps({"type": "P", "data": {"slug": f"mcp-l{i}", "n": i}}) for i in range(5))
st, out, res = tool("load", {"graph": G, "mode": "merge", "data": data})
check(not res.get("isError"), "load tool", res)

# limits
big = "x" * (70 * 1024)
st, out = rpc("tools/call", {"name": "query", "arguments": {"graph": G, "query": "query q() { match { $p: P } return { $p.slug } limit 1 }", "params": {"pad": big}}})
check(st in (413, 400) or (isinstance(out, dict) and ("error" in out or out.get("result", {}).get("isError"))), f"request over 64 KiB refused (http {st})", str(out)[:300])
st, out, res = tool("query", {"graph": G, "query": "query q() { match { $p: P } return { $p.slug, $p.s, $p.n } }"})
check(True, f"full read result isError={res.get('isError')} size={len(json.dumps(out))}")

# host and origin
st, out = rpc("tools/list", headers={"Host": "evil.example.com"})
check(st == 403, f"foreign Host refused (http {st})", out)
st, out = rpc("tools/list", headers={"Origin": "http://localhost:3000"})
check(st == 403, f"Origin refused without OIDC (http {st})", out)

# auth
st, out = rpc("tools/list", headers={"Authorization": "Bearer wrong"})
check(st == 401, f"bad token refused with 401 (http {st})", out)

# resources
st, out = rpc("resources/read", {"uri": "omnigraph://best-practices/queries"})
check(st == 200 and "result" in out, "best-practices resource", str(out)[:200])
st, out = rpc("resources/read", {"uri": "omnigraph://graphs/nope/schema"})
check(isinstance(out, dict) and out.get("error", {}).get("code") == -32002, "unknown graph resource is -32002", out)
st, out = rpc("resources/read", {"uri": f"omnigraph://graphs/{G}/schema"})
check(st == 200 and "result" in out, "schema resource", str(out)[:200])

# concurrency: 40 parallel slow-ish reads, expect some too_many_requests or all ok
res_codes = []
lock = threading.Lock()


def par():
    st, out, res = tool("query", {"graph": G, "query": "query q() { match { $p: P } return { $p.slug, $p.s } }"})
    sc = res.get("structuredContent") or {}
    code = (sc.get("error") or {}).get("code") if res.get("isError") else "ok"
    with lock:
        res_codes.append((st, code))


ts = [threading.Thread(target=par) for _ in range(40)]
[t.start() for t in ts]
[t.join() for t in ts]
from collections import Counter
print("parallel 40:", Counter(res_codes))
check(all(c in ("ok", "too_many_requests") for _, c in res_codes), "parallel calls answer ok or too_many_requests", Counter(res_codes))

# malformed JSON-RPC
st, out = post(None, raw=b"{not json")
check(st in (400, 200) and isinstance(out, (dict, str)), f"malformed body answered (http {st})", out)
print(f"done fails={len(fails)} {fails}")
