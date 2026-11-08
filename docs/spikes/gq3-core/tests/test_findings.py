"""One test per finding of docs/spikes/gq3-core.md. Each test shows the
behaviour the RFC's text gives (`RFC_LITERAL` or the rule as written) and
the behaviour of the rule the report proposes (`PROPOSED`)."""
import unittest
from collections import Counter
from dataclasses import replace

from tests.util import C, GQError, PROPOSED, RFC_LITERAL, ids, sids, standard_graph
from gq3.core import (Agg, Cmp, Col, Correlate, Cut, Env, Expand, Filter, Fix, Fuse, Group, Lit,
                      Not, Order, Param, Project, Prop, RecRef, Scan, Score, ApproxTopK, Union, lit)
from gq3.dsl import *
from gq3.fixtures import chain_with_shortcut, section8_schema
from gq3.model import EdgeType, Graph, I64, STRING, T
from gq3.surface import Desugarer, Program, SCall, SFrom, SUnion
from tests.test_rfc_fixtures import REACHABLE, distance_query

G = standard_graph("l2")
V3 = T("Vector", 3)
RFC_ADMIT = replace(PROPOSED, fix_admission="rfc")       # the RFC's monotonicity rule, the rest proposed


def outcomes(term, cfg, graph=G, params=None, seeds=range(25)):
    out = set()
    for s in seeds:
        out.add(tuple(C.run(term, graph, params or {}, replace(cfg, perturb=s)).rows))
    return out


class F01_OrderIsNotTotal(unittest.TestCase):
    """Section 2, Order: 'then unspecified within remaining ties' is not a total order."""

    def test_rows_differing_only_in_an_edge_reference(self):
        src = Expand(Filter(Scan("Person", "p"), Cmp("=", Prop(Col("p"), "handle"), lit("alice"))),
                     "p", (("Knows", "out"),), 1, 1, "q", "k")      # two parallel alice -> bob edges
        t = Cut(Order(src, ((Prop(Col("q"), "handle"), "asc", None),)), lit(1))
        self.assertGreater(len(outcomes(t, RFC_LITERAL)), 1)
        self.assertEqual(len(outcomes(t, PROPOSED)), 1)

    def test_grouped_rows_with_no_node_column(self):
        grouped = Group(Scan("Person", "p"), (("city", Prop(Col("p"), "city")),), (("n", Agg("count")),))
        t = Cut(Order(grouped, ((Col("n"), "asc", None),)), lit(1))     # Paris, Rome and null tie at 1
        self.assertGreater(len(outcomes(t, RFC_LITERAL)), 1)
        self.assertEqual(len(outcomes(t, PROPOSED)), 1)

    def test_a_limit_with_no_order_over_scalars(self):
        t = Cut(Project(Scan("Person", "p"), (("c", Prop(Col("p"), "city")),)), lit(1))
        self.assertGreater(len(outcomes(t, RFC_LITERAL)), 1)
        self.assertEqual(len(outcomes(t, PROPOSED)), 1)


    def test_which_operators_keep_an_order_is_unstated(self):
        # a Cut over a Correlate whose outer side is ordered: if Correlate kept the
        # outer order, the inner rows of one outer row would be cut in physical order
        outer = Order(Scan("Company", "c"), ((Prop(Col("c"), "slug"), "asc", None),))
        inner = Project(Expand(Env(), "c", (("WorksAt", "in"),), 1, 1, "y"), (("w", Col("y")),))
        t = Cut(Correlate(outer, inner), lit(1))
        self.assertGreater(len(outcomes(t, replace(PROPOSED, correlate_keeps_order=True))), 1)
        self.assertEqual(len(outcomes(t, PROPOSED)), 1)


class F02_CorrelationScope(unittest.TestCase):
    """Section 3, from and T68: the RFC's colleagues_of_colleagues and weighted are refused
    by its own collision rule; a wrapper read that renames makes them legal."""

    def test_wrapper_read_makes_colleagues_of_colleagues_legal(self):
        from gq3.corpus import Q
        handles = q("colleague_refs", [("of", STRING)], read([SFrom(SCall("colleagues", (p("of"),)))], [(v("q"), "r")]))
        fixed = q("coc", [("of", STRING)], read(
            [SFrom(SCall("colleagues", (p("of"),))), SFrom(SCall("colleague_refs", (pr("q", "handle"),)))],
            [pr("r", "name")], distinct=True))
        prog = Program(G.schema, [Q["colleagues"], handles, fixed])
        self.assertEqual(prog.run("coc", G, {"of": "alice"}).rows, [("Alice",)])


