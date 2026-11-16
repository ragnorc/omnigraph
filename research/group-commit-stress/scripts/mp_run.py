#!/usr/bin/env python3
"""Several embedded writer processes against one standalone graph root.

1. `omnigraph init` + seed load + `index build` on a fresh local root.
2. P processes of the harness binary (`gc_stress_harness` with `OG_GS_URI`),
   each with its own key prefix and history file; optionally one of them
   claims main's writer epoch mid-load, or a process is killed -9 mid-run.
3. `omnigraph export` and `commit list --json`, then the Python checker over
   every history together (receipts, lineage, duplicates, dangling edges,
   model replay).
"""
import argparse
import json
import os
import random
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
PY = "/srv/venv/bin/python"


def cli(bins, *argv, out=None):
    result = subprocess.run([os.path.join(bins, "omnigraph"), *argv], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"omnigraph {' '.join(argv)}: {result.stderr[-1500:]}")
    if out:
        with open(out, "w") as f:
            f.write(result.stdout)
    return result.stdout


def main():
    p = argparse.ArgumentParser()
    p.add_argument("label")
    p.add_argument("--bins", default="/srv/bins/proto-fixed")
    p.add_argument("--harness", default=open("/tmp/gs_bin").read().strip())
    p.add_argument("--procs", type=int, default=4)
    p.add_argument("--writers", type=int, default=8)
    p.add_argument("--secs", type=int, default=30)
    p.add_argument("--mix", default="fresh=60,hot=15,pair=5,update=5,delete=5,person=3,del_person=1,knows=5,multi=1")
    p.add_argument("--claim-at", type=int, default=0, help="process 0 claims main at this second")
    p.add_argument("--kill-at", type=float, default=0, help="kill -9 process 1 at this second, restart it")
    p.add_argument("--group-commit", default="1")
    args = p.parse_args()
    out = os.path.join(ROOT, "research", "group-commit-stress", "mp", args.label)
    os.makedirs(out, exist_ok=True)
    root = f"/srv/stress/mp/{args.label}-{int(time.time())}"
    os.makedirs(os.path.dirname(root), exist_ok=True)
    schema = os.path.join(HERE, "..", "cluster", "stress.pg")
    cli(args.bins, "init", "--schema", schema, root)
    seed = os.path.join(out, "seed.jsonl")
    with open(seed, "w") as f:
        for i in range(32):
            f.write(json.dumps({"type": "Item", "data": {"slug": f"h{i}", "n": i}}) + "\n")
        for i in range(12):
            f.write(json.dumps({"type": "Person", "data": {"name": f"p{i}"}}) + "\n")
    cli(args.bins, "load", "--data", seed, "--mode", "overwrite", "--target", root)
    cli(args.bins, "index", "build", "--target", root)
    cli(args.bins, "export", "--target", root, out=os.path.join(out, "seed-export.jsonl"))
    procs = []

    def spawn(i, secs, suffix=""):
        env = dict(os.environ)
        env.update({
            "RUST_MIN_STACK": "16777216", "OMNIGRAPH_GROUP_COMMIT": args.group_commit,
            "OG_GS_GROUP_COMMIT": args.group_commit,
            "OG_GS_URI": root, "OG_GS_TAG": f"p{i}{suffix}-", "OG_GS_WRITERS": str(args.writers),
            "OG_GS_SECS": str(secs), "OG_GS_SEED": str(100 + i), "OG_GS_READERS": "0",
            "OG_GS_RYW_PCT": "0", "OG_GS_MIX": args.mix, "OG_GS_HOT": "64", "OG_GS_PEOPLE": "24",
            "OG_GS_OUT": os.path.join(out, f"history-{i}{suffix}.jsonl"),
        })
        if i == 0 and args.claim_at:
            env["OG_GS_CLAIM_AT_SECS"] = str(args.claim_at)
        log = open(os.path.join(out, f"proc-{i}{suffix}.log"), "w")
        return subprocess.Popen([args.harness, "--ignored", "--nocapture", "--exact", "gc_stress_harness"],
                                cwd=os.path.join(ROOT, "crates", "omnigraph"), env=env, stdout=log,
                                stderr=subprocess.STDOUT)

    for i in range(args.procs):
        procs.append(spawn(i, args.secs))
    events = []
    if args.kill_at:
        time.sleep(args.kill_at)
        procs[1].send_signal(signal.SIGKILL)
        procs[1].wait()
        events.append(f"killed process 1 at {args.kill_at}s")
        # A replacement process with another prefix finishes the window.
        procs.append(spawn(1, max(5, int(args.secs - args.kill_at)), suffix="r"))
    codes = [proc.wait() for proc in procs]
    events.append(f"exit codes {codes}")
    cli(args.bins, "export", "--target", root, out=os.path.join(out, "final-export.jsonl"))
    commits_text = cli(args.bins, "commit", "list", "--target", root, "--json")
    commits = json.loads(commits_text)
    if isinstance(commits, list):
        commits = {"commits": commits}
    with open(os.path.join(out, "commits.json"), "w") as f:
        json.dump(commits, f)
    histories = sorted(os.path.join(out, name) for name in os.listdir(out) if name.startswith("history-"))
    # A killed process wrote no history: its in-flight writes are in doubt
    # and unknown to the checker, so its keys are excluded by prefix below.
    check = subprocess.run([PY, os.path.join(HERE, "http_stress.py"), "check", "--history", *histories,
                            "--export", os.path.join(out, "final-export.jsonl"),
                            "--seed-export", os.path.join(out, "seed-export.jsonl"),
                            "--commits", os.path.join(out, "commits.json"),
                            *(["--exclude-prefix", "fp1-", "--exclude-prefix", "h", "--exclude-prefix", "p",
                               "--exclude-prefix", "tp1-"] if args.kill_at else [])],
                           capture_output=True, text=True)
    with open(os.path.join(out, "check.txt"), "w") as f:
        f.write("\n".join(events) + "\n" + check.stdout + check.stderr)
    print("\n".join(events))
    print(check.stdout[-5000:], check.stderr[-2000:])
    sys.exit(check.returncode)


if __name__ == "__main__":
    main()
