#!/usr/bin/env python3
"""HTTP attribution check for batch commits that hold writes of several actors.

Against a running server, for every commit whose record carries a write list:

- `actor_id` is the common actor, or absent when the writes' actors differ;
- commit get (`GET /commits/{id}/changes`, every page) reports every row
  with a write index and that write's actor, and the rows of each write and
  table number exactly the width of its row-id range;
- every acknowledged write of the history whose receipt names the commit has
  exactly one write entry with its actor and exactly its rows; no entry
  matches only a refused write;
- the change feed (`GET /changes`, from the beginning) reports the same write
  index and actor for each row as commit get.

A write whose receipt names a one-write commit must find its actor in the
commit's `actor_id`.

    attr_http_check.py --base http://127.0.0.1:18080 --graph stress \
        --history h.jsonl [--token tok-0-stress] [--out attr.json]
"""
import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from collections import Counter, defaultdict

HEADERS = {"Omnigraph-Http-Api": "0.14"}


def get(base, path, token, params=None, timeout=300):
    url = f"{base}{path}"
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(url, method="GET")
    for k, v in HEADERS.items():
        req.add_header(k, v)
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def inserted_rows(op):
    kind = op.get("kind")
    rows = defaultdict(list)
    if kind in ("fresh", "hot", "pair"):
        rows["node:Item"] = list(op.get("items") or [])
    elif kind in ("multi", "fmulti"):
        rows["node:Item"] = list(op.get("items") or [])
        if op.get("person"):
            rows["node:Person"] = [op["person"]]
    elif kind == "person" and op.get("person"):
        rows["node:Person"] = [op["person"]]
    elif kind in ("knows", "sknows") and op.get("edge"):
        rows["edge:Knows"] = [f"{op['edge'][0]}->{op['edge'][1]}"]
    elif kind == "tag" and op.get("tag"):
        rows["node:Tag"] = [op["tag"][0]]
    return {k: sorted(v) for k, v in rows.items()}


def change_row(change):
    kind = change.get("kind")
    tname = (change.get("type") or {}).get("name")
    table = f"{kind}:{tname}"
    after = change.get("after") or {}
    if kind == "edge":
        ends = after.get("endpoints") or {}
        sig = f"{ends.get('from')}->{ends.get('to')}"
    else:
        props = after.get("properties") or {}
        sig = props.get("slug") or props.get("name") or change.get("id")
    return table, sig, change.get("write"), change.get("actor_id")


