"""The derived-forms table of section 2, row by row, and the
desugaring-uniqueness property of section 10."""
import unittest

from tests.util import C, GQError, PROPOSED, standard_graph
from gq3.core import (Agg, Cmp, Col, Correlate, Cut, Env, Expand, Filter, Fix, Fuse, Group, Inlined,
                      Lit, Not, Order, Param, Project, Prop, RecRef, Scan, Score, ApproxTopK, Union, Write,
                      Delete, Update, null_lit, lit)
from gq3.dsl import *
from gq3.fixtures import section8_schema
from gq3.model import EdgeType, I64, STRING, T, node_t
from gq3.surface import Desugarer, Program, SFrom, SCall, SUnion


def schema():
    s = section8_schema()
    s.edges["likes"] = EdgeType("Likes", "Person", "Person")   # a second Person -> Person edge
    return s


SCH = schema()


def ds(r, params=None, alternation="rfc"):
    return Desugarer(SCH, params or {"h": STRING}, alternation=alternation).read(r)


KN = (("Knows", "out"),)
P_H = Filter(Scan("Person", "p"), Cmp("=", Prop(Col("p"), "handle"), Param("h")))


class DerivedForms(unittest.TestCase):
    def test_match_composes_scan_expand_filter(self):
        got = ds(read([bind("p", "Person", handle=p("h")), tri("p", "knows", "q")], [v("q")]))
        self.assertEqual(got, Project(Expand(P_H, "p", KN, 1, 1, "q"), (("q", Col("q")),)))

    def test_return_with_an_aggregate_is_group(self):
        got = ds(read([tri("p", "worksAt", "c")], [pr("c", "name"), (count(v("p")), "n")]))
        self.assertEqual(got, Group(Expand(Scan("Person", "p"), "p", (("WorksAt", "out"),), 1, 1, "c"),
                                    (("c.name", Prop(Col("c"), "name")),), (("n", Agg("count", Col("p"))),)))

    def test_return_without_is_project_and_distinct_is_group_without_aggregates(self):
        self.assertEqual(ds(read([bind("p", "Person")], [pr("p", "city")])),
                         Project(Scan("Person", "p"), (("p.city", Prop(Col("p"), "city")),)))
        self.assertEqual(ds(read([bind("p", "Person")], [pr("p", "city")], distinct=True)),
                         Group(Scan("Person", "p"), (("p.city", Prop(Col("p"), "city")),), ()))

    def test_order_and_limit_are_order_and_cut(self):
        got = ds(read([bind("p", "Person")], [pr("p", "name")], order=[(pr("p", "age"), "desc")], limit=L(2)))
        ext = Project(Scan("Person", "p"), (("p", Col("p")), ("_r1", Prop(Col("p"), "name"))))
        want = Project(Cut(Order(ext, ((Prop(Col("p"), "age"), "desc", None),)), L(2)), (("p.name", Col("_r1")),))
        self.assertEqual(got, want)

    def test_block_as_a_value_is_correlate_over_a_keyless_group(self):
        got = ds(read([bind("p", "Person"), SLet("n", SBlock("count", None, (tri("p", "knows", "q"),)))], [v("n")]))
        inner = Group(Expand(Env(), "p", KN, 1, 1, "q"), (), (("_b1", Agg("count")),))
        self.assertEqual(got, Project(Project(Correlate(Scan("Person", "p"), inner),
                                              (("p", Col("p")), ("n", Col("_b1")))), (("n", Col("n")),)))

    def test_exists_is_count_greater_than_zero(self):
        got = ds(read([bind("p", "Person"), SExists((tri("p", "knows", "q"),))], [v("p")]))
        inner = Group(Expand(Env(), "p", KN, 1, 1, "q"), (), (("_b1", Agg("count")),))
        want = Project(Project(Filter(Correlate(Scan("Person", "p"), inner), Cmp(">", Col("_b1"), Lit(0, I64))),
                               (("p", Col("p")),)), (("p", Col("p")),))
        self.assertEqual(got, want)

    def test_not_is_filter_of_not_exists(self):
        got = ds(read([bind("p", "Person"), SNot((tri("p", "knows", "q"),))], [v("p")]))
        inner = Group(Expand(Env(), "p", KN, 1, 1, "q"), (), (("_b1", Agg("count")),))
        want = Project(Project(Filter(Correlate(Scan("Person", "p"), inner), Not(Cmp(">", Col("_b1"), Lit(0, I64)))),
                               (("p", Col("p")),)), (("p", Col("p")),))
        self.assertEqual(got, want)

    def test_optional_is_union_of_correlate_and_null_padded_negation(self):
        got = ds(read([bind("p", "Person"), SOptional((tri("p", "worksAt", "c"),))], [v("p"), v("c")]))
        wa = (("WorksAt", "out"),)
        arm1 = Correlate(Scan("Person", "p"), Project(Expand(Env(), "p", wa, 1, 1, "c"), (("c", Col("c")),)))
        inner = Group(Expand(Env(), "p", wa, 1, 1, "c"), (), (("_b1", Agg("count")),))
        neg = Project(Filter(Correlate(Scan("Person", "p"), inner), Not(Cmp(">", Col("_b1"), Lit(0, I64)))), (("p", Col("p")),))
        arm2 = Project(neg, (("p", Col("p")), ("c", null_lit(node_t("Company")))))
        self.assertEqual(got, Project(Union(arm1, arm2), (("p", Col("p")), ("c", Col("c")))))

    def test_two_patterns_sharing_no_variable_correlate_a_closed_inner(self):
        got = ds(read([bind("p", "Person"), bind("c", "Company")], [v("p"), v("c")]))
        self.assertEqual(got.src, Correlate(Scan("Person", "p"), Scan("Company", "c")))

    def test_alternation_is_a_union_of_two_expands(self):
        got = ds(read([bind("p", "Person", handle=p("h")), tri("p", ("knows", "likes"), "q")], [v("q")]))
        self.assertEqual(got.src, Union(Expand(P_H, "p", KN, 1, 1, "q"), Expand(P_H, "p", (("Likes", "out"),), 1, 1, "q")))
        sel = ds(read([bind("p", "Person", handle=p("h")), tri("p", ("knows", "likes"), "q")], [v("q")]), alternation="selector")
        self.assertEqual(sel.src, Expand(P_H, "p", (("Knows", "out"), ("Likes", "out")), 1, 1, "q"))

    def test_either_orientation_is_a_union_of_the_two(self):
        got = ds(read([bind("p", "Person", handle=p("h")), tri("p", "knows", "q", either=True)], [v("q")]))
        self.assertEqual(got.src, Union(Expand(P_H, "p", KN, 1, 1, "q"), Expand(P_H, "p", (("Knows", "in"),), 1, 1, "q")))

    def test_union_folds_from_the_left(self):
        a, b, c = (inline([bind(x, "Person")], [(v(x), "z")]) for x in "abc")
        got = ds(read([SFrom(SUnion((a, b, c)))], [v("z")]))
        ta, tb, tc = (Project(Scan("Person", x), (("z", Col(x)),)) for x in "abc")
        self.assertEqual(got.src, Union(Union(ta, tb), tc))

    def test_let_value_is_a_project_adding_one_column(self):
        got = ds(read([bind("p", "Person"), SLet("y", Arith("*", pr("p", "age"), L(2)))], [v("y")]))
        self.assertEqual(got.src, Project(Scan("Person", "p"), (("p", Col("p")), ("y", Arith("*", Prop(Col("p"), "age"), L(2))))))

    def test_let_relation_is_its_term_with_no_operator(self):
        named = ds(read([SLet("r", inline([bind("p", "Person")], [v("p")])), SFrom(SRelVar("r"))], [v("p")]))
        direct = ds(read([SFrom(inline([bind("p", "Person")], [v("p")]))], [v("p")]))
        self.assertEqual(named, direct)

    def test_from_is_correlate(self):
        got = ds(read([bind("p", "Person"), SFrom(inline([tri("p", "authored", "d")], [v("d")]))], [v("d")]))
        self.assertEqual(got.src, Correlate(Scan("Person", "p"),
                                            Project(Expand(Env(), "p", (("Authored", "out"),), 1, 1, "d"), (("d", Col("d")),))))

    def test_ranking_functions(self):
        pop = inline([bind("d", "Doc")], [v("d")])
        tp = Project(Scan("Doc", "d"), (("d", Col("d")),))
        self.assertEqual(ds(read([SFrom(SL2(pop, "embedding", p("q")))], [v("d")]), {"q": T("Vector", 3)}).src,
                         Score(tp, "l2", "embedding", Param("q"), "distance"))
        self.assertEqual(ds(read([SFrom(SBm25(pop, "body", p("q")))], [v("d")]), {"q": STRING}).src,
                         Score(tp, "bm25", "body", Param("q"), "score"))
        self.assertEqual(ds(read([SFrom(SNearest(pop, "embedding", p("q"), L(5)))], [v("d")]), {"q": T("Vector", 3)}).src,
                         ApproxTopK(tp, "vector", "embedding", Param("q"), L(5), "distance"))
        got = ds(read([SFrom(SRrf((SNearest(pop, "embedding", p("v"), L(5)), SBm25(pop, "body", p("q"))), 60))], [v("d")]),
                 {"q": STRING, "v": T("Vector", 3)})
        self.assertEqual(got.src, Fuse((ApproxTopK(tp, "vector", "embedding", Param("v"), L(5), "distance"),
                                        Score(tp, "bm25", "body", Param("q"), "score")), 60))

    def test_named_read_is_call_and_self_call_is_fix(self):
        g = standard_graph()
        helper = q("helper", [("h", STRING)], read([bind("p", "Person", handle=p("h"))], [v("p")]))
        user = q("user", [], read([SFrom(SCall("helper", (L("alice"),)))], [v("p")]))
        prog = Program(g.schema, [helper, user])
        core = prog.core("user")
        self.assertIsInstance(core.src, Inlined)
        self.assertEqual(core.src.args, (L("alice"),))
        from tests.test_rfc_fixtures import REACHABLE
        fix = Program(g.schema, [REACHABLE]).core("reachable")
        self.assertIsInstance(fix, Fix)
        self.assertTrue(any(isinstance(t, RecRef) for t in C.walk_terms(fix.bodies[0])))

    def test_write_body_is_the_clauses_term_then_one_sink_per_statement(self):
        g = standard_graph()
        w = q("w", [("h", STRING)], SWriteBody((bind("p", "Person", handle=p("h")), tri("p", "worksAt", "c", k="k")),
                                               (SDelete("k"), SUpdate("p", (("city", Lit(None, STRING.null())),)))))
        got = Program(g.schema, [w]).write("w")
        self.assertEqual(got, Write(Expand(P_H, "p", (("WorksAt", "out"),), 1, 1, "c", "k"),
                                    (Delete("k"), Update("p", (("city", Lit(None, STRING.null())),)))))

    def test_edition_2_delete_where_is_delete_over_filter_over_scan(self):
        g = standard_graph()
        e2 = q("e2", [("h", STRING)], SWriteBody((), (SDeleteWhere("Person", cmp("=", Bare("handle"), p("h"))),)))
        e3 = q("e3", [("h", STRING)], SWriteBody((bind("x", "Person"), where(cmp("=", pr("x", "handle"), p("h")))), (SDelete("x"),)))
        prog = Program(g.schema, [e2, e3])
        self.assertEqual(prog.write("e2"), Write(Filter(Scan("Person", "x"), Cmp("=", Prop(Col("x"), "handle"), Param("h"))), (Delete("x"),)))
        self.assertEqual(prog.write("e2"), prog.write("e3"))       # the rewriter's IR-equality proof holds

    def test_edition_2_delete_of_an_edge_type_has_no_edition_3_spelling_finding_F18(self):
        g = standard_graph()
        e2 = q("e2", [("h", STRING)], SWriteBody((), (SDeleteWhere("WorksAt", cmp("=", Bare("@src"), p("h"))),)))
        with self.assertRaises(GQError) as e:
            Program(g.schema, [e2]).write("e2")
        self.assertEqual(e.exception.code, "T1")   # `$x: WorksAt` is not a binding: bindings take node types


