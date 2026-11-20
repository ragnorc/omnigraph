#!/usr/bin/env python3
"""ixfuzz.py on a served graph: column-patch updates and index-served reads
through one long-lived `omnigraph-server` on a local cluster root.

Writes go through `/mutate` and `/load`, reads through `/query` and
`/export`, maintenance through recorded `/operations/optimize` (the served
index reconciliation), and a branch is created and merged through
`/branches`. The server keeps its caches across all of it. After each write
or optimize, full-text `search`, BTREE equality and range filters and a full
export must equal the model.

Usage: OG=<cli> OGS=<server> python3 tools/stress/sixfuzz.py <seed> <count>
"""
import json, os, random, shutil, subprocess, sys, time, urllib.request, urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ixfuzz import SCHEMA, WORDS  # noqa: E402

OG = os.environ.get("OG", "omnigraph")
OGS = os.environ.get("OGS", "omnigraph-server")
D = "/tmp/og/sixfuzz"
FAIL = "/tmp/og/sixfuzzfail"
PORT = 18190
BASE = f"http://127.0.0.1:{PORT}"
TOK = "tok-a"
G = "g"
POLICY = """version: 1
groups:
  ops: [act-a]
rules:
  - id: branch-actions
    allow:
      actors: { group: ops }
      actions: [read, export, change, maintain]
      branch_scope: any
  - id: graph-actions
    allow:
      actors: { group: ops }
      actions: [reclaim, invoke_query]
  - id: branch-to-branch
    allow:
      actors: { group: ops }
      actions: [branch_create, branch_merge, branch_delete]
      target_branch_scope: any
"""
Q_S = "query s($q: String) { match { $d: D\n search($d.body, $q) } return { $d.slug } }"
Q_EQ = "query keq($x: I32) { match { $d: D { k: $x } } return { $d.slug } }"
Q_LT = "query klt($x: I32) { match { $d: D\n $d.k < $x } return { $d.slug } }"


class Mismatch(Exception):
    pass


def http(method, path, body=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + TOK)
    req.add_header("Omnigraph-Http-Api", "0.14")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:3000]


def ulid():
    import secrets
    v = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    a = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    s = ""
    for _ in range(26):
        s = a[v & 31] + s
        v >>= 5
    return s


server = None


def start():
    global server
    shutil.rmtree(D, ignore_errors=True)
    os.makedirs(D)
    open(f"{D}/g.pg", "w").write(SCHEMA)
    open(f"{D}/policy.yaml", "w").write(POLICY)
    open(f"{D}/cluster.yaml", "w").write(
        "version: 1\nmetadata:\n  name: sixfuzz\ngraphs:\n  g:\n    schema: g.pg\n"
        "policies:\n  gp:\n    file: policy.yaml\n    applies_to: [g]\n")
    os.makedirs(f"{D}/root")
    p = subprocess.run([OG, "cluster", "apply", "--target", f"{D}/root", "--config", D, "--json"], capture_output=True, text=True)
    if p.returncode:
        sys.exit("apply: " + p.stdout[-1500:] + p.stderr[-1500:])
    st = subprocess.run([OG, "cluster", "status", "--target", f"{D}/root", "--json"], capture_output=True, text=True).stdout
    import re
    m = re.findall(r'"lock_id": "([^"]+)"', st)
    if m:
        subprocess.run([OG, "cluster", "force-unlock", m[0], "--target", f"{D}/root", "--yes"], capture_output=True, text=True)
    e = dict(os.environ)
    e["OMNIGRAPH_SERVER_BEARER_TOKENS_JSON"] = json.dumps({"act-a": TOK})
    e["OMNIGRAPH_OPERATION_ROUTES"] = "true"
    server = subprocess.Popen([OGS, "--cluster", f"{D}/root", "--bind", f"127.0.0.1:{PORT}"],
                              stdout=open(f"{D}/server.log", "w"), stderr=subprocess.STDOUT, env=e)
    for _ in range(240):
        try:
            with urllib.request.urlopen(BASE + "/readyz", timeout=5) as r:
                if r.status == 200:
                    return
        except Exception:
            pass
        if server.poll() is not None:
            sys.exit("server exited: " + open(f"{D}/server.log").read()[-2000:])
        time.sleep(0.5)
    sys.exit("server not ready")


