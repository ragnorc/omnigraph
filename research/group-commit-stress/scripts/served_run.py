#!/usr/bin/env python3
"""Orchestrate one served stress run against real binaries.

1. Bootstrap a fresh cluster root (local dir or s3:// prefix) with
   `omnigraph cluster apply`, release its admission lock.
2. Start `omnigraph-server` (env from --server-env, group commit on by default).
3. Seed hot Items and People over HTTP, build indexes with an optimize
   operation, export the seed.
4. Run `http_stress.py run` (closed-loop writers, readers, metrics), optionally
   with maintenance (optimize operations) and chaos (kill -9 + restart).
5. Export the final state and the commit list, stop the server, run the
   checker. Everything lands in research/group-commit-stress/served/<label>/.
"""
import argparse
import json
import os
import random
import signal
import subprocess
import sys
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
CLUSTER_SRC = os.path.join(HERE, "..", "cluster")
TOKENS = os.path.join(CLUSTER_SRC, "tokens.json")
PY = "/srv/venv/bin/python"
S3_ENV = {
    "AWS_ACCESS_KEY_ID": "omnigraphci",
    "AWS_SECRET_ACCESS_KEY": "omnigraphci-secret",
    "AWS_REGION": "us-east-1",
    "AWS_ALLOW_HTTP": "true",
    "AWS_S3_FORCE_PATH_STYLE": "true",
}
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid():
    value = (int(time.time() * 1000) << 80) | random.getrandbits(80)
    return "".join(CROCKFORD[(value >> (5 * i)) & 31] for i in range(25, -1, -1))


def http(method, url, body=None, token="tok-0-stress", timeout=120, raw=False):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Omnigraph-Http-Api", "0.14")
    req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
            return resp.status, (payload if raw else json.loads(payload or b"null"))
    except urllib.error.HTTPError as error:
        payload = error.read()
        try:
            return error.code, json.loads(payload)
        except ValueError:
            return error.code, payload.decode(errors="replace")


