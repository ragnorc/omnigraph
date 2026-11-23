#!/usr/bin/env python3
"""Branches as entries of main's catalog (#1504), served: W client threads,
each owning the names under w<i>/, create branches (from main, from their own
live branch, from a past commit), write rows to them and to main, merge them
into main (some with delete_branch), delete them and create the names again.
Every create and delete publishes on main, so they race with main writes and
merges from the other clients.

Each client knows its own names exactly, so after each of its own steps
`GET /branches` restricted to w<i>/ must equal its model. Rows carry the name
and life of the branch they were written on; a branch must hold exactly the
rows acknowledged on it (plus what its source held at create). main must hold
every row acknowledged on main and every row of a branch at an acknowledged
merge, and nothing nobody attempted.

KILL=1 SIGKILLs the server every few seconds and restarts it. A request cut by
the kill has an unknown outcome: the client reads back the branch list and its
rows to settle it. Then a not_ready branch or a retirement-pending one must be
finished by the next delete, as docs/user/branching/index.md says, and the name
must be usable again.

BC_S3=1 puts the cluster root on RustFS (127.0.0.1:9000, bucket omnigraph-ci).

Usage: OG=... OGS=... bcat.py <seed> <seconds>"""
import json, os, random, shutil, signal, subprocess, sys, threading, time, urllib.parse, urllib.request, urllib.error

OG = os.environ.get("OG", "omnigraph")
OGS = os.environ.get("OGS", "omnigraph-server")
SEED, SECS = int(sys.argv[1]), float(sys.argv[2])
W = int(os.environ.get("BC_W", "6"))
KILL = os.environ.get("KILL") == "1"
D = f"/tmp/og/bcat-{SEED}"
ROOT = f"{D}/root"
if os.environ.get("BC_S3") == "1":
    # RustFS on 127.0.0.1:9000 with bucket omnigraph-ci, as s3merge.py uses it
    ROOT = f"s3://omnigraph-ci/{os.environ.get('BC_S3_PREFIX', 'stress')}/bcat-{SEED}-{int(time.time())}"
    os.environ.update({"AWS_ACCESS_KEY_ID": "omnigraphci", "AWS_SECRET_ACCESS_KEY": "omnigraphci-secret",
                       "AWS_REGION": "us-east-1", "AWS_ALLOW_HTTP": "true", "AWS_S3_FORCE_PATH_STYLE": "true",
                       "AWS_ENDPOINT_URL": os.environ.get("BC_S3_ENDPOINT", "http://127.0.0.1:9000"),
                       "AWS_ENDPOINT_URL_S3": os.environ.get("BC_S3_ENDPOINT", "http://127.0.0.1:9000")})
PORT = 18900 + SEED % 90
BASE = f"http://127.0.0.1:{PORT}"
G = "g"
TOK = "tok-a"

SCHEMA = """node Item {
  slug: String @key
  src: String
}
"""
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
INS = "query ins($slug: String, $src: String) { insert Item { slug: $slug, src: $src } }"
ALL = "query all() { match { $i: Item } return { $i.slug } }"

failures = []
lock = threading.Lock()
stats = {}


TRAILS = {}


def fail(*a):
    with lock:
        failures.append(a)
    print("FAIL", *a, flush=True)
    if a and isinstance(a[0], int) and a[0] in TRAILS:
        print("  trail", a[0], TRAILS[a[0]][-12:], flush=True)


def stat(k):
    with lock:
        stats[k] = stats.get(k, 0) + 1


def http(method, path, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + TOK)
    req.add_header("Omnigraph-Http-Api", "0.14")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            b = resp.read()
            return resp.status, json.loads(b or b"null")
    except urllib.error.HTTPError as e:
        b = e.read()
        try:
            return e.code, json.loads(b)
        except Exception:
            return e.code, {"raw": b.decode()[:300]}
    except Exception as e:  # connection cut by a kill
        return None, {"exc": repr(e)[:200]}


def code(out):
    return out.get("code") if isinstance(out, dict) else None


def bstate(d):
    if not d:
        return "absent"
    if d.get("retirement_pending"):
        return "retire"
    if "head" not in d:
        return "not_ready"
    return "ready"


def bpath(name):
    return gpath("/branches/" + urllib.parse.quote(name, safe=""))


def gpath(p):
    return f"/graphs/{G}{p}"


def rows_of(branch, commit=None):
    body = {"query": ALL, "params": {}, "branch": branch}
    st, out = http("POST", gpath("/query"), body)
    if st != 200:
        return None, (st, out)
    return {r["i.slug"] if "i.slug" in r else list(r.values())[0] for r in out["rows"]}, None


