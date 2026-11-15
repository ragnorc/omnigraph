---
rfc: "2026-10-13-request-aware-plan-cost"
title: "Request-aware plan cost"
track: maintainer
status: draft
implementation: not-started
authors:
  - Ragnor Comerford
created: 2026-10-13
updated: 2026-11-15
discussion: https://github.com/ModernRelay/omnigraph/pull/996
supersedes: []
superseded_by: []
blocked_on: []
---

# RFC: Request-aware plan cost

> A term set in ***bold italics*** is being defined at that exact spot;
> it is used in plain text everywhere after.

Code references are at `main` `4f7c2076`; Lance references are at the pinned
13.0.0 crates. Measurements name their instrument and conditions where they
appear. The measurements of 2026-11-15 come from a prototype of this design
on the fork branch `exp/request-aware-cost` (head `4a040cc0`); its write-up,
fixtures and scripts are under `experiments/request-cost/` on that branch.

## Summary

The v2 planner prices every cost-driven choice in rows. This RFC prices it
in estimated time on the critical path of one query: the CPU work of the
rows an alternative processes, plus the ***request waves*** it waits through
in series, plus the bytes it moves. A request wave is a set of object-store
requests the engine issues together, so that they overlap and the wave
costs about one round trip. A ***storage profile***, chosen from the
snapshot's storage kind when the query is planned and recorded in the plan,
supplies every constant: the per-row weights, the wave latency, the
parallelism and the transfer rate.

The local profile is today's row constants with a wave latency of zero, so
on a local filesystem, and under DST, every plan is the plan the planner
builds today. The object-store profile, for S3-compatible and Azure roots,
carries row weights measured on a reference host and a wave latency of
about 20 ms. Both profiles are the same formula.

Two decisions use the model: the traversal mode of an `Expand` (per-hop
indexed scans or the in-memory CSR), at plan time and in its run-time
re-decisions, and the access path of a traversal destination (`id_lookup` or
`hash_join`), which gains a declared run-time switch. The RFC does not
choose which binding roots a pattern. Measured, the root is the larger
lever for the query that motivated this RFC, and choosing it needs more
than this model (see "What this does not fix: the root").

Every plan returns the same rows as before. Only operator choice changes,
and both alternatives of every affected decision already have identical
semantics.

## Motivation

### The rows-only model picks slow plans on object storage

`omnigraph_planner::cost` prices alternatives in rows: an indexed hop costs
`hops × frontier × fanout × probe_factor`, a CSR `CSR_BUILD_FACTOR (1.5) ×
|E|`, and a destination is read by `hash_join` only when its table holds at
most `HASH_JOIN_RATIO (8)` rows per estimated frontier row. On a local
filesystem a request costs about 0.1 ms and rows dominate. On object
storage a request costs about 20 ms, while the engine overlaps up to
Lance's I/O parallelism of 64 requests. There, how many times a plan waits
on storage in series can dominate, and rows do not measure it.

The clearest case is a chain of dependent lookups. On Person(200,000 rows,
50 fragments)–WorksAt–Company(200)–LocatedIn–City(20), the query that
counts the people working at companies in one City, with the City declared
first, today runs a key lookup, an indexed hop and an `id_lookup` of about
1,000 Companies, then an indexed hop and an `id_lookup` of about 10,000
Persons. Its 10,000-id lookup alone is about 40 slices of 256 ids, awaited
one after another:

| Warm step, City declared first | Requests | Waves | Storage ms | CPU ms | Real store ms |
|---|---:|---:|---:|---:|---:|
| today's plan (indexed hops, `id_lookup`) | 1,761 | 45 | 810 | 198 | 1,171 |
| CSR hops, `hash_join` Person | 104 | 5 | 89 | 107 | 192 |

"Storage ms" is GQT DST `--measure --model s3-like` simulated time (17 ms
per request plus bytes at 50 MiB/s), which counts no CPU. "CPU ms" is the
release build's warm wall time for the same plan on a local graph of the
same shape. "Real store ms" is the release build's warm median on RustFS
behind a proxy that adds 17 ms to every request. Rows are identical. The
20,000-row version of the same chain goes from 200 to 98 ms on the real
store. A hub, one Company holding 50,000 of 200,000 WorksAt edges, read
with three columns, goes from about 4,076 to 270 ms estimated (storage plus
CPU) once its destination is read by `hash_join`.

### The request cost is known and stable

The in-region cost of one request was measured from inside the serving
container on Railway against its S3-compatible bucket, 10 requests per
row on one reused connection:

