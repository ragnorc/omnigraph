#!/usr/bin/env python3
"""Campaign 7: chaos rounds mixing every actor.

Each round starts the server on the same root (and the TLS proxy for the
CLI), runs writers, optimize submitters, lookups and cancels, and then either
  - kills the server at a random moment (SIGKILL 70% / SIGTERM 30%), or
  - applies a deployment through the server that adds a nullable property to
    `kg`'s Company (a schema change that affects the graph and cancels its
    running optimize), waits for it, and stops the server with SIGTERM.
After every restart it checks: acknowledged rows present; no terminal record
rewritten; no `accepted`/`running` record without a live run once settled;
a run reported failed/cancelled/interrupted published nothing; at most one
commit answers a nonce; record bounds; eviction mark monotone; a deployment
that cancelled a run is named in that run's record.
Usage: c7_chaos.py OUTDIR [ROUNDS] [SEED]
"""
import json, os, random, re, shutil, signal, subprocess, sys, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import og, invariants
from c4_deploy import operator_config, start_proxy, deploy

STRESS = Path(__file__).resolve().parent
out = Path(sys.argv[1]); shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 30
random.seed(int(sys.argv[3]) if len(sys.argv) > 3 else 7)
log = og.Log(str(out / "log.txt"))
root = og.make_root(out, "chaos"); og.apply_cluster(root)
env = {"OMNIGRAPH_OPTIMIZE_GATE_HOLD_SECONDS": "2", "OMNIGRAPH_OPTIMIZE_MAX_GATE_HOLD_SECONDS": "5",
       "OMNIGRAPH_SHUTDOWN_GRACE_SECONDS": "10", "OMNIGRAPH_OPERATION_RESOLUTION_WALK_LIMIT": "4",
       "OMNIGRAPH_OPTIMIZE_PLANNING_CONCURRENCY": "2"}
acked, seen, nonces, violations = set(), {}, {}, []
last_mark, extra = None, 0
deploy_cancels = {}
port = 13700


def violate(msg):
    violations.append(msg)
    log("VIOLATION", msg)


def see(op):
    if not op:
        return
    oid = op["operation_id"]
    prev = seen.get(oid)
    if prev and prev.get("state") in og.TERMINAL and prev["state"] != op["state"]:
        violate(f"{oid}: terminal {prev['state']} rewritten to {op['state']}")
    seen[oid] = op
    if op.get("nonce"):
        nonces[oid] = op["nonce"]


def fixture_with(n):
    d = out / f"fixture{n}"
    if d.exists():
        return d
    shutil.copytree(STRESS / "fixture", d)
    extras = "".join(f"    extra{i}: String?\n" for i in range(n))
    text = (d / "kg.pg").read_text().replace("    industry: String?\n", "    industry: String?\n" + extras)
    (d / "kg.pg").write_text(text)
    return d


def start(rnd):
    global port
    port += 1
    if root.startswith("az://"):  # Azure keeps admission after every stop
        log("round", rnd, "az recovery", og.prepare_restart(root))
    srv = og.Server(root, port, str(out / f"server-{rnd}.log"), env)
    try:
        srv.start(timeout=60)
    except (RuntimeError, TimeoutError):
        unlocked = og.unlock(root)
        log("round", rnd, "restart needed force-unlock", unlocked)
        srv = og.Server(root, port, str(out / f"server-{rnd}b.log"), env).start(timeout=60)
    return srv