class F02b_InnerBindingIsCorrelated(unittest.TestCase):
    def test_an_inner_binding_of_an_outer_name_becomes_a_filter(self):
        inner = inline([bind("d", "Doc")], [(count(v("d")), "n")])
        ok = q("ok", [], read([bind("p", "Person"), SFrom(inner)], [v("n")]))
        same = q("same", [], read([bind("d", "Doc"), SFrom(inner)], [v("n")]))
        prog = Program(G.schema, [ok, same])
        self.assertEqual(set(prog.run("ok", G).rows), {(6,)})
        # the same inner text counts 1 per row once the outer scope has a $d
        self.assertEqual(set(prog.run("same", G).rows), {(1,)})


def likes_schema():
    s = section8_schema()
    s.edges["likes"] = EdgeType("Likes", "Person", "Person")
    return s


class F03_AlternationIsNotAUnion(unittest.TestCase):
    """Section 2, derived forms: `<e>` and `(e1 | e2)` as a Union of Expands."""

    def graph(self):
        g = Graph(likes_schema())
        for h in "abc":
            g.add_node("Person", handle=h, name=h, age=None, city=None)
        g.add_edge("knows", "a", "b")
        g.add_edge("knows", "b", "a")
        g.add_edge("likes", "b", "c")
        return g

    def rows(self, r, alternation):
        g = self.graph()
        t = Desugarer(g.schema, {}, alternation=alternation).read(r)
        return sorted(ids(C.run(t, g, {}, PROPOSED), 0))

    def test_either_orientation_duplicates_a_mutual_pair(self):
        r = read([bind("p", "Person", handle=L("a")), tri("p", "knows", "x", either=True)], [v("x")])
        self.assertEqual(self.rows(r, "rfc"), ["b", "b"])
        self.assertEqual(self.rows(r, "selector"), ["b"])      # edition 2: set semantics over endpoint pairs

    def test_multi_hop_alternation_loses_mixed_paths(self):
        r = read([bind("p", "Person", handle=L("a")), tri("p", ("knows", "likes"), "x", hops=(2, 2))], [v("x")])
        self.assertEqual(self.rows(r, "rfc"), [])               # knows{2,2} and likes{2,2} each find nothing new
        self.assertEqual(self.rows(r, "selector"), ["c"])       # edition 2: one shortest-distance search over both


class F04_ExpandBound(unittest.TestCase):
    """Section 2, Expand: `<= |R| * fanout^n` is exceeded with set semantics over m..n."""

    def test_a_binary_tree_one_to_two_hops(self):
        g = Graph(section8_schema())
        for h in "rabcdef":
            g.add_node("Person", handle=h, name=h, age=None, city=None)
        for x, y in [("r", "a"), ("r", "b"), ("a", "c"), ("a", "d"), ("b", "e"), ("b", "f")]:
            g.add_edge("knows", x, y)
        t = Expand(Filter(Scan("Person", "p"), Cmp("=", Prop(Col("p"), "handle"), lit("r"))), "p", (("Knows", "out"),), 1, 2, "q")
        r, ctx = C.run_ctx(t, g)
        self.assertEqual(len(r.rows), 6)                        # fanout 2: the table allows 1 * 2^2 = 4
        self.assertEqual(ctx.stats["expand_table_bound_exceeded"], 1)


def longest(budget=20, cap=3):
    # the max column filtered in the step: admitted by the RFC's rule (max, a budget)
    return q("longest", [("from", STRING)], read(
        [SFrom(SUnion((inline([bind("p", "Person", handle=p("from")), tri("p", "knows", "q")], [v("q"), (L(1), "d")]),
                       inline([SFrom(SCall("longest", (p("from"),))), where(cmp("<", v("d"), L(cap))), tri("q", "knows", "r")],
                              [(v("r"), "q"), (Arith("+", v("d"), L(1)), "d")]))))],
        [v("q"), (agg("max", v("d")), "d")]), budget=budget)


