"""Section 4: terms(...), membership and bm25_v1, with the analyzer reduced
to NFC, lowercase and simple tokenization."""
import math
import unittest

from tests.util import C, GQError, PROPOSED, standard_graph
from gq3 import search as S
from gq3.core import Insert, Lit, Param, Scan, Score, Write, lit
from gq3.fixtures import section8_schema
from gq3.model import Graph, STRING


def corpus(*bodies):
    return S.FieldCorpus({i: S.analyze(b) for i, b in enumerate(bodies)})


class Bm25V1(unittest.TestCase):
    def test_the_formula_by_hand(self):
        c = corpus("graph graph data", "graph", "rust", "")
        self.assertEqual(c.N, 3)                       # a token-empty value is not in the corpus
        q = S.make_terms("graph", "any", 0)
        idf = math.log1p((3 - 2 + 0.5) / (2 + 0.5))
        avg = 5 / 3
        for toks, tf in [(["graph", "graph", "data"], 2), (["graph"], 1)]:
            norm = 1.2 * (0.25 + 0.75 * len(toks) / avg)
            want = idf * (2.2 * tf / (tf + norm))
            self.assertEqual(S.bm25_v1(c, toks, q), want)

    def test_alternatives_share_their_groups_idf_and_take_the_maximum(self):
        c = corpus("graph grape", "graph", "rust")
        q = S.make_terms("graph", "any", 1)
        self.assertEqual(c.df("graph", 1), 2)
        toks = ["graph", "grape"]
        idf = math.log1p((3 - 2 + 0.5) / (2 + 0.5))
        avg = (2 + 1 + 1) / 3
        norm = 1.2 * (0.25 + 0.75 * 2 / avg)
        exact = 2.2 * 1 / (1 + norm)
        self.assertEqual(S.bm25_v1(c, toks, q), idf * exact)   # grape (one edit) weighs 1/2 and loses the max

    def test_repeated_and_reordered_query_terms_change_nothing(self):
        c = corpus("a graph of data", "graph", "data data")
        toks = ["a", "graph", "of", "data"]
        a = S.bm25_v1(c, toks, S.make_terms("graph data", "any", 0))
        b = S.bm25_v1(c, toks, S.make_terms("data graph graph", "any", 0))
        self.assertEqual(a, b)


class Terms(unittest.TestCase):
    def test_edit_budgets_nest(self):
        docs = [["graph"], ["grap"], ["gra"], ["grxph"], ["rgaph"], ["other"]]
        for text in ("graph", "grap", "gaph"):
            m = [{i for i, d in enumerate(docs) if S.matches(d, S.make_terms(text, "any", e))} for e in (0, 1, 2)]
            self.assertTrue(m[0] <= m[1] <= m[2])

    def test_a_transposition_costs_two(self):
        self.assertEqual(S.edit_distance("graph", "rgaph", 2), 2)
        self.assertFalse(S.matches(["rgaph"], S.make_terms("graph", "any", 1)))

    def test_all_and_any(self):
        d = ["graph", "data"]
        self.assertTrue(S.matches(d, S.make_terms("graph rust", "any")))
        self.assertFalse(S.matches(d, S.make_terms("graph rust", "all")))

    def test_refusals(self):
        for args, code in [(("  ...  ",), "T_TERMS_EMPTY"), (("x", "all", 3), "T_OPTION_RANGE"),
                           (("x", "some"), "T_OPTION"), ((None,), "T_TERMS_NULL")]:
            with self.assertRaises(GQError) as e:
                S.make_terms(*args)
            self.assertEqual(e.exception.code, code)

    def test_case_and_normalization(self):
        self.assertTrue(S.matches(S.analyze("Café GRAPH"), S.make_terms("café graph", "all")))

    def test_ranking_and_predicate_agree_on_membership(self):
        g = standard_graph()
        for text, mode, e in [("graph", "any", 0), ("grap notes", "all", 1), ("rust search", "any", 2)]:
            tv = S.make_terms(text, mode, e)
            scored = {x.id for x, _ in C.run(Score(Scan("Doc", "d"), "bm25", "body", Param("q"), "score"), g, {"q": tv}).rows}
            pred = {d for d, p in g.nodes["Doc"].items() if S.matches(S.analyze(p["body"]), tv)}
            self.assertEqual(scored, pred)


class Upsert(unittest.TestCase):
    def test_keyed_insert_is_upsert(self):
        g = standard_graph()
        w = Write(C.Env(), (Insert("Person", (("handle", lit("alice")), ("name", lit("Alicia")))),))
        g2, counts = C.run_write(w, g)
        self.assertEqual(g2.nodes["Person"]["alice"]["name"], "Alicia")
        self.assertEqual(g2.nodes["Person"]["alice"]["city"], "Berlin")      # unassigned properties are kept
        self.assertEqual(len(g2.nodes["Person"]), len(g.nodes["Person"]))
        self.assertEqual(counts, {"Insert": 1})


if __name__ == "__main__":
    unittest.main()
