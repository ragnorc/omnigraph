# GQ 3 upgrade fuzz: random GQ 2 graphs through the GQ 3 binary

Run 2026-11-23/24. Graphs built by GQ 2 binaries from randomly drawn
configurations and operation scripts, then upgraded step by step with the GQ 3
binary of draft PR ModernRelay/omnigraph#1316, with GQ 2 binaries probing the
graph after every step; then GQ 2 and GQ 3 binaries on one graph, alternating,
concurrently and killed mid-operation. Synthetic data only. Nothing here changes
`main`, `gq3/phase-0-front-end`, the RFC or #1316. This extends the
[migration matrix](../migration-matrix/README.md) (its runner, respelling,
comparator and classifier), which covered a fixed 112-graph product.

| | binary | commit | `omnigraph version --json` |
|---|---|---|---|
| GQ2-REL | released v0.16.0 (`scripts/install.sh`, `VERSION=v0.16.0`) | `00b8cca842f2bf009bebeefcf765672b5ff64b26` | 0.16.0, storage writes 17, serves 14–17, no `gq_language` |
| GQ2-MAIN | `main`, the second parent of #1316's newest merge of `origin/main` | `9ad7c8ef91594463636a3048ea20bbb557720cc6` | 0.16.1, writes 18, serves 14–18, no `gq_language` |
| NEW | #1316's head when the run started, kept fixed | `a74de14071cf125157eb147b7209a4357cba9071` | 0.16.1, writes 18, serves 14–18, `gq_language` 3.0 |

GQ2-MAIN and NEW were built once each with the shipped release recipe
(`cargo build --release --locked -p omnigraph-cli -p omnigraph-server -p
omnigraph-azure-admission --features omnigraph-server/aws`, `RUSTFLAGS=""`).
`results/raw/versions.txt` holds the three `version --json` outputs.

GQ2-REL's storage stamp (17) differs from NEW's (18), so `omnigraph upgrade`
(route `stamp-v14-to-v18`) is one of the NEW steps. A graph GQ2-MAIN builds is
born at 18, which GQ2-REL cannot open, so GQ2-REL probes only graphs GQ2-REL
built.

