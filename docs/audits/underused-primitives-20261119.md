# Underused Lance, DataFusion and Arrow primitives: graph branches

Audit run `run-wf-audit-modernrelay-omnigraph-underused-primitives-20261119T1100Z-d5260c`, 2026-11-19.

- Focus: graph branches. This covers branch create, delete, list and resolve, refs and tags, merge base and merge, branch diff, and the commit path a branch write takes. No focus was named. Earlier runs covered the whole engine, indexes and search, scans and query execution, and storage and versioning.
- Audited commit: ModernRelay/omnigraph `fd4d61f6e7871c752644de3e50462884b7b058b3` (main, 2026-11-18).
- Pinned libraries: lance 13.0.0 (whole package family), datafusion 54.0.0, arrow 58.4.1, object_store 0.14.2.
- Upstream since the pin: Lance 14.0.3 is the latest stable release. Lance 15.0.0-beta.25 is the latest beta. Many branch fixes landed upstream after 13.0.0: lance#10447, #10489, #10505, #10735, #10736, #10737 and #10705. Lance PR #10756 backports some of them to 14. The fences in `docs/dev/lance.md` name the bumps that would retire them. This audit did not assess those bumps.

## Summary

Five candidates were checked on both sides. Four are recorded in the findings queue. None is severe enough to promote to GitHub (priority 0 or 1).

| Rank | Finding ID | What changes | Evidence | Size | Queue slug |
|---|---|---|---|---|---|
| 1 | L-row-identity-dv-delta | Branch diff decodes and hashes every live row of a fragment whose only change is its deletion file | Measured (microbenchmark on the production decoders) | S | `og-audit-L-row-identity-dv-delta` |
| 2 | L-pinned-commit-archive-scan | Opening a merge base or retired commit on a named branch reads every retirement archive, one serial GET each | Measured (request counts) | S-M | `og-audit-L-pinned-commit-archive-scan` |
| 3 | L-branch-control-rereads | Branch create, delete and named open read the same ref or listing more than once (grouped, three items) | Inspected | S | `og-audit-L-branch-control-rereads` |
| 4 | L-linear-table-commit-residue | Linear table-commit wrappers are reachable only from tests; two stale names in docs and guards | Inspected | S | `og-audit-L-linear-table-commit-residue` |

