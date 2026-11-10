#!/usr/bin/env python3
"""Round 2, campaign 2: per-write wait beside back-to-back served optimizes,
with the writer yield (RFC 1.2, "A writer that prepares again adds its own
preparation, and no further runs").

Same shape as round 1's `c1_writer_wait.py` (so the numbers compare): phases
control / optimize / control2, `WRITERS` writers loading five-row
Person/Company batches on main, one submitter that submits the next
full-catalog optimize as soon as the previous one ends (`max_gate_hold_ms`
1000). Additions:

- every run's `writer_yield_ms`, `gate_hold_ms`, `queued_ms`, `compute_ms`;
- the instant of every optimize publication, from main's commit list
  (`created_at` of the run's `published_commit`); per write, how many
  optimize publications landed between its first attempt and its
  acknowledgement (`pubs_inside`): a write the yield protects sees at most
  one (the one that sent it back), two when it raced (it was queued behind
  another writer when the next run passed its yield);
- the round-1 metric (gate-hold intervals overlapping the write), with each
  hold placed to end at its publication;
- `POLLERS` threads looking up the live operation every `POLL_MS`;
- `BRANCH_WRITERS` writers loading on branch `side` (the yield is main-only);
- `MUTATORS` writers inserting one Person by mutation on main;
- re-prepares per phase, from the server's `omnigraph::loader` debug lines.

Usage: c9_writer_yield.py OUTDIR [SECONDS] [WRITERS]
Env: POLLERS (0), POLL_MS (100), BRANCH_WRITERS (0), MUTATORS (0),
     STRESS_HOLD_MS (1000), STRESS_PORT, PHASES (control,optimize,control2)
"""
import datetime, json, os, shutil, signal, sys, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import og, invariants

