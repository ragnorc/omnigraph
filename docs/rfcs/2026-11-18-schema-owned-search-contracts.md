---
rfc: "2026-11-18-schema-owned-search-contracts"
title: "Schema-owned search contracts"
track: maintainer
status: draft
implementation: not-started
authors:
  - Ragnor Comerford (@ragnorc)
created: 2026-11-18
updated: 2026-11-18
discussion: "https://github.com/ModernRelay/omnigraph/pull/1302"
supersedes: []
superseded_by: []
blocked_on:
  - "The lexical oracle fixtures and the four Lance guards hold on Lance 13.0.0 (spike `spike/gq3-lexical`, 2026-11-08); the certified-margin fetch of this RFC prototyped against the inversion guard before stage L ships it"
  - "The exact scan's cost and stage L's saving measured by a checked-in instrument (invariant 13); the only figures are the implementation guide's, about 9.7 s against about 113 ms over 171,000 documents"
  - "Unresolved question 3 settled before stage L: where the derived token column lives and how its coverage is tracked across updates, compaction and merges"
---

# RFC: Schema-owned search contracts

> A term set in ***bold italics*** is defined at that spot; it is plain text
> afterwards. Code references are at `main` `03e0b623`, Lance 13.0.0. A rule
> this RFC chooses, rather than one an invariant forces or today's behaviour
> supplies, is marked *[choice]*; a rule carried over unchanged, *[as today]*.
>
> This RFC was split out of the [GQ 3 RFC](2026-11-07-gq-3.md) on 2026-11-18.
> Its text is that RFC's, moved; the decision log lists the sentences the
> split wrote. It carries the schema-owned half of the analyzed lexical
> search RFC (PR #792) and of RFC 0048 (PR #793), both closed without merge
> on 2026-11-07. It defines no query syntax: the GQ 3 RFC owns every query
> form.

## Summary

Text analysis, lexical scoring, vector distance, embedding recipes and
scalar index kinds are declared in the schema and resolved before a schema
is accepted. The rule is one sentence: meaning comes from the accepted
schema and the plan, never from index presence, coverage or segment layout
(invariant 7). An exact evaluation path is the oracle at every coverage
state; a native index accelerates only where it proves the same answer.

- **Text.** `@index(fulltext, analyzer = …, scorer = …)` on a String
  property, with four immutable analyzer profiles and the `bm25_v1` scorer
  over the field corpus.
- **Lexical matching.** One matching rule at every edit budget, applied with
  the field's analyzer to document and query text, and answered by an exact
  scan whether the field is indexed, partially indexed or not indexed.
- **Scalar indexes.** `@index` states intent, and the engine builds the
  Lance scalar index kinds that serve the property's type.
- **Vectors.** `Vector(n, distance = "l2" | "cosine" | "dot")`; a returned
  distance is always the exact distance under the field's declared distance.
- **Recipes.** `@embed("source", model = "…")` binds a vector to the String
  it embeds, and a schema may declare one default recipe.
- **Acceleration.** A native full-text index serves a query only where its
  artifact proves parity with the exact scan (stage L).

This RFC adds schema syntax and changes what a search call returns. It adds
no query syntax and needs none: a query supplies a text, a matching mode and
an edit budget, or a vector, and reads the meaning fixed here
([What a query surface supplies](#what-a-query-surface-supplies)). The
[GQ 3 RFC](2026-11-07-gq-3.md#4-search) owns the calls, their windows,
fusion and the populations and laws of retrieval, and reads these contracts.

## Motivation

Text answers change with physical
index state: without an index `search` runs a bare case-sensitive scan
(#747), with one `fuzzy` spends its edit budget on letter case (#748), and
rows appended after an index build are analyzed differently from indexed
ones (#749), each a violation of invariant 7. `Vector(n)` declares no
distance and every index is built with L2. Two RFCs (PRs #792 and #793)
fixed these on the edition 2 surface and were reviewed, and the GQ 3 RFC
folded both into its search section on 2026-11-07. The part of that
section that is schema-owned meaning is this RFC.

It is its own RFC because it is its own decision. Every declaration here is
`.pg` text, and every contract here is read by a call whatever the call is
spelled, so nothing here needs an edition-3 form. The two also have
different ways back: the GQ 3 break is reverted by reverting a change,
while an index built under a profile is undone by a rebuild or by restoring
a backup ([Compatibility](#compatibility-and-reversibility)). Apart, each
can be accepted, shipped and reverted alone. The earlier separate drafts
failed for another reason: four documents specified one surface and drifted
(the GQ 3 RFC's Alternatives). This RFC specifies no surface.

## User and operational behavior

### Schema

**Text.** A String property becomes searchable by declaring its full-text
index with an analyzer:

```pg
node Doc {
  slug: String @key                                            // exact only
  title: String @index(fulltext, analyzer = "english_v1")      // matching and ranking
  body: String @index(fulltext, analyzer = "standard_v1", scorer = "bm25_v1")
}
```

`analyzer` names one of four immutable profiles; `scorer` is `bm25_v1`
(the default) or `none`, which allows matching and makes a ranking call on
the field a type error. Every setting of a profile is explicit; unspecified
Lance defaults are never part of the contract.

| Setting | `standard_v1` | `standard_folded_v1` | `english_v1` | `english_legacy_v1` |
|---|---|---|---|---|
| Tokenizer | `simple` | `simple` | `simple` | `simple` |
| Lowercase | yes | yes | yes | yes |
| Stemming and stop words | neither | neither | English stemmer, then the English stop list: each word and its stem | English stemmer, then the plain English stop list |
| ASCII folding | no | yes | yes, after stemming | yes, after stemming |
| Token-length filter | disabled | disabled | disabled | tokens of 40 bytes or more dropped |
| Unicode normalization | NFC before tokenization | same | same | none |

`english_v1` keeps Lance's filter order, the only order Lance's index builder
produces, so a native index can implement it. Because stemming runs first, a
stop list of plain words would miss the stop words the stemmer changes:
13 of NLTK's 198 English stop words would be indexed in stemmed form
(`onli`, `veri`, `ani`, `whi`, `dure`, …). The profile's list therefore
holds each of the 198 words and its Snowball English stem *[choice]*. Two
consequences of the order stay part of the profile: a word whose stem is a
stop word is dropped ("human beings" analyzes to `human`), and folding after
stemming keeps "résumés" (`résumé`) and "resumes" (`resum`) apart. A profile
that removes stop words and folds before stemming, as Lucene does, is a new
profile; Lance 13 cannot build it natively, so it waits for a configurable
filter order upstream. The list is checked in and passed to Lance
explicitly, never taken from its default, and the profile records the
list's digest and the stemmer's identity, so a dependency change cannot
alter a released profile.

A profile fixes the analyzed terms, not what an index stores. Whether a
native index records term positions is a build option of the index,
recorded in the artifact's RFC 0043 certificate and never in the schema
fingerprint *[choice]*. Indexes are built without positions. A later phrase
predicate defines how positions are assigned, including the gaps that
removed stop words leave; the exact scan answers it over any profile, and an
index rebuilt with positions through explicit reconciliation accelerates it,
with no schema change and no new profile.

A profile never changes once accepted; a different pipeline is a new
profile, and a new profile or scorer version needs an RFC. There are no
query-time analyzer overrides. An omitted `analyzer` resolves once, when the
field first gains its full-text index, as a default recipe does: to
`english_v1` from stage D on ([Rollout](#rollout)), and to
`english_legacy_v1` for a field whose index an accepted schema declared
before stage D *[choice]*. So every full-text field has exactly one profile
and the contract below applies to every field. `english_legacy_v1` is
today's index analysis, Lance's default (English stemming, the plain stop
list, ASCII folding, and a filter that drops tokens of 40 bytes or more,
with no NFC), stated as a profile with its stop list checked in, so no
accepted field changes its analysis at upgrade. It differs from
`english_v1` in the token filter, the stop list's stems and NFC, and a
field moves to another profile by declaring one. Canonical export spells
out every field's resolved profile. From stage D there is no
`FullTextIndexRequired`: a field with no index, a partial one or one built
for another analysis is answered by the exact scan. A profile is applied in
place: RFC 0043's certificate records the field's analyzer fingerprint, so a
certificate for the old analysis does not establish the new one, and the
field's index is rebuilt with the profile by explicit reconciliation.
Accepted SchemaIR owns the fingerprint (profile, normalizer and Unicode data
identity, tokenizer and filter order, stop list digest and stemmer identity,
scorer version) as a feature name under RFC 0040; a rename keeps it, drop
and re-add makes a new one.

The declared fingerprint names a profile; it does not prove that the code
running it still behaves as the profile did. RFC 0043's certificate
therefore also records a measured fingerprint: a digest of the terms the
native indexing pipeline, as the engine configures it for the profile,
emits over a checked-in probe corpus, taken when the index is built
*[choice]*. A native artifact qualifies only when its recorded digest
equals the one the running binary measures for the field's accepted
profile, and that digest equals the profile analyzer's own over the same
probes. The first check catches a dependency change under an existing
index; the second is parity between index and scan. A test pins each
released profile's two digests, so a dependency bump that changes what a
profile emits fails by name, and the fix is to keep the old behavior or
define a new profile, never to refresh the pin alone. Verification always
names the accepted fingerprint it checks; no path qualifies an artifact for
whatever analysis it carries. The probes catch only what they exercise, so
the corpus covers every pipeline step, NFC-sensitive input included. The
schema fingerprint stays declarative: it says what the profile is, and the
measured digest proves that a build implements it, so a binary upgrade
never changes an accepted schema's fingerprint.

**Scalar indexes.** `@index` on a property states intent, and the engine
builds the Lance scalar indexes that serve the property's type and the
registry's predicates over it *[choice]*: BTREE for equality, ranges and
order; BITMAP for an enum or `Bool`; LABEL_LIST for a list, serving `in`;
and NGRAM for a String, serving `contains`. A String with `@key` and no
`@index` gets a BTREE only: a key is matched by equality, `in` and ranges,
and `contains` on it is answered by the scan. A full-text index is built
only for `@index(fulltext, ...)`, never for plain `@index`. Each is derived
acceleration
(invariant 7): the planner uses one only where its answer equals the scan's,
rechecks the candidates where Lance's index returns a superset (as NGRAM
does), and falls back to the scan wherever coverage is missing or partial.
Each kind has its fence row and guard in `lance.md`. One accepted
declaration would change meaning: today a plain `@index` on a String builds
a full-text index and nothing else. The derivation is therefore a feature
name under RFC 0040 *[choice]*: a contract accepted without it keeps
reading a String's `@index` as its full-text index, so no built index goes
unused at upgrade; the first `schema plan` under the feature shows each
such property changing from a full-text index to BTREE and NGRAM, and
prints `@index(fulltext)` as the spelling that keeps it. No other accepted
declaration changes meaning; an existing graph gains the new kinds through
explicit index reconciliation.

**Vectors.** `Vector(n, distance = "l2" | "cosine" | "dot")`; an omitted
distance is `l2`, today's meaning, so no accepted schema changes meaning.
Every vector call reads the field's declared distance (`nearest(…)`, and
GQ 3's `distance(…)`).
The resolved distance is persisted; the index is built with it, and an index
built for another distance is never used, so a changed distance serves from
the exact scan until the index is rebuilt.

| Distance | Value, ascending |
|---|---|
| `l2` | sum of squared component differences, no square root |
| `cosine` | `1 - dot(x, q) / (norm(x) · norm(q))` |
| `dot` | `1 - dot(x, q)`; can be negative |

Cosine is undefined at a zero vector, so a `cosine` field refuses one: a
write that stores a zero vector there fails validation, a zero query vector
is refused before the read runs, and a zero vector stored by an earlier
writer drops its row from `distance` and `nearest` alike, as a null does, so
the scan and the index agree.


**Recipes.** `@embed("source", model = "…")` binds a vector to the String it
embeds. An embedding space is the immutable model revision plus the query
and record encoding choices (roles or prompts, pooling, normalization,
dimensions); a model label, a provider name or a matching dimension is not
proof of a shared space. A schema may declare one default recipe;
`@embed("source")` without a model inherits it when the field first gains a
binding, changing the default never rebinds an existing field, and canonical
export spells out every field's resolved recipe. A String query argument
uses the field's query encoder; a raw vector argument is the caller's
assertion that it is in the field's space, refused when the dimension is
wrong. Three identities stay separate, each a typed versioned descriptor
whose hash is a derived view: the encoding recipe (the space), the field
representation (owner, property, source, resolved analyzer or recipe,
distance) and the query encoding (the recipe and the exact input, belonging
to one execution). A field whose distance changes needs its index rebuilt;
one whose recipe changes needs its vectors regenerated; no export and
reload.

### What a query surface supplies

This RFC defines no query syntax. A query supplies the value in the first
column and reads the meaning in the second; the last two columns name the
calls that supply it.

| Value | Meaning | Edition 2 call | [GQ 3](2026-11-07-gq-3.md#4-search) call |
|---|---|---|---|
| A ***lexical query***: a text, a mode (`all` or `any`) and an edit budget in `0..=2` | [Lexical matching](#lexical-matching) | `search(f, q)` and `match_text(f, q)`: `any`, zero edits; `fuzzy(f, q, n)`: `any`, `n` edits, two when `n` is omitted | `terms(q, mode:, max_edits:)` |
| A field and a lexical query, as a predicate | the matched set | the three calls above | `match_terms(f, terms(…))` |
| A field and a lexical query, as a ranking | [`bm25_v1`](#lexical-scoring) | `bm25(f, q)`: `any`, zero edits | `bm25(rel, $x.f, q)` |
| A vector field and a query vector or text | the field's declared distance and recipe ([Schema](#schema)) | `nearest(f, q)` | `distance(rel, $x.f, $q)`, `nearest(rel, $x.f, $q, k:, recall:)` |

The edition 2 column is the GQ 3 RFC's respelling map
(`match_terms(f, terms(q, mode: any[, max_edits: n]))`) read in the other
direction. It applies only if stage D ships while edition 2 is still
accepted ([Rollout](#rollout)). The mode `all` and an exact vector distance
over a whole population have no edition 2 spelling, and this RFC adds none.

### Lexical matching

A lexical query describes matching independently of its consumer. Its text
is a non-null String; its mode is `all` (every analyzed query term has a
matching document term) or `any`; its edit budget is in `0..=2`. A predicate
and a ranking over the same lexical query agree on membership before any
cut.

| Aspect | Rule |
|---|---|
| Analysis | the field's analyzer on document and query text, at every edit budget including zero |
| Distance | insertions, deletions and substitutions over analyzed Unicode scalar values, one each; a transposition costs two; no prefix restriction, no length-based tolerance |
| Term identity | repeated query terms need no repeated occurrences; reordering cannot change membership; neither phrase matching nor distance over the whole value |
| Empty text | a query with no searchable terms is a type error; a null or token-empty document does not match |
| Completeness | every result satisfies the predicate exactly; an edit-tolerant predicate advertises no approximate recall |

```text
matches(edits = 0) ⊆ matches(edits = 1) ⊆ matches(edits = 2)
matches(indexed) = matches(unindexed) = matches(partially indexed)
```

## Design

### Lexical scoring

**`bm25_v1`** is one versioned scorer for exact and tolerant queries.
Membership is the lexical query's matched set, evaluated before any cut; a score is
never a second membership test. The statistics population is the
snapshot-visible field corpus: distinct policy-visible entities owning the
property with non-empty analyzed values. No row-level read policy exists
yet, so today every snapshot-visible entity is policy-visible; when one
lands, the exact scan and every native statistic apply it, since a score
computed over rows the reader cannot see discloses their terms. The corpus
is taken before any graph or property filter, so narrowing a population
leaves a surviving entity's score unchanged and scoring composes with graph
filters (the guard `fts_statistics_scope_can_reverse_ranking` shows the
alternative reverses an order). With `N` the corpus size, `df_q` the
documents holding an alternative of query term `q`, `len_d` and `avg_len`
the document and mean token counts, and `tf(t, d)` a term's occurrences:

```text
idf(q)      = log1p((N - df_q + 0.5) / (df_q + 0.5))
norm(d)     = 1.2 · (0.25 + 0.75 · len_d / avg_len)
weight(t,d) = 2.2 · tf(t,d) / (tf(t,d) + norm(d))
part(q,d)   = idf(q) · max over stored terms t within the budget of 2^(-edit(q,t)) · weight(t,d)
score(d)    = Σ over distinct query terms q of part(q,d)
```

`part(q, d)` is zero when `d` holds no stored term within `q`'s budget, which
the mode `any` admits, so the max is never over an empty set. Alternatives
share their group's IDF, so a rare misspelling cannot gain
weight, and the maximum inside a document stops several spellings summing as
independent evidence; the edit weights `1`, `1/2` and `1/4` are versioned
choices. The numeric profile is `F64` with `libm`'s `log1p`, checked `u64`
counting, no reassociation, summation in ascending UTF-8 query-term order; a
match yields a finite positive score or a typed numeric failure. Native
Lance BM25 is this formula in `f32` at zero edits, membership included, and
something else with edits: each expansion gets its own IDF, alternatives
add, and there is no edit penalty, so seven of the ten edit-tolerant oracle
fixtures rank differently natively, by up to 24.5 times
([Evidence](#evidence-and-tests)). Only the zero-edit shape can therefore
ever be certified on a native path.

### Exact execution and qualified acceleration

The analyzer is instantiated
from accepted SchemaIR whether or not an index exists; an index is never its
carrier. Engine v2's scan evaluates the typed predicate over streamed
document tokens with that analyzer and the edit relation, bounding query
bytes, distinct terms, value sizes and work before allocation (NFC can
expand UTF-8). This path is the oracle at every coverage state, and it is
what lets an unbuilt index answer instead of refusing (RFC 0047's
`FullTextIndexRequired`, which this RFC removes). A native path is eligible
only when its artifact passes RFC 0043's proof for the accepted profile and
its whole pipeline is qualified for the mode and edit budget. Lance 13's
fuzzy expansion keeps at most fifty expansions for the whole query, taken
term by term in query order and lexicographically within a term, so an early
term can use up the budget and leave a later term none; it also does not
protect the exact term, so a one-edit query can miss the document that holds
the term exactly: the native path breaks `matches(0) ⊆ matches(1)`, not only
completeness, and edit-tolerant shapes use the exact scan even with an index
until an expansion budget covering the automaton's limit is qualified. A
native index with the experimental 256-document posting blocks quantizes
document lengths, so the RFC 0043 certificate that qualifies one records the
posting block size, the format version and whether positions are stored, and
a 256-block index never qualifies. A native index also carries no NFC step,
and Lance's tokenizer is not the profile's analyzer, except for
`english_legacy_v1`, which is defined as that pipeline and whose index
needs no token column. For the other profiles stage L shares one
analyzer between index and scan *[choice]*. During explicit reconciliation
the engine runs the field's profile analyzer over each row and writes the
analyzed terms, in order and space-separated, to a derived token column;
Lance indexes that column with its `whitespace` tokenizer and every filter
of its own off, the length filter included; and a query's terms pass
through the same analyzer before they reach the index. The index then holds
exactly the terms the scan computes, NFC included, its document lengths are
the profile's token counts, and the measured fingerprint, taken over the
whole native pipeline (the analyzer and the `whitespace` tokenizer
together), passes its parity check by construction. The column is derived state under invariant 7, like
the index it feeds: content writes never fill it, a row whose column is
missing or was written under another fingerprint is uncovered and served by
the scan, it is rebuilt with the index, and the exact scan stays the oracle
without it. It costs storage of about the analyzed text's size and
reconciliation time; stored text is never rewritten. Where the column lives
and how its coverage is tracked is for stage L to decide
([Unresolved](#unresolved-questions)). RFC 0047's validator checks the plans
as invariants, search identity, retained predicate, scoring corpus, score
origin, required ordering, cut placement, prerequisites and the declared
contract, and each shape joins its exact subset only through a versioned
rule addition with its own regression.

A cut over an order over a zero-edit lexical ranking (GQ 3's `Cut` over
`Order` over `bm25`) may use Lance's limited
full-text path as a ***certified candidate generator***
when nothing above the scan drops rows, every filter is a prefilter under
full coverage, no fragment of the table carries a deletion file, no
row-level read policy narrows the property's entities, and the property's
entities live in one table, since native statistics are the index's and not
the field corpus otherwise. The fetch asks for `k + m` candidates, rescored
in `F64` by `bm25_v1`, and is accepted when the k-th rescored score exceeds
the last fetched native score times `1 + ε` plus `δ`: `ε` a stated bound on
Lance's relative `f32` error for the query's term count, and `δ` an absolute
bound, the largest error of a term's native IDF times the largest weight,
`2.2`, summed over the query's terms, since Lance rounds a common term's
IDF to zero at `N = 2^24` ([Evidence](#evidence-and-tests)) and no
relative bound covers an error from zero; a tie fails the margin, and the
exact scan is the fallback. Lance's limited and unlimited paths return
bit-identical scores and the exact top-k score set, but its `f32` order can
tie or invert two documents whose `F64` scores differ by less than an ulp,
which is why bit-identity with Lance is not exactness and the margin is
needed. The fetch prunes scoring work by an amount that depends on `k`
against the matched set and is not reproducible run to run, so the plan
states its cost as the unlimited scan's and the report keeps Lance's
comparison counter out of anything replay compares. Lance 13 runs the
limited path as its bulk MAXSCORE scorer by default and as the classic
block-max WAND loop under `LANCE_FTS_MAXSCORE=0`, with `LANCE_FTS_BULK_AND`
and `LANCE_FTS_SEARCH_CHUNK` selecting further variants. Lance documents
their results as score-identical; they are process environment, so the
engine pins them or the plan records them.

### Vector distances and indexes

A returned distance is always the exact distance under the field's distance;
approximation affects only which targets are found. Today's index family,
IVF_FLAT, keeps raw vectors for `l2` and `dot` and meets this there. For
`cosine`, Lance normalizes the vectors in its transform, uses L2 only to
assign partitions, and scores the flat storage under the index's own
distance, cosine with cached norms (`IvfTransformer::new_flat`, the flat
storage's distance calculator). An index-served value is therefore a
cosine distance, equal to the field's up to `f32` rounding, which need not
be bit-equal to the flat path's value on the uncovered tail; a `cosine`
search re-scores its candidates on the stored vectors (Lance's `refine`)
for that reason, as a quantized family must for every distance. Missing coverage never drops the
uncovered rows; it changes cost.

### Left out

Lance and LanceDB offer search capabilities this RFC does not define.
Each is left out for a stated reason, and none needs a different contract
to add later:

| Capability | Why it is not in this RFC | Where it would go |
|---|---|---|
| Phrase queries and slop | Membership is over terms, with no positions (the term identity rule); indexes are built without positions | a phrase predicate and registry row over any profile, answered by the exact scan, and native once its index is rebuilt with positions ([Schema](#schema)) |
| Per-term or per-field boosts | Emphasis between arms is `rrf`'s `weights`; a boost inside a score changes `bm25_v1`, and a new scorer version needs an RFC | a new scorer version |
| Multi-field scoring (BM25F) | `bm25_v1`'s statistics are one field's corpus; combining fields needs a scorer over a combined corpus | a new scorer version; until then, one `rrf` arm per field |
| A fuzzy prefix length | It changes the edit relation, which the profile and the oracle fix | a new edit relation, versioned like a profile |
| N-gram and substring matching | A different analysis, with Lance's NGRAM index as its acceleration | a new profile and registry row |
| Multivector, `hamming`, and binary, `F16` or `U8` vectors | `Vector(n)` holds `F32`; each element type or distance needs its own exact kernel and schema type | schema additions with a registry row each |

## Invariants

- **7, physical acceleration is derived.** Index presence never chooses the
  analyzer, the matched set, the score or the distance; a lagging or missing
  index changes cost; an index built for another distance is never used.
- **6, stable identity.** Analyzer fingerprints and representation
  identities bind to property identity; a rename keeps them, drop and re-add
  does not.
- **11, bounded resource use.** The exact scan bounds query bytes, distinct
  terms, value sizes and work before allocation, and charges the query's
  memory.
- **12, one source of truth.** SchemaIR owns analyzer, scorer, distance and
  recipe; descriptors and catalogue rows derive from them.
- **13, evidence matches the boundary.** The oracles own scores, and each
  index kind has its fence row and guard in `lance.md`.

Deny-list: no synchronous index build on a content write, no logical
precondition on index coverage.

## Compatibility and reversibility

- **Schema.** Every declaration of this RFC is additive and applied in
  place: an omitted distance is `l2`; an index declared before stage D
  without `analyzer` resolves to `english_legacy_v1`, the analysis it was
  built with, and one declared from stage D on to `english_v1`; a String's
  plain `@index` keeps its full-text index until a `schema plan` under the
  scalar-kinds feature shows the change; a field that declares an analyzer or changes a distance
  rebuilds its index once; one that changes a recipe regenerates its
  vectors. No export and reload.
- **Results.** Rows change only where today's answer depended on index
  state or letter case; a field's analysis changes only when its schema
  declares another profile; `bm25` scores are
  `bm25_v1`'s `F64` on every field, where today's are Lance's `F32`. A
  read that is refused today with `FullTextIndexRequired` returns rows.
  Scores change on adopting fields.
- **Cost.** Wherever stage L does not serve a query, a lexical predicate or
  ranking reads and analyzes the field's whole text at the query's
  snapshot, whatever filter or `limit` the query carries, and a `bm25`
  needs the whole field corpus for its statistics whatever its population.
  The implementation's guide (PR #1316 at `c2051634`) reports about 9.7 s
  over a 171,000-document corpus on two CPUs, where the native index it
  replaced answered in about 113 ms. With stage L a query still scans where
  L does not qualify it: an edit-tolerant query until its expansion is
  qualified, and a limited `bm25` on a table that carries a deletion file,
  the usual state of a table that takes updates or deletes.
- **Reverting.** Reverting a
  distance or recipe needs the rebuild or regeneration that adopting it
  needed. An older binary refuses a full-text index the new binary built or
  rebuilt, since it cannot read that certificate, so once one exists the
  supported rollback is restoring the backup taken before the upgrade.

## Alternatives

| Alternative | Why not |
|---|---|
| `@analyzed` beside `@index(fulltext)` | Two annotations configuring one index; the analyzer is the index's option |
| An explicit distance required on every vector | Every accepted schema would break; an omitted distance is `l2`, what they mean today |
| Native fuzzy defaults or truncated expansion as the contract; rescoping statistics by every graph filter; a warning on unindexed search | Results would change with analyzer, index and segment state; rescoping can reverse the order of the same entities; a warning does not repair a matched set |
| Bit-identity with Lance's unlimited scan as the WAND qualification | Lance scores in `f32`; two documents whose `F64` scores differ by less than an ulp can tie or invert, and did on a real index, so a certified margin with an `F64` rescore is needed, not a tie check |
| `english_v1` for every field without `analyzer`, accepted fields included (the GQ 3 RFC from 2026-11-15) | Every accepted full-text field would change its analysis at upgrade, so one query returns different rows over the same data; a default that resolves once, as a recipe's does, changes none |
| The scalar kinds derived for every accepted `@index` when the binary changes | An accepted String `@index` would lose its full-text index with no schema change and the built index would go unread; as a feature name the change appears in `schema plan` and is chosen |
| Stage D alone, the exact scan as the only path before any acceleration | Every full-text read becomes a whole-field scan (Compatibility, Cost) |
| These contracts as section 4 of the GQ 3 RFC (that RFC from 2026-11-07 to 2026-11-18) | They are `.pg` declarations and call meanings that need no edition-3 form, and they have another way back: an index built under a profile is undone by a rebuild or a backup restore, the break by reverting a change. In one document, accepting, shipping or reverting either one waited on the other |

## Evidence and tests

Existing owners, per [the testing map](../dev/testing.md):

- **Lexical oracles**: the Decimal oracle and its fixtures at
  [`ce5a3012`](https://github.com/ModernRelay/omnigraph/tree/ce5a3012d655f5a47c4475ada6ac5b8d4e488fbd)
  (the formula, zero edits, alternatives, `all` and `any`, null and
  token-empty values, two-edit membership) and the Rust test that consumes
  them; the four Lance guards at
  [`1e0bed40`](https://github.com/ModernRelay/omnigraph/blob/1e0bed40dbf1650ca78bb1b4fca225c04dd21810/crates/omnigraph/tests/lance_surface_guards.rs)
  re-qualified on Lance 13.0.0; the regression cases for #747, #748 and
  #749 as `issue_N_*.gqt` across absent, partial, full, removed and rebuilt
  indexes; the predicate and the ranking agreeing on membership for one
  `terms`; more than 50 expansion terms, a huge token, automaton failure and
  cancellation, each a complete result or a typed failure; score parity
  with the oracle; more tied entities than a native window, in reversed id
  order; the certified-margin fetch against the inversion guard.
- **Vector oracles**: distances for the three kinds, ties, and `distance`
  under built, partial and no index.
- **Native BM25 guards** (`spike/gq3-lexical`, all passing on Lance 13.0.0
  in debug and release): Lance's fuzzy scoring rewards a rare expansion
  38.35 times over the exact term and doubles a repeated query term, and its
  `f32` IDF rounds a common term to zero at `N = 2^24`
  (`native_fuzzy_bm25_rewards_a_rare_expansion`,
  `native_bm25_idf_can_round_a_common_term_to_zero`); a corpus-scoped index
  reverses the field-corpus ranking and a row-address prefilter keeps it
  (`fts_statistics_scope_can_reverse_ranking`); the native builder has no
  NFC step and a training-JSON `normalization` field is silently dropped
  (`nfc_preprocessing_requires_an_explicit_bounded_integration`); native
  scores equal `bm25_v1` within `f32` rounding at zero edits and rank seven
  of ten edit-tolerant fixtures differently
  (`gq3_bm25_v1_oracle_fixtures_against_native_lance_scores`); the limited
  path is bit-identical to the unlimited one and returns the exact top-k
  score set in 18 of 18 measurements, partial coverage included, while
  pruning a run-dependent share of comparisons
  (`gq3_bm25_fetch_through_block_max_wand`); an `f32` tie and an inversion
  against `bm25_v1` on a real 4,040-document index
  (`gq3_native_f32_bm25_can_tie_or_invert_the_bm25_v1_order`); a one-edit
  query under the default expansion budget missing the exact term
  (`gq3_native_fuzzy_expansion_truncates_membership_silently`). The two
  `gq3_bm25_*` guards own a new compatibility-fence row in
  [`docs/dev/lance.md`](../dev/lance.md) when stage L lands.

Lance surfaces read by this RFC's design: `InvertedIndexParams::build()`
for the analyzer without an index, `MatchQuery::with_max_expansions`,
`FullTextSearchQuery::limit` and `Scanner::limit`, `scan_stats_callback`,
and `Scanner::with_row_addr_prefilter`, all public; statistics injection on
the scanner path and an `F64` scorer inside WAND are absent upstream, which
is why the certified margin rescores outside Lance.

## Rollout

| Stage | Contents | Ships after |
|---|---|---|
| **D** declarations and the exact scan | the schema declarations of this RFC (`analyzer` with its default resolved once, `scorer`, `distance`, the default recipe, the scalar index kinds by type, the measured analyzer fingerprint in RFC 0043 certificates); the exact lexical scan on every full-text field | nothing |
| **L** lexical acceleration | native full-text acceleration where an artifact proves parity: the certified-margin fetch for zero-edit `bm25` with its preconditions, complete fuzzy expansion through `with_max_expansions` within the automaton's limit, the shared analyzer (a derived token column indexed with Lance's `whitespace` tokenizer, which carries NFC into the index), the RFC 0043 certificate with posting block size, format version and positions, a query that repeats an analyzed term scoring as the same query without the repetition, the `lance.md` fence row | D; its zero-edit paths in D's release |
| recipe providers | recipe providers that prove an immutable revision | D |

Stage D is what the GQ 3 RFC's stage B carried for these fields, under its
own name. It needs no edition-3 form, so it may ship before the GQ 3 break
or in the same release, and the break ships no earlier than D, since its
search calls read these contracts. Shipped before the break, edition 2's
`search`, `match_text`, `fuzzy`, `bm25` and `nearest` take the contracts
through the mapping in
[What a query surface supplies](#what-a-query-surface-supplies), and the
result changes of Compatibility arrive with D, not with the break.

D and stage L's zero-edit paths ship in one release *[choice]*. Alone, D
would turn every full-text read into a whole-field scan (Compatibility,
Cost), so the release that makes the scan the reference also carries the
index path for a qualified zero-edit query. L's edit-tolerant expansion may
follow.

## Unresolved questions

1. The `.pg` spelling of the schema-level default recipe, and which
   providers can prove an immutable model revision.
2. The probe corpus of the measured analyzer fingerprint: which inputs it
   must hold for a Unicode data or stemmer change to show, and how a profile
   names its Unicode data identity.
3. Where stage L's derived token column lives, a column of the graph table
   or a derived dataset per field, how its coverage is tracked across
   updates, compaction and merges, and whether the exact scan reads it for
   covered rows.
4. The values of `ε` per term count and of `δ` per term in the
   certified-margin fetch; the largest relative `f32` error observed was
   1.68e-7, and a common term's native IDF rounds to zero at `N = 2^24`.
5. Whether stage D ships before the GQ 3 break or in the same release.
   Before it, edition 2's search calls change rows as Compatibility lists
   while edition 2 is still accepted; with it, one release carries both and
   they revert together.
6. Whether an index built before stage D qualifies for `english_legacy_v1`
   under its existing RFC 0043 certificate, which records no measured
   fingerprint, or is served by the scan until it is rebuilt.
7. The storage and build cost of an NGRAM index on every `@index` String,
   not yet measured, and whether NGRAM is derived by default or asked for.

## Decision log

- 2026-11-18: split out of the GQ 3 RFC in the review of PR #1302, from that
  RFC at `21cc4999`. Moved with no rule changed: section 4's Schema, the
  lexical matching rules, `bm25_v1`, exact execution and qualified
  acceleration with the certified candidate generator, the vector index
  paragraph and six rows of Left out; the Schema, Results and Reverting
  entries of Compatibility that concern these fields; four Alternatives
  rows; the lexical, vector and native BM25 evidence; stage L and the recipe
  providers of stage R; unresolved questions 8 to 11; and the `blocked_on`
  gate for the lexical oracles. Written by the split: the Summary, the last
  paragraph of Motivation, What a query surface supplies, the first
  paragraph of Lexical matching, the Invariants entries for 11 and 13, the
  last Alternatives row, stage D as a name for what stage B carried, the
  Rollout paragraph and unresolved question 5. Where a moved sentence dated
  a change, "edition 2" became "today" and "the break" became "stage D".
  The decisions behind the moved text are in the GQ 3 RFC's decision log up
  to 2026-11-17.
- 2026-11-18: fixes from the review of PR #1302. Three rules change, each so
  that an accepted declaration keeps the meaning it was accepted with. An
  omitted `analyzer` resolves once: `english_legacy_v1`, today's analysis
  stated as a fourth profile, for a field indexed before stage D, and
  `english_v1` after; before, every accepted field changed analysis at
  upgrade. The scalar index kinds are a feature name, so an accepted
  String `@index` keeps its full-text index until a `schema plan` shows the
  change; the text had said no accepted schema changes meaning. Stage D
  ships with stage L's zero-edit paths, since D alone makes every full-text
  read a whole-field scan. Corrected against Lance 13's source: an IVF_FLAT
  cosine index scores under cosine, not L2, so the re-score is for
  bit-equality with the flat path; the limited full-text path is bulk
  MAXSCORE by default, switched by three environment variables the engine
  pins or the plan records. Compatibility gains the scan's cost. Gates
  added: a checked-in cost instrument, and the token column's design before
  stage L. Unresolved questions 6 and 7 are new.
