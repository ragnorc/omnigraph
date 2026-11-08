"""The schema of RFC section 8 as fixture data, and the graphs the tests use.

    interface Named { name: String }
    node Person: Named { handle: String @key  name: String  age: I32?  city: String? }
    node Company: Named { slug: String @key  name: String }
    node Doc { id: String @key  title: String  body: String @index(fulltext, analyzer = "english_v1")
               year: I32  tags: [String]  embedding: Vector(3, distance = "cosine")? @embed("body") }
    node Collection { slug: String @key  min_docs: I32 }
    edge WorksAt: Person -> Company @card(0..1)
    edge Knows: Person -> Person
    edge Authored: Person -> Doc
    edge Cites: Doc -> Doc
    edge In: Doc -> Collection
"""
from __future__ import annotations

from .model import (BOOL, F32, I32, I64, STRING, EdgeType, Graph, NodeType, Schema, T)


def section8_schema(distance: str = "cosine") -> Schema:
    s = Schema()
    s.interfaces["Named"] = {"name": STRING}
    s.nodes["Person"] = NodeType("Person", {"handle": STRING, "name": STRING, "age": I32.null(),
                                            "city": STRING.null()}, "handle", ("Named",))
    s.nodes["Company"] = NodeType("Company", {"slug": STRING, "name": STRING}, "slug", ("Named",))
    s.nodes["Doc"] = NodeType("Doc", {"id": STRING, "title": STRING, "body": STRING, "year": I32,
                                      "tags": T("List", "String"), "embedding": T("Vector", 3, True)}, "id")
    s.nodes["Collection"] = NodeType("Collection", {"slug": STRING, "min_docs": I32}, "slug")
    s.edges["worksAt"] = EdgeType("WorksAt", "Person", "Company", card=(0, 1))
    s.edges["knows"] = EdgeType("Knows", "Person", "Person")
    s.edges["authored"] = EdgeType("Authored", "Person", "Doc")
    s.edges["cites"] = EdgeType("Cites", "Doc", "Doc")
    s.edges["in"] = EdgeType("In", "Doc", "Collection")
    s.field_opts[("Doc", "body")] = {"fulltext": True, "analyzer": "english_v1"}
    s.field_opts[("Doc", "title")] = {"fulltext": True, "analyzer": "standard_v1"}
    s.field_opts[("Doc", "embedding")] = {"distance": distance}
    return s


def standard_graph(distance: str = "cosine") -> Graph:
    g = Graph(section8_schema(distance))
    P = [("alice", "Alice", 30, "Berlin"), ("bob", "Bob", 45, "Paris"), ("carol", "Carol", 70, None),
         ("dave", "Dave", None, "Berlin"), ("erin", "Erin", 25, "Rome")]
    for h, n, a, c in P:
        g.add_node("Person", handle=h, name=n, age=a, city=c)
    for slug, name in [("acme", "Acme"), ("globex", "Globex"), ("initech", "Acme"), ("umbrella", "Umbrella")]:
        g.add_node("Company", slug=slug, name=name)
    for p, c in [("alice", "acme"), ("bob", "acme"), ("carol", "globex"), ("dave", "initech")]:
        g.add_edge("worksAt", p, c)
    # a cycle alice -> bob -> carol -> alice, a self-loop on dave, a parallel edge alice -> bob
    for a, b in [("alice", "bob"), ("bob", "carol"), ("carol", "alice"), ("carol", "dave"),
                 ("dave", "dave"), ("erin", "alice"), ("alice", "bob")]:
        g.add_edge("knows", a, b)
    D = [("d1", "The graph book", "graphs and more graphs about graph theory", 2025, ("rust", "graphs"), (1.0, 0.0, 0.0)),
         ("d2", "Graph databases", "a database of graph data", 2024, ("db",), (0.9, 0.1, 0.0)),
         ("d3", "Rust in practice", "rust systems programming", 2026, ("rust",), (0.0, 1.0, 0.0)),
         ("d4", "The art of search", "search ranking with bm25 and vectors", 2025, (), (0.5, 0.5, 0.0)),
         ("d5", "Notes", "graph notes", 2023, ("misc",), None),
         ("d6", "Empty", "", 2022, (), (0.0, 0.0, 1.0))]
    for i, t, b, y, tags, e in D:
        g.add_node("Doc", id=i, title=t, body=b, year=y, tags=tags, embedding=e)
    for p, d in [("alice", "d1"), ("alice", "d2"), ("alice", "d3"), ("alice", "d4"), ("bob", "d2"), ("carol", "d5")]:
        g.add_edge("authored", p, d)
    for a, b in [("d1", "d2"), ("d1", "d3"), ("d2", "d3"), ("d4", "d1"), ("d5", "d1")]:
        g.add_edge("cites", a, b)
    g.add_node("Collection", slug="graphs", min_docs=2)
    g.add_node("Collection", slug="misc", min_docs=5)
    for d, c in [("d1", "graphs"), ("d2", "graphs"), ("d3", "misc"), ("d4", "graphs")]:
        g.add_edge("in", d, c)
    return g


def hundred_and_eleven(distance: str = "l2") -> Graph:
    """100 documents at distance 0, 11 at 0.25 (squared L2 from the query
    (0, 0, 0)), all in one year."""
    g = Graph(section8_schema(distance))
    for i in range(100):
        g.add_node("Doc", id=f"a{i:03d}", title=f"near {i}", body="near", year=2025, tags=(),
                   embedding=(0.0, 0.0, 0.0) if distance == "l2" else (1.0, 0.0, 0.0))
    for i in range(11):
        g.add_node("Doc", id=f"b{i:03d}", title=f"ring {i}", body="ring", year=2025, tags=(),
                   embedding=(0.5, 0.0, 0.0) if distance == "l2" else (1.0, 0.75, 0.0))
    return g


def chain_with_shortcut() -> Graph:
    """a -> b -> c -> d -> e plus the shortcut a -> c (the max-with-filter
    counterexample)."""
    g = Graph(section8_schema())
    for h in "abcde":
        g.add_node("Person", handle=h, name=h.upper(), age=None, city=None)
    for x, y in [("a", "b"), ("b", "c"), ("c", "d"), ("d", "e"), ("a", "c")]:
        g.add_edge("knows", x, y)
    return g
