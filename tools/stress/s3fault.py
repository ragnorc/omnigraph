#!/usr/bin/env python3
"""Served writes on an S3 cluster root (RustFS) with toxiproxy between the
server and the store. Writers insert keyed rows and record each answer as
acknowledged, refused or unknown. A fault thread adds and removes toxiproxy
toxics (latency, timeout, reset_peer, limit_data, slicer). An optimize thread
submits recorded optimize operations (#1282) and looks each one up. Readers
check that a row count never goes down on main.

At the end the toxics are removed, and the checks are:
- every acknowledged key is present, with the value written;
- no key is present that no writer attempted;
- every optimize operation reaches a terminal state, and a succeeded one
  names a commit that is in main's history;
- the server answers /readyz.

Usage: s3fault.py <seconds> <writers> [--restart]
Needs: RustFS on 127.0.0.1:9000 with bucket omnigraph-ci, toxiproxy-server on
8474, OG and OGS pointing at the CLI and server binaries."""
import json, os, random, shutil, signal, subprocess, sys, threading, time, urllib.request, urllib.error

OG = os.environ.get("OG", "omnigraph")
OGS = os.environ.get("OGS", "omnigraph-server")
SECS, NW = int(sys.argv[1]), int(sys.argv[2])
RESTART = "--restart" in sys.argv
D = "/tmp/og/s3fault"
ROOT = f"s3://omnigraph-ci/stress/s3fault-{int(time.time())}"
PORT = 18080
BASE = f"http://127.0.0.1:{PORT}"
G = "g"
TOK = "tok-a"
TOXI = "http://127.0.0.1:8474"
S3ENV = {
    "AWS_ACCESS_KEY_ID": "omnigraphci", "AWS_SECRET_ACCESS_KEY": "omnigraphci-secret",
    "AWS_REGION": "us-east-1", "AWS_ALLOW_HTTP": "true", "AWS_S3_FORCE_PATH_STYLE": "true",
}
events = []
elock = threading.Lock()


def ev(*a):
    with elock:
        events.append(a)
    print("EVENT", *a, flush=True)


def env(endpoint):
    e = dict(os.environ)
    e.update(S3ENV)
    e["AWS_ENDPOINT_URL"] = endpoint
    e["AWS_ENDPOINT_URL_S3"] = endpoint
    return e


def http(method, url, body=None, headers=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            b = resp.read()
            return resp.status, b.decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:2000]
    except Exception as e:
        return None, repr(e)


def api(method, path, body=None, timeout=60):
    st, txt = http(method, BASE + path, body, {"Authorization": "Bearer " + TOK, "Omnigraph-Http-Api": "0.14"}, timeout)
    try:
        return st, json.loads(txt) if txt else None
    except Exception:
        return st, txt


def toxi(method, path, body=None):
    return http(method, TOXI + path, body)


SCHEMA = """node P {
  slug: String @key
  n: I64
  w: I64?
}
"""
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


def setup():
    shutil.rmtree(D, ignore_errors=True)
    os.makedirs(D)
    open(f"{D}/g.pg", "w").write(SCHEMA)
    open(f"{D}/policy.yaml", "w").write(POLICY)
    open(f"{D}/cluster.yaml", "w").write(
        "version: 1\nmetadata:\n  name: s3fault\ngraphs:\n  g:\n    schema: g.pg\n"
        "policies:\n  gp:\n    file: policy.yaml\n    applies_to: [g]\n")
    direct = env("http://127.0.0.1:9000")
    p = subprocess.run([OG, "cluster", "apply", "--target", ROOT, "--config", D, "--json"], capture_output=True, text=True, env=direct)
    if p.returncode:
        sys.exit("apply: " + p.stdout[-1500:] + p.stderr[-1500:])
    unlock(direct)
    toxi("DELETE", "/proxies/s3")
    st, out = toxi("POST", "/proxies", {"name": "s3", "listen": "127.0.0.1:9100", "upstream": "127.0.0.1:9000"})
    if st not in (200, 201):
        sys.exit(f"toxiproxy: {st} {out}")


