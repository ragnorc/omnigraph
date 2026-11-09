"""Shared helpers for the optimize stress harnesses (stdlib only).

Talks to a real `omnigraph-server` over HTTP, applies cluster roots with the
real `omnigraph` CLI, and reads `__cluster/operations.json` straight from a
local root so every phase can check the record against what clients saw.
"""

from __future__ import annotations

import json
import os
import random
import signal
import subprocess
import threading
import time
import http.client
import urllib.parse
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BIN = Path(os.environ.get("OG_BIN_DIR", REPO / "target" / "debug"))
OMNIGRAPH = str(BIN / "omnigraph")
SERVER = str(BIN / "omnigraph-server")
FIXTURE = REPO / "stress" / "fixture"
CONTRACT = ("omnigraph-http-api", "0.14")
TOKENS = {"operator": "op-secret", "writer": "wr-secret", "reader": "rd-secret"}
TERMINAL = {"succeeded", "failed", "cancelled", "interrupted", "unsettled"}

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid(ms: int | None = None, rand: int | None = None) -> str:
    """A canonical ULID at `ms` (default now)."""
    if ms is None:
        ms = int(time.time() * 1000)
    if rand is None:
        rand = random.getrandbits(80)
    value = (ms << 80) | rand
    out = []
    for _ in range(26):
        out.append(CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(out))


def ulid_ms(text: str) -> int:
    value = 0
    for ch in text:
        value = value * 32 + CROCKFORD.index(ch)
    return value >> 80


class Http:
    """One keep-alive-free HTTP client; thread safe (a connection per call)."""

    def __init__(self, base: str, timeout: float = 120.0):
        parsed = urllib.parse.urlparse(base)
        self.host = parsed.hostname
        self.port = parsed.port
        self.timeout = timeout

    def call(self, method, path, body=None, token="op-secret", timeout=None, headers=None):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=timeout or self.timeout)
        hdrs = {CONTRACT[0]: CONTRACT[1]}
        if token:
            hdrs["authorization"] = f"Bearer {token}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            hdrs["content-type"] = "application/json"
        if headers:
            hdrs.update(headers)
        try:
            conn.request(method, path, body=data, headers=hdrs)
            resp = conn.getresponse()
            raw = resp.read()
            try:
                payload = json.loads(raw) if raw else None
            except ValueError:
                payload = {"raw": raw.decode(errors="replace")}
            return resp.status, payload, dict(resp.getheaders())
        finally:
            conn.close()

    # --- graph data ---
    def load(self, graph, ndjson, branch=None, mode=None, token="wr-secret"):
        body = {"data": ndjson}
        if branch:
            body["branch"] = branch
        if mode:
            body["mode"] = mode
        return self.call("POST", f"/graphs/{graph}/load", body, token=token)

    def mutate(self, graph, query, params=None, branch=None, token="wr-secret"):
        body = {"query": query}
        if params is not None:
            body["params"] = params
        if branch:
            body["branch"] = branch
        return self.call("POST", f"/graphs/{graph}/mutate", body, token=token)

    def query(self, graph, query, params=None, branch=None, token="rd-secret", snapshot=None):
        body = {"query": query}
        if params is not None:
            body["params"] = params
        if branch:
            body["branch"] = branch
        if snapshot:
            body["snapshot"] = snapshot
        return self.call("POST", f"/graphs/{graph}/query", body, token=token)

    def branch_create(self, graph, name, token="wr-secret"):
        return self.call("POST", f"/graphs/{graph}/branches", {"name": name}, token=token)

    def merge(self, graph, source, target="main", delete_branch=False, token="wr-secret"):
        body = {"source": source, "target": target, "delete_branch": delete_branch}
        return self.call("POST", f"/graphs/{graph}/branches/merge", body, token=token)

    def commits(self, graph, branch="main", limit=None, token="rd-secret"):
        q = f"?branch={branch}" + (f"&limit={limit}" if limit else "")
        return self.call("GET", f"/graphs/{graph}/commits{q}", token=token)

    def commit_show(self, graph, commit, token="rd-secret"):
        return self.call("GET", f"/graphs/{graph}/commits/{commit}", token=token)

    # --- recorded operations ---
    def submit(self, graph, op_id, tables=None, hold_ms=None, token="op-secret", timeout=None):
        body = {"operation_id": op_id}
        if tables is not None:
            body["tables"] = tables
        if hold_ms is not None:
            body["max_gate_hold_ms"] = hold_ms
        return self.call("POST", f"/graphs/{graph}/operations/optimize", body, token=token, timeout=timeout)

    def lookup(self, graph, op_id, token="op-secret", timeout=None):
        return self.call("GET", f"/graphs/{graph}/operations/{op_id}", token=token, timeout=timeout)

    def cancel(self, graph, op_id, token="op-secret"):
        return self.call("POST", f"/graphs/{graph}/operations/{op_id}/cancel", token=token)

    def ready(self):
        try:
            status, body, _ = self.call("GET", "/readyz", token=None, timeout=5)
            return status == 200
        except OSError:
            return False

    def wait_terminal(self, graph, op_id, deadline_s=300, poll=0.2):
        end = time.time() + deadline_s
        last = None
        while time.time() < end:
            status, body, _ = self.lookup(graph, op_id)
            last = (status, body)
            if status == 200 and body and body.get("operation") and body["operation"]["state"] in TERMINAL:
                return body["operation"]
            time.sleep(poll)
        raise TimeoutError(f"{op_id} not terminal: {last}")