class Server:
    def __init__(self, args, root, outdir):
        self.args, self.root, self.outdir = args, root, outdir
        self.proc = None
        self.restarts = 0
        self.env = dict(os.environ)
        self.env.update({
            "OMNIGRAPH_SERVER_BEARER_TOKENS_FILE": TOKENS,
            "RUST_LOG": args.rust_log,
            "OMNIGRAPH_GROUP_COMMIT": "1" if args.group_commit else "0",
            "OMNIGRAPH_OPERATION_ROUTES": "true",
        })
        if root.startswith("s3://"):
            self.env.update(S3_ENV)
            self.env["AWS_ENDPOINT_URL"] = self.env["AWS_ENDPOINT_URL_S3"] = f"http://127.0.0.1:{args.s3_port}"
        for pair in args.server_env:
            key, value = pair.split("=", 1)
            self.env[key] = value
        os.chmod(TOKENS, 0o600)

    def cli(self, *argv, check=True):
        out = subprocess.run([os.path.join(self.args.bins, "omnigraph"), *argv], env=self.env,
                             capture_output=True, text=True)
        if check and out.returncode != 0:
            raise RuntimeError(f"omnigraph {' '.join(argv)} failed: {out.stderr[-2000:]}")
        return out

    def bootstrap(self):
        out = self.cli("cluster", "apply", "--target", self.root, "--config", CLUSTER_SRC, "--as", "act-0", "--json")
        lock = [line for line in out.stderr.splitlines() if line.startswith("Admission lock:")]
        lock_id = lock[0].split(":", 1)[1].split(";")[0].strip()
        self.cli("--target", self.root, "cluster", "force-unlock", lock_id)

    def unlock_stale(self):
        status = self.cli("--target", self.root, "cluster", "status", "--json", check=False)
        try:
            data = json.loads(status.stdout)
        except ValueError:
            return None
        lock_id = None

        def find(obj):
            nonlocal lock_id
            if isinstance(obj, dict):
                for key, value in obj.items():
                    if key == "lock_id" and isinstance(value, str):
                        lock_id = value
                    find(value)
            elif isinstance(obj, list):
                for value in obj:
                    find(value)

        find(data)
        if lock_id:
            self.cli("--target", self.root, "cluster", "force-unlock", lock_id, check=False)
        return lock_id

    def start(self):
        log = open(os.path.join(self.outdir, f"server-{self.restarts}.log"), "w")
        self.proc = subprocess.Popen(
            [os.path.join(self.args.bins, "omnigraph-server"), "--cluster", self.root, "--bind",
             f"127.0.0.1:{self.args.port}"],
            env=self.env, stdout=log, stderr=subprocess.STDOUT)
        deadline = time.time() + 120
        while time.time() < deadline:
            try:
                status, body = http("GET", f"{self.base()}/readyz", timeout=2)
                if status == 200 and body.get("ready"):
                    return
            except Exception:  # noqa: BLE001
                pass
            if self.proc.poll() is not None:
                raise RuntimeError(f"server exited with {self.proc.returncode}; see server-{self.restarts}.log")
            time.sleep(0.3)
        raise RuntimeError("server did not become ready")

    def base(self):
        return f"http://127.0.0.1:{self.args.port}"

    def kill9(self):
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGKILL)
            self.proc.wait()
        self.restarts += 1

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def seed(server, args):
    g = f"{server.base()}/graphs/{args.graph}"
    for i in range(args.hot // 2):
        status, body = http("POST", f"{g}/mutate", {
            "query": "query ins($slug: String, $n: I64) { insert Item { slug: $slug, n: $n } }",
            "name": "ins", "params": {"slug": f"h{i}", "n": i}})
        assert status == 200, body
    for i in range(args.people // 2):
        status, body = http("POST", f"{g}/mutate", {
            "query": "query person($name: String) { insert Person { name: $name } }",
            "name": "person", "params": {"name": f"p{i}"}})
        assert status == 200, body
    if args.seed_index:
        result = optimize(server, args, wait=True)
        print("[seed] optimize", json.dumps(result)[:600], flush=True)
        assert result["status"] in (200, 202), result


def optimize(server, args, wait=True, max_gate_hold_ms=None):
    g = f"{server.base()}/graphs/{args.graph}"
    op_id = ulid()
    body = {"operation_id": op_id}
    if max_gate_hold_ms is not None:
        body["max_gate_hold_ms"] = max_gate_hold_ms
    started = time.time()
    status, answer = http("POST", f"{g}/operations/optimize", body, timeout=600)
    if status not in (200, 202):
        return {"status": status, "answer": answer, "secs": time.time() - started}
    while wait:
        status, answer = http("GET", f"{g}/operations/{op_id}", timeout=600)
        state = json.dumps(answer)
        if status != 200 or any(word in state for word in ('"completed"', '"failed"', '"cancelled"', '"succeeded"')):
            break
        time.sleep(0.5)
    return {"status": status, "answer": answer, "secs": round(time.time() - started, 2)}


def export(server, args, path):
    status, payload = http("POST", f"{server.base()}/graphs/{args.graph}/export", {}, timeout=600, raw=True)
    assert status == 200, payload
    with open(path, "wb") as f:
        f.write(payload)


def commits(server, args, path):
    status, body = http("GET", f"{server.base()}/graphs/{args.graph}/commits", timeout=600)
    assert status == 200, body
    with open(path, "w") as f:
        json.dump(body, f)
    return body


def main():
    p = argparse.ArgumentParser()
    p.add_argument("label")
    p.add_argument("--backend", choices=["local", "s3"], default="local")
    p.add_argument("--bins", default="/srv/bins/proto-unfixed")
    p.add_argument("--port", type=int, default=18080)
    p.add_argument("--s3-port", type=int, default=19000)
    p.add_argument("--graph", default="stress")
    p.add_argument("--hot", type=int, default=64)
    p.add_argument("--people", type=int, default=24)
    p.add_argument("--seed-index", type=int, default=1)
    p.add_argument("--group-commit", type=int, default=1)
    p.add_argument("--rust-log", default="warn")
    p.add_argument("--server-env", action="append", default=[])
    p.add_argument("--optimize-secs", type=float, default=0)
    p.add_argument("--chaos-kill-secs", type=float, default=0, help="mean seconds between kill -9")
    p.add_argument("--chaos-seed", type=int, default=7)
    p.add_argument("--toxics", default="", help="comma list of reset,timeout,truncate,jitter,bandwidth")
    p.add_argument("--toxic-every", type=float, default=6.0, help="mean seconds between toxic windows")
    p.add_argument("--toxic-window", type=float, default=2.0, help="seconds a toxic stays on")
    p.add_argument("--toxic-toxicity", type=float, default=0.3)
    p.add_argument("--latency-ms", type=int, default=15)
    p.add_argument("--watchdog", type=int, default=0, help="restart the server whenever it dies")
    argv = sys.argv[1:]
    driver_args = []
    if "--" in argv:
        cut = argv.index("--")
        argv, driver_args = argv[:cut], argv[cut + 1:]
    args = p.parse_args(argv)
    args.driver = driver_args
    outdir = os.path.join(ROOT, "research", "group-commit-stress", "served", args.label)
    os.makedirs(outdir, exist_ok=True)
    if args.backend == "local":
        root = f"/srv/stress/roots/{args.label}-{int(time.time())}"
        os.makedirs(root, exist_ok=True)
    else:
        root = f"s3://omnigraph-stress/{args.label}-{int(time.time())}"
    server = Server(args, root, outdir)
    server.bootstrap()
    server.start()
    seed(server, args)
    export(server, args, os.path.join(outdir, "seed-export.jsonl"))
    history = os.path.join(outdir, "history.jsonl")
    if os.path.exists(history):
        os.remove(history)
    driver = args.driver
    cmd = [PY, os.path.join(HERE, "http_stress.py"), "run", "--base", server.base(), "--graph", args.graph,
           "--hot", str(args.hot), "--people", str(args.people), "--out", history,
           "--server-pid", str(server.proc.pid), *driver]
    events = open(os.path.join(outdir, "events.jsonl"), "w")
    stop = threading.Event()

    def event(**kv):
        kv["t"] = round(time.time(), 3)
        events.write(json.dumps(kv) + "\n")
        events.flush()
        print("[event]", json.dumps(kv), flush=True)

    def maintenance():
        while not stop.wait(args.optimize_secs):
            result = optimize(server, args, wait=True)
            event(kind="optimize", status=result["status"], secs=result.get("secs"),
                  answer=json.dumps(result.get("answer"))[:400])

    def chaos():
        rng = random.Random(args.chaos_seed)
        while not stop.wait(rng.expovariate(1.0 / args.chaos_kill_secs)):
            event(kind="kill9", pid=server.proc.pid)
            server.kill9()
            lock = server.unlock_stale()
            event(kind="unlock", lock=lock)
            try:
                server.start()
                event(kind="restarted", pid=server.proc.pid)
            except Exception as error:  # noqa: BLE001
                event(kind="restart_failed", error=str(error)[:300])
                time.sleep(1)
                server.unlock_stale()
                server.start()
                event(kind="restarted", pid=server.proc.pid)

    def toxi(method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(f"http://127.0.0.1:8474{path}", data=data, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status
        except urllib.error.HTTPError as error:
            return error.code
        except Exception:  # noqa: BLE001
            return None

    def toxics():
        rng = random.Random(args.chaos_seed + 1)
        kinds = [kind for kind in args.toxics.split(",") if kind]
        while not stop.wait(rng.expovariate(1.0 / args.toxic_every)):
            kind = rng.choice(kinds)
            stream = rng.choice(["upstream", "downstream"])
            toxic = {"name": "chaos", "stream": stream, "toxicity": args.toxic_toxicity}
            if kind == "reset":
                toxic.update(type="reset_peer", attributes={"timeout": rng.randint(0, 50)})
            elif kind == "timeout":
                toxic.update(type="timeout", attributes={"timeout": rng.randint(100, 3000)})
            elif kind == "truncate":
                # Responses cut short: the store applied the request, the
                # client never sees its answer (a lost acknowledgement).
                toxic.update(type="limit_data", stream="downstream",
                             attributes={"bytes": rng.randint(0, 400)})
            elif kind == "jitter":
                toxic.update(type="latency", toxicity=1.0,
                             attributes={"latency": rng.randint(20, 200), "jitter": rng.randint(10, 300)})
            elif kind == "bandwidth":
                toxic.update(type="bandwidth", toxicity=1.0, attributes={"rate": rng.randint(50, 500)})
            status = toxi("POST", "/proxies/rustfs/toxics", toxic)
            event(kind="toxic_on", toxic=toxic, status=status)
            stop.wait(args.toxic_window)
            toxi("DELETE", "/proxies/rustfs/toxics/chaos")
            event(kind="toxic_off", toxic_kind=kind)

    def watchdog():
        # Restart the server whenever it dies on its own (crash points).
        while not stop.wait(0.2):
            if server.proc.poll() is not None and not stop.is_set():
                event(kind="server_died", code=server.proc.returncode)
                server.restarts += 1
                lock = server.unlock_stale()
                try:
                    server.start()
                    event(kind="restarted", pid=server.proc.pid, lock=lock)
                except Exception as error:  # noqa: BLE001
                    event(kind="restart_failed", error=str(error)[:300])

    threads = []
    if args.watchdog:
        threads.append(threading.Thread(target=watchdog, daemon=True))
    if args.toxics and args.backend == "s3":
        threads.append(threading.Thread(target=toxics, daemon=True))
    if args.optimize_secs > 0:
        threads.append(threading.Thread(target=maintenance, daemon=True))
    if args.chaos_kill_secs > 0:
        threads.append(threading.Thread(target=chaos, daemon=True))
    for thread in threads:
        thread.start()
    event(kind="driver_start", cmd=cmd[2:], root=root)
    driver_proc = subprocess.run(cmd)
    stop.set()
    for thread in threads:
        thread.join(timeout=900)
    event(kind="driver_done", code=driver_proc.returncode)
    if server.proc.poll() is not None:
        server.unlock_stale()
        server.start()
    export(server, args, os.path.join(outdir, "final-export.jsonl"))
    commits(server, args, os.path.join(outdir, "commits.json"))
    server.stop()
    check = subprocess.run([PY, os.path.join(HERE, "http_stress.py"), "check", "--history", history,
                            "--export", os.path.join(outdir, "final-export.jsonl"),
                            "--seed-export", os.path.join(outdir, "seed-export.jsonl"),
                            "--commits", os.path.join(outdir, "commits.json")],
                           capture_output=True, text=True)
    with open(os.path.join(outdir, "check.txt"), "w") as f:
        f.write(check.stdout + check.stderr)
    print(check.stdout[-6000:], check.stderr[-3000:])
    event(kind="check_done", code=check.returncode, root=root)
    sys.exit(check.returncode)


if __name__ == "__main__":
    main()