class F05_MonotonicityRule(unittest.TestCase):
    """Section 2, Fix and section 3, Recursion: 'only min and max' is not sufficient, and
    refuses a monotone `exists`."""

    g = chain_with_shortcut()

    def test_a_filter_on_a_max_column_makes_naive_and_semi_naive_disagree(self):
        prog = Program(self.g.schema, [longest()], admission="rfc")
        core = prog.core("longest")                              # admitted
        semi = dict((x.id, d) for x, d in C.run(core, self.g, {"from": "a"}, RFC_ADMIT).rows)
        naive = dict((x.id, d) for x, d in C.run(core, self.g, {"from": "a"}, replace(RFC_ADMIT, fix_strategy="naive")).rows)
        self.assertEqual(semi, {"b": 1, "c": 2, "d": 3, "e": 3})
        self.assertEqual(naive, {"b": 1, "c": 2, "d": 3})
        with self.assertRaises(GQError) as e:
            Program(self.g.schema, [longest()], admission="proposed").core("longest")
        self.assertIn("filter reads the min/max column", e.exception.message)

    def test_the_semi_naive_answer_is_not_a_fixpoint_of_the_body(self):
        prog = Program(self.g.schema, [longest()], admission="rfc")
        core = prog.core("longest")
        result = C.run(core, self.g, {"from": "a"}, RFC_ADMIT)
        ctx = C.Ctx(self.g, RFC_ADMIT, {"from": "a"})
        tenv = C.TEnv(self.g.schema, {"from": STRING}, cfg=RFC_ADMIT)
        C.type_term(core, tenv)
        ctx.notes = tenv.notes
        ctx.rec = {"longest": result}
        once = C._ev(core.bodies[0], ctx, {}, ())
        self.assertNotEqual(set(once.rows), set(result.rows))

    def test_a_cut_over_the_recursive_relation_is_admitted_by_the_rfc_rule(self):
        cut = q("cut", [("from", STRING)], read(
            [SFrom(SUnion((inline([bind("p", "Person", handle=p("from")), tri("p", "knows", "q")], [v("q")]),
                           inline([SFrom(inline([SFrom(SCall("cut", (p("from"),)))], [v("q")], order=[(pr("q", "handle"), "desc")], limit=L(1))),
                                   tri("q", "knows", "r")], [(v("r"), "q")]))))],
            [v("q")], distinct=True))
        core = Program(self.g.schema, [cut], admission="rfc").core("cut")
        semi = sids(C.run(core, self.g, {"from": "a"}, RFC_ADMIT))
        self.assertEqual(semi, ["b", "c", "d", "e"])
        # Kleene iteration oscillates: {b,c} -> {b,c,d} -> {b,c,d,e} -> {b,c} -> ...
        with self.assertRaises(GQError) as e:
            C.run(core, self.g, {"from": "a"}, replace(RFC_ADMIT, fix_strategy="naive"))
        self.assertEqual(e.exception.code, "E_FIX_DIVERGE")
        with self.assertRaises(GQError):
            Program(self.g.schema, [cut], admission="proposed").core("cut")

    def test_exists_over_the_recursive_relation_is_monotone_but_refused(self):
        members = q("members", [("from", STRING)], read(
            [SFrom(SUnion((inline([bind("p", "Person", handle=p("from"))], [(v("p"), "q")]),
                           inline([bind("r", "Person"), tri("r", "knows", "x"),
                                   SExists((SFrom(SCall("members", (p("from"),))), where(cmp("=", v("q"), v("x")))))],
                                  [(v("r"), "q")]))))],
            [v("q")], distinct=True))
        with self.assertRaises(GQError) as e:
            Program(G.schema, [members], admission="rfc").core("members")
        self.assertIn("count", e.exception.message)            # exists desugars to count { } > 0
        core = Program(G.schema, [members], admission="proposed").core("members")
        semi = sids(C.run(core, G, {"from": "alice"}))
        naive = sids(C.run(core, G, {"from": "alice"}, replace(PROPOSED, fix_strategy="naive")))
        self.assertEqual(semi, naive)
        self.assertEqual(semi, ["alice", "bob", "carol", "erin"])


