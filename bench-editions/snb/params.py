"""Build SNB parameter bindings from the official substitution parameters (SF0.1)
and a seeded message sample. Usage: python -I params.py <param-dir> <jsonl> <out.json> <n>"""
import json, sys, random, datetime as dt, csv
pdir, data, out, n = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
def ts(ms):
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.') + f"{int(ms) % 1000:03d}Z"
def read(i):
    with open(f"{pdir}/interactive_{i}_param.txt") as f:
        r = csv.reader(f, delimiter='|'); h = next(r)
        return [dict(zip(h, row)) for row in r][:n]
rnd = random.Random(20261117)
posts, msgs = [], []
with open(data) as f:
    for line in f:
        if line.startswith('{"type": "Message"'):
            d = json.loads(line)["data"]; msgs.append(d["id"])
            if d["kind"] == "post": posts.append(d["id"])
persons = [int(r["personId"]) for r in read(1)]
P = {}
P["is1"] = P["is2"] = P["is3"] = P["ic8"] = [{"personId": p} for p in persons]
mids = rnd.sample(msgs, n)
P["is4"] = P["is5"] = P["is7"] = [{"messageId": m} for m in mids]
P["is6"] = [{"messageId": m} for m in rnd.sample(posts, n)]
P["ic1"] = [{"personId": int(r["personId"]), "firstName": r["firstName"]} for r in read(1)]
P["ic2"] = [{"personId": int(r["personId"]), "maxDate": ts(r["maxDate"])} for r in read(2)]
P["ic4"] = [{"personId": int(r["personId"]), "startDate": ts(r["startDate"]),
             "endDate": ts(int(r["startDate"]) + int(r["durationDays"]) * 86400000)} for r in read(4)]
P["ic6"] = [{"personId": int(r["personId"]), "tagName": r["tagName"]} for r in read(6)]
P["ic9"] = [{"personId": int(r["personId"]), "maxDate": ts(r["maxDate"])} for r in read(9)]
P["ic11"] = [{"personId": int(r["personId"]), "countryName": r["countryName"], "workFromYear": int(r["workFromYear"])} for r in read(11)]
P["agg1"] = [{}]
# hybrid: the IC6 tag name as free text, scoped to the IC6 person's 2-hop circle
P["h1"] = [{"personId": int(r["personId"]), "q": r["tagName"].replace("_", " ")} for r in read(6)]
P["h2"] = P["h3"] = [{"q": r["tagName"].replace("_", " ")} for r in read(6)]
json.dump(P, open(out, "w"), indent=1)
print({k: len(v) for k, v in P.items()})
