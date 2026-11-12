# Autoresearch: graph index retention across writes (2026-11-12)

Run `run-wf-autoresearch-modernrelay-omnigraph-20261112T0900Z-6ddf54`, box
agent-devbox, Claude Opus 5.5. Base: ModernRelay/omnigraph main
`2bfb3f354528` (2026-11-12). Branch `autoresearch/graph-index-retention-20261112`.

## The cost

A traversal on the CSR path reads its edge tables once to build an in-memory
CSR/CSC topology (`GraphIndex::build`), or loads the persisted artifact
`__graph_index/csr-current.bin` after an `optimize`. The runtime cache keeps
the built topology, keyed by each covered edge table's identity, version,
detached pin, native branch and e_tag (`runtime_cache.rs`,
`GraphIndexCacheKey`). The execution guide says this key exists "so
unrelated edge types do not force a graph-wide scan".

On main, the key's benefit was mostly lost. Every one of these cleared the
whole cache, the shelved artifact decode included:

- an edge insert, at staging time, before publication
  (`exec/mutation.rs`, edge insert arm);
- an edge delete, and a node delete that cascades to edges;
- a merge that changed any edge table (`exec/merge.rs`);
- a publish of an unpublished commit that touched an edge table
  (`db/omnigraph.rs`, `publish_unpublished`);
- a branch create or delete (`branch_create_as`,
  `branch_create_from_impl`, `delete_captured_branch_storage`), which also
  dropped every held table handle (`invalidate_read_caches`).

