"""The serving dimension: an OLD cluster with edition-2 stored queries and
policies, then NEW's server and CLI on that cluster directory.

Journey (each step recorded to `<work>/results/serve.jsonl`):

1. OLD: `cluster validate`, `cluster apply`, a direct `load` per graph,
   `force-unlock`; OLD server boots (default, then `--require-all-graphs`),
   every probe below answers;
2. NEW: `queries validate`, `queries list`, `cluster plan` on the OLD root;
3. NEW server on the OLD root: default boot, strict boot
   (`OMNIGRAPH_STRICT_STORED_QUERIES=1`), strict with
   `--require-all-graphs`; probes: `/healthz`, `/readyz`, `/graphs`,
   `/graphs/{id}/queries`, every stored query invoked, an inline read;
4. OLD server again after NEW's servers ran (rollback before any apply);
5. the documented upgrade: rewrite the registry in edition 3, NEW
   `cluster validate` and direct `cluster apply`, NEW server, probes;
6. OLD server and CLI after NEW's apply (rollback after the switch).
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import catalog
from gen import BASE_PG, Data, write_jsonl
from lib import WORK, SERVER, og, append_jsonl, ENV

OUT = WORK / "results" / "serve.jsonl"
TOKEN = "mm-secret-token"
API = "0.14"

# kg: a mix of stored reads, searches and a write, most of which edition 3
# refuses; kg2: only texts both editions compile.
KG_QUERIES = ["q_traverse", "q_brace_enum", "q_scalars_all", "q_limit_no_order", "s_search_title",
              "s_bm25_title", "s_nearest", "s_rrf", "q_not_block"]
KG_WRITES = ["w_insert_new_key", "w_update_node"]
KG2_QUERIES = ["q_scalars_all", "q_distinct", "q_avg_int_extremes"]

GRAPH_POLICY = """version: 1
groups:
  ops: [act-alice]
rules:
  - id: ops-read
    allow:
      actors: { group: ops }
      actions: [read, export, change, maintain]
      branch_scope: any
  - id: ops-invoke
    allow:
      actors: { group: ops }
      actions: [invoke_query, reclaim]
  - id: ops-branches
    allow:
      actors: { group: ops }
      actions: [branch_create, branch_delete, branch_merge, schema_apply, commit_publish, commit_drop]
      target_branch_scope: any
"""
SERVER_POLICY = """version: 1
groups:
  ops: [act-alice]
rules:
  - id: ops-list
    allow:
      actors: { group: ops }
      actions: [graph_list]
  - id: ops-config
    allow:
      actors: { group: ops }
      actions: [config_manage]
"""


def rec(**kw):
    append_jsonl(OUT, kw)
    print(json.dumps(kw, default=str)[:300], flush=True)


BLOBS = WORK / "serve" / "blobs"


def write_source(src: Path, edition: str) -> None:
    if src.exists():
        shutil.rmtree(src)
    (src / "queries").mkdir(parents=True)
    (src / "queries2").mkdir()
    (src / "schema.pg").write_text(BASE_PG)
    (src / "graph.policy.yaml").write_text(GRAPH_POLICY)
    (src / "server.policy.yaml").write_text(SERVER_POLICY)
    (src / "cluster.yaml").write_text(("""version: 1
state: { backend: cluster, lock: true }
graphs:
  kg:
    schema: schema.pg
    queries: queries/
  kg2:
    schema: schema.pg
    queries: queries2/
policies:
  graph-access:
    file: graph.policy.yaml
    applies_to: [kg, kg2]
  server-access:
    file: server.policy.yaml
    applies_to: [cluster]
