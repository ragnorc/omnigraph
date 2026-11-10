#!/usr/bin/env python3
"""Round 2, campaign 2/4: events while a served optimize waits in its writer yield.

Setup per scenario (fresh root): seed, then submit run A while a large load
(LOAD_ROWS Person rows, slow to prepare) is preparing, so A publishes over it
and the load prepares again; run B, submitted right after A ends, waits for
that load in its yield. While B is `running` and the load has not returned:

  cancel      POST .../cancel: B must end promptly, `cancelled`, publish nothing
  sigterm     SIGTERM the server: prompt exit 0, B terminal (not `running`) after restart
  sigkill     SIGKILL: after restart B is `interrupted`, nothing published by it
  deploy      a schema-changing deployment through the server: B cancelled naming
              the deployment, the deployment completes
  none        control: B waits for the load and publishes after it

Every scenario then checks: B's nonce answered by no commit unless B
succeeded; acknowledged rows present; record shape.
Usage: c12_yield_events.py OUTDIR [scenario ...]   (env LOAD_ROWS, default 40000)
"""
import json, os, shutil, signal, sys, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import og, invariants
from c4_deploy import operator_config, start_proxy, deploy

STRESS = Path(__file__).resolve().parent
GRAPH = "kg"
LOAD_ROWS = int(os.environ.get("LOAD_ROWS", "40000"))


def scenario(out, name, port):
    d = out / name; shutil.rmtree(d, ignore_errors=True); d.mkdir(parents=True)
    log = og.Log(str(d / "log.txt"))
    root = og.make_root(d, name); og.apply_cluster(root)
    env = {"OMNIGRAPH_SHUTDOWN_GRACE_SECONDS": "25", "OMNIGRAPH_OPTIMIZE_GATE_HOLD_SECONDS": "5"}
    srv = og.Server(root, port, str(d / "server.log"), env).start(timeout=120)
    h = srv.http
    res = {"scenario": name, "root": root}
    for i in range(4):
        s, b, _ = h.load(GRAPH, "\n".join(json.dumps({"type": "Person", "data": {"name": f"seed{i}_{k}", "bio": "b"}}) for k in range(20)))
        assert s == 200, b
    acked = {f"seed{i}_{k}" for i in range(4) for k in range(20)}
    stop = threading.Event()
    load_result = {"writes": 0, "other": {}}
    lock = threading.Lock()

    def writer(w):
        i = 0
        while not stop.is_set():
            i += 1
            names = [f"w{w}i{i}k{k}" for k in range(5)]
            try:
                s, b, _ = h.load(GRAPH, "\n".join(json.dumps({"type": "Person", "data": {"name": n, "bio": "x"}}) for n in names))
            except OSError:
                s, b = None, None
            with lock:
                if s == 200:
                    acked.update(names); load_result["writes"] += 1
                else:
                    load_result["other"][str(s)] = load_result["other"].get(str(s), 0) + 1

    ws = [threading.Thread(target=writer, args=(w,), daemon=True) for w in range(2)]
    for t in ws:
        t.start()
    time.sleep(2)
    a = og.ulid()
    s, b, _ = h.submit(GRAPH, a)
    opA = h.wait_terminal(GRAPH, a, 120)
    res["A"] = (opA["state"], bool(opA.get("published_commit")), (opA.get("timings") or {}))
    bid = og.ulid()
    tb = time.time()
    s, b, _ = h.submit(GRAPH, bid)
    res["B_submit"] = s
    time.sleep(1.5)
    st, body, _ = h.lookup(GRAPH, bid)
    res["B_state_at_event"] = ((body or {}).get("operation") or {}).get("state")
    t_event = time.time()
    restart = False
    if name == "cancel":
        cs, cb, _ = h.cancel(GRAPH, bid)
        res["cancel_status"] = cs
        op = h.wait_terminal(GRAPH, bid, 60, poll=0.02)
        res["B_terminal_after_s"] = round(time.time() - t_event, 3)
    elif name in ("sigterm", "sigkill"):
        sig = signal.SIGTERM if name == "sigterm" else signal.SIGKILL
        rc = srv.kill(sig, wait=60)
        res["exit"] = rc
        res["stop_s"] = round(time.time() - t_event, 3)
        restart = True
    elif name == "deploy":
        pctl = d / "proxy"; proxy = start_proxy(port, pctl)
        cfg = d / "operator.yaml"; operator_config(cfg, port)
        rc, took, _ = deploy(cfg, STRESS / "fixture_v2", log)
        proxy.kill()
        res["deploy_rc"], res["deploy_s"] = rc, round(took, 2)
        op = h.wait_terminal(GRAPH, bid, 120)
        res["B_terminal_after_s"] = round(time.time() - t_event, 3)
    else:
        op = h.wait_terminal(GRAPH, bid, 120)
        res["B_terminal_after_s"] = round(time.time() - t_event, 3)
    stop.set()
    for t in ws:
        t.join(120)
    res["writers"] = load_result
    if restart:
        og.unlock(root) if name == "sigkill" else None
        try:
            srv = og.Server(root, port, str(d / "server2.log"), env).start(timeout=120)
        except RuntimeError:
            og.unlock(root)
            srv = og.Server(root, port, str(d / "server3.log"), env).start(timeout=120)
        h = srv.http
        st, body, _ = h.lookup(GRAPH, bid)
        op = (body or {}).get("operation")
        if op and op["state"] not in og.TERMINAL:
            op = h.wait_terminal(GRAPH, bid, 60)
    res["B"] = op and (op["state"], op.get("reason"), op.get("published_commit"), (op.get("timings") or {}))
    # invariants
    v = []
    commits = invariants.main_history(h, GRAPH)
    nonce = op and op.get("nonce")
    answering = [c["graph_commit_id"] for c in commits if nonce and invariants.nonce_of(c["graph_commit_id"]) == nonce]
    if op and op["state"] != "succeeded" and answering:
        v.append(f"B {op['state']} but {answering} answer its nonce")
    if op and op["state"] in ("accepted", "running"):
        v.append(f"B left {op['state']}")
    have = og.keys_of(h, GRAPH, "Person", "name")
    missing = acked - have
    if missing:
        v.append(f"{len(missing)} acknowledged rows missing")
    v += invariants.check_record_shape(og.read_record(root))
    res["violations"] = v
    srv.kill(signal.SIGTERM)
    log("RESULT", json.dumps(res, default=str))
    print(json.dumps(res, default=str)[:1500], flush=True)
    return res


def main():
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    names = sys.argv[2:] or ["none", "cancel", "sigterm", "sigkill", "deploy"]
    port = int(os.environ.get("STRESS_PORT", "14601"))
    results = []
    for i, n in enumerate(names):
        try:
            results.append(scenario(out, n, port + i))
        except Exception as e:  # noqa: BLE001
            results.append({"scenario": n, "error": repr(e)[:400]})
            print(json.dumps(results[-1]), flush=True)
    (out / "results.json").write_text(json.dumps(results, indent=1, default=str))


if __name__ == "__main__":
    main()
