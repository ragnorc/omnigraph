"""The RFC's own fixtures: the 100-and-11 population, the four population
counterexamples of section 4, `reachable` against `Expand{1,n}`, `distance`
under a round budget, and the refused recursions."""
import unittest
from dataclasses import replace

from tests.util import C, GQError, PROPOSED, RFC_LITERAL, ids, sids, standard_graph
from gq3.dsl import *
from gq3.fixtures import hundred_and_eleven
from gq3.model import F32, I64, STRING, T
from gq3.surface import Program, SCall, SFrom, SUnion

V3 = T("Vector", 3)
POP = inline([bind("d", "Doc"), where(cmp(">=", pr("d", "year"), L(2025)))], [v("d")])


def radius_query(window):
    # within_radius with the fixture's lower bound: 0.1 <= distance <= 0.6, limit 10
    rel = SNearest(POP, "embedding", p("q"), L(100)) if window else SL2(POP, "embedding", p("q"))
    return q("radius", [("q", V3)], read(
        [SFrom(rel), where(cmp("<=", v("distance"), L(0.6))), where(cmp(">=", v("distance"), L(0.1)))],
        [pr("d", "id"), v("distance")], order=[v("distance"), pr("d", "id")], limit=L(10)))


class HundredAndEleven(unittest.TestCase):
    g = hundred_and_eleven("l2")

    def test_l2_form_returns_ten(self):
        r = Program(self.g.schema, [radius_query(False)]).run("radius", self.g, {"q": (0.0, 0.0, 0.0)})
        self.assertEqual([x[0] for x in r.rows], [f"b{i:03d}" for i in range(10)])
        self.assertTrue(all(abs(d - 0.25) < 1e-6 for _, d in r.rows))

    def test_window_form_with_k_100_returns_none(self):
        r = Program(self.g.schema, [radius_query(True)]).run("radius", self.g, {"q": (0.0, 0.0, 0.0)})
        self.assertEqual(r.rows, [])


class PopulationCounterexamples(unittest.TestCase):
    """Section 4, Populations and laws, the four counterexamples."""
    g = standard_graph("l2")
    eligible = inline([bind("d", "Doc"), where(cmp(">=", pr("d", "year"), L(2025)))], [v("d")])

    def test_three_eligible_and_a_window_of_two_count_three_then_two(self):
        before = q("before", [], read([SFrom(self.eligible)], [(count(v("d")), "n")]))
        after = q("after", [("q", V3)], read([SFrom(SNearest(self.eligible, "embedding", p("q"), L(2)))], [(count(v("d")), "n")]))
        prog = Program(self.g.schema, [before, after])
        self.assertEqual(prog.run("before", self.g).rows, [(3,)])
        self.assertEqual(prog.run("after", self.g, {"q": (1.0, 0.0, 0.0)}).rows, [(2,)])

    def test_after_fan_out_three_rows_hold_two_targets_and_count_counts_rows(self):
        fan = inline([bind("d", "Doc"), tri("d", "cites", "o"), where(InList(pr("d", "id"), LL("d1", "d2")))], [v("d"), v("o")])
        rows = q("rows", [], read([SFrom(fan)], [(count(v("d")), "n"), (count(v("d"), distinct=True), "targets")]))
        self.assertEqual(Program(self.g.schema, [rows]).run("rows", self.g).rows, [(3, 2)])

    def test_a_global_window_can_leave_a_group_empty_and_nothing_refills_it(self):
        # the window keeps the two nearest docs (d1, d2), both in "graphs"; "misc" (d3) has no row
        per = q("per", [("q", V3)], read(
            [SFrom(SNearest(inline([bind("d", "Doc")], [v("d")]), "embedding", p("q"), L(2))), tri("d", "in", "x")],
            [pr("x", "slug"), (count(v("d")), "n")]))
        r = Program(self.g.schema, [per]).run("per", self.g, {"q": (1.0, 0.0, 0.0)})
        self.assertEqual(r.rows, [("graphs", 2)])

    def test_changing_eligibility_leaves_bm25_statistics_unchanged(self):
        wide = q("wide", [("q", STRING)], read([SFrom(SBm25(inline([bind("d", "Doc")], [v("d")]), "body", p("q")))],
                                               [pr("d", "id"), v("score")]))
        narrow = q("narrow", [("q", STRING)], read([SFrom(SBm25(inline([bind("d", "Doc"), where(InList(pr("d", "id"), LL("d1", "d5")))],
                                                                       [v("d")]), "body", p("q")))], [pr("d", "id"), v("score")]))
        prog = Program(self.g.schema, [wide, narrow])
        w = dict(prog.run("wide", self.g, {"q": "graph"}).rows)
        n = dict(prog.run("narrow", self.g, {"q": "graph"}).rows)
        self.assertEqual(set(n), {"d1", "d5"})
        for k in n:
            self.assertEqual(n[k], w[k])          # bit-identical: the corpus is the field corpus


REACHABLE = q("reachable", [("from", STRING)], read(
    [SFrom(SUnion((inline([bind("p", "Person", handle=p("from")), tri("p", "knows", "q")], [v("q")]),
                   inline([SFrom(SCall("reachable", (p("from"),))), tri("q", "knows", "r")], [(v("r"), "q")]))))],
    [v("q")], distinct=True))


