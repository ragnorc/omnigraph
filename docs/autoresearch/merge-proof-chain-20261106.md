# Autoresearch: the merge's pure-insert proof, 2026-11-06

Cost chosen: the storage requests a branch merge makes to prove that the
source only inserted rows into a table. This proof walks the source's
commits one at a time, so its cost grows with the number of commits on the
branch, not with the rows the branch changed. Issue #1222 reports a hosted
merge of a 313-commit branch that spent about 0.3 s per commit here, about
85 s of a 300 s merge.

- Branch: `autoresearch/merge-proof-chain-20261106` on the fork `ragnorc/omnigraph`
- Base commit: `f5a0dac9294c2d43b1886824e322ec6e01fae12c` (main, 2026-11-06)
- Run: scheduled occurrence `trigger-autoresearch-modernrelay-omnigraph-time@2026-11-06T09:00:00Z`,
  WorkflowRun `run-wf-autoresearch-modernrelay-omnigraph-20261106T0900Z-a31c7c`
- Box: Modal dev box `agent-devbox`, Claude Opus 5.5 (`claude-opus-5-5`)

## Result

One hypothesis kept (H1). The proof now reads each source commit with one
request instead of five.

| Merge of a source with N one-row insert commits | N = 2 | N = 8 | N = 32 | N = 128 |
|---|---:|---:|---:|---:|
| Requests, base (GQT `--measure`) | 56 | 92 | 248 | 1,016 |
| Requests, H1 | 52 | 64 | 114 | 306 |
| Modeled time, base (`s3-like`, ms) | 859 | 1,387 | 3,714 | 15,637 |
| Modeled time, H1 (ms) | 789 | 897 | 1,365 | 3,112 |
| Bytes read, base | 102,010 | 330,331 | 1,270,432 | 6,018,409 |
| Bytes read, H1 | 98,724 | 310,170 | 1,209,737 | 5,585,235 |

Per extra source commit: 7.6 requests and 117 ms before, 2.0 requests and
18 ms after (−84% modeled time). One of the two remaining requests is the
proof's single manifest read. The other is the data file read that copies
the commit's row into the target, which follows the rows, not the commits.

The DST storage-cost golden shows the same thing in its own counter (HEADs
count as `l.get`): `MergeSourceHistory2` l.get 54 → 50 and
`MergeSourceHistory32` l.get 248 → 112. No other line of the golden moved.

The cost still grows with the commit count: one dependent request per
commit. The redesign proposal below removes that.

## Lessons taken from earlier runs

I read the summaries and reports of the four earlier autoresearch runs
(warm read, branch ops, node delete, vectorized traversal) and the summary
and session trace of the failed run of 2026-11-05.