for rnd in range(rounds):
    srv = start(rnd)
    h = srv.http
    for oid in list(seen):
        s, b, _ = h.lookup("kg", oid)
        if s == 200 and b.get("operation"):
            see(b["operation"])
    rec = og.read_record(root)
    for v in invariants.check_record_shape(rec, live_ids=set()):
        violate(f"round {rnd} after restart: {v}")
    if rec and rec.get("eviction_mark_ms") is not None:
        if last_mark is not None and rec["eviction_mark_ms"] < last_mark:
            violate(f"eviction mark moved back {last_mark} -> {rec['eviction_mark_ms']}")
        last_mark = rec["eviction_mark_ms"]
    missing = acked - og.keys_of(h, "kg", "Person", "name")
    if missing:
        violate(f"round {rnd}: {len(missing)} acknowledged rows missing")
    stop = threading.Event()

    def writer(w):
        i = 0
        while not stop.is_set():
            i += 1
            rows = [f"r{rnd}w{w}i{i}k{k}" for k in range(random.randint(1, 12))]
            try:
                s, b, _ = h.load("kg", "\n".join(json.dumps({"type": "Person", "data": {"name": n, "bio": "x"}}) for n in rows))
            except OSError:
                return
            if s == 200:
                acked.update(rows)

    def submitter():
        while not stop.is_set():
            oid = og.ulid()
            try:
                tables = random.choice([None, None, ["node:Person"], ["node:Company", "edge:Knows"]])
                s, b, _ = h.submit("kg", oid, tables=tables, hold_ms=random.choice([None, 0, 300, 3000]))
                if s in (200, 202):
                    see(b["operation"])
                elif s == 500:
                    seen.setdefault(oid, {"operation_id": oid, "state": "unknown-500"})
                for _ in range(6):
                    if stop.is_set():
                        break
                    s, b, _ = h.lookup("kg", oid)
                    if s == 200 and b.get("operation"):
                        see(b["operation"])
                        if b["operation"]["state"] in og.TERMINAL:
                            break
                    if random.random() < 0.1:
                        h.cancel("kg", oid)
                    time.sleep(0.1)
            except OSError:
                return

    ths = [threading.Thread(target=writer, args=(w,), daemon=True) for w in range(3)] + \
          [threading.Thread(target=submitter, daemon=True) for _ in range(2)]
    for t in ths:
        t.start()
    if random.random() < 0.3:
        extra += 1
        proxy = start_proxy(port, out / f"proxy-{rnd}")
        cfg = out / f"operator-{rnd}.yaml"; operator_config(cfg, port)
        time.sleep(random.uniform(0.2, 2))
        rc, took, stdout = deploy(cfg, fixture_with(extra), log, wait="120")
        m = re.search(r"\b(\w{26}:\d+:\w{26})\b", stdout)
        deploy_id = m.group(1) if m else None
        time.sleep(random.uniform(0.5, 2))
        stop.set()
        for t in ths:
            t.join(10)
        code = srv.kill(signal.SIGTERM, wait=60)
        proxy.kill()
        log("round", rnd, "deployment", deploy_id, "rc", rc, f"{took:.1f}s", "then SIGTERM exit", code)
        if rc != 0:
            violate(f"round {rnd}: deployment rc {rc}")
        if deploy_id:
            deploy_cancels[deploy_id] = rnd
    else:
        time.sleep(random.uniform(0.5, 6))
        sig = signal.SIGKILL if random.random() < 0.7 else signal.SIGTERM
        code = srv.kill(sig, wait=60)
        stop.set()
        for t in ths:
            t.join(10)
        log("round", rnd, "killed", sig.name, "exit", code, "ops", len(seen), "acked", len(acked))

srv = start("final")
h = srv.http
h.load("kg", json.dumps({"type": "Company", "data": {"name": "final-settle"}}))
states = {}
for oid in list(seen):
    op = None
    for _ in range(6):
        s, b, _ = h.lookup("kg", oid)
        if s == 200 and b.get("operation"):
            op = b["operation"]; see(op)
            if op["state"] in og.TERMINAL:
                break
        elif s == 200:
            break
        time.sleep(0.2)
    states[op["state"] if op else "unknown"] = states.get(op["state"] if op else "unknown", 0) + 1
    if op and op["state"] in ("accepted", "running"):
        violate(f"{oid} still {op['state']} after the final restart")
    if op and op["state"] == "cancelled" and (op.get("reason") or "").startswith("deployment ") and op["reason"].split(" ", 1)[1] not in deploy_cancels:
        violate(f"{oid} cancelled by unknown deployment {op['reason']}")
rec = og.read_record(root)
violations += [f"final: {v}" for v in invariants.check_record_shape(rec, live_ids=set())]
violations += [f"final ops: {v}" for v in invariants.check_operations(h, "kg", seen, nonces, rec)]
missing = acked - og.keys_of(h, "kg", "Person", "name")
if missing:
    violate(f"final: {len(missing)} acknowledged rows missing")
srv.kill(signal.SIGTERM)
reasons = {}
for op in seen.values():
    r = (op.get("reason") or "")[:40]
    if op.get("state") in og.TERMINAL:
        reasons[f"{op['state']}:{r}"] = reasons.get(f"{op['state']}:{r}", 0) + 1
summary = {"rounds": rounds, "deployments": len(deploy_cancels), "ops_seen": len(seen), "acked_rows": len(acked),
           "final_states": states, "terminal_reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1])[:12]),
           "violations": violations}
log("SUMMARY", json.dumps(summary))
(out / "summary.json").write_text(json.dumps(summary, indent=1))
