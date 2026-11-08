#!/usr/bin/env python3
"""The GQ 3 competence instrument (spike/gq3-instrument).

For one edition and one run: every task is one independent headless
`claude -p` call (no session is resumed) with the edition's card and the
task; the answer is parsed by that edition's parser through the checker
(`cargo run -p omnigraph-compiler --example gq3_instrument`); a refused
answer gets one repair turn, again a fresh call, carrying the card, the task,
the answer and the diagnostic's full text.

    python3 docs/spikes/gq3-instrument/run.py --edition 3 --run 1 [--only t01,m3] [--workers 6]

Writes docs/spikes/gq3-instrument/runs/e<edition>-r<run>/<task>.json.
Needs a logged-in `claude`; with CLAUDE_CODE_OAUTH_TOKEN absent from the
environment it reads the parent Claude Code process's token
(`/proc/$CLAUDE_PID/environ`) and passes it to the child only.
"""
import argparse
import concurrent.futures as cf
import json
import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
CHECKER = os.path.join(ROOT, "target", "debug", "examples", "gq3_instrument")
SCHEMA2 = os.path.join(HERE, "schema2.pg")
MODEL = "sonnet"

FIRST = """You are writing a query in GQ, the query language of the Omnigraph graph database. The reference card below is the only GQ documentation available; follow it exactly.

<reference_card>
{card}
</reference_card>

Task: {task}

{answer_rule}"""

REPAIR = """You are writing a query in GQ, the query language of the Omnigraph graph database. The reference card below is the only GQ documentation available; follow it exactly.

<reference_card>
{card}
</reference_card>

Task: {task}

Your previous answer was:

```gq
{text}
```

The GQ compiler refused it with this diagnostic:

```
{diagnostic}
```

{answer_rule}"""

ONE = "Reply with exactly one GQ query in a single fenced ```gq code block and nothing else: no explanation before or after it."
COMPOSITION = ("Reply with GQ only, in fenced ```gq code blocks and nothing else. Answer with a single query if the "
               "language can express the task; if it cannot, write the fewest queries that together answer it, each in "
               "its own block in the order they run, with a `//` comment saying what the caller does with the results.")
ONE_REPAIR = "Reply with the corrected query in a single fenced ```gq code block and nothing else."
COMPOSITION_REPAIR = "Reply with the corrected GQ only, in fenced ```gq code blocks and nothing else."

EDITION_LINE = re.compile(r"^[ \t]*gq[ \t]+\d+[ \t]*;[ \t]*$", re.M)
FENCE = re.compile(r"```[ \t]*([A-Za-z0-9_-]*)[ \t]*\n(.*?)```", re.S)


def token():
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return os.environ["CLAUDE_CODE_OAUTH_TOKEN"]
    pid = os.environ.get("CLAUDE_PID")
    if pid:
        with open(f"/proc/{pid}/environ", "rb") as f:
            for kv in f.read().split(b"\0"):
                if kv.startswith(b"CLAUDE_CODE_OAUTH_TOKEN="):
                    return kv.split(b"=", 1)[1].decode()
    return None


def call_claude(prompt, workdir):
    env = {k: v for k, v in os.environ.items()
           if k not in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION", "CLAUDECODE")}
    tok = token()
    if tok:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = tok
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--model", MODEL, "--tools", "",
           "--safe-mode", "--no-session-persistence"]
    last = None
    for attempt in range(3):
        t0 = time.time()
        p = subprocess.run(cmd, cwd=workdir, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           timeout=600)
        try:
            out = json.loads(p.stdout)
        except json.JSONDecodeError:
            last = {"error": "non-JSON output", "stdout": p.stdout[-2000:], "stderr": p.stderr[-2000:]}
            time.sleep(10 * (attempt + 1))
            continue
        if out.get("is_error"):
            last = {"error": out.get("result"), "terminal_reason": out.get("terminal_reason")}
            time.sleep(15 * (attempt + 1))
            continue
        u = out.get("usage", {})
        return {
            "result": out.get("result", ""),
            "models": list(out.get("modelUsage", {}).keys()),
            "tokens_in": u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
            + u.get("cache_read_input_tokens", 0),
            "tokens_out": u.get("output_tokens", 0),
            "usage": u,
            "cost_usd": out.get("total_cost_usd"),
            "seconds": round(time.time() - t0, 1),
            "session_id": out.get("session_id"),
            "transport_retries": attempt,
        }
    raise RuntimeError(f"claude call failed three times: {last}")


def extract(result, edition):
    blocks = [b for _, b in FENCE.findall(result)]
    fenced = bool(blocks)
    if not blocks:
        blocks = [result]
    wrote_edition = [m.group(0).strip() for b in blocks for m in EDITION_LINE.finditer(b)]
    if edition == 3:
        body = "\n".join(EDITION_LINE.sub("", b).strip("\n") for b in blocks)
        text = "gq 3;\n" + body.strip("\n") + "\n"
    else:
        text = "\n".join(b.strip("\n") for b in blocks) + "\n"
    return {"text": text, "blocks": len(blocks), "fenced": fenced, "edition_lines_written": wrote_edition}


