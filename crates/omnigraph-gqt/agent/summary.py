"""The summary of a results directory of `run.py`: `summary.md` and
`summary.json`, per arm, from `records.jsonl`. Standard library only."""

import json
import os

# Edition 2's 2026-11-09 numbers on the 45 shared items, per run
# (results/2026-11-09/summary.json, the RFC gate's baseline).
EDITION2 = {"accepted": [43, 43, 43], "correct": [39, 39, 40]}


def json_lines(text):
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def spread(xs):
    if not xs:
        return "-"
    lo, hi = min(xs), max(xs)
    return f"{mean(xs):.1f} ({lo})" if lo == hi else f"{mean(xs):.1f} ({lo}–{hi})"


def accepted(r):
    return r["judge"].get("accepted") is True


def correct(r):
    return r["judge"].get("correct") is True


def gap(r):
    return accepted(r) and r["judge"].get("planner_gap") is True


def outcome(r):
    """One word for a first answer."""
    j = r["judge"]
    if j.get("error"):
        return "judge-error"
    if j.get("accepted") is False:
        return "refused"
    if j.get("planner_gap"):
        return "gap"
    if j.get("correct") is True:
        return "correct"
    if j.get("correct") is None:
        return "unjudged"
    if j.get("execution_error"):
        return "engine-error"
    return "wrong-rows"