| Request | Median | Range |
|---|---:|---:|
| HEAD | 19–20 ms | 17–26 ms |
| LIST of 1,000 keys (308 KB) | 40 ms | 39–42 ms |

GQT's `s3-like` model (17 ms per request) agrees with it closely enough to
price waves, and the per-request cost is a property of the store, not of
the data.

### Today's row constants are not a time model

A release-build calibration on the reference host (Calibration below)
measured each row kind's CPU cost. In scanned rows (0.50 µs each, stable
within 5% over five runs):

| Row kind | Today's constant, in its own unit | Measured, in scanned rows |
|---|---:|---:|
| scanned row, hash-join build row | 1 | 1 |
| `id_lookup` row | 8 scanned rows (`HASH_JOIN_RATIO`) | 33 (count) to 44 (three columns) |
| CSR build edge | 1.5 indexed-probe rows (`CSR_BUILD_FACTOR`) | 0.8 |
| indexed-probe row | 1 (the Expand decision's unit) | about 2 (0.9–3.3 across runs) |

So the rows-only model underprices `id_lookup` about 4–5× and overprices a
CSR build about 4×. A latency term added to today's constants keeps those
errors. On the 20,000-row graph, the `Expand` from one Company over a
20,000-edge table is faster on the CSR (real store 94 → 66 ms), but with
today's constants it flips only at about 1.5× the calibrated latency, so
the prototype's first model, which did exactly that, kept the slower
indexed hop. And a plan rooted at a 200,000-row scan is priced at about
eight times its measured CPU (816 against 105 ms), which matters as soon
as the planner compares roots. The object-store profile needs its own row
weights, not only a latency.

## User and operational behavior

Results do not change. Plans on object storage do: a traversal whose
destination needs many `id_lookup` slices may read it by `hash_join`, and a
traversal over a small edge table may run on the CSR. Explain shows why. It
adds a `storage_profile` plan row naming the profile and its constants, and
statistics rows for the priced alternatives of each affected decision. An
illustration of the shape, not of the final format:

```text
plan  storage_profile   object_store latency=40000 parallelism=64 bytes_per_unit=1700
expand $c WorksAt $p: mode csr   (indexed: … units, 3 waves; csr: … units, 2 waves)
scan Person as $p: access hash_join   (id_lookup: … units, 40 waves; hash_join: … units, 2 waves)
statistics  fragments(node:Person)  50  manifest fragments at the pinned version
```

Explain also prints `ExpandCostInputs` for every costed `Expand`, which
closes a gap: today those inputs reach the plan but not the explain
document. An `id_lookup` that switched to `hash_join` at run time reports it
as `ran`.

A plan carries its profile. A plan replayed on another backend runs with
the recorded profile, as it runs with its recorded memory limit; nothing
about correctness depends on it.

A GQT case can pin the profile with a `# storage_profile: object_store`
header, optionally `# storage_profile: object_store latency=N`, beside
`# traversal:`, so a DST or local case can assert the plan an object-store
graph would get. The pin is harness-only, as the traversal pin is: there is
no `set` statement, and a served run refuses it.

Operators need not do anything. The profile follows the graph's URI.

## Design

### Definitions

- ***Wave***: a set of requests the engine issues without waiting on each
  other. A wave of `R` requests on a store of I/O parallelism `N` costs
  `⌈R / N⌉` round trips.
- ***Serial depth***: the number of waves an alternative waits through in
  series. Its request cost is `serial depth × latency`.
- ***Storage profile***: the per-row weights, the wave latency, the
  parallelism `N` and the bytes per unit that convert an alternative's rows,
  waves and bytes into the planner's cost unit. It is chosen from
  `omnigraph_storage::StorageKind` when the query is planned.

### The objective

The model estimates the time of one query on an idle store, along the
critical path of the alternative being priced. It does not price total
requests. Under concurrent load, total requests matter too (Lance throttles
them, and a provider charges per GET), but in every measured case the
faster alternative also issued fewer or about as many requests (the chain:
1,761 against 104), so the two goals agree where this RFC changes plans.

Acceptance is judged on storage time plus CPU time, or on a real store.
Storage time alone overstated the original motivating gap: GQT's simulated
clock counts no CPU, and the 200,000-row scan and the hash build it left
out cost about 100 ms and 85 ms.

### The model

```text
cost(alternative) = Σ_kinds rows_k × weight_k
                  + serial depth × latency
                  + bytes / bytes_per_unit
```

The unit is one scanned row on the reference host. The row kinds are the
scanned row (which also counts a hash-join build row), the `id_lookup` row,
the indexed-probe row and the CSR build edge.

- **Local profile.** The weights are today's constants (scanned row 1,
  `id_lookup` row 8, indexed-probe row 1, CSR build edge 1.5), `latency =
  0` and `bytes_per_unit = 0`, which prices bytes at nothing. Because the
  access path and the Expand mode never compare an `id_lookup` row with an
  indexed-probe row, these weights reproduce every local decision exactly.
  That is a property the tests prove (Evidence), not an approximation.
