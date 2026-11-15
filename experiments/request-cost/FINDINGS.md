# Findings

All numbers are warm steps unless marked otherwise. "Storage" is the GQT DST
`s3-like` simulated time (17 ms per request, bytes at 50 MiB/s), which counts
no CPU. "CPU" is the release build's warm wall time for the same plan on a
local graph of the same shape (`results/local-cpu`). "Est." is their sum.
"Real" is the release build's warm median on RustFS through a proxy that adds
17 ms to every request (`results/realstore`). The tables in
`results/RESULTS.md` are generated. The default object-store profile is
`latency_units = 20,000` and `bytes_per_unit = 3,300`.

## Verdict

Request-aware cost picks faster plans on object storage where a rows-only
plan is deep in serial `id_lookup` slices. The two-hop chain from a selective
City runs 6.1× faster on the real store at 200,000 rows (1,171 → 192 ms) and
2.0× at 20,000 rows (200 → 98 ms). A hub company runs about 15× faster once
its access path flips (3,658 → 90 ms storage, about 4,076 → 270 ms est.).
The local profile prices nothing, and no local plan changed: all 2,473
corpus query steps kept their choices under the profile. It does not deliver
the RFC's motivating case. At the calibrated latency the selective-start
anchor keeps its plan, and that is right: the flip the RFC wanted saves
storage time but costs more CPU (est. 195 → 226 ms, real 212 → 201 ms for
the count, 209 → 226 ms for three columns). The faster plan is a different
root, which per-decision pricing with today's row constants never chooses.

## Per shape: 200,000 Person rows, 50 fragments

| Shape, written order | Plan before (rows only) | Plan after (L = 20,000) | Requests | Waves | KB read | Storage ms | CPU ms | Est. ms | Real ms |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| a count, `$c` last | scan, p>c csr, hj $c | same | 103 | 3 | 4,832 | 52.9 | 105 | 158 | 168–178 |
| a count, `$c` first | key, c>p idx, lookup $p | same (hj $p at L ≥ 33–40K) | 271 (195) | 9 (6) | 6,375 (7,118) | 161.9 (107.9) | 33 (118) | 195 (226) | 212–244 (201) |
| b 3 columns, `$c` last | scan, p>c csr, hj $c | same | 53 | 3 | 6,592 | 54.1 | 136 | 190 | 189–195 |
| b 3 columns, `$c` first | key, c>p idx, lookup $p | key, c>p idx, **hj $p** | 246 → 145 | 9 → 5 | 8,179 → 8,878 | 171.5 → 91.5 | 36 → 147 | 208 → 239 | 209 → 226 |
| c chain, city last | scan, csr, hj $c, csr, hj $t | same | 104 | 4 | 4,834 | 70.0 | 181 | 251 | 260–279 |
| c chain, city first | key, t>c idx, lookup $c, c>p idx, lookup $p | key, **t>c csr**, lookup $c, **c>p csr**, **hj $p** | 1,761 → 104 | 45 → 5 | 43,670 → 4,834 | 809.9 → 88.9 | 198 → 107 | 1,008 → 196 | 1,171 → 192 |
| d `not { $p worksAt $c {key} }` | key_set, c>p idx | same | 195 | 6 | 7,118 | 107.0 | 110 | 217 | 198 |
| d `not { $p worksAt $c, $p {age: 0} }` | correlated, c>p csr→idx, lookup $p | same | 22,857 | 1,558 | 459,957 | 28,044 | n/a | n/a | n/a |

Cold steps (the first two queries after a `--- restart`): `$c` last 623
requests, 31 waves, 550 ms; `$c` first 291 requests, 21 waves, 406 ms. Later
steps of the cold pass reuse what earlier ones loaded.
Cold plans and their changes match the warm ones. The cold chain went from
1,773 requests, 48 waves and 864 ms to 104, 5 and 89.

Under `--model unit` the ranking is the same as under `s3-like` for every
step: unit ms equals the wave count, and every flip above lowers it (9 → 5,
45 → 5).

## Per shape: 20,000 Person rows, 50 fragments (L = 20,000)