class F06_FixSetsAndBudgets(unittest.TestCase):
    """Section 2, Fix: the relation must be a set, and the budget is a round count."""

    def test_a_recursion_without_distinct_diverges_under_bag_semantics(self):
        bag_reach = replace(REACHABLE, name="reachable", body=replace(REACHABLE.body, distinct=False))
        core = Program(G.schema, [bag_reach]).core("reachable")
        self.assertEqual(sids(C.run(core, G, {"from": "erin"})), ["alice", "bob", "carol", "dave"])
        with self.assertRaises(GQError) as e:
            C.run(core, G, {"from": "erin"}, replace(PROPOSED, fix_set=False))
        self.assertEqual(e.exception.code, "E_FIX_DIVERGE")

    def test_min_converges_on_a_finite_graph_yet_a_small_budget_refuses_it(self):
        core = Program(G.schema, [distance_query(50)]).core("distance")
        _, ctx = C.run_ctx(core, G, {"from": "erin"})
        self.assertLessEqual(ctx.stats["fix_rounds"][0], G.node_count("Person") + 1)
        with self.assertRaises(GQError) as e:
            Program(G.schema, [distance_query(4)]).run("distance", G, {"from": "erin"})
        self.assertEqual(e.exception.code, "E_FIX_BUDGET")


class F07_CallRules(unittest.TestCase):
    def test_a_computed_recursive_argument_passes_the_rfc_check_but_has_no_fixpoint(self):
        body = q("count_down", [("n", I64)], read(
            [SFrom(SUnion((inline([bind("p", "Person")], [v("p")]),
                           inline([SFrom(SCall("count_down", (Arith("-", p("n"), L(1)),)))], [v("p")]))))],
            [v("p")], distinct=True))
        # The RFC's rule looks only for `not` and aggregates: the body has neither.
        d = Desugarer(G.schema, {"n": I64}, calls={"count_down": None}, call_returns={"count_down": ("p",)})
        t = d.read(body.body)

        def rec(x):
            if isinstance(x, C.Call):
                return RecRef("count_down", 1)
            if hasattr(x, "__dataclass_fields__") and isinstance(x, C.RELATION_TERMS):
                return type(x)(**{f: rec(getattr(x, f)) for f in x.__dataclass_fields__})
            return x
        self.assertTrue(C.admit_fix(Fix(("count_down",), (rec(t),), "count_down"), "rfc"))
        with self.assertRaises(GQError) as e:
            Program(G.schema, [body]).core("count_down")
        self.assertIn("computed arguments", e.exception.message)

    def test_textual_substitution_captures_a_name_binding_does_not(self):
        helper = q("friends_of", [("h", STRING)], read([bind("p", "Person", handle=p("h")), tri("p", "knows", "q")], [v("q")]))
        caller = q("caller", [], read([bind("p", "Person", handle=L("erin")), SFrom(SCall("friends_of", (pr("p", "handle"),)))], [v("q")]))
        prog = Program(G.schema, [helper, caller])
        self.assertEqual(sids(prog.run("caller", G)), ["alice"])
        # substitute $h := $p.handle into the body's text and correlate it
        body = Desugarer(G.schema, {"h": STRING}).read(helper.body)

        def subst(x):
            if x == Param("h"):
                return Prop(Col("p"), "handle")
            if isinstance(x, tuple):
                return tuple(subst(y) for y in x)
            if hasattr(x, "__dataclass_fields__") and not isinstance(x, Lit):
                return type(x)(**{f: subst(getattr(x, f)) for f in x.__dataclass_fields__})
            return x
        outer = Filter(Scan("Person", "p"), Cmp("=", Prop(Col("p"), "handle"), lit("erin")))
        captured = C.run(Correlate(outer, subst(body)), G)
        self.assertEqual(sorted(set(ids(captured, 1))), ["alice", "bob", "carol", "dave"])   # every knows target

    def test_the_plan_budget_counts_the_expanded_tree(self):
        reads = [q("r0", [], read([bind("p", "Person")], [v("p")]))]
        for i in range(1, 14):
            prev = SCall(f"r{i - 1}", ())
            reads.append(q(f"r{i}", [], read([SFrom(SUnion((prev, prev)))], [v("p")])))
        prog = Program(G.schema, reads, plan_budget=5000)
        self.assertLess(C.term_size(prog.core("r8")), 5000)
        with self.assertRaises(GQError) as e:
            prog.core("r13")
        self.assertEqual(e.exception.code, "T70")
        self.assertIn("plan budget", e.exception.message)