out = Path(sys.argv[1]); shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 120
nwriters = int(sys.argv[3]) if len(sys.argv) > 3 else 2
POLLERS = int(os.environ.get("POLLERS", "0"))
POLL_MS = int(os.environ.get("POLL_MS", "100"))
BRANCH_WRITERS = int(os.environ.get("BRANCH_WRITERS", "0"))
MUTATORS = int(os.environ.get("MUTATORS", "0"))
HOLD_MS = int(os.environ.get("STRESS_HOLD_MS", "1000"))
PHASES = os.environ.get("PHASES", "control,optimize,control2").split(",")
root = og.make_root(out, "wy"); og.apply_cluster(root)
env = {"OMNIGRAPH_OPTIMIZE_GATE_HOLD_SECONDS": str(max(1, HOLD_MS // 1000)),
       "OMNIGRAPH_OPTIMIZE_MAX_GATE_HOLD_SECONDS": "5",
       "RUST_LOG": os.environ.get("RUST_LOG", "info,omnigraph::loader=debug,omnigraph::exec=debug")}
srv = og.Server(root, int(os.environ.get("STRESS_PORT", "13950")), str(out / "server.log"), env).start(timeout=120)
h = srv.http
if BRANCH_WRITERS:
    s, b, _ = h.branch_create("kg", "side")
    assert s in (200, 201), (s, b)


def ts(text):
    text = text.replace("Z", "+00:00")
    base, _, tz = text.partition("+")
    if "." in base:
        head, frac = base.split(".")
        base = head + "." + frac[:6]
    return datetime.datetime.fromisoformat(base + "+00:00").timestamp()


def log_size():
    return os.path.getsize(out / "server.log")


def count_reprepares(start, end):
    with open(out / "server.log", "rb") as f:
        f.seek(start)
        chunk = f.read(end - start).decode(errors="replace")
    loads = chunk.count("prepared load authority changed before effects; repreparing")
    other = chunk.count("repreparing") - loads
    return loads, other


acked = {"Person": set(), "Company": set()}
acked_lock = threading.Lock()


def phase(name, optimize):
    stop = threading.Event()
    writes, runs, polls = [], [], []
    live = {"id": None}

    def writer(w, branch=None):
        i = 0
        while not stop.is_set():
            i += 1
            typ = "Person" if i % 2 else "Company"
            names = [f"{name}{'b' if branch else ''}w{w}i{i}k{k}" for k in range(5)]
            rows = [{"type": typ, "data": {"name": n, **({"bio": "x"} if typ == "Person" else {})}} for n in names]
            t0 = time.time()
            s, b, _ = h.load("kg", "\n".join(json.dumps(r) for r in rows), branch=branch)
            t1 = time.time()
            writes.append((t0, t1, typ if not branch else f"{typ}@side", s, (b or {}).get("error") if s != 200 and isinstance(b, dict) else None))
            if s == 200 and not branch:
                with acked_lock:
                    acked[typ].update(names)

    def mutator(w):
        i = 0
        q = "query ins($n: String) { insert Person { name: $n, bio: \"m\" } }"
        while not stop.is_set():
            i += 1
            n = f"{name}m{w}i{i}"
            t0 = time.time()
            s, b, _ = h.mutate("kg", q, {"n": n})
            t1 = time.time()
            writes.append((t0, t1, "mutate", s, (b or {}).get("error") if s != 200 and isinstance(b, dict) else None))
            if s == 200:
                with acked_lock:
                    acked["Person"].add(n)

    def submitter():
        while not stop.is_set():
            oid = og.ulid()
            s, b, _ = h.submit("kg", oid, hold_ms=HOLD_MS)
            if s not in (200, 202):
                time.sleep(0.05)
                continue
            live["id"] = oid
            while True:
                s, b, _ = h.lookup("kg", oid)
                op = (b or {}).get("operation") if s == 200 else None
                if op and op["state"] in og.TERMINAL:
                    runs.append(op)
                    break
                time.sleep(0.02)

    def poller():
        while not stop.is_set():
            oid = live["id"]
            if oid:
                t0 = time.time()
                s, _, _ = h.lookup("kg", oid)
                polls.append((t0, time.time(), s))
            time.sleep(POLL_MS / 1000)

    ths = [threading.Thread(target=writer, args=(w,), daemon=True) for w in range(nwriters)]
    ths += [threading.Thread(target=writer, args=(100 + w, "side"), daemon=True) for w in range(BRANCH_WRITERS)]
    ths += [threading.Thread(target=mutator, args=(w,), daemon=True) for w in range(MUTATORS)]
    if optimize:
        ths.append(threading.Thread(target=submitter, daemon=True))
        ths += [threading.Thread(target=poller, daemon=True) for _ in range(POLLERS)]
    start_log = log_size()
    for t in ths:
        t.start()
    time.sleep(seconds)
    stop.set()
    for t in ths:
        t.join(180)
    return writes, runs, polls, (start_log, log_size())


def pct(xs, p):
    return round(xs[min(len(xs) - 1, int(len(xs) * p))], 3) if xs else None


res = {"config": {"seconds": seconds, "writers": nwriters, "pollers": POLLERS, "poll_ms": POLL_MS,
                  "branch_writers": BRANCH_WRITERS, "mutators": MUTATORS, "hold_ms": HOLD_MS, "root": root}}
for name in PHASES:
    optimize = name.startswith("optimize")
    writes, runs, polls, (l0, l1) = phase(name, optimize)
    commits = {c["graph_commit_id"]: c for c in invariants.main_history(h, "kg")}
    pubs = []  # (publish instant, gate_hold s, op)
    holds = []
    for op in runs:
        t = op.get("timings") or {}
        pc = op.get("published_commit")
        if pc and pc in commits and commits[pc].get("created_at"):
            pt = ts(commits[pc]["created_at"])
            pubs.append((pt, (t.get("gate_hold_ms") or 0) / 1000, op["operation_id"]))
            holds.append((pt - (t.get("gate_hold_ms") or 0) / 1000, pt))
        elif t.get("gate_hold_ms") is not None:
            st = ts(op["accepted_at"]) + (t.get("queued_ms", 0) + t.get("writer_yield_ms", 0)) / 1000
            holds.append((st, st + t["gate_hold_ms"] / 1000))
    rows = []
    for t0, t1, typ, s, err in writes:
        if s != 200:
            continue
        inside = sum(1 for p in pubs if t0 < p[0] < t1)
        over = [hd for hd in holds if hd[0] < t1 and hd[1] > t0]
        held = sum(min(t1, hd[1]) - max(t0, hd[0]) for hd in over)
        rows.append((t1 - t0, inside, len(over), held, typ))
    main_rows = [r for r in rows if not r[4].endswith("@side")]
    side_rows = [r for r in rows if r[4].endswith("@side")]
    lat = sorted(r[0] for r in main_rows)
    side_lat = sorted(r[0] for r in side_rows)
    yields = sorted((op.get("timings") or {}).get("writer_yield_ms") or 0 for op in runs)
    gh = sorted((op.get("timings") or {}).get("gate_hold_ms") or 0 for op in runs)
    states = {}
    for op in runs:
        states[op["state"]] = states.get(op["state"], 0) + 1
    others = {}
    for *_, s, err in writes:
        if s != 200:
            k = f"{s}:{(err or '')[:80]}"
            others[k] = others.get(k, 0) + 1
    loads_rep, other_rep = count_reprepares(l0, l1)
    hist = {}
    for r in main_rows:
        hist[r[1]] = hist.get(r[1], 0) + 1
    r = {
        "writes_ok_main": len(main_rows), "writes_ok_side": len(side_rows), "writes_other": others,
        "throughput_main_per_s": round(len(main_rows) / seconds, 2),
        "p50": pct(lat, 0.5), "p90": pct(lat, 0.9), "p99": pct(lat, 0.99), "max": lat[-1] if lat else None,
        "side_p99": pct(side_lat, 0.99), "side_max": side_lat[-1] if side_lat else None,
        "runs": len(runs), "run_states": states, "publications": len(pubs),
        "writer_yield_ms": {"p50": pct(yields, 0.5), "p90": pct(yields, 0.9), "max": yields[-1] if yields else None,
                            "at_limit(>=9900)": sum(1 for y in yields if y >= 9900), "zero": sum(1 for y in yields if y == 0)},
        "gate_hold_ms": {"p50": pct(gh, 0.5), "max": gh[-1] if gh else None},
        "gate_hold_fraction": round(sum(b - a for a, b in holds) / seconds, 3),
        "writes_by_publications_inside": dict(sorted(hist.items())),
        "writes_with_2plus_publications": sum(1 for r in main_rows if r[1] >= 2),
        "writes_with_3plus_publications": sum(1 for r in main_rows if r[1] >= 3),
        "writes_spanning_2plus_holds": sum(1 for r in main_rows if r[2] >= 2),
        "writes_spanning_3plus_holds": sum(1 for r in main_rows if r[2] >= 3),
        "slowest (latency, pubs inside, holds overlapped, held s inside, kind)":
            [(round(a, 2), b, c, round(d, 2), e) for a, b, c, d, e in sorted(rows, reverse=True)[:8]],
        "reprepares_load": loads_rep, "reprepares_other": other_rep,
        "polls": len(polls), "poll_p99": pct(sorted(b - a for a, b, _ in polls), 0.99),
        "poll_statuses": sorted({s for *_, s in polls}),
    }
    res[name] = r
    print(name, json.dumps(r), flush=True)
    (out / f"{name}.json").write_text(json.dumps({"writes": writes, "pubs": pubs, "holds": holds,
                                                 "runs": runs}, default=str))
# invariant: every acknowledged row is on main
missing = {}
for typ, names in acked.items():
    have = og.keys_of(h, "kg", typ, "name")
    miss = names - have
    if miss:
        missing[typ] = sorted(miss)[:20] + [f"... {len(miss)} total"]
res["acked_rows_missing"] = missing
rec = og.read_record(root)
res["record_violations"] = invariants.check_record_shape(rec)
res["sigterm_exit"] = srv.kill(signal.SIGTERM)
(out / "result.json").write_text(json.dumps(res, indent=1, default=str))
print("acked missing", missing, "record violations", res["record_violations"], "exit", res["sigterm_exit"])
