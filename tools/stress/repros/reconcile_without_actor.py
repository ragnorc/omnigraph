#!/usr/bin/env python3
"""The documented reconciliation of a deployment that a killed server left
outstanding (`cluster apply --deployment-id ID --writers-stopped`, as in
docs/user/clusters/index.md) fails on a cluster whose graph has a policy:
the engine's policy check gets no actor, the CLI reports
`deployment_outcome_unknown` with "no actor for engine-layer policy check ...
almost certainly a bug", and it retains a new admission lock. The same
command with `--as <actor>` reconciles.

Steps: a local cluster with a graph policy; a served `cluster apply` that adds
a property; SIGKILL the server while it runs (repeated until a deployment is
left outstanding); release the dead server's lock; run the documented command.

Usage: OG=... OGS=... [REPRO_S3=1] reconcile_without_actor.py
REPRO_S3=1 uses an S3 root (RustFS on 9000) with the server behind toxiproxy
latency on 9100, which makes a kill after acceptance leave the graph started."""
import json, os, re, shutil, signal, subprocess, sys, time, urllib.request

OG, OGS = os.environ.get("OG", "omnigraph"), os.environ.get("OGS", "omnigraph-server")
HERE = os.path.dirname(os.path.abspath(__file__))
D = "/tmp/og/repro-reconcile"
S3 = os.environ.get("REPRO_S3") == "1"
ROOT = f"s3://omnigraph-ci/stress/repro-reconcile-{int(time.time())}" if S3 else f"{D}/root"
S3ENV = {"AWS_ACCESS_KEY_ID": "omnigraphci", "AWS_SECRET_ACCESS_KEY": "omnigraphci-secret", "AWS_REGION": "us-east-1", "AWS_ALLOW_HTTP": "true", "AWS_S3_FORCE_PATH_STYLE": "true"}
if S3:
    os.environ.update(S3ENV, AWS_ENDPOINT_URL="http://127.0.0.1:9000", AWS_ENDPOINT_URL_S3="http://127.0.0.1:9000")
PORT, TLS = 18130, 18453


def sh(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, **kw)


def status():
    return json.loads(sh([OG, "cluster", "status", "--target", ROOT, "--json"]).stdout)


def unlock():
    lid = status().get("lock_id")
    if lid:
        sh([OG, "cluster", "force-unlock", lid, "--target", ROOT, "--yes"])


def bundle(n):
    open(f"{D}/src/g.pg", "w").write("node P {\n  slug: String @key\n" + "".join(f"  x{i}: I64?\n" for i in range(1, n + 1)) + "}\n")


shutil.rmtree(D, ignore_errors=True)
os.makedirs(f"{D}/src")
if not S3:
    os.makedirs(ROOT)