class F08_UnionNullability(unittest.TestCase):
    """Section 2: `optional` desugars to a Union whose null-padded arm is nullable."""

    def test_optional_needs_union_to_join_nullability(self):
        from gq3.corpus import Q
        q_ = Q["people_and_employers"]
        self.assertEqual(len(Program(G.schema, [q_]).run(q_.name, G).rows), 5)
        with self.assertRaises(GQError) as e:
            Program(G.schema, [q_], cfg=RFC_LITERAL).run(q_.name, G)
        self.assertEqual(e.exception.code, "TUNION")

    def test_the_rfcs_distance_needs_it_too(self):
        # min($d) is nullable; the base arm's `1 as d` is not
        with self.assertRaises(GQError) as e:
            Program(G.schema, [distance_query(50)], cfg=RFC_LITERAL).core("distance")
        self.assertEqual(e.exception.code, "TUNION")


class F09_RankingPopulation(unittest.TestCase):
    """Sections 2, 5 and 6: 'exactly one node column' against the passages example and the rewriter."""

    pop = inline([bind("p", "Person"), tri("p", "authored", "d")], [v("p"), v("d")])

    def test_the_section_6_shape_has_two_node_columns(self):
        passages = q("passages", [("q", STRING)], read([SFrom(SBm25(self.pop, "body", p("q")))],
                                                       [pr("p", "name"), pr("d", "title"), v("score")],
                                                       order=[(v("score"), "desc")], limit=L(5)))
        with self.assertRaises(GQError) as e:
            Program(G.schema, [passages]).run("passages", G, {"q": "graph"})
        self.assertEqual(e.exception.code, "T_POP_NODECOL")
        field = replace(PROPOSED, score_population="field")
        r = Program(G.schema, [passages], cfg=field).run("passages", G, {"q": "graph"}, field)
        self.assertEqual(r.rows[0][:2], ("Carol", "Notes"))

    def test_the_rrf_rewrite_loses_the_populations_other_columns(self):
        field = replace(PROPOSED, score_population="field")
        hyb = q("hyb", [("q", STRING), ("v", V3)], read(
            [SLet("pop", self.pop), SFrom(SRrf((SNearest(SRelVar("pop"), "embedding", p("v"), L(5)), SBm25(SRelVar("pop"), "body", p("q"))), 60))],
            [pr("p", "name"), pr("d", "title"), v("score")]))
        with self.assertRaises(GQError) as e:
            Program(G.schema, [hyb], cfg=field).run("hyb", G, {"q": "graph", "v": (1.0, 0.0, 0.0)}, field)
        self.assertEqual(e.exception.code, "T_SCOPE")       # $p: Fuse yields only [$d, score]


class F10_ScoreAndWindowContracts(unittest.TestCase):
    def test_score_column_types(self):
        l2 = C.check_read(Score(Scan("Doc", "d"), "l2", "embedding", lit((1.0, 0.0, 0.0)), "distance"), G.schema)
        bm = C.check_read(Score(Scan("Doc", "d"), "bm25", "body", lit("graph"), "score"), G.schema)
        self.assertEqual(str(l2[-1][1]), "F32")              # the operators table
        self.assertEqual(str(bm[-1][1]), "F64")              # section 4's bm25_v1 profile; the table says F32

    def test_bm25_score_drops_non_members(self):
        self.assertLess(len(C.run(Score(Scan("Doc", "d"), "bm25", "body", lit("graph"), "score"), G).rows),
                        G.node_count("Doc"))

    def test_window_k_counts_targets_or_rows(self):
        fan = Project(Expand(Scan("Doc", "d"), "d", (("Cites", "out"),), 1, 1, "o"), (("d", Col("d")), ("oid", Prop(Col("o"), "id"))))
        t = ApproxTopK(fan, "vector", "embedding", lit((1.0, 0.0, 0.0)), lit(1), "distance")
        self.assertEqual(len(C.run(t, G).rows), 2)                         # d1 and both its rows: law 4
        self.assertEqual(len(C.run(t, G, {}, replace(PROPOSED, topk_unit="rows")).rows), 1)   # table: <= k rows
        self.assertGreater(len(outcomes(t, replace(RFC_LITERAL, topk_unit="rows"))), 1)    # and which row is unspecified


