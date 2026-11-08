"""Run the core spike's typer (spike/gq3-core, docs/spikes/gq3-core/) on the
inputs the Rust checker of spike/gq3-typeck types, and compare refusals.

Usage, from anywhere (the core spike's tree is an argument, never the
current directory):

    git -C <repo> archive fork/spike/gq3-core docs/spikes/gq3-core | tar x -C /tmp/core
    python3 -I docs/spikes/gq3-typeck/compare_core.py /tmp/core/docs/spikes/gq3-core

The Rust verdicts below are pinned by
`crates/omnigraph-compiler/src/query/typeck3_tests.rs::the_core_spike_probes_in_rust`,
which types the same inputs written as edition-3 text.
"""
import sys

sys.path.insert(0, sys.argv[1])

from gq3 import corpus  # noqa: E402
from gq3.dsl import *  # noqa: E402,F403
from gq3.model import GQError, I32, I64, STRING, T  # noqa: E402
from gq3.surface import Program, SFrom, SUnion, SWriteBody  # noqa: E402
from tests.util import standard_graph  # noqa: E402

G = standard_graph("l2")
V3 = T("Vector", 3)
Q = dict(corpus.Q)

# The asset changed after the core spike translated it (rfc/gq-3, 2026-11-08):
# `around` binds `$near`, `colleagues_of_colleagues` wraps its inner reads in
# `match`, and `weighted` uses `distance`.
Q["around"] = q("around", [("h", STRING)], read(
    [bind("p", "Person", handle=p("h")), tri("p", "knows", "friend", either=True),
     tri("p", "knows", "second", hops=(2, 2)), bind("near", "Person"),
     tri("p", ("knows",), "near", hops=(1, 3))],
    [pr("friend", "name"), (count(v("second")), "seconds"), (count(v("near")), "near")]))
Q["colleagues_of_colleagues"] = q("colleagues_of_colleagues", [("of", STRING)], read(
    [SFrom(inline([SFrom(SCall("colleagues", (p("of"),)))], [(v("q"), "c")])),
     SFrom(inline([SFrom(SCall("colleagues", (pr("c", "handle"),)))], [(v("q"), "r")]))],
    [pr("r", "name")], distinct=True))

PROBES = {}


def probe(name, query, *helpers):
    PROBES[name] = (query, helpers)


probe("inner_rebinding_in_not", q("x", [], read(
    [bind("p", "Person"), SNot((bind("p", "Person", handle=L("bob")),))], [pr("p", "name")])))
probe("future_name_in_block", q("x", [], read(
    [bind("p", "Person"), SNot((tri("p", "knows", "x"),)), bind("x", "Person", handle=L("q"))],
    [pr("p", "name")])))
probe("from_collision", q("x", [], read(
    [bind("c", "Company"), SFrom(inline([bind("x", "Company")], [(v("x"), "c")]))], [v("c")])))
probe("union_mismatch", q("x", [], read(
    [SFrom(SUnion((inline([bind("p", "Person")], [(pr("p", "age"), "c")]),
                   inline([bind("x", "Company")], [(pr("x", "name"), "c")]))))], [v("c")])))
probe("union_nullable", q("x", [], read(
    [SFrom(SUnion((inline([bind("p", "Person")], [(pr("p", "city"), "c")]),
                   inline([bind("x", "Company")], [(pr("x", "name"), "c")]))))], [v("c")])))
probe("not_over_recursion", q("bad", [("from", STRING)], read(
    [SFrom(SUnion((inline([bind("p", "Person", handle=p("from"))], [(v("p"), "m")]),
                   inline([bind("q", "Person"), SNot((SFrom(SCall("bad", (p("from"),))), tri("m", "knows", "q")))],
                          [(v("q"), "m")]))))], [v("m")], distinct=True)))
probe("exists_over_recursion", q("members", [("from", STRING)], read(
    [SFrom(SUnion((inline([bind("p", "Person", handle=p("from"))], [(v("p"), "m")]),
                   inline([bind("q", "Person"), SExists((SFrom(SCall("members", (p("from"),))), tri("m", "knows", "q")))],
                          [(v("q"), "m")]))))], [v("m")], distinct=True)))
probe("computed_recursive_arg", q("down", [("n", I64)], read(
    [SFrom(SUnion((inline([bind("p", "Person")], [v("p")]),
                   inline([SFrom(SCall("down", (Arith("-", p("n"), L(1)),)))], [v("p")]))))],
    [v("p")], distinct=True)))
