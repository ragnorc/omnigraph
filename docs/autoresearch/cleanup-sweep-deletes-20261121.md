# Autoresearch: the requests of a `cleanup` run

Run `run-wf-autoresearch-modernrelay-omnigraph-20261121T0900Z-18b3cb`, executor
agent-devbox, model Claude Opus 5.5. Base: ModernRelay/omnigraph main
`f36d3e92ec314703df96f08041dd957753724897`. Branch:
`ragnorc/omnigraph@autoresearch/cleanup-sweep-deletes-20261121`. Issue #1252
(step 2), with a planning cost found on the way.

## Result

Two hypotheses were kept.

- H1: the sweep sends a table's freed files through one `delete_stream` call.
  S3 receives one `DeleteObjects` request per 1,000 files instead of one
  request per file. The published manifests stay single deletes, oldest first.
- H2: the collector's plan opens each traced detached manifest once. Before,
  the chain walk opened every published version to find its predecessor, and
  the root loop and the sweep opened the same versions again for their paths.
  On an object store each open is a HEAD and a GET.

`cleanup --keep 1` after N one-row inserts on `main` (test schema, 3 node and
edge tables plus `__manifest`), measured on RustFS 1.0.0-beta.12 through a
counting proxy. Every number is a count of HTTP requests in the cleanup window,
from the plain proxy run; the wall column is from a second run through a proxy
that adds 20 ms per request. The test's own counter (`DeleteRequestCounter` on
the table and `__manifest` stores) gives the same delete and GET counts and 8
HEADs and 2 LISTs fewer in every row, from stores outside its probes.

| N commits | Version | Objects deleted | Delete requests | HEAD | GET | LIST | Wall, 20 ms per request |
|---:|---|---:|---:|---:|---:|---:|---:|
| 4 | main | 12 | 12 | 36 | 33 | 79 | 3,478 ms |
| 4 | H1 | 12 | 8 | 36 | 33 | 79 | 3,333 ms |
| 4 | H1 + H2 | 12 | 8 | 26 | 23 | 79 | 2,897 ms |
| 16 | main | 36 | 36 | 60 | 57 | 79 | 5,138 ms |
| 16 | H1 | 36 | 20 | 60 | 57 | 79 | 4,869 ms |
| 16 | H1 + H2 | 36 | 20 | 38 | 35 | 79 | 3,844 ms |

Per extra removed commit, main sends 2 delete requests, 2 HEADs and 2 GETs.
The change sends 1 of each. The remaining delete request per commit is the
manifest, which must go alone (see "H3, not built"). Without injected delay the
whole run takes 579 ms on main and 516 ms with both changes; the box is gVisor
with 2 cores, so wall time is indicative only.

On a local file store the same test shows the same delete calls: 44 single
deletes on main at 16 commits, and 26 calls now (22 manifests, 4 streams for
22 files). The local store reads manifests through direct paths that the
object-store wrapper does not see, so H2 shows only on an object store.

Results are unchanged: every cleanup, merge-cleanup, branching, failpoint and
collector test passes, and the gates below ran on the final tree.

## Why this cost

`cleanup` is the one operation that removes history, and its cost grows with
the history it removes. That growth is expected. The waste was a constant
factor on top of it: each freed file paid its own round trip, one after the
other, and each removed version was read twice. On S3, a cleanup after a busy
week of small commits removes thousands of files, so the sweep alone ran for
minutes at one request per file. The contract's cost column for `reclaim` says
"history removed"; this run keeps that and removes the factor.

## Map

1. `optimize::cleanup_all_datasets` (`crates/omnigraph/src/db/omnigraph/optimize.rs`)
   plans with `collector::plan_collection`, then calls `collector::sweep_table`
   per table location, `maint_concurrency()` tables at a time.