def branch_list():
    st, out = http("GET", gpath("/branches?detailed=true"))
    if st != 200:
        return None, (st, out)
    return out, None


# ---------------- server ----------------

server = None
srv_lock = threading.Lock()
gen = [0]


def unlock():
    p = subprocess.run([OG, "cluster", "status", "--target", ROOT, "--json"], capture_output=True, text=True)
    try:
        st = json.loads(p.stdout)
    except Exception:
        return

    def find(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "lock_id" and isinstance(v, str):
                    return v
                x = find(v)
                if x:
                    return x
        if isinstance(o, list):
            for v in o:
                x = find(v)
                if x:
                    return x
    lid = find(st)
    if lid:
        subprocess.run([OG, "cluster", "force-unlock", lid, "--target", ROOT, "--yes"], capture_output=True, text=True)


def setup():
    shutil.rmtree(D, ignore_errors=True)
    os.makedirs(D)
    if not ROOT.startswith("s3://"):
        os.makedirs(ROOT)
    open(f"{D}/g.pg", "w").write(SCHEMA)
    open(f"{D}/policy.yaml", "w").write(POLICY)
    open(f"{D}/cluster.yaml", "w").write(
        "version: 1\nmetadata:\n  name: bcat\ngraphs:\n  g:\n    schema: g.pg\n"
        "policies:\n  gp:\n    file: policy.yaml\n    applies_to: [g]\n")
    p = subprocess.run([OG, "cluster", "apply", "--target", ROOT, "--config", D, "--json"], capture_output=True, text=True)
    if p.returncode:
        sys.exit("apply: " + p.stdout[-1500:] + p.stderr[-1500:])
    unlock()


def start():
    global server
    e = dict(os.environ)
    e["OMNIGRAPH_SERVER_BEARER_TOKENS_JSON"] = json.dumps({"act-a": TOK})
    for attempt in range(3):
        server = subprocess.Popen([OGS, "--cluster", ROOT, "--bind", f"127.0.0.1:{PORT}"],
                                  stdout=open(f"{D}/server.log", "a"), stderr=subprocess.STDOUT, env=e)
        for _ in range(240):
            try:
                with urllib.request.urlopen(BASE + "/readyz", timeout=5) as resp:
                    if resp.status == 200:
                        return
            except Exception:
                pass
            if server.poll() is not None:
                break
            time.sleep(0.25)
        if server.poll() is None:
            server.kill()
        tail = open(f"{D}/server.log").read()[-1500:]
        if "state_lock_held" in tail:
            unlock()
            continue
        fail("server did not start", tail)
        return
    fail("server did not start after unlock retries")


def stop(kill=False):
    if kill:
        server.send_signal(signal.SIGKILL)
        server.wait()
        unlock()
    else:
        server.terminate()
        try:
            server.wait(60)
        except Exception:
            server.kill()
            server.wait()


# ---------------- clients ----------------

attempted = set()       # every slug any client sent
main_acked = set()      # rows main must hold
done = threading.Event()


class Client:
    def __init__(self, i):
        self.i = i
        self.r = random.Random(SEED * 1000 + i)
        self.live = {}      # name -> {"life": n, "own": set(acked slugs), "base": set or None, "pending": set()}
        self.deleted_pending = set()  # names whose delete is in doubt
        self.lives = {}
        self.n = 0
        self.trail = []

    def nm(self):
        k = self.r.randrange(4)
        if self.r.random() < 0.3 and self.live:
            par = self.r.choice(sorted(self.live))
            if par.count("/") < 3:
                return par + f"/c{k}"
        return f"w{self.i}/b{k}"

    def slug(self, name):
        self.n += 1
        life = self.live[name]["life"] if name in self.live else 0
        return f"{name}@{life}#{self.n}"

    def settle_branches(self, why):
        """Read back the list after an unknown outcome or a kill; adopt the
        server's view of names whose outcome is in doubt."""
        for _ in range(60):
            out, err = branch_list()
            if out is not None:
                return out
            time.sleep(0.5)
        fail(self.i, "list unavailable", why, err)
        return None

    def mine(self, out):
        names = set(out.get("branches") or [])
        det = {d.get("name"): d for d in out.get("details") or []}
        return {n for n in names if n.startswith(f"w{self.i}/")}, det

    def check_list(self, where):
        out, err = branch_list()
        if out is None:
            if KILL and err[0] in (None, 503):
                return
            fail(self.i, "list", where, err)
            return
        names, det = self.mine(out)
        want = set(self.live)
        if names != want:
            fail(self.i, "branch list", where, "missing", sorted(want - names), "extra", sorted(names - want),
                 {n: det.get(n) for n in sorted(names ^ want)})

    def check_rows(self, name, where):
        got, err = rows_of(name)
        if got is None:
            if KILL and err[0] in (None, 503):
                return
            fail(self.i, "rows", name, where, err)
            return
        b = self.live[name]
        mine = {s for s in got if s.startswith(f"{name}@{b['life']}#")}
        want = {s for s in b["own"]}
        extra = {s for s in mine - want if s not in b["pending"]}
        if want - mine or extra:
            fail(self.i, "branch rows", name, where, "missing", sorted(want - mine)[:5], "extra", sorted(extra)[:5])
        if b["base"] is not None:
            others = got - mine
            if others != b["base"] - mine:
                fail(self.i, "branch base rows", name, where, "missing", sorted((b["base"] - mine) - others)[:5],
                     "extra", sorted(others - b["base"])[:5])
        if got - attempted:
            fail(self.i, "rows nobody attempted", name, sorted(got - attempted)[:5])

    def create(self):
        name = self.nm()
        kind = self.r.random()
        body = {"name": name}
        base = None
        par = name.rsplit("/", 1)[0] if name.count("/") >= 2 else None
        if par and par in self.live:
            body["from"] = par
            got, _ = rows_of(par)
            base = got
        elif kind < 0.15:
            st, out = http("GET", gpath("/commits?branch=main&limit=5"))
            cs = (out or {}).get("commits") if st == 200 and isinstance(out, dict) else None
            if cs:
                body["from_commit"] = self.r.choice(cs).get("graph_commit_id") or cs[0].get("graph_commit_id")
        st, out = http("POST", gpath("/branches"), body)
        stat(("create", st, code(out)))
        self.trail.append(("create", locals().get("name"), st, code(out)))
        if st == 200:
            if name in self.live:
                fail(self.i, "create of a live name answered 200", name)
            self.lives[name] = self.lives.get(name, 0) + 1
            self.live[name] = {"life": self.lives[name], "own": set(), "base": base, "pending": set()}
        elif st is None or st >= 500:
            self.resolve(name, "create", (st, out), body)
        elif st == 409 and code(out) in ("branch_exists", "branch_name_conflict", "branch_prefix_conflict", "branch_not_ready",
                                          "branch_deleted", "conflict"):
            if name not in self.live and code(out) == "branch_exists":
                # a name below or above a live one, or held not_ready: legal only if it is not ours, or tombstoned
                out2, _ = branch_list()
                det = {d.get("name"): d for d in (out2 or {}).get("details") or []}
                if name not in det:
                    pass
        elif st == 400 or st == 404:
            pass
        else:
            stat(("create-other", st, code(out), json.dumps(out)[:150]))

    def resolve(self, name, op, err, body=None):
        out = self.settle_branches(op)
        if out is None:
            return
        names, det = self.mine(out)
        state = bstate(det.get(name))
        if op == "create":
            if name in names:
                if state == "not_ready":
                    st, o = http("DELETE", bpath(name))
                    stat(("not_ready delete", st, code(o)))
                    if st != 200:
                        fail(self.i, "delete of a not_ready branch", name, st, o)
                    return
                self.lives[name] = self.lives.get(name, 0) + 1
                self.live[name] = {"life": self.lives[name], "own": set(), "base": None, "pending": set()}
        elif op == "delete":
            if name not in names or state == "retire":
                if name in names:
                    st, o = http("DELETE", bpath(name))
                    stat(("retire finish", st, code(o)))
                    if st != 200:
                        fail(self.i, "finishing delete", name, st, o)
                self.live.pop(name, None)

    def write(self, name):
        s = self.slug(name)
        with lock:
            attempted.add(s)
        st, out = http("POST", gpath("/mutate"), {"query": INS, "params": {"slug": s, "src": f"w{self.i}"}, "branch": name})
        stat(("write", st, code(out)))
        self.trail.append(("write", locals().get("name"), st, code(out)))
        if st == 200:
            if name == "main":
                with lock:
                    main_acked.add(s)
            else:
                self.live[name]["own"].add(s)
        elif name != "main":
            if st is None or st >= 500:
                self.live[name]["pending"].add(s)
        if st not in (200, None) and not (KILL and st == 503):
            if not (st == 409 and code(out) in ("read_set_conflict", "conflict", "write_conflict")):
                fail(self.i, "write refused", name, st, json.dumps(out)[:300])

    def merge(self, name):
        dele = self.r.random() < 0.4
        before = set(self.live[name]["own"])
        st, out = http("POST", gpath("/branches/merge"), {"source": name, "target": "main", "delete_branch": dele})
        stat(("merge", st, code(out)))
        self.trail.append(("merge", locals().get("name"), st, code(out)))
        if st == 200:
            with lock:
                main_acked.update(before)
            if dele:
                if out.get("branch_deleted") is True:
                    self.live.pop(name, None)
                else:
                    stat(("merge delete not done", json.dumps(out)[:200]))
                    out2 = self.settle_branches("merge-delete")
                    if out2 is not None and name not in self.mine(out2)[0]:
                        self.live.pop(name, None)
        elif st is None or st >= 500:
            # main is checked at the end; settle the source the way a cut delete is settled
            # (absent: gone; retirement pending: finish it), since delete_branch may have run
            self.resolve(name, "delete", (st, out))
        elif st == 409:
            stat(("merge409", (out.get("error") or "")[:90], json.dumps(out.get("disposition"))))
        else:
            fail(self.i, "merge", name, st, json.dumps(out)[:300])

    def delete(self, name):
        st, out = http("DELETE", bpath(name))
        stat(("delete", st, code(out)))
        self.trail.append(("delete", locals().get("name"), st, code(out)))
        if st == 200:
            self.live.pop(name, None)
        elif st is None or st >= 500:
            self.resolve(name, "delete", (st, out))
        else:
            if not (KILL and st == 503):
                fail(self.i, "delete", name, st, json.dumps(out)[:300])

    def run(self):
        TRAILS[self.i] = self.trail
        while not done.is_set():
            x = self.r.random()
            names = sorted(self.live)
            try:
                if x < 0.25 or not names:
                    self.create()
                elif x < 0.55:
                    self.write(self.r.choice(names))
                elif x < 0.62:
                    self.write("main")
                elif x < 0.75:
                    self.merge(self.r.choice(names))
                elif x < 0.88:
                    self.delete(self.r.choice(names))
                else:
                    self.check_rows(self.r.choice(names), "step")
                if not KILL:
                    self.check_list("after step")
            except Exception as e:
                fail(self.i, "client exception", repr(e))
                import traceback
                traceback.print_exc()


def killer():
    r = random.Random(SEED)
    while not done.wait(r.uniform(2, 6)):
        with srv_lock:
            stop(kill=True)
            stat(("kill",))
            start()


def main():
    setup()
    start()
    clients = [Client(i) for i in range(W)]
    ts = [threading.Thread(target=c.run) for c in clients]
    k = threading.Thread(target=killer) if KILL else None
    for t in ts:
        t.start()
    if k:
        k.start()
    time.sleep(SECS)
    done.set()
    for t in ts:
        t.join()
    if k:
        k.join()
    # final settle: every client resolves its view; then restart and recheck
    for phase in ("final", "after restart"):
        if phase == "after restart":
            stop()
            unlock()
            start()
        out, err = branch_list()
        if out is None:
            fail("final list", err)
            break
        for c in clients:
            names, det = c.mine(out)
            if KILL and phase == "final":
                for n in sorted(names - set(c.live)):
                    d = bstate(det.get(n))
                    if d in ("not_ready", "retire"):
                        st, o = http("DELETE", bpath(n))
                        stat(("final cleanup delete", st, code(o)))
                        if st != 200:
                            fail(c.i, "final delete of", n, d, st, o)
                    else:
                        c.live[n] = {"life": -1, "own": set(), "base": None, "pending": set()}
                for n in sorted(set(c.live) - names):
                    c.live.pop(n)
                continue
            c.check_list(phase)
            for n in sorted(c.live):
                c.check_rows(n, phase)
        got, err = rows_of("main")
        if got is None:
            fail("main rows", phase, err)
            continue
        if main_acked - got:
            fail("main lost acknowledged rows", phase, len(main_acked - got), sorted(main_acked - got)[:8])
        if got - attempted:
            fail("main holds rows nobody attempted", phase, sorted(got - attempted)[:5])
        if KILL and phase == "final":
            # a dangling not_ready / retirement left now must be gone, every own name usable again
            for c in clients:
                for n in sorted(c.live)[:2]:
                    st, o = http("DELETE", bpath(n))
                    if st != 200:
                        fail(c.i, "delete after kills", n, st, o)
                    st, o = http("POST", gpath("/branches"), {"name": n})
                    if st != 200:
                        fail(c.i, "create again after kills", n, st, o)
                    else:
                        c.live[n] = {"life": -2, "own": set(), "base": None, "pending": set()}
    stop()
    print(json.dumps({"seed": SEED, "secs": SECS, "kill": KILL, "failures": len(failures),
                      "stats": {repr(k): v for k, v in sorted(stats.items(), key=lambda kv: repr(kv[0]))}}, indent=None))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
