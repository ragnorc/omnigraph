#!/usr/bin/env python3
"""Direct writes to a cluster graph obey its applied policy (#1423, #1434).
For each actor (act-w, act-u, act-r, none) and each write or maintenance
command, run the command directly, through the root with --graph and through
the graph's own store, and compare the outcome with `omnigraph access` for
the same actor and branch pair. A refused command must leave the graph
unchanged. Makes its cluster under /tmp/og/pol on first use."""
import json, os, re, subprocess, sys

OG = os.environ.get("OG", "omnigraph")
D = "/tmp/og/pol"
ROOT = f"{D}/root"
STORE = f"{ROOT}/graphs/g.omni"


def run(args, timeout=300):
    return subprocess.run([OG] + args, capture_output=True, text=True, timeout=timeout, cwd=D)


def unlock():
    s = run(["cluster", "status", "--target", ROOT, "--json"]).stdout
    m = re.findall(r'"lock_id": "([^"]+)"', s)
    if m:
        run(["cluster", "force-unlock", m[0], "--target", ROOT, "--yes"])


def access(actor, branch, target_branch=None):
    a = ["access", "--target", ROOT, "--graph", "g", "--branch", branch, "--json"]
    if target_branch:
        a += ["--target-branch", target_branch]
    if actor:
        a += ["--as", actor]
    p = run(a)
    try:
        j = json.loads(p.stdout)
        return set(j["actions"]) | set(j.get("graph_wide", []))
    except Exception:
        return set()


def head(br):
    unlock()
    p = run(["commit", "list", "--branch", br, "--json", "--target", ROOT, "--graph", "g"])
    try:
        cl = json.loads(p.stdout)
        cl = cl if isinstance(cl, list) else cl.get("commits", [])
        return max(c["graph_manifest_version"] for c in cl), len(run(["branches" if False else "branch", "list", "--json", "--target", ROOT, "--graph", "g"]).stdout)
    except Exception:
        return p.stdout[-200:] + p.stderr[-200:]


def ensure_feat():
    unlock()
    run(["branch", "create", "feat", "--from", "main", "--target", ROOT, "--graph", "g", "--as", "act-w"])
    unlock()
    open(f"{D}/f.jsonl", "w").write(json.dumps({"type": "P", "data": {"slug": "fx", "n": 7}}) + "\n")
    run(["load", "--data", f"{D}/f.jsonl", "--mode", "merge", "--branch", "feat", "--target", ROOT, "--graph", "g", "--as", "act-w"])
    unlock()


POLICY = """version: 1
protected_branches: [main, rel]
groups:
  writers: [act-w]
  users: [act-u]
  all: [act-w, act-u, act-r]
rules:
  - id: all-read
    allow:
      actors: { group: all }
      actions: [read, export]
      branch_scope: any
  - id: w-change
    allow:
      actors: { group: writers }
      actions: [change, maintain]
      branch_scope: any
  - id: w-branch
    allow:
      actors: { group: writers }
      actions: [branch_create, branch_delete]
      target_branch_scope: any
  - id: w-merge
    allow:
      actors: { group: writers }
      actions: [branch_merge]
      target_branch_scope: any
  - id: w-reclaim
    allow:
      actors: { group: writers }
      actions: [reclaim]
  - id: u-change
    allow:
      actors: { group: users }
      actions: [change]
      branch_scope: unprotected
  - id: u-branch
    allow:
      actors: { group: users }
      actions: [branch_create, branch_delete]
      target_branch_scope: unprotected
  - id: u-merge
    allow:
      actors: { group: users }
      actions: [branch_merge]
      target_branch_scope: unprotected
"""


def setup():
    """Make the cluster: one graph g bound to POLICY, main and rel protected."""
    if os.path.isdir(ROOT):
        return
    os.makedirs(ROOT)
    open(f"{D}/g.pg", "w").write("node P {\n  slug: String @key\n  n: I64?\n}\n")
    open(f"{D}/g.policy.yaml", "w").write(POLICY)
    open(f"{D}/cluster.yaml", "w").write("version: 1\nmetadata:\n  name: polfuzz\ngraphs:\n  g:\n    schema: g.pg\npolicies:\n  gp:\n    file: g.policy.yaml\n    applies_to: [g]\n")
    p = run(["cluster", "apply", "--target", ROOT, "--config", ".", "--json"])
    assert p.returncode == 0, p.stderr[-800:]