**Contents:** [Summary](#summary) · [What ran](#what-ran) ·
[Findings](#findings) · [Rollback](#rollback-per-new-step-and-gq-2-binary) ·
[The matrix's earlier defects](#the-matrixs-earlier-defects-at-new) ·
[Mixed binaries and crashes](#mixed-binaries-and-crashes) ·
[Coverage](#coverage) · [Reproducing](#reproducing)

## Summary

1,155 fuzz cases (1,001 random configurations, 24 at 100,000 rows, 130 with
GQ 2 and NEW writers alternating), 11 concurrent two-binary runs and 87
kill -9 runs; about 46 case-hours over 4¼ hours of wall clock on 32 cores.
No binary panicked or hung, no acknowledged write was lost, no graph became
unreadable to NEW, and every case that GQ 2 could read exported the same
bytes from GQ 2 and NEW.

* **Upgrade path (Part 2):** across 588,417 comparisons of NEW's answers
  between states with the same logical content (index never built, lagging,
  uncovered tail, rebuilt by GQ 2, rebuilt by NEW, after `optimize`, after
  `cleanup`, after each analyzer or scorer change and back), the only
  differences were `nearest` (F1) and one memory refusal (F6). NEW's lexical rows and `bm25` scores
  matched an independent reference of each profile in all 51,587 checks
  (`bm25_v1` scores within 1e-9), and every analyzer change and change-back
  behaved as oracle 2 asks. Every GQ 2/NEW difference fell in a signature the
  matrix sanctions or in two listed bullets (index state, tie rule).
* **Rollback (oracle 4):** after every NEW step GQ 2 refused the whole graph
  exactly when the stored schema held a construct `schema plan` listed, or
  the graph was stamped 18 (GQ2-REL), and every listed construct made both
  GQ 2 binaries refuse. GQ 2 refused full-text reads only after NEW's
  `index rebuild --kind full-text` (465 transitions), and its own rebuild
  restored them every time (2,286 of 2,286). Two places where the guides say
  more than the binary does: a one-way construct can be removed again by a
  NEW apply, after which GQ 2 opens the graph (F7), and NEW never reports an
  outpaced older full-text index as pending (F4).
* **Defects (class c):** one introduced by GQ 3's index kinds, seen when
  binaries are mixed: after GQ 2's index pass rebuilds NEW's NGRAM index as a
  BTREE under the same name, NEW's `index build` fails until something
  refreshes that BTREE (F2). Three predate GQ 3 (same in GQ2-REL, GQ2-MAIN and
  NEW): `nearest` returns fewer than `k` rows after a vector index build, a
  delete and `optimize` (F1); `index build`/`index rebuild`/`optimize`/`cleanup`
  refuse in plain text under `--json` (F3); near `query_memory_bytes`,
  physical layout decides between an answer and a refusal (F6). Two are
  documentation (F4, F5), one a documentation gap in the safe direction (F7).
* **The matrix's earlier defects:** all seven are fixed at NEW, and the three
  undocumented breaks are fixed or now documented
  ([table](#the-matrixs-earlier-defects-at-new)).
* **Mixed binaries (unsupported):** both binaries failed loudly, never
  silently: every acknowledged write of 11 concurrent runs is in both exports,
  no reader saw a count go down or a partial type, and every kill left the
  graph in its before or after state, openable by NEW and by GQ 2 (unless the
  killed apply had already published a one-way construct). The one
  mixed-binary defect is F2.

## What ran

`harness/` holds everything, as an extension of `migration-matrix/harness/`:
`ufcommon.py` adds the third binary to the matrix's `lib.py` runner and its
p22 comparator; `ufclassify.py` reuses the matrix's `analyze.classify_read`
and `results/differences.json`.

1. **Configurations** (`ufgen.py`, seeded): 1–6 node types, 0–4 edge types;
   every scalar type nullable and required, enums, `[String]`/`[I32]`,
   `Vector(2|4|8)` with and without `@index` (and, rarely, a `distance`),
   managed `Blob` and `Blob?`; `@key` single (String or I64), compound and
   absent; `@unique`, `@range`, edge `@card`, `@key(@src, @dst)`,
   `@unique(@src, @dst)`; the index mix: plain `@index` on Strings (edition 2's
   full text), `@key` Strings with and without `@index`, `@index` on numeric,
   enum, Bool, Date, vector properties, body-level `@index(list)` (edition 2
   accepts it), property-level `@index` on a list and `@index(fulltext)`
   (edition 2 refuses both), several text fields on one type, types with no
   index. Text draws on empty and null strings, 200–1,200-word values,
   repeated terms, stop words only, `run`/`running`/`runs`, case and accent
   variants, NFC and NFD of one word, emoji, CJK, Greek, Cyrillic,
   punctuation-only, ligatures, 39- and 40-byte tokens and near-duplicates for
   fuzzy matching. Types hold 10 to 20,000 rows (100,000 in the stress part).
   228 drawn schemas held something GQ 2 refuses; each refusal was recorded
   and explained, and the case continued with those constructs removed.
2. **Part 1** (`ufcase.Case.part1`): the case's GQ 2 binary (GQ2-REL for
   743 cases, GQ2-MAIN for 258) runs `init`, the base load, then 2–14 drawn
   operations: `load` in `merge`, `append` and `overwrite`, inserts, updates,
   deletes and edge inserts in edition-2 text, `index build`, `optimize`,
   `cleanup --keep N`, `index rebuild --kind full-text`, branches created,
   written, merged, deleted and recreated, and schema evolution by apply
   (added type, added, renamed and dropped properties, an index added or
   removed). 8,999 operations ran.
3. **Part 2** (`ufcase.Case.part2`): on a copy, a drawn NEW sequence of 4–12
   steps (8,054 in all): read-only and read-write opens; writes (`insert`,
   `upsert`, `replace`, `update`, `delete`, edge insert); loads (`insert`,
   `upsert`, `replace`, `overwrite`); `index build`; `index rebuild --kind
   full-text`; `optimize`; `cleanup`; branch create/write/merge/delete; NEW
   merging a GQ 2 branch; `upgrade`; and schema applies (a) `@index` to
   `@index(fulltext)`, (b) adding each analyzer, (c) changing an analyzer to
   another and back, with an `index build` between in 60 %, (d) adding or
   changing `scorer`, (e) writing `english_v1` out and removing it, (f)
   `@index(fulltext)` back to plain `@index`, (g) renaming or dropping and
   re-adding a full-text field, (h) an explicit `distance`, (i) `@index` on a
   list, property- or body-level, plus ordinary evolutions. Live branches are
   deleted before an apply (main-only); `schema plan` runs before every apply
   and its `one_way_constructs` is kept. In 60 % of cases the one-way applies
   were moved to the end so that GQ 2's probes see the earlier steps.
4. **After every NEW step** (`observe`): NEW's snapshot and export; the read
   set (`ufquery.py`: per type a scan and a count; per text field `match_terms`
   any, all with 1 edit, any with 2 edits, `bm25` limit 10, `bm25` with all
   its ties; per scalar property a range, an equality, `contains`,
   `starts_with` or list `in`; `nearest` per vector; `rrf`; traversals) in its
   hand respelling and in `lint --fix`'s respelling of the edition-2 text;
   and each GQ 2 binary's probe: `snapshot`, the edition-2 read set, `export`,
   a write on a copy, and on another copy its own `index build` and
   `index rebuild --kind full-text` followed by NEW's reads there.
5. **Oracles**: (1) NEW's answers (columns, Arrow types, rows, `F64` scores,
   tie order) are equal across every state with the same semantic schema and
   canonical export, including GQ 2's rebuilt copies, and equal to a fresh
   graph NEW builds from its export at the end of each case; (2) the same
   grouping makes an analyzer change answer per the new profile at once, the
   same before and after the following `index build`, and exactly as before
   when changed back, and `ufanalyzer.py` checks NEW's lexical rows and
   `bm25_v1` scores against a reference written from `analyzers.md` (stop
   lists taken from NEW's `analysis.rs` and from the `stop-words` crate Lance
   uses); (3) GQ 2's answer against NEW's on the same graph, classified by
   the matrix's rules and table, then by the reference (edition 2's Lance
   default analysis against the profile) for lexical reads; every NEW write
   and load is mirrored by GQ2-MAIN on the pre-step graph and the exports
   compared; (4) the rollback rules below; (5) no panic, hang or uncoded
   refusal under `--json`, NEW opens its graph, and GQ 2's export of a
   commit equals NEW's.
6. **Part 3** (`ufmixed.py`): alternating cases (a GQ 2 binary and NEW take
   turns on one graph, with index passes interleaved after the other side's
   writes, observed as in Part 2); concurrent runs on a 20,000-row graph
   (GQ 2 writes while NEW reads, NEW writes while GQ 2 reads, both write; with
   and without each writer running `index build`, full-text rebuild,
   `optimize` and `cleanup`); `kill -9` of NEW at a random point of
   `index build`, `index rebuild --kind full-text`, an analyzer apply, a plain
   apply and `optimize` (delays drawn over the operation's own measured
   duration), then each binary reopens, reads and exports, NEW reruns the
   operation, and `cleanup` runs.
7. **Shrinking** (`ufshrink.py`): delta debugging over the NEW steps and GQ 2
   operations, then types, properties and row counts, each trial a full
   replay; `ufreplay.py` replays a saved case.

Seeds: Part 2 1000–2000, stress 9000–9023, alternating 5000–5007,
5008–5027, 5100–5127, 5200–5224, 5300–5321, 5400–5429; kill seeds 1–2,
10–12, 20–29, 40–44. `results/cases-index.jsonl` lists every case with its
builder, sizes, operations, steps and findings. The harness gained checks
while running: the scalar-index reads from about seed 1100, the probe-write
fix from about 1460, and the refused-maintenance check from about 1590;
earlier seeds ran without them. Cases stop at 300 s (23 of 1,001 were
truncated, and 21 alternating ones at 280 s).

## Findings

Classes: **a** intended and listed in the RFC's Compatibility and
reversibility section; **b** intended but not listed; **c** a defect.
"Pre-existing" means GQ2-REL (v0.16.0) and GQ2-MAIN behave the same, so GQ 3
did not introduce it. Every repro is a script under `repro/`
(`repro/output.txt` is one run of `repro/run-all.sh` against the three
binaries); `results/findings.md` lists every signature with its seeds.

| # | class | oracle | smallest repro | GQ 2 result | NEW result |
|---|---|---|---|---|---|
| F1 | c, pre-existing | 1, 5 | `nearest_after_optimize.sh`: one type, 10 rows, `v0: Vector(2) @index`; `index build`, delete 5 rows, `optimize`, `nearest(k: 5)` | 3 rows of the 5 live ones (REL and MAIN) | 3 rows; a graph rebuilt from the export answers 5 |
| F2 | c | 5, rollback | `index_build_duplicate_ngram.sh`: `body: String @index`; NEW `index build`, rows, GQ 2 `index build`, rows, NEW `index build` | GQ 2's pass rebuilds `body_ngram` as a BTREE | every `index build` fails: `index batch produced duplicate index name 'body_ngram'`, plain text |
| F3 | c, pre-existing | 5 | `maintenance_json_plain_text.sh`: `index build --branch nope --json` | plain text on stderr, exit 1 | the same |
| F4 | c (doc) | 4 | `old_fulltext_not_reported_pending.sh` | GQ 2's pass lists its outpaced full-text indexes as pending | `pending_indexes: []`; `analyzers.md` says it reports them |
| F5 | c (doc, minor) | 4 | `zero_step_plan_lists_no_one_way.sh` | — | a no-step `schema plan` of a schema holding `@index(fulltext, analyzer …)` lists no `one_way_constructs` |
| F6 | c, pre-existing, minor | 1 | `memory_refusal_depends_on_layout.sh` (4,000 rows, lowered limit); fuzz stress seed 9011 (100,000 rows, default limit) | MAIN: refused after an index build or `optimize`, answered before | the same |
| F7 | b (doc in the safe direction) | 4 | `one_way_undone_by_new_apply.sh` | opens and reads the graph again | applies `@index(fulltext, analyzer …)` back to `@index` in one published step |
| — | a | 3 | seed 1855 | `nearest … limit 5` over a nullable indexed vector: 4 rows | 5 rows, GQ 2's 4 as a prefix (results.index-state) |
| — | a | 3 | alternating seed 5321 | a distance tie at the `limit` boundary resolved by scan order | resolved by the tie rule (results.limit-tie-rule) |

### F1. `nearest` returns fewer than `k` rows after a vector index build, a delete and `optimize`

Class c, pre-existing; oracle 1 (the index state changed the answer) and 5 (a
silent partial result). With a vector index, deleting rows and then running
`optimize` makes `nearest(…, k)` return fewer than `k` rows although at
least `k` live rows exist. The rows returned are the true nearest rows in
order; the result is cut short. At 5,000 rows with half deleted,
`nearest(k: 50)` returned 24. `index build` reports nothing to do, and there
is no vector `index rebuild`; only export and reload (or no index) gives `k`
rows. A scalar update (a column patch) and a whole-row `replace` did not
trigger it; deletes did, whether GQ 2 or NEW made them and whether GQ 2's or
NEW's `optimize` compacted them. It also shortens an `rrf` arm
(seed 1813: 6 rows instead of 8). Found in 4 Part 2 cases (seeds 1168, 1813,
1828, 1919; shrunk: `repro/nearest_after_optimize.json`, and
`nearest_after_optimize_s1813.json`, where GQ 2 ran the three operations).
The shape suggests the compacted vector index still holds entries for rows
the compaction dropped, Lance returns `k` candidates, and the dropped ones
are filtered out after; NEW's overfetch ladder
(`crates/omnigraph/src/engine/search.rs`, `next_overfetch_rung`) does not
widen an unfiltered scan that comes back short. Invariants 7 and 8.

### F2. After GQ 2 rebuilds NEW's NGRAM index as a BTREE, NEW's `index build` fails

Class c, introduced by GQ 3's index kinds; seen only with binaries mixed,
which includes rolling back and then upgrading again. `operations/upgrade.md`
says an earlier binary "reads the BITMAP, LABEL_LIST and NGRAM indexes
`@index` builds, but rebuilds a lagging one as a BTREE or fails", and offers
"rebuild the indexes with the earlier binary" as a rollback. GQ 2's
`index build` does replace NEW's lagging `body_ngram` with a BTREE of that
name (its report lists `body btree refreshed` twice). Once rows land after
that, every NEW `index build` on the type fails:

```text
stage index batch on node:Doc ([Scalar { column: "body", family: NGram }]):
index batch produced duplicate index name 'body_ngram'
```

as plain text under `--json` (exit 1), and keeps failing: NEW's full-text
rebuild and `cleanup` do not clear it. NEW's `optimize` does (afterwards
`index build` has nothing to do: the BTREE named `body_ngram` stands for the
NGRAM), as does another GQ 2 `index build`. Answers stay correct; NEW loses
its index maintenance for that type. Found by the concurrent Part 3 runs
(both writers with maintenance, 5 failures in one run), shrunk by hand;
deterministic with either GQ 2 binary, and absent when GQ2-MAIN plays both
roles. At fault: `crates/omnigraph/src/table_store.rs`, `IndexBatch::extend`
receives a fold of the lagging index named `body_ngram` and a build of the
missing NGRAM named `body_ngram` in one batch; the reconciliation identifies
the index by name, not by kind.

### F3. Maintenance commands refuse in plain text under `--json`

Class c, pre-existing (GQ2-REL, GQ2-MAIN and NEW alike); oracle 5. `index
build` and `index rebuild` with `--json` print a refusal as colored text on
stderr, exit 1, no JSON and no code, where `snapshot --json` returns
`{"code": "not_found", …}` for the same missing branch. The fuzz met the same
shape from `index build`, `index rebuild`, `optimize` and `cleanup` for every
refusal it saw: write contention (`write authority 'graph_head:main' changed
during preparation`, `collector branch 'main' changed during capture`), an
older binary's whole-graph refusal after a one-way apply, GQ 2's LABEL_LIST
fold (`A btree index can only be created on a non-nested field`, the
documented "or fails") and F2's duplicate name. 282 occurrences from GQ 2
binaries in Part 2 and in alternating cases, plus NEW's own under
contention (concurrent runs) and in F2. At fault:
`crates/omnigraph-cli/src/commands/maintenance/mod.rs`: `run_index`,
`run_optimize` and `run_cleanup` pass `json` only to their success writers
and return errors through `?` to the plain-text error reporter, where
`snapshot` and `mutate` print the coded JSON envelope.

### F4. NEW never reports an outpaced older full-text index as pending

Class c (documentation). `search/analyzers.md` says an earlier release's
full-text index on a plain `@index` or `@key` "is left in place: no read uses
it, … an index pass neither removes nor refreshes it and reports it pending
once rows outpace it". After NEW writes rows past GQ 2's full-text index,
NEW's `index build` reports `pending_indexes: []`, twice; GQ 2's own pass on
the same graph lists both fields. Across 947 NEW `index build` steps on GQ 2
graphs in every index state, none reported a full-text index pending. The
same silence covers a field respelled to `@index(fulltext, analyzer = …)` on
an upgraded graph: NEW's next `index build` builds no postings for it (GQ 2's
old index stays and counts as present), although the same paragraph says
"the next `omnigraph index build` builds its postings with the profile"; its
last sentence ("an index built before its field had a profile is rebuilt
with the profile by `omnigraph index rebuild`") is what applies. No answer
changes (no read uses the index). This is the other side of the matrix's
defect 4, which made NEW advise the one-way rebuild; the code no longer
advises it and the guide was not updated.

### F5. A `schema plan` with no step lists no one-way constructs

Class c (documentation, minor). `queries/upgrading-to-gq3.md` says `schema
plan`, `schema apply` and `cluster plan` "name each one the schema holds,
under `one_way_constructs`". A plan of a schema identical to the stored one
returns `steps: []` and no `one_way_constructs`, although the stored schema
holds them; any one-step change lists them all again. Seen in 127 cases
(sub-kinds c, d, e, f, g, h, i re-applying a stored text). No harm: a no-step
apply publishes nothing. Either the plan should list them or the guide should
say "each one the change stores".

### F6. Near `query_memory_bytes`, physical layout decides between an answer and a refusal

Class c, pre-existing, minor; oracle 1. Stress seed 9011: a sorted range read
over an indexed `DateTime` matching 53,999 of 100,000 rows answers on a graph
loaded from the export, and is refused (`resource limit exceeded for
query_memory_bytes: actual 191148197, limit 157286400`) after that graph's
`index build` or `optimize`, on the upgraded graph and on the GQ 2-built one,
by GQ2-MAIN and NEW alike. The small repro measures each state's need by
bisection: the index build raises the same read's need (26,655,859 to
26,692,470 bytes at 4,000 rows), so a limit between them answers before the
build and refuses after. The refusal is loud and typed, so this is a
predictability issue rather than a wrong answer; invariant 7 says missing
coverage may change cost but not correctness, and here the index's presence
turns an answer into a refusal.

### F7. A one-way construct can be removed again by a NEW apply

Class b (intended mechanism, documented as impossible). Both the RFC's
Reverting bullet and `upgrading-to-gq3.md` say that once a schema apply
stores a one-way construct, "only the backup taken before the upgrade
restores" an earlier binary's access. A NEW apply that removes the construct
also restores it: `@index(fulltext, analyzer = "standard_v1")` respelled back
to `@index` is one published step, and GQ 2 then opens, reads (its own
earlier full-text index answers with its own stemming), writes and exports
the graph; dropping the field does the same. 179 transitions from
`whole_refused` to `ok` or `fts_refused` followed `f_to_plain` and `g_drop`,
and GQ 2's reads there matched NEW's except where the matrix sanctions a
difference. Not every construct can be taken back this way: removing an index
is refused (`removing property constraints … is not supported`), and an
explicit `distance = "l2"` written back to an omitted one is a no-step apply.
The guide could say which constructs a later apply can remove.

### Not findings

* **GQ 2's `nearest` was short where NEW's was not** (seed 1855, both GQ 2
  binaries): over a nullable indexed vector with rows appended after the
  index, GQ 2 returned 4 rows for `limit 5`, an exact prefix of NEW's 5; NEW
  equals the fresh graph. Class a, results.index-state.
* **Ties at the `limit` boundary** (alternating seed 5321): two rows at the
  same distance, GQ 2 keeps the first scanned and NEW the one its tie rule
  orders first. Class a, results.limit-tie-rule.
* GQ 2's `index build` failing after NEW built a LABEL_LIST index over a
  body-level `@index(list)` (`A btree index can only be created on a
  non-nested field`, 96 probes) is the guide's "or fails"; it is listed under
  F3 only for its plain-text output.
* Concurrent maintenance refusing with `changed during preparation` /
  `changed during capture` is the documented single-writer topology; with
  both binaries writing, NEW's maintenance mostly lost the race (in four
  `both-write` runs 1 of 75 `index build`, 9 of 72 `optimize` and 3 of 72
  `cleanup` completed), as GQ 2's did against NEW's writes.

## Rollback per NEW step and GQ 2 binary

After each NEW step, each GQ 2 binary opened the graph. `ok`: everything
answered; `fts`: lexical reads refused with `full_text_index_rebuild_required`,
all else answered; `whole/one_way`: every command refused because the stored
schema holds a listed construct; `whole/stamp`: the graph is at format 18.
Counts are observations; arrows count status changes caused by the step; "GQ 2
rebuild" counts GQ 2's own `index rebuild --kind full-text` restoring its
lexical reads on a copy (it never failed to). The full table is
`results/rollback.md`.

| NEW step | GQ2-REL afterwards | GQ2-MAIN afterwards | docs say | verdict |
|---|---|---|---|---|
| start (before any NEW step) | ok 604 | ok 809 | — | — |
| read-only open, read-write open | ok 212, fts 41 (earlier rebuild), whole 59 | ok 305, fts 59, whole 52 | unaffected | holds; no file changed by read-only commands |
| write, load | ok 539, fts 136, whole/one_way 117, whole/stamp 69; fts→ok 2 (an overwrite load dropped the index) | ok 824, fts 184, whole/one_way 155; fts→ok 3 | unaffected | holds |
| `index build` | ok 300, fts 88, whole 197; no transition | ok 460, fts 111, whole 223; no transition | upgrade.md: may refuse full-text reads afterwards | never caused a refusal; the guide is conservative |
| `index rebuild --kind full-text` | ok→fts 195; rebuild restored 248 | ok→fts 270; rebuild restored 336 | full-text reads refused, own rebuild restores | holds |
| `optimize`, `cleanup` | ok 278, fts 84, whole 94 | ok 402, fts 117, whole 91; no transition | upgrade.md lists `optimize` | never caused a refusal |
| branch create/write/merge/delete, NEW merging a GQ 2 branch | ok 230, fts 65, whole 61 | ok 335, fts 94, whole 55; no transition | unaffected | holds |
| `upgrade` (stamp 17 to 18) | whole/stamp 100; ok→whole 60 | ok 68, fts 15, whole/one_way 17 | "a binary that serves only up to v17 then refuses the graph" | holds |
| schema (a) `@index(fulltext)` | ok→whole 33, fts→whole 18 | ok→whole 51, fts→whole 27 | `full_text_index` listed | holds |
| (b) analyzer, (c) change and back, (d) scorer, (e) `english_v1` explicit/removed | whole in every observation; 243 transitions into whole | whole in every observation; 358 transitions into whole | `full_text_analyzer`, `full_text_scorer` listed | holds |
| (f) `@index(fulltext)` → plain `@index` | whole→ok 26, whole→fts 16 | whole→ok 41, whole→fts 23 | only a backup restores | **F7** |
| (g) full-text field: rename keeps the construct; drop then re-add | drop: whole→ok 19, whole→fts 9; re-add: ok/fts→whole 35 | drop: whole→ok 33, whole→fts 12; re-add: ok/fts→whole 55 | only a backup restores | **F7** (drop) |
| (h) explicit `distance` | ok/fts→whole 22; `l2` alone: no-step, stays ok | ok/fts→whole 35 | `vector_distance` listed; a no-step apply publishes nothing | holds (the matrix's defect 1 is fixed) |
| (i) `@index` on a list, property-level | ok/fts→whole 18 | ok/fts→whole 32 | `list_index` listed | holds |
| (i) body-level `@index(list)` | ok 33, fts 10; no transition | ok 61, fts 11 | not one-way | holds |
| ordinary evolution (added type or property, rename, drop, index added) | no transition | no transition | not one-way | holds |

`schema plan`'s `one_way_constructs` matched the constructs each applied
schema holds in every plan with a step (2,995 schema steps), and matched
`schema apply`'s list; every whole-graph refusal followed one of them or the
stamp, and every listed construct made both GQ 2 binaries refuse
(`rollback_whole_unlisted` and `rollback_listed_not_refused`: 0). Full-text
refusals followed only NEW's full-text rebuild, and GQ 2's own rebuild
restored them in 2,286 of 2,286 probes.

## The matrix's earlier defects at NEW

The matrix's `repro/repro.sh` against GQ2-REL and NEW
(`results/raw/matrix-repro-at-new.txt`), confirmed by this run's counts:

| matrix item | at NEW | evidence |
|---|---|---|
| 1. explicit `distance = "l2"` locks GQ 2 out | fixed | plan and apply report 0 steps and `applied: false`; GQ 2 opens the graph (49 no-step `h` applies in the fuzz) |
| 2. `@index` on a list locks GQ 2 out with a parse error | documented: listed as `list_index` in `one_way_constructs` and the guide; GQ 2's message is still a parse error, not a feature name | 83 `i_prop` steps |
| 3. one refused stored query makes its whole file unavailable | fixed | `queries list` serves `fine` and lists only `old_form` unavailable |
| 4. NEW's `index build` steers to the one-way rebuild | fixed (0 advisories in 947 `index build` steps); the guide now says the opposite of the code, F4 | |
| 5. `fuzzy(f, q, 1)` gets a wrong fix | fixed | fix `max_edits: 1` with a machine-applicable edit |
| 6. `lint --json` drops fixes; no `lint --fix` | fixed | `lint --json` carries `fix` and `suggestion`; `lint --fix` respelled every read set; its respelling answered exactly as the hand respelling in every comparison |
| 7. `load --mode merge --json` plain-text usage error | fixed | coded `Q004` JSON with the fix |
| break: a stop-word-only query refused (`T20`) | fixed | 0 rows, as `analyzers.md` now says |
| break: plain `@index(fulltext)` locks GQ 2 out | documented (`full_text_index` listed) | |
| break: HTTP load reports `mode: replace` | unchanged, now listed under the RFC's Wire bullet | |

## Mixed binaries and crashes

Documented as unsupported; the question was whether they fail loudly or
corrupt. Full tables: `results/mixed.md`.

* **Alternating writers** (130 cases, 2,047 NEW steps and 1,139 GQ 2
  operations interleaved on one graph): no oracle failure beyond F3, F5 and
  the tie-rule difference; F2 was not drawn here (it needs a
  NEW index pass, rows, a GQ 2 index pass and rows in that order on a type GQ 2
  can still index) and was found by the concurrent runs.
* **Concurrent processes** on a 20,000-row graph, 60–300 s each, GQ2-REL and
  GQ2-MAIN: GQ 2 writes while NEW reads; NEW writes while GQ 2 reads; both
  write; each with and without maintenance in the writers. 11 runs, 14,732
  acknowledged inserts: none missing from either binary's export, exports
  byte-equal, no panic, no reader saw an insert-only count go down or a
  partial `Doc` count. Conflicts were loud (`changed during preparation`,
  `changed during capture`), but plain text under `--json` (F3). GQ 2 readers
  were refused full-text reads after NEW's concurrent rebuilds, as documented.
  F2 came from these runs.
* **kill -9 of NEW** in `index build` (20 runs), `index rebuild --kind
  full-text` (18), an analyzer apply (18), a plain apply (13) and `optimize`
  (18), on graphs built by GQ2-REL and GQ2-MAIN, at random points of each
  operation's measured duration: every killed operation left the graph
  either unchanged (NEW's answers as before) or complete (an apply killed
  after it published); NEW opened all 87, and both GQ 2 binaries opened every
  one except the 6 where the analyzer apply had published its one-way
  construct (3 killed after publishing, 3 that finished before the kill); the
  content was unchanged, NEW's rerun succeeded, and `cleanup --keep 1`
  afterwards succeeded and changed no answer.

## Coverage

`results/coverage.md` has every count. Of the dimensions the brief names:

* **Schema index mix**, over 3,510 node and 1,696 edge types: plain `@index` on
  a String 3,349 properties; `@key` String with `@index` 830, without 893;
  `@index` on numeric 1,848, Date/DateTime 634, enum 323, Bool 320, vector
  967; body-level `@index(list)` 456; types with several full-text fields
  879; types with no index 269; compound keys 502, no key 872; `@unique` 573,
  `@range` 147, edge `@card` 350; managed Blobs 887. Property-level `@index`
  on a list, `@index(fulltext)` and vector `distance` were drawn as GQ 2
  refusals (228 schemas) and then reached through NEW's applies.
* **GQ 2's full-text index state per lexical type** at the start of Part 2:
  never built 1,206, complete 1,444, rebuilt by GQ 2 1,161, uncovered tail
  454, lagging 808, deletions 270; NEW's rebuild then added 7,407
  observations in `rebuilt_by_new`.
* **NEW step kinds**: write 902, load 592, `index build` 947, full-text
  rebuild 600, `optimize` 490, `cleanup` 299, branch 499, merge of a GQ 2
  branch 122, `upgrade` 150, read-only open 268, read-write open 287, schema
  2,995 (every sub-kind (a)–(i) between 72 and 261 times; see
  `results/coverage.md`).
* **Every NEW step kind and schema sub-kind × every starting index state** was
  drawn (the table in `results/coverage.md`; "never drawn: none").

Not drawn or not covered:

* `upgrade` on a graph GQ2-MAIN built (already at 18, so `already_current`
  only); `upgrade` ran on GQ2-REL graphs only, 150 times.
* Removing a vector `distance` other than `l2`, and removing `@index` from a
  list: the generator never drew those NEW steps (drop-index applies were all
  refused, `removing property constraints … is not supported`).
* A NEW merge whose base was written by both binaries; historical and
  branch reads (the matrix covered those); `@embed`; served clusters,
  stored queries and MCP; S3 and Azure; external Blobs.
* Queries over edges' text and vectors (edges carried text and numeric
  properties but the read set reads node types only, at most three per case).
* The F2 sequence in the alternating generator (see Mixed binaries).
* 100,000-row cases ran only 3–5 NEW steps without GQ 2's rebuild probe.

## Reproducing

```bash
# the three binaries as above, and pylance 13.0.0, pyarrow and snowballstemmer in a venv
export UF_WORK=/path/to/work UF_BIN=/path/to/bins   # $UF_BIN/{rel,main,new}/omnigraph
export UF_P22=/path/to/gq3-checkout/scripts/cross-edition.py RUST_MIN_STACK=16777216
cd upgrade-fuzz/harness
UF_CASE_BUDGET=300 python ufrun.py 1000 2001 --jobs 20 --budget 575     # Part 1+2, resumable
UF_PROBE_REBUILD=0 UF_CASE_BUDGET=470 python ufrun.py 9000 9024 --stress --jobs 8 --budget 590
UF_JOBS=12 python ufmixed.py alternate 5000 5030                         # Part 3
python ufmixed.py concurrent both-write 300 rel maint                    # gq2w-newr | neww-gq2r | both-write
python ufmixed.py kill index_build 20 30 rel                             # index_build | fts_rebuild | optimize | schema_apply | schema_apply_plain
python ufshrink.py 1168 fresh_graph_differs near --out ../repro/x.json   # shrink a finding
python ufanalyze.py                                                      # results/*.md
REL=... MAIN=... NEW=... bash ../repro/run-all.sh > ../repro/output.txt  # every repro
```

Each `ufrun.py` invocation stops starting cases near its budget and resumes
from the seeds already in `cases.jsonl`, so a range splits over invocations
of under 10 minutes. A case's working directory is kept only when it found
something.
