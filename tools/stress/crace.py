#!/usr/bin/env python3
"""Conditional write races (#1424, #1426): N writers read the same commit,
then each sends `mutate --if-commit <commit>` inserting a different edge under
@card(0..1) (or the same node under @unique). Exactly one may win; every loser
must fail its precondition (exit 4, precondition_failure), never a @card or
@unique violation and never a second published edge. Runs on main and on a
fresh named branch that was never written. Usage: crace.py ROUNDS WRITERS"""
import json, os, shutil, subprocess, sys
from concurrent.futures import ThreadPoolExecutor

OG = os.environ.get("OG", "omnigraph")
D = "/tmp/og/crace"
SCHEMA = """node P {
  slug: String @key
  email: String?
  @unique(email)
}
node Q {
  slug: String @key
}
edge Owns: P -> Q @card(0..1) {
  @key(@src, @dst)
}
"""


def run(args, timeout=300):
    return subprocess.run([OG] + args, capture_output=True, text=True, timeout=timeout)


def main():
    rounds, writers = int(sys.argv[1]), int(sys.argv[2])
    shutil.rmtree(D, ignore_errors=True)
    os.makedirs(D)
    open(f"{D}/s.pg", "w").write(SCHEMA)
    g = f"{D}/g.omni"
    assert run(["init", "--schema", f"{D}/s.pg", g]).returncode == 0
    qs = [f"q{i}" for i in range(writers)]
    open(f"{D}/q.jsonl", "w").write("\n".join(json.dumps({"type": "Q", "data": {"slug": q}}) for q in qs) + "\n")
    assert run(["load", "--data", f"{D}/q.jsonl", "--mode", "merge", "--target", g]).returncode == 0
    open(f"{D}/m.gq", "w").write(
        "query own($p: String, $q: String) { insert Owns { from: $p, to: $q } }\n"
        "query mail($p: String, $e: String) { insert P { slug: $p, email: $e } }\n"
        "query mkp($p: String) { insert P { slug: $p } }\n"
        "query rd() { match { $p: P } return { $p.slug } limit 1 }\n")
    bad = 0
    stats = {}
    for rd in range(rounds):
        branch = "main"
        if rd % 2 == 1:
            branch = f"fresh{rd}"
            p = run(["branch", "create", branch, "--from", "main", "--target", g])
            assert p.returncode == 0, p.stderr
        kind = "edge" if rd % 4 < 2 else "unique"
        pk = f"p{rd}"
        if kind == "edge":
            # node exists on main before the fork
            if branch == "main":
                assert run(["mutate", "mkp", "--query", f"{D}/m.gq", "--params", json.dumps({"p": pk}), "--target", g]).returncode == 0
            else:
                # created on main before the fork? no: fork first, so make node on main and re-fork
                run(["branch", "delete", branch, "--target", g])
                assert run(["mutate", "mkp", "--query", f"{D}/m.gq", "--params", json.dumps({"p": pk}), "--target", g]).returncode == 0
                assert run(["branch", "create", branch, "--from", "main", "--target", g]).returncode == 0
        r = run(["query", "rd", "--query", f"{D}/m.gq", "--branch", branch, "--json", "--target", g])
        commit = json.loads(r.stdout)["graph_commit_id"]

        def w(i):
            if kind == "edge":
                args = ["mutate", "own", "--query", f"{D}/m.gq", "--params", json.dumps({"p": pk, "q": qs[i]})]
            else:
                args = ["mutate", "mail", "--query", f"{D}/m.gq", "--params", json.dumps({"p": f"{pk}x{i}", "e": f"same{rd}"})]
            return run(args + ["--branch", branch, "--if-commit", commit, "--json", "--target", g])

        with ThreadPoolExecutor(writers) as ex:
            res = list(ex.map(w, range(writers)))
        wins = [x for x in res if x.returncode == 0]
        losers = [x for x in res if x.returncode != 0]
        codes = {}
        for x in losers:
            txt = x.stdout + x.stderr
            if "precondition" in txt:
                c = "precondition"
            elif "card" in txt.lower():
                c = "CARD"
            elif "unique" in txt.lower():
                c = "UNIQUE"
            else:
                c = "other:" + txt.strip().splitlines()[-1][:120] if txt.strip() else "other"
            c = f"{c}/exit{x.returncode}"
            codes[c] = codes.get(c, 0) + 1
        # count what landed
        q = 'query c($p: String) { match { $a: P { slug: $p } $a owns $b } return { $b.slug } }' if kind == "edge" else \
            'query c($p: String) { match { $a: P { email: $p } } return { $a.slug } }'
        param = pk if kind == "edge" else f"same{rd}"
        c = run(["query", "-e", q, "--params", json.dumps({"p": param}), "--branch", branch, "--json", "--target", g])
        landed = json.loads(c.stdout)["row_count"]
        ok = len(wins) <= 1 and landed == len(wins) and all(k.startswith("precondition/exit4") for k in codes)
        stats_key = f"{kind}@{'main' if branch == 'main' else 'fresh'}"
        stats.setdefault(stats_key, []).append((len(wins), landed, codes))
        print(f"round {rd} {kind} on {branch}: wins={len(wins)} landed={landed} losers={codes} {'OK' if ok else 'BAD'}", flush=True)
        if not ok:
            bad += 1
            for x in losers:
                t = x.stdout + x.stderr
                if "precondition" not in t:
                    print("   loser:", t[-700:].replace("\n", " "))
                    break
    print(f"done bad={bad}")


if __name__ == "__main__":
    main()