def distance_query(budget, agg_fn="min", step=1):
    return q("distance", [("from", STRING)], read(
        [SFrom(SUnion((inline([bind("p", "Person", handle=p("from")), tri("p", "knows", "q")], [v("q"), (L(1), "d")]),
                       inline([SFrom(SCall("distance", (p("from"),))), tri("q", "knows", "r")],
                              [(v("r"), "q"), (Arith("+", v("d"), L(step)), "d")]))))],
        [v("q"), (agg(agg_fn, v("d")), "d")]), budget=budget)


class Recursion(unittest.TestCase):
    g = standard_graph()

    def test_reachable_on_a_cyclic_graph_against_bounded_expand(self):
        prog = Program(self.g.schema, [REACHABLE])
        fix = prog.run("reachable", self.g, {"from": "alice"})
        bounded = q("bounded", [("from", STRING)], read(
            [bind("p", "Person", handle=p("from")), tri("p", "knows", "q", hops=(1, 5))], [v("q")], distinct=True))
        exp = Program(self.g.schema, [bounded]).run("bounded", self.g, {"from": "alice"})
        self.assertEqual(sids(fix), ["alice", "bob", "carol", "dave"])
        # Finding F21: Expand never returns the start node through a cycle; the fixpoint does
        self.assertEqual(sids(exp), ["bob", "carol", "dave"])
        self.assertEqual(set(sids(fix)) - set(sids(exp)), {"alice"})

    def test_reachable_semi_naive_equals_naive(self):
        prog = Program(self.g.schema, [REACHABLE])
        a = prog.run("reachable", self.g, {"from": "erin"})
        b = prog.run("reachable", self.g, {"from": "erin"}, replace(PROPOSED, fix_strategy="naive"))
        self.assertEqual(set(a.rows), set(b.rows))

    def test_distance_with_min_under_a_round_budget(self):
        prog = Program(self.g.schema, [distance_query(50)])
        r = prog.run("distance", self.g, {"from": "erin"})
        self.assertEqual({x.id: d for x, d in r.rows}, {"alice": 1, "bob": 2, "carol": 3, "dave": 4})

    def test_distance_budget_overrun_is_a_typed_error(self):
        prog = Program(self.g.schema, [distance_query(2)])
        with self.assertRaises(GQError) as e:
            prog.run("distance", self.g, {"from": "erin"})
        self.assertEqual(e.exception.code, "E_FIX_BUDGET")

    def test_distance_without_a_budget_is_refused(self):
        with self.assertRaises(GQError) as e:
            Program(self.g.schema, [distance_query(None)]).core("distance")
        self.assertEqual(e.exception.code, "T70")

    def test_max_with_an_invented_value_on_a_cycle_only_stops_at_the_budget(self):
        prog = Program(self.g.schema, [distance_query(30, "max")])
        with self.assertRaises(GQError) as e:
            prog.run("distance", self.g, {"from": "erin"})
        self.assertEqual(e.exception.code, "E_FIX_BUDGET")

    def test_non_monotone_recursion_refused(self):
        bad = q("bad", [("from", STRING)], read(
            [SFrom(SUnion((inline([bind("p", "Person", handle=p("from")), tri("p", "knows", "q")], [v("q")]),
                           inline([bind("r", "Person"), SNot((SFrom(SCall("bad", (p("from"),))), where(cmp("=", v("q"), v("r")))))],
                                  [(v("r"), "q")]))))],
            [v("q")], distinct=True))
        for rule in ("rfc", "proposed"):
            with self.assertRaises(GQError) as e:
                Program(self.g.schema, [bad], admission=rule).core("bad")
            self.assertEqual(e.exception.code, "T70")
            self.assertIn("count", e.exception.message)

    def test_count_over_the_recursive_relation_refused(self):
        bad = q("cnt", [("from", STRING)], read(
            [SFrom(SUnion((inline([bind("p", "Person", handle=p("from")), tri("p", "knows", "q")], [v("q"), (L(1), "n")]),
                           inline([SFrom(SCall("cnt", (p("from"),)))], [v("q"), (v("n"), "n")]))))],
            [v("q"), (count(v("n")), "n")]))
        with self.assertRaises(GQError) as e:
            Program(self.g.schema, [bad], admission="rfc").core("cnt")
        self.assertEqual(e.exception.code, "T70")

    def test_recursion_through_two_declared_reads(self):
        a = q("ra", [("from", STRING)], read(
            [SFrom(SUnion((inline([bind("p", "Person", handle=p("from")), tri("p", "knows", "q")], [v("q")]),
                           inline([SFrom(SCall("rb", (p("from"),))), tri("q", "knows", "r")], [(v("r"), "q")]))))],
            [v("q")], distinct=True))
        b = q("rb", [("from", STRING)], read([SFrom(SCall("ra", (p("from"),)))], [v("q")], distinct=True))
        prog = Program(self.g.schema, [a, b])
        r = prog.run("ra", self.g, {"from": "erin"})
        self.assertEqual(sids(r), ["alice", "bob", "carol", "dave"])
        self.assertIsInstance(prog.core("ra"), C.Fix)
        self.assertEqual(prog.core("ra").names, ("ra", "rb"))

    def test_rounds_are_reported(self):
        core = Program(self.g.schema, [REACHABLE]).core("reachable")
        _, ctx = C.run_ctx(core, self.g, {"from": "erin"})
        # base, bob, carol, dave, then one round that adds nothing
        self.assertEqual(ctx.stats["fix_rounds"], [5])


if __name__ == "__main__":
    unittest.main()
