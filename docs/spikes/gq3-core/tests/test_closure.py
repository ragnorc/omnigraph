"""Section 2, Closure: one case per row of the table. Each puts a derived
relation (a grouped, projected or ranked read, never a bare Scan) in the
input position and checks acceptance, or refusal with the stated reason."""
import unittest

from tests.util import C, GQError, PROPOSED, standard_graph
from gq3.core import (Agg, Cmp, Col, Correlate, Cut, Delete, Env, Expand, Filter, Fix, Fuse, Group,
                      Inlined, Insert, Lit, Order, Param, Project, Prop, RecRef, Scan, Score, ApproxTopK,
                      Union, Write, lit)
from gq3.dsl import *
from gq3.model import I64, STRING, T, node_t
from gq3.surface import Program, SCall, SFrom, SUnion

G = standard_graph("l2")
KN = (("Knows", "out"),)
# a derived relation: people grouped by city with their count, keeping one node column via min
DERIVED_PEOPLE = Project(Filter(Scan("Person", "p"), Cmp(">=", Prop(Col("p"), "age"), lit(18))), (("p", Col("p")),))
DERIVED_DOCS = Project(Filter(Scan("Doc", "d"), Cmp(">=", Prop(Col("d"), "year"), lit(2024))), (("d", Col("d")),))
GROUPED = Group(Scan("Person", "p"), (("city", Prop(Col("p"), "city")),), (("n", Agg("count")),))


def accepts(t, params=None):
    return C.run(t, G, params or {}, PROPOSED)


class ClosureTable(unittest.TestCase):
    def refused(self, t, code):
        with self.assertRaises(GQError) as e:
            accepts(t)
        self.assertEqual(e.exception.code, code)
        return e.exception

    def test_correlate_inner_term(self):
        inner = Group(Expand(Env(), "p", KN, 1, 1, "q"), (), (("deg", Agg("count")),))
        self.assertEqual(len(accepts(Correlate(DERIVED_PEOPLE, inner)).rows), 4)

    def test_union_arms_types_must_agree(self):
        self.assertEqual(len(accepts(Union(GROUPED, GROUPED)).rows), 8)
        self.refused(Union(GROUPED, Project(Scan("Person", "p"), (("city", Prop(Col("p"), "age")), ("n", lit(1))))), "TUNION")

    def test_group_project_filter_order_cut_input(self):
        accepts(Group(GROUPED, (), (("m", Agg("max", Col("n"))),)))
        accepts(Project(GROUPED, (("c", Col("city")),)))
        accepts(Filter(GROUPED, Cmp(">", Col("n"), lit(1))))
        accepts(Cut(Order(GROUPED, ((Col("n"), "desc", None),)), lit(1)))

    def test_expand_source_needs_a_node_column(self):
        self.assertEqual(len(accepts(Expand(DERIVED_PEOPLE, "p", KN, 1, 1, "q")).rows), 5)
        e = self.refused(Expand(GROUPED, "city", KN, 1, 1, "q"), "T_EXPAND_SRC")
        self.assertIn("nothing to traverse from", e.message)

    def test_score_and_topk_population_with_one_node_column(self):
        accepts(Score(DERIVED_DOCS, "l2", "embedding", lit((1.0, 0.0, 0.0)), "distance"))
        accepts(ApproxTopK(DERIVED_DOCS, "vector", "embedding", lit((1.0, 0.0, 0.0)), lit(2), "distance"))
        self.refused(Score(GROUPED, "l2", "embedding", lit((1.0, 0.0, 0.0)), "distance"), "T_POP_NODECOL")
        two = Expand(Project(Scan("Person", "p"), (("p", Col("p")),)), "p", (("Authored", "out"),), 1, 1, "d")
        self.refused(Score(two, "bm25", "body", lit("graph"), "score"), "T_POP_NODECOL")

    def test_fuse_arms_ordered_scored_sharing_one_node_column(self):
        a = ApproxTopK(DERIVED_DOCS, "vector", "embedding", lit((1.0, 0.0, 0.0)), lit(2), "distance")
        b = Score(DERIVED_DOCS, "bm25", "body", lit("graph"), "score")
        accepts(Fuse((a, b), 60))
        # an ordered derived read is an arm too
        ordered = Order(Project(DERIVED_DOCS, (("d", Col("d")),)), ((Prop(Col("d"), "year"), "desc", None),))
        accepts(Fuse((a, ordered), 60))
        self.refused(Fuse((a, DERIVED_DOCS), 60), "T_FUSE_UNRANKED")

    def test_call_argument_scalars_only(self):
        helper = q("helper", [("h", STRING)], read([bind("p", "Person", handle=p("h"))], [v("p")]))
        bad = q("bad", [], read([SFrom(SCall("helper", (inline([bind("p", "Person")], [v("p")]),)))], [v("p")]))
        with self.assertRaises(GQError) as e:
            Program(G.schema, [helper, bad]).core("bad")
        self.assertEqual(e.exception.code, "TCALLARG")

    def test_fix_body_monotone_in_the_recursive_relation(self):
        from tests.test_rfc_fixtures import REACHABLE
        self.assertIsInstance(Program(G.schema, [REACHABLE]).core("reachable"), Fix)
        neg = q("neg", [("from", STRING)], read(
            [SFrom(SUnion((inline([bind("p", "Person", handle=p("from")), tri("p", "knows", "q")], [v("q")]),
                           inline([bind("r", "Person"), SNot((SFrom(SCall("neg", (p("from"),))), where(cmp("=", v("q"), v("r")))))],
                                  [(v("r"), "q")]))))],
            [v("q")], distinct=True))
        with self.assertRaises(GQError) as e:
            Program(G.schema, [neg]).core("neg")
        self.assertEqual(e.exception.code, "T70")

    def test_a_column_is_scalar_or_reference_never_a_relation(self):
        with self.assertRaises(GQError) as e:
            C.check_read(Project(Scan("Person", "p"), (("r", Scan("Doc", "d")),)), G.schema)
        self.assertEqual(e.exception.code, "T69")
        bad = q("bad", [], read([bind("p", "Person"), SLet("r", SBlock("count", None, ())), where(SL2(inline([bind("d", "Doc")], [v("d")]), "embedding", L((1.0, 0.0, 0.0))))], [v("p")]))
        with self.assertRaises(GQError) as e:
            Program(G.schema, [bad]).core("bad")
        self.assertEqual(e.exception.code, "T69")

    def test_a_sinks_input_may_be_any_relation(self):
        w = Write(Cut(Order(Score(DERIVED_DOCS, "l2", "embedding", lit((1.0, 0.0, 0.0)), "distance"),
                            ((Col("distance"), "asc", None),)), lit(1)), (Delete("d"),))
        g2, counts = C.run_write(w, G)
        self.assertEqual(counts["Delete"], 1)
        self.assertNotIn("d1", g2.nodes["Doc"])

    def test_a_sink_inside_a_read_never(self):
        w = Write(Scan("Person", "p"), (Delete("p"),))
        self.refused(Correlate(Scan("Doc", "d"), w), "A7")


if __name__ == "__main__":
    unittest.main()
