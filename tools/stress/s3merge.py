#!/usr/bin/env python3
"""Branch writes, merges and served schema applies on an S3 cluster root
(RustFS) with toxiproxy between the server and the store.

The run repeats two phases until the time is up:
- branch phase: create K branches, write keyed rows to each, merge each
  branch into main while its writers still run, then stop the writers,
  merge again and delete the branch;
- schema phase: with only main live, submit a served `cluster apply` that
  adds a nullable property (and a stored query that reads it) through the
  CLI and a server target.
Writers on main run the whole time. A fault thread adds and removes
toxiproxy toxics on the S3 side.

At the end the toxics are removed, every live branch is merged once more,
and the checks are:
- after a merge answered 200, every row acknowledged on the source branch
  before that merge was sent is on main;
- every acknowledged row (main or branch) is on main, with its value, once;
- no row is on main that no writer attempted;
- every apply that exited 0 left its property in the served schema, every
  property in the schema came from an attempted apply, and the stored
  query of the last succeeded apply answers;
- main accepts a write after the run, and the server answers /readyz.

Usage: s3merge.py <seconds> [--no-faults]; HARSH=1 adds long outages (timeout 0, reset_peer 0, limit_data 500); KILL_APPLY=1 SIGKILLs the server during half of the applies, then observes each by its deployment id
Needs: RustFS on 127.0.0.1:9000 with bucket omnigraph-ci, toxiproxy-server
on 8474, openssl, OG and OGS pointing at the CLI and server binaries."""
import json, os, random, shutil, signal, subprocess, sys, threading, time, urllib.request, urllib.error

OG = os.environ.get("OG", "omnigraph")
OGS = os.environ.get("OGS", "omnigraph-server")
SECS = int(sys.argv[1])
FAULTS = "--no-faults" not in sys.argv
HARSH = os.environ.get("HARSH") == "1"
KILL_APPLY = os.environ.get("KILL_APPLY") == "1"
K = int(os.environ.get("K", "3"))
D = "/tmp/og/s3merge"
ROOT = f"s3://omnigraph-ci/stress/s3merge-{int(time.time())}"
PORT, TLSPORT = 18090, 18443
BASE = f"http://127.0.0.1:{PORT}"
G = "g"
TOK = "tok-m"
TOXI = "http://127.0.0.1:8474"
HERE = os.path.dirname(os.path.abspath(__file__))
S3ENV = {
    "AWS_ACCESS_KEY_ID": "omnigraphci", "AWS_SECRET_ACCESS_KEY": "omnigraphci-secret",
    "AWS_REGION": "us-east-1", "AWS_ALLOW_HTTP": "true", "AWS_S3_FORCE_PATH_STYLE": "true",
}
events = []
elock = threading.Lock()


def ev(*a):
    with elock:
        events.append((round(time.time(), 2),) + a)
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
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:3000]
    except Exception as e:
        return None, repr(e)


def api(method, path, body=None, timeout=90):
    st, txt = http(method, BASE + path, body, {"Authorization": "Bearer " + TOK, "Omnigraph-Http-Api": "0.14"}, timeout)
    try:
        return st, json.loads(txt) if txt else None
    except Exception:
        return st, txt


def effects(out):
    return out.get("disposition", {}).get("effects") if isinstance(out, dict) else None


def toxi(method, path, body=None):
    return http(method, TOXI + path, body)


POLICY = """version: 1
groups:
  ops: [act-m]
rules:
  - id: branch-actions
    allow:
      actors: { group: ops }
      actions: [read, export, change, maintain]
      branch_scope: any
  - id: graph-actions
    allow:
      actors: { group: ops }
      actions: [reclaim, invoke_query, schema_apply]
  - id: branch-to-branch
    allow:
      actors: { group: ops }
      actions: [branch_create, branch_merge, branch_delete]
      target_branch_scope: any
"""
CPOLICY = """version: 1
groups:
  ops: [act-m]
rules:
  - id: manage
    allow:
      actors: { group: ops }
      actions: [config_manage]
"""


def schema(nprops):
    extra = "".join(f"  x{i}: I64?\n" for i in range(1, nprops + 1))
    return "node P {\n  slug: String @key\n  n: I64\n  b: String\n" + extra + "}\n"


