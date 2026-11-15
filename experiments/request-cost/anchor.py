#!/usr/bin/env python3
"""Step 5: what the request-aware model predicts for each written order.

Re-rooting is not implemented, so each written order's plan is priced as the
planner would build it from that root, with the model in cost.rs (today's row
constants, the request model's waves, bytes at 5,000 per unit) plus a price
for the root scan the model does not cover today: a key probe is one row and
one wave, a full scan its rows and ceil(F*C/N) waves. Each alternative inside
a plan takes the cheaper side, as the planner does.
"""
import math

N = 64
BPU = 5000
# Row weights in indexed-probe rows. RFC: today's constants. CAL: this host's
# measured costs (scan row 0.30, CSR build edge 0.25, id_lookup row 9.9).
# WARM additionally prices the CSR as built, the warm server's state.
RFC = dict(scan=1.0, csr=1.5, lookup=8.0, warm=False)
CAL = dict(scan=0.30, csr=0.25, lookup=9.9, warm=False)
WARM = dict(scan=0.30, csr=0.25, lookup=9.9, warm=True)
W = dict(RFC)

def waves(requests):
    return math.ceil(requests / N)

def scan(rows, frags, cols, nbytes, L):
    return W["scan"] * rows + waves(frags * cols) * L + nbytes / BPU

def key_probe(L):
    return 1 + 1 * L

def expand(frontier, edges, src, frags, ebytes, cached, L, cap=1024):
    fanout = edges / src
    idx = frontier * fanout + (1 + waves(2 * min(frags, max(1, math.ceil(frontier * fanout))))) * L
    csr = 0 if (cached or W["warm"]) else W["csr"] * edges + waves(2 * frags) * L + ebytes / BPU
    if frontier > cap:
        return csr, "csr"
    return (idx, "idx") if idx < csr else (csr, "csr")

def dest(frontier, rows, frags, cols, nbytes, L):
    slices = max(1, math.ceil(frontier / 256))
    il = W["lookup"] * frontier + slices * waves(cols * min(frags, 256)) * L
    hj = W["scan"] * rows + waves(cols * frags) * L + nbytes / BPU
    if L == 0 and W is RFC_ACTIVE:
        return (hj, "hj") if rows <= 8 * frontier else (il, "lookup")
    return (hj, "hj") if hj <= il else (il, "lookup")

def two_hop(P, C, Pf, cols, L, person_bytes, edge_bytes):
    """$p -WorksAt-> $c {slug} ; returns {order: (units, plan)}."""
    # $c last: scan Person, expand p->c (frontier P > cap: csr), dest Company.
    e, em = expand(P, P, P, Pf, edge_bytes, False, L)
    d, dm = dest(P, C, 1, 1, 0, L)
    last = scan(P, Pf, cols, person_bytes, L) + e + d
    # $c first: key probe, expand c->p from 1 (fanout P/C), dest Person.
    e2, em2 = expand(1, P, C, Pf, edge_bytes, False, L)
    d2, dm2 = dest(P / C, P, Pf, cols, person_bytes, L)
    first = key_probe(L) + e2 + d2
    return {"c_last": (last, f"scan, {em}, {dm}"), "c_first": (first, f"key, {em2}, {dm2}")}

def chain(P, C, T, Pf, L, person_bytes, edge_bytes):
    """$p -WorksAt-> $c -LocatedIn-> $t {slug}."""
    e1, m1 = expand(P, P, P, Pf, edge_bytes, False, L)
    d1, n1 = dest(P, C, 1, 1, 0, L)
    e2, m2 = expand(P, C, C, 1, 4000, m1 == "csr", L)  # frontier capped at C rows
    d2, n2 = dest(C, T, 1, 1, 0, L)
    last = scan(P, Pf, 1, person_bytes, L) + e1 + d1 + e2 + d2
    f1, k1 = expand(1, C, T, 1, 4000, False, L)
    g1, j1 = dest(C / T, C, 1, 1, 0, L)
    f2, k2 = expand(C / T, P, C, Pf, edge_bytes, k1 == "csr", L)
    g2, j2 = dest(C / T * P / C, P, Pf, 1, person_bytes, L)
    first = key_probe(L) + f1 + g1 + f2 + g2
    return {"city_last": (last, f"scan, {m1}, {n1}, {m2}, {n2}"),
            "city_first": (first, f"key, {k1}, {j1}, {k2}, {j2}")}

