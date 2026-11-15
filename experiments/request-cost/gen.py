#!/usr/bin/env python3
"""Writes the request-cost instrument's GQT cases.

Each case is one graph (Person -WorksAt-> Company -LocatedIn-> City) and a
family of query shapes, each shape in every written order that changes the
plan's root. Every query runs three times: after the seed, after a
`--- restart` (cold handle), and again (warm). Run under
`omnigraph-gqt <case> --measure --model unit|s3-like`.
"""
import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent


def recipe(persons, companies, cities, commits, hub_rows=0):
    per = -(-persons // commits)
    assert per <= 4096, per
    tables = f"""tables:
  - kind: node
    name: City
    rows: {cities}
    commits: 1
    columns:
      slug: {{kind: key, prefix: city-, width: 2}}
      name: {{kind: key, prefix: City , width: 2}}
  - kind: node
    name: Company
    rows: {companies}
    commits: 1
    columns:
      slug: {{kind: key, prefix: c-, width: 3}}
      name: {{kind: key, prefix: Company , width: 3}}
  - kind: node
    name: Person
    rows: {persons}
    commits: {commits}
    columns:
      slug: {{kind: key, prefix: p-, width: 6}}
      name: {{kind: key, prefix: Person , width: 6}}
      age: {{kind: modulo, modulus: 60}}
  - kind: edge
    name: LocatedIn
    rows: {companies}
    commits: 1
    from: {{kind: key, prefix: c-, width: 3}}
    to: {{kind: endpoint, prefix: city-, width: 2, population: {cities}, distribution: {{kind: ordinal}}}}
    columns: {{}}
"""
    uniform = persons - hub_rows
    tables += f"""  - kind: edge
    name: WorksAt
    rows: {uniform}
    commits: {commits}
    from: {{kind: key, prefix: p-, width: 6}}
    to: {{kind: endpoint, prefix: c-, width: 3, population: {companies}, distribution: {{kind: uniform}}}}
    columns: {{}}
"""
    if hub_rows:
        hub_commits = -(-hub_rows // 4096)
        tables += f"""  - kind: edge
    name: WorksAt
    rows: {hub_rows}
    start: {uniform}
    commits: {hub_commits}
    from: {{kind: key, prefix: p-, width: 6}}
    to: {{kind: literal, value: "c-000"}}
    columns: {{}}
"""
    return tables


SCHEMA = """node City {
    slug: String @key
    name: String
}
node Company {
    slug: String @key
    name: String
}
node Person {
    slug: String @key
    name: String
    age: I32
}
edge WorksAt: Person -> Company
edge LocatedIn: Company -> City
"""

# (name, query body, result shape lines). Every query is a single-row
# aggregate, so the expected rows stay short; the columns an aggregate reads
# are the destination columns a hydrating read would fetch.
def shapes(company, city, hub):
    q = []
    count_shape = ["n: I64"]
    q.append(("a_count_c_last", f"""query a_count_c_last() {{
    match {{
        $p: Person
        $p worksAt $c
        $c: Company {{ slug: "{company}" }}
    }}
    return {{ count($p) as n }}
}}""", count_shape))
    q.append(("a_count_c_first", f"""query a_count_c_first() {{
    match {{
        $c: Company {{ slug: "{company}" }}
        $p worksAt $c
        $p: Person
    }}
    return {{ count($p) as n }}
}}""", count_shape))
    cols_shape = ["n: I64", "total: F64", "first: String"]
    q.append(("b_columns_c_last", f"""query b_columns_c_last() {{
    match {{
        $p: Person
        $p worksAt $c
        $c: Company {{ slug: "{company}" }}
    }}
    return {{ count($p) as n, sum($p.age) as total, min($p.name) as first }}
}}""", cols_shape))
    q.append(("b_columns_c_first", f"""query b_columns_c_first() {{
    match {{
        $c: Company {{ slug: "{company}" }}
        $p worksAt $c
        $p: Person
    }}
    return {{ count($p) as n, sum($p.age) as total, min($p.name) as first }}
}}""", cols_shape))
    q.append(("c_chain_city_last", f"""query c_chain_city_last() {{
    match {{
        $p: Person
        $p worksAt $c
        $c locatedIn $t
        $t: City {{ slug: "{city}" }}
    }}
    return {{ count($p) as n }}
}}""", count_shape))
    q.append(("c_chain_city_first", f"""query c_chain_city_first() {{
    match {{
        $t: City {{ slug: "{city}" }}
        $c locatedIn $t
        $p worksAt $c
        $p: Person
    }}
    return {{ count($p) as n }}
}}""", count_shape))
    q.append(("d_not_works_at", f"""query d_not_works_at() {{
    match {{
        $p: Person
        not {{
            $p worksAt $c
            $c: Company {{ slug: "{company}" }}
        }}
    }}
    return {{ count($p) as n }}
}}""", count_shape))
    q.append(("d_company_without_young", """query d_company_without_young() {
    match {
        $c: Company
        not {
            $p worksAt $c
            $p: Person { age: 0 }
        }
    }
    return { count($c) as n }
}""", count_shape))
    return q


EXPECTED = json.loads((HERE / "expected.json").read_text()) if (HERE / "expected.json").exists() else {}


def case(name, notes, persons, companies, cities, commits, company, city,
         hub_rows=0, only=None):
    out = [f"# issue: none"]
    for line in notes:
        out.append(f"# notes: {line}")
    out.append("# traversal: auto")
    if os.environ.get("PROFILE"):
        out.append(f"# storage_profile: {os.environ['PROFILE']}")
    out.append("")
    out.append("--- runner")
    out.append("timeout_ms: 600000")
    out.append("environments:")
    if os.environ.get("BLESS_DIRECT"):
        out.append("  - target: omnigraph-engine")
        out.append("    storage: local-filesystem")
    else:
        out.append("  - target: omnigraph-engine-dst")
        out.append("    storage: in-memory-object-store")
        out.append("    seeds: [0]")
    out.append("")
    out.append("--- schema")
    out.append(SCHEMA)
    out.append("--- seed generate: v1 seed: 0")
    out.append(recipe(persons, companies, cities, commits, hub_rows))
    qs = shapes(company, city, hub_rows)
    if only:
        qs = [s for s in qs if s[0] in only]
    passes = ("after_seed",) if os.environ.get("BLESS_DIRECT") else ("after_seed", "restart", "warm")
    for pass_name in passes:
        if pass_name == "restart":
            out.append("--- restart")
            out.append("")
        for qname, body, shape in qs:
            out.append("--- query")
            out.append(body)
            out.append("--- expect unordered")
            out.append(EXPECTED.get(name, {}).get(qname, '{"n":0}'))
            out.append("--- expect shape")
            out.extend(shape)
            out.append("")
    (HERE / f"{name}.gqt").write_text("\n".join(out))


def harvest(name):
    """Reads the blessed expect rows of a direct-environment copy back."""
    text = (HERE / f"{name}.gqt").read_text().split("\n")
    rows = {}
    for i, line in enumerate(text):
        if line.startswith("query "):
            qname = line.split()[1].split("(")[0]
            j = text.index("--- expect unordered", i)
            rows[qname] = text[j + 1]
    return rows


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "harvest":
        expected = EXPECTED
        for name in sys.argv[2:]:
            expected[name] = harvest(name)
        (HERE / "expected.json").write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n")
        return
    common = dict(companies=200, cities=20, commits=50, company="c-007", city="city-03")
    case("two_hop_200k",
         ["Person 200,000 rows in 50 fragments, WorksAt 200,000 uniform, Company 200, City 20."],
         persons=200_000, **common)
    case("two_hop_20k",
         ["Person 20,000 rows in 50 fragments, WorksAt 20,000 uniform, Company 200, City 20."],
         persons=20_000, **common)
    case("hub_200k",
         ["Person 200,000 in 50 fragments; WorksAt: 150,000 uniform over 200 companies plus 50,000 to the hub c-000."],
         persons=200_000, companies=200, cities=20, commits=50, company="c-000",
         city="city-00", hub_rows=50_000,
         only={"a_count_c_last", "a_count_c_first", "b_columns_c_last", "b_columns_c_first"})
    case("small_control",
         ["Control: Person 200 in 1 fragment, Company 20, City 4; every plan is cheap."],
         persons=200, companies=20, cities=4, commits=1, company="c-007", city="city-03")


if __name__ == "__main__":
    main()
