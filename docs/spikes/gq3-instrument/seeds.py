#!/usr/bin/env python3
"""Seeded repairs for edition 3: check each seed with the edition-3 parser; when it is
refused, send the same repair turn run.py sends (a fresh `claude -p` call carrying the card,
the task, the seed and the diagnostic's full text) and check the repaired answer.

    python3 docs/spikes/gq3-instrument/seeds.py --rep 1
"""
import argparse, concurrent.futures as cf, json, os, tempfile
import run as R

HERE = R.HERE


def one(seed, task, card, workdir):
    truths = task["truth3"]
    c0 = R.check(3, seed["text"], truths)
    rec = {"seed": seed["id"], "task": seed["task"], "habit": seed["habit"], "seed_check": c0,
           "refused": not c0.get("parse_ok")}
    if c0.get("parse_ok"):
        return rec
    comp = task["m0"] is not None
    prompt = R.REPAIR.format(card=card, task=task["prompt"], text=seed["text"].rstrip("\n"),
                             diagnostic=c0["diagnostic"]["rendered"],
                             answer_rule=R.COMPOSITION_REPAIR if comp else R.ONE_REPAIR)
    a = R.call_claude(prompt, workdir)
    x = R.extract(a["result"], 3)
    c = R.check(3, x["text"], truths)
    rec["repair"] = {**a, **x, "check": c}
    rec["valid_after_repair"] = bool(c.get("parse_ok"))
    rec["semantic_equal"] = c.get("semantic_equal")
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rep", type=int, required=True)
    ap.add_argument("--workers", type=int, default=5)
    args = ap.parse_args()
    card = open(os.path.join(HERE, "cards", "edition3.md")).read()
    seeds = json.load(open(os.path.join(HERE, "seeds", "seeds.json")))
    out = os.path.join(HERE, "seeds", f"rep{args.rep}")
    os.makedirs(out, exist_ok=True)
    workdir = tempfile.mkdtemp(prefix="gq3-agent-")
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {}
        for sd in seeds:
            if os.path.exists(os.path.join(out, sd["id"] + ".json")):
                continue
            task = json.load(open(os.path.join(HERE, "tasks", sd["task"] + ".json")))
            futs[ex.submit(one, sd, task, card, workdir)] = sd
        for f in cf.as_completed(futs):
            rec = f.result()
            json.dump(rec, open(os.path.join(out, rec["seed"] + ".json"), "w"), indent=2)
            code = rec["seed_check"].get("diagnostic", {}).get("code")
            print(rec["seed"], rec["habit"], "refused" if rec["refused"] else "ACCEPTED", code,
                  rec.get("valid_after_repair"), rec.get("semantic_equal"), flush=True)


if __name__ == "__main__":
    main()