def count_rows(http: Http, graph: str, type_name: str, branch=None) -> int:
    q = f"query c() {{ match {{ $x: {type_name} }} return {{ count($x) as n }} }}"
    status, body, _ = http.query(graph, q, branch=branch)
    if status != 200:
        raise RuntimeError(f"count {type_name}: {status} {body}")
    rows = body.get("rows") or body.get("data") or []
    if isinstance(rows, list) and rows:
        row = rows[0]
        return int(row.get("n") if isinstance(row, dict) else row[0])
    raise RuntimeError(f"count {type_name}: unexpected {body}")


def keys_of(http: Http, graph: str, type_name: str, key: str, branch=None) -> set:
    q = f"query k() {{ match {{ $x: {type_name} }} return {{ $x.{key} as k }} }}"
    status, body, _ = http.query(graph, q, branch=branch)
    if status != 200:
        raise RuntimeError(f"keys {type_name}: {status} {body}")
    return {row["k"] for row in body.get("rows", [])}


# --- cluster lifecycle ---

def env_base(home: str) -> dict:
    env = dict(os.environ)
    env["OMNIGRAPH_HOME"] = home
    env["RUST_MIN_STACK"] = "16777216"
    env.pop("OMNIGRAPH_SERVER_BEARER_TOKEN", None)
    env.pop("OMNIGRAPH_SERVER_BEARER_TOKENS_FILE", None)
    return env


def apply_cluster(root: str, config: Path = FIXTURE, home: str = "/tmp/oghome"):
    """Direct apply, then release the lock it keeps (as test support does)."""
    os.makedirs(home, exist_ok=True)
    if root.startswith("az://"):
        import shutil, tempfile
        tmp = tempfile.mkdtemp(prefix="azcfg-")
        shutil.copytree(config, tmp, dirs_exist_ok=True)
        text = (Path(tmp) / "cluster.yaml").read_text()
        (Path(tmp) / "cluster.yaml").write_text(f"storage: {root}\n" + text)
        return az_admin(root, "bootstrap", tmp, home)
    env = env_base(home)
    out = subprocess.run([OMNIGRAPH, "cluster", "apply", "--config", str(config), "--target", root, "--json"],
                         env=env, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"apply failed: {out.stdout[-2000:]} {out.stderr[-2000:]}")
    unlock(root, home)
    return out