- **Object-store profile.** The weights are the reference host's measured
  ratios: scanned row 1, `id_lookup` row `a + b × projected columns`
  (fitted to the measured 33 scanned rows for a count and 44 for three
  columns), indexed-probe row 2, CSR build edge 0.8. `latency` is 20 ms in
  scanned rows, 40,000 (0.50 µs per row). `bytes_per_unit` is about 1,700:
  64 requests in flight at 50 MiB/s each move that much in one scanned
  row's 0.50 µs.

The unit is the scanned row because its cost is the most stable
measurement. The indexed-probe row, which the prototype used, is a
difference of two similar medians and varied 3.5× across runs, so a latency
expressed in it varied from 12,000 to 44,000 units.

Serial depth comes from the request model below. It needs only facts the
planner holds without reading data: row estimates (`estimate_rows`),
fragment counts and data-file sizes from the pinned manifests `gather`
already opens, the projected column count, and `N`.

### The request model

The planner prices the warm steady state of a server: Lance's session
caches hold manifests, file footers, column metadata and index pages, and
data reads are paid on every query. Cold costs are paid once per process
and per table version. Measured cold, a query waited 2–10× as many waves as
warm, and every measured decision ranked its alternatives the same way cold
as warm.

| Operator | Serial depth (warm) | Bytes priced | Source of the shape |
|---|---|---|---|
| Table scan (`ScanExec`, pipelined) with `F` fragments, `C` projected columns | `⌈F × C / N⌉` (one GET per column page; fragments open together and share one scheduler capped at `N`) | the projected columns' share of the data-file bytes | `operators/scan/pipelined.rs`; Lance `filtered_read.rs` |
| Scan with a pushed key or index filter | 2 (an index wave, then one take wave over the matching fragments) | 0 | Lance `ScalarIndexExec`; GQT step logs |
| `id_lookup` over a frontier of `n` rows | `Σ_slices ⌈R_s / N⌉` over `⌈n / 256⌉` slices, which are awaited one at a time; `R_s` is the requests of one slice, one take per fragment holding its ids. Measured over 50 fragments, a slice took one wave (38–52 requests) with one or with three projected columns | 0 | `operators/scan/input.rs` `lookup_candidates` |
| `HashJoin` build of a destination table | the destination scan's depth. Both alternatives start at the probe's first input batch, so neither pays an extra wave | the build's projected bytes (`build_side_bytes`, as the memory admission computes them) | `operators/hash_join.rs` |
| Indexed `Expand`, per hop and orientation | `1 + ⌈2 × min(F_e, matched) / N⌉` per input batch, plus a full scan of the edge table's unindexed fragments. The lookup is one Lance scan per input batch and orientation over all of the batch's distinct source ids (`scan_neighbor_map`); orientations run one after another. An input batch is the producer's, at most the session's 8,192 rows. `EXPAND_OUTPUT_ROWS` (256) bounds only the output chunks, which issue no requests | 0 for the indexed take; the unindexed fragments' bytes | `operators/single_hop.rs` `prepare`; `engine/graph.rs` `scan_neighbor_map` |
| CSR `Expand` | the build's depth `Σ_types ⌈2 × F_e / N⌉` over every edge type the plan traverses (below), priced once per plan | the data-file bytes of every edge table it covers | `engine/mod.rs` `plan_edge_types`; `graph_index/mod.rs` |
| `MetadataCount` | 0 | 0 | `operators/metadata_count.rs` |

Measured against GQT's request counts, the model's per-plan wave counts
were within one of the measured counts in every shape. The prototype
counted `C × fragments` requests per `id_lookup` slice, which priced a
three-column slice at three waves where it took one. That alone flipped a
1,095-id three-column destination to `hash_join`, which made the query
slower (estimated 208 → 239 ms, real 209 → 226 ms). The request model
counts a slice's requests by fragment, and the instrument checks it at more
than one projected column.

