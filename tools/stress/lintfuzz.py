#!/usr/bin/env python3
"""Mutation fuzzer for the compiler: random token edits of valid schema and
query sources, run through `omnigraph lint`; flags panics, signals and hangs."""
import os, random, re, subprocess, sys, glob, hashlib

OG = os.environ.get("OG", "omnigraph")
OUT = "/tmp/og/lintfail"
os.makedirs(OUT, exist_ok=True)
WORK = "/tmp/og/lintwork"
os.makedirs(WORK, exist_ok=True)

TOKENS = ["{", "}", "(", ")", "[", "]", ",", ":", ";", "..", ".", "$x", "$p", "@", "@id", "@src", "@key", "@unique",
          "@index", "@range", "@check", "@card", "@embed", "@rename_from", "?", "[String]", "Vector(0)", "Vector(99999999999)",
          "I64", "U64", "I32", "F32", "Blob", "Date", "DateTime", "Bool", "enum(a, b)", "enum()", "not", "and", "or", "in",
          "is null", "null", "true", "false", "0", "18446744073709551616", "99999999999999999999999", "1.5", "1e309", "\"\"", "\"x\"",
          "\"\\u{0}\"", "\"\\", "match", "return", "order", "limit 0", "limit 18446744073709551615", "insert", "update", "delete",
          "set", "where", "count", "sum", "avg", "min", "max", "distinct", "nearest", "bm25", "rrf", "search", "fuzzy",
          "{1,3}", "{0,0}", "{3,1}", "{1,4294967296}", "<", ">", "<=", ">=", "=", "!=", "contains", "starts_with", "exists",
          "date(\"2026-02-30\")", "datetime(\"x\")", "now()", "->", "node", "edge", "interface", "implements", "query",
          "*", "|", "$e:", "@type", "as", "desc", "asc", "explain", "branch", "/*", "*/", "//", "\n", "\t", "é", "\u0000"]


def seeds():
    pgs, gqs = [], []
    for f in glob.glob(os.path.join(os.environ.get("OMNIGRAPH_REPO", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")), "crates/omnigraph-gqt/cases/**/*.gqt"), recursive=True):
        txt = open(f, encoding="utf-8", errors="replace").read()
        m = re.search(r"^--- schema\n(.*?)(?=^--- )", txt, re.S | re.M)
        if not m:
            continue
        schema = m.group(1)
        qs = re.findall(r"^--- query\n(.*?)(?=^--- )", txt, re.S | re.M)
        for q in qs:
            if q.strip().startswith("query"):
                pgs.append(schema)
                gqs.append(q)
    return pgs, gqs


def tokenize(s):
    return re.findall(r'"(?:[^"\\]|\\.)*"|\$?[A-Za-z_@][A-Za-z0-9_]*|\d+(?:\.\d+)?|\.\.|->|<=|>=|!=|//|\S|\s+', s)


def mutate(r, s):
    toks = tokenize(s)
    if not toks:
        return s
    for _ in range(r.randint(1, 4)):
        op = r.random()
        i = r.randrange(len(toks))
        if op < 0.3:
            toks[i] = r.choice(TOKENS)
        elif op < 0.5:
            toks.insert(i, r.choice(TOKENS) + " ")
        elif op < 0.65:
            del toks[i]
            if not toks:
                toks = [" "]
        elif op < 0.8:
            j = r.randrange(len(toks))
            toks[i], toks[j] = toks[j], toks[i]
        else:
            j = r.randrange(len(toks))
            toks.insert(i, toks[j])
    return "".join(toks)


def main():
    start, count = int(sys.argv[1]), int(sys.argv[2])
    pgs, gqs = seeds()
    print(f"{len(gqs)} seed pairs", flush=True)
    bad = 0
    for k in range(start, start + count):
        r = random.Random(k)
        i = r.randrange(len(gqs))
        pg, gq = pgs[i], gqs[i]
        which = r.random()
        if which < 0.35:
            pg = mutate(r, pg)
        elif which < 0.9:
            gq = mutate(r, gq)
        else:
            pg, gq = mutate(r, pg), mutate(r, gq)
        pf, qf = f"{WORK}/s.pg", f"{WORK}/q.gq"
        open(pf, "w").write(pg)
        open(qf, "w").write(gq)
        try:
            p = subprocess.run([OG, "lint", "--schema", pf, "--query", qf], capture_output=True, text=True, timeout=20)
            rc, err = p.returncode, p.stderr + p.stdout
        except subprocess.TimeoutExpired:
            rc, err = "timeout", ""
        if rc not in (0, 1) or "panicked" in err or "RUST_BACKTRACE" in err:
            bad += 1
            h = hashlib.sha1((pg + gq).encode()).hexdigest()[:10]
            d = f"{OUT}/{k}-{h}"
            os.makedirs(d, exist_ok=True)
            open(f"{d}/s.pg", "w").write(pg)
            open(f"{d}/q.gq", "w").write(gq)
            open(f"{d}/out.txt", "w").write(f"rc={rc}\n{err[-4000:]}")
            print(f"case {k}: rc={rc} {err.strip().splitlines()[-1][:200] if err.strip() else ''}", flush=True)
    print(f"done bad={bad}")


if __name__ == "__main__":
    main()
