#!/usr/bin/env python3
"""HTTP stress driver and checker for group commit on a served graph.

Drives many concurrent writers (closed loop) against one omnigraph-server
graph, with several actors (bearer tokens), optional client-side request
cancellation (the connection is closed mid-request; the server owns the
write, so its outcome is unknown to the client), readers that record the
exact commit each read was served from, and a per-second metrics line.
Every invocation and completion is recorded to a JSONL history.

    http_stress.py run --base http://127.0.0.1:18080 --graph stress \
        --writers 64 --secs 60 --actors 4 --out h.jsonl
    http_stress.py check --history h.jsonl --export export.jsonl --commits commits.json

The checker verifies: no duplicate keys, no dangling edges, @unique held;
every acknowledged receipt is in the commit list with the write's actor;
main's lineage is one chain; a model replay of acknowledged writes in
version order equals the final export and every reader observation, except
keys an in-doubt write touched; no two writes of one commit share a key.
"""
import argparse
import asyncio
import json
import os
import random
import sys
import time
from collections import Counter, defaultdict

QUERIES = {
    "ins": "query ins($slug: String, $n: I64) { insert Item { slug: $slug, n: $n } }",
    "ins2": "query ins2($a: String, $b: String, $n: I64) { insert Item { slug: $a, n: $n } insert Item { slug: $b, n: $n } }",
    "upd": "query upd($slug: String, $n: I64) { update Item set { n: $n } where slug = $slug }",
    "del": "query del($slug: String) { delete Item where slug = $slug }",
    "person": "query person($name: String) { insert Person { name: $name } }",
    "del_person": "query del_person($name: String) { delete Person where name = $name }",
    "knows": "query knows($from: String, $to: String) { insert Knows { from: $from, to: $to } }",
    "multi": "query multi($slug: String, $n: I64, $name: String) { insert Item { slug: $slug, n: $n } insert Person { name: $name } }",
    "tag": "query tag($slug: String, $label: String) { insert Tag { slug: $slug, label: $label } }",
    "follow": "query follow($from: String, $to: String) { insert Follows { from: $from, to: $to } }",
    "pair": "query pair($k: String, $a: String, $b: I64) { insert Pair { k: $k, a: $a, b: $b } }",
    "pair_null": "query pair_null($k: String, $a: String) { insert Pair { k: $k, a: $a } }",
}
# Key-lookup stress (research/key-lookup-stress): kinds whose refusal is
# predictable, so a refusal of a write that must succeed is counted as false
# and an acceptance of a write that must fail is a violation.
#   ftag     fresh slug, fresh @unique label           must succeed
#   dtag     fresh slug, a label the seed holds        must be refused
#   ffollow  an owned source's 1st..3rd edge (@card 0..3) to a seeded q*
#            person, created first                     must succeed
#   xfollow  an owned source's 4th edge                must be refused
#   fpair    fresh composite (a, b) tuple              must succeed
#   npair    (a, null): a tuple with a null is no key  must succeed
#   dpair    a seeded (a, b) tuple                     must be refused
#   fresh    a fresh @key item                         must succeed
READ_ITEMS = "query all_items() { match { $i: Item } return { $i.slug as slug, $i.n as n } }"
READ_COUNT = "query cnt() { match { $i: Item } return { count($i) as c } }"
DEFAULT_MIX = "fresh=40,hot=20,pair=5,update=8,delete=5,person=5,del_person=2,knows=8,multi=4,tag=3"
HEADERS = {"Omnigraph-Http-Api": "0.14", "Content-Type": "application/json"}


def parse_mix(spec):
    out = []
    for part in spec.split(","):
        if part:
            name, weight = part.split("=")
            out.append((name, int(weight)))
    return out


def pick(mix, rng):
    total = sum(w for _, w in mix)
    roll = rng.randrange(total)
    for name, weight in mix:
        if roll < weight:
            return name
        roll -= weight
    return mix[0][0]


class Recorder:
    def __init__(self, path):
        self.file = open(path, "a", buffering=1)
        self.start = time.monotonic()
        self.next_id = 0
        self.window = Counter()
        self.lat = []

    def now_us(self):
        return int((time.monotonic() - self.start) * 1e6)

    def write(self, record):
        self.file.write(json.dumps(record) + "\n")


