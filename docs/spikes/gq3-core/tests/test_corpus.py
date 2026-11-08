"""Every edition-3 query of the before-and-after asset, executed. A query the
RFC's own rules refuse is asserted refused, with the finding it belongs to."""
import unittest

from tests.util import C, GQError, PROPOSED, sids, standard_graph
from gq3.corpus import Q
from gq3.surface import Program, SWriteBody

G = standard_graph("l2")
PROG = Program(G.schema, list(Q.values()))


def rows(name, **params):
    return PROG.run(name, G, params)


def srt(rel):
    return sorted(rel.rows, key=repr)


class Lookups(unittest.TestCase):
    def test_section_1(self):
        self.assertEqual(rows("person", h="alice").rows, [("Alice", "Berlin")])
        self.assertEqual([r[0] for r in rows("adults", min=26).rows], ["Carol", "Bob", "Alice"])
        self.assertEqual(rows("titled").rows, [("The graph book",)])
        self.assertEqual(sorted(rows("in_cities").rows), [("Alice",), ("Bob",), ("Dave",)])
        self.assertEqual(sorted(rows("tagged").rows), [("Rust in practice",), ("The graph book",)])
        self.assertEqual(rows("homeless").rows, [("Carol",)])
        self.assertEqual(len(rows("names").rows), 9)
        self.assertEqual(sorted(rows("doubled", min=100).rows), [("Carol", 140, 1956)])
        self.assertIn(("Carol", "unknown", "senior"), rows("with_city").rows)
        self.assertIn(("Dave", "Berlin", "active"), rows("with_city").rows)   # if(null >= 65, …) takes the else arm


class Traversal(unittest.TestCase):
    def test_section_2(self):
        self.assertEqual(rows("employer", h="alice").rows, [("Acme", "alice", "acme")])
        self.assertEqual(sids(rows("reachable", **{"from": "erin"})), ["alice", "bob", "carol", "dave"])
        self.assertEqual(sorted(rows("in_collection", slug="graphs").rows),
                         [("Graph databases",), ("The art of search",), ("The graph book",)])
        # written against schema order: `$c worksAt $p` means `$p worksAt $c`
        self.assertEqual(sorted(rows("boss", slug="acme").rows), [("Alice",), ("Bob",)])

    def test_around_is_refused_finding_F3(self):
        # `$p (knows | authored) $thing` ends on Person and Doc: edition 2 refuses it (T5) and the
        # RFC's Union desugaring refuses it as a Union type mismatch.
        with self.assertRaises(GQError) as e:
            rows("around", h="alice")
        self.assertEqual(e.exception.code, "TUNION")


class Aggregation(unittest.TestCase):
    def test_section_3(self):
        # headcount groups by name: acme and initech are both "Acme"
        self.assertEqual(sorted(rows("headcount").rows), [("Acme", 3), ("Globex", 1)])
        self.assertEqual(sorted(rows("reach", h="alice").rows), [("Bob",), ("Carol",)])
        self.assertEqual(rows("distinct_cities").rows, [(3,)])           # null excluded
        self.assertEqual(rows("big_employers").rows, [])
        self.assertEqual(rows("average_headcount").rows, [(4 / 3, 2)])
        self.assertEqual(len(rows("well_connected", min=1).rows), 5)
        self.assertEqual(rows("well_connected", min=2).rows, [("Carol",)])
        self.assertEqual(rows("well_connected", min=0).rows and len(rows("well_connected", min=0).rows), 5)
        self.assertEqual(rows("full_collections").rows, [("graphs",)])
        self.assertEqual(rows("degrees", min=2).rows, [("Carol", 2)])
        self.assertEqual(rows("authors_without_citations").rows, [("Carol",)])

    def test_big_employers_threshold_one_and_empty_group(self):
        from gq3.corpus import big_employers
        prog = Program(G.schema, [big_employers(1)])
        r = prog.run("big_employers", G)
        # acme 2, globex 1, initech 1; umbrella has no employee and therefore no group
        self.assertEqual(r.rows[0], ("Acme", 2))
        self.assertEqual(sorted(r.rows), [("Acme", 1), ("Acme", 2), ("Globex", 1)])