open(f"{D}/src/p.yaml", "w").write("version: 1\ngroups:\n  ops: [act]\nrules:\n  - id: b\n    allow:\n      actors: { group: ops }\n      actions: [read, change]\n      branch_scope: any\n  - id: g\n    allow:\n      actors: { group: ops }\n      actions: [schema_apply]\n")
open(f"{D}/src/c.yaml", "w").write("version: 1\ngroups:\n  ops: [act]\nrules:\n  - id: m\n    allow:\n      actors: { group: ops }\n      actions: [config_manage]\n")
open(f"{D}/src/cluster.yaml", "w").write("version: 1\nmetadata:\n  name: r\ngraphs:\n  g:\n    schema: g.pg\npolicies:\n  p:\n    file: p.yaml\n    applies_to: [g]\n  c:\n    file: c.yaml\n    applies_to: [cluster]\n")
bundle(0)
sh([OG, "cluster", "apply", "--target", ROOT, "--config", f"{D}/src", "--json"], check=True)
unlock()
sh(["sh", "-c", f"""cd {D} && openssl req -x509 -newkey rsa:2048 -nodes -keyout ca.key -out ca.pem -days 2 -subj /CN=ca -addext basicConstraints=critical,CA:TRUE -addext keyUsage=critical,keyCertSign,cRLSign && openssl req -newkey rsa:2048 -nodes -keyout leaf.key -out leaf.csr -subj /CN=127.0.0.1 && printf 'subjectAltName=IP:127.0.0.1\\nbasicConstraints=CA:FALSE\\nextendedKeyUsage=serverAuth\\n' > ext.cnf && openssl x509 -req -in leaf.csr -CA ca.pem -CAkey ca.key -CAcreateserial -out leaf.pem -days 2 -extfile ext.cnf"""], check=True)
os.makedirs(f"{D}/home")
open(f"{D}/home/config.yaml", "w").write(f"version: 1\ntargets:\n  srv:\n    kind: server\n    endpoint: https://127.0.0.1:{TLS}/\n    default_graph: g\n    auth: {{provider: token, source: {{env: OG_REPRO_TOKEN}}}}\n    transport:\n      tls:\n        ca: {{mode: append, file: {D}/ca.pem}}\n")
proxy = subprocess.Popen([sys.executable, "-I", f"{HERE}/../tlsproxy.py", str(TLS), str(PORT), f"{D}/leaf.pem", f"{D}/leaf.key"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
senv = dict(os.environ, OMNIGRAPH_SERVER_BEARER_TOKENS_JSON='{"act":"tok"}')
if S3:  # the server reaches the store through toxiproxy (listen 9100), slowed so the kill lands mid-graph
    senv.update(AWS_ENDPOINT_URL="http://127.0.0.1:9100", AWS_ENDPOINT_URL_S3="http://127.0.0.1:9100")
    def toxi(m, path, body=None):
        r = urllib.request.Request("http://127.0.0.1:8474" + path, data=json.dumps(body).encode() if body else None, method=m)
        try:
            urllib.request.urlopen(r, timeout=10).read()
        except Exception:
            pass
    toxi("DELETE", "/proxies/s3")
    toxi("POST", "/proxies", {"name": "s3", "listen": "127.0.0.1:9100", "upstream": "127.0.0.1:9000"})
cenv = dict(os.environ, OMNIGRAPH_HOME=f"{D}/home", OG_REPRO_TOKEN="tok")
outstanding = None
try:
    for attempt in range(1, 41):
        srv = subprocess.Popen([OGS, "--cluster", ROOT, "--bind", f"127.0.0.1:{PORT}"], stdout=open(f"{D}/server.log", "a"), stderr=subprocess.STDOUT, env=senv)
        for _ in range(120):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/readyz", timeout=2)
                break
            except Exception:
                time.sleep(0.25)
        bundle(attempt)
        if S3:
            toxi("POST", "/proxies/s3/toxics", {"name": "slow", "type": "latency", "stream": "downstream", "toxicity": 1.0, "attributes": {"latency": 150}})
        apply = subprocess.Popen([OG, "cluster", "apply", "--target", "srv", "--config", f"{D}/src", "--no-wait", "--json"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=cenv)
        apply.wait()  # returns once the deployment is durably accepted
        time.sleep(0.1 * (attempt % 8))
        srv.send_signal(signal.SIGKILL)
        srv.wait()
        if S3:
            toxi("DELETE", "/proxies/s3/toxics/slow")
        st = status()
        lk = sh([OG, "cluster", "status", "--target", ROOT, "--deployment-id", st.get("outstanding_id") or "-", "--json"]).stdout if st.get("outstanding_id") else ""
        if st.get("outstanding_id") and '"started"' in lk:
            outstanding = st["outstanding_id"]
            print(f"attempt {attempt}: outstanding deployment {outstanding}, graph g started")
            break
        if st.get("outstanding_id"):  # outstanding but its graph not started: reconcile it and try again
            unlock()
            sh([OG, "cluster", "apply", "--target", ROOT, "--deployment-id", st["outstanding_id"], "--writers-stopped", "--as", "act", "--json"])
        unlock()
    if not outstanding:
        sys.exit("no outstanding deployment after 40 attempts")
    unlock()  # the killed server's lock; the server is stopped
    p = sh([OG, "cluster", "apply", "--target", ROOT, "--deployment-id", outstanding, "--writers-stopped", "--json"])
    print("documented command, no --as: exit", p.returncode)
    print("  stdout:", " ".join(p.stdout.split())[:600])
    print("  stderr:", " ".join(re.sub(r"\x1b\[[0-9;]*m", "", p.stderr).split())[-500:])
    print("  status after:", json.dumps({k: status().get(k) for k in ("lock_id", "outstanding_id")}))
    unlock()
    q = sh([OG, "cluster", "apply", "--target", ROOT, "--deployment-id", outstanding, "--writers-stopped", "--as", "act", "--json"])
    print("same command with --as act: exit", q.returncode, "status", (json.loads(q.stdout).get("status") if q.stdout.strip().startswith("{") else q.stdout[:200]))
    print("  status after:", json.dumps({k: status().get(k) for k in ("lock_id", "outstanding_id")}))
finally:
    proxy.kill()
