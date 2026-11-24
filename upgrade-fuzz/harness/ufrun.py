"""Run fuzz cases in parallel, a seed range at a time, resumable.

    python ufrun.py <seed-from> <seed-to> [--jobs N] [--budget SECS] [--stress]

Every finished case appends one line to `$UF_WORK/results/cases.jsonl`; a
seed already there is skipped, so a range split over several invocations
(each under the 10-minute command limit) resumes at its saved position. No
new case starts once less than `UF_CASE_BUDGET` seconds of the invocation's
budget remain. A case's work directory is removed unless it found something.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import shutil
import sys
import time
from pathlib import Path

import ufcommon as C


def done_seeds(path: Path) -> set:
    s = set()
    if path.exists():
        for line in path.open():
            try:
                s.add(json.loads(line)["seed"])
            except Exception:
                pass
    return s


def one(args):
    seed, stress = args
    import ufcase
    rec = ufcase.Case(seed, stress=stress).run()
    root = C.WORK / "cases" / f"s{seed}"
    if not rec["findings"] and rec["status"] == "ok" and os.environ.get("UF_KEEP") != "1":
        shutil.rmtree(root, ignore_errors=True)
    rec.pop("events", None) if not rec["findings"] else None
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("lo", type=int)
    ap.add_argument("hi", type=int)
    ap.add_argument("--jobs", type=int, default=24)
    ap.add_argument("--budget", type=float, default=540)
    ap.add_argument("--stress", action="store_true")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    out = Path(a.out or (C.WORK / "results" / ("stress.jsonl" if a.stress else "cases.jsonl")))
    out.parent.mkdir(parents=True, exist_ok=True)
    done = done_seeds(out)
    todo = [s for s in range(a.lo, a.hi) if s not in done]
    t0 = time.time()
    case_budget = float(os.environ.get("UF_CASE_BUDGET", "420"))
    n_ok = n_f = 0
    with cf.ProcessPoolExecutor(max_workers=a.jobs) as ex:
        futs = {}
        it = iter(todo)
        def submit():
            if time.time() - t0 > a.budget - case_budget * 0.75:
                return False
            s = next(it, None)
            if s is None:
                return False
            futs[ex.submit(one, (s, a.stress))] = s
            return True
        for _ in range(a.jobs):
            if not submit():
                break
        while futs:
            fin, _ = cf.wait(list(futs), timeout=5, return_when=cf.FIRST_COMPLETED)
            for f in fin:
                s = futs.pop(f)
                try:
                    rec = f.result()
                except Exception as e:
                    rec = {"seed": s, "status": "worker_error", "error": str(e)[-2000:], "findings": []}
                with out.open("a") as fh:
                    fh.write(json.dumps(rec, default=str) + "\n")
                n_ok += 1
                n_f += len(rec.get("findings") or [])
                print(f"seed {s} {rec['status']} {rec.get('secs')}s findings={len(rec.get('findings') or [])}", flush=True)
                submit()
    left = [s for s in todo if s not in done_seeds(out)]
    print(json.dumps({"ran": n_ok, "findings": n_f, "left": len(left), "next": left[0] if left else None,
                      "secs": round(time.time() - t0)}))


if __name__ == "__main__":
    main()