async def writer_loop(session, args, rec, writer, deadline, tokens, stats):
    import aiohttp

    rng = random.Random(args.seed * 100003 + writer)
    actor = f"act-{writer % args.actors}"
    token = tokens[actor]
    seq = 0
    mix = parse_mix(args.mix)
    owned = None  # (person, acknowledged edges, in-doubt edges)
    url = f"{args.base}/graphs/{args.graph}/mutate"
    headers = dict(HEADERS, Authorization=f"Bearer {token}")
    while time.monotonic() < deadline:
        seq += 1
        kind = pick(mix, rng)
        n = writer * 1_000_000 + seq
        fresh = f"f{args.run_tag}{writer}-{seq}"
        hot = f"h{rng.randrange(args.hot)}"
        pa = f"p{rng.randrange(args.people)}"
        pb = f"p{rng.randrange(args.people)}"
        op = {"type": "op", "id": rec.next_id, "writer": writer, "actor": actor, "kind": kind,
              "items": [], "person": None, "edge": None, "n": n}
        rec.next_id += 1
        if kind == "fresh":
            name, params = "ins", {"slug": fresh, "n": n}; op["items"] = [fresh]
        elif kind == "hot":
            name, params = "ins", {"slug": hot, "n": n}; op["items"] = [hot]
        elif kind == "pair":
            name, params = "ins2", {"a": hot, "b": fresh, "n": n}; op["items"] = [hot, fresh]
        elif kind == "update":
            name, params = "upd", {"slug": hot, "n": n}; op["items"] = [hot]
        elif kind == "delete":
            name, params = "del", {"slug": hot}; op["items"] = [hot]
        elif kind == "person":
            name, params = "person", {"name": pa}; op["person"] = pa
        elif kind == "del_person":
            name, params = "del_person", {"name": pa}; op["person"] = pa
        elif kind == "knows":
            name, params = "knows", {"from": pa, "to": pb}; op["edge"] = [pa, pb]
        elif kind == "multi":
            name, params = "multi", {"slug": fresh, "n": n, "name": pa}; op["items"] = [fresh]; op["person"] = pa
        elif kind == "ftag":
            label = f"fl{args.run_tag}{writer}-{seq}"
            name, params = "tag", {"slug": f"t{args.run_tag}{writer}-{seq}", "label": label}
            op["tag"] = [params["slug"], label]; op["expect"] = "ok"
        elif kind == "dtag":
            label = f"seed-l{rng.randrange(args.seed_labels)}"
            name, params = "tag", {"slug": f"t{args.run_tag}{writer}-{seq}", "label": label}
            op["tag"] = [params["slug"], label]; op["expect"] = "refused"
        elif kind in ("ffollow", "xfollow"):
            if owned is None or owned[1] + owned[2] >= 4 or (owned[1] + owned[2] >= 3 and owned[2] > 0):
                person = f"o{args.run_tag}{writer}-{seq}"
                owned = [person, 0, 0, False]
                name, params = "person", {"name": person}; op["person"] = person
                op["kind"] = kind = "person"; op["expect"] = "ok"
            elif owned[1] + owned[2] < 3:
                kind = op["kind"] = "ffollow"
                to = f"q{rng.randrange(args.seed_people)}"
                name, params = "follow", {"from": owned[0], "to": to}
                op["follow"] = [owned[0], to]; op["expect"] = "ok"
            else:
                kind = op["kind"] = "xfollow"
                to = f"q{rng.randrange(args.seed_people)}"
                name, params = "follow", {"from": owned[0], "to": to}
                op["follow"] = [owned[0], to]; op["expect"] = "refused"
        elif kind == "fpair":
            name, params = "pair", {"k": f"k{args.run_tag}{writer}-{seq}", "a": f"fa{args.run_tag}{writer}-{seq}", "b": seq}
            op["pair"] = [params["k"], params["a"], seq]; op["expect"] = "ok"
        elif kind == "npair":
            name, params = "pair_null", {"k": f"k{args.run_tag}{writer}-{seq}", "a": "shared"}
            op["pair"] = [params["k"], "shared", None]; op["expect"] = "ok"
        elif kind == "dpair":
            i = rng.randrange(args.seed_labels)
            name, params = "pair", {"k": f"k{args.run_tag}{writer}-{seq}", "a": f"sa{i}", "b": i}
            op["pair"] = [params["k"], f"sa{i}", i]; op["expect"] = "refused"
        else:
            label = f"l{rng.randrange(args.hot)}"
            name, params = "tag", {"slug": f"t{args.run_tag}{writer}-{seq}", "label": label}
            op["tag"] = [params["slug"], label]
        if kind == "fresh":
            op["expect"] = "ok"
        body = {"query": QUERIES[name], "name": name, "params": params, "branch": "main"}
        cancel = args.cancel_pct > 0 and rng.randrange(100) < args.cancel_pct
        budget = rng.uniform(0.001, args.cancel_ms / 1000.0) if cancel else args.request_timeout
        op["invoked_us"] = rec.now_us()
        try:
            async with asyncio.timeout(budget):
                async with session.post(url, json=body, headers=headers) as resp:
                    text = await resp.text()
                    status = resp.status
            op["completed_us"] = rec.now_us()
            op["status"] = status
            if status == 429:
                # Server admission refused before any effect: back off, and
                # count it rather than record it.
                stats["throttled"] += 1
                await asyncio.sleep(rng.uniform(0.02, 0.2))
                continue
            if status == 200:
                data = json.loads(text)
                commit = data.get("commit") or {}
                op["outcome"] = "ok"
                op["version"] = commit.get("graph_manifest_version")
                op["commit"] = commit.get("graph_commit_id")
                op["parent"] = commit.get("parent_commit_id")
                op["commit_actor"] = commit.get("actor_id")
                op["affected"] = [data.get("affected_nodes"), data.get("affected_edges")]
            else:
                try:
                    data = json.loads(text)
                except ValueError:
                    data = {"error": text[:300]}
                disp = data.get("disposition") or {}
                op["code"] = data.get("code")
                op["error"] = (data.get("error") or "")[:300]
                op["effects"] = disp.get("effects")
                op["outcome"] = "refused" if disp.get("effects") == "none" else "in_doubt"
        except TimeoutError:
            op["completed_us"] = rec.now_us()
            op["outcome"] = "in_doubt"
            op["error"] = "client cancelled" if cancel else "client timeout"
        except aiohttp.ClientConnectorError as error:
            # The connection was never made: the request was not sent.
            op["completed_us"] = rec.now_us()
            op["outcome"] = "refused"
            op["error"] = f"connect: {error}"[:200]
            await asyncio.sleep(0.2)
        except (aiohttp.ClientError, ConnectionError, OSError) as error:
            op["completed_us"] = rec.now_us()
            op["outcome"] = "in_doubt"
            op["error"] = f"{type(error).__name__}: {error}"[:200]
            await asyncio.sleep(0.2)
        if kind == "ffollow" and owned is not None:
            if op["outcome"] == "ok":
                owned[1] += 1
            elif op["outcome"] == "in_doubt":
                owned[2] += 1
        if kind == "person" and owned is not None and op["outcome"] != "ok":
            owned = None
        if kind == "xfollow":
            owned = None
        rec.write(op)
        stats[op["outcome"]] += 1
        if op["outcome"] == "ok":
            rec.lat.append((op["completed_us"] - op["invoked_us"]) / 1000.0)