def case(seed, r, log):
    pre = f"s{seed}-"
    model = {"main": {}}
    nid = [0]
    branch = None

    def ok(st, out, what):
        log.append(f"{what} -> {st}")
        if st != 200:
            raise Mismatch(f"{what} answered {st}: {out[-1500:]}\nLOG:\n" + "\n".join(log[-60:]))
        return json.loads(out) if out else None

    def body():
        return None if r.random() < 0.1 else " ".join(r.choice(WORDS) for _ in range(r.randint(1, 5)))

    def new_rows(cnt):
        rows = {}
        for _ in range(cnt):
            rows[f"{pre}{nid[0]:06d}"] = {"body": body(), "k": None if r.random() < 0.1 else r.randint(0, 40), "t": r.randint(0, 5)}
            nid[0] += 1
        return rows

    def load(br, rows, mode):
        lines = []
        for s, v in sorted(rows.items()):
            data = {"slug": s, "t": v["t"], "v": [0.5, 1.0, 1.5, 2.0], "b": "base64:AAEC"}
            if v["body"] is not None:
                data["body"] = v["body"]
            if v["k"] is not None:
                data["k"] = v["k"]
            lines.append(json.dumps({"type": "D", "data": data}))
        st, out = http("POST", f"/graphs/{G}/load", {"data": "\n".join(lines) + "\n", "mode": mode, "branch": br})
        ok(st, out, f"load {mode} {len(rows)} on {br}")

    def q(br, src, params):
        st, out = http("POST", f"/graphs/{G}/query", {"query": src, "params": params, "branch": br})
        res = ok(st, out, "query")
        return sorted(row["d.slug"] for row in res["rows"] if row["d.slug"].startswith(pre))

    def export(br):
        st, out = http("POST", f"/graphs/{G}/export", {"branch": br, "type_names": ["D"]}, timeout=300)
        log.append(f"export {br} -> {st}")
        if st != 200:
            raise Mismatch(f"export answered {st}: {out[-800:]}")
        rows = {}
        for line in out.splitlines():
            if line.strip():
                o = json.loads(line)
                dd = o["data"]
                if dd["slug"].startswith(pre):
                    rows[dd["slug"]] = {"body": dd.get("body"), "k": dd.get("k"), "t": dd.get("t")}
        return rows

    def check(br, where):
        m = model[br]
        got = export(br)
        if got != m:
            miss, extra = sorted(set(m) - set(got)), sorted(set(got) - set(m))
            chg = [(k, m[k], got[k]) for k in sorted(set(m) & set(got)) if m[k] != got[k]]
            raise Mismatch(f"{where}: export differs on {br}: missing {miss[:5]} extra {extra[:5]} changed {chg[:5]}\nLOG:\n" + "\n".join(log[-60:]))
        for w in r.sample(WORDS, 3):
            exp = sorted(s for s, v in m.items() if v["body"] and w in v["body"].split())
            g = q(br, Q_S, {"q": w})
            if g != exp:
                raise Mismatch(f"{where}: search({w}) on {br}: expected {len(exp)} got {len(g)} missing {sorted(set(exp) - set(g))[:5]} extra {sorted(set(g) - set(exp))[:5]}\nLOG:\n" + "\n".join(log[-60:]))
        for src, f in ((Q_EQ, lambda v, x: v == x), (Q_LT, lambda v, x: v < x)):
            x = r.randint(0, 40)
            exp = sorted(s for s, v in m.items() if v["k"] is not None and f(v["k"], x))
            g = q(br, src, {"x": x})
            if g != exp:
                raise Mismatch(f"{where}: {src.split('(')[0]} x={x} on {br}: expected {len(exp)} got {len(g)} missing {sorted(set(exp) - set(g))[:5]} extra {sorted(set(g) - set(exp))[:5]}\nLOG:\n" + "\n".join(log[-60:]))

    rows = new_rows(r.randint(200, 2000))
    load("main", rows, "append")
    model["main"].update(rows)
    check("main", "after load")
    for step in range(r.randint(5, 12)):
        br = branch if branch and r.random() < 0.5 else "main"
        m = model[br]
        op = r.choices(["upd_body", "upd_k", "merge", "append", "delete", "optimize", "branch", "mergeback"],
                       [4, 4, 2, 1, 1, 3, 1, 1])[0]
        if op in ("upd_body", "upd_k") and m:
            sel = r.choice(["t", "k", "slugs"])
            if sel == "t":
                x = r.randint(0, 5)
                where, match = f't = {x} and slug starts_with "{pre}"', [s for s, v in m.items() if v["t"] == x]
            elif sel == "k":
                x = r.randint(0, 40)
                where, match = f'k < {x} and slug starts_with "{pre}"', [s for s, v in m.items() if v["k"] is not None and v["k"] < x]
            else:
                ss = r.sample(sorted(m), min(len(m), r.randint(1, 30)))
                where, match = "slug in [" + ", ".join(f'"{s}"' for s in ss) + "]", ss
            if op == "upd_body":
                val = r.choice(WORDS) + " " + r.choice(WORDS)
                sets, assign = {"body": val}, f'body: "{val}"'
            else:
                val = r.randint(0, 40)
                sets, assign = {"k": val}, f"k: {val}"
            st, out = http("POST", f"/graphs/{G}/mutate", {"query": f"query u() {{ update D set {{ {assign} }} where {where} }}", "branch": br})
            ok(st, out, f"update {assign} where {where[:60]} on {br}")
            for s in match:
                m[s] = dict(m[s], **sets)
        elif op == "merge" and m:
            rows = {s: {"body": body(), "k": r.randint(0, 40), "t": m[s]["t"]} for s in r.sample(sorted(m), min(len(m), r.randint(1, 200)))}
            rows.update(new_rows(r.randint(0, 50)))
            load(br, rows, "merge")
            m.update(rows)
        elif op == "append":
            rows = new_rows(r.randint(1, 300))
            load(br, rows, "append")
            m.update(rows)
        elif op == "delete" and m:
            x = r.randint(0, 40)
            st, out = http("POST", f"/graphs/{G}/mutate", {"query": f'query x() {{ delete D where k = {x} and slug starts_with "{pre}" }}', "branch": br})
            ok(st, out, f"delete k={x} on {br}")
            for s in [s for s, v in m.items() if v["k"] == x]:
                del m[s]
        elif op == "optimize":
            oid = ulid()
            st, out = http("POST", f"/graphs/{G}/operations/optimize", {"operation_id": oid})
            log.append(f"optimize submit -> {st}")
            if st not in (200, 202):
                raise Mismatch(f"optimize submit {st}: {out[-800:]}\nLOG:\n" + "\n".join(log[-60:]))
            for _ in range(600):
                st, out = http("GET", f"/graphs/{G}/operations/{oid}")
                o = json.loads(out) if st == 200 else {}
                state = (o.get("operation") or {}).get("state") or o.get("state")
                if state in ("succeeded", "failed", "cancelled", "abandoned"):
                    break
                time.sleep(0.5)
            log.append(f"optimize {oid} -> {state}")
            if state != "succeeded":
                raise Mismatch(f"optimize ended {state}: {out[-1500:]}\nLOG:\n" + "\n".join(log[-60:]))
            for b in model:
                check(b, f"after optimize step {step}")
            continue
        elif op == "branch" and branch is None:
            branch = f"{pre}b{step}"
            st, out = http("POST", f"/graphs/{G}/branches", {"name": branch, "from": "main"})
            ok(st, out, f"branch create {branch}")
            model[branch] = {s: dict(v) for s, v in model["main"].items()}
            base = {s: dict(v) for s, v in model["main"].items()}
            continue
        elif op == "mergeback" and branch is not None:
            ours, theirs = model["main"], model[branch]
            res, conflict = {}, False
            for k in set(base) | set(ours) | set(theirs):
                b, o, t = base.get(k), ours.get(k), theirs.get(k)
                if o == t:
                    v = o
                elif o == b:
                    v = t
                elif t == b:
                    v = o
                else:
                    # entity-level: both sides changed the same row differently (docs/user/branching/merge.md)
                    conflict = True
                    break
                if v is not None:
                    res[k] = v
            st, out = http("POST", f"/graphs/{G}/branches/merge", {"source": branch, "target": "main"})
            log.append(f"merge {branch} -> {st}")
            if st == 200:
                if conflict:
                    raise Mismatch(f"merge succeeded where the model saw a conflict\nLOG:\n" + "\n".join(log[-60:]))
                model["main"] = res
            elif not conflict:
                raise Mismatch(f"merge refused where the model saw none: {out[-1500:]}\nLOG:\n" + "\n".join(log[-60:]))
            model.pop(branch)
            branch = None
            check("main", f"after mergeback step {step}")
            continue
        else:
            continue
        check(br, f"after {op} step {step} on {br}")
    for b in model:
        check(b, "final")


def main():
    start_seed, count = int(sys.argv[1]), int(sys.argv[2])
    os.makedirs(FAIL, exist_ok=True)
    start()
    fails = 0
    try:
        for seed in range(start_seed, start_seed + count):
            r = random.Random(seed)
            log = [f"seed {seed}"]
            t0 = time.time()
            try:
                case(seed, r, log)
                print(f"seed {seed} ok {time.time() - t0:.0f}s", flush=True)
            except Mismatch as e:
                fails += 1
                print(f"seed {seed} MISMATCH\n{e}\n", flush=True)
                open(os.path.join(FAIL, f"{seed}.txt"), "w").write(str(e))
            if server.poll() is not None:
                print("server exited: " + open(f"{D}/server.log").read()[-2000:], flush=True)
                break
    finally:
        if server and server.poll() is None:
            server.terminate()
            server.wait(30)
    print(f"done fails={fails}")


if __name__ == "__main__":
    main()
