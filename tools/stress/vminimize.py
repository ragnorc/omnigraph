#!/usr/bin/env python3
"""Record the CLI calls of one ufuzz seed up to the first `index build` that
fails with 'name no partition count', then delta-minimize the call list."""
import importlib.util, json, os, shutil, subprocess, sys
OG = os.environ["OG"]; SEED = int(sys.argv[1]); REC = "/tmp/og/vrec"
spec = importlib.util.spec_from_file_location("u", os.path.join(os.path.dirname(__file__), "ufuzz.py"))
u = importlib.util.module_from_spec(spec); spec.loader.exec_module(u)
calls = []
real = u.run
class Stop(Exception): pass
def rec(args, timeout=300):
    a = list(args)
    for i, x in enumerate(a):           # snapshot data/param files
        if isinstance(x, str) and os.path.isfile(x) and x.endswith((".jsonl", ".json")):
            n = f"{REC}/f{len(calls)}_{os.path.basename(x)}"; shutil.copy(x, n); a[i] = n
    p = real(args, timeout)
    if a[0] not in ("export", "query"):
        calls.append(a)
    if a[:2] == ["index", "build"] and p.returncode and "partition count" in p.stderr:
        raise Stop()
    return p
u.run = rec
try:
    u.case(SEED); print("no failure"); sys.exit(1)
except Stop:
    pass
G = [x for x in calls[0] if x.endswith("g.omni")][0]
def replay(cs):
    d = "/tmp/og/vrec/try"; shutil.rmtree(d, ignore_errors=True); os.makedirs(d)
    g2 = f"{d}/g.omni"
    for c in cs:
        c = [g2 if x == G else x for x in c]
        p = subprocess.run([OG] + c, capture_output=True, text=True)
        if c[:2] == ["index", "build"] and "partition count" in p.stderr:
            return True
    return False
cs = calls
assert replay(cs), "replay does not fail"
# always keep init (first) and final index build (last)
changed = True
while changed:
    changed = False
    for i in range(1, len(cs) - 1):
        t = cs[:i] + cs[i + 1:]
        if replay(t):
            cs = t; changed = True; break
json.dump({"G": G, "calls": cs}, open(f"{REC}/min_{SEED}.json", "w"), indent=1)
for c in cs:
    print(" ".join(c))
    for x in c:
        if x.startswith(REC + "/f"):
            print("   ", open(x).read()[:600].replace("\n", "\n    "))
