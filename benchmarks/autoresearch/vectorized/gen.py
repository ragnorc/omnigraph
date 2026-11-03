#!/usr/bin/env python3
"""Generate the read-path scenario grid for the vectorized-execution instrument.

Each fixture is a GQT dataset file (schema + generated seed, zero steps) the
instrument opens; each workload is a plain text file of named GQ queries
separated by `=== <label>` lines. Rerun to regenerate; the files are derived.
"""
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"
FIX.mkdir(exist_ok=True)

SCHEMA = """node Person {
    name: String @key
    age: I64
    team: String
    bio: String
    score: I64
}
edge Knows: Person -> Person {
    weight: I64
}
"""


def fixture(name, nodes, fanout, bio_count=1, node_commits=None, dist="uniform", batch_rows=4096, edge_commits=None):
    edges = nodes * fanout
    width = 6
    node_commits = node_commits or max(1, -(-nodes // batch_rows))
    edge_commits = edge_commits or max(1, -(-edges // batch_rows))
    if dist == "uniform":
        to = f"{{kind: endpoint, prefix: p, width: {width}, population: {nodes}, distribution: {{kind: uniform}}}}"
    else:
        to = f"{{kind: endpoint, prefix: p, width: {width}, population: {nodes}, distribution: {{kind: zipf, exponent: 1.25}}}}"
    text = f"""# issue: none
# notes: autoresearch vectorized-execution fixture: {nodes} people, {edges} edges (fanout {fanout}, {dist}), bio x{bio_count}, {node_commits} node commits.

--- runner
timeout_ms: 10000
environments:
  - target: omnigraph-engine
    storage: local-filesystem

--- schema
{SCHEMA}
--- seed generate: v1 seed: 42
tables:
  - kind: node
    name: Person
    rows: {nodes}
    commits: {node_commits}
    columns:
      name: {{kind: key, prefix: p, width: {width}}}
      age: {{kind: modulo, modulus: 100}}
      team: {{kind: key, prefix: g, width: 0, modulo: 10}}
      bio: {{kind: repeat, text: "synthetic person biography ", count: {bio_count}}}
      score: {{kind: ordinal, start: 0, step: 7}}
  - kind: edge
    name: Knows
    rows: {edges}
    commits: {edge_commits}
    id: {{kind: key, prefix: e, width: 8}}
    from: {{kind: endpoint, prefix: p, width: {width}, population: {nodes}, distribution: {{kind: uniform}}}}
    to: {to}
    columns:
      weight: {{kind: modulo, modulus: 1000}}
"""
    (FIX / f"{name}.gqt").write_text(text)


# Sweep: rows (N), fanout (F), row width (bio), fragments (node commits), skew.
fixture("n1k_f4", 1_000, 4)
fixture("n10k_f4", 10_000, 4)
fixture("n100k_f4", 100_000, 4)
fixture("n10k_f1", 10_000, 1)
fixture("n10k_f16", 10_000, 16)
fixture("n10k_f4_wide", 10_000, 4, bio_count=40, batch_rows=1000)
fixture("n10k_f4_frag100", 10_000, 4, node_commits=100)
fixture("n10k_f16_zipf", 10_000, 16, dist="zipf")
fixture("n10k_f4_efrag100", 10_000, 4, edge_commits=100)

QUERIES = """=== scan_name
query scan_name() { match { $p: Person } return { $p.name } }
=== scan_wide
query scan_wide() { match { $p: Person } return { $p } }
=== filter_pushed
query filter_pushed() { match { $p: Person $p.age >= 90 } return { $p.name, $p.age } }
=== lookup
query lookup() { match { $p: Person { name: "p000007" } } return { $p.name, $p.age } }
=== grouped
query grouped() { match { $p: Person } return { $p.team, sum($p.age) as total, avg($p.score) as mean, count($p) as n } }
=== topk
query topk() { match { $p: Person } return { $p.name, $p.age } order { $p.age desc } limit 10 }
=== sort_all
query sort_all() { match { $p: Person } return { $p.name, $p.age } order { $p.age desc } }
=== hop1
query hop1() { match { $p: Person $p knows $q } return { $p.name, $q.name } }
=== hop1_limit
query hop1_limit() { match { $p: Person $p knows $q } return { $p.name, $q.name } limit 100 }
=== hop1_dst_filter
query hop1_dst_filter() { match { $p: Person $p knows $q $q.age >= 50 } return { $p.name, $q.name } }
=== hop1_memory_filter
query hop1_memory_filter() { match { $p: Person $p knows $q $p.age < $q.age } return { $p.name, $q.name } }
=== hop1_count
query hop1_count() { match { $p: Person $p knows $q } return { $p.team, count($q) as n } }
=== hop2_chain
query hop2_chain() { match { $p: Person $q: Person $r: Person $p knows $q $q knows $r } return { $p.name, $r.name } }
=== hop2_bfs
query hop2_bfs() { match { $p: Person $p knows{2,2} $r } return { $p.name, $r.name } }
=== hop3_bfs
query hop3_bfs() { match { $p: Person $p knows{3,3} $r } return { $p.name, $r.name } }
=== bound_edge
query bound_edge() { match { $p: Person $p $e:knows $q } return { $p.name, $e.weight, $q.name } }
=== no_friends
query no_friends() { match { $p: Person not { $p knows $q } } return { $p.name } }
=== popular
query popular() { match { $p: Person count { $p knows $q } > 4 } return { $p.name } }
"""
(HERE / "queries.txt").write_text(QUERIES)
print("wrote", len(list(FIX.glob("*.gqt"))), "fixtures")
