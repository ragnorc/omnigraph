# Audit: Lance, DataFusion and Arrow primitives Omnigraph underuses — storage and versioning

- **Run:** `run-wf-audit-modernrelay-omnigraph-underused-primitives-20261113T1100Z-7094fd`
  (scheduled occurrence 2026-11-13T11:00:00Z), executor `agent-devbox`.
- **Focus:** storage and versioning: table writes and Lance transactions; `__manifest`
  versions, branches, tags and history; maintenance (compaction, index builds, cleanup);
  the object-store layer. No focus was named. Earlier runs covered the whole engine
  (2026-11-04), indexes and search (2026-11-07), and scans and query execution
  (2026-11-10).
- **Audited commit:** ModernRelay/omnigraph `9fb5b48e65d09db5ca8206ced437a0e4ec07cef6`
  (main, 2026-11-13).
- **Pinned versions (Cargo.lock):** lance 13.0.0 (whole family), datafusion 54.0.0,
  arrow 58.4.1, object_store 0.14.2 (Lance) and 0.13.2 (DataFusion).

Paths are relative to the repository root. `E/` is `crates/omnigraph/src/`. Upstream paths
are in the crates.io sources at the pinned versions.

## Summary

12 new findings. 7 are filed as their own issues, 5 small ones are grouped into one issue,
and 2 more add evidence to existing open issues (#837, #1252). Nothing filed undoes a
compatibility fence in `docs/dev/lance.md`.

Top three:

1. **L-merge-source-stats.** Every keyed write gives Lance's merge-insert a one-shot stream.
   A stream has no statistics, so DataFusion keeps the whole target table on the hash join's
   build side. Measured on a 2M-row target with a 2,000-row source: 146.9 MB build side and
   364 ms. With Lance's own `execute_uncommitted_batches`, the build side was 128 KB and
   the call took 90 ms. Size S.
2. **L-manifest-open-at-version.** Reading `__manifest` at a known version first opens the
   latest version (and, on a named branch, the branch's latest version), then checks out
   the wanted one. On an ordered object store each "latest" is a LIST of `_versions/`.
   The change-feed recheck, time-travel reads, export at a version and cleanup all pay it.
   Size S to M.
3. **L-manifest-scan-store.** `manifest_scan_dataset` asks Lance's store registry for a
   4 MiB-block store and drops it at once. The registry keeps only weak references, so
   every call misses and builds new S3 or Azure clients. Size S.

## Primitive maps

Status words: **used**, **used partly**, **reimplemented**, **worked around** (deliberate,
cited), **not used**.

### Lance 13.0.0

| Primitive | Omnigraph status | Omnigraph references |
|---|---|---|
| `InsertBuilder::execute_uncommitted` | used | `E/table_store.rs:4052, 5170, 5761` |
| `InsertBuilder::execute_uncommitted_stream` | not used on graph tables | only `E/db/upgrade.rs:1984`; low gain (`ValidateExec` already holds the batches) |
| `MergeInsertJob::execute_uncommitted` (stream source) | used | `E/table_store.rs:3864-3881, 4351` |
| `MergeInsertJob::execute_uncommitted_batches` (in-memory source with statistics) | not used | see L-merge-source-stats |
| `MergeInsertBuilder` `use_index`, `RewriteColumns`, `conflict_retries(0)`, dedupe `Fail` | used | `E/table_store.rs:4323-4347` |
| `DeleteBuilder::execute_uncommitted` | used | `E/table_store.rs:3515` (hand-built path is #1256) |
| `CommitBuilder` detached, skip auto cleanup | used | `E/table_store.rs:1236-1253` |
| `CommitBuilder::with_affected_rows` on a detached commit | used, has no effect | `E/table_store.rs:1243-1245`; see small items |
| `CommitBuilder::execute_batch` (several uncommitted writes, one version) | not used; the pattern is copied for Overwrite only | `E/table_store.rs:5815-5845`; see L-merge-chain-one-commit |
| Transaction properties | used (written directly by `StagingWitness::stamp`) | `E/table_store.rs:4670` |
| `WriteParams` (`data_storage_version`, file writer options) | used; V2_2 explicit | `E/table_store.rs:116-122, 5156-5168` |
| `DatasetBuilder::with_version` | used for tables; not for `__manifest` reads at a version | `crates/omnigraph-core/src/instrumentation.rs:2024-2080`; `crates/omnigraph-catalog/src/graph.rs:349-366` |
| `Dataset::checkout_version` | used partly: only after a Latest open | `crates/omnigraph-catalog/src/graph.rs:357-361`, `crates/omnigraph-catalog/src/lib.rs:1669-1694` |
| `checkout_branch` | used | `crates/omnigraph-catalog/src/layout.rs:63-161` |
| `latest_version_id` / `resolve_latest_location` | worked around for reads (fence "Catalog freshness probe"); still used by the publisher | `crates/omnigraph-catalog/src/lib.rs:999-1018`; `crates/omnigraph-core/src/instrumentation/control_dataset.rs:71-104` |
| `version_refs` | used | `crates/omnigraph-catalog/src/lib.rs:1213-1226` |
| `Branches::list`, `replace_metadata`, `create_branch` | used | `crates/omnigraph-core/src/branch_control.rs`, `crates/omnigraph-core/src/lance_clone.rs:196` |
| `Branches::get` | reimplemented as one GET (fence "Branch ref reads") | `crates/omnigraph-core/src/branch_control.rs:75-121` |
| `Tags::get`, `Tags::list`, `TagContents::from_path` | used; 3 requests per tag read | `crates/omnigraph-catalog/src/retention.rs:229-243`, `branch_control.rs:618-623`; see L-ref-reads-one-get |
| `plan_compaction`, `CompactionTask::execute` | used; tasks run one at a time | `E/table_store.rs:4985-5018` |
| `compact_files` task concurrency (`num_threads`) | not used | see L-compaction-task-concurrency |
| `commit_compaction` | worked around (detached `Rewrite`, RFC 0067) | `E/table_store.rs:5019-5059` |
| `create_index_builder(..).execute_uncommitted` | used; one index and one table at a time | `E/table_store.rs:3347-3420, 5915-5990`, `E/db/omnigraph/table_ops.rs:429-475` |
| `optimize_indices`, delta segments | worked around (fence "Index segments") | `E/table_store.rs:3286-3300` |
| `cleanup_old_versions` | worked around (fence "Cleanup", deny-listed) | `crates/omnigraph/tests/forbidden_apis.rs` |
| `ObjectStoreRegistry` | used; defeated in one place | `crates/omnigraph-core/src/lance_access.rs:7-17`; `crates/omnigraph-core/src/instrumentation/small_manifest_reads.rs:19-55` |
| `lance_io::ObjectStore::new` (wrap an existing client) | not used | see L-manifest-scan-store |
| `ObjectStore::remove_stream`, `remove_dir_all` | `remove_dir_all` used (lists again on cloud stores); serial deletes | `E/db/omnigraph/collector.rs:368-392`; see the #1252 comment |
| Session caches | Lance defaults (#702); zero-cache control session (invariant 12) | `crates/omnigraph-core/src/lance_access.rs:59-69, 93-95` |

### DataFusion 54.0.0

Inside this focus DataFusion is reached only through Lance's merge-insert plan.

| Primitive | Omnigraph status | Omnigraph references |
|---|---|---|
| `JoinSelection` build-side swap from input statistics | not reached: Omnigraph's stream source reports none | `E/table_store.rs:3868-3869`; `datafusion-physical-optimizer-54.0.0/src/join_selection.rs:87-138` |
| `MemTable` exact statistics | not used (Lance's batches entry point would create it) | see L-merge-source-stats |

### Arrow 58.4.1

No Arrow-specific finding in this focus. Write batches are built and concatenated with
`arrow-select` as before; earlier runs cover row comparison (A1, #1247) and sizing (A3, #1251).

### object_store 0.14.2

| Primitive | Omnigraph status | Omnigraph references |
|---|---|---|
| `delete_stream` (S3 `DeleteObjects`, 1,000 keys per request; Azure batches of 256) | not used | `E/db/omnigraph/collector.rs:368-392`; see the #1252 comment |
| `RetryConfig` | used: captured and checked equal on both clients; one latent scoped path builds no retrying client | `crates/omnigraph-storage/src/binding.rs:181-195, 386-409, 474-498`; see small items |
| `ClientOptions` | defaults; unknown native keys refused (deliberate) | `crates/omnigraph-storage/src/binding.rs:450-470` |

## Ranked findings

Ranking: a resource bound or a guarantee first, then storage requests on common paths,
then wall time, each weighed against size and risk.

### 1. L-merge-source-stats: keyed writes make Lance buffer the whole target table — S

**Now.** `stage_keyed_write` wraps its one in-memory batch in a one-shot stream
(`E/table_store.rs:3868-3869`) and calls `MergeInsertJob::execute_uncommitted(stream)`
(`:4351`). This covers every upsert (`load --mode merge`, mutation upserts, three-way branch
merge) and update-only writes on a table without an `id` BTREE. All insertion-capable writes
use `use_index(false)` (`:4337`), so they all take the join.

**Upstream.** A stream reports no statistics. With the target past DataFusion's
`hash_join_single_partition_threshold_rows` (128 Ki rows), neither side qualifies for
`CollectLeft`, and the plan is a partitioned join whose build side is the target. Lance's
own test states this: `test_plan_join_build_side_follows_source_statistics`
(`lance-13.0.0/src/dataset/write/merge_insert.rs:9577-9675`). The public
`execute_uncommitted_batches` (`:2217-2224`) wraps the batches in a `MemTable` with exact
statistics. They reach the planner unless dedupe is `FirstSeen` (`:2453-2477`); Omnigraph
uses `Fail`. `JoinSelection` then puts the source on the build side.

**Why Omnigraph does this.** No reason found. There is one call site and it always holds
exactly one batch. #1225 (closed) discussed this hazard. It concluded that only a
partial-schema source on a wide table is affected and that a Lance upgrade is needed. The
measurement below uses a full-schema source on Lance 13.0.0.

**Evidence (measured).** A scratch test against the pinned Lance (not committed). Target:
2,000,000 rows of `id: Utf8` (`node-000000000123` shape) and `v: UInt64`, stable row ids,
in memory. Source: 2,000 rows (1,000 matched, 1,000 new). Omnigraph's builder settings
(`UpdateAll`, `InsertAll`, `use_index(false)`, `conflict_retries(0)`, dedupe `Fail`).
`analyze_plan` against `analyze_plan_batches`:

| Source | Join | Build input rows | `build_mem_used` | Elapsed |
|---|---|---|---|---|
| stream (Omnigraph today) | `Partitioned`, `Right` | 2,000,000 | 146.9 MB | 364 ms |
| `execute_uncommitted_batches` | `CollectLeft`, `Left` | 2,000 | 128.2 KB | 90 ms |

The target scan is the same in both (`projection=[id]`, 2 fragments, about 19.6 MB read).

**Gain.** Memory per keyed write follows the source (at most 8,192 rows), not the table.
That is invariant 11's "scale with the working set". Lance runs the merge in its own
`SessionContext` with no memory limit, so `write_max_bytes` does not bound the build side
today. CPU drops too. I/O does not change (#1248 covers the full scan).

**Cost and risk.** One call changes. The order of inserted rows in the written fragment may
change, so a golden that pins fragment row order could move. The DST storage-cost golden
should not move.

**Size:** S.

### 2. L-manifest-open-at-version: a `__manifest` read at a known version first opens the latest — S to M

**Now.** `snapshot_state_at` (`crates/omnigraph-catalog/src/graph.rs:349-366`) and
`snapshot_manifest_dataset` (`crates/omnigraph-catalog/src/lib.rs:1669-1694`) call
`open_manifest_dataset_with_session` / `open_manifest_dataset_native_with_session`
(`crates/omnigraph-catalog/src/layout.rs:63-112`). These open main `__manifest` at
`VersionResolution::Latest`, check out the branch's latest on a named branch, and only then
call `checkout_version(v)`. Callers include:

- the change-feed authority recheck, twice per enumerated commit
  (`E/changes/enumerate.rs:445-446, 466-495`);
- time-travel reads (`E/db/omnigraph.rs:2777-2800`) and export at a version
  (`E/db/omnigraph/export.rs:168`);
- the collector's `CollectorBranch::snapshot_at`
  (`crates/omnigraph-catalog/src/lib.rs:1283-1298`), once per retained version, serially
  (`E/db/omnigraph/collector.rs:1112-1121`). It reopens from the root although it holds the
  branch's dataset, and although `read_versions` (`lib.rs:1234-1261`) already checked these
  versions out under `--older-than`.

**Upstream.** `DatasetBuilder::with_version`, which the table pins already use
(`VersionResolution::At`), or `Dataset::checkout_version(Ref::Version(Some(native), Some(v)))`
on the held dataset (`lance-13.0.0/src/dataset.rs:528-545, 678-745`). Either resolves one
version by name: a HEAD and a GET. Latest resolution on an ordered store (S3, GCS, Azure) is
a LIST page of `_versions/` (`lance-table-13.0.0/src/io/commit.rs:574-620`).

**Why Omnigraph does this.** No reason found; the helpers were built for "latest". The
branch-authority reads (ref listing, identifier reads) stay as they are.

**Gain.** Per open at a version: 1 LIST and 1 GET on main; 2 LISTs and 1 to 2 GETs on a
named branch. For the collector, per retained version below the head, the reopen goes away
and the loop can run at `io_parallelism`, as `read_versions` already does. #962 measured one
such LIST at about 40 ms in-region, against 19 ms for a HEAD.

**Cost and risk.** Error classes change. `At(v)` maps a missing version to
`HistoricalVersionReclaimed`, which the change feed turns into a 410. A removed `__manifest`
shows up differently. Map each case explicitly. A direct open of a branch tree must check
that the opened manifest names the expected native branch.

**Evidence.** Code inspection. A main-only prototype (`snapshot_state_at` opening
`At(version)` on main) passed the DST storage-cost golden with no row changed, so no golden
scenario reads `__manifest` at a version on main. The golden neither confirms nor refutes
the saving. A labeled scenario for a change-feed page on a named branch would show it.

**Size:** S for main and the collector; M for named branches without the main open.

### 3. L-manifest-scan-store: every manifest scan builds new object-store clients — S

**Now.** `manifest_scan_dataset`
(`crates/omnigraph-core/src/instrumentation/small_manifest_reads.rs:19-55`) asks the
registry for a store for the same URI with `block_size = 4 MiB`, clones the struct into
`bounded`, and drops the registry's `Arc` when it returns. The registry keeps only weak
references and keys on the parameters, block size included
(`lance-io-13.0.0/src/object_store/providers.rs:271-335`). Nothing else holds that store in
production (`record_probed_store` keeps it only under a cost probe), so every call misses.
A miss calls the provider's `new_store`. Under `ScopedProvider` that builds two native
clients (`crates/omnigraph-core/src/lance_access/scoped.rs:43-110`); under `BoundProvider`
one (`bound.rs:68-100`). Each new S3 client has its own connection pool. Without static
keys it also gets a new credential chain, so a fresh STS, IMDS or ECS fetch. Azure is the
same. The function runs on every cold branch projection and full refresh
(`crates/omnigraph-catalog/src/lib.rs:1850, 2120`, `schema_publication.rs:76`).

**Upstream.** `lance_io::ObjectStore::new(original.inner.clone(), …, Some(4 MiB), …)`
(`lance-io-13.0.0/src/object_store.rs:1819`) wraps the client the dataset already has. The
crate's own doc says the registry "owns the reusable client pool"
(`crates/omnigraph-core/src/lance_access.rs:7-11`).

**Why Omnigraph does this.** The 4 MiB block makes a small manifest file one GET. Client
reuse was not considered.

**Gain.** No new client, TLS handshakes or credential fetch per cold manifest scan.

**Cost and risk.** Low. `ObjectStore::new` drops local directory operations and the
paginated lister; apply it to cloud schemes only. A local miss is cheap.

**Evidence.** Code inspection. `ObjectStoreRegistry::stats().misses` across two cold opens
would show it. The DST golden cannot, because building a client is not a request.

**Size:** S.

### 4. L-ref-reads-one-get: tag reads cost 3 requests where one GET does — S

**Now.**

- `Tags::get` costs exists-HEAD, size-HEAD and a ranged GET (`lance-13.0.0/src/dataset/refs.rs`),
  called for engine tags at `crates/omnigraph-catalog/src/publisher.rs:1509`,
  `unattached.rs:941`, `unpublished.rs:627, 839`.
- `read_native_tag` (`crates/omnigraph-catalog/src/retention.rs:229-243`) says "one object
  read" but goes through `TagContents::from_path` (HEAD plus ranged GET). It runs serially
  per proposal or snapshot (`unpublished.rs:707-723`, `unattached.rs:776-845`).
- Branch delete calls `tags().list()` (`crates/omnigraph-core/src/branch_control.rs:618-623`),
  which reads every reserved `merge_input_`, `unpublished_` and `unattached_` tag only to
  drop them by name.

**Upstream / existing.** Omnigraph already reads branch refs with one whole-object GET
(`read_ref_contents`, `branch_control.rs:102-121`; fence "Branch ref reads").
`TagContents` is serde and `tag_path` is public.

**Gain.** Tag read 3 → 1 request; each listed tag 2 → 1. Branch delete reads only user tags
(list names, filter, then read). The one-GET read also cannot be torn by `Tags::update`.

**Cost and risk.** Low. Keep `NotFound` skipping for raced deletes.

**Evidence.** Code inspection. `BranchDelete` and `Cleanup` `l.get` rows in
`crates/omnigraph-dst/cost_table.txt` would show it.

**Size:** S.

### 5. L-merge-chain-one-commit: a branch merge commits one detached version per 8,192-row chunk — M, decide first

**Now.** Every insert, update and delete chunk of a merge is its own detached commit,
chained on the previous one (`E/exec/merge.rs:4537-4550`, and the chunk loops around
`:4558-4610` and `:4854-4935`), up to 1,024 links per table (`MAX_BRANCH_MERGE_DATA_TRANSACTIONS`,
`:31-34`). Each insert chunk writes one fragment of at most 8,192 rows. A 1M-row merge
makes about 123 links: about 246 commit PUTs, about 123 small fragments for `optimize` to
compact later, and a chain that the pure-insert proof (`:1124-1136`) and the collector's
`walk_chain` must follow.

**Upstream.** Write chunks uncommitted, then commit once. `CommitBuilder::execute_batch`
(`lance-13.0.0/src/dataset/write/commit.rs:577-611`) is the stock shape for appends.
Omnigraph cannot call it as is, because its strict insert is a filter-bearing `Update`
(fence "Graph keys"). But it already joins uncommitted Overwrite chunks into one
transaction (`E/table_store.rs:5815-5845`). The key-existence filter is fixed-size
(`lance-table-13.0.0/src/format/key_existence.rs:69-93`), so one `Update` can carry all
insert chunks' fragments.

**Why Omnigraph does this.** The chain came from PR #734, when each link was promoted by
replaying it linearly. Detached-for-life tables (schema v11) removed promotion. What still
matters: the insert certificate (`certify_insert_absence`), the chunk failpoints and
`proven_chain_fragments`. Update chunks from one read version cannot simply be unioned,
because their deletion vectors on shared fragments would collide.

**Gain.** About 2 fewer commit PUTs per link, shorter chains, fewer small fragments.

**Cost and risk.** M: a combined-certificate test, failpoint rewiring, and a decision on
one transaction per kind. Inserts first.

**Evidence.** Code inspection. The DST golden has no named-branch merge row with several
chunks.

**Size:** M.

### 6. L-compaction-task-concurrency: optimize runs a table's compaction tasks one at a time — S, decide first

**Now.** `stage_compaction_plan` runs `task.execute(ds).await` in a plain loop
(`E/table_store.rs:5012-5018`).

**Upstream.** `compact_files` runs tasks with `buffer_unordered(num_threads)`
(`lance-13.0.0/src/dataset/optimize.rs:1078-1087`). `CompactionTask` is documented as a
standalone task that can run anywhere (`:1290`).

**Why Omnigraph does this.** Not stated. The remote-optimize RFC says rewriting one table at
a time keeps allocations from competing
(`docs/rfcs/2026-10-29-remote-optimize-through-the-serving-owner.md:214-216`). The Blob
fence bounds bytes per scanner batch per task.

**Gain.** Less wall time for a large table with many tasks, and on the served path a
shorter writer-gate hold.

**Cost and risk.** Memory grows with concurrent tasks. Keep Blob tables at 1. Use ordered
`buffered(k)` so fragment ids (assigned in result order, `:5023-5040`) and DST replay stay
identical. Take `k` from the existing optimize concurrency setting so the served path stays
at 1 unless decided otherwise.

**Evidence.** Code inspection. DST `Optimize` rows must not move.

**Size:** S.

### 7. L-index-build-concurrency: `index build` builds indexes one at a time — S to M

**Now.** `ensure_indices` stages tables one after another (`E/db/omnigraph/table_ops.rs:429-475`)
and, within a table, builds indexes one after another (`E/table_store.rs:5915-5990`; the
fold at `:3347-3420`). Each build is `execute_uncommitted` on its own snapshot. Staging runs
before the writer gates (`table_ops.rs:479-486`), so concurrency would not lengthen a gate
hold.

**Upstream.** Index builds are independent uncommitted operations. BTREE training spills
under a 150 MiB-per-partition pool (`lance-13.0.0/src/index/scalar.rs:159-179`), so
concurrent BTREE builds stay bounded.

**Gain.** Less wall time on graphs with many types and indexes.

**Cost and risk.** Memory grows with concurrent builds. Full-text builds stay serial until
#1296 bounds them.

**Evidence.** Code inspection.

**Size:** S to M.

### 8. Small items — S each (one issue)

- **L-detached-affected-rows.** `commit_detached` passes `with_affected_rows`
  (`E/table_store.rs:1243-1245`). Lance passes `affected_rows` only to `commit_transaction`;
  the detached path never reads it (`lance-13.0.0/src/dataset/write/commit.rs:459-480`).
  Drop it or say it is inert.
- **L-source-bytes-serial.** `source_file_bytes` sends one HEAD per deletion file, and one per
  data file with no recorded size, serially (`E/db/omnigraph/optimize.rs:1740-1770`). It runs
  in planning whenever a byte ceiling is set, which the served path always sets. An ordered
  `buffered(io_parallelism)` with the same early stop does it.
- **L-fork-absent-listing.** `named_fork_is_absent` (`E/table_store.rs:2320-2329`) reads every
  ref's contents (`list_branch_contents`) to test whether one name exists; a name listing is
  enough. The Blob witness (`E/blob.rs:1450-1455`) does the same. Legacy and error paths only.
- **O-bound-scoped-read-retry (latent).** With a captured binding and a scope,
  `binding.adapter` builds only the single-attempt S3 client
  (`crates/omnigraph-storage/src/binding.rs:386-409`), and `with_scope` uses it for both the
  retrying and the single role (`crates/omnigraph-storage/src/scoped.rs:347-349`). Reads would
  then not retry, against `docs/user/operations/storage-failures.md`. The environment path
  builds both clients (`crates/omnigraph-storage/src/lib.rs:1409-1417`). No current caller
  reaches it.
- **Doc mismatch.** `docs/user/operations/storage-failures.md:70` says direct clients retry up
  to 10 times by default. A captured binding caps retries at 3
  (`crates/omnigraph-storage/src/binding.rs:478-498`); only the environment path keeps
  object_store's 10.

### 9. Evidence added to existing issues

- **#837 (L-publisher-load-probe).** `GraphNamespacePublisher::dataset`
  (`crates/omnigraph-catalog/src/publisher.rs:604-626`) loads the latest `__manifest`
  through `current_control_dataset` (`crates/omnigraph-core/src/instrumentation/control_dataset.rs:71-104`),
  which calls Lance's `resolve_latest_location`: one LIST of `_versions/` per publish on
  an ordered store. The by-name probe built for #962 (`crates/omnigraph-catalog/src/lib.rs:999-1018`)
  does the same with two concurrent HEADs. This is a smaller step inside #837's direction.
- **#1252 (O-delete-stream-bulk).** object_store's `delete_stream` sends S3 `DeleteObjects`
  with 1,000 keys per request (`object_store-0.14.2/src/aws/mod.rs:336-361`) and Azure
  batches of 256; the scoped store already falls back to counted per-key deletes
  (`crates/omnigraph-storage/src/scoped.rs:870-897`). Also: index directories are deleted
  with `remove_dir_all`, which lists the directory again on object stores although
  `plan_orphans` already listed it (`E/db/omnigraph/collector.rs:384, 1456-1496`), and
  index-directory bytes never reach `bytes_removed` (`:384-388`).

## Filed issues

| Finding ID | Entry | Action | Link |
|---|---|---|---|
| L-merge-source-stats | 1 | filed | pending |
| L-manifest-open-at-version | 2 | filed | pending |
| L-manifest-scan-store | 3 | filed | pending |
| L-ref-reads-one-get | 4 | filed | pending |
| L-merge-chain-one-commit | 5 | filed | pending |
| L-compaction-task-concurrency | 6 | filed | pending |
| L-index-build-concurrency | 7 | filed | pending |
| L-detached-affected-rows, L-source-bytes-serial, L-fork-absent-listing, O-bound-scoped-read-retry, doc mismatch | 8 | filed (one issue) | pending |
| L-publisher-load-probe | 9 | commented | #837 |
| O-delete-stream-bulk | 9 | commented | #1252 |

Already reported and skipped: L1 (#1250), L4 (#1252), L7 (#1095), L9 (#1256), L11 and L12
(#1262), L14 (#1258), L2 (#1248), L13 (#1264). The collector's per-version table opens stay
with #1095.

## Deliberate workarounds kept

Each of these looks like an underused primitive and is not. Do not undo them.

- **Detached-only table commits, hand-built `Rewrite`, `Project`, `Merge`, `Delete` and
  strict-insert `Update`.** RFC 0067; fences "Detached commits", "Graph keys", "Schema evolution".
- **Second detached `CreateIndex` for the untrained full-text segment.** A transaction carries
  one operation, and `Overwrite` drops every index
  (`lance-table-13.0.0/src/transaction/manifest_build.rs:1017-1038`). Fence "Full-text analyzer".
- **Transaction file on every detached commit.** Its `{read_version}-{uuid}.txn` name is how
  pins and chain walks are matched. Fence "Detached commits".
- **Strict `Overwrite` with zero retries for `__manifest`.** Fence "Catalog publication".
- **Freshness probe by name; zero-cache control session.** Fence "Catalog freshness probe";
  invariant 12.
- **Exact ref reads with one GET and the retry arm.** Fence "Branch ref reads".
- **Own `__history` files instead of Lance version history or tags.** RFC 0068.
- **Retirement metadata and archive instead of Lance branch delete.** Fence "Branches/tags".
- **Engine collector instead of `cleanup_old_versions`.** Fence "Cleanup".
- **Whole index rebuilds.** Fence "Index segments".
- **Per-task Blob batch sizing; no per-run source budgets.** Fences "Compaction of Blob tables",
  "Dropped-column erasure".
- **Two Lance clients per scoped store (retrying, single-attempt); the conditional re-issue
  policy.** Fence "Manifest request resolution"; #1067, #1096.
- **Blob cell writes replace the whole row.** Lance 13 data overlays refuse Blob v2 and are an
  unknown feature in release builds; `RewriteColumns` refuses Blob columns (fence "Column patch").

## Upstream changes since the pins

- Lance 14.0.2 (2026-10-29) is still the latest stable release; 14.0.3-rc.1 and 15.0.0 betas
  are out. lance#9817 (stale FTS and BTREE segment entries) and lance#9692 (rechunked tails)
  closed in late October, behind the "Index segments" and "Ordered walks" fences; check at
  the next bump. lance#10781 (column patch key) is still open. lance#10557 merged after 13.0.0.
- No release changes a verdict above. L-merge-source-stats needs no upgrade.

## Evidence

- **Merge-insert build side (finding 1):** a scratch test in the engine's test crate against
  the pinned Lance, run once and deleted (nothing pushed). Numbers are from one run on a
  2-CPU box with an in-memory dataset; read them as ratios.
- **DST storage-cost golden:** passes on main. With the main-only prototype for finding 2 it
  still passes and no row changes (see finding 2). The prototype was discarded.
- Everything else is code inspection of both sides at the pinned versions.

## Could not verify

- Peak memory of a real served merge load on a large table; the measurement is the plan's own
  `build_mem_used`.
- Whether switching to the batches entry point moves any GQT or DST golden through inserted
  row order.
- The real cost of a registry miss on S3 or Azure (finding 3), and how often a server does a
  cold branch projection.
- Which call each golden LIST in the `BranchDiff*` and publishing rows is; the attribution in
  findings 2 and 9 is from counts and code.
- Wall-time gains and peak memory for findings 6 and 7.
- Whether RustFS (used in CI) supports `DeleteObjects` (#1252 comment).
