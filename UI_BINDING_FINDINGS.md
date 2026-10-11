# UI components bound to interfaces: gaps in the polymorphic types RFC

Review of [RFC 2026-10-07](https://github.com/ModernRelay/omnigraph/pull/884) against one consumer
it was not designed around: forkable templates whose UI components (calendar, kanban, timeline,
inbox) bind to stored queries over interfaces, read the catalog route, edit through interface-wide
`update`, and identify rows by (`@type`, `@id`).

**Sources.**
- main: `origin/main` at `80b3cfeb`, read in the `rfc-polymorphic-types` worktree. Paths marked
  **M** are relative to it.
- Prototype: `proto/polymorphic-types` at `74cb7a8f`, plus two uncommitted changes. One is the probe
  file. The other runs union-scan arms concurrently (`buffered(16)`, order kept) in
  `execute_abstract_scan`. Paths marked **P**.
- Pinned substrate: Lance 11.0.0, lance-index 11.0.0, arrow-ord 58.3.0, DataFusion 54.0.0.
  Paths marked **L** are relative to `~/.cargo/registry/src/index.crates.io-1949cf8c6b5b557f`.
- Personal graph schema, read-only: 76 node types, 75 of them with `slug: String @key`, no
  interfaces, 426 edge types.

**Probes.** All in `crates/omnigraph/tests/ui_consumer_probes.rs`, run against the prototype:

```bash
cargo test -p omnigraph-engine --test ui_consumer_probes -- --nocapture --test-threads 1
```

Outputs below are quoted from `PROBE` lines. Claims marked *code* were read from source and not
executed. Four of them concern main features the prototype predates (`ScanAccess`,
`materialize_returns_late`) and would surface only after a rebase.

## Validation round, 2026-10-11

The prototype was merged with main at `80b3cfeb` (`9d2a64b1`, `8f9f231d`) and the probes were
rerun, so the claims marked *code* above now have running evidence:

- **Late materialization (Q4): confirmed, and broader than predicted.** Every ordered, limited
  interface query fails on main with "`HydrateColumns` reads `node:Schedulable`, which is no node
  type". It is not limited to vector or struct columns. The same query on one member works.
- **Index access per arm (Q6): confirmed.** `explain` records `"access":"sequential"` for the
  interface scan; the same filter on `Meeting` records `"access":"index_probe"` on `rank_idx`.
- **A new single-table shortcut (Q4/Q6).** On main, every unranked table scan takes the
  pipelined read, which resolved `node:Identifiable` and failed. The prototype now keeps abstract
  scans off that path. The RFC therefore requires a union-scan plan node, not interception.
- **Generalization (P7): reversed.** Main's metadata-only evolution adds the tag column as nulls;
  the RFC returns to null tags read through an implicit type, now proven end to end.
- **Request counts (Q8), partly measured.** With the repo's cost harness, a cold 75-member lookup
  made 75 table opens, and a warm repeat reopened 55 to 61 tables (none at 60 members): the
  64-entry handle cache thrashes. Reads made inside the union arms are not attributed to the probe
  scope (it counted 0 reads for a cold full scan), so per-arm read counts on S3 remain estimates.
  A cold single-type keyed lookup in a fresh process: 1 open, 2 reads, 7.5 to 10 ms locally;
  the 75-member lookup: 37 to 54 ms.
- **Spot-checked on `origin/main`:** T54 taken (`codes.rs:71`); `search`/`fuzzy`/`match_text`
  are scan membership filters (`engine/search.rs:690-744`); `rrf` has exactly two arms
  (`optimizer.rs:2858`); `PolicyRequest` has no type field and `predicate_for` is planned
  (`crates/omnigraph-policy/src/lib.rs:288-292`, `976-984`); cluster preflight validates queries
  (`deployment/execution.rs:793`); mutations are lowered before parameters bind
  (`exec/mutation.rs:1192`).
- **Suites on the merged prototype:** compiler 471, planner 115, engine library 468, engine
  integration 573 (with CI's `RUST_MIN_STACK=16777216`; without it one test overflows the default
  stack, as it would on main), prototype 13, and all 333 GQT cases (two after a rerun at timeout).

## Summary

| # | Question | Verdict | One line |
|---|---|---|---|
| 1 | Result typing | holds for interfaces, gap in the RFC text and for unions | Members must match the interface type exactly, so an interface column never changes. The RFC's "joined types" wording and union joins over the concrete closure can change it. A redeclared key property silently loses `@key`. |
| 2 | Interface-property migrations | gap | main already fans out add, drop, rename and enum-widen to every member in one publication; the RFC's table covers none of it. Drop is silently destructive across N tables. |
| 3 | `@type` vs StableTypeId | gap | Renames keep the id, but nothing exposes it; nothing exposes a schema hash either. |
| 4 | order, limit, pagination | holds for order+limit; gap for tie-break and late materialization; pagination not covered | The global Sort stays above the union. Ties break on `__id` with an unstable sort. GQ has no cursor or parameterised limit. |
| 5 | Group, order, filter by `@type` | grouping holds; filtering and bare `count` are gaps | `$e.@type = "Task"` is pushed into member scans and fails. A bare `count($e)` fails. |
| 6 | Range filters and indexes | holds in the prototype; gap on main after a rebase | Each arm uses its own index. main's per-table access decision would turn indexes off for every arm. Planner statistics return nothing for an interface. |
| 7 | Search across members | partly holds | BM25 refusal is right. `search`/`fuzzy`/`match_text` are boolean filters and can run per arm. `rrf` fuses by bare id, so colliding ids merge. |
| 8 | Wide interfaces | gap | A 75-member keyed lookup costs about 10x a single type locally. The S3 estimate is 0.8 to 8 s cold, depending on concurrency. The handle cache holds 64 tables. |
| 9 | Writes from components | commit cost holds; pruning is a gap | Zero-match members commit nothing. Every member is still opened and scanned; nothing prunes by `@type`. |
| 10 | Catalog route for forms | gap | No route returns property types, nullability, enums, `@key` or annotations. There is no schema version or ETag. |
| 11 | Live views and membership | gap | Expanding at cursor creation freezes membership. Adding `implements` produces no change rows. |
| 12 | "Depends" for remove-implements | gap | Undefined. A typecheck-based rule is checkable in cluster preflight today. |
| 13 | Per-type policy | gap | The planned row filtering silently drops a denied member's rows. |
| 14 | Shared core name collision | gap | `interface Event` and `node Event` coexist today. Renaming the node and reusing the name for an interface silently changes stored queries. |

## Result typing and stability

### 1. Column types of `$x.p` (holds for interfaces; gap in the RFC text and for unions)

**Evidence.**
- main requires an implementor's property to match the interface's type exactly. The parser copies
  the interface property into a node that lacks it. If the node declares it, the parser compares
  the whole `PropType` (scalar, list, nullability, enum set) (M
  `crates/omnigraph-compiler/src/schema/parser.rs:586-640`, comparison at `612-622`;
  `types.rs:172-177`). The IR check repeats this (M `catalog/schema_ir.rs:1636-1646`). Probe:
  - `q1.member_nullable_where_interface_required: refused: node A property 'title' has type String? but interface I declares it as String`
  - `q1.member_enum_where_interface_string: refused: … has type enum(done, todo) but interface I declares it as String`
  - `q1.member_other_scalar: refused: … has type I64 but interface I declares it as I32`
- The prototype types an interface binding from the interface declaration (P
  `crates/omnigraph-compiler/src/catalog/mod.rs`, `binding_node_type`), and casts each arm to it (P
  `crates/omnigraph/src/engine/scan.rs`, `conform_member_batch`).
- Adding an implementor leaves a stored view's schema unchanged. Probe, before and after applying
  `node Deadline implements Schedulable`:
  - `q1.before: shape=[type:Utf8, id:Utf8, title:Utf8, start:Date64, end:Date64?]`
  - `q1.apply_new_member: ok`
  - `q1.after: shape=[type:Utf8, id:Utf8, title:Utf8, start:Date64, end:Date64?]`
- The RFC never states the type of `$x.p` for an interface binding. The Queries table says only
  when it is legal. The union-scan bullet says "Each arm is cast to the binding's joined types",
  which reads as a join across members.
- Unions: under the alternation rule (nullable if any member is, enum domains combined, enum with
  String gives String; main's edge version is at M `crates/omnigraph-compiler/src/traversal.rs:68-101`),
  joining over the concrete closure makes membership matter. With
  `$x: Task | Schedulable`, where `Task { start: DateTime, kind: enum(todo, done) }` and the
  interface declares `start: DateTime?, kind: String`, the result types depend on whether
  `Schedulable` has members yet. Adding the first implementor turns `start` nullable and `kind`
  into `String`, and makes `$x.priority` a type error.
- `return { $x }` changes shape when a one-implementor interface gains a second member, if a
  singleton interface counts as a "concrete binding".
- **Found while probing, on main today:** a member that redeclares the key property silently loses
  the interface's `@key`. The node's own declaration wins, and interface constraints are rebuilt
  only for copied properties (M `catalog/schema_shape.rs:182-190`, `215-219`). Probe, loading the
  same row twice in merge mode:
  - `q1.key_inherited: rows=1`
  - `q1.key_redeclared: rows=2`

  A component keyed on (`@type`, `@id`), and an `Entity { slug: $slug }` lookup, then see
  duplicates. The RFC's phase 0 plans only a warning.

**Proposed RFC text** (Queries, after the forms table):

> An abstract binding's property has exactly the type declared on the interface, or inherited by
> it, whatever the member set. In a union, each declared member contributes one type: a node type
> its property type, an interface its declared type. The alternation rule joins these declared
> types, never the concrete closure. Adding or removing an implementor therefore never changes a
> query's result schema, its legality, or the shape of `return { $x }`. An interface with one
> implementor is still multi-member for `return { $x }`. Until phase 4, a satisfying property must
> have the identical type. A phase 4 covariant member is cast to the interface type in its arm:
> covariance narrows what a member stores, never what the binding returns.

And in phase 0, replace "Warn when a redeclaration drops an interface constraint" with:

> Refuse a redeclaration that drops an interface `@key` or `@unique`. A member may add constraints,
> never remove inherited ones.

### 2. Interface-property migrations (gap)

main already handles most of these by fanning the change out to every implementor in one
publication. The RFC's migration table covers only type-level steps. Probe output (`q2.*`), with
main's code:

| Change | main today | Evidence |
|---|---|---|
| Add a nullable interface property | Supported: `AddProperty` on the interface and on each inheriting node, in one publication. Each node `AddProperty` is a full table rewrite. A member that already declares the property is refused ("changing interface-property satisfaction links"). | `q2.add_nullable_interface_property: supported=true`; M `schema_plan.rs:590-597`, `689-704`; `schema_apply.rs:935-945` |
| Add a required one | Refused, OG-MF-103. This applies even when the interface has no implementors, though an interface has no table, so that refusal is false. | `q2.add_required_interface_property: supported=false`; M `schema_plan.rs:598-612` |
| Remove an interface property | Supported: `DropProperty` on every inheriting node. The column silently disappears from N tables. | `q2.remove_interface_property: supported=true` |
| Rename (`@rename_from` on the interface property) | Supported: `RenameProperty` fans out. A member that redeclares the property needs its own hint. | `q2.rename_interface_property: supported=true`; M `schema_ir.rs:722-760`, `784` |
| Nullability or type change | Refused, OG-MF-106. A pure enum widening emits `ExtendEnum` per member. | `q2.interface_property_to_nullable: supported=false`; M `schema_plan.rs:625-648` |
| Member with a conflicting type | Refused at parse. Conflicting annotations are ignored (see the `@key` finding). | `q2.member_redeclares_conflicting: … has type I32 but interface Named declares it as String` |
| Add `implements` to an existing node | Refused today: "changing implemented interfaces … not supported". The RFC marks it supported. | `q2.add_implements_to_existing_node: supported=false` |
| Rename an interface | Refused, and unwritable: the grammar allows no annotation on an interface. | M `schema_plan.rs:360`; `schema.pest:13` |

**Proposed rows** for the RFC migration table:

| Step | Tier | Physical effect |
|---|---|---|
| Add a nullable interface property | supported | `AddProperty` on each inheriting member, in one publication. A member that already declares it identically gains a link (metadata only). |
| Add a required interface property | refused (OG-MF-103) | Metadata only when every member already declares it identically, or there are no members. |
| Remove an interface property | destructive, per member, each named in the plan | Members that redeclare it keep their column. Refused while a stored query reads it through the interface. |
| Rename an interface property | supported | `RenameProperty` on each member. Members that redeclare it need a matching `@rename_from` in the same migration. |
| Widen an enum interface property | supported | `ExtendEnum` on each member. |
| Other type or nullability change | refused (OG-MF-106) until phase 4 | |
| Constraint or annotation change | follows the node-property rule, per member | A member may not drop an inherited `@key`/`@unique`. |

### 3. Expose a stable type id (gap)

**Evidence.**
- A rename keeps the `StableTypeId` and the table incarnation (M `catalog/schema_ir.rs:637-711`;
  test `explicit_renames_preserve_type_property_and_incarnation_ids`, `2044-2062`).
  `schema_ir_hash` is a SHA-256 of the IR JSON, names included, so a rename changes it (M
  `schema_ir.rs:1229-1234`).
- The server exposes neither. `StableTypeId` and `schema_ir_hash` do not appear in
  `crates/omnigraph-server` or `omnigraph-api-types`. `schema_contract_digest` is used only
  internally (M `crates/omnigraph-server/src/serving.rs:50,94`). The only ETag is on the blob route
  (M `handlers.rs:842-890`).
- The id leaks only by accident. `GET /snapshot` returns `dataset_path`
  (`nodes/{stable_table_id:016x}-{incarnation:016x}`, M `crates/omnigraph-catalog/src/lib.rs:1086-1101`).
- A component that routes or caches by `@type` loses its cache on every rename, and cannot tell
  which schema version a name belongs to.

**Proposed RFC text** (Wire, API and descriptor):

> `$x.@type_id` is an opt-in meta field, emitted only when projected, like `@type`. It is the
> concrete type's StableTypeId as a decimal string. The catalog route gives each type its
> `type_id` (decimal string) and a top-level `schema_identity_domain`, since ids are unique only
> within one graph. The catalog route and every read response carry the schema hash, as a field and
> as an `ETag`, so a client can key cached rows by `type_id` and invalidate on hash change.

## Reads that views depend on

### 4. order, limit and pagination across a union (order+limit hold; tie-break and late materialization are gaps; pagination not covered)

**Evidence.**
- *Holds.* Order and limit are never pushed into a Lance scan. `resolve_query` builds
  `Projection → Sort{fetch: limit} → Limit`, and `ScanSpec` has no order or limit field (M
  `crates/omnigraph-planner/src/optimizer.rs:242-280`; `logical.rs:379-393`). A Sort is never
  dropped because its input is ordered. The union scan is a leaf, so the global top-k sees every
  arm. Probe: `q4q5.first_two` returned Task t2 (08:00) then Meeting m1 (09:00), the correct
  global first two across members.
- *Gap: tie-break.* Ties break on each binding's `__id`, plus `~edge_type` only for edge selections
  (M `optimizer.rs:681-716`, `744-790`). The prototype leaves that unchanged (P `optimizer.rs:666`).
  The engine sorts with `lexsort_to_indices`, which is unstable (L
  `arrow-ord-58.3.0/src/sort.rs:977`). Person "alice" and Organization "alice" with equal sort keys
  then have no defined order, and a limit picks either. The probe `q4.by_rank_limit` was the same
  in three runs, but that came from the concatenation order of the arms, not from a rule.
  Concurrent arms would remove even that.
- *Gap after a rebase: late materialization.* On main, `materialize_returns_late` strips some
  returned columns from an order+limit scan and refetches them by row address from
  `spec.table` (M `optimizer.rs:1905-2110`, `2096`; `engine/operators/hydrate.rs:304`) *(code)*. For
  a union that table key has no dataset, and row addresses from different member tables collide.
  It always fires for a union, because the row estimate is unknown (`:2082`).
- *Not covered: pagination.* `limit` takes an integer literal (M
  `crates/omnigraph-compiler/src/query/query.pest:144`). There is no offset and no cursor on
  `QueryRequest` or stored-query invocation (M `crates/omnigraph-api-types/src/lib.rs:880-906`,
  `1070-1088`). Keyset paging exists only for change feeds (`Predicate::IdAfter`, M
  `optimizer.rs:1278-1300`).

**Proposed RFC text.**

> The total order of an abstract binding is its `order` keys, then `@type`, then `@id`. The engine
> adds the last two as tie-breaks, as it adds `~edge_type` for edge selections. Late
> materialization by row address is per arm: each hydrated row carries its arm, and refetch goes to
> that member's table.

**New unresolved question: paging over a union.** A stable cursor needs four things:
1. the total order above;
2. the snapshot pinned in the cursor;
3. a limit that can be a parameter;
4. a keyset predicate made concrete per arm. For member T the type comparison folds to a
   constant: `T = t0` gives `k > k0 or (k = k0 and id > i0)`, `T > t0` gives `k >= k0`, and
   `T < t0` gives `k > k0`. Each arm therefore stays pushable to Lance without `~node_type`.

Key values must be encoded with the engine's null placement. Should GQ gain `after $cursor`, or
should the server add opaque cursors to stored-query invocation?

### 5. Grouping, ordering and filtering by `@type` (grouping holds; filtering and bare count are gaps)

**Evidence.** Probes:
- `q4q5.count_by_type: rows=[{"n":2,"type":"Meeting"}, {"n":2,"type":"Task"}]`. Grouping by an
  interface property also works: `q4q5.count_by_rank`.
- `q4q5.order_by_type`: works.
- `q4q5.only_tasks: ERROR Schema error: Unable to get field named "~node_type". Valid fields: ["__id", "endsAt", "rank", …]`.
  The filter lowers to `PropAccess(e, "~node_type")`. `filter_pushable` does not look at the
  schema (P `engine/plan_source.rs:343-346`), so it is pushed into every member's Lance scan.
- `q5.bare_count: ERROR node type 'Schedulable' does not exist at this snapshot`. Aggregate
  pushdown turns it into one metadata count on `node:Schedulable` (P `optimizer.rs:1585-1626`;
  `engine/operators/metadata_count.rs:85-91`). The RFC says a member-wise sum, which the prototype
  does not do. `q5.filtered_count: rows=[{"n":2}]` works.
- *Code:* there are four more ways `~node_type` reaches Lance:
  1. an expansion's destination filter, into `hydrate_nodes` (P `operators/scan/input.rs:293`);
  2. a hash-join build scan;
  3. the contains-join pipelined path, which opens `node:<Interface>` (P `pipelined.rs:61-71`);
  4. on main, `index_split` (M `plan_source/scan_access.rs:23-51`).

**Proposed RFC text** (Pruning bullet):

> `~node_type` is never pushed into a member scan. Before predicates are placed, every reference to
> it folds to the arm's constant type name. A conjunct `@type = v` or `@type in [...]` removes arms,
> after parameters are bound, so `@type = $type` prunes too. Any other predicate on `@type` is
> evaluated on that constant per arm. `filter_pushable` refuses query-only columns. An unfiltered
> `count($x)` sums one `MetadataCount` per member.

### 6. Range filters and indexes (holds in the prototype; gap on main after a rebase)

**Evidence.**
- In the prototype each arm opens its own dataset, and Lance plans the filter against that
  dataset's indexes (P `engine/scan.rs:62-63`; L `lance-11.0.0/src/dataset/scanner.rs:2803-2804`).
  A member without the index silently runs a filtered scan. Probes:
  - `q6.after_ten` (a DateTime range across members) returned the right rows.
  - `q6.partial_index` (`@index` on `Meeting.rank` only) returned both members' matches.
- *Code, main after a rebase:* index facts and the scan-access decision are keyed by the scan's one
  table key (M `plan_source/scan_access.rs:33`, `58-59`). For `node:Schedulable` there are no
  facts, so the decision is `Sequential`. It replays as `use_scalar_index(false)` on every arm (M
  `engine/lower.rs:207-212`; `engine/scan.rs:245-246`), and explain reports `sequential`.
- Planner statistics for an interface key are all `None`: `table_data_bytes`,
  `column_data_bytes` and `row_count` (P `plan_source.rs:245-256`). The virtual type has
  `key: None, indices: []`. Dependent scans always use id lookup, hash joins are never chosen, and
  nothing counts as "bounded by key", although the RFC's Cost bullet says it should.

**Proposed RFC text** (Planning and execution):

> Index facts, scan-access decisions and statistics are per arm. Each arm's filter resolves through
> that member's satisfying property. A member without an index on the filtered property scans; the
> others use theirs, and explain shows the access path per arm. The estimate for an abstract scan
> is the sum of its arms.

### 7. Search across members (partly holds)

**Evidence.**
- BM25 statistics are per index (document count, average length, IDF from one index's partitions:
  L `lance-index-11.0.0/src/scalar/inverted/scorer.rs:166-191`, `237-239`). The investigation
  measured the same document scoring 0.211 in one dataset and 0.826 in another. The refusal is
  right.
- `search`, `fuzzy` and `match_text` produce no scores. They are boolean membership filters (M
  `engine/search.rs:731-748`), so they can run per arm wherever the member has an inverted index.
  Refusing them across members is stricter than needed.
- `nearest` distances are comparable across members. Every vector index is `ivf_flat(…, L2)` (M
  `table_store.rs:2549`, `4565`), and flat search also defaults to L2 (L `scanner.rs:5227-5231`).
- `rrf` takes exactly two arms over one pipeline (M `optimizer.rs:2801-2880`), and fuses by the bare
  id string (M `engine/graph.rs:95-135`). Fusing across members would merge Person "alice" with
  Organization "alice".

**Evaluation.**
- *Per-arm score normalisation* (min-max, z-score): unstable. Scores change with k, with the
  filter, and between pages. Not recommended.
- *rrf across arms:* uses ranks, so score scale does not matter. It extends the existing operator
  to N arms, with (type, id) identity. Its cost: a weak top hit in a small table ranks level with a
  strong one in a large table.
- *A derived projection (phase 4):* gives true global BM25. It costs a maintained copy and
  migration work.

A search component need not wait for phase 4:
- `nearest` over an interface works as the RFC specifies (same model and dimension on every member);
- boolean text filters can run per arm;
- lexical ranking across types needs N-arm rrf, a phase 2 or 3 item, or per-type tabs.

**Proposed RFC text.** Narrow the refusal to `bm25`. Admit `search`, `fuzzy` and `match_text` per
arm when every member has an inverted index on the property. Add to phase 3: "`rrf` over N arms
with (type, id) identity".

### 8. Cost of wide interfaces (gap)

**Measured on local disk.** These are 75 members implementing `Entity { slug @key }`, one row each,
indexes built. Medians are of 5 cold opens and 50 warm runs, at load average about 25:

| Lookup | cold | warm |
|---|---|---|
| `$x: Entity { slug: $s }`, sequential arms (prototype) | 38-47 ms | 28-37 ms |
| same, 16 concurrent arms | 25-28 ms | 20-23 ms |
| `$x: T40 { slug: $s }` (one type) | ~3 ms | ~2 ms |

That is about 0.3 ms of fixed overhead per arm even with nothing to read. Earlier runs at load
averages of 100 to 300 measured 0.5 to 0.9 s, so contention multiplies the per-arm cost.

**On S3: an estimate, not a measurement.** *Code* evidence:
- A cold open of a detached-commit table is a HEAD plus a GET of the manifest. A cold open plus
  planning costs about 2 manifest requests (M
  `engine/plan_source/scan_access/planning_cost.rs:40-47`).
- A BTREE equality probe reads `page_lookup` plus one page. The pages are cached per index in the
  session (L `lance-index-11.0.0/src/scalar/btree.rs:1648-1685`; `lance-11.0.0/src/index/scalar.rs:588-602`).
- The table-handle cache holds 64 handles (M `crates/omnigraph-core/src/handle_cache.rs:80`). A
  75-member scan therefore evicts its own handles, and every "warm" query reopens tables. The local
  run shows the same direction: warm was 15-18 ms at 60 members and 25-30 ms at 75.

The estimate assumes about 20 ms per S3 request and the hosted server's 0.3 s floor:

| | sequential | 16-way |
|---|---|---|
| cold (5 dependent requests per member) | about 7.8 s (4-11 s) | about 0.8-1.0 s |
| warm, handle cache 64 < 75 members | about 3.3 s | about 0.5 s |
| warm, handle cache ≥ members | about 0.45-0.55 s | about 0.35-0.4 s |

(Aside: the repo's `warm_read_cost.rs:34-65` asserts that a warm read does not reread `__manifest`.
That conflicts with the note that the hosted server reloads it on every request. Check which binary
the server runs.)

**Recommendation.** Entity-wide keyed lookups can be usable without a global key index if the RFC
requires all of the following:
- bounded concurrent arms (the prototype now does this);
- a handle cache sized to the graph's table count, not 64;
- per-arm key-to-`__id` narrowing, which main has for single String keys;
- index coverage maintained on served writes, so per-member unindexed tails stay small.

A derived key → (type, incarnation) table pays off only for slug-only routing on a hot path, or for
interfaces with hundreds of members. If it is added, build it lazily per manifest version with a
coverage watermark, so it is a cost-only derivative (invariant 7). The RFC should say explicitly how
this differs from the id-to-type registry it rejects as maintained parallel truth.

**Proposed RFC text** (Cost):

> An abstract binding with N members opens and probes its arms concurrently, bounded by the query's
> I/O concurrency. The table-handle cache holds at least every table a single query may open. An
> acceptance threshold: a keyed lookup on a 75-member interface on S3 completes within 2× a single
> keyed lookup when warm.

## Writes from components

### 9. Pruning and commit cost (commit cost holds; pruning is a gap)

**Evidence.**
- A member that matches nothing stages nothing. `execute_update` returns before `append_batch` on
  zero rows, and a fully zero-match query takes the no-op branch, with no Lance commit and no
  manifest publish (M `crates/omnigraph/src/exec/mutation.rs:1506-1510`, `1036-1056`). Probes, using
  a two-statement update in which only Person matches:
  - `q9.versions: Person 9611…->10544… Organization 14326…->14326… manifest 2->3`
  - `q9.zero_match_single: result=Ok(0) … manifest 3->3`
- So an N-member update that matches one row costs one Lance commit and one manifest CAS (M
  `exec/staging.rs:1024`; `db/omnigraph/table_ops.rs:1303-1308`). It still opens and scans all N
  members.
- Nothing prunes. Mutations do not go through the planner: `execute_update` takes one type name (M
  `mutation.rs:1369-1393`). The RFC's pruning bullet is about reads. `@type = $type` is a
  parameter, bound after lowering (M `mutation.rs:1010`), so plan-time folding cannot see it.
- Opening N tables also widens the window in which a concurrent commit returns `ReadSetChanged` to
  a drag-to-reschedule (M `mutation.rs:1016-1023`).

**Proposed RFC text** (Mutations and loads):

> An interface-wide `update`/`delete` resolves its arms after binding parameters. A conjunct
> `@type = <literal|param>` or `@type in [...]` removes non-matching arms before any dataset is
> opened. A bound `@type` that is not a member of `I` is a typed refusal, not zero rows. A member
> whose predicate matches nothing stages nothing, and a query in which no member matches publishes
> no commit. An N-member update costs one Lance commit per matching member and one manifest CAS.

### 10. Catalog route for a generic create form (gap)

**Evidence.** The routes that return schema information today:

| Route | Shape |
|---|---|
| `GET /graphs/{id}/schema` (M `handlers.rs:1890-1928`) | `{schema_source, system_columns?}`: raw `.pg` text |
| `GET /graphs/{id}/snapshot` (M `handlers.rs:713-760`) | per dataset: kind, name, path, version, count |
| `GET /graphs/{id}/queries` (M `handlers.rs:1842-1888`) | stored-query params only |
| `POST /query` | `columns`: names only |

None returns, in structured form, per-property types, nullability, enum domains, `@key`,
`@description`/`@instruction`, or interface membership. A client must parse `.pg`, and there is no
schema version or hash.

**Proposed RFC text** (catalog route). Add these fields, all already held by the compiler
(`PropType{scalar, nullable, list, enum_values}`, M `types.rs:172-177`; `NodeIR.annotations/implements/constraints`,
M `schema_ir.rs:400-408`):
- per type: `type_id` (decimal string), kind, implemented interfaces, and type-level
  `@description`/`@instruction`;
- per property: name, scalar kind, list, vector dimension, nullability, enum values, and a flag for
  `@embed` or generated fields a form must not ask for;
- per property, annotations as well;
- `@key` and `@unique` tuples;
- per interface: properties, inherited interfaces and members;
- per edge: cardinality, properties and expanded endpoint sets;
- top level: `schema_identity_domain` and the schema hash, also sent as `ETag`.

## Live views and lifecycle

### 11. Change-feed membership (gap)

**Evidence.**
- The cursor is client-held and stores only a `filter_digest`, a hash of the sorted type names (M
  `crates/omnigraph/src/changes/token.rs:206-222`, `283-335`).
- The client resends `types=` on every poll, and the server checks the digest (M
  `changes/feed.rs:246-253`).
- Names are matched per commit interval against that interval's table name (M
  `changes/enumerate.rs:415`, `521`), and never checked against the catalog. Today a type added
  later is followed only if the client already listed its name, and a rename silently stops a
  filter on the old name.
- The RFC proposes expanding an interface when the cursor is created. That needs a new cursor
  format and freezes membership: a live view misses a member added later.
- Expanding on every poll is not enough either. Adding `implements` is a metadata-only step, so the
  new member's existing rows never appear as changes.
- The feed already has a typed boundary refusal, `ChangeSchemaBoundary` (M `enumerate.rs:406`;
  HTTP 409).

**Proposed RFC text** (Wire, API and descriptor):

> An interface name in a feed or export filter is kept as written and expanded per commit, against
> that commit's accepted catalog. When a polled commit changes the member set of a filtered
> interface, the poll ends with a typed `ChangeScopeMembershipChanged` refusal. It carries the added
> and removed members, and a cursor positioned before that commit. The client re-baselines. No
> inserts or deletes are synthesized.

### 12. What "depends" means for removing `implements` (gap)

**Evidence.**
- The stored-query registry is cluster config. It is typechecked against the desired catalog in
  cluster apply preflight, where schema and queries advance in one deployment (M
  `crates/omnigraph-cluster/src/deployment/execution.rs:784-808`, `1022`). It is also checked at
  server boot (M `crates/omnigraph-server/src/lib.rs:2474-2485`). A standalone `schema apply` passes
  a no-op validator (M `crates/omnigraph-cli/src/main.rs:1200-1206`).
- If "depends" meant "mentions the interface", one view over `Schedulable` would block removing
  `implements` from every member. That is too strict.
- If it means "fails to typecheck", removing one member leaves `$x: Schedulable` valid. Only type
  tests or rebindings that name that member fail. `@type = $type` (a parameter) keeps typechecking
  and silently matches nothing.

**Proposed RFC text** (Schema migration):

> A stored query depends on member M of I when it fails to typecheck against the desired catalog.
> Type tests, rebindings and `@type` literal comparisons naming a non-member are type errors. A
> parameterised `@type` has no static dependency and is refused at run time (see interface-wide
> mutations). The data dependency is any tagged edge row reaching M. Both are checked in cluster
> preflight against the desired bundle's queries, so a query and the schema can change in one
> apply. A standalone apply has no registry, so served startup refuses instead.

### 13. Per-type policy and silent partial results (gap)

**Evidence.**
- There is no per-type authorization on main today. A rule has actors, actions and branch scopes
  (M `crates/omnigraph-policy/src/lib.rs:209-214`, `270-274`). `/query` and `/changes` authorize
  `Read` on the branch, so a request is refused or fully allowed (M
  `crates/omnigraph-server/src/handlers.rs:1456-1463`, `3596-3604`).
- The planned `predicate_for` compiles Cedar predicates into DataFusion filters pushed into the
  scan (M `crates/omnigraph-policy/src/lib.rs:956-966`, `1044-1046`): row filtering. The RFC's
  Policy section treats denied endpoints as filtered. A denied arm would drop its rows and `count`
  would be wrong, with no signal.
- Invariant 8 covers integrity failures, not authorization. But a view that assumes an interface
  is complete is wrong in the same silent way.

**Proposed RFC text** (Policy):

> When per-type rules exist, a read whose binding or traversal destination includes a member the
> actor may not read is refused with a typed `policy_denied_member` error, unless the query narrows
> that member out (a type test, a rebinding, or `@type` pruning). An interface-wide mutation with a
> denied arm is refused before staging, never partly applied. Row-level predicates inside an allowed
> type may filter; the response then carries `policy_filtered: true`, with no counts, so a view can
> say its results are partial.

### 14. A shared core adding `interface Event` where a fork has `node Event` (gap)

**Evidence.**
- main has no check across kinds. Interfaces and nodes are validated in separate maps (M
  `catalog/schema_shape.rs:479-499`; `catalog/mod.rs:337-366`). Probe:
  `q14.same_name_interface_and_node: accepted`. In the prototype the node wins name resolution.
- Renaming a node keeps its identity (`@rename_from`, M `schema_ir.rs:648-676`), and a rename
  across kinds is refused (`:671`).
- The adoption migration (rename the node to `CalendarEvent @rename_from("Event")`, add
  `interface Event`, `CalendarEvent implements Event`) plans the rename and the interface. It is
  refused today only because of the `implements` change. Probe:
  `q14.rename_node_and_reuse_name_for_interface: supported=false … "changing implemented interfaces on node 'CalendarEvent' is not supported"`.
  Once phase 0 allows metadata-only `implements` additions, it passes. The RFC's collision rule
  checks only the desired schema, which has no collision.
- Stored queries that said `$e: Event` then typecheck as an interface binding. They return the same
  rows until another node implements `Event`, then widen silently. The registry check tests only
  that typecheck succeeds; it never compares binding kinds or result schemas.

**Proposed RFC text** (phase 0 and Schema migration):

> Interface and node names share one namespace; a schema declaring both kinds with one name is
> refused. Within one apply, a name released by a rename or drop cannot be taken by a declaration of
> a different kind ("interface 'Event' reuses the name node 'Event' gave up in this migration; apply
> the rename first"). Stored queries record, at registration, each binding name's kind and
> StableTypeId, and their result descriptor. The registry check refuses when a name now resolves to
> a different kind or id, or when the descriptor's names, kinds or nullability change.

The migration path for a fork:
1. In one apply, rename `node Event` to `CalendarEvent`, and update its stored queries.
2. In a later apply, add `interface Event` and `CalendarEvent implements Event` (phase 0,
   metadata only).

## Other findings

- **Type-check code clash.** T54 is already taken on main ("a relevance ranking does not accept the
  asc modifier", M `crates/omnigraph-compiler/src/query/codes.rs:71`). The RFC's new codes should
  start at T55.
- **DateTime on the wire.** `DateTime` columns come back as Arrow `Date64` and render as
  `2026-10-12T09:00:00`, without a zone designator. A calendar component has to assume UTC. This is
  not specific to polymorphism.

## Proposed new unresolved questions

- **Paging over a union.** Choose between GQ `after $cursor` and server cursors on stored-query
  invocation. Either way: a total order with (type, id), the snapshot pinned in the cursor, and a
  per-arm keyset predicate (section 4).
- **Stable type id on the wire.** Spell `@type_id` as a decimal string and decide its relation to
  `schema_identity_domain` (section 3).
- **Feed membership changes.** Refuse with `ChangeScopeMembershipChanged`, or report membership
  events inline (section 11).
- **Per-type policy.** Refuse denied members, or filter with a `policy_filtered` marker (section 13).
- **Derived global key index.** Is one needed, and how does a coverage-watermarked derivative
  differ from the rejected registry (section 8)?
- **Removing an interface property.** Should it stay one step that drops a column from N tables, or
  require the plan to list each member explicitly (section 2)?