def unlock(root: str, home: str = "/tmp/oghome"):
    if root.startswith("az://"):
        return (prepare_restart(root) or {}).get("unlocked")
    env = env_base(home)
    st = subprocess.run([OMNIGRAPH, "cluster", "status", "--target", root, "--json"], env=env,
                        capture_output=True, text=True)
    lock_id = json.loads(st.stdout[st.stdout.index("{"):]).get("lock_id")
    if lock_id:
        subprocess.run([OMNIGRAPH, "--target", root, "cluster", "force-unlock", lock_id, "--json"],
                       env=env, capture_output=True, text=True, check=True)
    return lock_id


class Server:
    """A real `omnigraph-server` child on a cluster root."""

    def __init__(self, root: str, port: int, log: str, extra_env: dict | None = None, binary: str = SERVER,
                 args: list | None = None):
        self.root, self.port, self.log = root, port, log
        env = env_base("/tmp/oghome")
        env["OMNIGRAPH_SERVER_BEARER_TOKENS_JSON"] = json.dumps(TOKENS)
        env["OMNIGRAPH_OPERATION_ROUTES"] = "true"
        env.update(extra_env or {})
        self.env = env
        self.binary = binary
        self.args = args
        self.proc = None
        self.http = Http(f"http://127.0.0.1:{port}")

    def start(self, wait=True, timeout=60):
        logf = open(self.log, "ab")
        args = self.args if self.args is not None else ["--cluster", self.root, "--bind", f"127.0.0.1:{self.port}"]
        cmd = [self.binary, *args]
        if self.root.startswith("az://"):
            self.env.update(AZ_ENV)
            cmd = [WRAPPER, "run", "--root", self.root, "--mode", "server", "--retry-seconds", "1",
                   "--grace-seconds", os.environ.get("STRESS_AZ_GRACE", "30"), "--", *cmd]
        self.proc = subprocess.Popen(cmd, env=self.env, stdout=logf, stderr=subprocess.STDOUT)
        if wait:
            end = time.time() + timeout
            while time.time() < end:
                if self.proc.poll() is not None:
                    raise RuntimeError(f"server exited {self.proc.returncode}; see {self.log}")
                if self.http.ready():
                    return self
                time.sleep(0.1)
            raise TimeoutError("server not ready")
        return self

    def child_pids(self):
        out = []
        for d in Path("/proc").iterdir():
            if not d.name.isdigit():
                continue
            try:
                stat = (d / "stat").read_text()
            except OSError:
                continue
            if int(stat.rsplit(")", 1)[1].split()[1]) == self.proc.pid:
                out.append(int(d.name))
        return out

    def kill(self, sig=signal.SIGKILL, wait=60):
        if self.root.startswith("az://") and sig == signal.SIGKILL and self.proc and self.proc.poll() is None:
            # SIGKILL of the wrapper would orphan the server: kill the server
            # first (a crash), then the wrapper, which strands the lease.
            for pid in self.child_pids():
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
            for pid in self.child_pids():
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(sig)
            try:
                self.proc.wait(timeout=wait)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        return self.proc.returncode if self.proc else None


S3_ENV = {
    "AWS_ACCESS_KEY_ID": "omnigraphci", "AWS_SECRET_ACCESS_KEY": "omnigraphci-secret",
    "AWS_REGION": "us-east-1", "AWS_ENDPOINT_URL": "http://127.0.0.1:9000",
    "AWS_ENDPOINT_URL_S3": "http://127.0.0.1:9000", "AWS_ALLOW_HTTP": "true",
    "AWS_S3_FORCE_PATH_STYLE": "true",
}
if os.environ.get("STRESS_S3_PREFIX"):
    os.environ.update(S3_ENV)

# Azure (Azurite): every writer runs under the admission wrapper; the CLI
# reaches az:// only through a registered target, so cluster administration
# uses the served child's `stress_cluster_admin` mode.
AZ_ENV = {"AZURE_STORAGE_USE_EMULATOR": "true", "AZURE_STORAGE_ACCOUNT_NAME": "devstoreaccount1"}
if os.environ.get("STRESS_AZ_PREFIX"):
    os.environ.update(AZ_ENV)
WRAPPER = str(BIN / "omnigraph-azure-admission")


