#!/usr/bin/env python3
"""Reads of main's current head while `cleanup` runs, in the supported
topology: one writing process at a time (write bursts and cleanups alternate,
never overlap), any number of reader processes.

Cleanup always retains each live branch's head. A read during which no write
happened reads that head, so it must succeed and give the exact answer, even
while a cleanup with `--keep 1` or `--older-than 0s` runs beside it. A read
that overlapped a write may read a snapshot the policy removes, which the docs
tell operators to avoid (#958); its failure is counted, not judged.

Readers: a count, a full export (checked row by row), and `blob get` of a
random row's content.

Usage: cread.py <seconds>"""
import json, os, random, shutil, subprocess, sys, threading, time, base64

OG = os.environ.get("OG", "omnigraph")
SECS = int(sys.argv[1])
D = "/tmp/og/cread"
G = f"{D}/g.omni"
lock = threading.Lock()
model = {}          # slug -> (n, payload)
epoch = [0]         # bumped before and after every write burst
writing = [False]
stats = {}
errs = []
stop = threading.Event()


def bump(k):
    with lock:
        stats[k] = stats.get(k, 0) + 1


def og(args, timeout=600):
    p = subprocess.run([OG] + args + ["--target", G], capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def tail(s):
    return (s.strip().splitlines() or [""])[-1][-160:]


def snapshot():
    with lock:
        return epoch[0], writing[0], dict(model)


def judged(e0, w0):
    with lock:
        return epoch[0] == e0 and not w0 and not writing[0]


def burst(r, k0):
    with lock:
        writing[0] = True
        epoch[0] += 1
    rows = []
    for i in range(r.randint(1, 5)):
        k = f"d{k0}-{i}"
        n = r.randint(0, 10**6)
        payload = os.urandom(r.choice([10, 300, 70000]))
        rows.append((k, n, payload))
    decl, stm, params = [], [], {}
    for j, (k, n, payload) in enumerate(rows):
        decl += [f"$k{j}: String", f"$n{j}: I64", f"$c{j}: Blob"]
        stm.append(f"insert Doc {{ slug: $k{j}, n: $n{j}, content: $c{j} }}")
        params.update({f"k{j}": k, f"n{j}": n, f"c{j}": "base64:" + base64.b64encode(payload).decode()})
    pf = f"{D}/params.json"
    open(pf, "w").write(json.dumps(params))
    rc, out, err = og(["mutate", "-e", f"query m({', '.join(decl)}) {{ {' '.join(stm)} }}", "--params-file", pf, "--json"])
    with lock:
        if rc == 0:
            for k, n, payload in rows:
                model[k] = (n, payload)
        else:
            errs.append(f"write failed rc={rc} {tail(err)}")
        epoch[0] += 1
        writing[0] = False


def driver():
    r = random.Random(1)
    k = 0
    while not stop.is_set():
        if r.random() < 0.5:
            k += 1
            burst(r, k)
        else:
            pol = r.choice([["--keep", "1"], ["--keep", "2"], ["--older-than", "0s"]])
            rc, out, err = og(["cleanup"] + pol + ["--confirm", "--json"])
            bump("cleanup ok" if rc == 0 else f"cleanup failed {tail(err)}")
        time.sleep(r.uniform(0, 0.5))


def count_reader():
    while not stop.is_set():
        e0, w0, m = snapshot()
        rc, out, err = og(["query", "-e", "query c() { match { $d: Doc } return { count($d) as c } }", "--json"])
        ok = judged(e0, w0)
        if rc == 0:
            c = json.loads(out)["rows"][0]["c"]
            if ok and c != len(m):
                errs.append(f"WRONG count {c} != {len(m)} at epoch {e0}")
            bump("count ok")
        elif ok:
            errs.append(f"COUNT_FAILED on a retained head: {tail(err)}")
        else:
            bump(f"count failed across a write: {tail(err)}")


def export_reader():
    while not stop.is_set():
        e0, w0, m = snapshot()
        rc, out, err = og(["export", "--branch", "main"])
        ok = judged(e0, w0)
        if rc != 0:
            if ok:
                errs.append(f"EXPORT_FAILED on a retained head: {tail(err)}")
            else:
                bump(f"export failed across a write: {tail(err)}")
            continue
        bump("export ok")
        if not ok:
            continue
        got = {}
        for line in out.splitlines():
            if line.strip():
                rec = json.loads(line)
                got[rec.get("id") or rec["data"]["slug"]] = rec["data"]
        if set(got) != set(m):
            errs.append(f"EXPORT_ROWS differ at epoch {e0}: missing {sorted(set(m) - set(got))[:3]} extra {sorted(set(got) - set(m))[:3]}")
        for k, d in got.items():
            if k in m and d.get("n") != m[k][0]:
                errs.append(f"EXPORT_WRONG {k}")


def blob_reader():
    r = random.Random(3)
    while not stop.is_set():
        e0, w0, m = snapshot()
        if not m:
            time.sleep(0.2)
            continue
        k = r.choice(list(m))
        dst = f"{D}/blob-{threading.get_ident()}"
        rc, out, err = og(["blob", "get", "node", "Doc", k, "content", "--out", dst])
        ok = judged(e0, w0)
        if rc == 0:
            data = open(dst, "rb").read()
            if data != m[k][1]:
                errs.append(f"BLOB_WRONG {k}: {len(data)} bytes != {len(m[k][1])}")
            bump("blob ok")
        elif "exists" in err.lower():
            bump("blob out exists")
        elif ok:
            errs.append(f"BLOB_FAILED on a retained head: {tail(err)}")
        else:
            bump(f"blob failed across a write: {tail(err)}")
        try:
            os.remove(dst)
        except FileNotFoundError:
            pass


def main():
    shutil.rmtree(D, ignore_errors=True)
    os.makedirs(D)
    open(f"{D}/s.pg", "w").write("node Doc {\n  slug: String @key\n  n: I64\n  content: Blob\n}\n")
    p = subprocess.run([OG, "init", "--schema", f"{D}/s.pg", G], capture_output=True, text=True)
    if p.returncode:
        sys.exit(p.stderr)
    burst(random.Random(0), 0)
    ths = [threading.Thread(target=f) for f in (driver, count_reader, count_reader, export_reader, blob_reader, blob_reader)]
    for t in ths:
        t.start()
    time.sleep(SECS)
    stop.set()
    for t in ths:
        t.join()
    rc, out, err = og(["cleanup", "--keep", "1", "--confirm", "--json"])
    if rc:
        errs.append(f"final cleanup {tail(err)}")
    rc, out, err = og(["export", "--branch", "main"])
    n = sum(1 for l in out.splitlines() if l.strip()) if rc == 0 else -1
    if n != len(model):
        errs.append(f"final export rows {n} != {len(model)}")
    res = {"rows": len(model), "stats": dict(sorted(stats.items())), "errors": errs[:40], "error_count": len(errs)}
    print(json.dumps(res, indent=1))
    json.dump(res, open(f"{D}/result.json", "w"))


if __name__ == "__main__":
    main()
