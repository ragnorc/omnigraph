# Polymorphic types prototype: findings

Branch `proto/polymorphic-types`. Each finding gives the layer, what the prototype hit, and what it
means for the RFC (`docs/rfcs/2026-10-07-polymorphic-types.md`, PR #884).

Tests: `crates/omnigraph/tests/polymorphism_proto.rs` and
`crates/omnigraph-gqt/cases/polymorphism_proto_interface_endpoints.gqt`. In both, Person "alice" and
Organization "alice" share an id, so any layer that matches endpoints on the id alone fails them.

```bash
cargo test -p omnigraph-engine --test polymorphism_proto
cargo run -p omnigraph-gqt --bin omnigraph-gqt -- crates/omnigraph-gqt/cases/polymorphism_proto_interface_endpoints.gqt
```

Results on 2026-10-08: 11 of 11 Rust tests and the GQT case pass. The existing suites still pass:
465 compiler tests, the planner suites, 706 engine tests (library, `schema_apply`, `changes`,
`end_to_end`, `engine_v2`, `traversal_indexed`, `traversal_adaptive`, `consistency`,
`forbidden_apis`, `literal_filters`, `export`), and all 275 GQT cases (six DST cases first hit
their 10-second timeout under machine load, then passed alone).

Not prototyped: GQ `insert` with `Type($id)` endpoints, binding the edge variable of a
polymorphic traversal (refused), search over an interface, `return { $x }` for an abstract
binding, unions, interface-wide `update`/`delete`, export and change-feed endpoint types, and the
constant-column Merge (P7).

## P1. Tag columns must sit at the end of the edge schema (storage, loader)

The edge batch builders (`loader::build_edge_batch`, `edge_key_columns`, the strict normalizer)
treat schema positions 0 to 2 as id, src and dst, and everything after as user properties built
from row JSON. A tag column placed among the properties would be filled from user JSON, and a
missing value would silently become a null tag.

**RFC:** fix the tag position (after every property) and require every edge batch builder and
projection to treat tags as system fields. Refuse user-supplied tag keys.

## P2. Cycle closing compared ids only (compiler, IR lowering)

When both ends of a traversal are already bound, lowering emits an Expand into a temp variable plus
`temp.id = dst.id` (`lower.rs`, cycle closing, #605). With an interface destination the id alone is
not an identity. `$e: ExternalID, $x: Identifiable, not { $e identifies $x }` pairs `li:alice` with
both Person alice and Organization alice, and the negation drops the Organization row. The
prototype also compares `temp.~node_type = dst.~node_type` when the destination is abstract.

**RFC:** the RFC already says identity is (type, id) for cycle closing. The prototype confirms that
the current code really breaks without it. State the identity rule once and list every
node-id comparison site.

## P3. Narrowing has two sources of truth (compiler, typecheck)

Typecheck binds each binding twice: into `declarations` and into `ctx`. `typecheck_traversal`
resolves against the declared types, while lowering scans the type the checker settled on.

**RFC:** specify narrowing as one rewrite at bind time (the intersection of declared types), and
require traversal resolution to read the narrowed type.

## P4. The graph index collapses colliding ids (engine, graph index)

`GraphIndex::build` gives each declared endpoint type name one dense id space (`TypeIndex`) and
fills it from bare `__src`/`__dst` strings. A polymorphic edge therefore gets a `TypeIndex` named
after the interface, in which Person "acme" and Organization "acme" are one dense id. Nothing
refuses this. `bulk_anti_join_mask` reads `gi.type_index(edge.to_type)` for an inbound check, so
`$p: Person, not { $p identifies _ }` would drop Person "acme" because Organization "acme" has an
edge. That is a silent wrong answer.

Five sites build the edge map: `engine/mod.rs` (referenced edge types), `search.rs`,
`table_ops.rs`, `optimize.rs` (artifact refresh) and a `runtime_cache` test. The prototype excludes
polymorphic edges at all of them. The CSR, the CSC and the persisted artifact never hold such an
edge, and the anti-join falls back to its per-row inner pipeline (the typed expand).

Measured: with the exclusion switched off, the tests still pass. `Lowering::bulk_row_count` takes
the bulk path only for a non-budgeted expand, and the prototype plans every polymorphic traversal
on the budgeted route, so the collapsed index is never read. The hazard is latent, held off by
the routing choice alone (see P8).

**RFC:** the RFC says polymorphic expansions never use CSR before phase 3. It must also say the
graph index BUILD excludes these edges: the build is wasted work, and the collapsed `TypeIndex` is
a trap for any consumer that reads it without going through an expansion. The edge-map
construction should be one catalog function, so the exclusion cannot drift between five copies.

## P5. Keyed edges collide across types (catalog, loader)

`@key(@src, @dst)` derives the edge id from the endpoint ids (`canonical_key_id`). On a polymorphic
side, `li:alice -> Person alice` and `li:alice -> Organization alice` would get the same edge id, and
a Merge load would silently upsert one over the other. The prototype splices each tagged side's tag
column into the key right after its endpoint (`keyed_with_endpoint_types`), so the id is the tuple
`[src, dst, dst_type]`.

A generalization migration interacts with this. The rewrite copies each keyed row's old id,
derived without a type, while every later keyed write derives its id with the type. A Merge load
of an existing Person edge would then miss its row and insert a duplicate. The prototype's planner
therefore refuses to generalize an edge with a `@key` or `@unique` constraint
(`generalizing_a_keyed_edge_is_refused`).

**RFC:** the RFC already requires the tag in the key. New point: generalizing a keyed edge changes
the identity of every existing row. v1 should refuse it, as the prototype does. A later version can
rewrite ids (which breaks external references: CDC consumers, `@id` reads) or keep legacy ids for
rows of the old type (a permanent special case in id derivation).

## P6. `@card` over an interface source (validation)

`evaluate_cardinality` counts edges per bare source id and reads source deletions from
`node:{from_type}`. With an interface source, `node:Identifiable` does not exist, and Person "x" and
Organization "x" share one count. The prototype refuses `@card` on an edge whose source is an
interface. A destination-polymorphic edge is fine, because the count is per concrete source.

**RFC:** the RFC specifies `@card` per `(src_type, src)`. Implementation must change both the
grouping and the deletion lookup. The same applies to `@unique` tuples containing `@src` or `@dst`.

## P7. Generalization fits the existing rewrite path (schema migration)

Generalizing an endpoint (`ExternalID -> Person` to `ExternalID -> Identifiable`, where Person
implements Identifiable) now plans as a new `GeneralizeEndpoint` step, instead of "changing edge
endpoints is not supported". Apply routes the edge table through the existing full-rewrite path
(`rewritten_tables`, as AddProperty does). `batch_for_schema_apply_rewrite` fills the missing tag
column with the old node type's StableTypeId, so no row is ever untyped and no implicit-type rule is
needed at read time. Cost: one table rewrite, the same as AddProperty today. Narrowing stays
unsupported.

The RFC's acceptance threshold ("generalizing a one-million-edge table writes no data file") rules
out a rewrite. A middle path, not yet probed: stage an `Operation::Merge` whose fragments gain a
column-only data file holding the constant old type id. That is Lance's per-fragment add-columns,
the distributed schema-evolution path, which writes new files without committing. A constant
column takes Lance v2.2's constant layout (about zero bytes per row, measured in the
investigation), so the cost is one tiny file per fragment. Row ids, indexes and existing data files
survive, and live rows never hold a null tag, so the null-means-implicit-type rule disappears
from every live read and write path (validation, cascade, traversal).

One case still needs the old type. Historical reads apply the CURRENT contract to old table
images. A table version from before the generalization has no tag column at all, and a historical
traversal with only concrete bindings (which the RFC admits) still needs each row's endpoint type.
So the IR keeps a `generalized_from` record per generalized side, used only to synthesize the tag
for an image that lacks the column.

**RFC:** replace "AllNulls Merge, null means `implicit_type`" with "constant-column Merge, no null
tags; `generalized_from` only for column-absent historical images". Keep the full rewrite as the
proven fallback, and add a Lance probe for the detached constant-column commit to the evidence
list.

## P8. An untyped plan of a polymorphic edge gives silent wrong answers (engine, expand)

Measured by switching off the typechecker's wrap, so `$e identifies $x` planned as an ordinary
named expand:

| Query | Typed expand (prototype) | Untyped named expand |
|---|---|---|
| `owners_of("alice")` | Organization web:alice, Person li:alice | four rows: each "alice" also gets the other type's ExternalID |
| `unidentified_people` | acme, bob | bob only: Person "acme" is dropped because Organization "acme" has an edge |
| `identified("li:alice")`, CSR start | Person | error: no type index for 'ExternalID' |

The CSR start fails loudly, but the indexed start returns wrong rows with no error. Before this
change, only the typechecker's choice to plan polymorphic traversals as one-member selections stood
between a user and those answers. The prototype now refuses in `ExpandStep::validate`: a step that
crosses an interface (`ExpandStep::typed`) must be budgeted. With the wrap switched off, all nine
probes become that refusal.

**RFC:** state as an invariant that every expansion touching a polymorphic edge or an abstract
endpoint reads endpoint types (qualified keys), and that the engine refuses any other plan. The
typechecker's routing is an optimisation, not the safety mechanism. Add a test that builds the bad
plan directly and expects the refusal.

## P9. Forcing CSR gives a misleading error (engine, settings)

`traversal = csr` on a polymorphic traversal is refused, as the RFC wants before phase 3. The
message is "edge selections do not support traversal = csr", which names the prototype's internal
one-member selection, not anything the user wrote.

**RFC:** give polymorphic traversals a dedicated policy (the RFC's `ExpandPolicy::Budgeted` for a
named polymorphic edge, not a one-member alternation). Its own refusal should name the edge, as in
"edge Identifies has an interface endpoint and cannot use traversal = csr".

## P10. What worked as designed

- Union scan over member tables, conformed to the interface's columns plus `~node_type`, through the
  existing scan operator with per-member pushdown: interface bindings, key-equality filters across
  colliding ids, and `@type` projection all returned exact rows.
- Typed traversal by qualifying interner keys as `type \u{1f} id`, with Lance probes still on raw
  ids and tag columns read alongside. Forward, reverse, two-hop and negation all kept colliding ids
  apart. No facet expansion (one member per concrete endpoint pair) was needed.
- Per-member hydration of abstract destinations joined back on (type, id).
- Referential integrity grouped by tag: orphan detection is per concrete type, and an unknown tag
  is an orphan.
- Cascade with `tag = id(T) AND id IN (...)`: deleting Organization "alice" removes only its edge.
- Load envelope `from_type`/`to_type`: refused when missing on a multi-member side, outside the
  interface, on a concrete side, or smuggled as a data field. Inferred for a one-member interface.
- Feature gate `polymorphic-endpoints` derived from the IR.