# Measured, warm: (unit-model waves, s3-like ms storage only, s3-like ms + local release CPU ms)
# keyed by (shape, order, latency) where the plan differs by latency.
MEASURED = {
    ("a200k", "c_last"): {0: (3, 52.9, 52.9 + 105), 12000: (3, 52.9, 158), 48000: (3, 52.9, 158)},
    ("a200k", "c_first"): {0: (9, 161.9, 161.9 + 33), 12000: (9, 161.9, 195), 48000: (6, 107.9, 107.9 + 118)},
    ("chain200k", "city_last"): {0: (4, 70.0, 70 + 181), 12000: (4, 70.0, 251), 48000: (4, 70.0, 251)},
    ("chain200k", "city_first"): {0: (45, 809.9, 809.9 + 198), 12000: (5, 88.9, 88.9 + 107), 48000: (5, 88.9, 88.9 + 105)},
    ("a20k", "c_last"): {0: (3, 52.1, 52.1 + 18.0), 12000: (3, 52.1, 70.1), 48000: (3, 52.1, 52.1 + 16.6)},
    ("a20k", "c_first"): {0: (5, 88.1, 88.1 + 14.2), 12000: (5, 88.1, 88.1 + 14.6), 48000: (4, 70.1, 70.1 + 7.4)},
    ("chain20k", "city_last"): {0: (4, 70.0, 70 + 26.3), 12000: (4, 70.0, 96.2), 48000: (4, 70.0, 94.7)},
    ("chain20k", "city_first"): {0: (13, 232.1, 232.1 + 38.7), 12000: (5, 88.1, 88.1 + 18.2), 48000: (5, 88.1, 88.1 + 17.6)},
    ("hub", "c_last"): {0: (3, 52.9, 52.9 + 99.6), 12000: (3, 52.9, 52.9 + 101.1), 48000: (3, 52.9, 52.9 + 110.6)},
    ("hub", "c_first"): {0: (203, 3652.0, 3652 + 396), 12000: (203, 3652.0, 3652 + 378), 48000: (6, 106.9, 106.9 + 150)},
}

RFC_ACTIVE = None


def main():
    global W, RFC_ACTIVE
    for label, weights in (("RFC row constants", RFC), ("calibrated row weights", CAL), ("calibrated, CSR warm", WARM)):
        W = weights
        RFC_ACTIVE = W if weights is RFC else None
        print(f"\n#### {label}\n")
        table()


def table():
    shapes = {
        "a200k": lambda L: two_hop(200_000, 200, 50, 1, L, 2_400_000, 3_000_000),
        "a20k": lambda L: two_hop(20_000, 200, 50, 1, L, 240_000, 300_000),
        "chain200k": lambda L: chain(200_000, 200, 20, 50, L, 2_400_000, 3_000_000),
        "chain20k": lambda L: chain(20_000, 200, 20, 50, L, 240_000, 300_000),
        # The hub's estimate is the average degree: the model prices it as a200k.
        "hub": lambda L: two_hop(200_000, 200, 50, 1, L, 2_400_000, 3_000_000),
    }
    print("| shape | L | model units per order (plan) | model picks | measured best: unit waves / s3 storage / s3 + CPU |")
    print("|---|---:|---|---|---|")
    for name, fn in shapes.items():
        for L in (0, 12000, 48000):
            costs = fn(L)
            pick = min(costs, key=lambda k: costs[k][0])
            orders = list(costs)
            m = {o: MEASURED[(name, o)][L] for o in orders}
            best = [min(orders, key=lambda o: m[o][i]) for i in range(3)]
            cells = "; ".join(f"{o} {costs[o][0]:,.0f} ({costs[o][1]})" for o in orders)
            marks = " / ".join(("**" + b + "**" if b == pick else b) for b in best)
            print(f"| {name} | {L:,} | {cells} | {pick} | {marks} |")
    # The latency at which the model first prefers the scan root.
    for name in ("a200k", "chain200k", "a20k", "chain20k"):
        fn = shapes[name]
        flip = next((L for L in range(0, 2_000_001, 1000)
                     if (lambda c: min(c, key=lambda k: c[k][0]))(fn(L)) in ("c_last", "city_last")), None)
        print(f"{name}: the model first prefers the scan root at L = {flip}")

if __name__ == "__main__":
    main()