A server holds one `Omnigraph` per graph and runs every write and read through
it. So on a served graph, any edge write on any branch or edge type, and every
branch create or delete, made the next traversal of every edge type rebuild
its topology from a full scan of its edge tables, or fetch and decode the
whole artifact again. The rule for agent writes ("write on a branch, then
merge") pays it three times per change: create, merge, delete.

Users pay this on ordinary requests. It grows with the size of edge tables the
write did not touch, and it should not grow at all.

What it should grow with: nothing for untouched edge types. For a written edge
type, the next traversal must rebuild that type's topology (the key moved).
For a branch control, nothing outside that branch.

## Why the invalidation was not needed

- The key already identifies the topology's input. Within one table lifetime
  a published `(identity, version, pin, native branch, e_tag)` names
  immutable data. A write makes a new version or a new random detached pin
  (Lance `io/commit.rs`: `rng().random::<u64>() | DETACHED_VERSION_MASK`), so
  the written table's next read misses on its own.
- Loads already wrote edges with no invalidation at all; only mutations and
  merges cleared the cache.
- The shelf (`shelf_serve`) checks every requested table's stamp on each
  serve, so it already stops serving a written table and keeps serving
  untouched ones. Clearing it threw that away.
- Branch controls guarded against a recreated branch reusing a dead
  incarnation's key on stores without e_tags. Since native refs carry a fresh
  incarnation (`branch_names.rs`: `{logical}.{ulid}`), a recreated branch's
  forked tables never share a key with the dead one. Only legacy refs
  (native == logical) can collide, and the new eviction covers them.

Prior art: Lance's own session caches key derived state by immutable
identity and never clear it on write (`session/caches.rs`: `ManifestKey`
is `(version, e_tag)`; `RowIdIndexKey` adds the e_tag because a recreated
dataset restarts its versions and refuses sharing without one). OmniGraph's
key follows the same rule; the clears were left over from when the cache was
keyed by snapshot id.

## Instruments

- GQT `--measure` (DST in-memory store, unit model, and `s3-like`), on
  cases outside the corpus: N Person, 4N `Knows` (uniform), 16 `Likes`,
  8 Company. One handle runs: a friends-of-friends count over all people
  (CSR path), the same query warm, then after each of: a `Likes` insert on
  main, a node-only insert, a branch create, a `Likes` insert on the branch,
  a fast-forward merge of it. N = 4,096 and 32,768. Result rows are checked
  at every step.
- `warm_read_cost::write_keeps_graph_index_of_untouched_edge_tables`: counts
  graph builds (`QueryIoProbes::graph_build_count`) on a forced-CSR
  traversal across the same sequence plus a branch delete and a `Knows`
  insert. It fails on main at the first unrelated write.
- DST golden: new labeled scenario `GraphIndexRetentionCost`.

## Baseline (main) and result

Next `fof` traversal after each step, N = 32,768 (131,072 `Knows` edges).
Requests / bytes read:

| After | main | H1 | H1 + H2 |
|---|---|---|---|
| warm (no write) | 26 / 2.18 MB | 26 / 2.18 MB | 26 / 2.18 MB |
| `Likes` insert on main | 59 / 5.08 MB | 26 / 2.18 MB | 26 / 2.18 MB |
| node-only insert | 26 / 2.18 MB | 26 / 2.18 MB | 26 / 2.18 MB |
| branch create | 67 / 5.10 MB | 67 / 5.10 MB | 26 / 2.18 MB |
| `Likes` insert on the branch | 59 / 5.08 MB | 26 / 2.18 MB | 26 / 2.18 MB |
| merge (fast-forward, `Likes` only) | 59 / 5.08 MB | 26 / 2.18 MB | 26 / 2.18 MB |

N = 4,096 (16,384 `Knows`): warm 5 / 247 KB; on main 10 / 577 KB after an
edge write or merge and 14 / 583 KB after a branch create; with H1 + H2 every
row is 5 / 247 KB.

Under the `s3-like` model (17 ms per request plus bytes at 50 MiB/s) the
32,768 traversal takes 73.7 ms warm and 250.7 ms when it rebuilds (measured
after a branch create on H1, 67 requests and 5.10 MB; main's rebuild after
an edge write is the same scan, 59 requests and 5.08 MB, not run separately
under this model). With H1 + H2 every row is 73.7 ms.

The branch write itself also got cheaper with H2: the `Likes` insert on the
branch made 54 requests (603.1 ms modeled) and now makes 52 (568.1 ms),
because main's held handles survive the branch create. At 4,096: 38 → 36.

Growth: on main the extra cost is a full scan of the traversed edge tables,
so it grows linearly with their size (577 KB → 5.08 MB for 8× the edges)
while the write touched none of them. With the change it is flat, equal to
the warm query.

The writes themselves cost the same (identical rows for every write step).

## Hypotheses

| # | Change | Counters | Gates | Outcome |
|---|---|---|---|---|
| H1 | A mutation, merge or unpublished-commit publish evicts only the scoped indexes and failed-load verdicts that cover a written edge table; keeps the shelf (`RuntimeCache::invalidate_edge_tables`, `LruMap::retain`) | 32k: 59 → 26 requests, 5.08 → 2.18 MB after an unrelated edge write, a branch edge write or a merge; DST golden unchanged | new integration test red on main, green; workspace tests; DST golden | kept (`432abb44`) |
| H2 | A branch create or delete evicts only the handles, scoped indexes, verdicts and shelf on that branch's own refs (`invalidate_read_caches_for_branch`, `native_ref_of_branch`) | 32k: 67 → 26 requests, 5.10 → 2.18 MB after a branch create; branch write 54 → 52; DST `AddFriend` l.get 49 → 39 | integration test extended (create and delete keep main's topology); recreated-branch tests; `forbidden_apis` updated to require the branch-scoped call | kept (`f4d64836`) |

DST golden lines that moved, each explained in `da781dec`:

- `AddFriend l.get 49 → 39`: edge inserts after a branch control reuse held
  handles (10 detached-manifest GETs fewer).
- `_verify a.get 10 → 9`: one artifact load fewer; the shelf survives a branch
  control.
- `_verify l.get 1385 → 1387`: the verify pass opens more table versions than
  the 64-handle cap; with branch controls no longer emptying the cache the LRU
  evicts differently: one more reopen of two tables, one fewer of a third.
  Traced per path with `arm_with_paths`.
- New rows `CsrHop*`: main gives `AfterOtherEdgeWrite 5`, `AfterBranchCreate
  9`; now both 4, equal to `CsrHopWarm`. H1 alone left the existing golden
  unchanged.

## Not changed, and proposals

- A refresh after another process's commit still clears everything
  (`resolve_target_inner`, `refresh`). That is the remaining fallback for
  e_tag-less stores when a branch is replaced under a handle; an earlier run
  noted it as "H4". The same reasoning (fresh incarnations, random pins)
  suggests it could clear only on a new manifest lifetime, but that needs its
  own review of replaced roots.
- A write to the traversed edge type still rebuilds that type's whole CSR.
  Proposal (not built): keep the CSR per edge table and apply the write's
  delta (appended rows, deletion vectors) to the cached one, as LiveGraph
  (PVLDB 13, 2020) and graph-HTAP delta stores do. On a hot edge type this is
  the next cost: a one-edge insert costs a full table scan on the next
  traversal.
