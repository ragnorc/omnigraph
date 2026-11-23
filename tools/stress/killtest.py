#!/usr/bin/env python3
"""Kill a CLI load at random moments; the graph must show the previous state
or the complete new one (atomic across all types), and accept writes after."""
import json, os, random, shutil, signal, subprocess, sys, time

OG = os.environ.get("OG", "omnigraph")
D = "/tmp/og/kill"
r = random.Random(int(sys.argv[1]))
trials = int(sys.argv[2])
Q = D + "/c.gq"
open(Q, "w").write("""query c() { match { $a: A } return { count($a) as a } }
query b() { match { $b: B } return { count($b) as b } }
query l() { match { $a: A  $a l $b } return { count($a) as l } }
query s() { match { $a: A  search($a.txt, "word5") } return { count($a) as s } }
""")


def q(g, name):
    p = subprocess.run([OG, "query", name, "--query", Q, "--json", "--target", g], capture_output=True, text=True)
    if p.returncode != 0:
        return ("ERR", p.stderr.strip().splitlines()[-2:] if p.stderr else "")
    return list(json.loads(p.stdout)["rows"][0].values())[0]


bad = 0
for t in range(trials):
    g = f"{D}/k{t}.omni"
    shutil.rmtree(g, ignore_errors=True)
    subprocess.run([OG, "init", "--schema", f"{D}/s.pg", g], capture_output=True)
    # optional prior state: small load
    prior = r.random() < 0.5
    if prior:
        subprocess.run([OG, "load", "--data", f"{D}/small.jsonl", "--mode", "overwrite", "--target", g], capture_output=True)
    delay = r.uniform(0.05, 3.2)
    p = subprocess.Popen([OG, "load", "--data", f"{D}/big.jsonl", "--mode", "overwrite", "--target", g],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(delay)
    p.send_signal(signal.SIGKILL)
    rc = p.wait()
    state = (q(g, "c"), q(g, "b"), q(g, "l"))
    full = (60000, 60000, 60000)
    before = (10, 10, 10) if prior else (0, 0, 0)
    ok = state in (full, before)
    # can we write afterwards, and does search work?
    w = subprocess.run([OG, "mutate", "-e", 'query m() { insert B { slug: "after", v: 1 } }', "--json", "--target", g],
                       capture_output=True, text=True)
    wok = w.returncode == 0
    s = q(g, "s")
    after_b = q(g, "b")
    print(f"trial {t} delay={delay:.2f} rc={rc} prior={prior} state={state} write_ok={wok} search={s} b_after={after_b}", flush=True)
    if not ok or not wok or (isinstance(after_b, int) and after_b != state[1] + 1):
        bad += 1
        print("  BAD", w.stderr[-800:], flush=True)
    else:
        shutil.rmtree(g, ignore_errors=True)
print("bad", bad)