**Bytes.** Bytes come from the manifests' data-file sizes, apportioned by
projected columns, and from the column statistics the planner already loads
for `hash_join` admission. The planner adds no plan-time read: widening
`calculate_data_stats` would open every fragment's data files on object
storage and change the DST storage-cost golden of every read. File sizes
overstate an edge table's `src` and `dst` bytes when the edge type has
properties. The error is small against the other terms: 4.8 MB is about
2,800 scanned rows, against 40,000 for one wave. Dependent reads (a
key-filtered scan, `id_lookup`, an indexed take) price zero bytes. That
understates them, since Lance fetches whole chunks, and it leans toward
today's choice.

**Parallelism.** `N` is the store's `io_parallelism()` read once in `gather`
and recorded, not a fixed 64: Lance reads `LANCE_IO_THREADS` on every call,
and the Lance performance guide advises lowering it for cross-region
access. The pipelined scan's 64 MiB read-ahead cap can hold fewer than `N`
requests in flight when pages are large. The model does not price that cap;
in the measured shapes it did not move the wave count by more than one.

**CSR scope.** One CSR build covers every edge type of `plan_edge_types`:
every `Expand` member of the plan in any mode, `AntiJoin` inner trees
included, built one type after another. The planner computes that set from
the logical plan before lowering and prices the build over all of it once.
The first `Expand` that chooses the CSR pays the build. Every later `Expand`
in the same plan sees `csr_cached`. Pricing only the deciding `Expand`'s
type is wrong: in the measured chain a 200-edge LocatedIn CSR, priced at
about one wave, marked the 200,000-edge WorksAt CSR as cached for the next
`Expand`, so a cold run would build the large one unpriced.

**`csr_cached`.** The flag must say only what the plan itself builds. On
today's `main` two arms of `Lowering::lower` over-claim it:

- the correlated-strategy `AntiJoin` arm (`optimizer.rs`) sets it whether
  or not its inner tree builds the CSR;
- the `Expand` arm sets it for a costed `csr` over an unknown frontier,
  which execution re-decides at run time and may run indexed.

Both arms are corrected. In the prototype this moved no recorded mode and
three run-time sides, inner-block hops that now run `indexed_scan` instead
of building the CSR, with identical rows: `issue_659_compiler_scopes` steps
28 and 29, and `v2/planner/anti_join_key_set_from_a_bounded_binding` step
6. `v2/planner/expand_after_a_correlated_block_prices_the_csr_build.gqt`
pins both sides.

The CSR is priced as a build because the planner cannot see the
process-wide `RuntimeCache` without taking a hidden input (see
Alternatives).

### Worked decisions on the object-store profile

At `latency` 40,000 scanned rows (20 ms), `N` 64, the 200,000-row graph:

| Decision | Alternatives, about | Chosen | Measured best |
|---|---|---|---|
| Chain destination, about 10,000 Person ids | `id_lookup`: 165 ms CPU + 40 waves = 965 ms; `hash_join`: 100 ms CPU + 1 wave = 120 ms | `hash_join` | `hash_join` (real 1,171 → 192 ms for the whole query) |
| Destination of 1,095 ids, three columns | `id_lookup`: 24 ms + 5 waves = 124 ms; `hash_join`: 100 ms + 3 waves = 160 ms | `id_lookup` | `id_lookup` (real 209 against 226 ms) |
| Destination of 1,095 ids, count | `id_lookup`: 18 ms + 5 waves = 118 ms; `hash_join`: 100 ms + 1 wave = 120 ms | a tie | a tie (real 212–244 against 201 ms; estimated 195 against 226 ms) |
| `Expand` from one Company over 200,000 edges | indexed: 1 ms + 3 waves = 61 ms; CSR: 80 ms + 2 waves = 120 ms | indexed | indexed |
| `Expand` from one Company over 20,000 edges (the 20,000-row graph) | indexed: 0.1 ms + 3 waves = 60 ms; CSR: 8 ms + 2 waves = 48 ms | CSR | CSR (real 94 → 66 ms) |
| `Expand` over a 200-edge table | indexed: 2 waves; CSR: under 0.1 ms + 1 wave | CSR | CSR (one request and one wave less) |

The decisions are robust where the alternatives' wave counts differ by many
waves (the chain destination, the small-table CSR) or their CPU differs by
several waves' worth (the large-table indexed hop). They are near ties
where both alternatives cost about the same CPU plus a few waves (the
1,095-id destinations, and the 20,000-edge CSR, which flips back at about
half the calibrated latency), and there either choice is within the real
store's ±15% noise. The prototype's sweep showed the same
split: the chain destination and the small-table CSR flipped at every
latency from 0.25× to 4× of the calibrated value, while the 1,095-id
destinations flipped inside that band.

