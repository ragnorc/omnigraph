"""Generate GQT cases (inline seeds; local-filesystem plus DST in-memory store) from the
harness query files, for `omnigraph-gqt --measure`. Expectations are blessed by each
edition's own runner (OMNIGRAPH_GQ_BLESS=1); the cases measure store requests per step.
Usage: python -I gen.py <outdir>"""
import json, sys, os, random
out = sys.argv[1]; H = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.makedirs(out, exist_ok=True)
rnd = random.Random(7)
# SNB sample: 150 persons from the IC1 parameters' neighbourhood order, knows among them,
# 25 messages each with their creator and tag edges, the tags those messages use.
recs = [json.loads(l) for l in open("/src/data/ldbc/snb-sf0.1.jsonl")]
persons = [r for r in recs if r.get("type") == "Person"][:150]
pid = {str(r["data"]["id"]) for r in persons}
creator = {}
for r in recs:
    if r.get("edge") == "HasCreator" and r["to"] in pid: creator.setdefault(r["to"], []).append(r["from"])
mids = {m for p in creator for m in creator[p][:25]}
msgs = [r for r in recs if r.get("type") == "Message" and str(r["data"]["id"]) in mids]
edges = [r for r in recs if (r.get("edge") == "Knows" and r["from"] in pid and r["to"] in pid)
         or (r.get("edge") == "HasCreator" and r["from"] in mids)]
htag = [r for r in recs if r.get("edge") == "HasTag" and r["from"] in mids]
tids = {r["to"] for r in htag}
tags = [r for r in recs if r.get("type") == "Tag" and str(r["data"]["id"]) in tids]
snb_seed = [json.dumps(r, ensure_ascii=False) for r in persons + msgs + tags + edges + htag]
schema = open(f"{H}/snb/schema.pg").read()
deg = {}
for r in edges:
    if r["edge"] == "Knows": deg[r["from"]] = deg.get(r["from"], 0) + 1
hub = int(max(deg, key=deg.get))
words = ["Napoleon", "Murdoch", "Handel", "Shakira", "Fujimori"]
snb_steps = [("is2", {"personId": hub}), ("ic2", {"personId": hub, "maxDate": "2012-12-01T00:00:00.000Z"}),
             ("ic9", {"personId": hub, "maxDate": "2012-12-01T00:00:00.000Z"}),
             ("h1", {"personId": hub, "q": "Napoleon"}), ("h2", {"q": "Napoleon Handel"}), ("h3", {"q": "Napoleon"})]
# SciFact sample: 1,000 documents.
docs = [l.rstrip("\n") for l in open("/src/data/beir/scifact.og.jsonl")]
beir_seed = rnd.sample(docs, 1000)
beir_steps = [("rank", {"q": "vitamin D deficiency increases cancer risk"}), ("lexcount", {"q": "vitamin D deficiency"})]
def case(ed, name, schema, seed, steps, qdir):
    s = ["# issue: none", f"# notes: harness case for the edition comparison ({ed}); expectations blessed by the edition's runner.",
         "", "--- runner", "timeout_ms: 600000", "environments:",
         "  - target: omnigraph-engine", "    storage: local-filesystem",
         "  - target: omnigraph-engine-dst", "    storage: in-memory-object-store", "    seeds: [0]", "",
         "--- schema", schema.strip(), "", "--- seed"] + seed + [""]
    for q, p in steps:
        s += ["--- query", open(f"{qdir}/{q}.gq").read().strip(), "--- params", json.dumps(p), "",
              "--- expect unordered", "", "--- expect shape", "x: String", ""]
    open(f"{out}/{name}_{ed}.gqt", "w").write("\n".join(s) + "\n")
for ed in ("e2", "gq3"):
    case(ed, "snb_sample", schema, snb_seed, snb_steps, f"{H}/snb/queries/{ed}")
    case(ed, "scifact_sample", open(f"{H}/beir/schema.pg").read(), beir_seed, beir_steps, f"{H}/beir/queries/{ed}")
print(len(snb_seed), "snb seed records; hub", hub, deg[str(hub)])