class F11_L2OnANonL2Field(unittest.TestCase):
    """Section 4: 'the exact top n is l2 … limit n' is not nearest's exact form on a cosine field."""

    def test_l2_and_cosine_disagree(self):
        g = Graph(section8_schema("cosine"))
        for i, e in [("x1", (1.0, 0.0, 0.0)), ("x2", (10.0, 1.0, 0.0)), ("x3", (0.8, 0.6, 0.0))]:
            g.add_node("Doc", id=i, title=i, body=i, year=2025, tags=(), embedding=e)
        qv = lit((1.0, 0.0, 0.0))
        exact = Cut(Order(Score(Scan("Doc", "d"), "l2", "embedding", qv, "distance"),
                          ((Col("distance"), "asc", None), (Prop(Col("d"), "id"), "asc", None))), lit(2))
        window = ApproxTopK(Scan("Doc", "d"), "vector", "embedding", qv, lit(2), "distance")
        self.assertEqual(ids(C.run(exact, g)), ["x1", "x3"])
        self.assertEqual(sids(C.run(window, g)), ["x1", "x2"])


class F12_FuseRanks(unittest.TestCase):
    def test_equal_scores_get_different_votes_by_id(self):
        g = Graph(section8_schema("l2"))
        for i in ("a", "b"):
            g.add_node("Doc", id=i, title=i, body="same text", year=2025, tags=(), embedding=(1.0, 0.0, 0.0))
        lex = Score(Scan("Doc", "d"), "bm25", "body", lit("same"), "score")
        vec = Score(Scan("Doc", "d"), "l2", "embedding", lit((1.0, 0.0, 0.0)), "distance")
        r = dict(C.run(Fuse((lex, vec), 60), g).rows)
        a, b = (r[k] for k in sorted(r, key=lambda x: x.id))
        self.assertGreater(a, b)          # identical in both arms, yet a outranks b

    def test_fan_out_rows_add_no_vote_and_fuse_yields_one_row_per_target(self):
        fan = Project(Expand(Scan("Doc", "d"), "d", (("Cites", "out"),), 1, 1, "o"), (("d", Col("d")), ("oid", Prop(Col("o"), "id"))))
        lex = Score(fan, "bm25", "body", lit("graph"), "score")
        vec = Score(Scan("Doc", "d"), "l2", "embedding", lit((1.0, 0.0, 0.0)), "distance")
        r = C.run(Fuse((lex, vec), 60), G)
        self.assertEqual(len(r.rows), len({x for x, _ in r.rows}))


class F13_NullEqualityForDifference(unittest.TestCase):
    """Section 2: Group's null key against three-valued equality; 'Difference is derived'."""

    def test_r_minus_r_keeps_rows_with_a_null(self):
        R = Project(Scan("Person", "p"), (("c", Prop(Col("p"), "city")),))
        same = Filter(Project(Scan("Person", "p2"), (("c2", Prop(Col("p2"), "city")),)), Cmp("=", Col("c2"), Col("c")))
        diff = Filter(Correlate(R, Group(same, (), (("n", Agg("count")),))), Cmp("=", Col("n"), lit(0)))
        self.assertEqual(C.run(diff, G).rows, [(None, 0)])      # R - R is not empty
        not_distinct = C.Or(Cmp("=", Col("c2"), Col("c")), C.And(C.IsNull(Col("c2")), C.IsNull(Col("c"))))
        same2 = Filter(Project(Scan("Person", "p2"), (("c2", Prop(Col("p2"), "city")),)), not_distinct)
        diff2 = Filter(Correlate(R, Group(same2, (), (("n", Agg("count")),))), Cmp("=", Col("n"), lit(0)))
        self.assertEqual(C.run(diff2, G).rows, [])

    def test_distinct_rows_and_count_distinct_disagree_on_null(self):
        rows = C.run(Group(Scan("Person", "p"), (("c", Prop(Col("p"), "city")),), ()), G).rows
        n = C.run(Group(Scan("Person", "p"), (), (("n", Agg("count", Prop(Col("p"), "city"), True)),)), G).rows
        self.assertEqual((len(rows), n[0][0]), (4, 3))