DIST_BASE = inline([bind("p", "Person", handle=p("from")), tri("p", "knows", "q")], [v("q"), (L(1), "d")])
probe("invented_value_without_min", q("dist", [("from", STRING)], read(
    [SFrom(SUnion((DIST_BASE, inline([SFrom(SCall("dist", (p("from"),))), tri("q", "knows", "r")],
                                      [(v("r"), "q"), (Arith("+", v("d"), L(1)), "d")]))))],
    [v("q"), v("d")], distinct=True), budget=50))
probe("min_without_rounds", q("dist", [("from", STRING)], read(
    [SFrom(SUnion((DIST_BASE, inline([SFrom(SCall("dist", (p("from"),))), tri("q", "knows", "r")],
                                      [(v("r"), "q"), (Arith("+", v("d"), L(1)), "d")]))))],
    [v("q"), (agg("min", v("d")), "d")])))
probe("min_with_rounds", q("dist", [("from", STRING)], read(
    [SFrom(SUnion((DIST_BASE, inline([SFrom(SCall("dist", (p("from"),))), tri("q", "knows", "r")],
                                      [(v("r"), "q"), (Arith("+", v("d"), L(1)), "d")]))))],
    [v("q"), (agg("min", v("d")), "d")]), budget=50))
probe("filter_on_max_column", q("dist", [("from", STRING)], read(
    [SFrom(SUnion((DIST_BASE, inline([SFrom(SCall("dist", (p("from"),))), where(cmp("<", v("d"), L(3))),
                                       tri("q", "knows", "r")],
                                      [(v("r"), "q"), (Arith("+", v("d"), L(1)), "d")]))))],
    [v("q"), (agg("max", v("d")), "d")]), budget=50))
probe("age_times_two_into_age", q("w", [("h", STRING)], SWriteBody(
    (bind("p", "Person", handle=p("h")),), (SUpdate("p", (("age", Arith("*", pr("p", "age"), L(2))),)),))))
probe("update_and_delete_one_write", q("w", [("h", STRING)], SWriteBody(
    (bind("p", "Person", handle=p("h")), tri("p", "worksAt", "c", k="k")),
    (SDelete("k"), SUpdate("p", (("city", Lit(None, STRING.null())),))))))

# What the Rust checker answers for the same inputs (edition-3 text in
# typeck3_tests.rs). "ok" or the code; the core spike's own code names
# (TUNION, T_WRITE_MIX, …) are mapped by the table below.
RUST = {
    "inner_rebinding_in_not": "T68",
    "future_name_in_block": "ok",
    "from_collision": "T68",
    "union_mismatch": "T73",
    "union_nullable": "ok",
    "not_over_recursion": "T70",
    "exists_over_recursion": "ok",
    "computed_recursive_arg": "T70",
    "invented_value_without_min": "T70",
    "min_without_rounds": "T70",
    "min_with_rounds": "ok",
    "filter_on_max_column": "T70",
    "age_times_two_into_age": "ok",
    "update_and_delete_one_write": "T76",
}
# The asset's edition-3 declarations: the Rust verdicts of
# `every_edition3_example_of_the_asset_type_checks`.
RUST_ASSET_REFUSED = {"names": "T1", "mentions": "T27", "weighted": "T68"}


def verdict(query, extra=()):
    prog = Program(G.schema, [query, *extra, *[Q[n] for n in ("colleagues",) if query.name != "colleagues"]])
    try:
        if isinstance(query.body, SWriteBody):
            # A write is typed by its desugaring and refused at run time on
            # the fixture graph (the interpreter has no static write typer).
            prog.write(query.name)
            params = {n: "alice" if t == STRING else 1 for n, t in query.params}
            if query.name == "hire":
                params = {"h": "zed", "slug": "acme"}
            if query.name == "close_company":
                params = {"slug": "acme"}
            if query.name == "tag_near":
                params = {"q": (1.0, 0.0, 0.0)}
            prog.run(query.name, G, params)
        else:
            prog.core(query.name)
        return "ok"
    except GQError as e:
        # The core spike's own names for codes this checker numbers.
        return {"TUNION": "T73"}.get(e.code, e.code)
    except Exception as e:  # noqa: BLE001 - an interpreter limit is reported, not hidden
        return f"error {type(e).__name__}: {e}"


def main():
    rows = []
    for name, query in sorted(Q.items()):
        if name.startswith("retire_e2") or name in ("big_employers",):
            continue
        mine = RUST_ASSET_REFUSED.get(name, "ok")
        rows.append(("asset " + name, verdict(query), mine))
    for name, (query, helpers) in PROBES.items():
        rows.append(("probe " + name, verdict(query, helpers), RUST[name]))
    disagree = 0
    for name, theirs, mine in rows:
        same = theirs == mine
        disagree += not same
        print(f"{'  ' if same else '≠ '}{name:45} core={theirs:12} rust={mine}")
    print(f"{len(rows)} inputs, {disagree} disagree")


main()
