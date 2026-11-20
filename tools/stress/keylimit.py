#!/usr/bin/env python3
"""The merge validation key limit (#1433, docs/user/branching/merge.md
"Validation limit"): a branch whose delta keys pass 32 MiB must be refused
with `branch-merge retained validation key bytes`, publish nothing, and then
merge in stages through `branch create --from-commit`, as the guide says.
Case A: fast-forward. Case B: main also writes, and plants one @unique clash
with a row late in the branch, which the second stage must refuse.
Usage: keylimit.py ROWS TAGLEN"""
import json, os, shutil, subprocess, sys, time

OG = os.environ.get("OG", "omnigraph")
D = "/tmp/og/keylimit"
SCHEMA = """node K {
  slug: String @key
  tag: String?
  n: I64
  @unique(tag)
}
"""
if os.environ.get("KL_PLAIN"):
    # no @unique/@range/@check/enum: a fast-forward needs no check (guide)
    SCHEMA = "node K {\n  slug: String @key\n  tag: String?\n  n: I64\n}\n"


def run(args, timeout=1800):
    t = time.time()
    p = subprocess.run([OG] + args, capture_output=True, text=True, timeout=timeout)
    p.secs = time.time() - t
    return p


def tag(i, L):
    s = f"t{i:08d}-"
    return s + "x" * (L - len(s))


def export_k(g, br):
    p = run(["export", "--branch", br, "--target", g])
    assert p.returncode == 0, p.stderr[-500:]
    out = {}
    for l in p.stdout.splitlines():
        r = json.loads(l)
        if r.get("type") == "K":
            out[r["data"]["slug"]] = (r["data"].get("tag"), r["data"]["n"])
    return out


def case(name, rows, L, clash):
    d = f"{D}/{name}"
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    open(f"{d}/s.pg", "w").write(SCHEMA)
    g = f"{d}/g.omni"
    assert run(["init", "--schema", f"{d}/s.pg", g]).returncode == 0
    open(f"{d}/m.jsonl", "w").write(json.dumps({"type": "K", "data": {"slug": "m0", "tag": "main-only", "n": 0}}) + "\n")
    assert run(["load", "--data", f"{d}/m.jsonl", "--mode", "merge", "--target", g]).returncode == 0
    assert run(["branch", "create", "big", "--from", "main", "--target", g]).returncode == 0
    model = {"m0": ("main-only", 0)}
    chunk = 6000
    for i in range(0, rows, chunk):
        lines = []
        for j in range(i, min(rows, i + chunk)):
            lines.append(json.dumps({"type": "K", "data": {"slug": (f"k{j}-" + "s" * L) if os.environ.get("KL_PLAIN") else f"k{j}", "tag": tag(j, L), "n": j}}))
            model[(f"k{j}-" + "s" * L) if os.environ.get("KL_PLAIN") else f"k{j}"] = (tag(j, L), j)
        open(f"{d}/w.jsonl", "w").write("\n".join(lines) + "\n")
        p = run(["load", "--data", f"{d}/w.jsonl", "--mode", "merge", "--branch", "big", "--json", "--target", g])
        assert p.returncode == 0, p.stdout[-400:] + p.stderr[-800:]
    keybytes = sum(len(k) + 4 + len(v[0]) + 4 for k, v in model.items())
    print(f"[{name}] branch has {rows} rows, approx key bytes {keybytes} ({keybytes / 2**20:.1f} MiB, with ids counted once)", flush=True)
    main_model = {"m0": ("main-only", 0)}
    if clash:
        # main writes after the fork; one row takes the tag of a late branch row
        late = rows - 7
        open(f"{d}/c.jsonl", "w").write(json.dumps({"type": "K", "data": {"slug": "mclash", "tag": tag(late, L), "n": -1}}) + "\n")
        assert run(["load", "--data", f"{d}/c.jsonl", "--mode", "merge", "--target", g]).returncode == 0
        main_model["mclash"] = (tag(late, L), -1)
    before = run(["commit", "list", "--branch", "main", "--json", "--target", g]).stdout
    p = run(["branch", "merge", "big", "--into", "main", "--json", "--target", g])
    txt = (p.stdout + p.stderr).replace("\n", " ")
    print(f"[{name}] whole merge: rc={p.returncode} {p.secs:.1f}s {txt[-700:]}", flush=True)
    after = run(["commit", "list", "--branch", "main", "--json", "--target", g]).stdout
    res = {"name": name, "whole_rc": p.returncode, "whole_limit_named": "retained validation key bytes" in txt,
           "main_unchanged": before == after}
    if p.returncode == 0:
        res["note"] = "whole merge accepted"
        act = export_k(g, "main")
        res["whole_merge_state_ok"] = act == {**model, **main_model} if not clash else None
        print(json.dumps(res), flush=True)
        return res
    # staged merge, as documented
    cl = json.loads(run(["commit", "list", "--branch", "big", "--json", "--target", g]).stdout)
    commits = cl if isinstance(cl, list) else cl.get("commits", cl.get("rows", []))
    ids = [c.get("graph_commit_id") or c.get("commit_id") or c.get("id") for c in commits]
    print(f"[{name}] big has {len(ids)} commits; first keys {list(commits[0].keys())[:8]}", flush=True)
    # commits newest first? find order by manifest_version if present
    if "manifest_version" in commits[0]:
        commits.sort(key=lambda c: c["manifest_version"])
        ids = [c.get("graph_commit_id") or c.get("commit_id") or c.get("id") for c in commits]
    mid = ids[len(ids) // 2]
    p = run(["branch", "create", "stage", "--from-commit", mid, "--target", g])
    assert p.returncode == 0, p.stderr[-500:]
    p1 = run(["branch", "merge", "stage", "--into", "main", "--json", "--target", g])
    t1 = (p1.stdout + p1.stderr).replace("\n", " ")
    print(f"[{name}] stage 1: rc={p1.returncode} {p1.secs:.1f}s {t1[-400:]}", flush=True)
    p2 = run(["branch", "merge", "big", "--into", "main", "--json", "--target", g])
    t2 = (p2.stdout + p2.stderr).replace("\n", " ")
    print(f"[{name}] stage 2: rc={p2.returncode} {p2.secs:.1f}s {t2[-500:]}", flush=True)
    act = export_k(g, "main")
    res.update({"stage1_rc": p1.returncode, "stage2_rc": p2.returncode,
                "stage2_unique": "unique" in t2.lower(), "stage2_limit": "retained validation key bytes" in t2})
    if not clash:
        res["final_ok"] = act == model and p1.returncode == 0 and p2.returncode == 0
    else:
        # stage 1 must pass (clash row is late), stage 2 must be refused for uniqueness, main = stage-1 rows + main rows
        res["final_ok"] = p1.returncode == 0 and p2.returncode != 0 and res["stage2_unique"] and \
            all(act.get(k) == v for k, v in main_model.items()) and "mclash" in act and \
            not any(act.get(f"k{j}") for j in range(rows - 10, rows))
    print(json.dumps(res), flush=True)
    return res


if __name__ == "__main__":
    rows, L = int(sys.argv[1]), int(sys.argv[2])
    os.makedirs(D, exist_ok=True)
    which = sys.argv[3] if len(sys.argv) > 3 else "AB"
    out = []
    if "A" in which:
        out.append(case("A-ff", rows, L, False))
    if "B" in which:
        out.append(case("B-clash", rows, L, True))
    print("RESULT", json.dumps(out))