- The failed run closed itself about five minutes in, after a call-count
  instrument came back flat. It said to start from a counter that moves. This
  run started from a cost known to grow (issue #1222's production log) and
  measured it at four sizes before changing code.
- Name the counter. The score here is GQT `--measure` requests and its
  `s3-like` modeled time; the DST golden is the CI contract. They count
  slightly differently (GQT also counts failed HEADs).
- Pin `merge_lineage = on` in measured cases, as release builds do (the
  branch-ops run found debug builds default to `verify`, which doubles merge
  reads). The DST scenario sets it.
- Keep work files under `/src`; only `/src` and `/persist` survive a restart.
- Push to the fork `ragnorc/omnigraph`; the upstream ruleset refuses branches.
- Five engine integration tests overflow the stack in debug builds on this
  box. I ran them on the base commit too: they fail there the same way, and
  pass on both with `RUST_MIN_STACK=16777216`.
- The merge-path proposals of the branch-ops run were not retried. Its H3 and
  H4 (#1229, merged) made the merge read only the changed fragments; this run
  works on the separate proof that runs before those reads.

## What the proof does, and where the cost came from

`crates/omnigraph/src/exec/merge.rs`, `classify_adopt` →
`try_proven_pure_insert_adopt` → `try_proven_pure_insert_history` →
`proven_chain_fragments`. For a table only the source changed, the merge
tries to prove that every commit in (base, source] is an exact-id fenced
insert (`certified_insert_absence_rows`, `table_store.rs`) or an index-only
commit. If the proof holds, the merge adopts the source's new fragments
without a row diff.

Each graph write to a table is a Lance detached commit. Its manifest names
its transaction file `{read_version}-{uuid}.txn`, and the next older commit
is `read_version`. The walk follows that link back to the base pin.

Before H1, each step did this (DST request log, `dst_merge_source_history_cost_paths`
with `MERGE_SOURCE_VERBOSE=1`):

1. `Dataset::read_transaction` on the current link. Lance resolves a
   detached manifest location without a size, so it opens the file with a
   HEAD, then reads the inline transaction at `transaction_section` in two
   range reads (the transaction is about 33 KB, mostly the insert's
   key-existence filter).
2. `Dataset::checkout_version(read_version)` for the parent: a HEAD for the
   size, then a tail read of the parent's manifest.

That is five dependent requests per commit with the 4 KiB local block size
the DST memory store uses, and four with Lance's 64 KiB cloud block size.
The same manifest file is read twice: once to open the version and once for
the transaction. The walk needs neither the opened dataset nor the second
read. It needs the recorded identity and the transaction.

Expected growth: linear in the commit count, flat in rows per commit and in
target history. Measured: linear in commit count (table above). Target
history is already covered flat by `MergeTargetHistory2/32`.

## Hypotheses

| # | Change | Counters | Gates | Outcome |
|---|---|---|---|---|
| H1 | Read a detached link with one whole-object GET of its manifest and decode the recorded identity and the inline transaction from those bytes (`DetachedChainLink::read`, `omnigraph-core/src/staging.rs`). A linear link, a manifest with no inline transaction, another version or branch, or a failed read takes Lance's route as before. | 32 commits: 248 → 114 requests, 3,714 → 1,365 ms modeled; 128 commits: 1,016 → 306, 15,637 → 3,112 ms. HEADs in the merge flat (4 at every size). | All passed; see Gates. | Kept (`cf427404`) |

H1 decodes the file with Lance's own pieces: `lance_table::format::pb`,
`MAGIC`, `lance_io::utils::read_message_from_buf` and
`Transaction::try_from`. The framing it relies on (a 16-byte footer whose
first 8 bytes are the manifest offset; a length-prefixed manifest ending at
the footer; the length-prefixed transaction at `transaction_section`, before
the manifest) is what `lance_table::io::manifest::read_manifest` and
`Dataset::read_transaction` use in Lance 13.0.0. A new surface guard,
`lance_surface_guards::detached_manifest_file_holds_its_transaction_inline`,
checks that the reader returns the transaction and identity Lance's own
route returns, so a Lance bump that changes the framing goes red. Lance
13.0.0 inlines every transaction up to 20 MiB (`MAX_INLINE_TRANSACTION_BYTES`),
so graph writes always take the one-GET route.

Two checks keep the reader as strict as Lance: the decoded manifest must
name the requested version and the dataset's branch (as
`Dataset::read_version_transaction` checks), and every offset is bounds
checked before slicing (in-source tests `length_prefixed_message_*`).

No other hypothesis was built. Ideas considered and not built:

- Use `Dataset::read_version_transaction` instead of the checkout. It skips
  building a dataset but still reads the manifest twice with a HEAD each
  time. No request saved.
- List `_versions/` once to learn every manifest's size and drop the HEADs.
  The listing holds every detached version of every branch of the table, so
  it grows with all history. Rejected.
- Read the links concurrently. The next version is known only from the
  transaction just read, so the walk cannot be parallel without another
  source of the chain. See the proposal.

## Redesign proposal: a proof that does not walk commits

H1 leaves one dependent request per source commit. Two directions remove
the walk. Both are proposals; neither was prototyped, and the numbers below
are estimates from the measured counts, not measurements.

1. **Check the result instead of every commit.** The walk proves that
   (base, source] only inserted rows whose ids were absent. The same fact
   can be checked on the two pins: every base fragment is still in the
   source with the same data and deletion files (so no base row changed or
   went away), the new fragments carry no deletion file (so no inserted row
   was later changed), and the new rows' ids are unique and absent from the
   base (an id-index lookup on the base, plus the uncovered tail). The
   adopt already reads the new fragments to copy their rows, so the id
   column is nearly free. Cost would follow the inserted rows and the index,
   not the commit count: for the 128-commit case, about 128 fewer dependent
   requests, so roughly 2.3 s less modeled time. The risk is correctness: it
   replaces Lance's per-commit absence certificate with an engine-side
   check, so it needs the merge truth-table tests and `merge_lineage = verify`
   comparisons before it could be trusted.
2. **Know the chain without reading it.** Iceberg keeps every snapshot's
   `parent-snapshot-id` in the table metadata file, so walking ancestry
   costs no extra reads, and Delta names commits with contiguous numbers so
   a reader can fetch them in parallel. Omnigraph's `__manifest` already
   records each graph commit's table pin, so the chain is known from graph
   lineage; the walker could fetch the links concurrently and then check the
   `read_version` links in memory. That keeps the certificate but needs a
   cheap read of the pins of every commit in (base, source], which the merge
   does not have today.

Writers can avoid the cost now: one `load` per batch instead of many one-row
`mutate` calls gives one commit per table, so the proof is one step.

## Timing

Wall clock on this box (gVisor, 2 cores) was not used. The timing evidence is
the GQT `s3-like` model: 17 ms per request plus bytes at 50 MiB/s, with
requests the engine issues together overlapping. It is a model, not a
measurement on S3. The qualified benchmark environment was not available.
The hosted figure in #1222 (about 0.3 s per commit) fits a chain of four or
five dependent S3 requests per commit; I did not measure the hosted server.

## Gates

On `cf427404` unless noted:

- `lance_surface_guards`: all pass, including the new guard.
- Engine merge, branch, proven-insert and adopt tests plus `omnigraph-core`
  (`cargo dev-nextest`, 479 tests): 474 pass. The 5 failures are debug-build
  stack overflows that fail the same way on the base commit and pass on both
  with a 16 MiB stack.
- DST storage-cost golden: regenerated, replays identically; only the two new
  rows changed between the instrument commit and H1.
- GQT corpus (`cargo test -p omnigraph-gqt --locked`): 460 cases pass, plus
  the runner's own tests.
- `cargo fmt --all --check`; `cargo clippy` on the default and failpoints
  workspace graphs and on `omnigraph-gqt`: clean.
- Not run here: the canonical full workspace test, the full DST suite,
  RustFS/S3 and Azure suites, and `typos` (not installed in the box). CI on
  the pull request runs them.

## What could not be verified

- Real S3 or RustFS request counts and latency.
- A table on a legacy (pre-v11) table fork, whose detached commits live under
  a Lance branch. The reader builds the path from the dataset's branch
  location, as Lance does, and falls back to Lance's route if the read fails;
  I did not build such a table.
- The hosted graph's shape (106k rows, 313 commits): the cases here have a
  few rows and up to 128 commits.
- Issue #1222's cause 1 (a Blob table walked twice) is untouched.

## Sources

Used:

- Issue #1222 and its reproduction comment (request shapes per commit).
- Lance 13.0.0 source: `Dataset::read_transaction`, `read_version_transaction`,
  `checkout_version`, `lance_table::io::manifest::{read_manifest, do_write_manifest}`,
  `io::commit::{default_resolve_version, MAX_INLINE_TRANSACTION_BYTES}`,
  `lance_io::utils::read_message`.
- Lance format pages: [table layout](https://lance.org/format/table/layout/),
  [transactions](https://lance.org/format/table/transaction/) (inline
  transaction sections; the published page names `transaction_section`
  field 23 and deprecates field 21, while the 13.0.0 proto still uses 21, so
  the guard will flag the change at a bump),
  [versioning](https://lance.org/format/table/versioning/).

Added this run:

- Apache Iceberg table spec, `snapshots[].parent-snapshot-id` and `snapshot-log`:
  https://github.com/apache/iceberg/blob/main/format/spec.md
- Delta Lake protocol, contiguous commit versions and `_last_checkpoint`:
  https://github.com/delta-io/delta/blob/master/PROTOCOL.md

## Instruments

- `crates/omnigraph-dst/tests/scenarios.rs`: `MergeSourceHistoryCost` (golden
  rows `MergeSourceHistory2`/`32`) and the ignored
  `dst_merge_source_history_cost_paths` (calls by realm;
  `MERGE_SOURCE_COMMITS`, `MERGE_SOURCE_VERBOSE`).
- `docs/autoresearch/merge-proof-chain-20261106/cases/`: the four GQT
  measurement cases, run with
  `omnigraph-gqt <case> --measure --model s3-like --artifacts <dir>`.