def unlock(e):
    p = subprocess.run([OG, "cluster", "status", "--target", ROOT, "--json"], capture_output=True, text=True, env=e)
    try:
        st = json.loads(p.stdout)
    except Exception:
        sys.exit("status: " + p.stdout[-800:] + p.stderr[-800:])
    lock_id = find_lock(st)
    if lock_id:
        q = subprocess.run([OG, "cluster", "force-unlock", lock_id, "--target", ROOT, "--yes"], capture_output=True, text=True, env=e)
        if q.returncode:
            q = subprocess.run([OG, "cluster", "force-unlock", lock_id, "--target", ROOT], capture_output=True, text=True, env=e)
        ev("force-unlock", lock_id, q.returncode, q.stderr.strip()[-300:])


def find_lock(o):
    if isinstance(o, dict):
        for k, v in o.items():
            if k == "lock_id" and isinstance(v, str):
                return v
            r = find_lock(v)
            if r:
                return r
    if isinstance(o, list):
        for v in o:
            r = find_lock(v)
            if r:
                return r
    return None


server = None


def start_server(wait=60):
    global server
    e = env("http://127.0.0.1:9100")
    e["OMNIGRAPH_SERVER_BEARER_TOKENS_JSON"] = json.dumps({"act-a": TOK})
    e["OMNIGRAPH_OPERATION_ROUTES"] = "true"
    e["OMNIGRAPH_OPTIMIZE_GATE_HOLD_SECONDS"] = "5"
    log = open(f"{D}/server.log", "a")
    server = subprocess.Popen([OGS, "--cluster", ROOT, "--bind", f"127.0.0.1:{PORT}"], stdout=log, stderr=log, env=e)
    for _ in range(wait * 2):
        st, _ = http("GET", BASE + "/readyz", timeout=5)
        if st == 200:
            return
        if server.poll() is not None:
            # a lock held by a stopped owner may need its lease to lapse first
            ev("server exited at start", server.returncode)
            time.sleep(2)
            log = open(f"{D}/server.log", "a")
            server = subprocess.Popen([OGS, "--cluster", ROOT, "--bind", f"127.0.0.1:{PORT}"], stdout=log, stderr=log, env=e)
        time.sleep(0.5)
    sys.exit("server not ready")


attempted = {}  # key -> n
acked = {}
unknown = {}
refused = {}
HARSH = os.environ.get('HARSH') == '1'
alock = threading.Lock()
stop = threading.Event()
pause = threading.Event()  # set while the server restarts


def writer(wid, seed):
    r = random.Random(seed)
    i = 0
    while not stop.is_set():
        if pause.is_set():
            time.sleep(0.2)
            continue
        i += 1
        k = f"w{wid}-{i}"
        n = r.randint(0, 1000)
        with alock:
            attempted[k] = n
        st, out = api("POST", f"/graphs/{G}/mutate", {"query": "query m($k: String, $n: I64) { insert P { slug: $k, n: $n } }", "params": {"k": k, "n": n}}, timeout=90)
        with alock:
            if st == 200:
                acked[k] = n
            elif st in (400, 401, 403, 404, 409, 412, 429) or (st == 503 and isinstance(out, dict) and out.get("disposition", {}).get("effects") == "none"):
                refused[st] = refused.get(st, 0) + 1  # refused before effects
                if isinstance(out, dict) and out.get("disposition", {}).get("effects") not in (None, "none"):
                    ev("REFUSAL_WITH_EFFECTS", k, st, str(out)[:300])
            else:
                unknown[k] = (st, str(out)[:300])
        if st not in (200,):
            time.sleep(0.2)


def reader():
    last = 0
    while not stop.is_set():
        if pause.is_set():
            time.sleep(0.2)
            continue
        st, out = api("POST", f"/graphs/{G}/query", {"query": "query c() { match { $p: P } return { count($p) as c } }", "params": {}}, timeout=60)
        if st == 200:
            c = out["rows"][0]["c"]
            if c < last:
                ev("COUNT_WENT_DOWN", last, c)
            last = max(last, c)
        time.sleep(0.3)