### The access path's run-time switch

The access path is priced from the frontier estimate, which uses an edge
type's average fanout. A hub root, a node whose degree is far above
average, is under-priced: its frontier, and with it the number of slices,
is larger than estimated. In the measured hub the estimate was 1,000 ids
against 50,789 actual, and the count query kept `id_lookup` through 203
waves (3.65 s of storage). The traversal mode already re-decides at run
time on the observed frontier. The access path gains the same:

- `id_lookup` declares a `hash_join` fallback. Between slices, it compares
  the price of the remaining slices for the observed frontier with the
  price of a `hash_join` build under the recorded profile. When the build
  is cheaper and fits the memory admission, the rest of the frontier is
  read by `hash_join`. Rows already emitted stay emitted; the switch
  changes only how the remaining ids are read.
- The switch is a declared alternative of the plan and is reported as
  `ran`, so a replay of the same plan on the same snapshot takes the same
  side.
- Under the local profile the switch applies today's ratio rule to the
  observed frontier. That can change local run-time sides, not recorded
  plans, and it lands with its own GQT updates, as the `csr_cached`
  correction does.

### Frontiers the planner cannot estimate

Inside a correlated block the destination has no frontier estimate, so its
access path always takes `id_lookup`. In the measured block
`not { $p worksAt $c, $p { age: 0 } }`, that looked up all 200,000 Person
rows in 782 slices: 22,857 requests, 1,558 waves, 28 s simulated. Pricing
waves cannot help a decision with no estimate. The planner passes the
outer row estimate into the inner tree's access paths and `Expand` modes,
as `chain_rows` already does for the anti-join strategy, and the run-time
switch bounds what remains.

### Where each decision uses it

- **Expand mode.** `choose_expand_mode` compares `cost(indexed)` with
  `cost(csr)` under the recorded profile. The frontier and hop ceilings
  (`OMNIGRAPH_EXPAND_INDEXED_MAX_FRONTIER`, `_MAX_HOPS`) are unchanged.
  The run-time re-decisions (`decide_expand_start`, `should_switch_to_csr`,
  and the per-batch check of `single_hop`) call the same function with the
  profile carried in `ExpandCostInputs`, so execution cannot undo a
  plan-time choice by pricing with a different model.
- **Access path.** `choose_access_path` compares `cost(id_lookup)` with
  `cost(hash_join)`, and the run-time switch above uses the same function.
  The memory admission (build bytes at most a quarter of the query pool)
  stays a hard condition, not a cost.
- The anti-join strategy (`choose_anti_join_strategy`), late
  materialization and the contains-join side keep their rows-only rules
  here. Each can adopt the model in a later change without a new RFC,
  because it reads the same recorded profile.

### Recording, explain and replay

- `QuerySource::gather` reads the snapshot's root URI and classifies it
  with `storage_kind_for_uri`. `Local` maps to the local profile, and `S3`
  and `Azure` to the object-store profile. DST's `shared-memory://` roots
  classify as `Local`, as they do today. The harness pin overrides the
  choice. `gather` also reads `N` from the store.
- `Assumptions` gains a typed `storage_profile` field holding the whole
  profile, weights included, read by execution exactly as `memory_limit`
  and `gate_policy` are. The storage kind cannot be derived from the
  dataset pins, because `dataset_path` is relative to the root, so it has
  to be recorded.
- `ExpandCostInputs` carries the profile and the edge tables' fragment
  counts and bytes, and `AccessPath` decisions record their priced
  alternatives as statistics rows.
- Fragment counts are statistics rows (`fragments(<table>)`), computed from
  the manifests `gather` already opened. Like file bytes they are pure
  functions of the pins, so they need no assumption field.
- `BOUND_PLAN_VERSION` increases. The prototype left it unchanged, because
  a local plan omits the new field and serializes as before. That is not
  enough: bound plans do not refuse unknown fields, so an older binary would
  read an object-store plan, drop its profile, and re-decide at run time
  with rows only, a silent change to the replayed trace. An older bound plan
  is refused with the existing regeneration instruction.
- Replay reads the recorded profile. The scrubbed-replay rule holds: no
  input beside the bound plan and the snapshot reaches execution.

## What this does not fix: the root

The query that first motivated this RFC counts the Persons who work at the
Company with slug `c-007` on the 200,000-row graph (1,095 matches), with
`$c` declared last or first:

| Warm step | Plan | Waves | Storage ms | CPU ms | Real store ms |
|---|---|---:|---:|---:|---:|
| `$c` declared last | scan Person, CSR, `hash_join` Company | 3 | 53 | 105 | 168–178 |
| `$c` declared first | `$c` by key, indexed hop, `id_lookup` Person | 9 | 162 | 33 | 212–244 |
| `$c` first, `hash_join` Person | `$c` by key, indexed hop, `hash_join` Person | 6 | 108 | 118 | 201 |

The scan root is the fastest plan, by waves, by storage time and by storage
plus CPU, at 20,000 and at 200,000 rows and for the hub. No choice of
operators under the `$c`-first root reaches it: the `hash_join` flip the
first draft of this RFC targeted (at most 6 waves and 110 ms) saves storage
time but adds the CPU of a 200,000-row build, a wash on the real store.

Choosing the root is a separate decision, and the prototype showed that
this model is not yet enough for it. Priced per decision, the model prefers
the key root for every measured shape at every latency up to 50 times the
calibrated value, with today's or with the measured row weights, and even
with a warm CSR. A key-rooted plan can adopt the same CSR and `hash_join`
as the scan root, so it ties or wins in rows, and its extra serial waves (a
key probe of about 2 waves, then a build that starts only after the probe's
first batch) are not added along its critical path. A follow-up that
chooses the root needs:

- each plan's serial waves composed along its critical path, rather than
  summed per decision;
- a price for the root scan itself;
- the measured row weights of this RFC.

This RFC is a prerequisite of that work, not a substitute for it. Until
then, a query author who knows a binding is selective gains nothing on
object storage by declaring it first.

## Invariants

- **7. Physical acceleration is derived state.** The profile changes which
  operator runs and never what a query means. Every affected decision,
  the run-time switch included, chooses between alternatives the execution
  guide already declares semantically identical.
- **9. Query semantics are typed structures.** The profile is a typed
  field of `Assumptions` and `ExpandCostInputs`, not a string or an ambient
  read.
- **11. Bounded and observable.** Explain prints the profile and every
  priced alternative, and the run-time switch is reported as `ran`. The
  memory admission of `hash_join` is unchanged.
- **13. Evidence matches the boundary.** The cost claims carry a
  checked-in instrument and a calibration record (Evidence).
- **Deny-list: cost-blind plan choice or hidden statistics.** Every input
  of every priced decision is recorded in the plan and printed by
  explain, including the profile, its weights, `N` and the fragment counts.
- **Deny-list: a cloud-only correctness fix or a separate cloud fork.**
  This is cost, not correctness. There is one model and one code path;
  the profiles differ only in constants. No result, error or durable effect
  depends on the profile.
- **Deny-list: a logical precondition on fragment count.** Fragment
  counts only price alternatives. A wrong count yields a slower plan,
  never a wrong answer.
- **RFC 0047.** Identical inputs, now including the recorded profile, give
  the identical accepted plan. Execution chooses only declared
  alternatives and reports which it took (`ran`). Plan acceptance does not
  validate `ExpandPolicy` or `AccessPath` today, and nothing here needs it
  to.

## Compatibility and reversibility

No storage, wire or query-language change. Bound plans need regeneration
across the version bump, as every plan-envelope change does. The profile
leaves local and DST plans unchanged, and the GQT corpus with them: in the
prototype, all 2,473 corpus query steps kept their cost-driven choices
under the profile. Two parts of the rollout change local run-time sides,
never recorded modes, and each carries its own case updates: the
`csr_cached` correction and the access path's run-time switch.

Reverting takes one change of constants: setting the object-store
profile's latency and bytes to zero and its weights to the local profile's
restores today's plans everywhere, without a format change.

## Alternatives

- **Do nothing; tell authors to declare the selective binding first.** The
  measurements show that advice slows hosted reads.
- **A latency term on today's row constants.** This was the first draft
  and the prototype's first model. It keeps the 20,000-edge `Expand` on the
  slower indexed hop (real 94 against 66 ms on the CSR), prices a scan root
  at about eight times its CPU, and meets the first draft's acceptance bar
  only at twice the calibrated latency, where the flip is a wash on the
  real store.
- **Recalibrate the local profile too.** The rows-only constants also
  mislead local plans: the chain's object-store plan is 1.9× faster locally
  (198 → 107 ms). Doing it changes local plans and needs its own corpus
  review, so it is a separate decision that reads the same calibration.