def summarize(out):
    records = json_lines(open(os.path.join(out, "records.jsonl")).read())
    meta = json.load(open(os.path.join(out, "meta.json")))
    arms = [a for a in ("gq3", "where") if any(r["arm"] == a for r in records)]
    runs = sorted({r["run"] for r in records})
    first = {(r["arm"], r["run"], r["task"]): r for r in records if r["condition"] == "first"}
    repairs = {(r["arm"], r["run"], r["task"], r["condition"]): r for r in records
               if r["condition"] in ("repair_card", "repair_bare")}
    seeded = [r for r in records if r["condition"] == "seeded"]
    tasks = {}
    for r in records:
        tasks.setdefault(r["task"], r)

    numbers = {"meta": meta, "arms": {}}
    for arm in arms:
        per_set = {}
        for subset in ("shared", "tasks", "corpus", "all"):
            cols = {k: [] for k in (
                "n", "accepted", "correct", "judged", "gap", "ir_equal", "unjudged", "no_expected",
                "refused", "repair_card_accepted", "repair_card_correct", "repair_bare_accepted",
                "repair_bare_correct", "after_repair_correct", "tokens_in", "tokens_out")}
            for run in runs:
                fs = [r for (a, n, _), r in first.items() if a == arm and n == run and (
                    subset == "all" or (subset == "shared" and r["edition2_queries"] == 1)
                    or r["set"] == subset)]
                if not fs:
                    continue
                refused = [r for r in fs if r["judge"].get("accepted") is False]
                rc = [repairs.get((arm, run, r["task"], "repair_card")) for r in refused]
                rb = [repairs.get((arm, run, r["task"], "repair_bare")) for r in refused]
                rc = [x for x in rc if x]
                rb = [x for x in rb if x]
                final = [repairs.get((arm, run, r["task"], "repair_card"), r)
                         if r["judge"].get("accepted") is False else r for r in fs]
                cols["n"].append(len(fs))
                cols["accepted"].append(sum(accepted(r) for r in fs))
                cols["correct"].append(sum(correct(r) for r in fs))
                cols["judged"].append(sum(r["judge"].get("correct") is not None for r in fs))
                cols["gap"].append(sum(gap(r) for r in fs))
                cols["ir_equal"].append(sum(r["judge"].get("ir_equal") is True for r in fs))
                cols["unjudged"].append(sum(accepted(r) and r["judge"].get("correct") is None for r in fs))
                cols["no_expected"].append(sum(accepted(r) and "no_expected_rows" in r["judge"] for r in fs))
                cols["refused"].append(len(refused))
                cols["repair_card_accepted"].append(sum(accepted(x) for x in rc))
                cols["repair_card_correct"].append(sum(correct(x) for x in rc))
                cols["repair_bare_accepted"].append(sum(accepted(x) for x in rb))
                cols["repair_bare_correct"].append(sum(correct(x) for x in rb))
                cols["after_repair_correct"].append(sum(correct(x) for x in final))
                cols["tokens_in"].append(round(mean([r["agent"]["tokens_in"] for r in fs])))
                cols["tokens_out"].append(round(mean([r["agent"]["tokens_out"] for r in fs])))
            per_set[subset] = cols
        sd = {"accepted": [], "correct": [], "n": []}
        for run in runs:
            ss = [r for r in seeded if r["arm"] == arm and r["run"] == run]
            if ss:
                sd["n"].append(len(ss))
                sd["accepted"].append(sum(accepted(r) for r in ss))
                sd["correct"].append(sum(correct(r) for r in ss))
        per_set["seeded"] = sd
        numbers["arms"][arm] = per_set

    gate = {}
    for arm in arms:
        sh = numbers["arms"][arm]["shared"]
        acc_ok = mean(sh["accepted"]) >= mean(EDITION2["accepted"])
        cor_ok = mean(sh["correct"]) >= mean(EDITION2["correct"])
        gate[arm] = {
            "shared_items": sh["n"][0] if sh["n"] else 0,
            "accepted": sh["accepted"], "correct": sh["correct"], "planner_gaps": sh["gap"],
            "edition2_accepted": EDITION2["accepted"], "edition2_correct": EDITION2["correct"],
            "acceptance_at_or_above": acc_ok, "correctness_at_or_above": cor_ok,
            "verdict": "pass" if acc_ok and cor_ok else "fail",
        }
    numbers["gate"] = gate

    L = [f"# Agent instrument results: {os.path.basename(out)}", "",
         f"Model `{meta['model']}`, {meta['claude']}, git `{meta['git_head']}`, started {meta['started']}.",
         "Card digests: " + ", ".join(f"`{k}` `{v[:12]}`" for k, v in sorted(meta["cards"].items())) + ".",
         "README.md beside the runner defines every column. Not a CI gate.", "",
         "Means over the runs, the range in parentheses. `shared` is the 45 items edition 2 expresses in one "
         "query; `all` is the 75.", ""]
    head = ("| Arm | Set | Items | First try accepted | First try correct (rows) | Planner gaps (P006) "
            "| Unjudged (no expected rows) | IR-equal | Refused | Repair with card accepted / correct "
            "| Repair without card accepted / correct | Correct after repair | Tokens in / out |")
    L += [head, "|" + "---|" * 13]
    for arm in arms:
        for subset in ("shared", "tasks", "corpus", "all"):
            c = numbers["arms"][arm][subset]
            if not c["n"]:
                continue
            L.append(
                f"| {arm} | {subset} | {c['n'][0]} | {spread(c['accepted'])} | {spread(c['correct'])} "
                f"| {spread(c['gap'])} | {spread(c['unjudged'])} ({spread(c['no_expected'])}) | {spread(c['ir_equal'])} "
                f"| {spread(c['refused'])} | {spread(c['repair_card_accepted'])} / {spread(c['repair_card_correct'])} "
                f"| {spread(c['repair_bare_accepted'])} / {spread(c['repair_bare_correct'])} "
                f"| {spread(c['after_repair_correct'])} | {spread(c['tokens_in'])} / {spread(c['tokens_out'])} |")
    L += ["", "| Arm | Seeds | Seeded repairs accepted | Seeded repairs correct |", "|---|---|---|---|"]
    for arm in arms:
        sd = numbers["arms"][arm]["seeded"]
        if sd["n"]:
            L.append(f"| {arm} | {sd['n'][0]} | {spread(sd['accepted'])} | {spread(sd['correct'])} |")

    L += ["", "## The gate", "",
          f"Edition 2 on 2026-11-09, 45 shared items, per run: accepted {EDITION2['accepted']}, "
          f"correct by rows {EDITION2['correct']}.", ""]
    for arm, g in gate.items():
        L.append(f"- `{arm}`: accepted {g['accepted']}, correct by rows {g['correct']} (planner gaps "
                 f"{g['planner_gaps']}). Acceptance at or above edition 2's: "
                 f"{'yes' if g['acceptance_at_or_above'] else 'no'}; correctness at or above: "
                 f"{'yes' if g['correctness_at_or_above'] else 'no'}. Verdict: **{g['verdict']}**.")

    L += ["", "## By task", "",
          "First answers over the runs per arm: `c` correct, `g` planner gap, `w` wrong rows, `e` engine "
          "error, `r` refused, `u` unjudged. Rows where the arms differ or either is not correct every run.",
          "", "| Task | Set | Shared | Kind | gq3 | where |", "|---|---|---|---|---|---|"]
    letters = {"correct": "c", "gap": "g", "wrong-rows": "w", "engine-error": "e", "refused": "r",
               "unjudged": "u", "judge-error": "x"}
    by_task = {}
    for tid in sorted(tasks, key=lambda t: (tasks[t]["set"], t)):
        cells = {}
        for arm in arms:
            cells[arm] = "".join(letters[outcome(first[(arm, run, tid)])] for run in runs
                                 if (arm, run, tid) in first)
        by_task[tid] = cells
        vals = list(cells.values())
        if len(set(vals)) > 1 or any(set(v) != {"c"} for v in vals):
            t = tasks[tid]
            L.append(f"| {tid} | {t['set']} | {'yes' if t['edition2_queries'] == 1 else ''} | {t['kind']} "
                     f"| {cells.get('gq3', '')} | {cells.get('where', '')} |")
    numbers["by_task"] = by_task

    L += ["", "## Every first answer that is not correct", "",
          "| Arm | Run | Task | Outcome | Code or reason |", "|---|---|---|---|---|"]
    wrong = []
    for (arm, run, tid), r in sorted(first.items()):
        o = outcome(r)
        if o == "correct":
            continue
        j = r["judge"]
        detail = ((j.get("diagnostic") or {}).get("code") or (j.get("diagnostic") or {}).get("message", "")
                  if o == "refused" else j.get("execution_error") or j.get("correct_reason") or j.get("error", ""))
        detail = str(detail).replace("|", "\\|").replace("\n", " ")[:160]
        wrong.append({"arm": arm, "run": run, "task": tid, "outcome": o, "detail": detail})
        L.append(f"| {arm} | {run} | {tid} | {o} | {detail} |")
    numbers["not_correct"] = wrong

    L += ["", "## Seeds by code", "", "| Arm | Seed | Code | Class | Accepted | Correct |", "|---|---|---|---|---|---|"]
    by_seed = {}
    for r in seeded:
        by_seed.setdefault((r["arm"], r["seed"]), []).append(r)
    for (arm, sid), rs in sorted(by_seed.items()):
        L.append(f"| {arm} | {sid} | {rs[0].get('seed_code') or '-'} | {rs[0]['class']} "
                 f"| {sum(accepted(x) for x in rs)}/{len(rs)} | {sum(correct(x) for x in rs)}/{len(rs)} |")

    with open(os.path.join(out, "summary.md"), "w") as f:
        f.write("\n".join(L) + "\n")
    with open(os.path.join(out, "summary.json"), "w") as f:
        json.dump({k: v for k, v in numbers.items() if k != "meta"}, f, indent=2, sort_keys=True)
        f.write("\n")
    print("\n".join(L[:40]))