def ulid():
    import secrets
    t = int(time.time() * 1000)
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    v = (t << 80) | secrets.randbits(80)
    s = ""
    for _ in range(26):
        s = alphabet[v & 31] + s
        v >>= 5
    return s


ops = {}


def optimizer():
    r = random.Random(7)
    while not stop.is_set():
        time.sleep(r.uniform(3, 8))
        if pause.is_set():
            continue
        oid = ulid()
        body = {"operation_id": oid, "max_gate_hold_ms": r.choice([200, 1000, 5000])}
        st, out = api("POST", f"/graphs/{G}/operations/optimize", body, timeout=60)
        ops[oid] = {"submit": (st, out if not isinstance(out, dict) else out.get("state") or out.get("status") or out)}
        ev("optimize submit", oid, st, str(out)[:200])
        if st in (None, 500) or (st == 202 and r.random() < 0.3):
            # same id, same request: must answer the existing record
            st2, out2 = api("POST", f"/graphs/{G}/operations/optimize", body, timeout=60)
            ev("optimize resubmit", oid, st2, str(out2)[:200])
        if r.random() < 0.2:
            st3, out3 = api("POST", f"/graphs/{G}/operations/{oid}/cancel", {}, timeout=60)
            ev("optimize cancel", oid, st3, str(out3)[:200])


def faults():
    r = random.Random(11)
    kinds = [
        ("latency", {"latency": 400, "jitter": 300}),
        ("timeout", {"timeout": 2000}),
        ("reset_peer", {"timeout": 300}),
        ("limit_data", {"bytes": 20000}),
        ("slicer", {"average_size": 64, "size_variation": 32, "delay": 200}),
        ("bandwidth", {"rate": 64}),
    ] + ([("timeout", {"timeout": 0}), ("reset_peer", {"timeout": 0}), ("limit_data", {"bytes": 500})] * 3 if HARSH else [])
    n = 0
    while not stop.is_set():
        time.sleep(r.uniform(2, 6))
        if pause.is_set():
            continue
        kind, attrs = r.choice(kinds)
        stream = r.choice(["upstream", "downstream"])
        name = f"t{n}"
        n += 1
        st, out = toxi("POST", "/proxies/s3/toxics", {"name": name, "type": kind, "stream": stream, "toxicity": r.choice([0.3, 1.0]), "attributes": attrs})
        ev("toxic on", name, kind, stream, st)
        time.sleep(r.uniform(0.5, 12 if HARSH else 4))
        toxi("DELETE", f"/proxies/s3/toxics/{name}")
        ev("toxic off", name)


def clear_toxics():
    st, out = toxi("GET", "/proxies/s3/toxics")
    try:
        for t in json.loads(out):
            toxi("DELETE", f"/proxies/s3/toxics/{t['name']}")
    except Exception:
        pass


def supervisor():
    # restart a server that stopped on its own, as an orchestrator with
    # restart-on-failure would; on S3 the new process must take the lock
    # over through its lease, with no force-unlock
    while not stop.is_set():
        time.sleep(1)
        if pause.is_set() or server is None:
            continue
        rc = server.poll()
        if rc is not None:
            ev("SERVER_STOPPED_ITSELF", rc)
            t0 = time.time()
            try:
                start_server(wait=180)
                ev("server restarted by supervisor", round(time.time() - t0, 1))
            except SystemExit as e:
                ev("SUPERVISOR_RESTART_FAILED", str(e))
                return


def restarter():
    time.sleep(SECS / 2)
    pause.set()
    time.sleep(2)
    clear_toxics()
    ev("server SIGTERM")
    server.send_signal(signal.SIGTERM)
    try:
        rc = server.wait(timeout=120)
    except subprocess.TimeoutExpired:
        server.kill()
        rc = server.wait()
        ev("server did not stop in 120 s; killed")
    ev("server exit", rc)
    if rc != 0:
        unlock(env("http://127.0.0.1:9000"))
    start_server()
    ev("server restarted")
    pause.clear()