class F16_ProjectBeforeOrder(unittest.TestCase):
    """Section 2, derived forms: `return` -> Project and `order` -> Order leave the order open."""

    def test_order_after_the_final_project_cannot_see_an_unprojected_key(self):
        with self.assertRaises(GQError) as e:
            C.check_read(Order(Project(Scan("Person", "p"), (("p.name", Prop(Col("p"), "name")),)),
                               ((Prop(Col("p"), "age"), "desc", None),)), G.schema)
        self.assertEqual(e.exception.code, "T_SCOPE")
        from gq3.corpus import Q
        self.assertEqual(Program(G.schema, [Q["top_people"]]).run("top_people", G, {"k": 1}).rows, [("Carol",)])


class F17_ClauseOrder(unittest.TestCase):
    """Section 3: `from` reads what is written before it; patterns do not care about order."""

    def test_moving_a_from_changes_its_meaning(self):
        inner = inline([tri("d", "cites", "x")], [v("x")])
        before = q("before", [], read([bind("d", "Doc", id=L("d1")), SFrom(inner)], [pr("x", "id")]))
        after = q("after", [], read([SFrom(inner), bind("d", "Doc", id=L("d1"))], [pr("x", "id")]))
        prog = Program(G.schema, [before, after])
        self.assertEqual(sorted(prog.run("before", G).rows), [("d2",), ("d3",)])
        self.assertEqual(sorted(prog.run("after", G).rows), [("d1",), ("d1",), ("d2",), ("d3",), ("d3",)])


class F18_Writes(unittest.TestCase):
    def test_two_rows_assigning_one_property_two_values(self):
        w = q("w", [], SWriteBody((bind("p", "Person", handle=L("carol")), tri("p", "knows", "x")),
                                  (SUpdate("p", (("age", pr("x", "age")),)),)))
        prog = Program(G.schema, [w])
        with self.assertRaises(GQError) as e:
            prog.run("w", G)
        self.assertEqual(e.exception.code, "E_WRITE_CONFLICT")
        ages = set()
        for s in range(20):
            g2, _ = prog.run("w", G, {}, replace(RFC_LITERAL, perturb=s))
            ages.add(g2.nodes["Person"]["carol"]["age"])
        self.assertEqual(ages, {30, None})       # alice's age or dave's, by physical row order

    def test_update_and_delete_of_one_entity(self):
        w = q("w", [], SWriteBody((bind("p", "Person", handle=L("bob")),),
                                  (SDelete("p"), SUpdate("p", (("city", Lit(None, STRING.null())),)))))
        with self.assertRaises(GQError) as e:
            Program(G.schema, [w]).run("w", G)
        self.assertEqual(e.exception.code, "E_WRITE_CONFLICT")

    def test_the_edition_3_retire_is_not_the_edition_2_retire(self):
        from gq3.corpus import Q
        prog = Program(G.schema, list(Q.values()))
        g2, _ = prog.run("retire_e2_update", G, {"h": "erin"})
        g3, counts = prog.run("retire", G, {"h": "erin"})
        self.assertIsNone(g2.nodes["Person"]["erin"]["city"])          # edition 2 clears erin's city
        self.assertEqual(g3.nodes["Person"]["erin"]["city"], "Rome")    # edition 3 has no row to update
        self.assertEqual(counts, {"Delete": 0, "Update": 0})


if __name__ == "__main__":
    unittest.main()