| Shape, order | Plan change | Requests | Waves | Storage ms | Est. ms | Real ms |
|---|---|---:|---:|---:|---:|---:|
| a count, `$c` last | none | 103 | 3 | 52.1 | 70 | 75 |
| a count, `$c` first | none at 20K (c>p csr at L ≥ 24–32K: 135 → 91 req, 5 → 4 waves, 88 → 70 ms, real 94 → 66) | 135 | 5 | 88.1 | 102 | 94 |
| c chain, city first | lookup → hj $p, idx → csr | 443 → 104 | 13 → 5 | 232.1 → 88.1 | 271 → 106 | 200 → 98 |
| d correlated, age 0 | c>p csr→idx becomes csr | 4,264 → 4,214 | 160 → 159 | 2,878 → 2,860 | n/a | n/a |

## Hub: one company holds 50,000 of 200,000 WorksAt edges

| Order | Plan before | Plan after (L = 20,000) | Requests | Waves | Storage ms | Est. ms |
|---|---|---|---:|---:|---:|---:|
| a count, `$c` first | key, idx, lookup $p | same (hj $p at L ≥ 33–40K: 618 → 188 req, 203 → 6 waves, 3,652 → 107 ms) | 618 | 203 | 3,652 | 4,048 |
| b 3 columns, `$c` first | key, idx, lookup $p | key, idx, **hj $p** | 960 → 138 | 203 → 5 | 3,658 → 90.5 | 4,076 → 270 |
| `$c` last (both) | scan, csr, hj $c | same | 103 / 53 | 3 | 53–54 | 153–190 |

## Small control: 200 Person rows, 1 fragment (L = 20,000)

Every Expand moves from `indexed_scan` to `csr` (a 200-edge CSR in one wave
against an index wave plus a take). Each step saves one request and one wave,
for example 6 → 5 requests, 4 → 3 waves and 70 → 52 ms (`results/RESULTS.md`).
The CPU difference is below a millisecond.

## Flip points

Plan-only bisection on the release harness, and DST runs at 3,000, 12,000,
20,000, 48,000 and 192,000:

| Decision | Flips at latency_units |
|---|---|
| chain: Person destination of about 10,000 ids, id_lookup → hash_join | about 1,500–3,300 (every point swept) |
| CSR for a 200-edge table (LocatedIn, small graph) | about 150 |
| 20K: Expand from one company (100 edges, 20,000-edge table), idx → csr | 24,000–32,000 |
| 200K anchor, 3-column destination (1,000 ids), id_lookup → hash_join | 16,000–18,000 |
| 200K anchor, count destination | 32,000–40,000 |
| 200K Expand from one company (1,000 edges, 200,000-edge table), idx → csr | about 299,000; never inside the calibration band |

The calibration band is 12,000–44,000 (`CALIBRATION.md`). Only the chain
and the small-table CSR flip at every point of the band. The anchor
decisions flip inside it, so their outcome depends on the least stable
measurement. The RFC acceptance bar (warm 200K selective start at most
6 waves and 110 ms, scan start unchanged at 3 waves and 53 ms) is met at
L ≥ 40,000: 6 waves and 107.9 ms, with the scan start unchanged. It is not
met at the calibrated median. Meeting it also makes that query slower in
CPU-inclusive time.

## Step 5: would the model pick the right root?

`anchor.py` prices each written order's plan with the model plus a root-scan
price (a key probe is 1 row and 1 wave; a scan is its rows and
`ceil(F × C / N)` waves). Measured best, by unit waves, s3-like storage and
s3-like plus CPU:

- **a (both sizes), hub:** measured, the scan root (`$c` last) is fastest
  under all three measures. The model prefers the key root at every latency,
  and at no latency up to 2,000,000 does it prefer the scan root. The
  scan-rooted plan's 200,000 scan rows and 300,000 CSR-build rows are
  modelled as about 816 ms of CPU at 1.63 µs. Measured, they take 105 ms,
  because a scan row is 0.5 units and the warm CSR is free.
- **c chain, 200K:** storage and waves favour the scan root (70 vs 89 ms;
  4 vs 5 waves). CPU-inclusive time favours the key root (196 vs 251 ms est.,
  192 vs 260–279 real). The model picks the key root, which is right on
  total time and wrong on storage-only time.