- **Two models: rows for local storage, time for object storage.** This
  yields the same plans as the chosen design, but leaves two formulas to
  keep in step.
- **Measure latency at run time and feed it into planning.** The plan
  would depend on process history that it does not record, breaking the
  equal-inputs, equal-plan rule of RFC 0047. A recorded profile with a
  later operator-configurable value gets the same benefit
  deterministically.
- **Price the CSR the `RuntimeCache` already holds as free.** On the
  20,000-row graph this alone would turn the selective start's 30 requests
  into 18. But cache contents are process state, not plan input, and a
  replay in another process could choose differently. That needs its own
  decision about declared alternatives, and is out of scope here.
- **Make dependent lookups cheaper first** (larger or concurrent
  `id_lookup` slices). These are worth doing and shrink the gap, but the
  planner still has to know a wave's price to choose. They change the
  request model's constants, not the need for it.
- **Choose the root now.** See "What this does not fix: the root".

## Evidence and tests

- **Planner unit tests** (`omnigraph-planner` `cost.rs`): a table-driven
  check that the local profile reproduces today's decisions across a grid
  of frontier, fanout, table and fragment sizes, for both decisions and
  both run-time re-decisions; the object-store decisions of the worked
  table; and one test per request-model row that fails when the operator's
  shape changes, so the model cannot drift silently from the operators.
- **GQT cases** under `cases/v2/planner/`, pinned with
  `# storage_profile: object_store`: the chain asserting CSR hops and
  `hash_join` Person; the 1,095-id three-column destination and the
  200,000-edge single-Company `Expand` asserting that they do not flip; the
  small-table CSR; the `csr_cached` case above; a hub asserting the run-time
  switch as `ran`; and a correlated block asserting the outer estimate.
  Every current case keeps passing unchanged, apart from the run-time sides
  the rollout names.
- **Cost instrument**: the prototype's fixtures and scripts
  (`experiments/request-cost/` on `exp/request-aware-cost`) checked in under
  `benchmarks/` with step 1, run with `--measure --model s3-like` and
  `--model unit`, plus the local-CPU and real-store scripts. Acceptance:
  - the chain from a selective City is at most 6 waves and 100 ms of
    storage at 200,000 rows (from 45 and 810 ms), and at most half its
    rows-only time on the real-store proxy;
  - the hub, read with three columns, is at most 6 waves (from 203), with
    the switch or the plan-time choice;
  - no measured shape is slower than its rows-only plan by more than the
    real store's ±15% noise, on storage plus CPU and on the real-store
    proxy;
  - all of this holds at half and at twice the calibrated latency and
    weights, and every shape is unchanged under the local profile.
- **Replay**: `engine_v2_plan_replay.rs` gains a plan gathered under the
  object-store pin and replayed in a local session, with the same plan and
  trace, and a hub plan whose replay takes the same switch side;
  `engine_v2_scrubbed_replay.rs` checks that an ambient storage kind or
  `LANCE_IO_THREADS` does not reach a replay.
- **Calibration record**: kept beside the benchmark fixtures (below).

## Calibration

The prototype's record is `experiments/request-cost/CALIBRATION.md`.

- **Host.** Modal sandbox under gVisor, AMD CPU (the model name is hidden),
  32 vCPUs visible and `nproc` 2, 64 GiB, Debian 13 on an overlay
  filesystem, Rust 1.97.1 release profile, Lance 13.0.0.
- **Method.** A release example (`request_cost_calibrate`) on a 200,000-row
  graph with 50 fragments and indexes built; warm medians over frontiers of
  1,000 to 24,000 edges; each weight is a least-squares slope. A scanned row
  is an aggregate over Person minus the same over Company, per row; an
  `id_lookup` row is a warm-CSR query's slope per destination row; a CSR
  build edge is a fresh handle's first CSR hop minus its first indexed hop,
  per edge; an indexed-probe row is an indexed hop minus the same hop on a
  warm CSR, per edge.
- **Result, five runs.** Scanned row 0.47–0.51 µs; `id_lookup` row
  16.2–17.0 µs (count) and 21.5–22.2 µs (three columns); CSR build edge
  0.36–0.42 µs; indexed-probe row 0.46–1.63 µs.
- **Caveats.** One host with two effective cores. The slopes include local
  file I/O through gVisor, which a server reading object storage does not
  pay; there that time is network time, which the wave and byte terms
  already price. The request latency comes from the in-region Railway
  measurement, not from this host.

