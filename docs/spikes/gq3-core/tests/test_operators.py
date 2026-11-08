"""One class per row of the operators table: the semantics it states and
the cardinality bound, checked by the evaluator on every call."""
import unittest
from dataclasses import replace

from tests.util import C, Config, GQError, PROPOSED, RFC_LITERAL, bag, ids, sids, standard_graph
from gq3.core import (Agg, Cmp, Col, Lit, Param, Prop, lit, null_lit, Correlate, Cut, Env, Expand,
                      Filter, Fuse, Group, Order, Project, Scan, Score, ApproxTopK, Union)
from gq3.model import BOOL, I64, STRING, T, node_t

G = standard_graph("l2")
KNOWS = (("Knows", "out"),)


def run(t, cfg=PROPOSED, params=None):
    return C.run(t, G, params or {}, cfg)


class ScanT(unittest.TestCase):
    def test_every_node_of_the_type(self):
        r, ctx = C.run_ctx(Scan("Person", "p"), G)
        self.assertEqual(sids(r), ["alice", "bob", "carol", "dave", "erin"])
        self.assertEqual(ctx.stats["ops"]["Scan"], 1)

    def test_interface_scans_every_implementor(self):
        self.assertEqual(len(run(Scan("Named", "n")).rows), 9)


class ExpandT(unittest.TestCase):
    def test_single_hop_set_semantics_over_endpoint_pairs(self):
        # alice knows bob through two parallel edges: one row without $k
        r = run(Expand(Filter(Scan("Person", "p"), Cmp("=", Prop(Col("p"), "handle"), lit("alice"))), "p", KNOWS, 1, 1, "q"))
        self.assertEqual(ids(r, 1), ["bob"])

    def test_edge_binds_only_at_one_hop_then_one_row_per_edge(self):
        src = Filter(Scan("Person", "p"), Cmp("=", Prop(Col("p"), "handle"), lit("alice")))
        r = run(Expand(src, "p", KNOWS, 1, 1, "q", "k"))
        self.assertEqual(len(r.rows), 2)
        with self.assertRaises(GQError) as e:
            run(Expand(src, "p", KNOWS, 2, 2, "q", "k"))
        self.assertEqual(e.exception.code, "T_EDGE_BIND")

    def test_shortest_distance(self):
        # erin -> alice -> bob -> carol -> {alice, dave}: {2,2} is exactly two hops away
        src = Filter(Scan("Person", "p"), Cmp("=", Prop(Col("p"), "handle"), lit("erin")))
        self.assertEqual(sids(run(Expand(src, "p", KNOWS, 2, 2, "q")), 1), ["bob"])
        self.assertEqual(sids(run(Expand(src, "p", KNOWS, 1, 4, "q")), 1), ["alice", "bob", "carol", "dave"])

    def test_start_node_only_through_its_own_self_loop(self):
        a = Filter(Scan("Person", "p"), Cmp("=", Prop(Col("p"), "handle"), lit("alice")))
        d = Filter(Scan("Person", "p"), Cmp("=", Prop(Col("p"), "handle"), lit("dave")))
        self.assertNotIn("alice", ids(run(Expand(a, "p", KNOWS, 1, 5, "q")), 1))   # never through the cycle
        self.assertIn("dave", ids(run(Expand(d, "p", KNOWS, 1, 1, "q")), 1))       # the self-loop is one hop

    def test_source_must_be_a_node_column(self):
        with self.assertRaises(GQError) as e:
            run(Expand(Project(Scan("Person", "p"), (("h", Prop(Col("p"), "handle")),)), "h", KNOWS, 1, 1, "q"))
        self.assertEqual(e.exception.code, "T_EXPAND_SRC")


class FilterT(unittest.TestCase):
    def test_three_valued_logic_null_is_not_true(self):
        # carol's city is null: neither city = "Berlin" nor its negation keeps her
        eq = Cmp("=", Prop(Col("p"), "city"), lit("Berlin"))
        kept = sids(run(Filter(Scan("Person", "p"), eq)))
        negated = sids(run(Filter(Scan("Person", "p"), C.Not(eq))))
        self.assertNotIn("carol", kept + negated)
        self.assertEqual(len(kept) + len(negated), 4)

    def test_or_with_true_is_true_over_null(self):
        e = C.Or(Cmp("=", Prop(Col("p"), "city"), lit("x")), Cmp("=", Prop(Col("p"), "handle"), lit("carol")))
        self.assertEqual(sids(run(Filter(Scan("Person", "p"), e))), ["carol"])