def main():
    setup()
    open(f"{D}/a.jsonl", "w").write(json.dumps({"type": "P", "data": {"slug": "a", "n": 1}}) + "\n")
    open(f"{D}/m.gq", "w").write('query m() { update P set { n: 2 } where slug = "a" }\n')
    actors = ["act-w", "act-u", "act-r", None]
    # (name, action, branch, target_branch, args builder)
    cmds = [
        ("load main", "change", "main", None, lambda: ["load", "--data", f"{D}/a.jsonl", "--mode", "merge", "--branch", "main"]),
        ("load feat", "change", "feat", None, lambda: ["load", "--data", f"{D}/a.jsonl", "--mode", "merge", "--branch", "feat"]),
        ("mutate main", "change", "main", None, lambda: ["mutate", "m", "--query", f"{D}/m.gq", "--branch", "main"]),
        ("mutate feat", "change", "feat", None, lambda: ["mutate", "m", "--query", f"{D}/m.gq", "--branch", "feat"]),
        ("branch create rel (protected)", "branch_create", "main", "rel", lambda: ["branch", "create", "rel", "--from", "main"]),
        ("branch create tmp", "branch_create", "main", "tmp", lambda: ["branch", "create", "tmp", "--from", "main"]),
        ("merge feat->main", "branch_merge", "feat", "main", lambda: ["branch", "merge", "feat", "--into", "main"]),
        ("merge main->feat", "branch_merge", "main", "feat", lambda: ["branch", "merge", "main", "--into", "feat"]),
        ("branch delete feat", "branch_delete", "feat", None, lambda: ["branch", "delete", "feat"]),
        ("optimize", "maintain", "main", None, lambda: ["optimize", "--yes"]),
        ("index build", "maintain", "main", None, lambda: ["index", "build", "--branch", "main"]),
        ("cleanup", "reclaim", "main", None, lambda: ["cleanup", "--keep", "1", "--older-than", "0s", "--confirm", "--yes"]),
    ]
    bad = 0
    rows = []
    for via in ["root", "store"]:
        for actor in actors:
            for name, action, br, tb, mk in cmds:
                ensure_feat()
                # `access` does not report reclaim (docs/user/operations/policy.md);
                # only rule w-reclaim grants it
                allowed = (actor == "act-w") if action == "reclaim" else action in access(actor, br, tb)
                before = (head("main"), head("feat"))
                unlock()
                tgt = ["--target", ROOT, "--graph", "g"] if via == "root" else ["--target", STORE]
                args = mk() + tgt + ["--json"] + (["--as", actor] if actor else [])
                p = run(args)
                txt = (p.stdout + p.stderr).replace("\n", " ")
                after = (head("main"), head("feat"))
                denied = "policy" in txt.lower() and ("denied" in txt.lower() or "names no actor" in txt.lower())
                ok_run = p.returncode == 0
                verdict = "ok"
                if allowed and not ok_run:
                    verdict = "ALLOWED-BUT-FAILED"
                if not allowed and ok_run:
                    verdict = "DENIED-BUT-RAN"
                if not allowed and not ok_run and not denied:
                    verdict = "refused-other"
                if not ok_run and before != after:
                    verdict += "+STATE-CHANGED"
                line = f"{via:5} {str(actor):6} {name:30} access={'allow' if allowed else 'deny ':5} rc={p.returncode} {verdict}"
                if verdict != "ok":
                    m = re.search(r'"error": "([^"]{0,220})', txt)
                    line += "  :: " + (m.group(1) if m else txt[-220:])
                print(line, flush=True)
                if verdict not in ("ok",):
                    bad += 1
                # undo effects that later cases rely on
                unlock()
                run(["branch", "delete", "tmp", "--target", ROOT, "--graph", "g", "--as", "act-w"])
                unlock()
                run(["branch", "delete", "rel", "--target", ROOT, "--graph", "g", "--as", "act-w"])
                unlock()
    print(f"done nonok={bad}")


if __name__ == "__main__":
    main()
