# Autoresearch: the manifest HEAD of every pinned table open

Run `run-wf-autoresearch-modernrelay-omnigraph-20261118T0900Z-42c228`, executor
agent-devbox, model Claude Opus 5.5. Base: ModernRelay/omnigraph main
`d20eba74b7218ef235bc8effcdd70aa640bc9b4e`. Branch:
`ragnorc/omnigraph@autoresearch/pinned-open-manifest-20261118`. Issue #1250.

## Result

One hypothesis was kept, in its second form (H1b). A table open that keeps no
read cache now reads the manifest at the path, size and ETag that the pin's
registration recorded. Such an open no longer sends a HEAD to learn the size.
The opens that keep no read cache are the table opens of a snapshot that holds
no read caches: reads pinned to a commit, and the merge-base and other
historical snapshots that a merge opens. The first form (H1) used the recorded
location on every pinned open. A workspace test caught that it let a cached
manifest answer for a version that `cleanup` had reclaimed, so it was dropped.

| Operation (3-table graph) | Requests on main | Requests now | Model time on main (s3-like) | Model time now |
|---|---:|---:|---:|---:|
| Read pinned to a commit (DST) | 25 | 16 | 403 ms | 250 ms |
| Same read, repeated | 25 | 16 | 403 ms | 250 ms |
| Same read, against RustFS (real S3 protocol) | 25 | 17 | not timed | not timed |
| Cold `branch merge` of a named branch | 115 | 102 | 1,815 ms | 1,594 ms |
| Cold read, insert or `branch diff` on main | unchanged | unchanged | unchanged | unchanged |

Bytes read are unchanged in every row. Results are unchanged.

The DST storage-cost golden moves 13 `l.get` lines, and all of them go down.
Examples: `MergeTargetHistory2` and `32` 91 to 82, `MergeBackAfterThreeWay`
103 to 94, `RemovalOverwriteProbed` 13 to 11, `Optimize` 65 to 63, the
whole-run `_verify` 1,387 to 1,355. No other verb moves. With the recorded
location switched off, the regenerated golden is byte-identical to main's, so
every moved line comes from this change. Two new labels pin a read at a
commit: `ReadPinnedHop` and `ReadPinnedHopRepeat` cost 30 on main and 22 now,
at 2 and at 32 commits of history.

## Why this cost

The workflow prefers a cost that users pay on every request. A table open at a
pinned version happens on every read pinned to a commit (Studio's history view,
a client that pins its reads, `--snapshot`), on every cold open after a restart
or a handle-cache eviction, and on every write, merge, diff, change-feed page
and maintenance step that opens a version it does not hold. On an object store
each of those opens paid two dependent round trips for the manifest instead of
one. Issue #1250 described the mechanism by code reading and said that the
request count was not measured. This run measured it.

## Map

1. A pin's registration row (`TableVersionMetadata` in
   `crates/omnigraph-core/src/metadata.rs`) records `manifest_path`,
   `manifest_size` and `e_tag`. A writer takes them from the
   `ManifestLocation` that Lance returns when it writes the manifest
   (`TableVersionMetadata::from_dataset`).
2. Two openers read a pin: `TableHandleCache::get_or_open`
   (`crates/omnigraph-core/src/handle_cache.rs`) for live branch reads and
   writes, and `open_dataset_entry` (`crates/omnigraph-catalog/src/lib.rs`)
   for reads without read caches (pinned reads, write preludes). Both call
   `instrumentation::open_pinned_dataset`. Before this change they passed it
   only the staged version, the transaction uuid and the last linear version.
3. `dataset_builder` installs `lance_clone::configured_commit_handler`. Its
   `resolve_version_location` answers only a clone's source version and
   otherwise calls Lance.
4. Lance 13 `default_resolve_version` (`lance-table` `io/commit.rs`) returns a
   detached version with `size: None` and `e_tag: None`, and it sends no
   request. Lance main still does this.
5. `Dataset::get_manifest` (`lance` `dataset.rs`) skips the session metadata
   cache when `size` is `None`. `Dataset::load_manifest` calls
   `ObjectStore::open`, which sends a HEAD on an object store, before it reads
   the last block. A later read of the transaction or index section through
   the same location (`read_transaction_from_storage`) asks again. The step-10
   log of the measurement shows both HEADs.

On a local filesystem Lance reads manifests through direct paths, so the HEAD
is a `stat` call and no counter sees it. This is why `warm_read_cost.rs` and
the other local cost owners never showed it. The local tests still run the new
path, including the repeat after a failure.

## Expected and measured growth

Expected: the saved requests equal the number of manifest reads through an
uncached pinned open, one per open. They do not grow with history, rows or
fragments. Measured: the pinned read saves exactly its 9 HEADs (9 opens of 3
manifests), and its repeat saves the same 9, because nothing is cached across
reads. The new golden rows are equal at 2 and 32 commits, on main and now. The
named-branch merge saves 13 of its 115 requests, all in the snapshots it opens
without read caches.

