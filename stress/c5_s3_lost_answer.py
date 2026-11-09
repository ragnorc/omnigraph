#!/usr/bin/env python3
"""Campaign 5d: a genuinely in-doubt operation-record write on S3 (RustFS).

The fault proxy (`s3proxy.py`) sits between the server and RustFS. Right
after a run's `publishing` record write, the next record PUTs are answered
with a connection reset:

  late    each copy is forwarded 4 s later: the write lands after the
          server's re-issues and read-back gave up (a lost answer that lands)
  never   each copy is dropped: nothing lands (a lost answer that never lands)

Then: lookups of the run, a fresh submission (repeated for 20 s), SIGTERM's
exit, and whether the next server starts and admits.
Usage: c5_s3_lost_answer.py OUTDIR [late|never ...]
"""
import json, os, shutil, signal, subprocess, sys, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("STRESS_S3_PREFIX", "s3://omnigraph-stress/lost")
import og

PROXY = 14990


def run(out, mode, after=3):
    d = out / f"{mode}-{after}"; shutil.rmtree(d, ignore_errors=True); d.mkdir(parents=True)
    log = og.Log(str(d / "log.txt"))
    root = og.make_root(d, mode); og.apply_cluster(root)
    key = root[len("s3://"):]
    ctl = d / "proxy"; ctl.mkdir()
    proxy = subprocess.Popen([sys.executable, str(Path(__file__).parent / "s3proxy.py"), str(PROXY), "9000", str(ctl)],
                             stdout=subprocess.PIPE, text=True)
    proxy.stdout.readline()
    env = {"AWS_ENDPOINT_URL": f"http://127.0.0.1:{PROXY}", "AWS_ENDPOINT_URL_S3": f"http://127.0.0.1:{PROXY}"}
    srv = og.Server(root, 14991, str(d / "server.log"), env).start()
    h = srv.http
    res = {"mode": mode, "write": ["acceptance", "running", "publishing", "final"][after]}
    try:
        for i in range(3):
            h.load("kg", json.dumps({"type": "Person", "data": {"name": f"s{i}", "bio": "b"}}))
        warm = og.ulid(); h.submit("kg", warm); h.wait_terminal("kg", warm)
        for i in range(3):
            h.load("kg", json.dumps({"type": "Person", "data": {"name": f"t{i}", "bio": "b"}}))
        rec_put = f"PUT /{key}/__cluster/operations.json"
        def puts():
            p = ctl / "log"
            return [l for l in p.read_text().splitlines() if rec_put in l and "LATE" not in l] if p.exists() else []
        base = len(puts())
        def arm():
            while len(puts()) < base + after:  # 0: acceptance, 1: running, 2: publishing, 3: final
                time.sleep(0.001)
            (ctl / "match").write_text(rec_put)
            (ctl / ("late_forward" if mode == "late" else "drop_request")).write_text("")
            (ctl / "delay_ms").write_text("4000")
            res["armed_after"] = len(puts()) - base
        t = threading.Thread(target=arm, daemon=True); t.start()
        oid = og.ulid(); s0, b0, _ = h.submit("kg", oid)
        res["submit"] = (s0, (b0 or {}).get("code"), ((b0 or {}).get("operation") or {}).get("state"))
        t.join(60)
        time.sleep(float(os.environ.get("ARM_SECONDS", "1.5")))
        for f in ("late_forward", "drop_request", "match"):
            (ctl / f).unlink(missing_ok=True)
        res["dropped_record_puts"] = sum(1 for l in (ctl / "log").read_text().splitlines() if rec_put in l and "fault=" in l and "fault=None" not in l)
        time.sleep(5)  # late copies land now
        res["late_landings"] = [l.split(" -> ")[-1] for l in (ctl / "log").read_text().splitlines() if l.split(" ", 1)[1].startswith("LATE")]
        looks = []
        for _ in range(3):
            s, b, _ = h.lookup("kg", oid)
            looks.append((s, (b.get("operation") or {}).get("state") if s == 200 and b.get("operation") else (b or {}).get("error", "")[:200]))
            time.sleep(0.5)
        res["lookups"] = looks
        subs = []
        t_end = time.time() + 20
        while time.time() < t_end:
            s, b, _ = h.submit("kg", og.ulid())
            subs.append(s)
            if s in (200, 202):
                break
            time.sleep(2)
        res["fresh_submissions"] = subs
        res["fresh_error"] = (b or {}).get("error", "")[:300] if subs and subs[-1] >= 400 else None
        s, b, _ = h.load("kg", json.dumps({"type": "Company", "data": {"name": "after"}}))
        res["graph_write_after"] = s
        res["sigterm_exit"] = srv.kill(signal.SIGTERM)
        t0 = time.time()
        srv2 = og.Server(root, 14991, str(d / "server2.log"), env)
        try:
            srv2.start(timeout=120)
            res["restart"] = f"started after {time.time() - t0:.1f}s"
        except (RuntimeError, TimeoutError) as e:
            res["restart"] = f"refused: {str(e)[:120]}"
            res["unlocked"] = og.unlock(root)
            srv2 = og.Server(root, 14991, str(d / "server3.log"), env).start(timeout=120)
        s, b, _ = srv2.http.lookup("kg", oid)
        res["lookup_after_restart"] = (s, (b.get("operation") or {}).get("state"))
        s, b, _ = srv2.http.submit("kg", og.ulid())
        res["submit_after_restart"] = s
        srv2.kill(signal.SIGTERM)
    finally:
        srv.kill(signal.SIGKILL)
        proxy.kill()
    log("RESULT", json.dumps(res, default=str))
    return res


if __name__ == "__main__":
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    for spec in sys.argv[2:] or ["late:3", "never:3", "late:0", "never:0", "late:1", "late:2", "never:2"]:
        mode, after = spec.split(":")
        print(json.dumps(run(out, mode, int(after)), default=str), flush=True)