""").replace("BLOBS", str(BLOBS)))
    byname = {q.name: q for q in catalog.READS + catalog.SEARCHES + catalog.WRITES}
    pick = (lambda q: q.e2) if edition == "e2" else (lambda q: q.e3)
    (src / "queries" / "reads.gq").write_text("\n\n".join(pick(byname[n]) for n in KG_QUERIES))
    (src / "queries" / "writes.gq").write_text("\n\n".join(pick(byname[n]) for n in KG_WRITES))
    (src / "queries2" / "reads.gq").write_text("\n\n".join(pick(byname[n]) for n in KG2_QUERIES))


def http(method: str, port: int, path: str, body=None, auth=True, api=True):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    if body is not None:
        req.add_header("content-type", "application/json")
    if auth:
        req.add_header("authorization", f"Bearer {TOKEN}")
    if api:
        req.add_header("Omnigraph-Http-Api", API)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    except Exception as e:
        return {"status": None, "error": repr(e), "secs": time.perf_counter() - t0}
    try:
        payload = json.loads(raw)
    except Exception:
        payload = raw.decode("utf-8", "replace")[-2000:]
    return {"status": status, "body": payload, "secs": round(time.perf_counter() - t0, 4)}


class Server:
    def __init__(self, side: str, root: Path, port: int, extra=(), env=None, label=""):
        self.side, self.root, self.port, self.label = side, root, port, label
        e = dict(ENV)
        e["OMNIGRAPH_SERVER_BEARER_TOKENS_JSON"] = json.dumps({"act-alice": TOKEN})
        e["OMNIGRAPH_HOME"] = str(WORK / "home" / f"server-{side}")
        e.update(env or {})
        self.log = WORK / "logs" / f"server-{label}.log"
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self.p = subprocess.Popen([str(SERVER[side]), "--cluster", f"file://{root}", "--bind", f"127.0.0.1:{port}", *extra],
                                  stdout=self.log.open("w"), stderr=subprocess.STDOUT, env=e, stdin=subprocess.DEVNULL)

    def wait(self, secs=60) -> str:
        t0 = time.time()
        while time.time() - t0 < secs:
            if self.p.poll() is not None:
                return "exited"
            r = http("GET", self.port, "/healthz", auth=False, api=False)
            if r.get("status"):
                return "up"
            time.sleep(0.3)
        return "timeout"

    def stop(self) -> int | None:
        if self.p.poll() is None:
            self.p.send_signal(signal.SIGTERM)
            try:
                self.p.wait(30)
            except subprocess.TimeoutExpired:
                self.p.kill()
        return self.p.returncode

    def tail(self, n=4000) -> str:
        return self.log.read_text()[-n:]


def blob_get(port: int):
    # a managed Blob: external references need an S3-compatible base (a
    # server-safe base refuses file://, and direct CLI writes are deny-only)
    return http("GET", port, "/graphs/kg/blob?entity=node&type=Asset&id=a003&property=content")


def probe_server(srv: Server, step: str, compare_to: dict | None = None) -> dict:
    out = {}
    port = srv.port
    out["blob.ext"] = blob_get(port)
    out["healthz"] = http("GET", port, "/healthz", auth=False, api=False)
    out["readyz"] = http("GET", port, "/readyz", auth=False, api=False)
    out["graphs"] = http("GET", port, "/graphs")
    for gid in ("kg", "kg2"):
        out[f"{gid}.queries"] = http("GET", port, f"/graphs/{gid}/queries")
        names = KG_QUERIES + KG_WRITES if gid == "kg" else KG2_QUERIES
        byname = {q.name: q for q in catalog.READS + catalog.SEARCHES + catalog.WRITES}
        for n in names:
            if n.startswith("w_"):
                continue  # writes are invoked once, at the end
            out[f"{gid}.invoke.{n}"] = http("POST", port, f"/graphs/{gid}/queries/{n}", {"params": byname[n].params})
        out[f"{gid}.inline_e3"] = http("POST", port, f"/graphs/{gid}/query",
                                       {"query": byname["q_brace_enum"].e3, "name": "q_brace_enum"})
        out[f"{gid}.inline_e2"] = http("POST", port, f"/graphs/{gid}/query",
                                       {"query": byname["q_brace_enum"].e2, "name": "q_brace_enum"})
    out["kg.invoke.w_insert_new_key"] = http("POST", port, "/graphs/kg/queries/w_insert_new_key", {"params": {}})
    rec(step=step, kind="server_probe", side=srv.side, probes=out)
    return out


def cli(side, *args, **kw):
    r = og(side, *args, home=WORK / "home" / f"cli-{side}", **kw)
    return {"rc": r.rc, "ok": r.ok, "msg": None if r.ok else r.message()[-3000:], "json": r.json(),
            "out": r.out[-3000:] if r.json() is None else None}


def unlock(side: str, root: Path, step: str):
    st = cli(side, "cluster", "status", "--target", root, "--json")
    lock = None
    j = st.get("json") or {}
    for key in ("lock_id", "lock"):
        v = j.get(key)
        if isinstance(v, str):
            lock = v
        elif isinstance(v, dict):
            lock = v.get("lock_id") or v.get("id")
    res = None
    if lock:
        res = cli(side, "cluster", "force-unlock", lock, "--target", root)
    rec(step=step, kind="unlock", side=side, status=st, lock=lock, unlock=res)


def main():
    base = WORK / "serve"
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True)
    if OUT.exists():
        OUT.unlink()
    src, root = base / "src", base / "root"
    root.mkdir()
    write_source(src, "e2")
    rec(step="old.cluster_validate", kind="cli", **cli("old", "cluster", "validate", "--config", src, "--json"))
    rec(step="old.cluster_apply", kind="cli", **cli("old", "cluster", "apply", "--target", root, "--config", src, "--as", "act-alice", "--json"))
    data = Data(seed=7)
    rows = data.base_rows()
    write_jsonl(base / "base.jsonl", rows)
    # a direct writer on a cluster root keeps the cluster lock: release it
    # after each one (nothing else runs)
    for gid in ("kg", "kg2"):
        rec(step=f"old.load.{gid}", kind="cli", **cli("old", "load", "--data", base / "base.jsonl", "--mode", "merge",
                                                       "--target", root, "--graph", gid, "--as", "act-alice", "--json"))
        unlock("old", root, f"old.unlock_after_load.{gid}")
        rec(step=f"old.index_build.{gid}", kind="cli", **cli("old", "index", "build", "--target", root, "--graph", gid, "--as", "act-alice", "--json"))
        unlock("old", root, f"old.unlock_after_index.{gid}")

    # OLD serving, two boot settings
    BLOBS.mkdir(parents=True, exist_ok=True)
    (BLOBS / "x.bin").write_bytes(b"external bytes for the migration matrix")
    for label, extra in (("old-default", ()), ("old-require-all", ("--require-all-graphs",))):
        s = Server("old", root, 18180, extra, label=label)
        st = s.wait()
        rec(step=f"{label}.boot", kind="boot", state=st, log=s.tail(1500) if st != "up" else None)
        if st == "up":
            probe_server(s, label)
        rc = s.stop()
        rec(step=f"{label}.stop", kind="stop", rc=rc)
        unlock("old", root, f"{label}.unlock")
    rec(step="old.queries_validate", kind="cli", **cli("old", "queries", "validate", "--target", root, "--json"))

    # NEW's CLI on the OLD root
    rec(step="new.queries_validate", kind="cli", **cli("new", "queries", "validate", "--target", root, "--json"))
    rec(step="new.queries_validate.kg2", kind="cli", **cli("new", "queries", "validate", "--target", root, "--graph", "kg2", "--json"))
    rec(step="new.queries_list.kg", kind="cli", **cli("new", "queries", "list", "--target", root, "--graph", "kg", "--json"))
    rec(step="new.cluster_validate_e2_source", kind="cli", **cli("new", "cluster", "validate", "--config", src, "--json"))
    rec(step="new.cluster_plan_e2_source", kind="cli", **cli("new", "cluster", "plan", "--target", root, "--config", src, "--json"))
    rec(step="new.cluster_status", kind="cli", **cli("new", "cluster", "status", "--target", root, "--json"))
    rec(step="new.access", kind="cli", **cli("new", "access", "--target", root, "--graph", "kg", "--as", "act-alice", "--json"))
    for side in ("old", "new"):
        rec(step=f"{side}.blob_stat", kind="cli", **cli(side, "blob", "stat", "node", "Asset", "a003", "content",
                                                        "--target", root, "--graph", "kg", "--json"))
        rec(step=f"{side}.export_kg", kind="cli_export", **{k: v for k, v in cli(side, "export", "--target", root, "--graph", "kg").items() if k != "json"})
    rec(step="new.policy_validate", kind="cli", **cli("new", "policy", "validate", "--target", root, "--graph", "kg"))

    # NEW serving the OLD root
    boots = (("new-default", (), {}),
             ("new-strict", (), {"OMNIGRAPH_STRICT_STORED_QUERIES": "1"}),
             ("new-strict-require-all", ("--require-all-graphs",), {"OMNIGRAPH_STRICT_STORED_QUERIES": "1"}),
             ("new-default-require-all", ("--require-all-graphs",), {}))
    for label, extra, env in boots:
        s = Server("new", root, 18181, extra, env, label=label)
        st = s.wait()
        rec(step=f"{label}.boot", kind="boot", state=st, log=s.tail(3000))
        if st == "up":
            probe_server(s, label)
        rc = s.stop()
        rec(step=f"{label}.stop", kind="stop", rc=rc, log_tail=s.tail(3000))
        unlock("new", root, f"{label}.unlock")

    # OLD again, before any NEW apply
    s = Server("old", root, 18180, (), label="old-after-new-serve")
    st = s.wait()
    rec(step="old-after-new-serve.boot", kind="boot", state=st, log=s.tail(1500) if st != "up" else None)
    if st == "up":
        probe_server(s, "old-after-new-serve")
    s.stop()
    unlock("old", root, "old-after-new-serve.unlock")

    # NEW applying the edition-2 source as it stands
    rec(step="new.cluster_apply_e2_source", kind="cli", **cli("new", "cluster", "apply", "--target", root, "--config", src, "--as", "act-alice", "--json"))
    # the documented upgrade: rewrite in edition 3, validate, apply directly
    src3 = base / "src3"
    write_source(src3, "e3")
    rec(step="new.cluster_validate_e3_source", kind="cli", **cli("new", "cluster", "validate", "--config", src3, "--json"))
    rec(step="new.cluster_plan_e3_source", kind="cli", **cli("new", "cluster", "plan", "--target", root, "--config", src3, "--json"))
    rec(step="new.cluster_apply_e3_source", kind="cli", **cli("new", "cluster", "apply", "--target", root, "--config", src3, "--as", "act-alice", "--json"))
    unlock("new", root, "new.apply.unlock")
    rec(step="new.queries_validate_after_apply", kind="cli", **cli("new", "queries", "validate", "--target", root, "--json"))
    s = Server("new", root, 18181, (), label="new-after-apply")
    st = s.wait()
    rec(step="new-after-apply.boot", kind="boot", state=st, log=s.tail(3000))
    if st == "up":
        probe_server(s, "new-after-apply")
    s.stop()
    unlock("new", root, "new-after-apply.unlock")

    # rollback: OLD after NEW's apply
    rec(step="old.queries_validate_after_new_apply", kind="cli", **cli("old", "queries", "validate", "--target", root, "--json"))
    rec(step="old.cluster_status_after_new_apply", kind="cli", **cli("old", "cluster", "status", "--target", root, "--json"))
    for label, extra in (("old-after-new-apply", ()), ("old-after-new-apply-require-all", ("--require-all-graphs",))):
        s = Server("old", root, 18180, extra, label=label)
        st = s.wait()
        rec(step=f"{label}.boot", kind="boot", state=st, log=s.tail(3000))
        if st == "up":
            probe_server(s, label)
        s.stop()
        unlock("old", root, f"{label}.unlock")
    rec(step="old.cluster_apply_e2_after_new", kind="cli", **cli("old", "cluster", "apply", "--target", root, "--config", src, "--as", "act-alice", "--json"))
    unlock("old", root, "old.reapply.unlock")
    s = Server("old", root, 18180, (), label="old-after-reapply-e2")
    st = s.wait()
    rec(step="old-after-reapply-e2.boot", kind="boot", state=st, log=s.tail(3000))
    if st == "up":
        probe_server(s, "old-after-reapply-e2")
    s.stop()


if __name__ == "__main__":
    main()