## Hypotheses

| Id | Change | Counter | Gates | Outcome |
|---|---|---|---|---|
| H1a | The handler of every pinned open returns the recorded location when the recorded path equals Lance's path | no change. The row spells the path from the root URI (`shared-memory://gqt-dst/case/...`), and Lance spells it inside the store (`case/...`) | not run | Refined into H1 |
| H1 | The same, matching the recorded path's suffix at a path-component boundary, on every pinned open (handle cache, `open_at_entry`, uncached opens) | pinned read 25 to 16, cold read 8 to 6, cold merge 115 to 100, golden 47 lines down | workspace tests: 3 new failures (`historical_read_of_reclaimed_version_is_typed`, `system_column_upgrade_history_survives_unrelated_reclaimed_tables`, `commit_changes_are_exact_ordered_and_bounded`) | Dropped, commit `72d2ca58` |
| H1b | The recorded location only on opens that own a session which caches nothing, and a failed recorded open repeated through Lance's resolution | pinned read 25 to 16 (RustFS 25 to 17), named-branch merge 115 to 102, golden 13 lines down, none up | listed below | Kept, commits `95bfc47b` and `4e923df5` |
| H2 | Share one statement-scoped handle cache across the opens of one pinned read | 9 manifest GETs for 3 tables would become 3 | not built | Proposal, see below |

Why H1 failed. Lance's `Dataset::get_manifest` caches a manifest whose size it
knows in the open's session, keyed by version and ETag. The graph's data
session lives as long as the handle, and Lance gives no public call that
evicts an entry. After `cleanup --keep 1` reclaimed `Person` history, the
change feed reopened the reclaimed version through the data session
(`TableStore::open_at_entry`), Lance served the cached manifest, and the feed
returned the old commit's rows instead of the typed `ChangeFeedGap`. On main,
detached manifests never reach that cache, because Lance does not know their
size. The second failure was the error class: at a known size Lance reads a
missing manifest with a ranged read and reports `lance::Error::IO`, not the
`DatasetNotFound` that `open_dataset` turns into `HistoricalVersionReclaimed`.
This holds on every store, not only on local files.

How H1b holds. Only `open_pinned_dataset_uncached` passes the recorded
location, and it builds its own statement session with zero cache capacity, so
no manifest outlives the open. Its one caller is `open_dataset_entry_uncached`,
which `Snapshot::open_lance_dataset` uses when the snapshot holds no read
caches. The change feed's re-open of a named-branch table also uses a
zero-cache session, but it stays on Lance's resolution (`4e923df5`): its
ETag witness (`TableVersionMetadata::witnesses`) must compare the store's token
with the recorded one, and through the recorded location both are the same
value. Opens on the data session (the handle cache and
`TableStore::open_at_entry`) resolve through Lance as on main. If an open
through the recorded location fails, `open_dataset` repeats it through Lance's
resolution and returns that result, so every failure keeps the class it has on
main. The repeat costs one extra open, and only on a failure. A wrong recorded
size is caught by Lance's trailing-magic check. A row without both size and
ETag, another version that the RFC 0067 twin rule opens, another dataset or
branch, or a V1 path resolves through Lance.

## Proposals

H2, one open per table in a pinned read. A read pinned to a commit opens
`Person` 4 times, `Company` 3 times and `WorksAt` 2 times (step 5 of
`pinned_open.gqt`). `Snapshot::open_lance_dataset` builds a new zero-cache
session for each open, and nothing holds the result. Most of the opens happen
while the planner reads index facts (`query_index_facts.pre_load`), before the
statement's memory pool exists. A statement-scoped `SnapshotReadCaches`,
admitted from the query pool as `loader::read_reuse::LoadReadReuse` does for
loads, would make it 3 manifest GETs instead of 9 (16 requests to about 10,
250 ms to about 150 ms in the model). Because the caches end with the
statement, the reclaimed-history problem of H1 would not arise. The admission
order (planning before the pool) is the open design question. This covers part
of #1052.

