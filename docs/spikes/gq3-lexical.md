# Spike: GQ 3 lexical search evidence on Lance 13.0.0

**Audience:** the author of the GQ 3 RFC (`docs/rfcs/2026-11-07-gq-3.md` on
`ragnorc/omnigraph` `rfc/gq-3`)
**Scope:** section 4 (Search: `bm25_v1`, "Exact execution and qualified
acceleration") and section 10 (the lexical oracles and native BM25 guards),
against the Lance the tree pins today, 13.0.0
**Branch:** `spike/gq3-lexical` on `ragnorc/omnigraph`, on top of `main`
`e7e8e03b`

Every number below comes from a test on this branch; the test names are in
each finding, and [How to run](#b-what-was-built-and-how-to-run-it) gives the
commands. Debug-profile builds unless a finding says otherwise.

## (a) Findings

### 1. The four Lance guards hold on 13.0.0, unchanged (section 10; `blocked_on` item 4)

The four guards at `1e0bed40` compiled against Lance 13.0.0 with no API
change and pass. What each pins, with the 13.0.0 numbers:

| Guard | Lance 13.0.0 |
|---|---|
| `native_fuzzy_bm25_rewards_a_rare_expansion` | holds: exact `beta` scores 0.10977068; fuzzy `beto` 4.2096553 against `beta` 0.10977068, **38.35 times**; `beta beta` doubles both exactly (0.21954136, 8.419311) |
| `native_bm25_idf_can_round_a_common_term_to_zero` | holds: at N = 2^24 the native `f32` IDF is 0, `log1p` in `f64` is 2.98e-8 |
| `fts_statistics_scope_can_reverse_ranking` | holds: the field corpus ranks `d-00..02` (1.1451323) over `d-03` (0.38299227); an index over the eligible subset reverses it (`d-03` 1.2039728 over 0.35667497); an external row-address prefilter keeps the field corpus's scores bit-identical; per-table top-1 loses every alpha winner; a shared `MemBM25Scorer` through `InvertedIndex::bm25_search` recovers one; after deleting one of six rows the index still reports `(6, 6, [6])` |
| `nfc_preprocessing_requires_an_explicit_bounded_integration` | holds: composed and decomposed `résumé` tokenize differently and agree after NFC; a `normalization` field in the training JSON is silently ignored; NFC turns 2 input bytes into 4; 8194 input scalars are consumed before the first output scalar |

The RFC's section 10 wording ("about 38 times", "doubles a repeated query
term", "rounds a common term to zero at N = 2^24") is right on 13.0.0.
They also pass in the release profile, which `docs/dev/testing.md` asks of
a substrate qualification. Proposed change: mark the guard half of
`blocked_on` item 4 satisfied, naming
this branch's commit `f76b5354` until it lands on `main`.

### 2. The Decimal oracle and its Rust test hold, and the fixtures regenerate byte-identical (section 10)

`lexical_scoring_v1_reference_oracle` passes all 13 fixture cases. Running
`lexical_scoring_v1.py` (Python 3.12.16, 80-digit `Decimal`) rewrites
`lexical_scoring_v1.json` byte-identical to the `ce5a3012` file. The oracle
does not touch Lance, so this re-qualification says nothing about the
substrate; findings 3 to 13 do. The RFC's list of what the fixtures cover
("the formula, zero edits, alternatives, `all` and `any`, null and
token-empty values, two-edit membership") is accurate. It omits two cases
the file also holds, a one-scalar edit over `é` and the empty corpus.
Proposed change: none needed. Optionally name those two cases.

### 3. Native BM25 is `bm25_v1` in `f32` at zero edits, and something else with edits (section 4, `bm25_v1`; section 10)

New guard `gq3_bm25_v1_oracle_fixtures_against_native_lance_scores` runs
every oracle fixture through native Lance full-text search over the same
terms (`simple` tokenizer, lowercase, no stemming, stop words or folding;
`MatchQuery` with `fuzziness: Some(edits)`, `Operator::And` for `all`).

- **Zero edits:** both zero-edit fixtures give the same membership and order
  as the oracle, and scores agree within 8.3e-8 relative error, which is `f32`
  rounding. Lance's `idf` (`ln((N - n + 0.5) / (n + 0.5) + 1)`) and doc
  weight (`(K1 + 1) tf / (tf + K1 (1 - B + B len / avg))`, `K1` 1.2, `B`
  0.75) are the RFC's formula in `f32`.
- **Membership agrees in all 13 fixtures**, the edit-tolerant ones included.
  Lance 13's fuzzy matcher is a Unicode-scalar Levenshtein automaton
  (`UnicodeLevenshtein`, `index/partition.rs`), so a transposition costs two
  and `é` costs one, as `bm25_v1` defines. Null and token-empty values stay
  outside the corpus, as the RFC says. In
  `null_and_token_empty_are_outside_corpus` the native `beto` score matches
  the closed form with N = 3 and avg_len = 4/3 within 1e-6.
- **Ranking does not agree:** 7 of the 10 edit-tolerant fixtures order
  differently, with relative score errors up to 24.5 times. There are three
  mechanisms, each pinned. Each expansion gets its own IDF (`rare_variant`:
  native `beto` 1.386 over `beta` 0.539; the oracle has 0.144 under 0.288).
  Alternatives add (`alternatives_do_not_add`: native `beta beto` 1.295,
  oracle 0.212). There is no edit penalty (`two_edits_and_transposition`:
  zero, one and two edits away all score 1.5404451 natively; the oracle gives
  0.241, 0.121 and 0.060).

The RFC's "Native Lance BM25 does not supply this contract" is right. It is
also broader than the evidence: at zero edits native BM25 differs from the
contract only by `f32` arithmetic and by where its statistics come from
(findings 5 and 9). Proposed change: say so in section 4. The difference
decides what stage L can accelerate: zero-edit `bm25` is the only shape
whose native scores are close enough to certify (finding 5). Edit-tolerant
`bm25` has no native route at all until Lance scores expansions as a group.

### 4. The WAND fetch returns bit-identical scores and the exact top-k score set (section 4, "Exact execution and qualified acceleration")

New guard `gq3_bm25_fetch_through_block_max_wand` builds an index with the
engine's parameters (`InvertedIndexParams::default()`) over 4040 generated
documents in five fragments: 4000 Zipf-like documents of 4 to 60 words, plus
40 identical documents appended with their ids in reverse row order. It then
runs the query `engine/scan.rs` builds for a ranking,
`FullTextSearchQuery::new(text).with_column("body")`, with and without
`.limit(Some(k))`, through `Dataset::scan`. Six queries (one, two and three
terms, from 40 to 3424 matches, plus the tie block) times k ∈ {1, 10, 100}
make 18 measurements. It ran six times.

- **Score bits:** every returned `_score` equals the unlimited scan's score
  for that row, bit for bit, in 18 of 18 measurements and in all six runs.
- **Score set:** the fetched top-k score multiset equals the exact top-k's
  in 18 of 18 measurements. A fetch was never short of `min(k, matched)`.
- **Partial coverage:** after a 500-row fragment was appended past the
  index, 9 of 9 measurements were bit-identical with equal score sets, with
  up to 14 tail rows in the exact top 100. Lance plans that case as indexed
  top-k ∪ flat top-k under a `SortExec` with a fetch.
- `Scanner::limit(Some(k))` gives the same result as
  `FullTextSearchQuery::limit`. Lance's `Scanner::fts` copies the scanner's
  limit and offset into the full-text parameters when the query carries none.

Limited and unlimited runs go through the same executor
(`bm25_search_final_documents`). The unlimited run is the case
`limit = usize::MAX`, whose threshold never rises. That is why the bits
agree. So the RFC's claim is true as stated, but it compares Lance to Lance.
Finding 5 is why that is not enough.

### 5. Bit-identity to Lance's unlimited scan does not make the fetch exact for `bm25_v1` (section 4)

`bm25_v1` is `F64` with `libm`'s `log1p`. Section 4 makes the engine's own
scan the oracle, and Lance scores in `f32`. Two documents whose `f64` scores
differ by less than about one `f32` ulp come back from Lance tied, or in the
opposite order. New guard `gq3_native_f32_bm25_can_tie_or_invert_the_bm25_v1_order`
shows both on a real index. Each corpus has N = 4040 and 130000 tokens; the
query is `qa qb`. The pairs were found by replaying Lance 13's `f32`
operation order from `scorer.rs`, which reproduces Lance's bits exactly.

| Case | df(qa), df(qb) | A (len; tf qa, tf qb) | B | `bm25_v1` A − B | Lance A, B | k = 1 fetch |
|---|---|---|---|---|---|---|
| tie | 354, 1778 | 35; 4, 4 | 26; 3, 4 | +4.66e-8 | `0x40ad9893`, `0x40ad9893` | A, by arrival |
| inversion | 509, 916 | 29; 1, 4 | 60; 3, 3 | +2.20e-7 | `0x4096cd14` < `0x4096cd16` | **B**, no tie |

In the inversion case the fetch is bit-identical to Lance's unlimited scan,
there is no tie for any boundary check to see, and `bm25_v1`'s `Cut` keeps a
different document. On the generated corpus of finding 4 no such pair
occurred (largest relative error 1.68e-7, zero ties or inversions across six
queries), so the hazard is rare, not absent. A shape-based qualification
cannot rule it out.

Proposed change, preferred: treat the fetch as a certified candidate
generator. Fetch k + m, rescore those candidates with `bm25_v1` in `F64`,
and accept only when the k-th `bm25_v1` score exceeds the last fetched native
score times (1 + ε). ε is a stated bound on Lance's `f32` error for the
query's term count: on these corpora the largest observed relative error was
1.68e-7, so a few ulps per term. Fall back to the exact scan otherwise. A tie
fails the strict margin, so this also replaces finding 6's rule.
Alternative: define `bm25_v1`'s numeric profile as Lance's `f32` operation
order. That puts the substrate's arithmetic in charge of meaning, against
invariant 7 and the RFC's own rule that index state never changes an exact
answer, so I would not.

### 6. "Falls back when the boundary ties" needs a mechanism; k + 1 works (section 4)

A k-row result cannot show whether the k-th score ties. Lance's heap
(`push_scored_key`, `index/search_candidates.rs`) replaces an entry only on
a strictly greater score, so a tie keeps the rows that arrived first. Of the
40 identical documents, `qtiex` with k = 10 returned `t-030` to `t-039`.
Those are the first ten rows written, which carry the largest ids. The exact
`Cut` breaks ties by node identity, so it wants `t-000` to `t-009`. Five of
the 18 measurements tied at the k-th score, and in four of them the fetched
winners differed. In the fifth, `q7x` k = 10, arrival order happened to
match id order.

Measured: a fetch of k + 1 detected every boundary tie, 18 of 18 against the
exact answer. It says "tie" exactly when the k-th and (k+1)-th fetched
scores are equal. Proposed change: spell the rule as "fetch k + 1; fall back
when the k-th and (k+1)-th scores are equal", or absorb it into finding 5's
margin.

### 7. The "result comes back short" fallback guards nothing the preconditions admit (section 4)

The rule's preconditions are: nothing above the scan drops rows, every
filter is a prefilter, and coverage is full. Under them, a fetch shorter than
k means fewer than k rows match, which is already the exact answer. That held
in all 27 measurements, partial coverage included. Proposed change: delete
the clause, or name the case it guards. The only short case observed in
Omnigraph is a postfilter after scoring (`Eligibility` after scoring,
`engine/scan.rs`), and the preconditions already exclude it.

### 8. "The limit prunes scoring work" holds only at small k, and the amount varies between runs (section 4; A1)

Lance's `index_comparisons`, read through `Scanner::scan_stats_callback`,
gives these counts, unlimited → limited, as ranges over six runs:

| Query (matches) | k = 1 | k = 10 | k = 100 |
|---|---|---|---|
| `q0x` (3424) | 3424 → 512–768 | 3424 → 1792–2048 | 3424 → **3424** |
| `q40x` (287) | 287 → 128–256 | 287 → 287 | 287 → 287 |
| `q7x` (867) | 867 → 256–384 | 867 → 768–867 | 867 → 867 |
| `q2x q300x` (1614) | 1614 → 217–218 | 1614 → 217–218 | 1614 → 1588–1590 |
| `q1x q60x q900x` (2107) | 2107 → 173–176 | 2107 → 286–414 | 2107 → 878–993 |
| `qtiex` (40) | 40 → 40 | 40 → 40 | 40 → 40 |

Partitions race on a shared threshold, so the same query on the same
snapshot does different work from run to run. Median wall time of 15
alternating runs, debug build: `q0x` 5.4 ms → 1.0–1.3 ms at k = 1, and
5.4 ms → 1.7 ms at k = 100, where no comparison was saved. Release build,
same measurement:

| Query | k = 1 | k = 10 | k = 100 |
|---|---|---|---|
| `q0x` | 3.39 → 0.96 ms | 3.25 → 1.12 ms | 3.22 → 1.27 ms |
| `q1x q60x q900x` | 2.48 → 0.85 ms | 2.51 → 1.07 ms | 2.65 → 1.33 ms |
| `qtiex` (40 matches) | 0.93 → 0.83 ms | 0.90 → 0.89 ms | 0.84 → 0.87 ms |

At k = 100 `q0x` saves no comparison and is still 2.5 times faster, so most
of the wall-time gain is materializing and sorting fewer rows, not pruning.
The engine's unlimited path materializes every match before its own
`Order`.

Proposed change: write "may prune scoring work, by an amount that depends on
k against the matched set and that is not reproducible run to run". State the
fetched path's cost as an upper bound, the unlimited scan, which is what A1
can promise. Keep `index_comparisons` out of anything replay compares; A3's
trace compares `actual_rows` only, so today's report is safe.

### 9. Native statistics are not the field corpus `bm25_v1` names (section 4, "the statistics population")

`bm25_v1`'s corpus is the snapshot-visible, policy-visible entities owning
the property. Lance scores from the index's own statistics, which differ in
three ways:

- **Deletions:** stale until a rebuild. Guard 3 deletes one of six rows and
  reads `(6, 6, [6])` back.
- **Policy:** rows a policy hides are still counted.
- **Physical extent:** statistics cover one table's index. Guard 3's
  per-table top-1 loses the global winners.

The scanner path has no way to inject statistics. The only injection point is
`InvertedIndex::bm25_search(…, base_scorer: Option<&MemBM25Scorer>)`, below
the scanner, and it carries Lance's `f32` scorer. Proposed change: list the
conditions under which a native score can be certified at all, and add them
to the WAND rule's preconditions:

- no fragment of the table carries a deletion file (Lance's own
  `allow_indexed_stats_residual` uses this test);
- no row-level read policy narrows the property's entities;
- the property's entities live in one table.

Otherwise the fetch is disqualified.

### 10. Native fuzzy expansion breaks the RFC's monotonicity law, not just completeness (section 4, "The lexical query"; section 10)

New guard `gq3_native_fuzzy_expansion_truncates_membership_silently`. The
corpus holds 320 one-edit variants of `beta` (130 of which sort before it)
and one document `beta`. A zero-edit query matches 1 document. A one-edit
query with the default `max_expansions` of 50 matches exactly 50, and **the
document holding the exact term is not among them**. Lance 13 keeps the 50
lexicographically smallest expansions of each query term across the index's
partitions (`collect_fuzzy_candidates_with_automaton`), and the exact term
is not protected. With `MatchQuery::with_max_expansions(10_000)` the result
is complete, 321 documents. Under the engine's default analysis the same
corpus matches 50 at one edit.

So `matches(edits = 0) ⊆ matches(edits = 1)` fails natively. That is a
stronger fact than the RFC's "the pinned fuzzy expansion is not complete
membership", and it bears on edition 2 today: `build_fts_query` in
`engine/scan.rs` builds `FullTextSearchQuery::new_fuzzy(q, Some(edits))` with
the 50-term default. Proposed changes:

- cite this guard in section 4 and section 10 for the law the native path
  breaks;
- note for stage L that complete expansion is reachable through the public
  `with_max_expansions`, bounded by the automaton's 10,000-state limit
  (`UNICODE_LEVENSHTEIN_STATE_LIMIT`), whose failure still needs a typed
  outcome;
- consider an edition-2 issue for the dropped exact term (see
  [What was not done](#c-what-was-not-done)).

### 11. No native index can carry a declared profile, and the posting block size changes scores (section 4, Schema)

All three declared profiles normalize with NFC before tokenization. Guard 4
shows the native builder has no NFC step, and a training-JSON field claiming
one is silently dropped. A native artifact for a declared profile therefore
needs one of two things, and the RFC names neither: the engine indexes a
derived NFC column, or stored text is NFC-normalized at write, which changes
stored values. Separately, Lance 13 scores with quantized document lengths
(Lucene SmallFloat, up to 6.25% error, `quantize_doc_length`) whenever an
index uses the experimental 256-document posting blocks (`load_with_remapping`,
`index/partition.rs`). The default, 128, scores exact lengths; every
measurement here used it. Proposed changes: say which NFC route stage L
takes, and add the posting block size and format version to the RFC 0043
fingerprint that qualifies a native path. A 256-block index must never
qualify.

### 12. The WAND rule reads Lance surfaces the RFC says it does not (section 10, "Surveyed and not changed: Lance surfaces (none read)")

Today no ranking scan carries a fetch. `scan.rs` builds the ranking query
without `limit`, and `ScanTuning::limit` is never set on a `bm25` scan. The
rule would add three dependencies:

- Lance's limited FTS path;
- the scanner-limit pushdown, so any future `ScanTuning::limit` on a `bm25`
  scan silently enables WAND;
- `index_comparisons`, if qualification uses it.

Proposed change: drop "none read" for stage L, and add a `docs/dev/lance.md`
compatibility-fence row owned by `gq3_bm25_fetch_through_block_max_wand` and
`gq3_native_f32_bm25_can_tie_or_invert_the_bm25_v1_order`.

### 13. Lance 13 APIs the exact-scan and acceleration designs assume: what is public and what is not (sections 4 and 10)

| Need | Lance 13.0.0 |
|---|---|
| The analyzer without an index (the exact scan) | public: `InvertedIndexParams::build()` → `token_stream_for_doc` (guard 4 uses it); NFC must be applied before it |
| Expansion budget | public: `MatchQuery::with_max_expansions`; no completeness report |
| A fetch on the ranking scan | public: `FullTextSearchQuery::limit`, `Scanner::limit` |
| Work counter for qualification | public: `scan_stats_callback` → `ExecutionSummaryCounts::index_comparisons` |
| Eligibility without restating statistics | public: `Scanner::with_row_addr_prefilter(RowAddrMask)` (guard 3) |
| Injecting corpus statistics on the scanner path | **absent**; only `InvertedIndex::bm25_search(base_scorer)` below the scanner |
| A custom (`f64`, `bm25_v1`) scorer inside WAND | **absent**: `bm25_search` takes `&MemBM25Scorer`, not `dyn Scorer` |
| Fuzzy search with injected statistics | refused unless `bm25_search_prepared` / `PreparedBm25Query`, both `#[doc(hidden)]` |
| A tie-break by key inside the top-k heap | **absent**: ties keep arrival order |
| NFC in the index builder | **absent** (guard 4) |

The exact scan as section 4 designs it needs only the public rows. The
acceleration needs only public rows if it takes finding 5's
rescore-and-margin form. Statistics parity through the scanner (finding 9)
and a `bm25_v1` scorer inside WAND would each need an upstream change.

## (b) What was built and how to run it

Two commits on `spike/gq3-lexical`, tests only; no production code, no
edition-2 behaviour, and nothing under `crates/omnigraph-reference-engine/`
changed.

- `f76b5354` ports the evidence onto `main` unchanged:
  `crates/omnigraph/tests/fixtures/lexical_scoring_v1.{py,json}`,
  `lexical_scoring_v1_reference_oracle` at the end of
  `crates/omnigraph/tests/search.rs`, and the four guards at the end of
  `crates/omnigraph/tests/lance_surface_guards.rs`. `libm` (`=0.2.16`, the
  pin `omnigraph-gqt-core` already uses) and `unicode-normalization` (the
  workspace pin) become engine dev-dependencies.
- `e3b17001` adds four guards to `lance_surface_guards.rs`, all prefixed
  `gq3_`, with the shared helpers `gq3_plain_fts_params`, `gq3_fts_dataset`
  and `gq3_fts_rows`. They print their measurements with `--nocapture` and
  assert the facts above. Wall time is printed, never asserted.
  - `gq3_bm25_v1_oracle_fixtures_against_native_lance_scores`
  - `gq3_bm25_fetch_through_block_max_wand`
  - `gq3_native_f32_bm25_can_tie_or_invert_the_bm25_v1_order`
  - `gq3_native_fuzzy_expansion_truncates_membership_silently`

Run (from the repository root):

```bash
cargo dev-test --test lance_surface_guards -- --nocapture \
  fts_statistics_scope_can_reverse_ranking nfc_preprocessing_requires_an_explicit_bounded_integration \
  native_fuzzy_bm25_rewards_a_rare_expansion native_bm25_idf_can_round_a_common_term_to_zero gq3_
cargo dev-test --test search -- --nocapture --exact lexical_scoring_v1_reference_oracle
python3 -I crates/omnigraph/tests/fixtures/lexical_scoring_v1.py   # rewrites the JSON; diff it
```

Both whole suites pass on the branch: `lance_surface_guards` 81 passed and
2 ignored (the existing ignored guards); `search` 37 passed. The engine
package on the canonical feature graph,
`cargo dev-nextest -E 'package(omnigraph-engine)'`, ran 1788 tests: 1779
passed and 9 failed, all from this box's environment. Eight overflowed the
default thread stack and pass with CI's `RUST_MIN_STACK=16777216`. The ninth,
`db::upgrade::tests::interrupted::unreadable_legacy_object_asks_for_a_rerun`,
marks a file `0o000` to make it unreadable, and this box runs as root, which
reads it anyway; the test is in-source and untouched here. `cargo dev-clippy`
and `cargo fmt --all --check` are clean. `scripts/check-docs.py` and `typos`
1.52.0 pass on the changed files.

## (c) What was not done

- **Timings at scale.** Wall times are from 4040 documents on a 2-core box,
  in debug and release (`cargo test --release --locked -p omnigraph-engine
  --test lance_surface_guards -- --nocapture gq3_`, where all eight lexical
  guards also pass). No measurement over a corpus large enough for pruning
  to dominate materialization.
- **An engine-level `.gqt` case for finding 10.** The guard is at the Lance
  boundary. A `.gqt` reproduction on `main`'s engine, a `fuzzy($d.title,
  "beta", 1)` that misses the document whose title is `beta`, is not
  written. It would be red, so it belongs with a fix, under an
  `issue_N_*.gqt` name. One unexplained observation: the installed CLI
  (0.14.1-edge, older than `main`) matched only 3 of the 321 documents for
  `fuzzy($d.title, "beta", 1)`, the same 3 that `search` matches. Lance 13
  alone matches 50 there. That binary predates `main` and possibly its Lance,
  so I did not chase it.
- **The rest of section 10's lexical list:** the #747, #748 and #749
  regression cases across index states, more than 50 expansion terms through
  the engine, a huge token, automaton failure at the 10,000-state limit and
  cancellation. These need the edition-3 exact scan, which does not exist
  yet.
- **A certified-margin prototype (finding 5):** fetch k + m, rescore in
  `F64`, accept on margin. Not built; the measurements say what ε must
  cover.
- **Fuzzy WAND.** Every WAND measurement is zero-edit, any-term. Finding 3
  makes edit-tolerant native scores ineligible anyway.
- **S3 or Azure.** Every measurement is on the local filesystem.