class ProjectT(unittest.TestCase):
    def test_bag_duplicates_kept(self):
        r = run(Project(Scan("Person", "p"), (("c", Prop(Col("p"), "city")),)))
        self.assertEqual(len(r.rows), 5)
        self.assertEqual(bag(r)[("Berlin",)], 2)


class GroupT(unittest.TestCase):
    def test_implicit_keys_and_null_is_a_key_value(self):
        r = run(Group(Scan("Person", "p"), (("c", Prop(Col("p"), "city")),), (("n", Agg("count")),)))
        self.assertEqual(dict(r.rows), {"Berlin": 2, "Paris": 1, "Rome": 1, None: 1})

    def test_no_keys_exactly_one_row_count_zero_rest_null(self):
        empty = Filter(Scan("Person", "p"), Cmp("=", Prop(Col("p"), "handle"), lit("nobody")))
        r = run(Group(empty, (), (("n", Agg("count")), ("m", Agg("min", Prop(Col("p"), "age"))),
                                  ("s", Agg("sum", Prop(Col("p"), "age"))))))
        self.assertEqual(r.rows, [(0, None, None)])

    def test_keyed_group_over_empty_input_is_empty(self):
        empty = Filter(Scan("Person", "p"), Cmp("=", Prop(Col("p"), "handle"), lit("nobody")))
        self.assertEqual(run(Group(empty, (("c", Prop(Col("p"), "city")),), (("n", Agg("count")),))).rows, [])


class CorrelateT(unittest.TestCase):
    def test_outer_columns_then_inner(self):
        inner = Project(Expand(Env(), "p", (("Authored", "out"),), 1, 1, "d"), (("d", Col("d")),))
        r = run(Correlate(Scan("Person", "p"), inner))
        self.assertEqual(r.names, ("p", "d"))
        self.assertEqual(len(r.rows), 6)

    def test_inner_column_colliding_with_outer_is_refused(self):
        inner = Project(Expand(Env(), "p", (("Authored", "out"),), 1, 1, "d"), (("p", Col("d")),))
        with self.assertRaises(GQError) as e:
            run(Correlate(Scan("Person", "p"), inner))
        self.assertEqual(e.exception.code, "T68")


class UnionT(unittest.TestCase):
    def test_names_from_the_left_types_by_position(self):
        a = Project(Scan("Person", "p"), (("name", Prop(Col("p"), "name")),))
        b = Project(Scan("Company", "c"), (("label", Prop(Col("c"), "name")),))
        r = run(Union(a, b))
        self.assertEqual(r.names, ("name",))
        self.assertEqual(len(r.rows), 9)

    def test_mismatched_column_types_refused(self):
        a = Project(Scan("Person", "p"), (("x", Prop(Col("p"), "name")),))
        b = Project(Scan("Person", "p"), (("x", Prop(Col("p"), "age")),))
        with self.assertRaises(GQError) as e:
            run(Union(a, b))
        self.assertEqual(e.exception.code, "TUNION")
        with self.assertRaises(GQError):
            run(Union(Scan("Person", "p"), Scan("Company", "p")))


class OrderCutT(unittest.TestCase):
    def test_keys_then_node_identities(self):
        r = run(Order(Project(Scan("Person", "p"), (("p", Col("p")), ("c", Prop(Col("p"), "city")))),
                      ((Col("c"), "asc", None),)))
        self.assertEqual(ids(r), ["carol", "alice", "dave", "bob", "erin"])   # nulls first ascending

    def test_cut_over_unordered_orders_by_node_identity(self):
        self.assertEqual(ids(run(Cut(Scan("Person", "p"), lit(2)))), ["alice", "bob"])

    def test_cut_bound(self):
        r, ctx = C.run_ctx(Cut(Scan("Person", "p"), lit(10)), G)
        self.assertEqual(len(r.rows), 5)


class ScoreT(unittest.TestCase):
    def test_l2_is_squared_distance_and_null_drops_the_row(self):
        r = run(Score(Scan("Doc", "d"), "l2", "embedding", lit((0.0, 0.0, 0.0)), "distance"))
        d = {x.id: s for x, s in r.rows}
        self.assertNotIn("d5", d)                    # null embedding
        self.assertAlmostEqual(d["d4"], 0.5, places=6)   # 0.5^2 + 0.5^2, no square root

    def test_non_finite_input_is_a_typed_error(self):
        with self.assertRaises(GQError) as e:
            run(Score(Scan("Doc", "d"), "l2", "embedding", lit((float("inf"), 0.0, 0.0)), "distance"))
        self.assertEqual(e.exception.code, "E_NONFINITE")

    def test_bm25_scores_only_members(self):
        r = run(Score(Scan("Doc", "d"), "bm25", "body", lit("rust"), "score"))
        self.assertEqual(sids(r), ["d3"])