H3, the HEAD on data-session opens. Cold reads, writes and merges on main
still pay the HEAD (8 to 6 and 115 to 100 were measured under H1). Keeping that
saving needs either a Lance call that evicts a dataset's manifests from a
session (then `cleanup` evicts what it reclaims in this process, and the
cross-process case stays as it is for the handle cache, #958), or a resolver
that takes a size without caching. Both are Lance changes. The cold merge also
reads the same two `Person` manifests about 7 times (four reads of the first
4 KiB, three of the last block) across opens that share no cache.

## Timing evidence

There is no wall-clock claim. The model times above are the GQT `s3-like`
model (17 ms per request plus bytes at 50 MiB/s). The RustFS run gives request
counts on the real S3 protocol, not time. The box is gVisor with 2 cores, and
no qualified benchmark environment was used.

## Gates

All on the final tree (`1813feb6`, the same code as the pull request):

- `cargo fmt --all --check`: pass.
- `cargo dev-test --no-fail-fast` (canonical workspace graph with failpoints):
  5,437 passed, 10 failed. The 10 are the environmental failures that earlier
  runs recorded on this box (root ignores `chmod 000`, symlink and
  machine-identity checks in the benchmark crate, one CLI managed-lifecycle
  symlink test). None reaches the changed code.
- `cargo test -p omnigraph-gqt --locked` (full corpus): 582 passed.
- `crates/omnigraph-dst`: `cargo test --locked`, including the regenerated
  golden: pass.
- Clippy: workspace default features, workspace failpoints superset, and GQT:
  pass (the first run caught `unnecessary_literal_unwrap` in the new test).
- RustFS 1.0.0-beta.12 (S3 protocol): `s3_storage::` and `write_cost_s3::`, 24
  passed.

The first full workspace run, on H1, failed three tests that this report
describes under H1. That is how H1 was dropped.

Pull request: https://github.com/ModernRelay/omnigraph/pull/1449

## Lessons taken from past runs

- Start from a byte or request counter, not the call-count golden alone (the
  2026-11-05 run stopped after it found nothing that moved). This run started
  from GQT `--measure` request logs.
- Read Lance's source before trusting an assumption. The 2026-11-12 review
  found an eviction rule that matched nothing because the run assumed a ref
  layout. This run read `default_resolve_version`, `get_manifest`,
  `load_manifest` and `read_transaction_from_storage` in the pinned 13.0.0
  source and on Lance main.
- Do not add a second reader of Lance's private layout (the review of #1285).
  This change uses only the public `CommitHandler::resolve_version_location`
  and `ManifestLocation`.
- Check that a rule is width-safe before shipping it (B1 of the #1318
  review). The handler adds no request on any path. Its worst case is one
  wasted read for a wrong size.
- Do not edit sources while a gate compiles, and do not `pkill -f` a pattern
  that matches the agent's own shell. This run stopped its gates by PID.
- New from this run: a cost change that makes Lance cache something must be
  checked against `cleanup`. Run the whole workspace graph before the first
  push, because the focused cost owners did not catch the reclaimed-history
  failures.
- Keep a `.performance.md` note to 200 characters or fewer.
- Prove a counter against main with the change switched off in the same tree.
  The 2026-11-09 run's reverse patches went stale.

## Sources

Used:

- Lance 13.0.0 source: `lance-table` `io/commit.rs` (`default_resolve_version`,
  `ManifestLocation`), `lance` `dataset.rs` (`get_manifest`, `load_manifest`,
  `read_transaction_from_storage`), `lance` `dataset/builder.rs`
  (`load_by_uri`). Lance main, checked on 2026-11-18: detached versions still
  resolve with no size.
- Issue #1250 and its recommended fix. Issue #1052 for the pinned-read path.

Added this run (same mechanism in other table formats):

- Apache Iceberg PR #5207, "Add length arg to FileIO.newInputFile": the
  manifest list stores each manifest's length, so planning skips the length
  round trip.
- delta-rs PR #1630, "pass known file sizes to filesystem": the sizes come
  from the Delta log, which removes the HEAD on open.
- DuckDB PR #17652: the file handle starts from `OpenFileInfo` with its
  `file_size`, which prevents extra HEAD requests.

All three keep the size in the table's own metadata, written when the object
is written, and they treat it as a hint that the reader can check. H1 does
the same with the size that the `__manifest` registration already holds.

## Not verified

- Wall-clock time on real S3, and Azure.
- The engine opener on a `shared-memory` store outside the DST build: it
  refuses that scheme, so the in-source test drives Lance's builder with the
  same handler, and the DST golden covers the engine opener.
- Rows written by releases before the size and ETag were recorded: they have
  no recorded manifest and keep Lance's path. The in-source test covers this
  case, and no genuine old graph was opened.
- `blob.rs` has one more zero-cache open of a named-branch pin
  (`open_dataset_entry` on a fresh statement session). It keeps Lance's
  resolution in this change.

## Instruments

The two GQT cases are in `pinned-open-manifest-20261118-cases/`, outside the
corpus. Run each with the runner built at main and at this branch:

```bash
cargo build -p omnigraph-gqt --bin omnigraph-gqt --locked
target/debug/omnigraph-gqt docs/autoresearch/pinned-open-manifest-20261118-cases/pinned_open.gqt --measure --artifacts /tmp/out
target/debug/omnigraph-gqt docs/autoresearch/pinned-open-manifest-20261118-cases/cold_ops.gqt --measure --model s3-like --artifacts /tmp/out
```

The per-request log (verb, path, range, phase) is in the `measurements` field
of the report JSON. The RustFS numbers come from
`write_cost_s3::pinned_read_sends_no_manifest_head_on_s3_issue_1250`, run with
the environment of CI's `rustfs_integration` job against RustFS
1.0.0-beta.12 (the release binary, checksum checked against its
`SHA256SUMS`).