Not recorded: L-merge-candidate-tail-rescan. The measurement did not confirm the predicted cost. See [What could not be verified](#what-could-not-be-verified).

## Method

Three read-only passes read the Omnigraph sources at the audited commit and the upstream crates at the pinned versions:

- Branch lifecycle: refs, tags, create and delete.
- Merge and diff.
- Transactions and commit handlers.

Each candidate was then read again on both sides. Three were measured with scratch tests in `crates/omnigraph/tests/integration/` that used the shared `helpers::cost` harness (`IoCounts`, `last_manifest_reads`). The tests ran on the box's warm build of `9fb5b48e`, five days older than the audited commit. The measured code paths are identical in both commits: `changes/row_identity.rs`, `retention.rs` and `branch_control.rs` have no diff, and the merge chunk loop in `exec/merge.rs` is unchanged. The scratch tests were not pushed.

Duplicates were checked against every open and closed issue and pull request on ModernRelay/omnigraph (titles, plus `gh search` on the function names) and against the findings queue, which held no audit findings before this run.

## Library tables

### Lance 13.0.0

| Primitive | Omnigraph status | Where |
|---|---|---|
| `Dataset::create_branch` (shallow-clone commit, then `Branches::create` put-if-absent) | Used for `__manifest` only, wrapped. Graph tables are not forked (fence "Table forks"). Omnigraph repeats checks that the put-if-absent already makes (finding 3). | `omnigraph-core/src/lance_clone.rs:197`, `branch_control.rs:872` |
| `checkout_version(Ref::Version(branch, v))`, which opens by path and reads no ref | Used, but not where it would replace a full inventory (finding 2) | `omnigraph-catalog/src/retention.rs:335`, `lib.rs:2556` |
| `Branches::list` / `list_branches` (LIST, then HEAD and ranged GET per ref) | Used for full inventories, with a torn-read retry. Deliberate (fence "Branch ref reads"). | `branch_control.rs:67, 332` |
| `Branches::get`, `BranchContents::from_path`, `Dataset::branch_identifier` | Reimplemented as one whole-object GET. Deliberate (fence "Branch ref reads"). | `branch_control.rs:75-142` |
| `Branches::replace_metadata` (HEAD, HEAD, ranged GET, PUT) | Used for retirement, after Omnigraph already read the ref (finding 3) | `branch_control.rs:650` |
| `delete_branch`, `force_delete_branch` | `delete_branch` is not used: delete retires, archives, then unlinks. `force_delete_branch` is used for reclaim. Deliberate (fence "Branches/tags"). | `branch_control.rs:462-530, 685` |
| `Tags::create/get/list/delete` | Used. Branch delete still lists every tag through Lance; #1382 proposed one GET and was closed as not planned. | `branch_control.rs:618`, `publisher.rs:1506` |
| `latest_version_id` / `versions` | Worked around with version-name HEAD probes. Deliberate (fence "Catalog freshness probe"). | `omnigraph-catalog/src/lib.rs:1020` |
| `shallow_clone` | Not used. Graph branches need no table fork (fence "Table forks"). | — |
| `CommitBuilder::with_detached` | Used for every table effect and every `__manifest` candidate | `table_store.rs:1375`, `omnigraph-catalog/src/commit.rs:190` |
| `with_max_retries(0)` strict overwrite | Used as the `__manifest` version CAS. Deliberate (fence "Catalog publication"). | `omnigraph-catalog/src/commit.rs:189` |
| Linear `commit_transaction` with rebase and retries | Not used in production. The wrappers remain and are reachable only from tests (finding 4). | `table_store.rs:4797-5052` |
| `TransactionRebase` | Used directly to carry a staged write over row-preserving links | `table_store.rs:901-952` |
| `CommitBuilder::execute_batch` | Not applicable: it accepts only `Append`. The merge case is #1383 (closed). | — |
| `CommitHandler`, `ConditionalPutCommitHandler`, `verify_commit_outcome` | Wrapped to re-issue an ambiguous manifest put. Deliberate (fence "Manifest request resolution", #1067). | `lance_clone.rs:218-530`, `manifest_request.rs` |
| `DatasetDelta` (`get_inserted_rows`, `get_updated_rows`, `get_deleted_row_ids`) | Cannot be used. It needs numerically ordered versions, and detached versions are random (fence "Row version stamps"). | — |
| Private `fragment_delta`, `deleted_batches_in_fragment` (deletion-vector difference) | Reimplemented in merge (`exec/merge.rs:2483`). Only partly reimplemented in branch diff (finding 1). | `changes/row_identity.rs:42` |
| `read_row_ids`, `read_dataset_versions`, `RowIdSequence::get`, `version_at` | Used to decode inline sequences. `get` and `version_at` are not used. | `row_identity.rs:149-182` |
| Scanner `_rowid` / `_rowaddr IN` take shortcut | Used for hydration, merge deletes and diff | `ordered_cursor.rs:494`, `exec/merge.rs:342` |
| `unindexed_fragments` | Not consulted by the merge candidate fetch (not recorded; see below) | `exec/merge.rs:2815` |
| `read_version_transaction` | Not used. Already covered by #1222. | — |

### DataFusion 54.0.0

| Primitive | Omnigraph status | Where |
|---|---|---|
| `SortMergeJoinExec` (full and anti) for a three-way merge | Not used. The hand-written three-way cursor is deliberate (fence "Ordered walks", merge.md). | `exec/merge.rs:2000` |
| Bounded `SortExec` with spill | Used | `table_store.rs:3117-3220` |
| `Expr::in_list` | Used for `_rowid`. Merge candidates still build SQL text; covered by #1257. | `exec/merge.rs:377` |

### Arrow 58.4.1

| Primitive | Omnigraph status | Where |
|---|---|---|
| `interleave_record_batch`, `take_record_batch` | Used | `ordered_cursor.rs:80, 526` |
| `RowConverter` and arrow-ord comparisons for row equality | Not used. Covered by #1247. | `changes/row_compare.rs` |

## Findings

### 1. L-row-identity-dv-delta: branch diff hashes a whole fragment when only its deletion file changed

Current code: `row_identity_plan` (`crates/omnigraph/src/changes/row_identity.rs:42-145`) pairs the fragments of the two versions by id. It skips a pair only when the data files and the deletion file are both identical (lines 66-75). Otherwise `live_row_stamps` (lines 149-182) decodes every live `(_rowid, stamp)` pair of both sides into a `std::collections::HashMap` (SipHash). An update commonly leaves this shape: a large old fragment whose deletion vector gains a few rows, plus a small new fragment. Each pair costs the whole fragment twice. Above `ROW_IDENTITY_ROWS_MAX` (8M rows held) the plan gives up and the diff falls back to the exact ordered merge of both tables.

Upstream primitive: Lance computes the same change from the deletion-vector difference (`fragment_delta` and `deleted_batches_in_fragment`, `lance-13.0.0/src/dataset/delta.rs:624-760`). Those functions are private, but the parts are public: `RowIdSequence::get` (`lance-table-13.0.0/src/rowids.rs:504`) and `RowDatasetVersionSequence::version_at` (`rowids/version.rs:273`). Omnigraph merge already applies the rule (`exec/merge.rs:2546-2595`).

Why the rule is safe: when data and overlay files are identical, the row id and the stamp at each offset are identical on both sides. Lance restamps only when it rewrites files (`lance-table` `transaction/manifest_build.rs:919, 1193, 1466, 1532`). The existing guard `row_identity_discovery_stamps_row_ids_and_rewrites_lance_13` already pins that "a deletion-only commit restamps nothing". So only offsets that are deleted on exactly one side can change.

Gain: measured with a scratch test that runs the production decoders (`read_row_ids`, `read_dataset_versions`, `mask`, the same `HashMap` extend and compare) on one fragment pair with one deleted row:

| Fragment rows | Today: time (debug build), map entries held | Deletion-vector difference |
|---|---|---|
| 8,192 | 8.8 ms, 28,672 entries | under 0.001 ms, 1 entry |
| 1,048,576 | 1,450 ms, 3,670,016 entries (about 59 MiB at 17 bytes per entry) | under 0.001 ms, 1 entry |

A debug build overstates the time. The entry count and the memory do not depend on the build, and no memory budget counts that memory.

Cost and risk: small. Take the shortcut only when the two fragments' `row_id_meta` and `last_updated_at_version_meta` bytes are also equal, and fall back to the full decode otherwise. The test that counts "every live row is a candidate" (line 126-127) must count live rows separately. `change_lineage = verify` (`verify_emit_agreement`) stays the oracle.

Deliberate? Partly. RFC `2026-10-26-change-discovery-by-fragment-identity.md` (lines 403-411) rejected the deletion-vector difference as the only rule, because the difference fails across compaction. This hybrid keeps stamp pairing for every rewritten fragment, so that reason does not apply.

Related: #1258 proposes that merge reuse `row_identity_plan`, which would carry this cost into merge.

Size: S.

### 2. L-pinned-commit-archive-scan: resolving a merge base on a named branch reads every retirement archive

Current code: `ManifestCoordinator::pinned_graph_commit_with_session` (`crates/omnigraph-catalog/src/retention.rs:334-390`) builds its candidate list for any commit on a named branch as follows:

1. `list_all_branch_contents` (line 346) reads every active ref.
2. `retired_manifest_branches` (lines 308-313) calls `list_archived_manifest_branches` (`omnigraph-core/src/branch_control.rs:409-449`). That function lists the whole `__manifest/tree/` prefix recursively and GETs each retirement archive, one at a time.
3. It then calls `list_retired_manifest_branch_contents`, which reads every active ref a second time.

Only after that does it check out the one version. The function is called in these places:

- Branch merge, when the merge base is not the target head (`exec/merge.rs:5556`).
- Every read of a commit whose branch was deleted, through `admit_commit` and `ensure_retired_incarnation_opens` (`db/graph_coordinator.rs:1323`, `omnigraph-catalog/src/lib.rs:2651-2660`). `ensure_retired_incarnation_opens` holds a `GraphLineageRow` that carries `native_branch`, but converts it to a `GraphCommit` (line 2656) and drops that field.
- Cleanup, once per retained merge base in a serial loop (`db/omnigraph/collector.rs:1255-1268`).

Upstream primitive: `checkout_version(Ref::Version(Some(native), Some(v)))` (`lance-13.0.0/src/dataset.rs:528-545, 676-747`) opens the version under `tree/<native>` and reads no ref. Omnigraph already opens a retired tree this way (`omnigraph-catalog/src/lib.rs:2556-2563`).

Evidence (measured): a scratch test merges `b` into `a`. The merge base is a commit on `a` that is not `a`'s head. Before the merge, K other branches are created, written once and deleted.

| Deleted branches | `__manifest` reads of the merge | GETs under `tree/` |
|---|---|---|
| 0 | 37 | 15 |
| 4 | 41 | 19 |
| 16 | 53 | 31 |

Each deleted branch adds one GET to every such merge, sent serially, until `cleanup` reclaims the tree. Live sibling branches (0, 4 or 16) left the count at 37. So the duplicate active-ref inventory of steps 1 and 3 was not visible in this counter: either the counter does not see those reads, or they cost nothing here. It is reported as code inspection only.

Gain: a known native name needs one GET of the ref, or of the archive when the ref is gone, then one checkout. That makes the cost flat in deleted branches, which invariant 11 asks for. For rows without `native_branch` (legacy), the duplicate retired-ref inventory can still go.

Cost and risk: the "ref or archive exists" evidence and the `parent_version < v` check both come from that one GET, so both stay. The check that the head's commit id matches stays too. Merge and the collector hold a `GraphCommit` and need the native name threaded through, or a lookup of the lineage row. At the least, the collector can take the inventory once per run instead of once per merge base.

Deliberate? The fence "Branch ref reads" records only that "historical resolution also read[s] retirement archives". It gives no reason for a full inventory when the native ref is known.

Related, not covering: #972 (commit lookup fans out over live branches, a different function), #1052 (pinned read surcharge), #642.

Size: S-M.

### 3. L-branch-control-rereads: branch create, delete and named open read the same objects twice (grouped)

These are three small items with one cause: a function reads a ref or listing that its caller has just read.

- Create: `create_branch_from` (`omnigraph-catalog/src/lib.rs:2789-2807`) runs `ensure_manifest_branch_create_namespace`, which makes one LIST of `_refs/branches/` and GETs the source and the related names. `create_branch_recoverably` (`omnigraph-core/src/branch_control.rs:872-935`) then does four more reads. It GETs the source ref again for its identifier (line 893), lists `_refs/branches/` again (line 897), and GETs the target ref (line 917), although the name is a freshly minted `{name}.{ULID}` (`lib.rs:2798`) and the listing showed it absent. It also lists `tree/<native>/` (line 926). Lance's `create_branch` already refuses an existing ref with a put-if-absent (`lance-13.0.0/src/dataset/refs.rs:147-163, 516-528`). Passing the listing and the source contents through, and skipping the target GET for a fresh name, saves about 3 requests per create from main and 4 from a named source. The golden row `BranchCreate` is `l.list` 6 and `l.get` 6. Keep the tree probe for the retry path. It matters only if an id source re-mints a crashed attempt's name, for example DST's `SeededUlids`.
- Delete: `retire_branch_recoverably` (`branch_control.rs:578-655`) GETs the ref (line 587), then calls `Branches::replace_metadata` (line 653). Lance's call makes a HEAD (exists), a HEAD (size) and a ranged GET of the same object before its PUT (`refs.rs:531-561`). `archive_retired_ref` then GETs it again (line 502). `delete_branch_with_expected` also resolves the native name again (`lib.rs:2912`, one LIST and one GET) although the coordinator holds it (`lib.rs:2684`). Writing the contents already in hand with one PUT (same type, same serde form, same no-CAS semantics) saves 3 requests. It also removes the torn-read window inside `replace_metadata` that the fence "Branch ref reads" describes for Lance's reader. Using the held native name saves 2 more. The golden row `BranchDelete` is `l.get` 16 and `l.list` 6. The cost: this bypasses a public Lance writer that RFC 0042 and `recovery.md` chose, as the archive write and the ref unlink already do.
- Named open: `open_manifest_branch_with_identifier` (`omnigraph-catalog/src/layout.rs:222-225`) calls `resolve_native_manifest_branch`, which reads the ref's contents (`branch_control.rs:174`) and returns only the name. It then GETs the same ref again as `before`. Returning the contents saves one GET per cold named-branch open and per refresh after a freshness miss.

Evidence: code inspection. The request counts come from reading the code. The golden rows give today's totals. Confirm each item by the move of its line in `crates/omnigraph-dst/cost_table.txt` and in `integration/branch_control_cost.rs`.

Size: S each.

### 4. L-linear-table-commit-residue: linear table-commit wrappers that only tests reach

Current code: `TableStore::commit_staged`, `commit_staged_exact` and `commit_staged_with_mode` (`crates/omnigraph/src/table_store.rs:4797-4850, 5013-5052`), `StagedCommitMode` (line 1360) and `map_lance_exact_commit_error` (line 7210) commit a graph table linearly through Lance's `commit_transaction` with rebase and retries. So do the matching `TableStorage` trait methods (`storage_layer.rs:866-879`, implementations at 1530 and 1613). Their only non-test caller is `build_indices_on_dataset_for_catalog` (`db/omnigraph/table_ops.rs:1339-1382`). That function is called only from the tail of `prepare_updates_for_commit` that runs when `txn` is `None` (`table_ops.rs:1401-1451`). The only caller that passes `None` is the `#[cfg(test)]` `commit_updates` (`table_ops.rs:1543-1553`). Every production caller passes `Some(txn)` (line 1663).

Since detached-only tables (invariant 2: a write "never moves its linear HEAD"), nothing in production reaches this path. If something did, it would break that invariant. The functions are `pub(crate)` or trait methods, so the compiler does not warn that they are dead.

Two stale names belong with this item:

- `tests/forbidden_apis.rs:1154` describes the `table_ops` `.commit_staged(` call as the "shared merge/Optimize index tail". Merge and optimize now use `commit_staged_detached`.
- `docs/dev/lance.md` (row "Detached commits") and `exec/merge.rs:34` name a collector function `walk_chain`, which no longer exists.

Gain: about 250 lines of production code removed or moved under `#[cfg(test)]`, and an accurate forbidden-API registry. There are no request savings.

Cost and risk: low. About 20 test call sites build linear fixtures with these methods (`table_store/staged_tests.rs`, `db/catalog_tests.rs:632`, `db/omnigraph.rs:6552`, `core_tests.rs:74`) and need a test-only helper. The version-1 create keeps its own path (`commit_staged_create_exact`).

Evidence: code inspection (callers listed with grep at the audited commit). Confirm it by moving the items under `#[cfg(test)]` and building the workspace.

Size: S.

## Deliberate workarounds kept

These were checked and are not proposed for change. Each is a fence in `docs/dev/lance.md`:

- One-GET ref and tag readers instead of `Branches::get` and `from_path` (fence "Branch ref reads": a torn read under `replace_metadata`).
- No table forks for graph branches (fence "Table forks": Lance leaves a branch made from a detached version headless).
- Branch delete by retirement, archive and unlink instead of `delete_branch` (fence "Branches/tags").
- Version-name HEAD probes instead of `latest_version_id` (fence "Catalog freshness probe").
- Binding parameters passed to `create_branch` (fence "Bound branch create").
- The re-issuing commit-handler wrapper (fence "Manifest request resolution", #1067).
- Strict-overwrite `__manifest` publication (fence "Catalog publication").
- `DatasetDelta` unused, because detached versions are not ordered (fence "Row version stamps").
- The hand-written three-way cursor instead of a DataFusion sort-merge join (fence "Ordered walks").
- Compaction and index folds rebuilt above Lance and committed detached (fences "Index segments" and "Detached commits").

## What could not be verified

- L-merge-candidate-tail-rescan (not recorded). The claim: in a three-way lineage merge, each chunk of 512 candidate ids opens base, source and target cursors with an `id IN (...)` filter (`exec/merge.rs:2815-2845`, `LINEAGE_FILTER_MAX_IDS` at line 2198). Lance answers fragments that the id index covers from the index and scans every uncovered fragment (`lance-13.0.0/src/io/exec/filtered_read.rs:932-957`), so each chunk would rescan the branch's new fragments. The scratch measurement (data reads of the merge, local store) did not show that at the predicted scale:

  | Rows updated on the branch | Loads (new fragments) | Data reads |
  |---|---|---|
  | 512 | 1 | 4 |
  | 4,096 | 1 | 11 |
  | 512 | 8 | 37 |
  | 4,096 | 8 | 11 |

  Eight chunks over one fragment add one read per chunk. Eight chunks over eight fragments read no more than one fragment did. The 37 reads for 512 rows in eight loads have another cause, which this run did not find. A local store and the request counter may hide repeated reads of the same pages, for example through Lance's caches. The per-chunk rescan is therefore unconfirmed. A byte counter, or an S3 run, would settle it. Related: #1294 (the same mechanism on read-path id lookups) and #971.
- The duplicate active-ref inventory in finding 2 and all counts in finding 3 are from code inspection.
- The `ordered_cursor_scan` probe read 0 on the lineage merge route, although that route opens ordered cursors. The probe may count only the full walk. This was not investigated.
- The upstream branch fixes after Lance 13 (listed at the top) were not assessed against the fences.

## Queue and GitHub

| Finding | Queue slug | GitHub |
|---|---|---|
| L-row-identity-dv-delta | `og-audit-L-row-identity-dv-delta` | not promoted (priority 3) |
| L-pinned-commit-archive-scan | `og-audit-L-pinned-commit-archive-scan` | not promoted (priority 3) |
| L-branch-control-rereads | `og-audit-L-branch-control-rereads` | not promoted (priority 4) |
| L-linear-table-commit-residue | `og-audit-L-linear-table-commit-residue` | not promoted (priority 4) |

No finding was covered by an existing issue or pull request, so this run added no GitHub comment.
