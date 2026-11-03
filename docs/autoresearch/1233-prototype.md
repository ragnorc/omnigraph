# Issue 1233 prototype: retained branch bases follow the opened root

**Status:** prototype for design and sizing. It is not the shipped fix, and
no pull request is open for it.
**Base:** `origin/main` at `17abff34`.
**Branch:** `proto/1233-branch-base-follows-root` on the `ragnorc/omnigraph`
fork.

## Summary

The prototype adds one reader rule. When Omnigraph opens a `__manifest`
branch handle, it replaces each base in the handle's in-memory
`Manifest::base_paths` that Lance's shallow clone recorded for a branch of
the same `__manifest` dataset. The replacement is the location that the
opened root gives that branch. The persisted manifest and transaction bytes
do not change. Lance does not need a patch: in Lance 13.0.0,
`Dataset::manifest` is a public field, and every `base_id` read resolves
through it.

A new regression test fails on `main` the way the issue reports it, and
passes with the rule. It covers all five acceptance cases, a no-fallback
case and a write through a rebound handle. The fix is small, but the
decision behind it is not. The sizing verdict is **L** (an amendment or a
short RFC). The reasons are in [Sizing](#sizing-m-or-l).

## Where Lance resolves a `base_id` file

These references are to Lance 13.0.0 (`lance-13.0.0`, `lance-table-13.0.0`
and `lance-io-13.0.0` on crates.io).

- `Dataset::create_branch` (`dataset.rs`) builds `Operation::Clone` with
  `ref_path: source_location.uri`, the absolute URI of the source branch of
  the same dataset.
- `do_commit_new_dataset` (`io/commit.rs`) loads the source manifest from
  storage at `ref_path` and calls `Manifest::shallow_clone`. That copies the
  source's persisted `base_paths` and adds
  `BasePath::new(id, ref_path, ref_name, true)`. Its `name` is the source
  branch (`None` for main), and `is_dataset_root` is `true`. Every inherited
  data file and deletion file gets `base_id`. Index metadata keeps its own
  `base_id` through the same map.
- Every reader resolves a `base_id` through `self.manifest.base_paths` and
  nothing else: `data_file_dir_for_base`, `dataset_dir_for_deletion`,
  `indice_files_dir`, and `object_store(Some(id))` through
  `base_object_store`. `BasePath::extract_path` turns the URI into an object
  path through the session's store registry.
- On a `file` store, `ObjectStore::open` reads that absolute path directly
  (`LocalObjectReader`, `has_direct_local_paths`). So an object-store
  wrapper cannot redirect local reads. That rules out a wrapper-based
  rule.
- `checkout_by_ref` (behind `checkout_branch` and `checkout_version`) always
  builds a new `Dataset` from the persisted manifest (`get_manifest`, then
  `checkout_manifest`). Lance offers no hook between loading the manifest
  and returning the handle.
- `BranchLocation::find_main` and `find_branch(name)` derive any branch
  location of the dataset from the opened location. That is the fact the
  stored URI copies and then freezes.

Lance's own [table layout](https://lance.org/format/table/layout/) page
("Dataset Portability") says a copied dataset root "will function
immediately at the new location". It names the rewrite of `base_paths` as
the port step for multi-base datasets. A branch under `tree/` is inside the
root, but it breaks that promise. This is worth an upstream Lance issue
(see [Lance change](#does-lance-need-a-change)).

### Hooks considered

| Hook | Verdict |
|---|---|
| `WrappingObjectStore` on base stores (`with_base_store_params`) | Bypassed for local reads (`LocalObjectReader`). It also sees object paths only, not which base they came from. |
| A custom `ObjectStoreProvider::extract_path` in the session registry | The registry is process-wide (`lance_access::STORE_REGISTRY`). Two copies of one backup, open in one process, would need opposite rewrites of the same old prefix. |
| `DatasetBuilder::with_serialized_manifest` | Works, but it re-opens the handle. It loses the resolved `ManifestLocation` (e-tag and size), which the freshness and identity checks compare. |
| A custom metadata-cache backend that rewrites cached manifests | Control sessions have zero-size caches, and an uncached `load_manifest` skips the cache. |
| **Replace `Dataset::manifest` (a public `Arc<Manifest>`) after each checkout** | **Chosen.** It keeps the handle's location, commit handler, session, store params and refs. It clones the cached `Manifest`, so the shared session cache entry does not change. |

## Design

`omnigraph_core::branch_base::follow_opened_root(dataset)`
([`crates/omnigraph-core/src/branch_base.rs`](../../crates/omnigraph-core/src/branch_base.rs))
applies this rule:

1. The rule applies only to a handle whose `find_main()` URI ends in
   `/__manifest`. Table datasets, `__history` and every other dataset keep
   their bases.
2. It considers only dataset-root bases (`is_dataset_root`).
3. A base is a same-dataset branch base only if its recorded URI ends with
   exactly the suffix that its own `name` selects: `/__manifest` for
   `None` (main), and `/__manifest/tree/<name>` for a branch. Any other base,
   such as an external dataset, a data-only base or a mismatched name, keeps
   its recorded URI.
4. Such a base gets `find_branch(name)` of the opened location. Equal URIs
   are left alone, so the rule is idempotent. The handle gets a cloned
   `Manifest` with the new map.
5. Nothing falls back. A rebound file that is missing in the opened root
   fails with the opened root's `Not found`.

The prototype calls the rule at every `__manifest` checkout that returns a
handle used for reads or writes. That is 25 sites in `omnigraph-catalog`
(`layout.rs`, `graph.rs`, `lib.rs`, `branch_detail.rs`, `publisher.rs`,
`retention.rs`, `unpublished.rs`, `unattached.rs`, `legacy.rs` and
`legacy/census.rs`) and 7 in the engine's `db/upgrade.rs`. It does not call
the rule at the existence probe in `branch_control.rs` (after an ambiguous
create), because that probe reads no file. Each call is one
`.and_then(follow_opened_root)` after the checkout's `.await`.

**Liability:** Omnigraph has no single place where it opens a `__manifest`
branch, so a new checkout site that forgets the call would read the old
path again. A shippable version should route every `__manifest` checkout
through one catalog function (`checkout_manifest_ref`). A
`forbidden_apis.rs` rule should then refuse a bare `checkout_branch` or
`checkout_version` on a `__manifest` handle.

### What new manifests persist

The prototype persists nothing new. It does not change the storage format,
and it needs no format stamp.

- **Branch create on a relocated root.** Lance reloads the source manifest
  from storage. The new branch therefore records the opened (new) URI for
  its direct source and copies the stale URIs of older ancestors. The rule
  rebinds both on read.
- **A publish on a rebound branch.** `Manifest::new_from_previous` copies the
  in-memory map. The next version therefore records the opened root's
  absolute URI. That is still Lance's meaning: an absolute, valid path at the
  moment of writing.
- **Existing manifests and clone transaction files** are never rewritten.
  The local e-tag that branch identity includes stays the same.

A root-relative persisted value was considered and rejected. Lance specifies
`BasePath.path` as "an absolute path interpretable by the object store".
`uri_to_url` would read a relative value against the process's working
directory. Stock Lance and every released Omnigraph binary would then
misread the graph, so it would need a format stamp and a Lance change, and
it would still need the reader rule for existing bytes. The reader rule
alone covers old and new bytes.

## Does Lance need a change?

No change is needed for this fix. The hook (`pub manifest`) exists in
13.0.0. A short look at 14.0.2 and 15.0.0-beta.8 should confirm that the
field is still public before a dependency bump; the issue reports the same
`ref_path` assignment in both.

An upstream change is still worth proposing, because Lance's own
portability promise fails for branches. Lance could resolve a dataset-root
base whose `name` is a branch of the same dataset, and whose recorded URI
ends in that branch's `tree/` suffix, from `find_main()` of the opened
location. Or it could record such a base relative to the main location. If
Lance ships either, the Omnigraph rule becomes a no-op on new bytes. It
must stay for existing bytes. This would be a new row in the compatibility
fence table of [lance.md](../dev/lance.md), next to "Branches/tags" and
"Bound branch create".

## Test results

Owner: `crates/omnigraph/tests/integration/branching.rs`,
`relocated_root_serves_retained_branches_from_its_own_files_issue_1233`.
A `.gqt` case cannot copy a root. Fixture: the standard test graph (4
`Person` rows). It has `retained` (unwritten), `written` (+1 row) and
`child` (an unwritten branch created from `written` after that write). Every
read opens a fresh `Omnigraph` handle.

| Case | Before the rule (`main`) | With the rule |
|---|---|---|
| Unwritten retained branch on a copy, original divergent | **fails**: `Not found: …/original.omni/__manifest/data/….lance` (`lance-io` `local.rs:273`) | 4 rows |
| Child of a written branch | fails (same mechanism, issue comment) | 5 rows |
| Branch-only historical snapshot lookup (`ReadTarget::snapshot(written head)`) with unwritten siblings | fails (lookup opens every branch manifest) | 5 rows |
| Still-present but divergent original (it advanced, and its inherited `__manifest` data files are gone) | fails: reads the original | reads the copy |
| Original path removed | fails | all reads pass |
| **No fallback:** a copy missing one inherited file, with the original intact | **returns 4 rows from the original path** (a silent cross-tree read) | fails, naming `missing.omni`, never `original.omni` |
| Write on a rebound `retained`, plus a branch created from rebound `child` | not reached | 5 rows each |
| Same-path restore of the whole-root backup | passes | passes |

The case order follows the test. The "before" column comes from running the
same test with the rule stashed. Those runs stopped at the first failing
assertion (the divergent-original read). The no-fallback run returned 4
rows. The other "before" cells are what the issue comments record for the
same mechanism.

In-source unit tests in `branch_base.rs`:

- `only_same_dataset_branch_bases_follow_the_opened_root` rebinds main and
  branch bases. It keeps a mismatched name, an external `s3://` dataset and
  a data-only base unchanged, and it checks that the rule is idempotent.
- `a_dataset_outside_a_manifest_directory_keeps_every_base` checks the
  `__manifest` fence.

## Gates

All gates ran on the Modal box (gVisor, Linux, running as root). They used
the repository's own selections, with `RUST_MIN_STACK=16777216` as CI sets
it. Without that setting,
`branching::branch_delete_retires_native_parent_and_cleanup_preserves_live_child`
overflows the default stack on `main` too.

| Gate | Command | Result |
|---|---|---|
| Format | `cargo fmt --all --check` | clean |
| Clippy | `cargo dev-clippy` | clean, no warnings |
| Workspace tests | `cargo dev-test --no-fail-fast` | 5,096 passed, 10 failed, 41 ignored. All 10 also fail on `main` in this sandbox (see below). |
| GQT corpus | `cargo test -p omnigraph-gqt -p omnigraph-gqt-core --locked` | 750 passed, 0 failed |
| DST | `cargo test -p omnigraph-dst --locked`, from `crates/omnigraph-dst` | 112 passed, 0 failed, 32 ignored (fleet and hunt instruments) |
| DST storage-cost golden | `dst_bench_cost_count_golden` | passes; `cost_table.txt` unchanged |
| Docs | `python3 scripts/check-docs.py` | OK (447 files) |
| New owner tests | `branching::relocated_root_…_issue_1233`, `branch_base::tests` | pass; the whole `branching::` suite passes, 58 tests |

The 10 workspace failures come from the sandbox, not from this change:

- **Eight `omnigraph-bench` unit tests.** gVisor's `/proc/self/status` has no
  `Cpus_allowed_list`, so machine identity capture fails. The symlinked
  ancestor refusals also are not raised under gVisor.
- **`cli_cluster::managed_lifecycle_capture_and_retry_records_refuse_symlinks_without_reading_targets`.**
  It is the same symlink behavior.
- **`db::upgrade::tests::interrupted::unreadable_legacy_object_asks_for_a_rerun`.**
  The test makes a file unreadable with mode `0o000`, and root reads it
  anyway.

The last two were re-run against `origin/main`'s sources and fail there in
the same way. `cargo deny` and `typos` were not run: neither tool is
installed on the box, and this change edits no dependency.

## Sizing: M or L

**Verdict: L, as an amendment or a short RFC, not a plain M fix.**

It is not L for the usual reasons. It changes no on-disk format, needs no
stamp, rewrites no bytes, needs no Lance change and adds no surface. The
code is about 100 lines plus call sites. It is L for these reasons:

1. **It is a storage-compatibility rule.** Omnigraph would stop treating a
   persisted Lance field, `BasePath.path`, as the authority for a class of
   bases. Omnigraph picks out that class by its path suffix and branch name.
   That conflicts with the spirit of invariant 8 (never reinterpret
   persisted state) and with the rule in [lance.md](../dev/lance.md) that
   names never establish ownership. The provenance argument for it is that
   only `create_branch` ever adds a `__manifest` base. That is a claim about
   every writer, past and future, so a maintainer should accept it as a
   rule, not a reviewer as a code detail. It also adds a delta over stock
   Lance 13 to the compatibility fence table.
2. **It touches accepted RFCs.** RFC 0029 says raw object copying is not
   claimed as a supported migration "unless a separate test proves every
   embedded physical reference remains valid". RFC 0064 says there is "no
   assumed root-copy/rename support". The issue's goal is that a whole-root
   copy opens at a new path. That claims what both RFCs hold back. This
   prototype proves it only for `__manifest` branch bases on a local file
   store. It does not cover legacy forks, external Blobs, index files with
   `base_id`, or S3 and Azure.
3. **The contract needs a stated boundary.** For example: a copy at a new
   path serves every branch whose bases are inside the root, and refuses
   (not reads elsewhere) when a base is outside it. That user-visible
   promise belongs in `docs/user/concepts/storage.md` and the backup
   guidance, and the RFCs above have to agree with it.

An M-sized subset exists if maintainers want to decouple the two. Land the
reader rule only as an enforcement of invariants 3 and 12 (no branch read
leaves the opened root, so the silent cross-tree read is closed), and do not
document relocation support. That still needs the call-site consolidation
and the fence row. The sentence that a copied root can be opened elsewhere
waits for the amendment. A reviewer may reasonably call even this subset L,
because of point 1.

## Risks

- **Rebasing a real external base.** The rule needs all of these: a
  `__manifest` handle, `is_dataset_root`, and an exact name-matched suffix.
  An external dataset whose path ends in `/__manifest/tree/<same native
  name>` would still be rebased. Native names carry a ULID per lifetime, so
  that collision means another graph's tree with the same lifetime. That
  happens only when someone copies a root and then shallow-clones across
  roots by hand, which Omnigraph never does. A mismatched name keeps its
  recorded path. A shippable version should probably fail closed on that
  shape instead of reading the recorded path. That is an open design
  choice.
- **S3 and Azure.** The tests did not run on S3 or Azure. `find_branch`
  builds the URI from the opened location, so a copy into another bucket or
  container gets a base under the opened store's scheme and bucket.
  `base_object_store` builds that store from the rebound URI with the
  dataset's store params. A bound handle's provider options come from its
  registry (see "Bound branch create" in lance.md). A rebound base in a
  different bucket needs the same binding qualification as the root. That
  is RustFS and Azurite evidence for a shippable PR.
- **Legacy pre-v11 table forks.** These are Lance branches of user tables,
  with the same absolute-base mechanism. The rule is fenced to `__manifest`
  and does not fix them. The collector's `borrowed_origins` resolves fork
  bases through the recorded path. On a relocated root it would protect the
  original path's files, not the copy's. A later `cleanup` could then
  delete parent-table files that a retained fork in the copy still borrows.
  Before relocation is claimed, either extend the rule (with the same
  suffix test over the table's directory) to `metadata.rs` opens and to the
  collector, or refuse relocation for graphs that still hold forks.
- **Index files with `base_id`.** These are covered automatically, because
  index resolution uses the same map. `fts_compat.rs` also reads
  `dataset.manifest.base_paths` and sees the rebound map. Production
  `__manifest` has no fragment-reuse index (lance#7840), so no test
  exercised this.
- **Cleanup.** Native tree retention follows the ref parent chain
  (`retain_dependencies`), not `base_paths`, so the copy's `__manifest`
  trees keep their dependencies. The `__manifest` collector paths open
  through the same catalog functions and see rebound bases. `read_memo`
  registers roots from `base_paths` when the root is opened, and the main
  root covers every `tree/` path. A cleanup run on a relocated root was not
  run.
- **Missed call sites.** This is the main maintenance risk, as described
  above.
- **Unaddressed reference classes.** Table-pin `manifest_path` is already
  rebuilt from the root, as before. External Blob URIs stay absolute by
  contract.

## Left for a shippable PR

1. An accepted amendment (RFC 0029 and RFC 0064 wording) or a short RFC
   that states the relocation contract and the base-provenance rule.
2. One catalog function for `__manifest` checkouts, plus a
   `forbidden_apis.rs` guard against bare checkouts.
3. A decision on the mismatched-name shape (fail closed or keep), and on
   legacy forks (extend the rule to tables and the collector, or refuse
   relocation).
4. Object-store evidence: a RustFS copy into another prefix and another
   bucket, and Azurite. Then a cleanup run on a relocated root, with a
   retained branch and a tagged snapshot.
5. A fence row in lance.md, user docs (`storage.md`, the upgrade and
   maintenance backup text, troubleshooting), a `changelog.d` note, and
   reconciling `skills/omnigraph` if it mentions backups.
6. An upstream Lance issue that links the layout page's portability promise
   to `create_branch`'s absolute `ref_path`.
7. Possibly a DST storage-cost line, if a relocated-open scenario is wanted.
   The rule does no I/O, so the existing golden does not move.

## Links

- Branch:
  <https://github.com/ragnorc/omnigraph/tree/proto/1233-branch-base-follows-root>
- Rule: [`crates/omnigraph-core/src/branch_base.rs`](../../crates/omnigraph-core/src/branch_base.rs)
- Regression test: `crates/omnigraph/tests/integration/branching.rs`,
  `relocated_root_serves_retained_branches_from_its_own_files_issue_1233`