def _child_binary():
    if os.environ.get("OG_CHILD_BIN"):
        return os.environ["OG_CHILD_BIN"]
    deps = REPO / "target" / "debug" / "deps"
    cands = [p for p in deps.glob("stress_served_child-*") if p.is_file() and os.access(p, os.X_OK) and p.suffix == ""]
    return str(max(cands, key=lambda p: p.stat().st_mtime))


def az_admin(root: str, mode: str, config: str | None = None, home: str = "/tmp/oghome"):
    env = env_base(home)
    env.update(AZ_ENV)
    env["STRESS_ADMIN"] = mode
    env["STRESS_ADMIN_ROOT"] = root
    if config:
        env["STRESS_CONFIG"] = config
    out = subprocess.run([WRAPPER, "run", "--root", root, "--mode", "job", "--retry-seconds", "1", "--",
                          _child_binary(), "--ignored", "--exact", "stress_cluster_admin", "--nocapture"],
                         env=env, capture_output=True, text=True, timeout=600)
    if out.returncode != 0:
        raise RuntimeError(f"az admin {mode} failed: {out.stdout[-2000:]} {out.stderr[-2000:]}")
    line = next((l for l in out.stdout.splitlines() if l.startswith("STRESS_UNLOCKED")), "STRESS_UNLOCKED none")
    lock = line.split()[1]
    return None if lock == "none" else lock


def az_inspect(root: str) -> dict:
    env = dict(os.environ); env.update(AZ_ENV)
    out = subprocess.run([WRAPPER, "inspect", "--root", root], env=env, capture_output=True, text=True, timeout=60)
    return dict(l.split("=", 1) for l in out.stdout.splitlines() if "=" in l)


def az_break(root: str) -> bool:
    """Break a stranded lease. Only after the harness has seen the old process exit."""
    info = az_inspect(root)
    if info.get("lease_status") != "locked":
        return False
    env = dict(os.environ); env.update(AZ_ENV)
    subprocess.run([WRAPPER, "break", "--root", root, "--confirm-root-sha256", info["root_sha256"],
                    "--confirm-no-old-processes"], env=env, capture_output=True, text=True, timeout=60, check=True)
    return True


def prepare_restart(root: str):
    """Azure: a stopped server retains its lease and cluster lock; recover both."""
    if not root.startswith("az://"):
        return None
    broke = az_break(root)
    lock = az_admin(root, "unlock")
    return {"lease_broken": broke, "unlocked": lock}


def make_root(local_dir, name: str) -> str:
    """A fresh cluster root: under STRESS_S3_PREFIX when set, else a local directory."""
    az = os.environ.get("STRESS_AZ_PREFIX")
    if az:
        return f"{az.rstrip('/')}/{name}-{time.time_ns()}"
    prefix = os.environ.get("STRESS_S3_PREFIX")
    if prefix:
        return f"{prefix.rstrip('/')}/{name}-{time.time_ns()}"
    root = str(Path(local_dir) / "root")
    os.makedirs(root, exist_ok=True)
    return root


def read_record(root: str):
    if root.startswith("az://"):
        import azutil
        container, _, key = root[5:].partition("/")
        status, data = azutil.get_blob(container, f"{key}/__cluster/operations.json")
        if status == 404:
            return None
        if status != 200:
            raise RuntimeError(f"az get record {status} {data[:200]}")
        return json.loads(data)
    if root.startswith("s3://"):
        import s3util
        bucket, _, key = root[5:].partition("/")
        status, data = s3util.request("GET", f"/{bucket}/{key}/__cluster/operations.json")
        if status == 404:
            return None
        if status != 200:
            raise RuntimeError(f"s3 get record {status} {data[:200]}")
        return json.loads(data)
    path = Path(root) / "__cluster" / "operations.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def record_ops(record) -> list:
    if not record:
        return []
    return list(record.get("active", {}).values()) + record.get("unresolved", []) + record.get("terminal", [])


class Log:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()

    def __call__(self, *parts):
        line = time.strftime("%H:%M:%S") + " " + " ".join(str(p) for p in parts)
        with self.lock:
            print(line, flush=True)
            with open(self.path, "a") as f:
                f.write(line + "\n")