class Shapes(unittest.TestCase):
    def test_section_4(self):
        self.assertEqual(sorted(rows("people_and_employers").rows, key=repr),
                         sorted([("Alice", "Acme"), ("Bob", "Acme"), ("Carol", "Globex"), ("Dave", "Acme"), ("Erin", None)], key=repr))
        self.assertEqual(len(rows("people_and_companies").rows), 9)
        self.assertEqual(sids(rows("colleagues", of="alice")), ["bob"])
        self.assertEqual([r[1] for r in rows("latest_per_author").rows if r[0] == "Alice"],
                         ["Rust in practice", "The graph book", "The art of search"])   # the 2025 tie goes to d1 < d4
        self.assertEqual([r[0] for r in rows("top_people", k=2).rows], ["Carol", "Bob"])

    def test_colleagues_of_colleagues_is_refused_finding_F2(self):
        with self.assertRaises(GQError) as e:
            rows("colleagues_of_colleagues", of="alice")
        self.assertEqual(e.exception.code, "T68")


class Search(unittest.TestCase):
    def test_section_5(self):
        q = (1.0, 0.0, 0.0)
        self.assertEqual([r[0] for r in rows("similar", q=q).rows], ["The graph book", "The art of search", "Rust in practice"])
        self.assertEqual([r[0] for r in rows("similar_scored", q=q).rows][:2], ["The graph book", "Graph databases"])
        self.assertEqual(rows("cited_near", coll="graphs", q=q).rows[0][::2], ("The graph book", 2))
        self.assertEqual([r[0] for r in rows("within_radius", q=q, r=0.6).rows], ["The graph book", "The art of search"])
        self.assertEqual([r[0] for r in rows("about", q="graph").rows][0], "Notes")
        self.assertEqual(sorted(rows("mentions", q="graph").rows), [("Graph databases",), ("The graph book",)])
        h = rows("hybrid", q="graph", v=q).rows
        self.assertEqual(h[0][0], "The graph book")
        self.assertEqual(len(rows("similar_fast", q=q).rows), 5)

    def test_weighted_is_refused_finding_F2(self):
        with self.assertRaises(GQError) as e:
            rows("weighted", q="graph", v=(1.0, 0.0, 0.0))
        self.assertEqual(e.exception.code, "T68")


class Writes(unittest.TestCase):
    def run_write(self, name, **params):
        return PROG.run(name, G, params)

    def test_hire(self):
        g2, counts = self.run_write("hire", h="zed", slug="globex")
        self.assertIn("zed", g2.nodes["Person"])
        self.assertTrue(any(e.src == "zed" and e.dst == "globex" for e in g2.edges["WorksAt"]))

    def test_close_company_two_one_and_no_employees(self):
        for slug, n in [("acme", 2), ("globex", 1), ("umbrella", 0)]:
            g2, counts = self.run_write("close_company", slug=slug)
            self.assertEqual(counts.get("Delete", 0), n)
            self.assertEqual(sum(1 for e in g2.edges["WorksAt"] if e.dst == slug), 0)

    def test_birthday_and_tag_near(self):
        g2, _ = self.run_write("birthday", h="alice")
        self.assertEqual(g2.nodes["Person"]["alice"]["age"], 31)
        g3, counts = self.run_write("tag_near", q=(1.0, 0.0, 0.0))
        self.assertEqual(counts["Update"], 5)
        self.assertEqual(g3.nodes["Doc"]["d1"]["tags"], ("near",))
        self.assertEqual(G.nodes["Doc"]["d1"]["tags"], ("rust", "graphs"))     # one snapshot; the input is untouched

    def test_propose(self):
        g2, _ = self.run_write("propose", h="yan")
        self.assertIn("yan", g2.nodes["Person"])


if __name__ == "__main__":
    unittest.main()