# ---------------------------------------------------------------- uniqueness

def variants():
    """Surface forms over one schema. Several deliberately mean the same."""
    P = bind("p", "Person")
    return {
        "exists": read([P, SExists((tri("p", "knows", "q"),))], [v("p")]),
        "count>0": read([P, where(cmp(">", SBlock("count", None, (tri("p", "knows", "q"),)), L(0)))], [v("p")]),
        "count>=1": read([P, where(cmp(">=", SBlock("count", None, (tri("p", "knows", "q"),)), L(1)))], [v("p")]),
        "not": read([P, SNot((tri("p", "knows", "q"),))], [v("p")]),
        "count=0": read([P, where(cmp("=", SBlock("count", None, (tri("p", "knows", "q"),)), L(0)))], [v("p")]),
        "braces": read([bind("p", "Person", handle=p("h"))], [v("p")]),
        "filter": read([P, where(cmp("=", pr("p", "handle"), p("h")))], [v("p")]),
        "schema_order": read([bind("c", "Company"), tri("p", "worksAt", "c")], [v("p")]),
        "reversed": read([bind("c", "Company"), tri("c", "worksAt", "p")], [v("p")]),
        "union3": read([SFrom(SUnion(tuple(inline([bind(x, "Person")], [(v(x), "z")]) for x in "abc")))], [v("z")]),
        "union_nested": read([SFrom(SUnion((SUnion(tuple(inline([bind(x, "Person")], [(v(x), "z")]) for x in "ab")),
                                            inline([bind("c", "Person")], [(v("c"), "z")]))))], [v("z")]),
        "let_rel": read([SLet("r", inline([bind("p", "Person")], [v("p")])), SFrom(SRelVar("r"))], [v("p")]),
        "from_rel": read([SFrom(inline([bind("p", "Person")], [v("p")]))], [v("p")]),
        "let_value": read([P, SLet("a", pr("p", "age"))], [v("a")]),
        "return_expr": read([P], [(pr("p", "age"), "a")]),
        "distinct": read([P], [pr("p", "city")], distinct=True),
        "plain": read([P], [pr("p", "city")]),
        "either": read([P, tri("p", "knows", "q", either=True)], [v("q")]),
        "directed": read([P, tri("p", "knows", "q")], [v("q")]),
    }


