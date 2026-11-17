# Omnigraph edition 2 against GQ 3 on public graph and retrieval benchmarks

Run on 2026-11-17. Research and measurement only. Nothing on `main` or on
`gq3/phase-0-front-end` was changed.

| | Edition 2 | GQ 3 |
|---|---|---|
| Source | `ModernRelay/omnigraph` `main` | fork `ragnorc/omnigraph` `gq3/phase-0-front-end` (PR #1316), head when the run started, fixed for the whole run |
| Commit | `54ced5d3f573a45bfbe717dd2099cd1fa3b06220` | `a514157ce9938459be079e25e9fbf376e7ad22a9` |
| Merge base | `4f7c2076` (the #1416 merge), so both carry `--base` and `--stats` | same |
| Binary | `cargo build --release --locked -p omnigraph-cli`, `RUSTFLAGS=` (as release builds set it), sha256 `dafaece4…8a591` | same build, sha256 `c2e9a374…0e46a` |
| `omnigraph version` | 0.15.1, internal schema 17 (serves 14 to 17), HTTP 0.14 | identical |

**Machine.** A Modal sandbox under gVisor (kernel `4.19.0-gvisor`): 2 CPUs by
cgroup quota (`lscpu` reports 32 and the model as `unknown`), 64 GiB RAM, an
overlay root filesystem with no XFS, APFS or S3 endpoint. Wall times are
evidence, not a gate.

## Method

The system under test is the `omnigraph` binary, one process per query, over
an **empty graph shallow clone of a frozen base**: `omnigraph query --target
<empty dir> --base <base> --stats <file>`. This is the measured step of PR
#1416's clone route. Every base was written once by the edition-2 binary
(`init`, `load --mode overwrite`, `index build`, `optimize`), and both binaries
read the same base. #1416 keys a base by storage format (internal schema
version, Lance writer version, analyzer generation), and both binaries write
format 17 with Lance 13.0.0. The GQ 3 binary reads an edition-2 base as the
RFC's Compatibility section says it should: an `@index` String field with no
`analyzer` is `english_v1`, answered by the exact scan. One schema text
therefore serves both editions, because `Vector(n)` and `@index` mean the same
in both.

After every step the driver asserts that the clone holds nothing but its
marker, so every read left the base untouched.

I could not use #1416's own runner (`omnigraph-bench run <scenario> --store
<dir> --at <ref>=<binary>`), although it is built for both commits. It refuses
this box before any run with `gqt_prepare_failed: scratch device 0:27 is not
XFS`. Its local route accepts only APFS clonefile or qualified XFS, and this
box has neither, nor an S3 endpoint. Its base builder also accepts only GQT
fixtures with generated or inline seeds, so it could not have loaded these
external datasets anyway. `harness/og.py` therefore reproduces only #1416's
measured step and records the same evidence:
- the wall time of the child process, from spawn to exit;
- `--stats`: `elapsed_ms`, Lance calls by class on the `__manifest` and table
  planes, and `peak_rss_bytes`.

**Cache state.** Every step is a fresh process (#1416's `fresh-per-step`
lifecycle), so no engine cache is warm. The OS page cache is warm: one
discarded warm-up query runs per query and edition, and I did not drop pages,
since gVisor does not allow it. There is no in-process warm number, because
the server is cluster-only and I did not stand up a cluster. Editions
alternate their order on each repetition.

**Store requests under DST** come from the repository's own GQT runner
(`omnigraph-gqt --measure`), built in dev profile from each commit. They were
measured on two sample cases with inline seeds (an SNB sample and a SciFact
sample). Each edition blessed its own expectations.

**Query texts.** I wrote every edition-2 query by hand. I then rewrote each
one into GQ 3 using only the RFC's respelling map: property braces become
leading `where` conjuncts, filter clauses become `where` conjuncts, separators
become commas, `not { }` becomes `not exists { }` with the T84 bindings,
`search` becomes `match_terms(…, terms(…, mode: any))`, and a ranking key
becomes `let $pop = …, from bm25|nearest(…) as $score` with `k` equal to
`limit` (N3). Each pair was linted by its own binary. GQ 3's lint reported the
rewrites as the same typed IR where it checks that (for example, the reversed
undirected triple). Two things the map does not say, found by the type
checker: GQ 3 orders by a `return` alias as a bare name (`order { postCount
desc }`, where `$postCount` is T6), and a declared read may not be named
`bm25` (T74).

## 1. Survey

Facts come from primary sources, which a sub-agent opened. "Opened" marks are
in the source list at the end. "E2" and "GQ 3" say whether that edition can
express the benchmark's queries, judged from each binary's skill and the RFC.
Where an edition cannot, the missing feature is named.

### Graph query benchmarks

| Benchmark | Measures | Scale | Query mix | Metrics | License | E2 | GQ 3 |
|---|---|---|---|---|---|---|---|
| LDBC SNB Interactive v1 | transactional reads and inserts on a social network | SF0.1: 327.6k nodes, 1.5M edges; SF1: 3.2M nodes, 17.3M edges | 14 complex reads, 7 short reads, 8 inserts | throughput; latency percentiles; on-time rule | spec CC BY 4.0, code Apache-2.0, data license not stated | **Partial.** IS1, IS3–IS5, IC2, IC4 (end date precomputed), IC6, IC8, IC9, IC11; IC1 without the distance column. Missing: path length (IC1, IC13, IC14), outer join or count as a value (IC3, IC5), arithmetic (IC7), date-part extraction (IC10), unbounded closure (IS2 and IS6 root post, IC12) | **Partial, more.** Same set, plus IC3 and IC5 through `let $n = count { }` and `optional { }`, and IS7's flag as a block value (not run). Still missing: paths (stage Path), unbounded closure (`Fix`, stage Rec, refused as T86), date-part functions |
| SNB Interactive v2 | v1 plus cascading deletes | SF1 to SF30000; SF0.1 for tests | 14 complex reads (IC14 now a cheapest path), 7 short reads, 8 inserts, 8 deletes | as v1 | as v1 | as v1; deletes as `delete T where` | as v1; deletes as `match … delete $x` |
| LDBC SNB BI | analytical reads plus insert and delete batches | SF1 and up (217 MB) | 20 reads | power@SF, throughput@SF | as v1 | **Few.** Most need date extraction, arithmetic, weighted paths or closure | **More** (arithmetic, grouped `from`, `optional`). Missing: date extraction, weighted shortest path (BI15, BI19, BI20), closure (BI9) |
| LDBC FinBench (Transaction) | financial graph with hub truncation | SF0.01: about 6.4k nodes, 62k edges; SF0.1 is 67 MB | 12 complex reads, 6 simple reads, 19 writes, 3 read-write transactions | throughput | spec CC BY 4.0 | **Few.** TCR4's 3-cycle, sums. Missing: paths with increasing timestamps and per-step truncation (TCR1, 2, 5, 8), shortest path (TCR3), unbounded closure (TCR11), ratio arithmetic (TCR7, 9) | **Some more** (ratios via arithmetic and `if`). Missing: paths with per-step truncation, shortest path, closure |
| LDBC Graphalytics | algorithm kernels | XS classes; wiki-Talk 2.39M vertices | BFS, PageRank, WCC, CDLP, LCC, SSSP | Tp, EVPS | spec CC BY 4.0 | **No:** no graph algorithms | **No:** graph algorithms are stage R; SSSP is stage Path |
| LSQB | join engine and optimizer on subgraph counts | SNB SF0.1 projected: 6.4 MB | 9 `count(*)` patterns | runtime | Apache-2.0 | **Most.** Chains, triangles, stars, NOT patterns; Q7's OPTIONAL is missing | **All** (`optional { }`) |
| gMark | generator for schema-driven RPQ workloads | user-defined | UCRPQs with set selectivity | n/a | MIT | bounded paths only; no Kleene star | same, until stage Rec |
| JOB (IMDB) | join ordering and cardinality estimation | IMDB 2013, 21 tables, 3.6 GB | 113 queries from 33 templates, select-project-join | q-error, plan cost, runtime | no repository license | **After modelling IMDB as a graph**: equi-joins as edges, `contains`, `or`, `min` | same |
| JOB-light, G-CARE | cardinality estimation | IMDB, 6 tables; LUBM, YAGO and others | 70 queries; labelled subgraphs | q-error | unknown | fixed patterns | fixed patterns |
| WDBench | Wikidata query-log shapes | about 1.26B triples (paper not opened) | BGP, multi-BGP, OPTIONAL, property paths, C2RPQ | runtime with a 60 s timeout | no repository license | BGPs only | BGPs and OPTIONAL; paths need stage Rec |

### Retrieval benchmarks

| Benchmark | Measures | Scale | Metrics | License | E2 | GQ 3 |
|---|---|---|---|---|---|---|
| BEIR | zero-shot lexical and dense retrieval | 18 sets: SciFact 5,183 docs and 300 test queries, NFCorpus 3,633 and 323, FiQA 57,638 and 648, TREC-COVID 171,332 and 50, and others | nDCG@10 | code Apache-2.0; data per set (SciFact CC BY-SA 4.0) | **Yes:** `bm25` on the native index; dense via `nearest` over supplied vectors | **Yes:** `bm25` by exact scan; same `nearest` |
| MS MARCO passage | passage ranking | 8.84M passages; 6,980 dev queries; 2.9 GB | MRR@10 | non-commercial research | yes | yes, but the exact scan is about 50 ms per MiB of text (below) |
| BRIGHT | reasoning-intensive retrieval | 1,384 queries; 7.9k to 414k docs | nDCG@10 | CC BY 4.0 | yes (BM25) | yes |
| MTEB retrieval | embedding quality | 15 BEIR-derived sets | nDCG@10 | Apache-2.0 | dense via vectors | dense via vectors |
| ann-benchmarks | ANN recall against throughput | fashion-mnist 60k×784 L2; sift 1M×128 L2; glove 1.18M×25 or ×100 angular; nytimes 290k×256; gist 1M×960 | recall@k against QPS | MIT | L2; angular after normalization | L2, and cosine or dot declared on the field |
| big-ann (NeurIPS '21, '23) | billion-scale, filtered, sparse and streaming ANN | 1B vectors; filtered YFCC 10M; sparse MS MARCO 8.8M | recall@10 at QPS | MIT, data CC BY 4.0 | filtered kNN as `nearest` with a prefilter; **no** sparse or range search | filtered kNN; range search via `distance` and `where`; **no** sparse |
| STaRK | text plus relational retrieval over a KB | Prime: 129k entities, 8.1M relations; Amazon 1.04M; MAG 1.87M | Hit@1, Hit@5, R@20, MRR | code MIT, data CC BY 4.0 | yes: `bm25`, `nearest` and `rrf` with traversal (embeddings supplied) | yes: the same, with population-first `rrf` |
| CRAG | RAG question answering over mock KGs | 4,409 QA pairs | an LLM judge | CC BY-NC 4.0 | not a query benchmark | not a query benchmark |
| GraphRAG-Bench (2506.05690) | GraphRAG pipelines | novels and medical | an LLM judge, with **no exact labels** | MIT | not a query benchmark | not a query benchmark |
| WebQSP, CWQ, MetaQA | KG question answering with entity answers | Freebase; MetaQA over WikiMovies | F1, hits@1 | various | fixed 1–3 hop patterns | same |

### Query writing by agents (not run: each calls a model)

| Benchmark | Size | Metric | Comparison with the RFC's M0 instrument |
|---|---|---|---|
| Neo4j Text2Cypher (2024) | 44,387 pairs; 4,833 test | BLEU, exact match, execution | Ground truth is Cypher. A GQ port needs every query translated and executed. M0 is 45 tasks on one schema, scored by hand for semantic correctness, with first-try validity and repair turns |
| CypherBench | 11 Wikidata graphs; 2,488 test | execution accuracy, PSJS | The best fit: property graphs with execution-based scoring. A GQ port covers its pattern classes, except unbounded paths, in GQ 3 |
| SynthCypher / Auto-Cypher, Mind the Query, Text2GraphQuery-Bench | 29.8k; 27.5k; 267k pairs | execution-grounded, GLEU | Cypher, GQL and SQL/PGQ targets; much larger than M0 and not GQ |
| Spider 2.0, BIRD | 632 workflows; 12,751 pairs | execution success; EX and VES | SQL, enterprise dialects. They measure the agent more than the language, unlike M0, which holds the agent fixed and varies the language |

M0 is the only instrument here whose independent variable is the language
itself: the same tasks, the same model, edition 2 against edition 3. The
public sets would add scale and outside ground truth, but only after a
translation step that is itself a source of error. CypherBench, translated to
GQ with execution-checked ground truth, is the natural next step beyond M0.

## 2. Selection

| Chosen | Scale run | Why | What the subset loses |
|---|---|---|---|
| **LDBC SNB Interactive v1, SF0.1** | full SF0.1 (CsvBasic-LongDateFormatter): 1,805,553 records, 286k messages, 1.5k persons. Loaded in 6.4 s; 142 MB base | Closest to what the GQT corpus and the skill exercise: key lookups, 1–3 hop traversal, correlated negation, grouped counts, ordered top-k. 14 read queries both editions express, with the official substitution parameters (10 per query) | IC3, IC5, IC7, IC10, IC12–IC14 (above); IC1's distance key; IS2's and IS6's root-post walk (IS6 is run on posts only); IS7's knows flag. Inserts were not run. Throughput under the official schedule was not run; this is single-query latency |
| **SNB graph-plus-text** (`h1`–`h3`) | the same base, `Message.content` with `@index` | The "scope first, rank second" pattern the skill teaches: `bm25` over the IC6 person's 2-hop circle (`h1`), global `bm25` over 286k messages (`h2`), `match_terms` count (`h3`). Query text: the IC6 tag name | no relevance labels: rows are compared between editions only |
| **BEIR SciFact, NFCorpus, FiQA-2018, TREC-COVID** | SciFact and NFCorpus: every test query; FiQA: 300 of 648 test queries (seeded sample); TREC-COVID: all 50 queries on the full 171k corpus, plus seeded 10k and 50k subsets for the size curve | Standard lexical sets with qrels and published BM25 baselines; four corpus sizes (3.6k to 171k), so the exact-scan risk can be measured | FiQA's metrics cover 300 of 648 queries. The TREC-COVID subsets have no valid qrels and are used for latency only |
| **ann-benchmarks fashion-mnist-784-euclidean** | full 60k train set; the first 200 test queries | high dimension, L2, official dataset | 9,800 test queries |
| **ann-benchmarks sift-128-euclidean** | first 200,000 of 1M; ground truth recomputed exactly (squared L2, ties to the smaller id); 200 queries | the canonical ANN set at a size this box loads in seconds | 80% of the corpus, so recall here is not comparable with published 1M numbers |

Not chosen:
- **FinBench**: its complex reads need time-ordered paths with per-step
  truncation, which neither edition has. The repository's registered FinBench
  is a merge benchmark over a physical bundle not present here. SF0.1 was
  downloaded and is unused.
- **SNB BI and Graphalytics**: neither edition expresses most of their
  queries.
- **JOB**: it needs IMDB modelled as a graph, and its point is the
  relational join order.
- **MS MARCO**: 8.8M passages would take each GQ 3 query minutes. The size
  curve below predicts it.
- **big-ann**: billion scale.
- **STaRK**: the smallest KB is 8.1M relations, and its hybrid arm needs the
  1536-dimension query and node embeddings STaRK ships (more than 1 GB for
  Prime). I did not fit that load and embedding download into this run.
- **CRAG and GraphRAG-Bench**: an LLM judge, no exact labels.
- **The agent benchmarks**: they call a model.

## 3. Results

### 3a. LDBC SNB SF0.1: 14 graph queries plus 3 graph-plus-text queries

10 official parameter bindings × 3 repetitions per query and edition. Engine
is `--stats.elapsed_ms`; wall adds the process spawn. "Rows" counts
parameters whose rows are byte-equal and in the same order, over parameters
that differ.

| query | E2 wall p50 / p95 ms | GQ 3 wall p50 / p95 ms | Δ p50 | E2 RSS MiB | GQ 3 RSS MiB | Lance calls E2 / GQ 3 | rows equal |
|---|---|---|---|---|---|---|---|
| IS1 profile | 135 / 138 | 138 / 140 | +2% | 301 | 338 | 53 / 53 | 10/10 |
| IS2 recent messages | 148 / 161 | 150 / 164 | +1% | 311 | 354 | 135 / 135 | 10/10 |
| IS3 friends | 142 / 145 | 142 / 146 | 0% | 312 | 345 | 67 / 67 | 10/10 |
| IS4 message | 126 / 129 | 128 / 131 | +2% | 304 | 332 | 28 / 28 | 10/10 |
| IS5 creator | 135 / 138 | 137 / 140 | +1% | 301 | 331 | 56 / 56 | 10/10 |
| IS6 forum (posts) | 145 / 149 | 147 / 150 | +1% | 303 | 332 | 86 / 86 | 10/10 |
| IS7 replies | 133 / 147 | 133 / 150 | 0% | 299 | 327 | 37 / 37 | 10/10 |
| IC1 (no distance) | 166 / 173 | 169 / 176 | +2% | 308 | 337 | 102 / 102 | 10/10 |
| IC2 | 267 / 290 | 269 / 288 | +1% | 340 | 380 | 1,080 / 1,080 | 10/10 |
| IC4 | 489 / 507 | 497 / 514 | +2% | 424 | 465 | 2,185 / 2,185 | 10/10 |
| IC6 | 2,298 / 2,652 | 2,333 / 2,654 | +2% | 501 | 530 | 17,292 / 17,292 | 10/10 |
| IC8 | 177 / 198 | 177 / 198 | 0% | 330 | 363 | 224 / 224 | 10/10 |
| IC9 | 2,385 / 2,679 | 2,405 / 2,710 | +1% | 429 | 456 | 23,916 / 23,916 | 10/10 |
| IC11 | 175 / 345 | 180 / 349 | +3% | 330 | 347 | 138 / 138 | 10/10 |
| agg1: messages per tag, top 20 (not LDBC) | 490 / 507 | 483 / 483 | −1% | 385 | 416 | 29 / 29 | 1/1 |
| **h1** `bm25` over the 2-hop circle | 470 / 731 | **2,416 / 2,555** | **+414%** | 426 | 463 | 324 / **16,667** | 0/10 bytes; ids: mean overlap@10 0.97, same order 5/10 |
| **h2** global `bm25`, 286k messages | 147 / 152 | **638 / 656** | **+334%** | 312 | 361 | 190 / 24 | 0/10 bytes; ids: overlap@10 0.98, same order 7/10 |
| **h3** `match_terms` count | 151 / 169 | **627 / 645** | **+315%** | 320 | 361 | 382 / 24 | 10/10 |

The graph queries give identical rows (141 of 141 parameter bindings), make
identical Lance calls, and run within about 2% at p50. The respelling map's
claim that rows do not change holds for every graph query run here. GQ 3's
peak RSS is 28–43 MiB higher on every query. The same gap appears on a 3-row
graph (about 285 against 318 MiB), so it is a fixed per-process cost, not
query work.

Byte inequality in `h1` and `h2` comes from the score values: they differ in
the third significant digit (for example 7.144 against 7.134), as `bm25_v1`'s
F64 over `english_v1` statistics replaces Lance's F32 over Lance's analysis.
Membership and order differ only where `english_v1` analyzes differently
(next section). Both are Compatibility bullets ("Results").

### 3b. BEIR lexical retrieval, the benchmark's own metrics

Each test query is ranked by `bm25` over title and text, top 100. nDCG@10
uses graded gains (pytrec_eval's `ndcg_cut_10`). BEIR's published BM25
(Elasticsearch) numbers are shown for reference.

| set (queries) | BEIR BM25 nDCG@10 | E2 nDCG@10 / R@100 / MRR@10 | GQ 3 nDCG@10 / R@100 / MRR@10 | top-10 identical in order | mean overlap@10 | GQ 3 better / worse per query |
|---|---|---|---|---|---|---|
| SciFact (300) | 0.665 | 0.684 / 0.921 / 0.646 | **0.687** / 0.924 / 0.649 | 176 | 0.94 | 9 / 10 |
| NFCorpus (323) | 0.325 | 0.327 / 0.247 / 0.531 | **0.328** / 0.249 / 0.533 | 261 | 0.94 | 6 / 7 |
| FiQA (300 of 648) | 0.236 (all 648) | 0.273 / 0.572 / 0.329 | 0.269 / 0.574 / 0.326 | 67 | 0.91 | 19 / 27 |
| TREC-COVID (50) | 0.656 | 0.661 / 0.125 / 0.930 | 0.645 / 0.124 / 0.881, **1 query refused** | 21 | 0.90 | 7 / 14 |
| TREC-COVID, the refused query re-run with `query_memory_bytes` = 300 MiB | | | 0.655 / 0.127 / 0.901 | 21 | 0.92 | 8 / 13 |

Quality is level within noise. GQ 3 is slightly up on SciFact and NFCorpus
and slightly down on FiQA and TREC-COVID. A sign test on the per-query wins
(19/27, 8/13) does not reject equality. The movement is the RFC's
Compatibility bullet "rows change on every full-text field where edition 2's
analysis differs from its profile". I traced one case: on TREC-COVID-10k, 6 of
50 `match_terms` counts differ. For "how does the coronavirus respond…",
edition 2 matches 151 more documents, every one through the word "does".
Lance's default stop list misses the stem `doe`. `english_v1`'s list holds
each stop word and its Snowball stem (RFC §4, "13 of NLTK's 198 English stop
words would be indexed in stemmed form"). GQ 3 never matched a document that
edition 2 did not.

### 3c. Lexical latency against corpus size

This is the known risk. GQ 3 answers `bm25` and `match_terms` by exact scan,
while edition 2 uses Lance's native inverted index. Engine p50 of `bm25` top
100, one process per query:

| corpus | docs | text (JSONL MiB) | E2 ms | GQ 3 ms | ratio | GQ 3 RSS p50 MiB (E2) |
|---|---|---|---|---|---|---|
| NFCorpus | 3,633 | 5.7 | 41 | 287 | 7× | 344 (303) |
| SciFact | 5,183 | 7.7 | 48 | 375 | 8× | 347 (310) |
| TREC-COVID 10k subset | 10,000 | 11.2 | 50 | 552 | 11× | 359 (309) |
| FiQA | 57,638 | 45.3 | 65 | 2,147 | 33× | 384 (331) |
| TREC-COVID 50k subset | 50,000 | 56.4 | 72 | 2,892 | 40× | 392 (342) |
| SNB messages (`h2`) | 286,744 | short messages | 122 | 613 | 5× | 361 (312) |
| TREC-COVID | 171,332 | 192.3 | 113 | 9,737 | 86× | 558 (407) |

GQ 3's cost is linear in the text it analyzes: 47–51 ms per MiB of JSONL on
every BEIR corpus, about 20 MiB/s on one query. Edition 2 grows from 41 to
113 ms across a 47× larger corpus. `match_terms` (`lexcount`) costs the same
as `bm25` in GQ 3: 549 against 552 ms at 10k, 2,905 against 2,892 ms at 50k.
In edition 2 it is 76 and 258 ms. At MS MARCO's 8.8M passages (about 3 GB of
text), the line predicts minutes per query.

### 3d. Vector retrieval (ann-benchmarks)

`nearest` k = 10. Recall@10 is ann-benchmarks' distance criterion (within the
10th true distance + 0.1%) against exact ground truth.

| set (queries) | query | E2 recall@10 / wall p50 ms / Lance calls | GQ 3 recall@10 / wall p50 ms / Lance calls | rows equal | RSS E2 / GQ 3 MiB |
|---|---|---|---|---|---|
| fashion-mnist 60k×784 (200) | `nearest`, default `ann_nprobes` 20 | 1.000 / 272 / 50 | 1.000 / 272 / 50 | 200/200 | 662 / 702 |
| fashion-mnist (100) | exhaustive: E2 `ann_nprobes=0`, GQ 3 `recall: 1` | 1.000 / 271 / 50 | 1.000 / 271 / 50 | 100/100 | 658 / 702 |
| fashion-mnist (100) | GQ 3 `distance` (exact scan), no E2 form | — | 1.000 / 273 / 29 | — | — / 618 |
| sift 200k×128 (200) | `nearest`, default | 1.000 / 178 / 44 | 1.000 / 178 / 44 | 200/200 | 497 / 540 |
| sift 200k (100) | exhaustive | 1.000 / 177 / 44 | 1.000 / 177 / 44 | 100/100 | 499 / 540 |
| sift 200k (100) | GQ 3 `distance` | — | 1.000 / 237 / 24 | — | — / 495 |

The editions match exactly: same rows and same latency, and returned
distances equal the exact squared L2. Every run reports `exhaustive: true` at
the default 20 probes, so the IVF index that `index build` makes at these
sizes searches every partition. This instrument therefore does not reach the
approximate regime, and recall is 1.0 by construction (compare issue #486, a
degenerate mono-partition vector index). The new exact `distance` is 0–33%
slower than the index path, with fewer calls.

### 3e. Store requests under DST (`omnigraph-gqt --measure`, seed 0)

Each edition's own runner, dev profile, in-memory store. Requests per step:

| case | step | E2 requests | GQ 3 requests |
|---|---|---|---|
| SNB sample (150 persons, up to 25 messages each) | setup (schema, seed, indexes) | 312 | 317 |
| | IS2 | 35 | 35 |
| | IC2 | 34 | 34 |
| | IC9 | 32 | 32 |
| | h1 `bm25` over the 2-hop circle | 42 | 17 |
| | h2 global `bm25` | 6 | 5 |
| | h3 `match_terms` count | 9 | 5 |
| SciFact sample (1,000 docs) | setup | 74 | 84 |
| | `bm25` top 100 | 62 | 7 |
| | `match_terms` count | 47 | 5 |

Graph steps are identical. Lexical steps make fewer requests in GQ 3, because
the exact scan reads the column in large reads and opens no index. DST's
model counts requests and virtual I/O time, not CPU, so it cannot see the
exact scan's cost, which is all CPU. At SF0.1, `h1`'s plan changes shape and
GQ 3 makes 51× more Lance calls (3f, R2), so the sample's counts do not
extrapolate.

## 4. Regressions, ranked by severity

| # | Regression | Evidence | Cause found | Work that would address it |
|---|---|---|---|---|
| R1 | **A query edition 2 answers is refused.** TREC-COVID query 48 (a 22-word question over 171k docs) fails in GQ 3 with `payload_too_large: query_memory_bytes actual 157286456, limit 157286400`; edition 2 answers in 140 ms | `results/beir-trec-covid-q48-raised.jsonl`: with the limit at 300 MiB it answers in 9.9 s at 546 MiB peak RSS | The exact scan's memory charge grows with corpus and query terms and hits the default 150 MiB pool. The RFC's law 11 makes exhaustion a typed failure, so the failure is bounded. It is still a loss of availability against edition 2 | **Stage L**: certified-margin native fetch for zero-edit `bm25`, which avoids the full-corpus scan. Before that, a spillable or streamed scoring path in the exact scan |
| R2 | **A lexical ranking scoped by a traversal is 5× slower and makes 51× more Lance calls** (`h1`: p50 470 → 2,416 ms; 324 → 16,667 calls) | the `h1` rows in 3a; `explain` of both editions | Edition 2's plan drives from the native full-text scan (a ranked `ScanExec`) and hash-joins the CSR traversal onto it. GQ 3's `Score` sits over the population, so the plan walks `p → knows{1,2} → f → hasCreator → m` first, in `indexed_scan` mode with id restrictions (the same 24k-call shape IC9 has in both editions), then scores by exact scan | **Planner audit**: cost the driving side, so a selective lexical predicate can drive and the traversal join onto it, as `match_terms` membership allows (law 1 keeps eligibility the same). Also issue #1294 (price index coverage for dependent scans). **Stage L** gives the native candidate set to drive with |
| R3 | **Global lexical latency grows linearly with corpus text**: 7× at 3.6k docs, 33–40× at 50k, 86× at 171k (9.7 s against 113 ms). `match_terms` the same (4× on SNB `h3`, 11× at 10k) | 3c | The RFC's B stage answers every full-text call by the exact scan (about 50 ms per MiB of text on this box) and reads no index | **Stage L** (native acceleration with the shared analyzer and token column), as the RFC defers it. Until it ships, the skill and docs should state the scan's cost per corpus size |
| R4 | **The exact lexical scan panics a debug build** (`attempt to subtract with overflow`) on any document where a letter directly precedes U+2122 `™` (`x™`, `deficiency™`; not `™` alone, `x®`, `x²`, `ﬁ` or `é`), under all three profiles. Found because SciFact doc 17415081 holds the mojibake `â€™`. The release binary does not panic, and a 3-document check gave plausible rows, so the wrapped value's effect in release is unknown | `gqt/repro_tm_panic.gqt` (one `Doc { text: "x™ vitamin" }`, a `match_terms` count) run by GQ 3's `omnigraph-gqt`. Edition 2's runner handles the same seed, through Lance's index builder | It is in GQ 3's scan path (`engine/lexical_scan.rs`, which tokenizes through `lance_tokenizer`'s `token_stream_for_doc`). I did not pin the line, because the GQT worker swallows the panic location. `standard_v1` panics too, so it is not the stemmer or ASCII folding | A fix in PR #1316 before the break: debug-build GQT and DST run this path, and stage L reuses the analyzer for its token column. Add a probe-corpus row with `™` after a letter to the analyzer fingerprint corpus, which the RFC says must cover every pipeline step |
| R5 | **Fixed +28 to +50 MiB peak RSS per process** on every query, about +10%. Also present on a 3-row graph; startup +3 ms (15 → 18 ms) | 3a and 3d RSS columns | Not isolated. It is fixed, not proportional to query work: the binary is 3.3 MB larger, and plausible sources are new static tables (stop lists, Unicode data, the registry). I did not profile it | Small. Worth a `--stats` row in the catalog so it is tracked |
| R6 | **Lexical rows change** (scores in the third digit; top-10 membership 0.90–0.98 overlap; TREC-COVID nDCG@10 −0.006 to −0.016; FiQA −0.004; SciFact and NFCorpus +0.001 to +0.003) | 3b | **Explained, not a defect:** the Compatibility bullet ("on every full-text field where edition 2's analysis differs… `bm25` scores are `bm25_v1`'s F64"). Traced to the stop list's stems in one case | none needed; the release note should say quality moved within ±0.02 nDCG@10 on four BEIR sets |

No regression was found in graph-query rows, graph-query latency, Lance calls,
vector rows, recall or vector latency. I could not find work items named "p20
to p22" in the RFC, the PR or the fork's branches, so the table maps each
regression to stage L, the planner audit, and the issues I found.

## 5. What to add to the repository's benchmark catalog

1. **Registered external fixtures for the clone route.** #1416's base builder
   takes only GQT fixtures with generated or inline seeds. A `fixture
   register --jsonl <file> --schema <pg>` recipe (load, index build,
   optimize), keyed by storage format like the others, would let these
   datasets run through `run --store … --at …`. The JSONL is regenerated
   from the public source and not committed.
2. **One scenario per edition per query.** `--at` runs one catalog text
   against every ref, but a language break needs two texts. Let a scenario
   carry per-edition workload files, `workload: {e2: …, gq3: …}`, selected by
   the binary's language, over one base. The record should then carry a row
   digest without scores, so rows-equal is a report column.
3. **A lexical size ladder.** `bm25` top-100 and `match_terms` count over
   generated corpora at 10k, 50k and 200k documents of about 1 KiB each, so
   stage L's speedup and any regression in the exact scan show as a curve
   on every commit. Today the catalog's only search scenario is
   `e2e-query-destination-search` on a tiny fixture.
4. **A graph-scoped ranking scenario** (`h1`'s shape: key lookup →
   `knows{1,2}` → creator → `bm25`) at a size where the driving side
   matters. It is the only case here where the plan changed between editions.
5. **A quality gate for search**: SciFact (5k docs, 300 queries, CC BY-SA
   4.0), downloaded at run time, with nDCG@10 and Recall@100 as recorded
   metrics, not timing. It runs in under 2 minutes on this box.
6. **A vector scenario large enough to be approximate.** At 60k and 200k
   rows the default index is exhaustive at 20 probes. Pin `num_partitions`,
   or use SIFT-1M, so `nearest` recall below 1.0 is measurable.
7. **Make the local route runnable on unqualified Linux**, as a
   claim-ineligible point the way served runs already are. It refuses
   anything but APFS or XFS, so CI containers and sandboxes like this one
   cannot run it at all.
8. **A DST `--measure` pair** of the two sample cases, as GQT cases with
   inline seeds. Their per-step requests belong in `cost_table.txt` once the
   edition settles.

## 6. Not run, and why

- **#1416's runner** (`omnigraph-bench run --store … --at …`): refused on
  this box (no XFS, APFS or S3). I built it for both commits; see Method.
- **The repository's `query-shapes` and `traversal` groups across the two
  commits**: they need the same runner.
- **STaRK**: load size and the embeddings it needs; see Selection.
- **FinBench, SNB BI, Graphalytics, IC3, IC5, IC7, IC10, IC12–IC14**: not
  expressible in both editions (features named in the survey). FinBench
  SF0.1 was downloaded and is unused.
- **SNB updates and throughput** under the official driver schedule, and
  every concurrent workload.
- **Warm in-process latency**: every number is a fresh process. The server
  is cluster-only, and no cluster was stood up.
- **Page-cache-cold runs**: gVisor does not allow dropping caches.
- **The approximate vector regime** (the index is exhaustive at these sizes),
  cosine fields, `rrf` fusion with relevance labels (no labelled set with
  vectors loaded), and edit-tolerant `match_terms` (`max_edits`).
- **FiQA's other 348 test queries and lexical counts on the full
  TREC-COVID**: time.
- **Every agent query-writing benchmark**: they call a model.

## Files

- `harness/og.py`: the measured step (product binary, empty clone over a
  frozen base, `--stats`).
- `harness/bench.py`: editions alternated per repetition.
- `harness/analyze.py`: latency, RSS, calls and parity table.
- `harness/make_base.sh`: base builder.
- `snb/`: schema, CSV-to-JSONL loader, parameter builder, 18 queries × 2
  editions.
- `beir/`: schema, loader, qrels-driven parameters, metrics, 2 queries × 2
  editions.
- `ann/`: HDF5-to-JSONL loader with exact ground truth, metrics, queries.
- `gqt/gen.py`: the DST sample-case generator. The cases embed dataset text
  and are not committed.
- `results/`: the summary tables, BEIR and ANN metric lines, build stats and
  the DST `--measure` outputs. Raw per-run JSONL holds dataset text and is
  not committed.

Reproduce: download the sets named above into `/src/data`, run each
`convert.py`, build bases with `make_base.sh <edition-2 omnigraph> …`, then
`bench.py` with both binaries and `analyze.py` and `*/metrics.py`.

## Survey sources

Opened unless marked.
- LDBC SNB specification v2.2.5 (ldbcouncil.org/ldbc_snb_docs).
- SNB datasets page.
- SNB Interactive v2 implementations README.
- SNB arXiv 2001.02299 (metadata only).
- Interactive v2 arXiv 2307.04820 (not opened).
- SNB BI PVLDB 16(4) p877 (not opened).
- FinBench specification v0.2.0-alpha and datasets page.
- FinBench arXiv 2306.15975 (metadata only).
- Graphalytics arXiv 2011.15028 and datasets page.
- LSQB repository; LSQB paper (not opened).
- gMark arXiv 1511.08386.
- JOB, PVLDB 9 p204.
- MSCN / JOB-light, arXiv 1809.00677.
- G-CARE repository; G-CARE paper (not opened).
- WDBench repository; WDBench paper (not opened).
- BEIR, arXiv 2104.08663 and repository.
- MS MARCO repository and site; arXiv 1611.09268 (metadata only).
- BRIGHT, arXiv 2407.12883.
- MTEB, arXiv 2210.07316; MMTEB 2502.13595 (abstract only).
- ann-benchmarks README; arXiv 1807.05614 (metadata only).
- big-ann NeurIPS '21, arXiv 2205.03763; NeurIPS '23 README; 2409.17424
  (abstract only).
- STaRK, arXiv 2404.13207.
- CRAG, arXiv 2406.04744 and repository.
- GraphRAG-Bench 2506.05690 (abstract and README); 2506.02404 (abstract only).
- WebQSP, ACL P16-2033.
- CWQ, arXiv 1803.06643.
- MetaQA, arXiv 1709.04071 and repository.
- Text2Cypher, arXiv 2412.10064.
- CypherBench, arXiv 2412.18702.
- SynthCypher, arXiv 2412.12612 (abstract only).
- Mind the Query, EMNLP Industry 2025.
- Text2GraphQuery-Bench, arXiv 2602.11745 (abstract only).
- Spider 2.0, arXiv 2411.07763 (abstract only) and repository.
- BIRD, arXiv 2305.03111 and site.