def all_commits(base, graph, token):
    out, token_page = [], None
    while True:
        body = get(base, f"/graphs/{graph}/commits", token, {"limit": 1000, "page_token": token_page})
        out.extend(body.get("commits", []))
        token_page = body.get("next_page_token")
        if not token_page:
            return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base", required=True)
    p.add_argument("--graph", default="stress")
    p.add_argument("--token", default="tok-0-stress")
    p.add_argument("--history", nargs="+", required=True)
    p.add_argument("--out")
    p.add_argument("--export", help="final export (JSONL): rows of unpublished in-doubt batches must be absent")
    args = p.parse_args()
    ops = []
    for path in args.history:
        for line in open(path):
            d = json.loads(line)
            if d.get("type") == "op" and d.get("outcome"):
                ops.append(d)
    v = []
    commits = all_commits(args.base, args.graph, args.token)
    by_id = {c["graph_commit_id"]: c for c in commits}
    acked = defaultdict(list)
    for op in ops:
        if op["outcome"] != "ok" or not op.get("commit"):
            continue
        c = by_id.get(op["commit"])
        if c is None:
            v.append(f"op {op['id']}: receipt commit {op['commit']} not listed")
        elif not c.get("writes"):
            if c.get("actor_id") != op["actor"]:
                v.append(f"op {op['id']}: actor {op['actor']} one-write commit records {c.get('actor_id')}")
        else:
            acked[op["commit"]].append(op)
    doubtful = [op for op in ops if op["outcome"] == "in_doubt"]
    refused = [op for op in ops if op["outcome"] == "refused"]
    batch = [c for c in commits if c.get("writes")]
    commit_get = {}
    actors_hist = Counter()
    for c in batch:
        cid = c["graph_commit_id"]
        writes = c["writes"]
        actors = {w.get("actor_id") for w in writes}
        actors_hist[len(actors)] += 1
        want = writes[0].get("actor_id") if len(actors) == 1 else None
        if c.get("actor_id") != want:
            v.append(f"commit {cid}: actor_id {c.get('actor_id')} want {want}")
        rows, page = [], None
        while True:
            body = get(args.base, f"/graphs/{args.graph}/commits/{cid}/changes", args.token, {"page_token": page})
            rows.extend(change_row(ch) for ch in body.get("changes", []))
            page = body.get("next_page_token")
            if not page:
                break
        groups = defaultdict(list)
        for table, sig, wi, actor in rows:
            if wi is None or wi >= len(writes):
                v.append(f"commit {cid}: {table} {sig} has write {wi}")
                continue
            if actor != writes[wi].get("actor_id"):
                v.append(f"commit {cid}: {table} {sig} actor {actor} but write {wi} is {writes[wi].get('actor_id')}")
            groups[(wi, table)].append(sig)
        entry_rows = []
        for wi, w in enumerate(writes):
            er = {}
            for r in w["rows"]:
                got = len(groups.get((wi, r["table_key"]), []))
                if got != r["end"] - r["start"]:
                    v.append(f"commit {cid} write {wi} {r['table_key']}: range width {r['end'] - r['start']}, {got} rows")
                er[r["table_key"]] = sorted(groups.get((wi, r["table_key"]), []))
            entry_rows.append(er)
        taken = [False] * len(writes)
        for op in acked.get(cid, []):
            want_rows = inserted_rows(op)
            hit = next((wi for wi in range(len(writes))
                        if not taken[wi] and writes[wi].get("actor_id") == op["actor"] and entry_rows[wi] == want_rows), None)
            if hit is None:
                v.append(f"op {op['id']} ({op['actor']}, {want_rows}): no write of {cid} with its actor and rows")
            else:
                taken[hit] = True
        for wi in range(len(writes)):
            if taken[wi]:
                continue
            actor = writes[wi].get("actor_id")
            if not any(op["actor"] == actor and inserted_rows(op) == entry_rows[wi] for op in doubtful):
                ref = any(op["actor"] == actor and inserted_rows(op) == entry_rows[wi] for op in refused)
                v.append(f"commit {cid} write {wi} ({actor}, {entry_rows[wi]}) matches no acknowledged or in-doubt write"
                         + (" (matches a refused write)" if ref else ""))
        commit_get[cid] = sorted(rows, key=lambda r: tuple("" if x is None else str(x) for x in r))
    # The change feed.
    feed = defaultdict(list)
    params = {"start": "beginning"}
    polls = 0
    while True:
        polls += 1
        body = get(args.base, f"/graphs/{args.graph}/changes", args.token, params)
        for block in body.get("blocks", []):
            cid = block["cause"]["graph_commit_id"]
            feed[cid].extend(change_row(ch) for ch in block.get("changes", []))
        if body.get("next_page_token"):
            params = {"page_token": body["next_page_token"]}
        elif body.get("cursor") and not body.get("caught_up"):
            params = {"cursor": body["cursor"]}
        else:
            break
    # In-doubt writes name their batch's attempted commit (H7): a published
    # attempt holds each of them with its actor and rows; an unpublished one
    # left none of their rows.
    commit_re = re.compile(r"hb1\.[0-9A-Z]{26}\.\d+\.[0-9A-Z]{26}")
    present = None
    if args.export:
        present = defaultdict(set)
        for line in open(args.export):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            data = d.get("data") or {}
            if d.get("type") == "Item":
                present["node:Item"].add(data.get("slug"))
            elif d.get("type") == "Person":
                present["node:Person"].add(data.get("name"))
    attempts = defaultdict(list)
    for op in doubtful:
        found = commit_re.findall(op.get("error") or "")
        if found:
            attempts[found[0]].append(op)
    landed = lost = 0
    for attempt, members in attempts.items():
        c = by_id.get(attempt)
        codes = {(op.get("code"), op.get("effects")) for op in members}
        if len(codes) > 1:
            v.append(f"attempt {attempt}: members got different outcomes {codes}")
        if c is not None:
            landed += 1
            entries = [(w.get("actor_id"), r) for w, r in zip(c.get("writes") or [], [None] * 10**6)]
            rows_by_entry = []
            got = defaultdict(list)
            for table, sig, wi, actor in commit_get.get(attempt, []):
                got[(wi, table)].append(sig)
            for wi, w in enumerate(c.get("writes") or []):
                rows_by_entry.append((w.get("actor_id"), {r["table_key"]: sorted(got.get((wi, r["table_key"]), [])) for r in w["rows"]}))
            for op in members:
                want = inserted_rows(op)
                if c.get("writes"):
                    if (op["actor"], want) not in rows_by_entry:
                        v.append(f"op {op['id']} in doubt on published {attempt}: no entry with its actor and rows")
                elif c.get("actor_id") != op["actor"]:
                    v.append(f"op {op['id']} in doubt on published one-write {attempt}: actor {c.get('actor_id')}")
        else:
            lost += 1
            if present is not None:
                for op in members:
                    for table, sigs in inserted_rows(op).items():
                        for sig in sigs:
                            if table in present and sig in present[table] and sig.startswith(("f", "q")):
                                v.append(f"op {op['id']} in doubt on unpublished {attempt}: its row {sig} exists")
    for cid, want in commit_get.items():
        got = sorted(feed.get(cid, []), key=lambda r: tuple("" if x is None else str(x) for x in r))
        if got != want:
            v.append(f"commit {cid}: feed {len(got)} rows vs commit get {len(want)}")
    summary = {"commits": len(commits), "batch_commits": len(batch),
               "mixed_actor_commits": sum(n for k, n in actors_hist.items() if k > 1),
               "actors_per_batch_commit": dict(sorted(actors_hist.items())),
               "entries": sum(len(c["writes"]) for c in batch),
               "batch_rows": sum(len(r) for r in commit_get.values()),
               "feed_polls": polls, "in_doubt_attempts": len(attempts),
               "in_doubt_attempts_published": landed, "in_doubt_attempts_lost": lost,
               "in_doubt_without_attempt": sum(1 for op in doubtful if not commit_re.findall(op.get("error") or "")),
               "violations": len(v)}
    print(json.dumps(summary))
    for line in v[:40]:
        print("VIOLATION", line)
    if args.out:
        with open(args.out, "w") as f:
            json.dump({"summary": summary, "violations": v}, f, indent=1)
    return 1 if v else 0


if __name__ == "__main__":
    sys.exit(main())
