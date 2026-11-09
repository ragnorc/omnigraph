#!/usr/bin/env python3
"""Campaign 2: served recorded optimize under concurrent clients and writers.

Applies a fresh cluster root, starts a real `omnigraph-server` with small
gate holds and ceilings, then for DURATION seconds runs:

- writers loading and mutating `kg` main (Person, Company, Doc with Blob and
  vector, edges), recording what each was told was committed;
- a branch worker that loads on a branch and merges it into main;
- submitters with fresh ids, repeats (same request), mismatches (same id,
  different request), ids at and around the eviction mark, ids dated ahead
  of the clock, gate holds above the ceiling, table lists;
- lookups and cancels of random known ids;
- a sampler of `__cluster/operations.json` (eviction mark monotone, bounds).

Then it checks the invariants (acknowledged writes present, at most one
publication per run, terminal meanings, record shape, evicted ids refused,
writer wait vs gate hold) and writes a JSON summary.

Usage: c2_served_hammer.py OUTDIR [--duration S] [--port P] [--seed N]
       [--env KEY=VALUE ...]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import random
import shutil
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import og  # noqa: E402
import invariants  # noqa: E402

GRAPH = "kg"
TYPES = ["node:Person", "node:Company", "node:Doc", "edge:Knows", "edge:WorksAt", "edge:Authored"]


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.acked = {"Person": set(), "Company": set(), "Doc": set()}
        self.uncertain = {"Person": set(), "Company": set(), "Doc": set()}
        self.write_lat = []  # (start, end, kind, status)
        self.ops = {}  # id -> last operation JSON seen
        self.requests = {}  # id -> request body submitted first
        self.nonces = {}
        self.events = []  # (t, kind, id, status, detail)
        self.violations = []
        self.marks = []  # (t, eviction_mark_ms)
        self.evicted_seen = set()
        self.stop = False
        self.counter = 0

    def next(self):
        with self.lock:
            self.counter += 1
            return self.counter

    def violate(self, msg):
        with self.lock:
            self.violations.append(msg)
        LOG("VIOLATION", msg)

    def see(self, op):
        if not op:
            return
        with self.lock:
            oid = op["operation_id"]
            prev = self.ops.get(oid)
            if prev and prev["state"] in og.TERMINAL and op["state"] != prev["state"]:
                self.violations.append(f"{oid}: terminal {prev['state']} rewritten to {op['state']}")
            if prev and prev["state"] in og.TERMINAL and op["state"] == prev["state"] and op != prev:
                diff = {k for k in set(op) | set(prev) if op.get(k) != prev.get(k)}
                self.violations.append(f"{oid}: terminal record changed fields {sorted(diff)}")
            self.ops[oid] = op
            if op.get("nonce"):
                self.nonces[oid] = op["nonce"]


LOG = None


def person(i, tag):
    return {"type": "Person", "data": {"name": f"{tag}-p{i}", "age": i % 97, "bio": f"person {i} from {tag} writes graph notes"}}


def doc(i, tag, blob_bytes):
    payload = base64.b64encode(os.urandom(blob_bytes)).decode()
    emb = [round(random.random(), 4) for _ in range(8)]
    return {"type": "Doc", "data": {"slug": f"{tag}-d{i}", "title": f"document {i} about compaction", "content": f"base64:{payload}", "embedding": emb}}


def writer(st: State, http: og.Http, wid: int, blob_bytes: int):
    tag = f"w{wid}"
    i = 0
    while not st.stop:
        i += 1
        kind = random.choice(["persons", "persons", "docs", "edges", "mutate", "company"])
        t0 = time.time()
        try:
            if kind == "persons":
                rows = [person(i * 100 + k, tag) for k in range(random.randint(1, 20))]
                status, body, _ = http.load(GRAPH, "\n".join(json.dumps(r) for r in rows))
                keys = [r["data"]["name"] for r in rows]
                typ = "Person"
            elif kind == "company":
                rows = [{"type": "Company", "data": {"name": f"{tag}-c{i}", "industry": "infra"}}]
                status, body, _ = http.load(GRAPH, json.dumps(rows[0]))
                keys, typ = [f"{tag}-c{i}"], "Company"
            elif kind == "docs":
                rows = [doc(i * 10 + k, tag, blob_bytes) for k in range(random.randint(1, 3))]
                status, body, _ = http.load(GRAPH, "\n".join(json.dumps(r) for r in rows))
                keys, typ = [r["data"]["slug"] for r in rows], "Doc"
            elif kind == "edges":
                with st.lock:
                    people = sorted(st.acked["Person"])
                if len(people) < 2:
                    continue
                a, b = random.sample(people, 2)
                rows = [{"edge": "Knows", "from": a, "to": b, "data": {"since": 2000 + i % 25}}]
                status, body, _ = http.load(GRAPH, json.dumps(rows[0]))
                keys, typ = [], None
            else:
                with st.lock:
                    people = sorted(st.acked["Person"])
                if not people:
                    continue
                name = random.choice(people)
                status, body, _ = http.mutate(GRAPH, "query bump($n: String, $a: I32) { update Person set { age: $a } where name = $n }",
                                              params={"n": name, "a": random.randint(1, 99)})
                keys, typ = [], None
        except Exception as e:  # noqa: BLE001
            status, body, keys, typ = -1, str(e), [], None
        t1 = time.time()
        with st.lock:
            st.write_lat.append((t0, t1, kind, status))
            if typ:
                if status == 200:
                    st.acked[typ].update(keys)
                elif status in (-1, 500, 502, 503, 504) and not (isinstance(body, dict) and body.get("code") in ("service_unavailable",)):
                    st.uncertain[typ].update(keys)
        if status not in (200,):
            st.events.append((t0, "write", kind, status, json.dumps(body)[:300] if body else None))
            if status not in (409, 429, 503):
                LOG("writer", wid, kind, status, json.dumps(body)[:300] if body else None)


def brancher(st: State, http: og.Http):
    n = 0
    while not st.stop:
        n += 1
        name = f"feat{n}"
        status, body, _ = http.branch_create(GRAPH, name)
        if status != 200:
            st.events.append((time.time(), "branch_create", name, status, str(body)[:200]))
            time.sleep(0.5)
            continue
        rows = [person(k, f"b{n}") for k in range(random.randint(1, 10))]
        status, body, _ = http.load(GRAPH, "\n".join(json.dumps(r) for r in rows), branch=name)
        if status != 200:
            st.events.append((time.time(), "branch_load", name, status, str(body)[:200]))
            continue
        t0 = time.time()
        status, body, _ = http.merge(GRAPH, name, delete_branch=True)
        t1 = time.time()
        with st.lock:
            st.write_lat.append((t0, t1, "merge", status))
            if status == 200:
                st.acked["Person"].update(r["data"]["name"] for r in rows)
            elif status in (-1, 500):
                st.uncertain["Person"].update(r["data"]["name"] for r in rows)
        if status != 200:
            st.events.append((t0, "merge", name, status, json.dumps(body)[:300]))
            LOG("merge", name, status, json.dumps(body)[:300])
        time.sleep(random.uniform(0.2, 1.0))


def classify_submit(st, oid, body_sent, status, body, expect=None):
    st.events.append((time.time(), "submit", oid, status, (body or {}).get("error") if isinstance(body, dict) else None))
    if status in (200, 202) and isinstance(body, dict) and body.get("operation"):
        st.see(body["operation"])
        op = body["operation"]
        if op["request"] != {"kind": "optimize", **{k: v for k, v in body_sent.items() if k != "operation_id"}}:
            st.violate(f"{oid}: answered record request {op['request']} differs from submitted {body_sent}")
    if expect and status not in expect:
        st.violate(f"{oid}: submit expected {expect} got {status} {json.dumps(body)[:400]}")


def expired_check(st, http, oid, resp):
    """An id refused as expired must not be held by the record: a retained id answers first."""
    status, look, _ = http.lookup(GRAPH, oid)
    st.events.append((time.time(), "submit", oid, 409, "expired"))
    if status == 200 and look.get("lookup") == "unknown":
        with st.lock:
            st.evicted_seen.add(oid)
    elif status == 200 and look.get("operation"):
        st.violate(f"{oid}: refused operation_id_expired while the record still holds it ({look['operation']['state']})")
    else:
        st.violate(f"{oid}: lookup after expiry refusal {status} {look}")


def submitter(st: State, http: og.Http, sid: int, max_hold_ms: int):
    while not st.stop:
        roll = random.random()
        with st.lock:
            known = list(st.requests.items())
            mark = st.marks[-1][1] if st.marks else None
        if roll < 0.40 or not known:
            oid = og.ulid()
            body = {"operation_id": oid}
            r = random.random()
            if r < 0.3:
                body["tables"] = random.sample(TYPES, random.randint(1, 3))
            if random.random() < 0.6:
                body["max_gate_hold_ms"] = random.choice([0, 1, 50, 500, 1500, max_hold_ms])
            status, resp, _ = http.submit(GRAPH, oid, tables=body.get("tables"), hold_ms=body.get("max_gate_hold_ms"))
            if status in (200, 202):
                with st.lock:
                    st.requests.setdefault(oid, body)
            classify_submit(st, oid, body, status, resp, expect={200, 202, 429, 409, 503})
        elif roll < 0.55:
            oid, body = random.choice(known)
            status, resp, _ = http.submit(GRAPH, oid, tables=body.get("tables"), hold_ms=body.get("max_gate_hold_ms"))
            with st.lock:
                prev = st.ops.get(oid)
            # a retained id answers its record; an evicted one is unknown -> refused expired
            exp = {200, 202} if oid not in st.evicted_seen else {409}
            if status == 409 and "operation_id_expired" in json.dumps(resp):
                expired_check(st, http, oid, resp)
            else:
                classify_submit(st, oid, body, status, resp, expect=exp | {503})
                if status == 409 and oid not in st.evicted_seen:
                    st.violate(f"{oid}: repeat of the same request refused 409 {resp}")
        elif roll < 0.62:
            oid, body = random.choice(known)
            other = dict(body)
            other["max_gate_hold_ms"] = (body.get("max_gate_hold_ms") or 0) + 7
            status, resp, _ = http.submit(GRAPH, oid, tables=other.get("tables"), hold_ms=other["max_gate_hold_ms"])
            if status == 409 and "operation_id_expired" in json.dumps(resp):
                expired_check(st, http, oid, resp)
            else:
                classify_submit(st, oid, other, status, resp, expect={409, 503} | ({400} if oid in st.evicted_seen else set()))
            if status == 409 and oid not in st.evicted_seen and "operation_request_mismatch" not in json.dumps(resp):
                st.violate(f"{oid}: mismatch refused without operation_request_mismatch: {resp}")
        elif roll < 0.68 and mark is not None:
            # ids at, just before and just after the eviction mark
            which = random.choice([-1, 0, 1, 5])
            oid = og.ulid(mark + which)
            status, resp, _ = http.submit(GRAPH, oid)
            exp = {409} if which <= 0 else {200, 202, 429, 409, 503}
            classify_submit(st, oid, {"operation_id": oid}, status, resp, expect=exp)
            if which <= 0 and status == 409 and "operation_id_expired" not in json.dumps(resp):
                st.violate(f"{oid} at mark{which:+d}: 409 without operation_id_expired: {resp}")
            if status in (200, 202):
                with st.lock:
                    st.requests.setdefault(oid, {"operation_id": oid})
        elif roll < 0.72:
            # dated ahead of the server clock by more than the allowed lead
            oid = og.ulid(int(time.time() * 1000) + 120_000)
            status, resp, _ = http.submit(GRAPH, oid)
            classify_submit(st, oid, {"operation_id": oid}, status, resp, expect={400})
        elif roll < 0.75:
            oid = og.ulid()
            status, resp, _ = http.submit(GRAPH, oid, hold_ms=max_hold_ms + 1)
            classify_submit(st, oid, {"operation_id": oid}, status, resp, expect={400, 503})
        elif roll < 0.90:
            oid, _ = random.choice(known)
            status, resp, _ = http.lookup(GRAPH, oid)
            st.events.append((time.time(), "lookup", oid, status, None))
            if status == 200 and resp.get("operation"):
                st.see(resp["operation"])
            elif status == 200 and resp.get("lookup") == "unknown":
                with st.lock:
                    st.evicted_seen.add(oid)
            else:
                st.violate(f"lookup {oid}: {status} {json.dumps(resp)[:300]}")
        else:
            oid, _ = random.choice(known)
            status, resp, _ = http.cancel(GRAPH, oid)
            st.events.append((time.time(), "cancel", oid, status, None))
            if status in (200, 202) and resp.get("operation"):
                st.see(resp["operation"])
            elif not (status == 200 and resp.get("lookup") == "unknown"):
                st.violate(f"cancel {oid}: {status} {json.dumps(resp)[:300]}")
        time.sleep(random.uniform(0.0, 0.15))


def sampler(st: State, root: str):
    last_mark = None
    while not st.stop:
        try:
            rec = og.read_record(root)
        except Exception as e:  # noqa: BLE001  (a torn read of a file being replaced)
            st.events.append((time.time(), "record_read", None, -1, str(e)[:200]))
            time.sleep(0.05)
            continue
        if rec:
            mark = rec.get("eviction_mark_ms")
            if last_mark is not None and (mark is None or mark < last_mark):
                st.violate(f"eviction mark moved back {last_mark} -> {mark}")
            if mark is not None:
                with st.lock:
                    if not st.marks or st.marks[-1][1] != mark:
                        st.marks.append((time.time(), mark))
                last_mark = mark
            for v in invariants.check_record_shape(rec):
                st.violate("record: " + v)
            for op in og.record_ops(rec):
                if op.get("nonce"):
                    with st.lock:
                        st.nonces[op["operation_id"]] = op["nonce"]
        time.sleep(0.05)


def main():
    global LOG
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--duration", type=float, default=120)
    ap.add_argument("--port", type=int, default=12100)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--writers", type=int, default=4)
    ap.add_argument("--submitters", type=int, default=6)
    ap.add_argument("--blob-bytes", type=int, default=4096)
    ap.add_argument("--env", action="append", default=[])
    ap.add_argument("--root", default=None)
    a = ap.parse_args()
    random.seed(a.seed)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    LOG = og.Log(str(out / "log.txt"))
    if a.root:
        root = a.root
    else:
        shutil.rmtree(out / "root", ignore_errors=True)
        root = og.make_root(out, "c2")
    og.apply_cluster(root)
    env = {
        "OMNIGRAPH_OPTIMIZE_GATE_HOLD_SECONDS": "1",
        "OMNIGRAPH_OPTIMIZE_MAX_GATE_HOLD_SECONDS": "3",
        "OMNIGRAPH_OPTIMIZE_MAX_SOURCE_FRAGMENTS": "40",
        "OMNIGRAPH_OPTIMIZE_RUN_SOURCE_BYTES": str(2 * 1024 * 1024),
        "OMNIGRAPH_OPTIMIZE_MAX_SOURCE_BYTES": str(1024 * 1024),
        "OMNIGRAPH_OPERATION_RESOLUTION_WALK_LIMIT": "2",
    }
    for kv in a.env:
        k, v = kv.split("=", 1)
        env[k] = v
    max_hold_ms = int(env["OMNIGRAPH_OPTIMIZE_MAX_GATE_HOLD_SECONDS"]) * 1000
    srv = og.Server(root, a.port, str(out / "server.log"), env).start()
    http = srv.http
    LOG("server up", root, env)
    st = State()
    threads = [threading.Thread(target=writer, args=(st, http, w, a.blob_bytes), daemon=True) for w in range(a.writers)]
    threads.append(threading.Thread(target=brancher, args=(st, http), daemon=True))
    threads += [threading.Thread(target=submitter, args=(st, http, s, max_hold_ms), daemon=True) for s in range(a.submitters)]
    threads.append(threading.Thread(target=sampler, args=(st, root), daemon=True))
    for t in threads:
        t.start()
    t_end = time.time() + a.duration
    while time.time() < t_end:
        time.sleep(5)
        with st.lock:
            n_ops = len(st.ops)
            states = {}
            for op in st.ops.values():
                states[op["state"]] = states.get(op["state"], 0) + 1
            nw = len(st.write_lat)
        LOG("progress ops", n_ops, states, "writes", nw, "violations", len(st.violations), "marks", len(st.marks))
        if srv.proc.poll() is not None:
            st.violate(f"server exited {srv.proc.returncode}")
            break
    st.stop = True
    for t in threads:
        t.join(timeout=60)
    # quiesce: wait for every known op to be terminal or publishing-unresolved
    LOG("quiescing")
    for oid in list(st.requests):
        try:
            status, resp, _ = http.lookup(GRAPH, oid)
            if status == 200 and resp.get("operation"):
                op = resp["operation"]
                end = time.time() + 120
                while op["state"] in ("accepted", "running") and time.time() < end:
                    time.sleep(0.3)
                    status, resp, _ = http.lookup(GRAPH, oid)
                    op = resp.get("operation") or op
                st.see(op)
                if op["state"] in ("accepted", "running"):
                    st.violate(f"{oid}: still {op['state']} after quiesce, in_progress={resp.get('in_progress')}")
            elif status == 200:
                st.evicted_seen.add(oid)
        except Exception as e:  # noqa: BLE001
            st.violate(f"quiesce lookup {oid}: {e}")
    # a final write so that any publishing record can settle, then look again
    http.load(GRAPH, json.dumps(person(999999, "final")))
    for oid in list(st.requests):
        status, resp, _ = http.lookup(GRAPH, oid)
        if status == 200 and resp.get("operation"):
            st.see(resp["operation"])
    record = og.read_record(root)
    live = set()
    for v in invariants.check_record_shape(record, live_ids=live):
        st.violate("final record: " + v)
    for v in invariants.check_operations(http, GRAPH, st.ops, st.nonces, record):
        st.violate("ops: " + v)
    # acknowledged writes present
    for typ, key in (("Person", "name"), ("Company", "name"), ("Doc", "slug")):
        present = og.keys_of(http, GRAPH, typ, key)
        missing = st.acked[typ] - present
        if missing:
            st.violate(f"{len(missing)} acknowledged {typ} rows missing, e.g. {sorted(missing)[:5]}")
        LOG(f"{typ}: acked {len(st.acked[typ])} present {len(present)} uncertain {len(st.uncertain[typ])}")
    # evicted ids: a resubmission must never run again
    for oid in sorted(st.evicted_seen)[:5]:
        body = st.requests.get(oid, {"operation_id": oid})
        status, resp, _ = http.submit(GRAPH, oid, tables=body.get("tables"), hold_ms=body.get("max_gate_hold_ms"))
        if status in (200, 202):
            st.violate(f"evicted {oid} was admitted again: {resp}")
    # writer wait vs gate hold: the longest write latency against the longest gate hold
    holds = [op["timings"].get("gate_hold_ms", 0) for op in st.ops.values() if op.get("timings")]
    lat = sorted(t1 - t0 for t0, t1, k, s in st.write_lat if s == 200)
    summary = {
        "ops": len(st.ops),
        "states": {s: sum(1 for op in st.ops.values() if op["state"] == s) for s in og.TERMINAL | {"accepted", "running", "publishing"}},
        "writes": len(st.write_lat),
        "write_p50": lat[len(lat) // 2] if lat else None,
        "write_p99": lat[int(len(lat) * 0.99)] if lat else None,
        "write_max": lat[-1] if lat else None,
        "max_gate_hold_ms_seen": max(holds) if holds else None,
        "marks": st.marks[-3:],
        "evicted_seen": len(st.evicted_seen),
        "violations": st.violations,
        "event_counts": {},
    }
    for _, kind, _, status, _ in st.events:
        k = f"{kind}:{status}"
        summary["event_counts"][k] = summary["event_counts"].get(k, 0) + 1
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    (out / "ops.json").write_text(json.dumps(st.ops, indent=1))
    (out / "events.json").write_text(json.dumps(st.events, indent=0, default=str))
    (out / "record.json").write_text(json.dumps(record, indent=1))
    LOG("summary", json.dumps({k: v for k, v in summary.items() if k != "violations"}, default=str))
    LOG("violations", len(st.violations))
    for v in st.violations[:50]:
        LOG("  ", v)
    srv.kill(sig=15)
    return 1 if st.violations else 0


if __name__ == "__main__":
    sys.exit(main())