2. `plan_collection` calls `trace_table` per location
   (`crates/omnigraph/src/db/omnigraph/collector.rs`). `trace_table` lists
   `_versions/` (`list_manifests`, which records each detached manifest's
   size), then:
   - `discover_published` walks each registration's staged version back
     through its chain links, opening every version with `open_at` to read its
     transaction's `read_version`;
   - the root loop opens every root for its paths (`referenced_paths`) and the
     bases it borrows (`borrowed_origins`);
   - `judge_outside` opens frozen linear versions and unpublished manifests;
   - `plan_sweep` opens every sweep candidate (the walked versions that are not
     roots) for its paths;
   - `plan_orphans` lists the location's objects and records the size of each
     file the sweep will delete (`listed_sizes`, PR #1266).
3. `sweep_table` deletes the freed files (`delete_paths`), then the published
   manifests oldest first with a failpoint between each, then the dead
   stagings' files and manifests, then the orphans.
4. `delete_paths` called `delete_object` per file, which called
   `lance::io::ObjectStore::delete`, that is `ObjectStoreExt::delete`: a
   `delete_stream` of one path. object_store 0.14.2's S3 store sends each
   such call as a `POST ?delete` with one key (`aws/mod.rs`, `delete_stream`).
5. An open of a detached version in Lance 13 resolves the manifest with no
   size, so it sends a HEAD before the GET (found by the 2026-11-18 run).
   The walk and the sweep each paid that for every removed version.

## Expected against measured growth

| Cost | Should grow with | Main | Now |
|---|---|---|---|
| Delete requests | Manifests removed (they must go one by one, in order), plus one per 1,000 files | Manifests + files | Manifests + one stream per file group |
| Manifest HEAD and GET in planning | Versions traced, once each | Twice per removed version | Once per traced version |
| LIST | Tables and live branches, not history | 79, flat | 79, flat |

## Hypotheses

| # | Change | Counter before | Counter after | Gates | Outcome |
|---|---|---|---|---|---|
| H1 | Freed files of a group go through one `delete_stream` on the table store's inner object store; an absent file counts no bytes; manifests stay single deletes | 16 commits: 36 delete requests (RustFS), 44 delete calls (local) | 20, 26 | All below | Kept (`9b45b880`) |
| H2 | Resolve roots before the chain walk; the walk reads each version's paths at the same open (as a root or a sweep candidate); the root loop and the sweep skip traced versions | 16 commits: 60 HEAD, 57 GET (RustFS) | 38 HEAD, 35 GET | All below | Kept (`165cfde1`) |
| H3 | Delete the published manifests in one stream too | Not built | | | Dropped before building, see below |

H1 detail. The stream goes to `lance::io::ObjectStore::inner`, the same object
store `ObjectStore::delete` uses, so every wrapper Omnigraph installs still sees
each path (Lance's tracker, request accounting, the DST fault wrapper, which
rolls per object). The server's lifetime-scoped store
(`omnigraph-storage/src/scoped.rs`) splits any delete stream into single
deletes, ten at a time, so on that path the gain is concurrency, not fewer
requests. Lance's own `ObjectStore::remove_stream` was not used: it deletes
one object per request, `io_parallelism()` at a time, and its comment says
that batching needs a different signature.

H2 detail. The plan's sets are unchanged: the same versions are published,
rooted and swept, in the same chain order, and `referenced_paths`,
`marked_paths` and `borrowed_origins` get the same members. Only the number of
opens changes. The collector cost test bounded index reads by opens; that
held only because links were opened twice. A root reads its index section
twice (paths and borrowed bases), so the bound is now opens plus roots, and the
`CollectorCost` field docs say so.

H3, not built. The manifests are deleted oldest first, links before tips, with
a failpoint between each (`CLEANUP_SWEEP_BETWEEN_MANIFESTS`), so that a pass
that stops leaves a chain the next pass can walk and plan again
(`failpoints.rs::collector_pass_interrupted_mid_sweep_counts_nothing_and_retries_whole`).
One `DeleteObjects` request has no order, so a stopped pass could leave a tip
whose link is gone, a state this run did not show the next pass can plan.
Batching them needs that proof. That proof is the proposal below, not a change.

## Proposals

- Manifest deletes in bulk. Show that `discover_published` stops cleanly at an
  absent link and that a tip whose link is gone is still swept, then delete each
  table's removed manifests in one stream. That would take the 16-commit case
  from 20 delete requests to about 4.
- The HEAD per traced version. Each remaining open sends a HEAD because Lance
  13 resolves a detached version with no size. The `_versions/` listing has
  just given every size (`plan.manifest_bytes`). PR #1449 (open) teaches the
  pinned opener to use a recorded size on a cache-free session; the collector
  could open through the same route, which would take the 16-commit case from
  38 HEADs to about 16.

## Gates

All on the final tree (`99bce61f` plus the changelog note), with `RUST_MIN_STACK=16777216`:

- `cargo fmt --all --check`: clean.
- `cargo dev-test --no-fail-fast`: 5,496 passed, 10 failed. The 10 are the
  box's known environmental failures, the same set the earlier runs saw on
  main: `unreadable_legacy_object_asks_for_a_rerun` (runs as root), three
  `archive::tests::*symlink*`, `machine::tests::captures_a_complete_identity_on_supported_hosts`,
  `projection::tests::bounded_reads_reject_special_files_and_symlinked_ancestors_without_blocking`,
  two `real_graph_run::tests`, `runner::tests::warm_and_post_reopen_start_from_identical_frozen_bytes_and_verify_exact_content`
  and `cli_cluster::managed_lifecycle_capture_and_retry_records_refuse_symlinks_without_reading_targets`.
  `forbidden_apis` and `failpoints` pass.
- `cargo test -p omnigraph-gqt --locked`: 594 passed.
- DST suite (`crates/omnigraph-dst`, `cargo test --locked`): 113 passed and
  the golden, after the regeneration below, passes.
- Clippy, failpoints graph, default graph and GQT graph, `-D warnings -W
  clippy::dbg_macro`: clean.
- RustFS 1.0.0-beta.12, `s3_storage::` and `write_cost_s3::`: 24 passed,
  including the new S3 cost test.
- `scripts/check-docs.py` and `scripts/check-agents-md.sh`: OK.

DST storage-cost golden: `Cleanup l.get` 138 to 106, `_audit l.get` 630 to
602, `_verify l.get` 1,387 to 1,339. No other line moves; `Cleanup l.delete`
stays 20 because the DST fault wrapper counts each object of a stream. With
H2 reverted (H1 only) the golden equals main's, so all three lines come from
H2.

## Timing

Wall times are from the box (gVisor, 2 cores) against a local RustFS, with a
Python proxy that adds 20 ms before forwarding each request. They show the
direction, not a figure for AWS. No qualified benchmark environment was used.

## Lessons from past runs

From the traces of the 11-05, 11-06, 11-09, 11-12 and 11-18 runs:

- Run gates with `RUST_MIN_STACK=16777216`; five engine tests overflow without
  it. Applied.
- Use the warm `cargo dev-*` aliases. A `-p omnigraph-engine --features
  failpoints` build recompiled Lance for a different feature set; I stopped it
  and used `cargo dev-test --test integration -- <filter>`.
- Never `pkill -f` a pattern that the calling shell's command line contains. I
  did it once this run and it killed the command itself; the lesson was in the
  list and I still tripped on it.
- Start from a counter that moves. The DST golden counts deletes per object, so
  it cannot show H1; the new counter splits single deletes from streams, and
  the RustFS proxy counts real HTTP requests.
- Run the whole workspace test before the first push (11-18 found three
  failures only there). Done.
- Read Lance and object_store source before trusting an assumption (11-12
  review). Doing so showed that `delete` is itself a one-path stream in
  object_store 0.14 and that the scoped store splits streams.
- Use public APIs only (11-06 review). `ObjectStore::inner` and
  `delete_stream` are public.

## Sources

Used and added this run:

- Amazon S3 API reference, DeleteObjects:
  https://docs.aws.amazon.com/AmazonS3/latest/API/API_DeleteObjects.html (up
  to 1,000 keys per request).
- object_store 0.14.2, `aws/mod.rs` `delete_stream` (chunks of 1,000) and
  `lib.rs` (`ObjectStoreExt::delete` is a one-path stream).
- lance-io 13.0.0, `object_store.rs` `remove_stream` and its comment on
  batching.
- Apache Iceberg PR 4052, "SupportsBulkOperations: S3FileIO batch deletion":
  https://github.com/apache/iceberg/pull/4052, and `S3FileIOProperties`
  (delete batch size).
- delta-rs issue 393, parallel vacuum:
  https://github.com/delta-io/delta-rs/issues/393; Delta Lake blog, efficient
  vacuum with file inventory: https://delta.io/blog/efficient-delta-vacuum/.
- lakeFS S3 gateway, DeleteObjects:
  https://github.com/treeverse/lakeFS/blob/f7117df3/pkg/gateway/operations/deleteobjects.go.
- Issue #1252 and its 2026-11-13 storage audit comment; issue #1095 (cleanup
  opens every version before deleting any).

## Not verified

- AWS S3 itself, Azure (its store batches 256 per request) and GCS.
- A served cleanup: the contract refuses `cleanup` while a server holds the
  cluster, and the server's scoped store would split the stream anyway.
- Cleanups that free more than 1,000 files per table.