def check(edition, text, truths):
    jobs = [{"id": "answer", "edition": edition, "text": text, "truth": None}]
    for i, t in enumerate(truths):
        jobs.append({"id": f"truth{i}", "edition": edition, "text": text,
                     "truth": ("gq 3;\n" + t) if edition == 3 else t})
    p = subprocess.run([CHECKER, "--schema", SCHEMA2], input="\n".join(json.dumps(j) for j in jobs) + "\n",
                       capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        return {"checker_error": p.stderr[-2000:]}
    rows = [json.loads(l) for l in p.stdout.splitlines() if l.strip()]
    res = rows[0]
    sems = [r.get("semantic") for r in rows[1:] if r.get("semantic") is not None]
    if truths:
        res["semantic_strict"] = any(s.get("strict") is True for s in sems)
        res["semantic_renamed"] = any(s.get("renamed") is True for s in sems)
        res["semantic_detail"] = sems
        res["semantic_equal"] = any(s.get("strict") is True or s.get("renamed") is True or s.get("ir_renamed") is True
                                    for s in sems)
    return res


def run_task(task, edition, card, workdir):
    truths = task["truth3"] if edition == 3 else task["truth2"]
    comp = task["m0"] is not None
    rec = {"task": task["id"], "kind": task["kind"], "m0": task["m0"], "edition": edition,
           "has_truth": bool(truths)}
    prompt = FIRST.format(card=card, task=task["prompt"], answer_rule=COMPOSITION if comp else ONE)
    a1 = call_claude(prompt, workdir)
    x1 = extract(a1["result"], edition)
    c1 = check(edition, x1["text"], truths)
    rec["try1"] = {**a1, **x1, "check": c1}
    rec["first_try_valid"] = bool(c1.get("parse_ok"))
    rec["repaired"] = False
    rec["valid_after_repair"] = rec["first_try_valid"]
    if not c1.get("parse_ok"):
        diag = c1.get("diagnostic", {}).get("rendered", json.dumps(c1))
        prompt2 = REPAIR.format(card=card, task=task["prompt"], text=x1["text"].rstrip("\n"), diagnostic=diag,
                                answer_rule=COMPOSITION_REPAIR if comp else ONE_REPAIR)
        a2 = call_claude(prompt2, workdir)
        x2 = extract(a2["result"], edition)
        c2 = check(edition, x2["text"], truths)
        rec["try2"] = {**a2, **x2, "check": c2}
        rec["repaired"] = True
        rec["valid_after_repair"] = bool(c2.get("parse_ok"))
    final = rec.get("try2", rec["try1"])["check"]
    rec["final_semantic_equal"] = final.get("semantic_equal") if truths else None
    rec["final_decls"] = final.get("decls")
    rec["tokens_in"] = rec["try1"]["tokens_in"] + rec.get("try2", {}).get("tokens_in", 0)
    rec["tokens_out"] = rec["try1"]["tokens_out"] + rec.get("try2", {}).get("tokens_out", 0)
    return rec


def recheck(args):
    tdir = os.path.join(HERE, "tasks")
    out = os.path.join(HERE, "runs", f"e{args.edition}-r{args.run}")
    for f in sorted(os.listdir(out)):
        path = os.path.join(out, f)
        rec = json.load(open(path))
        task = json.load(open(os.path.join(tdir, rec["task"] + ".json")))
        truths = task["truth3"] if args.edition == 3 else task["truth2"]
        rec["has_truth"] = bool(truths)
        for k in ("try1", "try2"):
            if k in rec:
                rec[k]["check"] = check(args.edition, rec[k]["text"], truths)
        final = rec.get("try2", rec["try1"])["check"]
        rec.pop("final_semantic_renamed", None)
        rec.pop("final_semantic_strict", None)
        rec["final_semantic_equal"] = final.get("semantic_equal") if truths else None
        rec["final_decls"] = final.get("decls")
        with open(path, "w") as fh:
            json.dump(rec, fh, indent=2)
            fh.write("\n")
    print("rechecked", out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--edition", type=int, required=True, choices=[2, 3])
    ap.add_argument("--run", type=int, required=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--recheck", action="store_true", help="re-run the checker on stored answers; no claude call")
    args = ap.parse_args()
    if args.recheck:
        return recheck(args)
    card = open(os.path.join(HERE, "cards", f"edition{args.edition}.md")).read()
    tdir = os.path.join(HERE, "tasks")
    tasks = [json.load(open(os.path.join(tdir, f))) for f in sorted(os.listdir(tdir)) if f.endswith(".json")]
    if args.only:
        keep = set(args.only.split(","))
        tasks = [t for t in tasks if t["id"] in keep]
    out = os.path.join(HERE, "runs", f"e{args.edition}-r{args.run}")
    os.makedirs(out, exist_ok=True)
    workdir = tempfile.mkdtemp(prefix="gq3-agent-")  # empty: the agent sees nothing but the prompt
    todo = [t for t in tasks if not os.path.exists(os.path.join(out, t["id"] + ".json"))]
    failures = 0
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(run_task, t, args.edition, card, workdir): t for t in todo}
        for f in cf.as_completed(futs):
            t = futs[f]
            try:
                rec = f.result()
            except Exception as e:  # a transport failure, outside the language
                failures += 1
                print(f"{t['id']}: FAILED {e}", file=sys.stderr)
                if failures >= 3:
                    print("three transport failures: stopping", file=sys.stderr)
                    ex.shutdown(cancel_futures=True)
                    break
                continue
            with open(os.path.join(out, t["id"] + ".json"), "w") as fh:
                json.dump(rec, fh, indent=2)
                fh.write("\n")
            code = rec["try1"]["check"].get("diagnostic", {}).get("code")
            print(f"e{args.edition} r{args.run} {t['id']}: first={rec['first_try_valid']} code={code} "
                  f"after_repair={rec['valid_after_repair']} sem={rec['final_semantic_equal']}", flush=True)


if __name__ == "__main__":
    main()