- Dead branches' entries for fork refs (`fork.{incarnation}...`) stay until
  LRU evicts them; their keys cannot collide, so this is memory only, bounded
  by the caps.

## Timing

No qualified benchmark ran. GQT `s3-like` virtual time is in the table. Box
wall clock (gVisor, 2 cores) was not used for claims.

## Gates

All on the branch tip, debug builds, `RUST_MIN_STACK=16777216`:

- `cargo dev-test --no-fail-fast` (canonical CI test graph): 5,310 passed,
  44 ignored, 11 failed. One was mine: the issue-859 handle-count test
  assumed a branch create empties the cache; it now counts the base handle
  that outlives the create (`ba1154b9`), and the `runtime_cache::` tests pass.
  The other 10 do not touch this change and fail on this box from its
  environment (it runs as root under gVisor): 8 in `omnigraph-bench`
  (symlink refusals, machine identity, archive and real-graph runs), the CLI's
  `cli_cluster::managed_lifecycle_capture_and_retry_records_refuse_symlinks_without_reading_targets`,
  and `db::upgrade::tests::interrupted::unreadable_legacy_object_asks_for_a_rerun`
  (needs a non-root user, as in earlier runs). All 10 fail the same way on main `2bfb3f35` on this box.
- New `warm_read_cost::write_keeps_graph_index_of_untouched_edge_tables`
  fails on main (`(["Alice"], 1)` where 0 builds are expected after a
  `WorksAt` insert) and passes here, with the other 32 `warm_read_cost` tests.
- `forbidden_apis` passes with the branch-scoped call required in
  `branch_create_as`, `branch_create_from_impl` and
  `delete_captured_branch_storage`.
- GQT corpus (`cargo test -p omnigraph-gqt`): 566 passed, including the
  reference-engine parity steps.
- DST suite (`crates/omnigraph-dst`, `cargo test`): 114 passed; golden
  regenerated with each changed line explained.
- `cargo fmt --all --check`, `cargo clippy --workspace --all-targets` and
  `cargo clippy -p omnigraph-gqt --all-targets` with `-D warnings -W
  clippy::dbg_macro`: clean.
- `scripts/check-docs.py` (pinned docs environment) and
  `scripts/check-agents-md.sh`: OK. `typos` is not installed on the box.

## Lessons taken from past runs

- 2026-11-05 (failed): start from a byte counter. Done: GQT `--measure`
  bytes showed the growth at once.
- PR #1285 (closed): no second reader of Lance's private layout. This change
  touches only OmniGraph's own caches.
- PR #1318 review: a rule counting one thing (fragments) missed the cost
  that mattered (bytes). This run reports requests and bytes for every row.
- Vectorized and keyed-write runs: debug-build stack overflows; tests ran
  with `RUST_MIN_STACK=16777216`.
- Earlier warm-read run left H4 (keep handles across refresh) unattempted;
  this run takes the write and branch-control half of it and leaves the
  refresh half as a proposal.
- New this run: the `dev-test` alias already passes `--workspace`, so `-p`
  does not narrow it; and `pkill -f <pattern>` from the agent shell kills
  the shell itself when the pattern is in its own command line.

## Sources

- OmniGraph: `docs/dev/execution.md` (topology cache), `docs/dev/invariants.md`
  (invariant 7, derived state), `runtime_cache.rs`, `graph_index/persist.rs`,
  `branch_names.rs`, `handle_cache.rs`.
- Lance 13.0.0 source: `session/caches.rs` (`ManifestKey`, `RowIdIndexKey`),
  `io/commit.rs` (random detached versions).
- Added this run: LiveGraph, Zhu et al., PVLDB 13(7), 2020
  (https://vldb.org/pvldb/vol13/p1020-zhu.pdf); "Adaptive update handling for
  graph HTAP", Distributed and Parallel Databases, 2023
  (https://doi.org/10.1007/s10619-023-07428-y).

## What could not be verified

- Real S3 latency and wall clock.
- The persisted-artifact path (after `optimize`) was not measured with
  GQT, which has no optimize step. The DST golden's `_verify a.get 10 → 9`
  shows one fewer artifact load; the size of a full-artifact GET grows with
  every edge in the graph, so the saving there is larger than the scan path
  on graphs whose artifact is current.
- Peak memory: retained indexes stay within the existing caps (8 scoped
  entries, one shelf, 64 handles / 64 MiB).