class ApproxTopKT(unittest.TestCase):
    def test_exact_top_k_with_recall_carried(self):
        r = run(ApproxTopK(Scan("Doc", "d"), "vector", "embedding", lit((1.0, 0.0, 0.0)), lit(2), "distance", 0.9))
        self.assertEqual(sids(r), ["d1", "d2"])
        self.assertEqual(r.meta["recall"], 0.9)
        self.assertTrue(r.meta["exhaustive"])

    def test_k_is_required(self):
        with self.assertRaises(GQError) as e:
            run(ApproxTopK(Scan("Doc", "d"), "vector", "embedding", lit((1.0, 0.0, 0.0)), None, "distance"))
        self.assertEqual(e.exception.code, "T17")


class FuseT(unittest.TestCase):
    pop = Scan("Doc", "d")

    def arms(self):
        vec = ApproxTopK(self.pop, "vector", "embedding", lit((1.0, 0.0, 0.0)), lit(2), "distance")
        lex = Score(self.pop, "bm25", "body", lit("graph"), "score")
        return vec, lex

    def test_rrf_sum_over_the_arms_a_target_appears_in(self):
        vec, lex = self.arms()
        r = run(Fuse((vec, lex), 60))
        s = {x.id: v for x, v in r.rows}
        # vec ranks d1, d2; lex ranks the "graph" docs by bm25
        lex_rank = [x.id for x, _ in sorted(run(lex).rows, key=lambda r: (-r[1], r[0].id))]
        for doc in s:
            want = (1 / (60 + ["d1", "d2"].index(doc) + 1) if doc in ("d1", "d2") else 0) + \
                   (1 / (60 + lex_rank.index(doc) + 1) if doc in lex_rank else 0)
            self.assertAlmostEqual(s[doc], want)
        self.assertNotIn("d3", s)                     # no arm selected it

    def test_unequal_arms_and_weights(self):
        vec, lex = self.arms()
        r = run(Fuse((vec, lex), 60, (2.0, 0.5)))
        s = {x.id: v for x, v in r.rows}
        self.assertAlmostEqual(s["d5"], 0.5 / (60 + 1))   # d5 is first in lex only; the short arm adds nothing

    def test_refusals(self):
        vec, lex = self.arms()
        for t, code in [(Fuse((vec,), 60), "T_FUSE_ARITY"), (Fuse((vec, lex), 60, (1.0,)), "T_FUSE_WEIGHTS"),
                        (Fuse((lex, lex), 60), "T_FUSE_DUP"), (Fuse((vec, lex), 0), "T_OPTION_RANGE"),
                        (Fuse((vec, Project(self.pop, (("d", Col("d")),))), 60), "T_FUSE_UNRANKED")]:
            with self.assertRaises(GQError) as e:
                run(t)
            self.assertEqual(e.exception.code, code)

    def test_arms_over_different_node_columns_refused(self):
        vec, lex = self.arms()
        other = Score(Project(self.pop, (("e", Col("d")),)), "bm25", "body", lit("graph"), "score")
        with self.assertRaises(GQError) as e:
            run(Fuse((vec, other), 60))
        self.assertEqual(e.exception.code, "T_FUSE_COLUMNS")


class ArithmeticT(unittest.TestCase):
    def ev(self, e):
        return C.eval_expr(e, {}, C.Ctx(G))

    def test_truncation_toward_zero_and_remainder_sign(self):
        self.assertEqual(self.ev(C.Arith("/", lit(-7), lit(2))), -3)
        self.assertEqual(self.ev(C.Arith("%", lit(-7), lit(2))), -1)

    def test_overflow_and_zero_divisor_are_typed_errors(self):
        for e, code in [(C.Arith("*", lit(2 ** 62), lit(4)), "E_OVERFLOW"), (C.Arith("/", lit(1), lit(0)), "E_DIV_ZERO"),
                        (C.Arith("/", lit(1.0), lit(0.0)), "E_NONFINITE")]:
            with self.assertRaises(GQError) as x:
                self.ev(e)
            self.assertEqual(x.exception.code, code)

    def test_null_propagates_and_types_widen(self):
        self.assertIsNone(self.ev(C.Arith("+", Lit(None, I64.null()), lit(1))))
        self.assertEqual(str(C.arith_type("+", T("I32"), T("I64"))), "I64")
        self.assertEqual(str(C.arith_type("+", T("I32"), T("F32"))), "F64")
        self.assertEqual(str(C.arith_type("+", T("I32", None, True), T("I32"))), "I32?")


if __name__ == "__main__":
    unittest.main()