# Collisions the formatter canonicalises (section 6, `queries fmt`) or that are
# a name for a term (section 2, `let $r = rel`): the only exceptions found.
EXPECTED_COLLISIONS = {
    frozenset({"exists", "count>0"}),            # fmt: count { } > 0 -> exists
    frozenset({"braces", "filter"}),             # fmt: the braces are the filter's canonical instance
    frozenset({"schema_order", "reversed"}),     # fmt: a directed triple to schema order
    frozenset({"union3", "union_nested"}),       # not in the fmt list: union is variadic and folds left
    frozenset({"let_rel", "from_rel"}),          # a named relation is a reference to its term
}


class Uniqueness(unittest.TestCase):
    def test_each_form_has_exactly_one_core_term(self):
        for name, r in variants().items():
            self.assertEqual(ds(r), ds(r), name)

    def test_the_collisions_are_exactly_the_canonicalisation_list(self):
        terms = {}
        for name, r in variants().items():
            terms.setdefault(repr(ds(r)), set()).add(name)
        collisions = {frozenset(s) for s in terms.values() if len(s) > 1}
        # Finding F14: A6 read literally ("two surface forms that desugar to the same
        # core term are a violation") fails on the RFC's own derived-forms table.
        self.assertEqual(collisions, EXPECTED_COLLISIONS)

    def test_not_and_count_zero_differ_and_let_differs_from_return(self):
        self.assertNotEqual(ds(variants()["not"]), ds(variants()["count=0"]))
        self.assertNotEqual(ds(variants()["let_value"]), ds(variants()["return_expr"]))

    def test_clause_order_changes_the_term_not_the_rows_finding_F17(self):
        a = read([bind("p", "Person"), tri("p", "knows", "q"), tri("p", "worksAt", "c")], [v("q"), v("c")])
        b = read([bind("p", "Person"), tri("p", "worksAt", "c"), tri("p", "knows", "q")], [v("q"), v("c")])
        self.assertNotEqual(ds(a), ds(b))
        g = standard_graph()
        ra = C.run(ds(a), g, {}, PROPOSED)
        rb = C.run(ds(b), g, {}, PROPOSED)
        from collections import Counter
        self.assertEqual(Counter(ra.rows), Counter(rb.rows))


if __name__ == "__main__":
    unittest.main()