- **c chain, 20K:** waves, storage and est. favour the scan root (4 vs 5;
  70 vs 88 ms; 96 vs 106 ms). The real store is a tie within noise (104 vs
  98 ms). The model picks the key root.

With the calibrated row weights (scan 0.3, CSR build 0.25, id_lookup 9.9 per
indexed row) and even with a warm CSR, the model still never prefers the scan
root for these shapes. A key-rooted plan can adopt the same hash join and CSR
as the scan root, so it ties or wins in rows. Its extra serial waves (a key
probe of about 2 waves, then a hash-join build that starts only after the
probe chain's first batch) are not composed along the critical path. Summed
per decision, the model's wave counts are within one of the measured counts
(scan root 4 vs 3; key root with idx and lookup 8 vs 9; idx and hj 5 vs 6;
chain after 4 vs 5). The ranking error comes from the row term and from
missing critical-path composition, not from the per-operator wave model.

## What the model gets wrong or cannot see

1. **CPU is half the story, and the row constants are off.** Measured in
   indexed-probe rows, an `id_lookup` row costs 17–22 (the model says 8), a
   scan row 0.5 (1), and a CSR build edge 0.4 (1.5). The
   storage-only GQT numbers behind the RFC's motivation omit about 100 ms of
   CPU for a 200,000-row scan and about 85 ms for a hash build over Person.
   The rows-only constants also mislead local plans: the chain's
   object-store plan is 1.9× faster locally (198 → 107 ms). The local
   profile cannot fix that without changing local plans.
2. **The hub.** The access path is priced from the average-degree estimate
   (1,000) against an actual 50,789. At the default latency the count
   variant keeps `id_lookup`: 203 waves, 3.65 s storage. There is no run-time
   access-path switch (the RFC's unresolved question 5).
3. **Unknown frontiers.** In a correlated block the destination's frontier
   estimate is unknown, and the access path then always takes `id_lookup`.
   `d company without young` looks up all 200,000 Person rows in 782 slices:
   22,857 requests, 1,558 waves, 28 s simulated. The profile cannot help a
   decision that has no estimate. Passing the outer estimate in, as
   `chain_rows` does for the anti-join strategy, would.
4. **CSR scope.** One CSR build covers every edge type the plan traverses.
   The model prices only the deciding Expand's type. In the chain, a
   200-edge LocatedIn CSR (priced at about 1 wave) makes the
   200,000-edge WorksAt CSR "cached" for the next Expand. Warm, that
   happened to be right. Cold, it builds the large CSR unpriced.
5. **Fragment-count proxies.** Waves come from `F × C / N`. The projected
   column count is the planner's projection (a count query reads fewer
   columns), which is why the anchor's count and 3-column variants flip at
   different latencies (33–40K vs 16–18K). Fragments touched by an `id_lookup`
   slice are assumed to be `min(F, 256)`, which is right for uniform ids and
   wrong for clustered ones.
6. **Cold caches.** The model prices the warm steady state. The cold queries
   cost 2–10× the warm waves (31 vs 3 for the scan start, 21 vs 9 for the
   key start). The ranking held cold in every measured case.
7. **Calibration noise.** `latency_units` is 20 ms over the least stable
   measurement (an indexed hop minus a CSR hop), which varied 3.5× across
   five runs. Real-store timings of one plan under different pins varied
   ±15% with 5 repetitions (for example 212 vs 244 ms).

## Gates and corpus

- No local plan line moved under the profile: the 2,473 corpus query steps
  (both replays) had identical plan choices before the profile commit and
  after the model commit.
- The `csr_cached` correction moved no recorded mode. It moved three
  run-time sides, inner-block hops that now run `indexed_scan` instead of
  building the CSR, with identical rows: `issue_659_compiler_scopes`
  steps 28 and 29, and `v2/planner/anti_join_key_set_from_a_bounded_binding`
  step 6. The RFC's `AntiJoin` arm alone moved nothing. The over-claim it
  names also comes from the Expand arm, where a costed `csr` over an unknown
  frontier is re-decided at run time.
  `v2/planner/expand_after_a_correlated_block_prices_the_csr_build.gqt` pins
  both sides.