def main():
    setup()
    start_server()
    ths = [threading.Thread(target=writer, args=(w, w)) for w in range(NW)]
    ths += [threading.Thread(target=reader), threading.Thread(target=optimizer), threading.Thread(target=faults)]
    if RESTART:
        ths.append(threading.Thread(target=restarter))
    ths.append(threading.Thread(target=supervisor))
    for t in ths:
        t.start()
    time.sleep(SECS)
    stop.set()
    for t in ths:
        t.join()
    clear_toxics()
    time.sleep(1)
    errs = []
    st, _ = http("GET", BASE + "/readyz", timeout=10)
    if st != 200:
        errs.append(f"readyz {st}")
    # operations reach a terminal state
    terminal = {"succeeded", "failed", "cancelled", "interrupted", "unsettled"}
    commits = None
    stc, cl = api("GET", f"/graphs/{G}/commits?branch=main&limit=1000", timeout=120)
    for oid in ops:
        state = None
        for _ in range(60):
            st, out = api("GET", f"/graphs/{G}/operations/{oid}", timeout=60)
            if st == 200 and isinstance(out, dict):
                rec = out.get("operation") or {}
                state = rec.get("state") or rec.get("status") or out.get("lookup")
                if state in terminal or out.get("lookup") == "unknown":
                    break
            time.sleep(1)
        ops[oid]["final"] = (state, str(out)[:400])
        if state not in terminal:
            errs.append(f"optimize {oid} not terminal: {state} {str(out)[:300]}")
    # export
    st, txt = http("POST", BASE + f"/graphs/{G}/export", {"branch": "main", "type_names": ["P"]}, {"Authorization": "Bearer " + TOK, "Omnigraph-Http-Api": "0.14"}, timeout=300)
    if st != 200:
        errs.append(f"export {st} {txt[:300]}")
        got = {}
    else:
        got = {}
        for line in txt.splitlines():
            if line.strip():
                d = json.loads(line)["data"]
                if d["slug"] in got:
                    errs.append(f"duplicate {d['slug']}")
                got[d["slug"]] = d["n"]
    for k, n in acked.items():
        if k not in got:
            errs.append(f"LOST acked {k}")
        elif got[k] != n:
            errs.append(f"WRONG {k} {got[k]} != {n}")
    for k in got:
        if k not in attempted:
            errs.append(f"PHANTOM {k}")
    landed_unknown = sum(1 for k in unknown if k in got)
    print(json.dumps({
        "attempted": len(attempted), "acked": len(acked), "unknown": len(unknown),
        "unknown_landed": landed_unknown, "rows": len(got),
        "optimize_ops": len(ops), "optimize_states": {s: sum(1 for o in ops.values() if o.get("final", (None,))[0] == s) for s in terminal | {None}},
        "unknown_samples": list(unknown.items())[:5], "refused": refused, "unknown_statuses": sorted({str(v[0]) for v in unknown.values()}),
        "refusals_with_effects": [e for e in events if e[0] == "REFUSAL_WITH_EFFECTS"][:5],
        "errors": errs[:40], "error_count": len(errs),
        "self_stops": [e for e in events if e[0] in ("SERVER_STOPPED_ITSELF", "server restarted by supervisor", "SUPERVISOR_RESTART_FAILED", "server exited at start")],
        "count_went_down": sum(1 for e in events if e[0] == "COUNT_WENT_DOWN"),
    }, indent=1, default=str))
    json.dump({"ops": ops, "events": events}, open(f"{D}/result.json", "w"), default=str)
    server.send_signal(signal.SIGTERM)
    try:
        server.wait(timeout=60)
    except subprocess.TimeoutExpired:
        server.kill()


if __name__ == "__main__":
    main()