The record names the exact commands and fixture, so a later maintainer can
reproduce the weights. A recalibration moves every object-store decision at
once, which is acceptable because plans record their profile; the record is
a maintained artifact.

## Rollout

1. **Plumbing and instrument.** The profile is gathered, recorded,
   explained and pinnable; fragment counts become statistics; the plan
   version increases; the cost instrument is checked in. The object-store
   profile still prices like the local one, so no plan changes. This can
   ship alone.
2. **`csr_cached` and CSR scope.** Both arms of the `csr_cached` correction
   and the CSR's price over `plan_edge_types`, with their local run-time
   side updates.
3. **Calibration.** The object-store profile's weights, latency and
   `bytes_per_unit` from the calibration record.
4. **Expand mode** uses the object-store profile, at plan time and in the
   run-time re-decisions. This is the first change object-store plans see.
5. **Access path** uses the object-store profile, with the run-time switch
   and the outer estimate for correlated blocks. The plan-time choice does
   not ship without the switch: without it, a hub keeps its 203 waves.

`implementation` advances to `in-progress` with step 1 and to `complete`
with step 5. Root selection, cache-aware CSR pricing, the local profile's
recalibration and the other decisions are separate work that reads the same
profile.

## Unresolved questions

1. **Where the object-store constants come from.** The options are fixed
   defaults per storage kind, a process environment knob read once in
   `gather` and recorded (as the expand ceilings are), or a per-graph value
   in `cluster.yaml`. The proposal is fixed defaults plus a recorded
   environment override.
2. **One object-store profile or several.** S3 Standard, Azure and
   low-latency stores such as S3 Express differ. The proposal is one
   profile until a measurement shows a decision that differs.
3. **The reference host against the serving host.** The weights are ratios
   of one host's CPU, while the latency is absolute, so a faster serving
   host makes a wave relatively more expensive. The proposal is to
   calibrate once on a serving-class host before step 3 ships and record
   it, and to rely on the robustness check (half and twice the constants)
   rather than per-host values.
4. **The `id_lookup` weight per projected column.** Two measured points
   (count and three columns) fit `a + b × columns`. The proposal is to
   measure more column counts in step 3.
5. **Warm pricing for one-shot processes.** An embedded CLI invocation is
   cold, so warm pricing understates its index and footer requests. The
   proposal is to accept that, since every measured decision ranked the
   same way cold.
6. **The persisted CSR artifact.** After `optimize`,
   `__graph_index/csr-current.bin` turns a CSR build into one GET. Knowing
   whether it matches the pins at plan time costs a request. The proposal
   is to price the build and leave the artifact as a run-time discount.

## Decision log

- 2026-10-13: drafted from the anchor-order measurements (GQT `--measure`,
  DST, `s3-like` and `unit` models; local CLI), the in-region request
  timings on the hosted personal graph, and a survey of the cost-driven
  decisions and their recording.
- 2026-10-14: review on PR #996. Added a priced-bytes column to the
  request model and stated that dependent reads price zero bytes; made
  the `AntiJoin` `csr_cached` correction a co-requisite of step 2;
  clarified that an indexed hop scans once per input batch (the 256-row
  `EXPAND_OUTPUT_ROWS` bounds only output chunks); required the
  calibration record to name its host and command; added the
  average-degree limitation and the access path's run-time switch as an
  open question.
- 2026-11-15: revised after the prototype on `exp/request-aware-cost`
  measured the design (storage, local CPU and a real store with injected
  latency; a release-build calibration). The second review's B1 held: with
  today's row constants the motivating flips needed a latency far above the
  calibrated one, and where they happened they saved storage time but cost
  more CPU. Changes: the objective is storage plus CPU time, and acceptance
  is judged on it or on a real store; the profile carries measured row
  weights, with the scanned row as the unit; the `HashJoin` extra wave is
  dropped (review O1); the CSR is priced over every edge type the plan
  traverses (O2); bytes come from manifest file sizes with no new plan-time
  read (O3); `N` is read from the store and recorded (O4); references are
  at current `main` and Lance 13 (O5); acceptance includes shapes that must
  not flip and holds at half and twice the constants (O6); both
  `csr_cached` arms are corrected; the access path's run-time switch and
  the outer estimate for correlated blocks are part of the design and gate
  its rollout; an `id_lookup` slice's requests are counted by fragment, not
  by fragment and column, which the prototype overcounted; the plan version bump is kept because bound plans ignore
  unknown fields; the root is stated as out of reach of this model, with
  what a follow-up needs.
