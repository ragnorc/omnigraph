#!/usr/bin/env python3
"""Campaign 3b: random SIGKILL / SIGTERM of a loaded server, many rounds.

Each round starts the server on the same root, runs writers (loads with
fresh keys) and optimize submitters for a random 0.5-6 s, then kills the
server with SIGKILL (70%) or SIGTERM (30%) at that random moment, and
restarts it (force-unlocking when the previous owner kept the lock). After
every restart it checks: every acknowledged row is present; every operation a
client ever saw resolves (terminal, or `publishing` until a write on main) and
a terminal state is never rewritten; a run reported failed/cancelled/
interrupted published nothing; at most one commit answers any nonce; the
record's bounds hold; the eviction mark never moves back.
Usage: c3_random_kills.py OUTDIR [ROUNDS] [SEED]
"""
import json, os, random, shutil, signal, sys, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import og, invariants

out = Path(sys.argv[1]); shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 40
random.seed(int(sys.argv[3]) if len(sys.argv) > 3 else 5)
log = og.Log(str(out / "log.txt"))
root = og.make_root(out, "kills"); og.apply_cluster(root)
env = {"OMNIGRAPH_OPTIMIZE_GATE_HOLD_SECONDS": "2", "OMNIGRAPH_OPTIMIZE_MAX_GATE_HOLD_SECONDS": "5",
       "OMNIGRAPH_SHUTDOWN_GRACE_SECONDS": "5", "OMNIGRAPH_OPERATION_RESOLUTION_WALK_LIMIT": "3"}
acked, seen, nonces, violations = set(), {}, {}, []
last_mark = None
port = 13800


def violate(msg):
    violations.append(msg)
    log("VIOLATION", msg)


def see(op):
    if not op:
        return
    oid = op["operation_id"]
    prev = seen.get(oid)
    if prev and prev["state"] in og.TERMINAL and prev["state"] != op["state"]:
        violate(f"{oid}: terminal {prev['state']} rewritten to {op['state']}")
    seen[oid] = op
    if op.get("nonce"):
        nonces[oid] = op["nonce"]


SEAMS = os.environ.get("CHAOS_SEAMS") == "1"
SEAM_NAMES = ["optimize.before_compact", "optimize.post_table_effect", "optimize.post_phase_b_pre_manifest_commit",
              "optimize.configuration_clear", "graph_publish.after_manifest_commit", "graph_publish.before_commit_append",
              "publish.pre_merge", "publish.post_merge_pre_ack", "publish.read_back",
              "mutation.post_finalize_pre_publisher"]
armed_log = []


def make_server(rnd, suffix=""):
    if SEAMS:
        from c3_crash import Child
        ctl = out / f"ctl-{rnd}{suffix}"
        ctl.mkdir(exist_ok=True)
        srv = Child(root, port, str(out / f"server-{rnd}{suffix}.log"), ctl, env)
        srv.ctl = ctl
        return srv
    return og.Server(root, port, str(out / f"server-{rnd}{suffix}.log"), env)


for rnd in range(rounds):
    port += 1
    srv = make_server(rnd)
    try:
        srv.start(timeout=60)
    except (RuntimeError, TimeoutError):
        unlocked = og.unlock(root)
        log("round", rnd, "restart needed force-unlock", unlocked)
        srv = make_server(rnd, "b").start(timeout=60)
    h = srv.http
    if SEAMS:
        from c3_crash import arm
        def arm_random():
            time.sleep(random.uniform(0, 2))
            for _ in range(random.randint(1, 3)):
                seam = random.choice(SEAM_NAMES)
                n = random.randint(1, 4)
                try:
                    arm(srv.ctl, seam, "fail_always" if seam == "publish.read_back" else "fail_once", n)
                    armed_log.append((rnd, seam, n))
                except Exception:  # noqa: BLE001
                    return
        threading.Thread(target=arm_random, daemon=True).start()
    # resolve what earlier rounds left
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
    present = og.keys_of(h, "kg", "Person", "name")
    missing = acked - present
    if missing:
        violate(f"round {rnd}: {len(missing)} acknowledged rows missing, e.g. {sorted(missing)[:3]}")
    stop = threading.Event()

    def writer(w):
        i = 0
        while not stop.is_set():
            i += 1
            rows = [f"r{rnd}w{w}i{i}k{k}" for k in range(random.randint(1, 15))]
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
                s, b, _ = h.submit("kg", oid, hold_ms=random.choice([None, 0, 200, 2000]))
                if s in (200, 202):
                    see(b["operation"])
                elif s == 500:
                    seen.setdefault(oid, {"operation_id": oid, "state": "unknown-500"})
                for _ in range(5):
                    if stop.is_set():
                        break
                    s, b, _ = h.lookup("kg", oid)
                    if s == 200 and b.get("operation"):
                        see(b["operation"])
                        if b["operation"]["state"] in og.TERMINAL:
                            break
                    time.sleep(0.1)
            except OSError:
                return

    ths = [threading.Thread(target=writer, args=(w,), daemon=True) for w in range(3)] + \
          [threading.Thread(target=submitter, daemon=True) for _ in range(2)]
    for t in ths:
        t.start()
    time.sleep(random.uniform(0.5, 6))
    sig = signal.SIGKILL if random.random() < 0.7 else signal.SIGTERM
    t0 = time.time()
    code = srv.kill(sig, wait=60)
    stop.set()
    for t in ths:
        t.join(10)
    log("round", rnd, "killed", sig.name, "exit", code, f"{time.time()-t0:.1f}s", "ops", len(seen), "acked", len(acked))

# final settle: one more server, a write on main, and every seen op resolved
port += 1
srv = og.Server(root, port, str(out / "server-final.log"), env)
try:
    srv.start(timeout=60)
except (RuntimeError, TimeoutError):
    og.unlock(root)
    srv = og.Server(root, port, str(out / "server-final-b.log"), env).start(timeout=60)
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
    key = op["state"] if op else "unknown"
    states[key] = states.get(key, 0) + 1
    if op and op["state"] in ("accepted", "running"):
        violate(f"{oid} still {op['state']} with no live run after final restart")
rec = og.read_record(root)
violations += [f"final: {v}" for v in invariants.check_record_shape(rec, live_ids=set())]
violations += [f"final ops: {v}" for v in invariants.check_operations(h, "kg", seen, nonces, rec)]
missing = acked - og.keys_of(h, "kg", "Person", "name")
if missing:
    violate(f"final: {len(missing)} acknowledged rows missing")
srv.kill(signal.SIGTERM)
summary = {"rounds": rounds, "ops_seen": len(seen), "acked_rows": len(acked), "final_states": states,
           "seams_armed": len(armed_log), "violations": violations}
log("SUMMARY", json.dumps(summary))
(out / "summary.json").write_text(json.dumps(summary, indent=1))