async def reader_loop(session, args, rec, reader, deadline, tokens):
    url = f"{args.base}/graphs/{args.graph}/query"
    token = tokens[f"act-{reader % args.actors}"]
    headers = dict(HEADERS, Authorization=f"Bearer {token}")
    if args.reader_count:
        body = {"query": READ_COUNT, "name": "cnt"}
    else:
        body = {"query": READ_ITEMS, "name": "all_items"}
    while time.monotonic() < deadline:
        try:
            async with asyncio.timeout(30):
                async with session.post(url, json=body, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if args.reader_count:
                            rows = data.get("rows", [])
                            count = rows[0]["c"] if rows else 0
                            rec.write({"type": "obs_count", "t_us": rec.now_us(),
                                       "commit": data.get("graph_commit_id"), "count": count})
                        else:
                            items = defaultdict(list)
                            for row in data.get("rows", []):
                                items[row["slug"]].append(row["n"])
                            rec.write({"type": "obs", "t_us": rec.now_us(), "commit": data.get("graph_commit_id"),
                                       "items": items})
        except Exception:  # noqa: BLE001 - readers tolerate outages
            await asyncio.sleep(0.5)
        await asyncio.sleep(args.read_interval)


async def metrics_loop(args, rec, deadline, stats, server_pid):
    import psutil

    proc = None
    if server_pid:
        try:
            proc = psutil.Process(server_pid)
        except Exception:  # noqa: BLE001
            proc = None
    last = Counter(stats)
    last_t = time.monotonic()
    while time.monotonic() < deadline:
        await asyncio.sleep(args.metrics_secs)
        now = time.monotonic()
        snap = Counter({k: v for k, v in stats.items() if isinstance(v, int)})
        delta = {k: snap[k] - last.get(k, 0) for k in ("ok", "refused", "in_doubt")}
        lat = sorted(rec.lat)
        rec.lat.clear()
        p = lambda q: lat[min(len(lat) - 1, int(q * len(lat)))] if lat else 0  # noqa: E731
        rss = None
        cpu = None
        if proc is not None:
            try:
                rss = proc.memory_info().rss / 1e6
                cpu = proc.cpu_percent(None)
            except Exception:  # noqa: BLE001
                rss = None
        throttled = snap["throttled"] - last.get("throttled", 0)
        line = {"type": "metrics", "t_s": round(now - rec.start, 1),
                "ok_per_s": round(delta["ok"] / (now - last_t), 2), "throttled": throttled,
                "refused": delta["refused"], "in_doubt": delta["in_doubt"],
                "p50_ms": round(p(0.5), 1), "p99_ms": round(p(0.99), 1), "max_ms": round(lat[-1], 1) if lat else 0,
                "server_rss_mb": rss, "server_cpu_pct": cpu}
        rec.write(line)
        print("[metrics]", json.dumps(line), flush=True)
        last = snap
        last_t = now


async def run(args):
    import aiohttp

    tokens = json.load(open(args.tokens))
    rec = Recorder(args.out)
    rec.write({"type": "config", "args": vars(args)})
    deadline = time.monotonic() + args.secs
    stats = Counter()
    connector = aiohttp.TCPConnector(limit=0, force_close=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [asyncio.create_task(writer_loop(session, args, rec, w, deadline, tokens, stats))
                 for w in range(args.writers)]
        tasks += [asyncio.create_task(reader_loop(session, args, rec, r, deadline, tokens))
                  for r in range(args.readers)]
        tasks.append(asyncio.create_task(metrics_loop(args, rec, deadline, stats, args.server_pid)))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                print("task failed:", repr(result), file=sys.stderr)
    summary = {"type": "run_summary", "ok": stats["ok"], "refused": stats["refused"], "throttled": stats["throttled"],
               "in_doubt": stats["in_doubt"], "secs": args.secs}
    rec.write(summary)
    print("[run]", json.dumps(summary), flush=True)


# ---------------------------------------------------------------- checker


def load_history(paths):
    ops, obs, merges = [], [], []
    for path in paths:
        for line in open(path):
            d = json.loads(line)
            if d.get("type") == "op":
                ops.append(d)
            elif d.get("type") == "obs":
                obs.append(d)
            elif d.get("type") == "merge":
                merges.append(d)
            elif d.get("type") == "obs_count":
                obs.append(d)
    return ops, obs, merges


FOLLOWS = []
PAIRS = []


def load_export(path):
    items, people, edges, tags = defaultdict(list), Counter(), [], []
    FOLLOWS.clear()
    PAIRS.clear()
    for line in open(path):
        d = json.loads(line)
        if "edge" in d:
            if d["edge"] == "Knows":
                edges.append((d["from"], d["to"]))
            elif d["edge"] == "Follows":
                FOLLOWS.append((d["from"], d["to"]))
        elif d.get("type") == "Pair":
            PAIRS.append((d["data"]["k"], d["data"].get("a"), d["data"].get("b")))
        elif d.get("type") == "Item":
            items[d["data"]["slug"]].append(d["data"]["n"])
        elif d.get("type") == "Person":
            people[d["data"]["name"]] += 1
        elif d.get("type") == "Tag":
            tags.append((d["data"]["slug"], d["data"]["label"]))
    return items, people, edges, tags


def apply(model, op):
    items, people, edges = model
    kind = op["kind"]
    if kind in ("fresh", "hot", "pair", "multi"):
        for key in op["items"]:
            items[key] = op["n"]
        if kind == "multi" and op.get("person"):
            people.add(op["person"])
    elif kind == "update":
        for key in op["items"]:
            if key in items:
                items[key] = op["n"]
    elif kind == "delete":
        for key in op["items"]:
            items.pop(key, None)
    elif kind == "person":
        people.add(op["person"])
    elif kind == "del_person":
        people.discard(op["person"])
        for pair in [p for p in edges if op["person"] in p]:
            del edges[pair]
    elif kind == "knows":
        edges[tuple(op["edge"])] += 1


FALSE = {}


def check(args):
    ops, obs, merges = load_history(args.history)
    violations = []
    commits = json.load(open(args.commits))["commits"]
    by_id = {c["graph_commit_id"]: c for c in commits}
    version_of = {c["graph_commit_id"]: c["graph_manifest_version"] for c in commits}
    ordered = sorted(commits, key=lambda c: c["graph_manifest_version"])
    for a, b in zip(ordered, ordered[1:]):
        if b.get("parent_commit_id") != a["graph_commit_id"]:
            violations.append(f"lineage: v{b['graph_manifest_version']} parent {b.get('parent_commit_id')} is not v{a['graph_manifest_version']}")
    per_commit = Counter()
    for op in ops:
        if op["outcome"] != "ok" or not op.get("commit"):
            continue
        per_commit[op["commit"]] += 1
        c = by_id.get(op["commit"])
        if c is None:
            violations.append(f"op {op['id']}: receipt commit {op['commit']} not in the commit list")
            continue
        if c["graph_manifest_version"] != op.get("version"):
            violations.append(f"op {op['id']}: receipt v{op.get('version')} commit is v{c['graph_manifest_version']}")
        if c.get("actor_id") != op["actor"]:
            violations.append(f"op {op['id']}: actor {op['actor']} commit actor {c.get('actor_id')}")
    uncertain_items, uncertain_people = set(), set()
    excluded = tuple(args.exclude_prefix or ())
    for op in ops:
        if op["outcome"] == "in_doubt":
            uncertain_items.update(op["items"])
            if op.get("person"):
                uncertain_people.add(op["person"])
            if op.get("edge"):
                uncertain_people.update(op["edge"])
    by_ver = defaultdict(list)
    for op in ops:
        if op["outcome"] == "ok" and op.get("version") is not None and op.get("commit"):
            by_ver[op["version"]].append(op)
    for version, batch in by_ver.items():
        seen = {}
        for op in batch:
            for key in op["items"]:
                if key in seen:
                    violations.append(f"v{version}: ops {seen[key]} and {op['id']} share key {key}")
                seen[key] = op["id"]

    def compare(model, items_seen, label, people_seen=None, edges_seen=None):
        diffs = []
        m_items, m_people, m_edges = model
        for key in set(m_items) | set(items_seen):
            if key in uncertain_items or key.startswith("seed") or (excluded and key.startswith(excluded)):
                continue
            want = m_items.get(key)
            got = items_seen.get(key)
            got = got[0] if got else None
            if want != got:
                diffs.append(f"{key}: model {want} got {got}")
        if people_seen is not None:
            for person in set(m_people) | set(people_seen):
                if person in uncertain_people or (excluded and person.startswith(excluded)):
                    continue
                if (person in m_people) != (person in people_seen):
                    diffs.append(f"person {person}: model {person in m_people} got {person in people_seen}")
        if edges_seen is not None:
            got_edges = Counter(edges_seen)
            for pair in set(m_edges) | set(got_edges):
                if pair[0] in uncertain_people or pair[1] in uncertain_people or (
                        excluded and (pair[0].startswith(excluded) or pair[1].startswith(excluded))):
                    continue
                if m_edges.get(pair, 0) != got_edges.get(pair, 0):
                    diffs.append(f"edge {pair}: model {m_edges.get(pair, 0)} got {got_edges.get(pair, 0)}")
        if diffs:
            violations.append(f"{label}: {len(diffs)} differences, first {diffs[:6]}")

    def args_key_filter(key):
        return {key}

    # Invariants on the final export.
    items, people, edges, tags = load_export(args.export)
    for key, values in items.items():
        if len(values) > 1:
            violations.append(f"final: duplicate Item {key} x{len(values)} {values}")
    for name, count in people.items():
        if count > 1:
            violations.append(f"final: duplicate Person {name} x{count}")
    for src, dst in edges:
        if src not in people or dst not in people:
            violations.append(f"final: dangling edge {src}->{dst}")
    labels = Counter(label for _, label in tags)
    for label, count in labels.items():
        if count > 1:
            violations.append(f"final: @unique Tag label {label} x{count}")
    degree = Counter(src for src, _ in FOLLOWS)
    for src, count in degree.items():
        if count > 3:
            violations.append(f"final: @card(0..3) Follows from {src} x{count}")
    for src, dst in FOLLOWS:
        if src not in people or dst not in people:
            violations.append(f"final: dangling Follows {src}->{dst}")
    tuples = Counter((a, b) for _, a, b in PAIRS if a is not None and b is not None)
    for key, count in tuples.items():
        if count > 1:
            violations.append(f"final: @unique Pair (a, b) {key} x{count}")
    # Predictable outcomes: a refusal of a write that must succeed is a false
    # refusal (KeyConflict, uniqueness or @card refused against a key the
    # committed state did not hold); an acceptance of a write that must fail
    # is a violation.
    false_refusals = Counter()
    false_accepts = Counter()
    expected_refusals = Counter()
    tag_slugs = {slug for slug, _ in tags}
    pair_keys = {k for k, _, _ in PAIRS}
    for op in ops:
        expect = op.get("expect")
        if expect == "ok" and op["outcome"] == "refused":
            false_refusals[(op["kind"], op.get("code"), (op.get("error") or "")[:60])] += 1
        elif expect == "refused" and op["outcome"] == "ok":
            false_accepts[op["kind"]] += 1
            violations.append(f"op {op['id']} {op['kind']} must be refused but was accepted")
        elif expect == "refused" and op["outcome"] == "refused":
            expected_refusals[op["kind"]] += 1
        if op.get("tag") and op["outcome"] == "ok" and op["tag"][0] not in tag_slugs:
            violations.append(f"op {op['id']}: acknowledged Tag {op['tag'][0]} missing from the export")
        if op.get("pair") and op["outcome"] == "ok" and op["pair"][0] not in pair_keys:
            violations.append(f"op {op['id']}: acknowledged Pair {op['pair'][0]} missing from the export")
    for key, count in false_refusals.items():
        violations.append(f"false refusal x{count}: {key}")
    FALSE.update({"false_refusals": sum(false_refusals.values()),
                  "false_accepts": sum(false_accepts.values()),
                  "expected_refusals": dict(expected_refusals)})

    def check_obs(model, v, o):
        if "count" in o:
            # Batch atomicity on counts: the served count must equal the
            # model's at that version, within the keys in-doubt writes touched.
            certain = sum(1 for key in model[0] if key not in uncertain_items)
            slack = sum(1 for key in uncertain_items if not key.startswith("h"))
            if not (certain <= o["count"] <= certain + slack + len([k for k in model[0] if k in uncertain_items])):
                violations.append(f"snapshot v{v}: count {o['count']} outside model [{certain}, {certain + slack}]")
            return
        for key, values in o["items"].items():
            if len(values) > 1:
                violations.append(f"snapshot v{v}: duplicate Item {key} x{len(values)}")
        compare(model, o["items"], f"snapshot v{v}")

    # Model replay with reader observations in version order.
    seed = {}
    if args.seed_export:
        s_items, s_people, _, _ = load_export(args.seed_export)
        seed = {"items": {k: v[0] for k, v in s_items.items()}, "people": set(s_people)}
    model = (dict(seed.get("items", {})), set(seed.get("people", set())), Counter())
    observations = []
    for o in obs:
        v = version_of.get(o["commit"])
        if v is None:
            violations.append(f"observation at {o['commit']} names no listed commit")
            continue
        observations.append((v, o))
    observations.sort(key=lambda x: x[0])
    versions = sorted(by_ver)
    oi = 0
    for version in versions:
        while oi < len(observations) and observations[oi][0] < version:
            v, o = observations[oi]
            check_obs(model, v, o)
            oi += 1
        for op in by_ver[version]:
            apply(model, op)
    for v, o in observations[oi:]:
        check_obs(model, v, o)
    compare(model, items, "final", people, edges)
    lat = sorted((op["completed_us"] - op["invoked_us"]) / 1000.0 for op in ops if op["outcome"] == "ok")
    summary = {
        "ops": len(ops), "ok": sum(op["outcome"] == "ok" for op in ops),
        "refused": sum(op["outcome"] == "refused" for op in ops),
        "in_doubt": sum(op["outcome"] == "in_doubt" for op in ops),
        "commits_with_writes": len(per_commit),
        "writes_per_commit": round(sum(per_commit.values()) / max(1, len(per_commit)), 2),
        "max_batch": max(per_commit.values()) if per_commit else 0,
        "observations": len(observations),
        "p50_ms": lat[len(lat) // 2] if lat else 0, "p99_ms": lat[int(len(lat) * 0.99)] if lat else 0,
        "errors": Counter((op.get("code"), (op.get("error") or "")[:80]) for op in ops if op["outcome"] != "ok").most_common(8),
        "violations": len(violations),
        **FALSE,
    }
    print(json.dumps(summary, default=str))
    for v in violations[:40]:
        print("VIOLATION", v)
    return 1 if violations else 0


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--base", default="http://127.0.0.1:18080")
    r.add_argument("--graph", default="stress")
    r.add_argument("--tokens", default=os.path.join(os.path.dirname(__file__), "..", "cluster", "tokens.json"))
    r.add_argument("--writers", type=int, default=32)
    r.add_argument("--secs", type=float, default=30)
    r.add_argument("--actors", type=int, default=1)
    r.add_argument("--seed", type=int, default=1)
    r.add_argument("--mix", default=DEFAULT_MIX)
    r.add_argument("--hot", type=int, default=64)
    r.add_argument("--people", type=int, default=24)
    r.add_argument("--seed-people", type=int, default=64)
    r.add_argument("--seed-labels", type=int, default=32)
    r.add_argument("--readers", type=int, default=2)
    r.add_argument("--read-interval", type=float, default=0.25)
    r.add_argument("--reader-count", type=int, default=0, help="readers read count(Item) instead of every row")
    r.add_argument("--cancel-pct", type=int, default=0)
    r.add_argument("--cancel-ms", type=int, default=1000)
    r.add_argument("--request-timeout", type=float, default=120)
    r.add_argument("--metrics-secs", type=float, default=10)
    r.add_argument("--server-pid", type=int, default=0)
    r.add_argument("--run-tag", default="")
    r.add_argument("--out", required=True)
    c = sub.add_parser("check")
    c.add_argument("--history", nargs="+", required=True)
    c.add_argument("--export", required=True)
    c.add_argument("--seed-export")
    c.add_argument("--commits", required=True)
    c.add_argument("--exclude-prefix", action="append", default=[],
                   help="keys and people with this prefix are not compared (a killed writer's)")
    args = parser.parse_args()
    if args.cmd == "run":
        asyncio.run(run(args))
    else:
        sys.exit(check(args))


if __name__ == "__main__":
    main()
