"""Invariant checks shared by the served campaigns.

Every check returns a list of violation strings; an empty list means the
invariant held. They read only the server's public answers and the record file
on a local root.
"""

from __future__ import annotations

import og


def main_history(http: og.Http, graph: str) -> list:
    """Every commit on `graph`'s main, newest first, through the paged listing."""
    commits, token = [], None
    while True:
        q = f"/graphs/{graph}/commits?branch=main&limit=1000" + (f"&page_token={token}" if token else "")
        status, body, _ = http.call("GET", q, token="rd-secret")
        if status != 200:
            raise RuntimeError(f"commit list {status} {body}")
        commits.extend(body["commits"])
        token = body.get("next_page_token")
        if not token:
            return commits


def first_parent_chain(commits: list) -> list:
    """Main's first-parent chain from its head, newest first."""
    by_id = {c["graph_commit_id"]: c for c in commits}
    if not commits:
        return []
    chain, cur = [], commits[0]
    while cur is not None:
        chain.append(cur)
        cur = by_id.get(cur.get("parent_commit_id"))
    return chain


def nonce_of(commit_id: str) -> str:
    # hb1.{block}.{slot}.{nonce}; a bare ULID is its own nonce
    return commit_id.split(".")[-1]


def check_operations(http: og.Http, graph: str, seen_ops: dict, nonces: dict, record) -> list:
    """`seen_ops`: id -> last operation JSON a client saw; `nonces`: id -> nonce seen while publishing."""
    v = []
    commits = main_history(http, graph)
    chain = first_parent_chain(commits)
    chain_ids = {c["graph_commit_id"]: c for c in chain}
    by_nonce = {}
    for c in chain:
        by_nonce.setdefault(nonce_of(c["graph_commit_id"]), []).append(c)
    ops = {op["operation_id"]: op for op in og.record_ops(record) if op["graph"] == graph}
    merged = dict(seen_ops)
    merged.update(ops)
    published_by_ops = {}
    for oid, op in merged.items():
        if op.get("graph", graph) != graph:
            continue
        state = op["state"]
        pc = op.get("published_commit")
        nonce = op.get("nonce") or nonces.get(oid)
        if state == "succeeded" and pc:
            if pc not in chain_ids:
                v.append(f"{oid}: succeeded with published_commit {pc} that is not on main's first-parent chain")
            else:
                c = chain_ids[pc]
                if op.get("input_commit") and c.get("parent_commit_id") != op["input_commit"]:
                    v.append(f"{oid}: published {pc} whose parent {c.get('parent_commit_id')} is not its input {op['input_commit']}")
                if nonce and nonce_of(pc) != nonce:
                    v.append(f"{oid}: published {pc} does not answer nonce {nonce}")
                if c.get("actor_id") != op.get("principal"):
                    v.append(f"{oid}: published {pc} by actor {c.get('actor_id')} not principal {op.get('principal')}")
            published_by_ops.setdefault(pc, []).append(oid)
        if state in ("failed", "cancelled", "interrupted") and pc:
            v.append(f"{oid}: {state} but names published_commit {pc}")
        if state in ("failed", "cancelled", "interrupted") and nonce and nonce in by_nonce:
            v.append(f"{oid}: {state} but main holds commit(s) {[c['graph_commit_id'] for c in by_nonce[nonce]]} answering its nonce {nonce}")
        if nonce and len(by_nonce.get(nonce, [])) > 1:
            v.append(f"{oid}: nonce {nonce} answered by {len(by_nonce[nonce])} commits on main (published more than once)")
    for pc, owners in published_by_ops.items():
        if len(owners) > 1:
            v.append(f"commit {pc} claimed by several operations {owners}")
    # every optimize commit (actor operator, no merged parent) is claimed by a known op
    claimed = set(published_by_ops)
    for c in chain:
        if c.get("actor_id") == "operator" and c["graph_commit_id"] not in claimed:
            n = nonce_of(c["graph_commit_id"])
            owners = [oid for oid, nn in nonces.items() if nn == n]
            state = [merged[o]["state"] for o in owners if o in merged]
            if any(s in ("failed", "cancelled", "interrupted") for s in state):
                v.append(f"main commit {c['graph_commit_id']} by operator is claimed by no succeeded operation (nonce owners {owners}, states {state})")
    return v


def check_record_shape(record, live_ids: set | None = None) -> list:
    v = []
    if record is None:
        return v
    unresolved = record.get("unresolved", [])
    if len(unresolved) > 4:
        v.append(f"{len(unresolved)} unresolved records (> 4)")
    for op in unresolved:
        if op["state"] != "publishing":
            v.append(f"unresolved {op['operation_id']} is {op['state']}")
    for scope, op in record.get("active", {}).items():
        if live_ids is not None and op["operation_id"] not in live_ids and op["state"] in ("accepted", "running"):
            v.append(f"active {op['operation_id']} is {op['state']} with no live run")
    term = record.get("terminal", [])
    if len(term) > 32:
        v.append(f"{len(term)} terminal records (> 32)")
    for op in term:
        if op["state"] not in og.TERMINAL:
            v.append(f"terminal list holds {op['operation_id']} in {op['state']}")
    ids = [op["operation_id"] for op in og.record_ops(record)]
    if len(ids) != len(set(ids)):
        v.append("duplicate operation ids in record")
    return v