def write_bundle(nprops):
    d = f"{D}/bundle"
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(f"{d}/queries")
    open(f"{d}/g.pg", "w").write(schema(nprops))
    open(f"{d}/policy.yaml", "w").write(POLICY)
    open(f"{d}/cpolicy.yaml", "w").write(CPOLICY)
    q = "query count_p() { match { $p: P } return { count($p) as c } }\n"
    if nprops:
        q += f"query with_x() {{ match {{ $p: P\n $p.x{nprops} is null }} return {{ count($p) as c }} }}\n"
    open(f"{d}/queries/q.gq", "w").write(q)
    open(f"{d}/cluster.yaml", "w").write(
        "version: 1\nmetadata:\n  name: s3merge\ngraphs:\n  g:\n    schema: g.pg\n    queries: queries/\n"
        "policies:\n  gp:\n    file: policy.yaml\n    applies_to: [g]\n  cp:\n    file: cpolicy.yaml\n    applies_to: [cluster]\n")
    return d


def tls_and_home():
    sh = f"""set -e; cd {D}
openssl req -x509 -newkey rsa:2048 -nodes -keyout ca.key -out ca.pem -days 2 -subj /CN=og-stress-ca \
  -addext basicConstraints=critical,CA:TRUE -addext keyUsage=critical,keyCertSign,cRLSign 2>/dev/null
openssl req -newkey rsa:2048 -nodes -keyout leaf.key -out leaf.csr -subj /CN=127.0.0.1 2>/dev/null
printf 'subjectAltName=IP:127.0.0.1\\nbasicConstraints=CA:FALSE\\nextendedKeyUsage=serverAuth\\n' > ext.cnf
openssl x509 -req -in leaf.csr -CA ca.pem -CAkey ca.key -CAcreateserial -out leaf.pem -days 2 -extfile ext.cnf 2>/dev/null
"""
    subprocess.run(["sh", "-c", sh], check=True)
    os.makedirs(f"{D}/home", exist_ok=True)
    open(f"{D}/home/config.yaml", "w").write(
        "version: 1\ntargets:\n  srv:\n    kind: server\n"
        f"    endpoint: https://127.0.0.1:{TLSPORT}/\n    default_graph: g\n"
        "    auth: {provider: token, source: {env: OG_STRESS_TOKEN}}\n"
        f"    transport:\n      tls:\n        ca: {{mode: append, file: {D}/ca.pem}}\n")


def cli(args, timeout=900):
    e = dict(os.environ)
    e["OMNIGRAPH_HOME"] = f"{D}/home"
    e["OG_STRESS_TOKEN"] = TOK
    try:
        p = subprocess.run([OG] + args, capture_output=True, text=True, env=e, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return "timeout", "", ""


def find_key(o, key):
    if isinstance(o, dict):
        for k, v in o.items():
            if k == key and isinstance(v, str):
                return v
            r = find_key(v, key)
            if r:
                return r
    if isinstance(o, list):
        for v in o:
            r = find_key(v, key)
            if r:
                return r
    return None


def unlock(e):
    p = subprocess.run([OG, "cluster", "status", "--target", ROOT, "--json"], capture_output=True, text=True, env=e)
    try:
        lock_id = find_key(json.loads(p.stdout), "lock_id")
    except Exception:
        sys.exit("status: " + p.stdout[-800:] + p.stderr[-800:])
    if lock_id:
        q = subprocess.run([OG, "cluster", "force-unlock", lock_id, "--target", ROOT, "--yes"], capture_output=True, text=True, env=e)
        ev("force-unlock", lock_id, q.returncode)


server = None
proxy = None


def start_server(wait=120):
    global server
    e = env("http://127.0.0.1:9100")
    e["OMNIGRAPH_SERVER_BEARER_TOKENS_JSON"] = json.dumps({"act-m": TOK})
    e["OMNIGRAPH_OPERATION_ROUTES"] = "true"
    log = open(f"{D}/server.log", "a")
    server = subprocess.Popen([OGS, "--cluster", ROOT, "--bind", f"127.0.0.1:{PORT}"], stdout=log, stderr=log, env=e)
    for _ in range(wait * 2):
        st, _ = http("GET", BASE + "/readyz", timeout=5)
        if st == 200:
            return
        if server.poll() is not None:
            ev("server exited at start", server.returncode)
            time.sleep(2)
            server = subprocess.Popen([OGS, "--cluster", ROOT, "--bind", f"127.0.0.1:{PORT}"], stdout=log, stderr=log, env=e)
        time.sleep(0.5)
    sys.exit("server not ready")


def setup():
    global proxy
    shutil.rmtree(D, ignore_errors=True)
    os.makedirs(D)
    tls_and_home()
    d = write_bundle(0)
    direct = env("http://127.0.0.1:9000")
    p = subprocess.run([OG, "cluster", "apply", "--target", ROOT, "--config", d, "--json"], capture_output=True, text=True, env=direct)
    if p.returncode:
        sys.exit("apply: " + p.stdout[-1500:] + p.stderr[-1500:])
    unlock(direct)
    toxi("DELETE", "/proxies/s3")
    st, out = toxi("POST", "/proxies", {"name": "s3", "listen": "127.0.0.1:9100", "upstream": "127.0.0.1:9000"})
    if st not in (200, 201):
        sys.exit(f"toxiproxy: {st} {out}")
    open("/tmp/og/proxy_mode", "w").write("pass")
    proxy = subprocess.Popen([sys.executable, "-I", f"{HERE}/tlsproxy.py", str(TLSPORT), str(PORT), f"{D}/leaf.pem", f"{D}/leaf.key"],
                             stdout=open(f"{D}/proxy.log", "w"), stderr=subprocess.STDOUT)


lock = threading.Lock()
attempted = {}      # slug -> (n, branch)
acked = {}          # slug -> (n, branch, ack_time)
unknown = {}        # slug -> (status, text)
refused = {}
merges = []         # (branch, t_sent, status, outcome kind or error)
applies = []        # (nprops, rc, deployment id, tail)
stop = threading.Event()
phase_lock = threading.Lock()


def write_row(branch, slug, n):
    with lock:
        attempted[slug] = (n, branch)
    body = {"query": "query m($k: String, $n: I64, $b: String) { insert P { slug: $k, n: $n, b: $b } }",
            "params": {"k": slug, "n": n, "b": branch}}
    if branch != "main":
        body["branch"] = branch
    st, out = api("POST", f"/graphs/{G}/mutate", body)
    with lock:
        if st == 200:
            acked[slug] = (n, branch, time.time())
        elif st in (400, 401, 403, 404, 409, 412, 429, 503) and effects(out) in (None, "none"):
            key = f"{st}:{out.get('code') if isinstance(out, dict) else ''}"
            refused[key] = refused.get(key, 0) + 1
        else:
            unknown[slug] = (st, str(out)[:300])
    return st, out


def main_writer(wid, stop_ev):
    r = random.Random(wid)
    i = 0
    while not stop_ev.is_set():
        i += 1
        st, _ = write_row("main", f"m{wid}-{i}", r.randint(0, 10**6))
        if st != 200:
            time.sleep(0.3)


def branch_writer(branch, stop_ev, seed):
    r = random.Random(seed)
    i = 0
    while not stop_ev.is_set():
        i += 1
        st, out = write_row(branch, f"{branch}-{i}", r.randint(0, 10**6))
        if st != 200:
            time.sleep(0.3)


def stmt(text, read=False):
    return api("POST", f"/graphs/{G}/{'query' if read else 'mutate'}", {"query": text})


def branches_live():
    st, out = stmt("branch list", read=True)
    if st == 200:
        return sorted(r.get("name") for r in out.get("rows", []))
    return None


def merge(branch):
    t = time.time()
    st, out = stmt(f'branch merge "{branch}" into main')
    kind = None
    if st == 200 and isinstance(out, dict):
        o = out.get("outcome") or {}
        kind = o.get("merge") or o.get("kind")
    with lock:
        merges.append((branch, t, st, kind if st == 200 else str(out)[:300]))
    ev("merge", branch, st, kind if st == 200 else str(out)[:200])
    return st == 200


def branch_phase(cycle):
    names = [f"c{cycle}b{j}" for j in range(K)]
    live = []
    for b in names:
        st, out = stmt(f'branch create "{b}" from main')
        ev("create", b, st, "" if st == 200 else str(out)[:200])
        if st != 200:
            ls = branches_live()
            if ls and b in ls:
                live.append(b)  # created despite the answer
        else:
            live.append(b)
    stops = {b: threading.Event() for b in live}
    ths = [threading.Thread(target=branch_writer, args=(b, stops[b], hash(b) & 0xffff)) for b in live]
    for t in ths:
        t.start()
    t_end = time.time() + 20
    while time.time() < t_end and not stop.is_set():
        time.sleep(random.uniform(3, 6))
        if live:
            merge(random.choice(live))  # merge while the branch writers run
    for e in stops.values():
        e.set()
    for t in ths:
        t.join()
    for b in live:
        ok = False
        for _ in range(3):
            if merge(b):
                ok = True
                break
            time.sleep(2)
        if ok:
            st, out = stmt(f'branch delete "{b}"')
            ev("delete", b, st, "" if st == 200 else str(out)[:200])


def schema_phase():
    ls = branches_live()
    if ls is None or ls != ["main"]:
        ev("schema phase skipped, branches", ls)
        return
    nprops = 1 + len(schema_xs())
    d = write_bundle(nprops)
    t = time.time()
    killer = None
    if KILL_APPLY and random.random() < 0.5:
        def kill_mid_apply():
            time.sleep(random.uniform(0.05, 1.5))
            with lock:
                s = server
            ev("SIGKILL server during apply", nprops)
            s.send_signal(signal.SIGKILL)
        killer = threading.Thread(target=kill_mid_apply)
        killer.start()
    rc, out, err = cli(["cluster", "apply", "--target", "srv", "--config", d, "--timeout", "600", "--json"])
    if killer:
        killer.join()
    dep = None
    try:
        dep = find_key(json.loads(out), "deployment_id")
    except Exception:
        pass
    if not dep:
        m = __import__("re").search(r"Deployment-ID: (\S+)", err)
        dep = m.group(1) if m else None
    with lock:
        applies.append((nprops, rc, dep, round(time.time() - t, 1), (out[-600:] + " | " + err[-600:]).replace("\n", " ")))
    ev("apply", nprops, rc, dep, round(time.time() - t, 1), err[-300:].replace("\n", " "))
    if rc not in (0,):
        # resolve the outcome by its original id, as the docs say, never by resubmitting
        if dep:
            for _ in range(240):  # the supervisor restarts a killed server; a takeover waits out the lease
                if http("GET", BASE + "/readyz", timeout=5)[0] == 200:
                    break
                time.sleep(1)
            rc2, out2, err2 = cli(["cluster", "status", "--target", "srv", "--deployment-id", dep, "--wait", "--timeout", "300", "--json"])
            converged = '"converged": true' in out2
            has = nprops in schema_xs()
            ev("apply status", dep, rc2, "converged" if converged else "not converged", "schema has" if has else "schema lacks", f"x{nprops}", (out2[-300:] + err2[-200:]).replace("\n", " "))
            if converged != has:
                with lock:
                    status_mismatch.append((dep, nprops, converged, has, out2[-1500:], err2[-500:]))
            with lock:
                applies[-1] = applies[-1] + ((rc2, converged, has),)


def schema_xs():
    st, sch = api("GET", f"/graphs/{G}/schema", timeout=60)
    if st != 200:
        return set()
    return {int(x) for x in __import__("re").findall(r"\bx(\d+)\b", json.dumps(sch))}


status_mismatch = []


def faults():
    r = random.Random(13)
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
        kind, attrs = r.choice(kinds)
        stream = r.choice(["upstream", "downstream"])
        name = f"t{n}"
        n += 1
        st, _ = toxi("POST", "/proxies/s3/toxics", {"name": name, "type": kind, "stream": stream, "toxicity": r.choice([0.3, 1.0]), "attributes": attrs})
        ev("toxic on", name, kind, stream, st)
        time.sleep(r.uniform(0.5, 12 if HARSH else 4))
        toxi("DELETE", f"/proxies/s3/toxics/{name}")


def clear_toxics():
    st, out = toxi("GET", "/proxies/s3/toxics")
    try:
        for t in json.loads(out):
            toxi("DELETE", f"/proxies/s3/toxics/{t['name']}")
    except Exception:
        pass


def supervisor():
    while not stop.is_set():
        time.sleep(1)
        if server is not None and server.poll() is not None:
            ev("SERVER_STOPPED_ITSELF", server.returncode)
            start_server(wait=180)
            ev("server restarted by supervisor")


def main():
    setup()
    start_server()
    mstop = threading.Event()
    ths = [threading.Thread(target=main_writer, args=(w, mstop)) for w in range(2)]
    ths.append(threading.Thread(target=supervisor))
    if FAULTS:
        ths.append(threading.Thread(target=faults))
    for t in ths:
        t.start()
    t_end = time.time() + SECS
    cycle = 0
    while time.time() < t_end:
        branch_phase(cycle)
        schema_phase()
        cycle += 1
    stop.set()
    mstop.set()
    for t in ths:
        t.join()
    clear_toxics()
    time.sleep(2)
    errs = []
    if server.poll() is not None:
        errs.append(f"server exited {server.returncode}")
        start_server()
    ls = branches_live() or []
    for b in ls:
        if b != "main":
            if not merge(b):
                errs.append(f"final merge of {b} failed")
    st, _ = http("GET", BASE + "/readyz", timeout=10)
    if st != 200:
        errs.append(f"readyz {st}")
    st, txt = http("POST", BASE + f"/graphs/{G}/export", {"branch": "main", "type_names": ["P"]},
                   {"Authorization": "Bearer " + TOK, "Omnigraph-Http-Api": "0.14"}, timeout=600)
    got = {}
    if st != 200:
        errs.append(f"export {st} {txt[:300]}")
    else:
        for line in txt.splitlines():
            if line.strip():
                dd = json.loads(line)["data"]
                if dd["slug"] in got:
                    errs.append(f"DUPLICATE {dd['slug']}")
                got[dd["slug"]] = (dd["n"], dd["b"])
    for k, (n, b, _) in acked.items():
        if k not in got:
            errs.append(f"LOST acked {k} on {b}")
        elif got[k] != (n, b):
            errs.append(f"WRONG {k} {got[k]} != {(n, b)}")
    for k in got:
        if k not in attempted:
            errs.append(f"PHANTOM {k}")
    # merge prefix property, judged against the final main (merges only add)
    for b, t, s, kind in merges:
        if s == 200:
            before = [k for k, (n, bb, ta) in acked.items() if bb == b and ta < t]
            miss = [k for k in before if k not in got]
            if miss:
                errs.append(f"MERGE_MISSED {b} at {t}: {miss[:3]}")
    # schema
    stq, sch = api("GET", f"/graphs/{G}/schema", timeout=60)
    sch_txt = json.dumps(sch) if stq == 200 else ""
    props = sorted({int(x) for x in __import__("re").findall(r"\bx(\d+)\b", sch_txt)})
    ok_applies = [a[0] for a in applies if a[1] == 0]
    for p in ok_applies:
        if p not in props:
            errs.append(f"APPLY_LOST x{p}")
    for m in status_mismatch:
        errs.append(f"STATUS_SCHEMA_MISMATCH {m[0]} x{m[1]} converged={m[2]} schema_has={m[3]}")
    if props != list(range(1, len(props) + 1)):
        errs.append(f"SCHEMA_GAP {props}")
    tried = {a[0] for a in applies}
    for p in props:
        if p not in tried:
            errs.append(f"SCHEMA_PHANTOM x{p}")
    if stq != 200:
        errs.append(f"schema read {stq} {str(sch)[:200]}")
    if ok_applies:
        stw, outw = api("POST", f"/graphs/{G}/queries/with_x", {"params": {}})
        if stw != 200:
            errs.append(f"stored query with_x {stw} {str(outw)[:200]}")
    st, out = write_row("main", "final-write", 1)
    if st != 200:
        errs.append(f"final write {st} {str(out)[:200]}")
    res = {
        "root": ROOT, "attempted": len(attempted), "acked": len(acked), "unknown": len(unknown),
        "unknown_landed": sum(1 for k in unknown if k in got), "rows": len(got), "refused": refused,
        "merges": len(merges), "merge_statuses": sorted({str(m[2]) + ":" + str(m[3])[:60] for m in merges}),
        "applies": [a[:5] for a in applies], "schema_props": props,
        "unknown_samples": list(unknown.items())[:5],
        "self_stops": [e for e in events if "SERVER" in str(e[1]) or "server" in str(e[1])],
        "errors": errs[:40], "error_count": len(errs),
    }
    print(json.dumps(res, indent=1, default=str))
    json.dump({"res": res, "events": events, "merges": merges, "applies": applies}, open(f"{D}/result.json", "w"), default=str)
    server.send_signal(signal.SIGTERM)
    try:
        server.wait(timeout=60)
    except subprocess.TimeoutExpired:
        server.kill()
    proxy.kill()


if __name__ == "__main__":
    main()
