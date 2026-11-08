### crates/omnigraph-gqt/cases/bare_node_projection_search_scan.gqt#3:query

```gq
query top_chunks($q: String) {
    match {
        $c: Chunk
        search($c.text, $q)
    }
    return { $c }
    order { bm25($c.text, $q) }
    limit 2
}
```

```gq
gq 3;
query top_chunks($q: String) {
    match { from bm25({ match {
        $c: Chunk
        search($c.text, $q)
    } return { $c } }, $c.text, $q) }
    return { $c }
    order { $score desc }
    limit 2
}
```

### crates/omnigraph-gqt/cases/bare_node_projection_search_scan.gqt#7:query

```gq
query hybrid_chunks($q: String, $v: Vector(2)) {
    match { $c: Chunk }
    return { $c }
    order { rrf(nearest($c.embedding, $v), bm25($c.text, $q)) }
    limit 3
}
```

```gq
gq 3;
query hybrid_chunks($q: String, $v: Vector(2)) {
    match { let $p = { match { $c: Chunk } return { $c } }  from rrf(nearest($p, $c.embedding, $v, k: 3), bm25($p, $c.text, $q), k: 60) }
    return { $c }
    order { $score desc }
    limit 3
}
```

### crates/omnigraph-gqt/cases/blob_external_uri_refused_without_policy.gqt#6:mutate

```gq
query update_to_external_uri($content: Blob?) {
    update Doc set { content: $content, title: "changed" } where slug = "a"
}
```

```gq
gq 3;
query update_to_external_uri($content: Blob?) {
    $x: Doc  $x.slug = "a"
    update $x set { content: $content, title: "changed" }
}
```

### crates/omnigraph-gqt/cases/blob_external_uri_refused_without_policy.gqt#9:mutate

```gq
query update_to_external_uri_matching_no_row($content: Blob?) {
    update Doc set { content: $content } where slug = "nobody"
}
```

```gq
gq 3;
query update_to_external_uri_matching_no_row($content: Blob?) {
    $x: Doc  $x.slug = "nobody"
    update $x set { content: $content }
}
```

### crates/omnigraph-gqt/cases/blob_update_carries_unassigned_blobs.gqt#3:mutate

```gq
query assign_first_carry_second($slug: String, $first: Blob?) {
    update Doc set { first: $first } where slug = $slug
}
```

```gq
gq 3;
query assign_first_carry_second($slug: String, $first: Blob?) {
    $x: Doc  $x.slug = $slug
    update $x set { first: $first }
}
```

### crates/omnigraph-gqt/cases/blob_update_carries_unassigned_blobs.gqt#6:mutate

```gq
query clear_first_carry_second($slug: String, $first: Blob?) {
    update Doc set { first: $first } where slug = $slug
}
```

```gq
gq 3;
query clear_first_carry_second($slug: String, $first: Blob?) {
    $x: Doc  $x.slug = $slug
    update $x set { first: $first }
}
```

### crates/omnigraph-gqt/cases/blob_update_carries_unassigned_blobs.gqt#9:mutate

```gq
query assign_scalar_carry_both($slug: String) {
    update Doc set { title: "renamed" } where slug = $slug
}
```

```gq
gq 3;
query assign_scalar_carry_both($slug: String) {
    $x: Doc  $x.slug = $slug
    update $x set { title: "renamed" }
}
```

### crates/omnigraph-gqt/cases/blob_update_carries_unassigned_blobs.gqt#12:mutate

```gq
query assign_both($slug: String, $first: Blob?, $second: Blob?) {
    update Doc set { first: $first, second: $second } where slug = $slug
}
```

```gq
gq 3;
query assign_both($slug: String, $first: Blob?, $second: Blob?) {
    $x: Doc  $x.slug = $slug
    update $x set { first: $first, second: $second }
}
```

### crates/omnigraph-gqt/cases/blob_update_carries_unassigned_blobs.gqt#18:mutate

```gq
query assign_scalar_over_null_and_stored_blobs() {
    update Doc set { rank: 9 } where rank > 1
}
```

```gq
gq 3;
query assign_scalar_over_null_and_stored_blobs() {
    $x: Doc  $x.rank > 1
    update $x set { rank: 9 }
}
```

### crates/omnigraph-gqt/cases/blob_update_carries_unassigned_blobs.gqt#23:mutate

```gq
query null_on_non_nullable_scalar_matching_no_row($slug: String, $rank: I64?) {
    update Doc set { rank: $rank } where slug = $slug
}
```

```gq
gq 3;
query null_on_non_nullable_scalar_matching_no_row($slug: String, $rank: I64?) {
    $x: Doc  $x.slug = $slug
    update $x set { rank: $rank }
}
```

### crates/omnigraph-gqt/cases/block_closed_before_a_restart_is_released_at_the_production_budget.gqt#9:mutate

```gq
query set_bob_on_s() {
    update Person set { age: 7 } where name = "bob"
}
```

```gq
gq 3;
query set_bob_on_s() {
    $x: Person  $x.name = "bob"
    update $x set { age: 7 }
}
```

### crates/omnigraph-gqt/cases/block_closed_before_a_restart_is_released_at_the_production_budget.gqt#12:mutate

```gq
query bump_alice($n: I32) {
    update Person set { age: $n } where name = "alice"
}
```

```gq
gq 3;
query bump_alice($n: I32) {
    $x: Person  $x.name = "alice"
    update $x set { age: $n }
}
```

### crates/omnigraph-gqt/cases/bm25_distinct_scores_rank_descending.gqt#3:query

```gq
query bm25_ranked($q: String) {
    match { $n: Note }
    return { $n.slug }
    order { bm25($n.body, $q) }
    limit 3
}
```

```gq
gq 3;
query bm25_ranked($q: String) {
    match { from bm25({ match { $n: Note } return { $n } }, $n.body, $q) }
    return { $n.slug }
    order { $score desc }
    limit 3
}
```

### crates/omnigraph-gqt/cases/bm25_distinct_scores_rank_descending.gqt#7:query

```gq
query distinct_literal_order($q: String) {
    match { $n: Note }
    return distinct { $n.slug, 1 as one }
    order { bm25($n.body, $q), 1 }
    limit 3
}
```

```gq
gq 3;
query distinct_literal_order($q: String) {
    match { from bm25({ match { $n: Note } return { $n } }, $n.body, $q) }
    return distinct { $n.slug, 1 as one }
    order { $score desc, 1 }
    limit 3
}
```

### crates/omnigraph-gqt/cases/boolean_literal_refused_where_a_property_is_named_false.gqt#3:mutate

```gq
query shadowed_property() {
    delete Flag where false = false
}
```

```gq
gq 3;
query shadowed_property() {
    $x: Flag  false = false
    delete $x
}
```

### crates/omnigraph-gqt/cases/boolean_literal_refused_where_a_property_is_named_false.gqt#8:mutate

```gq
query literal_on_a_plain_type() {
    delete Switch where flag = false
}
```

```gq
gq 3;
query literal_on_a_plain_type() {
    $x: Switch  $x.flag = false
    delete $x
}
```

### crates/omnigraph-gqt/cases/boolean_precedence_and_binds_tighter_than_or.gqt#18:mutate

```gq
query delete_bare_or_and() {
    delete Row where a or b and c
}
```

```gq
gq 3;
query delete_bare_or_and() {
    $x: Row  $x.a or $x.b and $x.c
    delete $x
}
```

### crates/omnigraph-gqt/cases/branch_create_at_a_commit.gqt#6:mutate

```gq
query age_alice() {
    update Person set { age: 31 } where name = "alice"
}
```

```gq
gq 3;
query age_alice() {
    $x: Person  $x.name = "alice"
    update $x set { age: 31 }
}
```

### crates/omnigraph-gqt/cases/branch_create_at_a_commit.gqt#8:mutate

```gq
query remove_bob() {
    delete Person where name = "bob"
}
```

```gq
gq 3;
query remove_bob() {
    $x: Person  $x.name = "bob"
    delete $x
}
```

### crates/omnigraph-gqt/cases/branch_merge_after_owner_reforks.gqt#5:mutate

```gq
query feature_write() {
    update Person set { age: 31 } where name = "alice"
}
```

```gq
gq 3;
query feature_write() {
    $x: Person  $x.name = "alice"
    update $x set { age: 31 }
}
```

### crates/omnigraph-gqt/cases/branch_merge_after_owner_reforks.gqt#11:mutate

```gq
query main_write() {
    update Person set { age: 32 } where name = "alice"
}
```

```gq
gq 3;
query main_write() {
    $x: Person  $x.name = "alice"
    update $x set { age: 32 }
}
```

### crates/omnigraph-gqt/cases/branch_merge_after_owner_reforks.gqt#15:mutate

```gq
query feature_writes_after_pointer_switch() {
    update Person set { age: 33 } where name = "alice"
}
```

```gq
gq 3;
query feature_writes_after_pointer_switch() {
    $x: Person  $x.name = "alice"
    update $x set { age: 33 }
}
```

### crates/omnigraph-gqt/cases/branch_merge_after_owner_reforks.gqt#19:mutate

```gq
query replacement_write() {
    update Person set { age: 34 } where name = "alice"
}
```

```gq
gq 3;
query replacement_write() {
    $x: Person  $x.name = "alice"
    update $x set { age: 34 }
}
```

### crates/omnigraph-gqt/cases/branch_merge_after_owner_reforks.gqt#29:mutate

```gq
query feature_writes_after_borrowing() {
    update Person set { age: 35 } where name = "alice"
}
```

```gq
gq 3;
query feature_writes_after_borrowing() {
    $x: Person  $x.name = "alice"
    update $x set { age: 35 }
}
```

### crates/omnigraph-gqt/cases/branch_merge_after_owner_reforks.gqt#31:mutate

```gq
query replacement_writes_independently() {
    update Person set { age: 36 } where name = "alice"
}
```

```gq
gq 3;
query replacement_writes_independently() {
    $x: Person  $x.name = "alice"
    update $x set { age: 36 }
}
```

### crates/omnigraph-gqt/cases/branch_statement_lifecycle.gqt#13:mutate

```gq
query bump_on_b1() {
    update Person set { age: 31 } where name = "alice"
}
```

```gq
gq 3;
query bump_on_b1() {
    $x: Person  $x.name = "alice"
    update $x set { age: 31 }
}
```

### crates/omnigraph-gqt/cases/branch_statement_lifecycle.gqt#15:mutate

```gq
query bump_on_main() {
    update Person set { age: 32 } where name = "alice"
}
```

```gq
gq 3;
query bump_on_main() {
    $x: Person  $x.name = "alice"
    update $x set { age: 32 }
}
```

### crates/omnigraph-gqt/cases/camelcase_property_in_mutation_predicate.gqt#3:mutate

```gq
query delete_open_large_deals() {
    delete Deal where dealStage = "open" and amountUsd > 10
}
```

```gq
gq 3;
query delete_open_large_deals() {
    $x: Deal  $x.dealStage = "open" and $x.amountUsd > 10
    delete $x
}
```

### crates/omnigraph-gqt/cases/camelcase_property_in_mutation_predicate.gqt#5:mutate

```gq
query close_large_deals($floor: I32) {
    update Deal set { dealStage: "closed" } where amountUsd > $floor
}
```

```gq
gq 3;
query close_large_deals($floor: I32) {
    $x: Deal  $x.amountUsd > $floor
    update $x set { dealStage: "closed" }
}
```

### crates/omnigraph-gqt/cases/card_minimum_counts_every_written_source.gqt#9:mutate

```gq
query retitle() {
    update Person set { title: "cto" } where name = "alice"
}
```

```gq
gq 3;
query retitle() {
    $x: Person  $x.name = "alice"
    update $x set { title: "cto" }
}
```

### crates/omnigraph-gqt/cases/catalogue/mixed_joins_and_refusals.gqt#9:mutate

```gq
query rename_scan() {
    update @Operator set { name: "Read" } where name = "Scan"
}
```

```gq
gq 3;
query rename_scan() {
    $x: @Operator  $x.name = "Scan"
    update $x set { name: "Read" }
}
```

### crates/omnigraph-gqt/cases/cold_merge_base_after_target_merged_older_branch.gqt#12:mutate

```gq
query bump_alice($n: I32) {
    update Person set { age: $n } where name = "alice"
}
```

```gq
gq 3;
query bump_alice($n: I32) {
    $x: Person  $x.name = "alice"
    update $x set { age: $n }
}
```

### crates/omnigraph-gqt/cases/cold_merge_base_after_target_merged_older_branch.gqt#20:mutate

```gq
query delete_w2() {
    delete Person where name = "w2"
}
```

```gq
gq 3;
query delete_w2() {
    $x: Person  $x.name = "w2"
    delete $x
}
```

### crates/omnigraph-gqt/cases/cold_merge_base_released_into_history.gqt#9:mutate

```gq
query set_bob_on_s() {
    update Person set { age: 7 } where name = "bob"
}
```

```gq
gq 3;
query set_bob_on_s() {
    $x: Person  $x.name = "bob"
    update $x set { age: 7 }
}
```

### crates/omnigraph-gqt/cases/cold_merge_base_released_into_history.gqt#12:mutate

```gq
query bump_alice($n: I32) {
    update Person set { age: $n } where name = "alice"
}
```

```gq
gq 3;
query bump_alice($n: I32) {
    $x: Person  $x.name = "alice"
    update $x set { age: $n }
}
```

### crates/omnigraph-gqt/cases/compound_and_bare_boolean_filters.gqt#6:query

```gq
query leading_nearest_runs($q: Vector(2)) {
    match { $p: Person  $p.enabled = true }
    return { $p.slug }
    order { nearest($p.embedding, $q) }
    limit 1
}
```

```gq
gq 3;
query leading_nearest_runs($q: Vector(2)) {
    match { from nearest({ match { $p: Person  $p.enabled = true } return { $p } }, $p.embedding, $q, k: 1) }
    return { $p.slug }
    order { $distance }
    limit 1
}
```

### crates/omnigraph-gqt/cases/compound_and_bare_boolean_filters.gqt#23:mutate

```gq
query compound_where_runs($cut: I64) {
    update Person set { enabled: false } where age < $cut or name = "carol"
}
```

```gq
gq 3;
query compound_where_runs($cut: I64) {
    $x: Person  $x.age < $cut or $x.name = "carol"
    update $x set { enabled: false }
}
```

### crates/omnigraph-gqt/cases/concurrent_stale_reads_race_their_installs_then_a_write_lands.gqt#5:mutate

```gq
query age_alice() { update Person set { age: 99 } where name = "alice" }
```

```gq
gq 3;
query age_alice() { $x: Person  $x.name = "alice"  update $x set { age: 99 } }
```

### crates/omnigraph-gqt/cases/concurrent_writers_on_a_named_branch.gqt#7.0:concurrent

```gq
query age_alice() { update Person set { age: 99 } where name = "alice" }
```

```gq
gq 3;
query age_alice() { $x: Person  $x.name = "alice"  update $x set { age: 99 } }
```

### crates/omnigraph-gqt/cases/concurrent_writers_on_a_named_branch.gqt#9.0:concurrent

```gq
query delete_alice() { delete Person where name = "alice" }
```

```gq
gq 3;
query delete_alice() { $x: Person  $x.name = "alice"  delete $x }
```

### crates/omnigraph-gqt/cases/conjunction_filter_prefilters_nearest.gqt#3:query

```gq
query nearest_matching_both_filters($q: Vector(4)) {
    match {
        $d: Doc
        $d.n >= 7
        $d.name contains "-00"
    }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 3
}
```

```gq
gq 3;
query nearest_matching_both_filters($q: Vector(4)) {
    match { from nearest({ match {
        $d: Doc
        $d.n >= 7
        contains($d.name, "-00")
    } return { $d } }, $d.embedding, $q, k: 3) }
    return { $d.slug }
    order { $distance }
    limit 3
}
```

### crates/omnigraph-gqt/cases/constants_evaluated_per_invocation.gqt#11:mutate

```gq
query assign_with_forty($slug: String, $age: I64) {
    update Person set { adult: $age > 30 } where slug = $slug
}
```

```gq
gq 3;
query assign_with_forty($slug: String, $age: I64) {
    $x: Person  $x.slug = $slug
    update $x set { adult: $age > 30 }
}
```

### crates/omnigraph-gqt/cases/constants_evaluated_per_invocation.gqt#14:mutate

```gq
query assign_with_twenty($slug: String, $age: I64) {
    update Person set { adult: $age > 30 } where slug = $slug
}
```

```gq
gq 3;
query assign_with_twenty($slug: String, $age: I64) {
    $x: Person  $x.slug = $slug
    update $x set { adult: $age > 30 }
}
```

### crates/omnigraph-gqt/cases/constants_evaluated_per_invocation.gqt#20:mutate

```gq
query or_absorbs_null($slug: String, $flag: Bool?) {
    update Person set { adult: true or $flag } where slug = $slug
}
```

```gq
gq 3;
query or_absorbs_null($slug: String, $flag: Bool?) {
    $x: Person  $x.slug = $slug
    update $x set { adult: true or $flag }
}
```

### crates/omnigraph-gqt/cases/constants_evaluated_per_invocation.gqt#23:mutate

```gq
query and_absorbs_null($slug: String, $flag: Bool?) {
    update Person set { adult: false and $flag } where slug = $slug
}
```

```gq
gq 3;
query and_absorbs_null($slug: String, $flag: Bool?) {
    $x: Person  $x.slug = $slug
    update $x set { adult: false and $flag }
}
```

### crates/omnigraph-gqt/cases/count_over_traversal_nearest_counts_window.gqt#3:query

```gq
query count_nearest_with_edges($q: Vector(4)) {
    match {
        $d: Doc
        $d knows $t
    }
    return { count($d) as total }
    order { nearest($d.embedding, $q) }
    limit 2
}
```

```gq
gq 3;
query count_nearest_with_edges($q: Vector(4)) {
    match { from nearest({ match {
        $d: Doc
        $d knows $t
    } return { $d, $t } }, $d.embedding, $q, k: 2) }
    return { count($d) as total }
    limit 2
}
```

### crates/omnigraph-gqt/cases/delete_conjunction_counts_the_intersection.gqt#3:mutate

```gq
query note_a_names_tagged_y() {
    update Person set { note: "seen" } where name starts_with "a" and tags contains "y"
}
```

```gq
gq 3;
query note_a_names_tagged_y() {
    $x: Person  starts_with($x.name, "a") and "y" in $x.tags
    update $x set { note: "seen" }
}
```

### crates/omnigraph-gqt/cases/delete_conjunction_counts_the_intersection.gqt#5:mutate

```gq
query delete_a_names_tagged_x() {
    delete Person where name starts_with "a" and tags contains "x"
}
```

```gq
gq 3;
query delete_a_names_tagged_x() {
    $x: Person  starts_with($x.name, "a") and "x" in $x.tags
    delete $x
}
```

### crates/omnigraph-gqt/cases/detached_branch_search_tracks_own_versions.gqt#3:query

```gq
query nearest_docs($q: Vector(4)) {
    match { $d: Doc }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 4
}
```

```gq
gq 3;
query nearest_docs($q: Vector(4)) {
    match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 4) }
    return { $d.slug }
    order { $distance }
    limit 4
}
```

### crates/omnigraph-gqt/cases/detached_branch_search_tracks_own_versions.gqt#9:mutate

```gq
query move_a($e: Vector(4)) { update Doc set { embedding: $e } where slug = "a" }
```

```gq
gq 3;
query move_a($e: Vector(4)) { $x: Doc  $x.slug = "a"  update $x set { embedding: $e } }
```

### crates/omnigraph-gqt/cases/detached_branch_search_tracks_own_versions.gqt#12:query

```gq
query nearest_docs($q: Vector(4)) {
    match { $d: Doc }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 4
}
```

```gq
gq 3;
query nearest_docs($q: Vector(4)) {
    match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 4) }
    return { $d.slug }
    order { $distance }
    limit 4
}
```

### crates/omnigraph-gqt/cases/detached_branch_search_tracks_own_versions.gqt#16:query

```gq
query nearest_docs($q: Vector(4)) {
    match { $d: Doc }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 4
}
```

```gq
gq 3;
query nearest_docs($q: Vector(4)) {
    match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 4) }
    return { $d.slug }
    order { $distance }
    limit 4
}
```

### crates/omnigraph-gqt/cases/detached_branch_search_tracks_own_versions.gqt#20:mutate

```gq
query delete_b() { delete Doc where slug = "b" }
```

```gq
gq 3;
query delete_b() { $x: Doc  $x.slug = "b"  delete $x }
```

### crates/omnigraph-gqt/cases/detached_branch_search_tracks_own_versions.gqt#23:query

```gq
query nearest_docs($q: Vector(4)) {
    match { $d: Doc }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 4
}
```

```gq
gq 3;
query nearest_docs($q: Vector(4)) {
    match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 4) }
    return { $d.slug }
    order { $distance }
    limit 4
}
```

### crates/omnigraph-gqt/cases/detached_branch_search_tracks_own_versions.gqt#27:query

```gq
query nearest_docs($q: Vector(4)) {
    match { $d: Doc }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 4
}
```

```gq
gq 3;
query nearest_docs($q: Vector(4)) {
    match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 4) }
    return { $d.slug }
    order { $distance }
    limit 4
}
```

### crates/omnigraph-gqt/cases/detached_branch_search_tracks_own_versions.gqt#34:query

```gq
query nearest_docs($q: Vector(4)) {
    match { $d: Doc }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 4
}
```

```gq
gq 3;
query nearest_docs($q: Vector(4)) {
    match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 4) }
    return { $d.slug }
    order { $distance }
    limit 4
}
```

### crates/omnigraph-gqt/cases/detached_branch_search_tracks_own_versions.gqt#39:query

```gq
query nearest_docs($q: Vector(4)) {
    match { $d: Doc }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 4
}
```

```gq
gq 3;
query nearest_docs($q: Vector(4)) {
    match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 4) }
    return { $d.slug }
    order { $distance }
    limit 4
}
```

### crates/omnigraph-gqt/cases/detached_branch_search_tracks_own_versions.gqt#43:query

```gq
query nearest_docs($q: Vector(4)) {
    match { $d: Doc }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 4
}
```

```gq
gq 3;
query nearest_docs($q: Vector(4)) {
    match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 4) }
    return { $d.slug }
    order { $distance }
    limit 4
}
```

### crates/omnigraph-gqt/cases/detached_delete_before_publication_keeps_incident_edges.gqt#6:mutate

```gq
query delete_bob() { delete Person where name = "bob" }
```

```gq
gq 3;
query delete_bob() { $x: Person  $x.name = "bob"  delete $x }
```

### crates/omnigraph-gqt/cases/detached_delete_before_publication_keeps_incident_edges.gqt#21:mutate

```gq
query delete_bob() { delete Person where name = "bob" }
```

```gq
gq 3;
query delete_bob() { $x: Person  $x.name = "bob"  delete $x }
```

### crates/omnigraph-gqt/cases/detached_delete_cascade_survives_restart.gqt#5:mutate

```gq
query delete_bob() { delete Person where name = "bob" }
```

```gq
gq 3;
query delete_bob() { $x: Person  $x.name = "bob"  delete $x }
```

### crates/omnigraph-gqt/cases/detached_delete_fails_before_primary_delete.gqt#4:mutate

```gq
query delete_bob() { delete Person where name = "bob" }
```

```gq
gq 3;
query delete_bob() { $x: Person  $x.name = "bob"  delete $x }
```

### crates/omnigraph-gqt/cases/detached_delete_fails_before_primary_delete.gqt#12:mutate

```gq
query delete_bob() { delete Person where name = "bob" }
```

```gq
gq 3;
query delete_bob() { $x: Person  $x.name = "bob"  delete $x }
```

### crates/omnigraph-gqt/cases/detached_error_after_manifest_commit_is_durable_once.gqt#14:mutate

```gq
query older() { update Person set { age: 51 } where name = "carol" }
```

```gq
gq 3;
query older() { $x: Person  $x.name = "carol"  update $x set { age: 51 } }
```

### crates/omnigraph-gqt/cases/detached_merge_publish_outcome_in_doubt.gqt#7:mutate

```gq
query delete_bob() { delete Person where name = "bob" }
```

```gq
gq 3;
query delete_bob() { $x: Person  $x.name = "bob"  delete $x }
```

### crates/omnigraph-gqt/cases/detached_multitable_delete_cascade_failure_after_table_1.gqt#4:mutate

```gq
query w() {
    delete Person where name = "eve"
}
```

```gq
gq 3;
query w() {
    $x: Person  $x.name = "eve"
    delete $x
}
```

### crates/omnigraph-gqt/cases/detached_multitable_delete_cascade_failure_after_table_1.gqt#25:mutate

```gq
query w() {
    delete Person where name = "eve"
}
```

```gq
gq 3;
query w() {
    $x: Person  $x.name = "eve"
    delete $x
}
```

### crates/omnigraph-gqt/cases/detached_rewriting_merge_fails_between_phases.gqt#7:mutate

```gq
query delete_bob() { delete Person where name = "bob" }
```

```gq
gq 3;
query delete_bob() { $x: Person  $x.name = "bob"  delete $x }
```

### crates/omnigraph-gqt/cases/detached_update_before_publication_keeps_old_values.gqt#6:mutate

```gq
query update_alice() { update Person set { age: 31 } where name = "alice" }
```

```gq
gq 3;
query update_alice() { $x: Person  $x.name = "alice"  update $x set { age: 31 } }
```

### crates/omnigraph-gqt/cases/detached_update_before_publication_keeps_old_values.gqt#15:mutate

```gq
query update_again() { update Person set { age: 32 } where name = "alice" }
```

```gq
gq 3;
query update_again() { $x: Person  $x.name = "alice"  update $x set { age: 32 } }
```

### crates/omnigraph-gqt/cases/diverged_merge_into_switched_owner.gqt#5:mutate

```gq
query feature_write() {
    update Person set { age: 31 } where name = "alice"
}
```

```gq
gq 3;
query feature_write() {
    $x: Person  $x.name = "alice"
    update $x set { age: 31 }
}
```

### crates/omnigraph-gqt/cases/diverged_merge_into_switched_owner.gqt#9:mutate

```gq
query main_write() {
    update Person set { age: 32 } where name = "alice"
}
```

```gq
gq 3;
query main_write() {
    $x: Person  $x.name = "alice"
    update $x set { age: 32 }
}
```

### crates/omnigraph-gqt/cases/diverged_merge_into_switched_owner.gqt#17:mutate

```gq
query hotfix_write() {
    update Person set { age: 33 } where name = "alice"
}
```

```gq
gq 3;
query hotfix_write() {
    $x: Person  $x.name = "alice"
    update $x set { age: 33 }
}
```

### crates/omnigraph-gqt/cases/empty_merge_preserves_lazy_child_first_write.gqt#5:mutate

```gq
query parent_first_write() {
    update Person set { age: 31 } where name = "alice"
}
```

```gq
gq 3;
query parent_first_write() {
    $x: Person  $x.name = "alice"
    update $x set { age: 31 }
}
```

### crates/omnigraph-gqt/cases/empty_merge_preserves_lazy_child_first_write.gqt#13:mutate

```gq
query owner_next_write() {
    update Person set { age: 32 } where name = "alice"
}
```

```gq
gq 3;
query owner_next_write() {
    $x: Person  $x.name = "alice"
    update $x set { age: 32 }
}
```

### crates/omnigraph-gqt/cases/explicit_vector_nearest_does_not_require_gemini_credentials.gqt#3:query

```gq
query vector_search_vector($q: Vector(4)) {
    match { $d: Doc }
    return { $d.slug, $d.title }
    order { nearest($d.embedding, $q) }
    limit 3
}
```

```gq
gq 3;
query vector_search_vector($q: Vector(4)) {
    match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 3) }
    return { $d.slug, $d.title }
    order { $distance }
    limit 3
}
```

### crates/omnigraph-gqt/cases/expression_type_errors.gqt#13:mutate

```gq
query count_in_a_mutation_where() {
    update Person set { adult: true } where count(age) > 1
}
```

```gq
gq 3;
query count_in_a_mutation_where() {
    $x: Person  count($x.age) > 1
    update $x set { adult: true }
}
```

### crates/omnigraph-gqt/cases/expression_type_errors.gqt#15:mutate

```gq
query property_leaf_in_an_assignment() {
    update Person set { adult: age > 30 } where slug = "a"
}
```

```gq
gq 3;
query property_leaf_in_an_assignment() {
    $x: Person  $x.slug = "a"
    update $x set { adult: age > 30 }
}
```

### crates/omnigraph-gqt/cases/expression_type_errors.gqt#19:mutate

```gq
query search_in_a_mutation_where() {
    delete Person where search(text, "hello")
}
```

```gq
gq 3;
query search_in_a_mutation_where() {
    $x: Person  search($x.text, "hello")
    delete $x
}
```

### crates/omnigraph-gqt/cases/expression_type_errors.gqt#27:mutate

```gq
query string_as_a_mutation_where() {
    delete Person where name
}
```

```gq
gq 3;
query string_as_a_mutation_where() {
    $x: Person  $x.name
    delete $x
}
```

### crates/omnigraph-gqt/cases/failed_releasing_publish_keeps_rows_and_retries.gqt#8:mutate

```gq
query bump_alice($n: I32) {
    update Person set { age: $n } where name = "alice"
}
```

```gq
gq 3;
query bump_alice($n: I32) {
    $x: Person  $x.name = "alice"
    update $x set { age: $n }
}
```

### crates/omnigraph-gqt/cases/filtered_nearest_fewer_matches_than_limit.gqt#3:query

```gq
query nearest_single_match($q: Vector(4)) {
    match {
        $d: Doc
        $d.n = 7
    }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 3
}
```

```gq
gq 3;
query nearest_single_match($q: Vector(4)) {
    match { from nearest({ match {
        $d: Doc
        $d.n = 7
    } return { $d } }, $d.embedding, $q, k: 3) }
    return { $d.slug }
    order { $distance }
    limit 3
}
```

### crates/omnigraph-gqt/cases/filtered_nearest_more_matches_than_limit.gqt#3:query

```gq
query nearest_matching_rows($q: Vector(4)) {
    match {
        $d: Doc
        $d.n > 3
    }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 3
}
```

```gq
gq 3;
query nearest_matching_rows($q: Vector(4)) {
    match { from nearest({ match {
        $d: Doc
        $d.n > 3
    } return { $d } }, $d.embedding, $q, k: 3) }
    return { $d.slug }
    order { $distance }
    limit 3
}
```

### crates/omnigraph-gqt/cases/filtered_nearest_no_match_is_empty.gqt#3:query

```gq
query nearest_without_matches($q: Vector(4)) {
    match {
        $d: Doc
        $d.name starts_with "doc-9"
    }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 2
}
```

```gq
gq 3;
query nearest_without_matches($q: Vector(4)) {
    match { from nearest({ match {
        $d: Doc
        starts_with($d.name, "doc-9")
    } return { $d } }, $d.embedding, $q, k: 2) }
    return { $d.slug }
    order { $distance }
    limit 2
}
```

### crates/omnigraph-gqt/cases/filtered_nearest_returns_matching_rows_not_postfiltered_topk.gqt#3:query

```gq
query filtered_nearest($q: Vector(4)) {
    match { $d: Doc { status: "hit" } }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 3
}
```

```gq
gq 3;
query filtered_nearest($q: Vector(4)) {
    match { from nearest({ match { $d: Doc { status: "hit" } } return { $d } }, $d.embedding, $q, k: 3) }
    return { $d.slug }
    order { $distance }
    limit 3
}
```

### crates/omnigraph-gqt/cases/filtered_nearest_returns_matching_rows_not_postfiltered_topk.gqt#7:query

```gq
query filtered_nearest_clause_eq($q: Vector(4)) {
    match { $d: Doc
        $d.status = "hit" }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 3
}
```

```gq
gq 3;
query filtered_nearest_clause_eq($q: Vector(4)) {
    match { from nearest({ match { $d: Doc
        $d.status = "hit" } return { $d } }, $d.embedding, $q, k: 3) }
    return { $d.slug }
    order { $distance }
    limit 3
}
```

### crates/omnigraph-gqt/cases/filtered_nearest_returns_matching_rows_not_postfiltered_topk.gqt#11:query

```gq
query filtered_nearest_clause_range($q: Vector(4)) {
    match { $d: Doc
        $d.status <= "hit" }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 3
}
```

```gq
gq 3;
query filtered_nearest_clause_range($q: Vector(4)) {
    match { from nearest({ match { $d: Doc
        $d.status <= "hit" } return { $d } }, $d.embedding, $q, k: 3) }
    return { $d.slug }
    order { $distance }
    limit 3
}
```

### crates/omnigraph-gqt/cases/fork_of_a_deleted_unmerged_branch_merges_after_release.gqt#9:mutate

```gq
query set_bob_on_s() {
    update Person set { age: 3 } where name = "bob"
}
```

```gq
gq 3;
query set_bob_on_s() {
    $x: Person  $x.name = "bob"
    update $x set { age: 3 }
}
```

### crates/omnigraph-gqt/cases/fork_of_a_deleted_unmerged_branch_merges_after_release.gqt#13:mutate

```gq
query set_carol_on_t() {
    update Person set { age: 5 } where name = "carol"
}
```

```gq
gq 3;
query set_carol_on_t() {
    $x: Person  $x.name = "carol"
    update $x set { age: 5 }
}
```

### crates/omnigraph-gqt/cases/fork_of_a_deleted_unmerged_branch_merges_after_release.gqt#16:mutate

```gq
query bump_alice($n: I32) {
    update Person set { age: $n } where name = "alice"
}
```

```gq
gq 3;
query bump_alice($n: I32) {
    $x: Person  $x.name = "alice"
    update $x set { age: $n }
}
```

### crates/omnigraph-gqt/cases/generated_branch_merge_dataset.gqt#9:mutate

```gq
query delete_src_0($c: String) { delete BenchE000 where cohort = $c }
```

```gq
gq 3;
query delete_src_0($c: String) { $x: BenchE000  $x.cohort = $c  delete $x }
```

### crates/omnigraph-gqt/cases/generated_branch_merge_dataset.gqt#16:mutate

```gq
query delete_tgt_0($c: String) { delete BenchE000 where cohort = $c }
```

```gq
gq 3;
query delete_tgt_0($c: String) { $x: BenchE000  $x.cohort = $c  delete $x }
```

### crates/omnigraph-gqt/cases/graph_diff_branch_review.gqt#8:mutate

```gq
query age_alice() {
    update Person set { age: 31 } where name = "alice"
}
```

```gq
gq 3;
query age_alice() {
    $x: Person  $x.name = "alice"
    update $x set { age: 31 }
}
```

### crates/omnigraph-gqt/cases/graph_diff_merge_proposal.gqt#5:mutate

```gq
query age_alice() {
    update Person set { age: 31 } where name = "alice"
}
```

```gq
gq 3;
query age_alice() {
    $x: Person  $x.name = "alice"
    update $x set { age: 31 }
}
```

### crates/omnigraph-gqt/cases/graph_diff_net_range.gqt#6:mutate

```gq
query alice_ages() {
    update Person set { age: 31 } where name = "alice"
}
```

```gq
gq 3;
query alice_ages() {
    $x: Person  $x.name = "alice"
    update $x set { age: 31 }
}
```

### crates/omnigraph-gqt/cases/graph_diff_net_range.gqt#8:mutate

```gq
query alice_reverts() {
    update Person set { age: 30 } where name = "alice"
}
```

```gq
gq 3;
query alice_reverts() {
    $x: Person  $x.name = "alice"
    update $x set { age: 30 }
}
```

### crates/omnigraph-gqt/cases/graph_diff_net_range.gqt#12:mutate

```gq
query gina_leaves() {
    delete Person where name = "gina"
}
```

```gq
gq 3;
query gina_leaves() {
    $x: Person  $x.name = "gina"
    delete $x
}
```

### crates/omnigraph-gqt/cases/graph_diff_net_range.gqt#14:mutate

```gq
query hank_leaves() {
    delete Person where name = "hank"
}
```

```gq
gq 3;
query hank_leaves() {
    $x: Person  $x.name = "hank"
    delete $x
}
```

### crates/omnigraph-gqt/cases/graph_diff_net_range.gqt#18:mutate

```gq
query ivan_leaves() {
    delete Person where name = "ivan"
}
```

```gq
gq 3;
query ivan_leaves() {
    $x: Person  $x.name = "ivan"
    delete $x
}
```

### crates/omnigraph-gqt/cases/graph_diff_net_range.gqt#25:mutate

```gq
query jill_ages() {
    update Person set { age: 19 } where name = "jill"
}
```

```gq
gq 3;
query jill_ages() {
    $x: Person  $x.name = "jill"
    update $x set { age: 19 }
}
```

### crates/omnigraph-gqt/cases/graph_diff_refusals.gqt#9:mutate

```gq
query feature_bob() {
    update Person set { age: 41 } where name = "bob"
}
```

```gq
gq 3;
query feature_bob() {
    $x: Person  $x.name = "bob"
    update $x set { age: 41 }
}
```

### crates/omnigraph-gqt/cases/graph_diff_refusals.gqt#11:mutate

```gq
query main_bob() {
    update Person set { age: 42 } where name = "bob"
}
```

```gq
gq 3;
query main_bob() {
    $x: Person  $x.name = "bob"
    update $x set { age: 42 }
}
```

### crates/omnigraph-gqt/cases/inline_property_filter_prefilters_nearest.gqt#3:query

```gq
query nearest_inline_filter($q: Vector(4)) {
    match {
        $d: Doc { n: 7 }
    }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 3
}
```

```gq
gq 3;
query nearest_inline_filter($q: Vector(4)) {
    match { from nearest({ match {
        $d: Doc { n: 7 }
    } return { $d } }, $d.embedding, $q, k: 3) }
    return { $d.slug }
    order { $distance }
    limit 3
}
```

### crates/omnigraph-gqt/cases/issue_1236_stacked_diff_after_parent_delete.gqt#5:mutate

```gq
query promote() {
    update Person set { title: "Lead" } where name = "p5"
}
```

```gq
gq 3;
query promote() {
    $x: Person  $x.name = "p5"
    update $x set { title: "Lead" }
}
```

### crates/omnigraph-gqt/cases/issue_1236_stacked_diff_after_parent_delete.gqt#10:mutate

```gq
query promote() {
    update Person set { title: "Principal" } where name = "p7"
}
```

```gq
gq 3;
query promote() {
    $x: Person  $x.name = "p7"
    update $x set { title: "Principal" }
}
```

### crates/omnigraph-gqt/cases/issue_1277_composed_and_wide_predicates_keep_null_rows_out.gqt#9:mutate

```gq
query update_five_properties_tested_twice() {
    update Wide set { tag: "hit" } where not ((p1 in ["a"]) and (p1 in ["b"])) or not ((p2 in ["a"]) and (p2 in ["b"])) or not ((p3 in ["a"]) and (p3 in ["b"])) or not ((p4 in ["a"]) and (p4 in ["b"])) or not ((p5 in ["a"]) and (p5 in ["b"]))
}
```

```gq
gq 3;
query update_five_properties_tested_twice() {
    $x: Wide  not (($x.p1 in ["a"]) and ($x.p1 in ["b"])) or not (($x.p2 in ["a"]) and ($x.p2 in ["b"])) or not (($x.p3 in ["a"]) and ($x.p3 in ["b"])) or not (($x.p4 in ["a"]) and ($x.p4 in ["b"])) or not (($x.p5 in ["a"]) and ($x.p5 in ["b"]))
    update $x set { tag: "hit" }
}
```

### crates/omnigraph-gqt/cases/issue_1277_composed_and_wide_predicates_keep_null_rows_out.gqt#11:mutate

```gq
query update_four_properties_tested_twice() {
    update Wide set { tag: "hit" } where not ((p1 in ["a"]) and (p1 in ["b"])) or not ((p2 in ["a"]) and (p2 in ["b"])) or not ((p3 in ["a"]) and (p3 in ["b"])) or not ((p4 in ["a"]) and (p4 in ["b"]))
}
```

```gq
gq 3;
query update_four_properties_tested_twice() {
    $x: Wide  not (($x.p1 in ["a"]) and ($x.p1 in ["b"])) or not (($x.p2 in ["a"]) and ($x.p2 in ["b"])) or not (($x.p3 in ["a"]) and ($x.p3 in ["b"])) or not (($x.p4 in ["a"]) and ($x.p4 in ["b"]))
    update $x set { tag: "hit" }
}
```

### crates/omnigraph-gqt/cases/issue_1277_disjoint_in_lists_keep_null_rows_out.gqt#35:mutate

```gq
query update_or_of_negated_memberships() {
    update Item set { tag: "hit" } where not (s in ["x"]) or not (s in ["y"])
}
```

```gq
gq 3;
query update_or_of_negated_memberships() {
    $x: Item  not ($x.s in ["x"]) or not ($x.s in ["y"])
    update $x set { tag: "hit" }
}
```

### crates/omnigraph-gqt/cases/issue_1277_disjoint_in_lists_keep_null_rows_out.gqt#40:mutate

```gq
query delete_or_of_negated_memberships() {
    delete Item where not (s in ["x"]) or not (s in ["y"])
}
```

```gq
gq 3;
query delete_or_of_negated_memberships() {
    $x: Item  not ($x.s in ["x"]) or not ($x.s in ["y"])
    delete $x
}
```

### crates/omnigraph-gqt/cases/issue_473_merge_net_zero_branch.gqt#7:mutate

```gq
query drop_b_c() {
    delete Knows where from = "b"
}
```

```gq
gq 3;
query drop_b_c() {
    $x: Knows  $x.@src = "b"
    delete $x
}
```

### crates/omnigraph-gqt/cases/issue_563_aggregate_uncapped.gqt#3:query

```gq
query recall_count($q: String) {
    match {
        $c: Chunk
        search($c.text, $q)
    }
    return { count($c) as total }
    order { bm25($c.text, $q) }
    limit 2
}
```

```gq
gq 3;
query recall_count($q: String) {
    match { from bm25({ match {
        $c: Chunk
        search($c.text, $q)
    } return { $c } }, $c.text, $q) }
    return { count($c) as total }
    limit 2
}
```

### crates/omnigraph-gqt/cases/issue_563_underfill_retry.gqt#3:query

```gq
query recall($q: String) {
    match {
        $c: Chunk
        $c chunkOfArtifact $a
        search($c.text, $q)
    }
    return { $c.slug, $a.slug }
    order { bm25($c.text, $q) }
    limit 2
}
```

```gq
gq 3;
query recall($q: String) {
    match { from bm25({ match {
        $c: Chunk
        $c chunkOfArtifact $a
        search($c.text, $q)
    } return { $c, $a } }, $c.text, $q) }
    return { $c.slug, $a.slug }
    order { $score desc }
    limit 2
}
```

### crates/omnigraph-gqt/cases/issue_567_filtered_nearest_returns_exact_matches.gqt#3:query

```gq
query kept_nearest($q: Vector(4)) {
    match { $d: Doc { keep: true } }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 5
}
```

```gq
gq 3;
query kept_nearest($q: Vector(4)) {
    match { from nearest({ match { $d: Doc { keep: true } } return { $d } }, $d.embedding, $q, k: 5) }
    return { $d.slug }
    order { $distance }
    limit 5
}
```

### crates/omnigraph-gqt/cases/issue_567_traversal_nearest_fills_limit.gqt#3:query

```gq
query nearest_with_edge($q: Vector(4)) {
    match {
        $d: Doc
        $d knows $t
    }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 3
}
```

```gq
gq 3;
query nearest_with_edge($q: Vector(4)) {
    match { from nearest({ match {
        $d: Doc
        $d knows $t
    } return { $d, $t } }, $d.embedding, $q, k: 3) }
    return { $d.slug }
    order { $distance }
    limit 3
}
```

### crates/omnigraph-gqt/cases/issue_603_negation_string_predicate_outer_var.gqt#3:query

```gq
query not_prefixed() {
    match {
        $p: Person
        not { $p.name starts_with "a" }
    }
    return { $p.name }
}
```

```gq
gq 3;
query not_prefixed() {
    match {
        $p: Person
        not { starts_with($p.name, "a") }
    }
    return { $p.name }
}
```

### crates/omnigraph-gqt/cases/issue_624_second_merge_after_fast_forward.gqt#28:query

```gq
query vector_read_indexed_row($q: Vector(4)) {
    match { $p: Person }
    return { $p.name }
    order { nearest($p.embedding, $q) }
    limit 1
}
```

```gq
gq 3;
query vector_read_indexed_row($q: Vector(4)) {
    match { from nearest({ match { $p: Person } return { $p } }, $p.embedding, $q, k: 1) }
    return { $p.name }
    order { $distance }
    limit 1
}
```

### crates/omnigraph-gqt/cases/issue_624_second_merge_after_fast_forward.gqt#32:query

```gq
query vector_read_new_row($q: Vector(4)) {
    match { $p: Person }
    return { $p.name }
    order { nearest($p.embedding, $q) }
    limit 1
}
```

```gq
gq 3;
query vector_read_new_row($q: Vector(4)) {
    match { from nearest({ match { $p: Person } return { $p } }, $p.embedding, $q, k: 1) }
    return { $p.name }
    order { $distance }
    limit 1
}
```

### crates/omnigraph-gqt/cases/issue_634_blob_property_keeps_search_ranking.gqt#3:query

```gq
query ranked($q: String) {
    match {
        $c: Chunk
        search($c.text, $q)
    }
    return { $c.slug }
    order { bm25($c.text, $q) }
}
```

```gq
gq 3;
query ranked($q: String) {
    match { from bm25({ match {
        $c: Chunk
        search($c.text, $q)
    } return { $c } }, $c.text, $q) }
    return { $c.slug }
    order { $score desc }
}
```

### crates/omnigraph-gqt/cases/issue_634_blob_property_keeps_search_ranking.gqt#7:query

```gq
query closest($v: Vector(2)) {
    match { $c: Chunk }
    return { $c.slug }
    order { nearest($c.embedding, $v) }
    limit 2
}
```

```gq
gq 3;
query closest($v: Vector(2)) {
    match { from nearest({ match { $c: Chunk } return { $c } }, $c.embedding, $v, k: 2) }
    return { $c.slug }
    order { $distance }
    limit 2
}
```

### crates/omnigraph-gqt/cases/issue_639_shared_merged_parent_base.gqt#7:mutate

```gq
query set_a_on_x() {
    update Person set { age: 1 } where name = "a"
}
```

```gq
gq 3;
query set_a_on_x() {
    $x: Person  $x.name = "a"
    update $x set { age: 1 }
}
```

### crates/omnigraph-gqt/cases/issue_639_shared_merged_parent_base.gqt#13:mutate

```gq
query set_a_on_main() {
    update Person set { age: 2 } where name = "a"
}
```

```gq
gq 3;
query set_a_on_main() {
    $x: Person  $x.name = "a"
    update $x set { age: 2 }
}
```

### crates/omnigraph-gqt/cases/issue_639_shared_merged_parent_base.gqt#15:mutate

```gq
query set_b_on_t() {
    update Person set { age: 1 } where name = "b"
}
```

```gq
gq 3;
query set_b_on_t() {
    $x: Person  $x.name = "b"
    update $x set { age: 1 }
}
```

### crates/omnigraph-gqt/cases/issue_640_search_score_projection.gqt#3:query

```gq
query nearest_projected($v: Vector(2)) {
    match { $p: Person }
    return { $p.name, nearest($p.embedding, $v) as score }
    order { nearest($p.embedding, $v), nearest($p.embedding, $v) }
    limit 2
}
```

```gq
gq 3;
query nearest_projected($v: Vector(2)) {
    match { from nearest({ match { $p: Person } return { $p } }, $p.embedding, $v, k: 2) }
    return { $p.name, $distance as score }
    order { $distance, $distance }
    limit 2
}
```

### crates/omnigraph-gqt/cases/issue_640_search_score_projection.gqt#7:query

```gq
query nearest_projected_default_name($v: Vector(2)) {
    match { $p: Person }
    return { $p.name, nearest($p.embedding, $v) }
    order { nearest($p.embedding, $v), nearest($p.embedding, $v) }
    limit 1
}
```

```gq
gq 3;
query nearest_projected_default_name($v: Vector(2)) {
    match { from nearest({ match { $p: Person } return { $p } }, $p.embedding, $v, k: 1) }
    return { $p.name, $distance as _distance }
    order { $distance, $distance }
    limit 1
}
```

### crates/omnigraph-gqt/cases/issue_640_search_score_projection.gqt#11:query

```gq
query bm25_projected($q: String) {
    match { $p: Person }
    return { $p.name, bm25($p.bio, $q) }
    order { bm25($p.bio, $q) desc, bm25($p.bio, $q) desc }
    limit 3
}
```

```gq
gq 3;
query bm25_projected($q: String) {
    match { from bm25({ match { $p: Person } return { $p } }, $p.bio, $q) }
    return { $p.name, $score as _score }
    order { $score desc, $score desc }
    limit 3
}
```

### crates/omnigraph-gqt/cases/issue_640_search_score_projection.gqt#15:query

```gq
query nearest_projected_no_match($v: Vector(2)) {
    match { $p: Person { name: "absent" } }
    return { $p.name, nearest($p.embedding, $v) as score }
    order { nearest($p.embedding, $v) }
    limit 2
}
```

```gq
gq 3;
query nearest_projected_no_match($v: Vector(2)) {
    match { from nearest({ match { $p: Person { name: "absent" } } return { $p } }, $p.embedding, $v, k: 2) }
    return { $p.name, $distance as score }
    order { $distance }
    limit 2
}
```

### crates/omnigraph-gqt/cases/issue_640_search_score_projection.gqt#19:query

```gq
query bm25_projected_no_match($q: String) {
    match { $p: Person { name: "absent" } }
    return { $p.name, bm25($p.bio, $q) as score }
    order { bm25($p.bio, $q) desc }
    limit 3
}
```

```gq
gq 3;
query bm25_projected_no_match($q: String) {
    match { from bm25({ match { $p: Person { name: "absent" } } return { $p } }, $p.bio, $q) }
    return { $p.name, $score as score }
    order { $score desc }
    limit 3
}
```

### crates/omnigraph-gqt/cases/issue_640_search_score_projection.gqt#32:query

```gq
query rrf_projected($q: String, $v: Vector(2)) {
    match { $p: Person }
    return { $p.name, rrf(nearest($p.embedding, $v), bm25($p.bio, $q)) as fused }
    order { rrf(nearest($p.embedding, $v), bm25($p.bio, $q)), rrf(nearest($p.embedding, $v), bm25($p.bio, $q)) }
    limit 3
}
```

```gq
gq 3;
query rrf_projected($q: String, $v: Vector(2)) {
    match { let $p2 = { match { $p: Person } return { $p } }  from rrf(nearest($p2, $p.embedding, $v, k: 3), bm25($p2, $p.bio, $q), k: 60) }
    return { $p.name, $score as fused }
    order { $score desc, $score desc }
    limit 3
}
```

### crates/omnigraph-gqt/cases/issue_640_search_score_projection.gqt#41:query

```gq
query nearest_projected_through_traversal($v: Vector(2)) {
    match {
        $p: Person
        $p knows $f
    }
    return { $f.name, nearest($p.embedding, $v) as score }
    order { nearest($p.embedding, $v), $f.name }
    limit 3
}
```

```gq
gq 3;
query nearest_projected_through_traversal($v: Vector(2)) {
    match { from nearest({ match {
        $p: Person
        $p knows $f
    } return { $p, $f } }, $p.embedding, $v, k: 3) }
    return { $f.name, $distance as score }
    order { $distance, $f.name }
    limit 3
}
```

### crates/omnigraph-gqt/cases/issue_640_search_score_projection.gqt#45:query

```gq
query nearest_ordered_plain_projection($v: Vector(2)) {
    match { $p: Person }
    return { $p.name }
    order { nearest($p.embedding, $v) }
    limit 2
}
```

```gq
gq 3;
query nearest_ordered_plain_projection($v: Vector(2)) {
    match { from nearest({ match { $p: Person } return { $p } }, $p.embedding, $v, k: 2) }
    return { $p.name }
    order { $distance }
    limit 2
}
```

### crates/omnigraph-gqt/cases/issue_659_common_property_shapes.gqt#7:query

```gq
query selected_list_predicate() {
    match { $a: Person $a $e:(knows | likes) $b $e.tags contains "x" }
    return { $e.@type as kind }
}
```

```gq
gq 3;
query selected_list_predicate() {
    match { $a: Person $a $e:(knows | likes) $b "x" in $e.tags }
    return { $e.@type as kind }
}
```

### crates/omnigraph-gqt/cases/issue_659_common_property_shapes.gqt#16:query

```gq
query nullable_block_argument_counts_nonnull_values() {
    match {
        $a: Person { name: "a" }
        count($e.state contains "open") { $a $e:(knows | likes) $b } = 2
    }
    return { $a.name as name }
}
```

```gq
gq 3;
query nullable_block_argument_counts_nonnull_values() {
    match {
        $a: Person { name: "a" }
        count(contains($e.state, "open")) { $a $e:(knows | likes) $b } = 2
    }
    return { $a.name as name }
}
```

### crates/omnigraph-gqt/cases/issue_659_common_property_shapes.gqt#19:query

```gq
query nullable_block_argument_parameter_counts_nonnull_values($term: String) {
    match {
        $a: Person { name: "a" }
        count($e.state contains $term) { $a $e:(knows | likes) $b } = 2
    }
    return { $a.name as name }
}
```

```gq
gq 3;
query nullable_block_argument_parameter_counts_nonnull_values($term: String) {
    match {
        $a: Person { name: "a" }
        count(contains($e.state, $term)) { $a $e:(knows | likes) $b } = 2
    }
    return { $a.name as name }
}
```

### crates/omnigraph-gqt/cases/issue_659_compiler_scopes.gqt#30:query

```gq
query named_child_string_argument() {
    match { $p: Person count($f.name contains "x") { $p knows $f } > 0 }
    return { $p.name as person }
}
```

```gq
gq 3;
query named_child_string_argument() {
    match { $p: Person count(contains($f.name, "x")) { $p knows $f } > 0 }
    return { $p.name as person }
}
```

### crates/omnigraph-gqt/cases/issue_659_member_direction_and_types.gqt#20:query

```gq
query correlated_common_string_argument() {
    match { $p: Person count($e.label contains "own") { $p $e:(owns | ownedBy) $d } = 2 }
    return { $p.name as name }
}
```

```gq
gq 3;
query correlated_common_string_argument() {
    match { $p: Person count(contains($e.label, "own")) { $p $e:(owns | ownedBy) $d } = 2 }
    return { $p.name as name }
}
```

### crates/omnigraph-gqt/cases/issue_659_ranked_selection_differential.gqt#3:query

```gq
query nearest_named($q: Vector(4)) {
    match { $d: Doc $t: Doc $d knows $t  }
    return { $d.slug as slug }
    order { nearest($d.embedding, $q) }
    limit 3
}
```

```gq
gq 3;
query nearest_named($q: Vector(4)) {
    match { from nearest({ match { $d: Doc $t: Doc $d knows $t  } return { $d, $t } }, $d.embedding, $q, k: 3) }
    return { $d.slug as slug }
    order { $distance }
    limit 3
}
```

### crates/omnigraph-gqt/cases/issue_659_ranked_selection_differential.gqt#8:query

```gq
query nearest_alternative($q: Vector(4)) {
    match { $d: Doc $t: Doc $d (knows | knows) $t  }
    return { $d.slug as slug }
    order { nearest($d.embedding, $q) }
    limit 3
}
```

```gq
gq 3;
query nearest_alternative($q: Vector(4)) {
    match { from nearest({ match { $d: Doc $t: Doc $d (knows | knows) $t  } return { $d, $t } }, $d.embedding, $q, k: 3) }
    return { $d.slug as slug }
    order { $distance }
    limit 3
}
```

### crates/omnigraph-gqt/cases/issue_659_ranked_selection_differential.gqt#13:query

```gq
query nearest_wildcard($q: Vector(4)) {
    match { $d: Doc $t: Doc $d * $t  }
    return { $d.slug as slug }
    order { nearest($d.embedding, $q) }
    limit 3
}
```

```gq
gq 3;
query nearest_wildcard($q: Vector(4)) {
    match { from nearest({ match { $d: Doc $t: Doc $d * $t  } return { $d, $t } }, $d.embedding, $q, k: 3) }
    return { $d.slug as slug }
    order { $distance }
    limit 3
}
```

### crates/omnigraph-gqt/cases/issue_659_ranked_selection_differential.gqt#18:query

```gq
query nearest_named_chain($q: Vector(4)) {
    match { $d: Doc $t: Doc $d knows $t $t tagged $m }
    return { $d.slug as slug }
    order { nearest($d.embedding, $q) }
    limit 3
}
```

```gq
gq 3;
query nearest_named_chain($q: Vector(4)) {
    match { from nearest({ match { $d: Doc $t: Doc $d knows $t $t tagged $m } return { $d, $t, $m } }, $d.embedding, $q, k: 3) }
    return { $d.slug as slug }
    order { $distance }
    limit 3
}
```

### crates/omnigraph-gqt/cases/issue_659_ranked_selection_differential.gqt#23:query

```gq
query nearest_selected_chain($q: Vector(4)) {
    match { $d: Doc $t: Doc $d knows $t $t (tagged | tagged) $m }
    return { $d.slug as slug }
    order { nearest($d.embedding, $q) }
    limit 3
}
```

```gq
gq 3;
query nearest_selected_chain($q: Vector(4)) {
    match { from nearest({ match { $d: Doc $t: Doc $d knows $t $t (tagged | tagged) $m } return { $d, $t, $m } }, $d.embedding, $q, k: 3) }
    return { $d.slug as slug }
    order { $distance }
    limit 3
}
```

### crates/omnigraph-gqt/cases/issue_659_ranked_selection_differential.gqt#28:query

```gq
query fused_sparse_named($q: Vector(4), $other: Vector(4)) {
    match { $d: Doc $t: Doc $d knows $t }
    return { $d.slug as slug }
    order { rrf(nearest($d.embedding, $q), nearest($d.embedding, $other)) }
    limit 3
}
```

```gq
gq 3;
query fused_sparse_named($q: Vector(4), $other: Vector(4)) {
    match { let $p = { match { $d: Doc $t: Doc $d knows $t } return { $d, $t } }  from rrf(nearest($p, $d.embedding, $q, k: 3), nearest($p, $d.embedding, $other, k: 3), k: 60) }
    return { $d.slug as slug }
    order { $score desc }
    limit 3
}
```

### crates/omnigraph-gqt/cases/issue_659_ranked_selection_differential.gqt#33:query

```gq
query fused_sparse_wildcard($q: Vector(4), $other: Vector(4)) {
    match { $d: Doc $t: Doc $d * $t }
    return { $d.slug as slug }
    order { rrf(nearest($d.embedding, $q), nearest($d.embedding, $other)) }
    limit 3
}
```

```gq
gq 3;
query fused_sparse_wildcard($q: Vector(4), $other: Vector(4)) {
    match { let $p = { match { $d: Doc $t: Doc $d * $t } return { $d, $t } }  from rrf(nearest($p, $d.embedding, $q, k: 3), nearest($p, $d.embedding, $other, k: 3), k: 60) }
    return { $d.slug as slug }
    order { $score desc }
    limit 3
}
```

### crates/omnigraph-gqt/cases/issue_660_exact_edge_deletion.gqt#3:mutate

```gq
query group_the_middle_ranks() {
    update Person set { group: "middle" } where rank > 1 and rank < 4
}
```

```gq
gq 3;
query group_the_middle_ranks() {
    $x: Person  $x.rank > 1 and $x.rank < 4
    update $x set { group: "middle" }
}
```

### crates/omnigraph-gqt/cases/issue_660_exact_edge_deletion.gqt#10:mutate

```gq
query delete_the_pair() {
    delete Knows where @src = "a" and @dst = "b"
}
```

```gq
gq 3;
query delete_the_pair() {
    $x: Knows  $x.@src = "a" and $x.@dst = "b"
    delete $x
}
```

### crates/omnigraph-gqt/cases/issue_660_exact_edge_deletion.gqt#24:mutate

```gq
query delete_by_parameters($src: String, $dst: String) {
    delete Knows where @src = $src and @dst = $dst
}
```

```gq
gq 3;
query delete_by_parameters($src: String, $dst: String) {
    $x: Knows  $x.@src = $src and $x.@dst = $dst
    delete $x
}
```

### crates/omnigraph-gqt/cases/issue_665_nearest_retry_same_limit.gqt#3:query

```gq
query nearest_with_edge($q: Vector(4)) {
    match {
        $d: Doc
        $d knows $t
    }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 1
}
```

```gq
gq 3;
query nearest_with_edge($q: Vector(4)) {
    match { from nearest({ match {
        $d: Doc
        $d knows $t
    } return { $d, $t } }, $d.embedding, $q, k: 1) }
    return { $d.slug }
    order { $distance }
    limit 1
}
```

### crates/omnigraph-gqt/cases/issue_671_date_string_with_time_of_day_refused.gqt#6:mutate

```gq
query touch_on_datetime_literal() {
    update Event set { name: "moved" } where day = date("2024-01-01T02:00:00+05:00")
}
```

```gq
gq 3;
query touch_on_datetime_literal() {
    $x: Event  $x.day = date("2024-01-01T02:00:00+05:00")
    update $x set { name: "moved" }
}
```

### crates/omnigraph-gqt/cases/issue_681_sibling_merge_readopts_deleted_edge.gqt#9:mutate

```gq
query delete() {
    delete Knows where from = "alice"
}
```

```gq
gq 3;
query delete() {
    $x: Knows  $x.@src = "alice"
    delete $x
}
```

### crates/omnigraph-gqt/cases/issue_773_delete_survives_merge_after_older_branch_merge.gqt#19:mutate

```gq
query delete_w2() {
    delete Person where name = "w2"
}
```

```gq
gq 3;
query delete_w2() {
    $x: Person  $x.name = "w2"
    delete $x
}
```

### crates/omnigraph-gqt/cases/issue_773_merge_commit_base_excludes_its_merged_parent.gqt#13:mutate

```gq
query delete_w() {
    delete Person where name = "w"
}
```

```gq
gq 3;
query delete_w() {
    $x: Person  $x.name = "w"
    delete $x
}
```

### crates/omnigraph-gqt/cases/issue_773_remerge_of_merged_source_is_up_to_date.gqt#11:mutate

```gq
query delete_z() {
    delete Person where name = "z"
}
```

```gq
gq 3;
query delete_z() {
    $x: Person  $x.name = "z"
    delete $x
}
```

### crates/omnigraph-gqt/cases/issue_773_update_conflicts_after_source_merged_older_branch.gqt#5:mutate

```gq
query set_alice_1() {
    update Person set { age: 1 } where name = "alice"
}
```

```gq
gq 3;
query set_alice_1() {
    $x: Person  $x.name = "alice"
    update $x set { age: 1 }
}
```

### crates/omnigraph-gqt/cases/issue_773_update_conflicts_after_source_merged_older_branch.gqt#19:mutate

```gq
query set_alice_2() {
    update Person set { age: 2 } where name = "alice"
}
```

```gq
gq 3;
query set_alice_2() {
    $x: Person  $x.name = "alice"
    update $x set { age: 2 }
}
```

### crates/omnigraph-gqt/cases/issue_787_rrf_order_keys_after_fused_score.gqt#3:query

```gq
query secondary_key_breaks_fused_tie() {
    match { $d: Doc }
    return { $d.slug }
    order { rrf(bm25($d.text, "alpha"), bm25($d.text, "beta")), $d.slug desc }
    limit 1
}
```

```gq
gq 3;
query secondary_key_breaks_fused_tie() {
    match { let $p = { match { $d: Doc } return { $d } }  from rrf(bm25($p, $d.text, "alpha"), bm25($p, $d.text, "beta"), k: 60) }
    return { $d.slug }
    order { $score desc, $d.slug desc }
    limit 1
}
```

### crates/omnigraph-gqt/cases/issue_787_rrf_order_keys_after_fused_score.gqt#6:query

```gq
query id_breaks_fused_tie() {
    match { $d: Doc }
    return { $d.slug }
    order { rrf(bm25($d.text, "beta"), bm25($d.text, "alpha")) }
    limit 1
}
```

```gq
gq 3;
query id_breaks_fused_tie() {
    match { let $p = { match { $d: Doc } return { $d } }  from rrf(bm25($p, $d.text, "beta"), bm25($p, $d.text, "alpha"), k: 60) }
    return { $d.slug }
    order { $score desc }
    limit 1
}
```

### crates/omnigraph-gqt/cases/issue_788_search_led_aggregate_order_keys.gqt#3:query

```gq
set engine = v2;
query largest_group($t: String) {
    match { $d: Doc }
    return { $d.kind, count($d) as n }
    order { bm25($d.text, $t), n desc }
    limit 1
}
```

```gq
gq 3;
set engine = v2;
query largest_group($t: String) {
    match { from bm25({ match { $d: Doc } return { $d } }, $d.text, $t) }
    return { $d.kind, count($d) as n }
    order { n desc }
    limit 1
}
```

### crates/omnigraph-gqt/cases/issue_788_search_led_aggregate_order_keys.gqt#7:query

```gq
set engine = v2;
query smallest_group($t: String) {
    match { $d: Doc }
    return { $d.kind, count($d) as n }
    order { bm25($d.text, $t), n asc }
    limit 1
}
```

```gq
gq 3;
set engine = v2;
query smallest_group($t: String) {
    match { from bm25({ match { $d: Doc } return { $d } }, $d.text, $t) }
    return { $d.kind, count($d) as n }
    order { n asc }
    limit 1
}
```

### crates/omnigraph-gqt/cases/issue_788_search_led_aggregate_order_keys.gqt#11:query

```gq
set engine = v2;
query every_group_ordered($t: String) {
    match { $d: Doc }
    return { $d.kind, count($d) as n }
    order { bm25($d.text, $t), n desc }
    limit 10
}
```

```gq
gq 3;
set engine = v2;
query every_group_ordered($t: String) {
    match { from bm25({ match { $d: Doc } return { $d } }, $d.text, $t) }
    return { $d.kind, count($d) as n }
    order { n desc }
    limit 10
}
```

### crates/omnigraph-gqt/cases/issue_801_in_predicate_over_a_list.gqt#25:query

```gq
set engine = v2;
query contains_spelling($ms: [String]) {
    match {
        $m: Matter
        ($ms contains $m.number)
    }
    return { $m.number }
}
```

```gq
gq 3;
set engine = v2;
query contains_spelling($ms: [String]) {
    match {
        $m: Matter
        ($m.number in $ms)
    }
    return { $m.number }
}
```

### crates/omnigraph-gqt/cases/issue_801_in_predicate_over_a_list.gqt#67:mutate

```gq
query recode($ms: [String]) {
    update Matter set { code: 9 } where number in $ms
}
```

```gq
gq 3;
query recode($ms: [String]) {
    $x: Matter  $x.number in $ms
    update $x set { code: 9 }
}
```

### crates/omnigraph-gqt/cases/issue_801_in_predicate_over_a_list.gqt#73:mutate

```gq
query recode_no_member($ms: [String]) {
    update Matter set { code: 7 } where number in $ms
}
```

```gq
gq 3;
query recode_no_member($ms: [String]) {
    $x: Matter  $x.number in $ms
    update $x set { code: 7 }
}
```

### crates/omnigraph-gqt/cases/issue_801_in_predicate_over_a_list.gqt#76:mutate

```gq
query recode_outside_the_empty_set($cs: [I64]) {
    update Matter set { code: 7 } where not (code in $cs)
}
```

```gq
gq 3;
query recode_outside_the_empty_set($cs: [I64]) {
    $x: Matter  not ($x.code in $cs)
    update $x set { code: 7 }
}
```

### crates/omnigraph-gqt/cases/issue_857_datetime_sub_millisecond_digits_refused.gqt#15:mutate

```gq
query move_now() { update Event set { at: datetime("2025-01-01T00:00:00Z") } where at = now() }
```

```gq
gq 3;
query move_now() { $x: Event  $x.at = now()  update $x set { at: datetime("2025-01-01T00:00:00Z") } }
```

### crates/omnigraph-gqt/cases/issue_857_datetime_sub_millisecond_digits_refused.gqt#18:mutate

```gq
query set_now() { update Event set { at: now() } where name = "a" }
```

```gq
gq 3;
query set_now() { $x: Event  $x.name = "a"  update $x set { at: now() } }
```

### crates/omnigraph-gqt/cases/issue_920_out_of_range_float_literal.gqt#5:query

```gq
query out_of_range_nearest_vector_literal() {
    match {
        $x: N
    }
    return { $x.f }
    order { nearest($x.embedding, [999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999.0, 0.0]) }
    limit 1
}
```

```gq
gq 3;
query out_of_range_nearest_vector_literal() {
    match { from nearest({ match {
        $x: N
    } return { $x } }, $x.embedding, [999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999.0, 0.0], k: 1) }
    return { $x.f }
    order { $distance }
    limit 1
}
```

### crates/omnigraph-gqt/cases/issue_953_return_distinct.gqt#3:query

```gq
set engine = v2;
query members_plain() {
    match {
        $f: Forum
        $f hasMember $p
        $f.title contains "graph"
    }
    return { $p.name }
}
```

```gq
gq 3;
set engine = v2;
query members_plain() {
    match {
        $f: Forum
        $f hasMember $p
        contains($f.title, "graph")
    }
    return { $p.name }
}
```

### crates/omnigraph-gqt/cases/issue_953_return_distinct.gqt#6:query

```gq
set engine = v2;
query members_distinct() {
    match {
        $f: Forum
        $f hasMember $p
        $f.title contains "graph"
    }
    return distinct { $p.name }
}
```

```gq
gq 3;
set engine = v2;
query members_distinct() {
    match {
        $f: Forum
        $f hasMember $p
        contains($f.title, "graph")
    }
    return distinct { $p.name }
}
```

### crates/omnigraph-gqt/cases/issue_953_return_distinct.gqt#12:query

```gq
set engine = v2;
query first_two_names() {
    match {
        $f: Forum
        $f hasMember $p
        $f.title contains "graph"
    }
    return distinct { $p.name }
    order { $p.name asc }
    limit 2
}
```

```gq
gq 3;
set engine = v2;
query first_two_names() {
    match {
        $f: Forum
        $f hasMember $p
        contains($f.title, "graph")
    }
    return distinct { $p.name }
    order { $p.name asc }
    limit 2
}
```

### crates/omnigraph-gqt/cases/issue_953_return_distinct.gqt#18:query

```gq
set engine = v2;
query members_as_nodes() {
    match {
        $f: Forum
        $f hasMember $p
        $f.title contains "graph"
    }
    return distinct { $p }
    order { $p }
}
```

```gq
gq 3;
set engine = v2;
query members_as_nodes() {
    match {
        $f: Forum
        $f hasMember $p
        contains($f.title, "graph")
    }
    return distinct { $p }
    order { $p }
}
```

### crates/omnigraph-gqt/cases/issue_969_graph_diff_wider_windows.gqt#6:mutate

```gq
query alice_ages() {
    update Person set { age: 31 } where name = "alice"
}
```

```gq
gq 3;
query alice_ages() {
    $x: Person  $x.name = "alice"
    update $x set { age: 31 }
}
```

### crates/omnigraph-gqt/cases/issue_969_graph_diff_wider_windows.gqt#8:mutate

```gq
query alice_ages_again() {
    update Person set { age: 32 } where name = "alice"
}
```

```gq
gq 3;
query alice_ages_again() {
    $x: Person  $x.name = "alice"
    update $x set { age: 32 }
}
```

### crates/omnigraph-gqt/cases/issue_969_graph_diff_wider_windows.gqt#12:mutate

```gq
query dave_leaves() {
    delete Person where name = "dave"
}
```

```gq
gq 3;
query dave_leaves() {
    $x: Person  $x.name = "dave"
    delete $x
}
```

### crates/omnigraph-gqt/cases/issue_969_graph_diff_wider_windows.gqt#14:mutate

```gq
query carol_leaves() {
    delete Person where name = "carol"
}
```

```gq
gq 3;
query carol_leaves() {
    $x: Person  $x.name = "carol"
    delete $x
}
```

### crates/omnigraph-gqt/cases/issue_969_graph_diff_wider_windows.gqt#28:mutate

```gq
query erin_ages_on_a() {
    update Person set { age: 26 } where name = "erin"
}
```

```gq
gq 3;
query erin_ages_on_a() {
    $x: Person  $x.name = "erin"
    update $x set { age: 26 }
}
```

### crates/omnigraph-gqt/cases/issue_969_graph_diff_wider_windows.gqt#31:mutate

```gq
query erin_ages_on_b() {
    update Person set { age: 27 } where name = "erin"
}
```

```gq
gq 3;
query erin_ages_on_b() {
    $x: Person  $x.name = "erin"
    update $x set { age: 27 }
}
```

### crates/omnigraph-gqt/cases/keyed_edge_delete_after_merge.gqt#16:mutate

```gq
query delete_alice_edges() { delete Knows where from = "Alice" }
```

```gq
gq 3;
query delete_alice_edges() { $x: Knows  $x.@src = "Alice"  delete $x }
```

### crates/omnigraph-gqt/cases/keyed_edge_delete_wins_over_readd.gqt#9:mutate

```gq
query delete() {
    delete Knows where from = "alice"
}
```

```gq
gq 3;
query delete() {
    $x: Knows  $x.@src = "alice"
    delete $x
}
```

### crates/omnigraph-gqt/cases/keyword_named_vector_property_index.gqt#3:query

```gq
query nearest_by_keyword_property($q: Vector(2)) {
    match { $d: Doc }
    return { $d.slug }
    order { nearest($d.trim, $q) }
    limit 2
}
```

```gq
gq 3;
query nearest_by_keyword_property($q: Vector(2)) {
    match { from nearest({ match { $d: Doc } return { $d } }, $d.trim, $q, k: 2) }
    return { $d.slug }
    order { $distance }
    limit 2
}
```

### crates/omnigraph-gqt/cases/list_membership_compares_numbers_across_integer_and_float.gqt#3:mutate

```gq
query assign($xs: [I64]) {
    update Person set { adult: $xs contains 1.0 } where slug = "a"
}
```

```gq
gq 3;
query assign($xs: [I64]) {
    $x: Person  $x.slug = "a"
    update $x set { adult: 1.0 in $xs }
}
```

### crates/omnigraph-gqt/cases/list_membership_compares_numbers_across_integer_and_float.gqt#6:query

```gq
set engine = v2;
query read($xs: [I64]) {
    match { $p: Person }
    return { $p.adult, $xs contains 1.0 as projected, $xs contains 2.0 as absent, [1] contains 1.0 as folded }
}
```

```gq
gq 3;
set engine = v2;
query read($xs: [I64]) {
    match { $p: Person }
    return { $p.adult, 1.0 in $xs as projected, 2.0 in $xs as absent, 1.0 in [1] as folded }
}
```

### crates/omnigraph-gqt/cases/list_membership_compares_numbers_across_integer_and_float.gqt#10:mutate

```gq
query assign_null_needle($xs: [I64], $needle: F64?) {
    update Person set { maybe: $xs contains $needle } where slug = "a"
}
```

```gq
gq 3;
query assign_null_needle($xs: [I64], $needle: F64?) {
    $x: Person  $x.slug = "a"
    update $x set { maybe: $needle in $xs }
}
```

### crates/omnigraph-gqt/cases/list_membership_over_indexed_columns_across_writes_and_restart.gqt#62:mutate

```gq
query delete_covered_member() {
    delete Item where slug = "c"
}
```

```gq
gq 3;
query delete_covered_member() {
    $x: Item  $x.slug = "c"
    delete $x
}
```

### crates/omnigraph-gqt/cases/load_csv_plan_overwrite_emptied_target_omits_removal_on_branch_and_after_restart.gqt#5:mutate

```gq
query empty_person_on_work() { delete Person where id = "p1" or id = "p2" }
```

```gq
gq 3;
query empty_person_on_work() { $x: Person  $x.id = "p1" or $x.id = "p2"  delete $x }
```

### crates/omnigraph-gqt/cases/load_csv_plan_overwrite_emptied_target_omits_removal_on_branch_and_after_restart.gqt#14:mutate

```gq
query empty_person_on_main() { delete Person where id = "p1" or id = "p2" }
```

```gq
gq 3;
query empty_person_on_main() { $x: Person  $x.id = "p1" or $x.id = "p2"  delete $x }
```

### crates/omnigraph-gqt/cases/long_branch_names_first_touch.gqt#5:mutate

```gq
query write() {
    update Person set { age: 31 } where name = "alice"
}
```

```gq
gq 3;
query write() {
    $x: Person  $x.name = "alice"
    update $x set { age: 31 }
}
```

### crates/omnigraph-gqt/cases/long_branch_names_first_touch.gqt#12:mutate

```gq
query write() {
    update Person set { age: 31 } where name = "alice"
}
```

```gq
gq 3;
query write() {
    $x: Person  $x.name = "alice"
    update $x set { age: 31 }
}
```

### crates/omnigraph-gqt/cases/long_branch_names_first_touch.gqt#19:mutate

```gq
query write() {
    update Person set { age: 31 } where name = "alice"
}
```

```gq
gq 3;
query write() {
    $x: Person  $x.name = "alice"
    update $x set { age: 31 }
}
```

### crates/omnigraph-gqt/cases/long_branch_names_first_touch.gqt#26:mutate

```gq
query write() {
    update Person set { age: 31 } where name = "alice"
}
```

```gq
gq 3;
query write() {
    $x: Person  $x.name = "alice"
    update $x set { age: 31 }
}
```

### crates/omnigraph-gqt/cases/merge_adopt_lazy_target_below_inherited_version.gqt#5:mutate

```gq
query a_2_on_main() {
    update Person set { age: 2 } where name = "a"
}
```

```gq
gq 3;
query a_2_on_main() {
    $x: Person  $x.name = "a"
    update $x set { age: 2 }
}
```

### crates/omnigraph-gqt/cases/merge_adopt_lazy_target_below_inherited_version.gqt#7:mutate

```gq
query a_3_on_main() {
    update Person set { age: 3 } where name = "a"
}
```

```gq
gq 3;
query a_3_on_main() {
    $x: Person  $x.name = "a"
    update $x set { age: 3 }
}
```

### crates/omnigraph-gqt/cases/merge_adopt_lazy_target_below_inherited_version.gqt#9:mutate

```gq
query a_4_on_main() {
    update Person set { age: 4 } where name = "a"
}
```

```gq
gq 3;
query a_4_on_main() {
    $x: Person  $x.name = "a"
    update $x set { age: 4 }
}
```

### crates/omnigraph-gqt/cases/merge_adopt_lazy_target_below_inherited_version.gqt#11:mutate

```gq
query a_5_on_main() {
    update Person set { age: 5 } where name = "a"
}
```

```gq
gq 3;
query a_5_on_main() {
    $x: Person  $x.name = "a"
    update $x set { age: 5 }
}
```

### crates/omnigraph-gqt/cases/merge_adopt_lazy_target_below_inherited_version.gqt#13:mutate

```gq
query b_9_on_feature() {
    update Person set { age: 9 } where name = "b"
}
```

```gq
gq 3;
query b_9_on_feature() {
    $x: Person  $x.name = "b"
    update $x set { age: 9 }
}
```

### crates/omnigraph-gqt/cases/merge_adopt_lazy_target_below_inherited_version.gqt#17:mutate

```gq
query a_50_on_feature() {
    update Person set { age: 50 } where name = "a"
}
```

```gq
gq 3;
query a_50_on_feature() {
    $x: Person  $x.name = "a"
    update $x set { age: 50 }
}
```

### crates/omnigraph-gqt/cases/merge_adopt_lower_source_version_keeps_edges.gqt#9:mutate

```gq
query drop_b_c() {
    delete Knows where from = "b"
}
```

```gq
gq 3;
query drop_b_c() {
    $x: Knows  $x.@src = "b"
    delete $x
}
```

### crates/omnigraph-gqt/cases/merge_after_branch_recreation.gqt#17:mutate

```gq
query recreated_source_deletes_bob() {
    delete Person where name = "bob"
}
```

```gq
gq 3;
query recreated_source_deletes_bob() {
    $x: Person  $x.name = "bob"
    delete $x
}
```

### crates/omnigraph-gqt/cases/merge_after_branch_recreation.gqt#19:mutate

```gq
query old_child_changes_alice() {
    update Person set { age: 31 } where name = "alice"
}
```

```gq
gq 3;
query old_child_changes_alice() {
    $x: Person  $x.name = "alice"
    update $x set { age: 31 }
}
```

### crates/omnigraph-gqt/cases/merge_back_after_three_way_merge.gqt#11:mutate

```gq
query remove_bob() {
    delete Person where name = "bob"
}
```

```gq
gq 3;
query remove_bob() {
    $x: Person  $x.name = "bob"
    delete $x
}
```

### crates/omnigraph-gqt/cases/merge_back_after_three_way_merge.gqt#13:mutate

```gq
query remove_carol() {
    delete Person where name = "carol"
}
```

```gq
gq 3;
query remove_carol() {
    $x: Person  $x.name = "carol"
    delete $x
}
```

### crates/omnigraph-gqt/cases/merge_back_after_three_way_merge.gqt#17:mutate

```gq
query birthday() {
    update Person set { age: 31 } where name = "alice"
}
```

```gq
gq 3;
query birthday() {
    $x: Person  $x.name = "alice"
    update $x set { age: 31 }
}
```

### crates/omnigraph-gqt/cases/merge_back_conflict_preserves_rows.gqt#11:mutate

```gq
query source_deletes_bob() {
    delete Person where name = "bob"
}
```

```gq
gq 3;
query source_deletes_bob() {
    $x: Person  $x.name = "bob"
    delete $x
}
```

### crates/omnigraph-gqt/cases/merge_back_conflict_preserves_rows.gqt#13:mutate

```gq
query source_changes_alice() {
    update Person set { age: 32 } where name = "alice"
}
```

```gq
gq 3;
query source_changes_alice() {
    $x: Person  $x.name = "alice"
    update $x set { age: 32 }
}
```

### crates/omnigraph-gqt/cases/merge_back_conflict_preserves_rows.gqt#15:mutate

```gq
query target_changes_alice() {
    update Person set { age: 31 } where name = "alice"
}
```

```gq
gq 3;
query target_changes_alice() {
    $x: Person  $x.name = "alice"
    update $x set { age: 31 }
}
```

### crates/omnigraph-gqt/cases/merge_base_criss_cross_higher_generation_wins.gqt#7:mutate

```gq
query right_first() {
    update Person set { age: 1 } where name = "b"
}
```

```gq
gq 3;
query right_first() {
    $x: Person  $x.name = "b"
    update $x set { age: 1 }
}
```

### crates/omnigraph-gqt/cases/merge_base_criss_cross_higher_generation_wins.gqt#9:mutate

```gq
query left_first() {
    update Person set { age: 1 } where name = "a"
}
```

```gq
gq 3;
query left_first() {
    $x: Person  $x.name = "a"
    update $x set { age: 1 }
}
```

### crates/omnigraph-gqt/cases/merge_base_criss_cross_higher_generation_wins.gqt#11:mutate

```gq
query left_second() {
    update Person set { age: 2 } where name = "a"
}
```

```gq
gq 3;
query left_second() {
    $x: Person  $x.name = "a"
    update $x set { age: 2 }
}
```

### crates/omnigraph-gqt/cases/merge_base_criss_cross_higher_generation_wins.gqt#21:mutate

```gq
query right_later() {
    update Person set { age: 7 } where name = "a"
}
```

```gq
gq 3;
query right_later() {
    $x: Person  $x.name = "a"
    update $x set { age: 7 }
}
```

### crates/omnigraph-gqt/cases/merge_base_criss_cross_preserves_descendant_edit.gqt#7:mutate

```gq
query left_first() {
    update Person set { age: 1 } where age = 0
}
```

```gq
gq 3;
query left_first() {
    $x: Person  $x.age = 0
    update $x set { age: 1 }
}
```

### crates/omnigraph-gqt/cases/merge_base_criss_cross_preserves_descendant_edit.gqt#9:mutate

```gq
query right_first() {
    update Person set { age: 1 } where age = 0
}
```

```gq
gq 3;
query right_first() {
    $x: Person  $x.age = 0
    update $x set { age: 1 }
}
```

### crates/omnigraph-gqt/cases/merge_base_criss_cross_preserves_descendant_edit.gqt#19:mutate

```gq
query right_later() {
    update Person set { age: 7 } where name = "a"
}
```

```gq
gq 3;
query right_later() {
    $x: Person  $x.name = "a"
    update $x set { age: 7 }
}
```

### crates/omnigraph-gqt/cases/merge_base_one_sided_deleted_parent.gqt#7:mutate

```gq
query set_a_on_x() {
    update Person set { age: 1 } where name = "a"
}
```

```gq
gq 3;
query set_a_on_x() {
    $x: Person  $x.name = "a"
    update $x set { age: 1 }
}
```

### crates/omnigraph-gqt/cases/merge_base_one_sided_deleted_parent.gqt#13:mutate

```gq
query set_b_on_s() {
    update Person set { age: 1 } where name = "b"
}
```

```gq
gq 3;
query set_b_on_s() {
    $x: Person  $x.name = "b"
    update $x set { age: 1 }
}
```

### crates/omnigraph-gqt/cases/merge_base_recorded_in_a_released_merge_commit.gqt#9:mutate

```gq
query set_bob_on_s_once() {
    update Person set { age: 1 } where name = "bob"
}
```

```gq
gq 3;
query set_bob_on_s_once() {
    $x: Person  $x.name = "bob"
    update $x set { age: 1 }
}
```

### crates/omnigraph-gqt/cases/merge_base_recorded_in_a_released_merge_commit.gqt#11:mutate

```gq
query diverge_target() {
    update Person set { age: 1 } where name = "alice"
}
```

```gq
gq 3;
query diverge_target() {
    $x: Person  $x.name = "alice"
    update $x set { age: 1 }
}
```

### crates/omnigraph-gqt/cases/merge_base_recorded_in_a_released_merge_commit.gqt#16:mutate

```gq
query bump_alice($n: I32) {
    update Person set { age: $n } where name = "alice"
}
```

```gq
gq 3;
query bump_alice($n: I32) {
    $x: Person  $x.name = "alice"
    update $x set { age: $n }
}
```

### crates/omnigraph-gqt/cases/merge_base_recorded_in_a_released_merge_commit.gqt#21:mutate

```gq
query set_bob_on_s_twice() {
    update Person set { age: 2 } where name = "bob"
}
```

```gq
gq 3;
query set_bob_on_s_twice() {
    $x: Person  $x.name = "bob"
    update $x set { age: 2 }
}
```

### crates/omnigraph-gqt/cases/merge_base_repeat_fast_forward_through_merged_parent.gqt#5:mutate

```gq
query s_sets_a() {
    update Person set { age: 1 } where name = "a"
}
```

```gq
gq 3;
query s_sets_a() {
    $x: Person  $x.name = "a"
    update $x set { age: 1 }
}
```

### crates/omnigraph-gqt/cases/merge_base_repeat_fast_forward_through_merged_parent.gqt#9:mutate

```gq
query s_sets_b() {
    update Person set { age: 1 } where name = "b"
}
```

```gq
gq 3;
query s_sets_b() {
    $x: Person  $x.name = "b"
    update $x set { age: 1 }
}
```

### crates/omnigraph-gqt/cases/merge_base_siblings_that_merged_an_older_branch_keep_its_later_delete.gqt#7:mutate

```gq
query touch_p_once() {
    update Person set { age: 0 } where name = "p"
}
```

```gq
gq 3;
query touch_p_once() {
    $x: Person  $x.name = "p"
    update $x set { age: 0 }
}
```

### crates/omnigraph-gqt/cases/merge_base_siblings_that_merged_an_older_branch_keep_its_later_delete.gqt#9:mutate

```gq
query touch_p_twice() {
    update Person set { age: 0 } where name = "p"
}
```

```gq
gq 3;
query touch_p_twice() {
    $x: Person  $x.name = "p"
    update $x set { age: 0 }
}
```

### crates/omnigraph-gqt/cases/merge_base_siblings_that_merged_an_older_branch_keep_its_later_delete.gqt#17:mutate

```gq
query set_l() {
    update Person set { age: 1 } where name = "l"
}
```

```gq
gq 3;
query set_l() {
    $x: Person  $x.name = "l"
    update $x set { age: 1 }
}
```

### crates/omnigraph-gqt/cases/merge_base_siblings_that_merged_an_older_branch_keep_its_later_delete.gqt#19:mutate

```gq
query set_r() {
    update Person set { age: 1 } where name = "r"
}
```

```gq
gq 3;
query set_r() {
    $x: Person  $x.name = "r"
    update $x set { age: 1 }
}
```

### crates/omnigraph-gqt/cases/merge_base_siblings_that_merged_an_older_branch_keep_its_later_delete.gqt#25:mutate

```gq
query delete_w() {
    delete Person where name = "w"
}
```

```gq
gq 3;
query delete_w() {
    $x: Person  $x.name = "w"
    delete $x
}
```

### crates/omnigraph-gqt/cases/merge_criss_cross_after_reopen.gqt#7:mutate

```gq
query left_first() {
    update Person set { age: 1 } where name = "a"
}
```

```gq
gq 3;
query left_first() {
    $x: Person  $x.name = "a"
    update $x set { age: 1 }
}
```

### crates/omnigraph-gqt/cases/merge_criss_cross_after_reopen.gqt#9:mutate

```gq
query right_first() {
    update Person set { age: 1 } where name = "b"
}
```

```gq
gq 3;
query right_first() {
    $x: Person  $x.name = "b"
    update $x set { age: 1 }
}
```

### crates/omnigraph-gqt/cases/merge_criss_cross_after_reopen.gqt#19:mutate

```gq
query left_later() {
    update Person set { age: 2 } where name = "c"
}
```

```gq
gq 3;
query left_later() {
    $x: Person  $x.name = "c"
    update $x set { age: 2 }
}
```

### crates/omnigraph-gqt/cases/merge_criss_cross_after_reopen.gqt#21:mutate

```gq
query right_later() {
    update Person set { age: 3 } where name = "d"
}
```

```gq
gq 3;
query right_later() {
    $x: Person  $x.name = "d"
    update $x set { age: 3 }
}
```

### crates/omnigraph-gqt/cases/merge_dies_after_archiving_the_source_and_the_source_releases_itself.gqt#7:mutate

```gq
query diverge_main() {
    update Person set { age: 9 } where name = "bob"
}
```

```gq
gq 3;
query diverge_main() {
    $x: Person  $x.name = "bob"
    update $x set { age: 9 }
}
```

### crates/omnigraph-gqt/cases/merge_dies_after_archiving_the_source_and_the_source_releases_itself.gqt#10:mutate

```gq
query bump_alice($n: I32) {
    update Person set { age: $n } where name = "alice"
}
```

```gq
gq 3;
query bump_alice($n: I32) {
    $x: Person  $x.name = "alice"
    update $x set { age: $n }
}
```

### crates/omnigraph-gqt/cases/merge_refuses_edge_whose_source_the_target_deleted.gqt#7:mutate

```gq
query del_main() { delete Person where id = "p000000" }
```

```gq
gq 3;
query del_main() { $x: Person  $x.id = "p000000"  delete $x }
```

### crates/omnigraph-gqt/cases/merge_refuses_edge_whose_target_the_target_deleted_integer_keys.gqt#9:mutate

```gq
query del_main() { delete Person where id = 1 }
```

```gq
gq 3;
query del_main() { $x: Person  $x.id = 1  delete $x }
```

### crates/omnigraph-gqt/cases/merged_idle_branch_deleted_and_recreated.gqt#5:mutate

```gq
query set_a_on_s() {
    update Person set { age: 1 } where name = "a"
}
```

```gq
gq 3;
query set_a_on_s() {
    $x: Person  $x.name = "a"
    update $x set { age: 1 }
}
```

### crates/omnigraph-gqt/cases/merged_idle_branch_deleted_and_recreated.gqt#7:mutate

```gq
query set_b_on_main() {
    update Person set { age: 1 } where name = "b"
}
```

```gq
gq 3;
query set_b_on_main() {
    $x: Person  $x.name = "b"
    update $x set { age: 1 }
}
```

### crates/omnigraph-gqt/cases/merged_idle_branch_deleted_and_recreated.gqt#14:mutate

```gq
query set_b_again_on_main() {
    update Person set { age: 2 } where name = "b"
}
```

```gq
gq 3;
query set_b_again_on_main() {
    $x: Person  $x.name = "b"
    update $x set { age: 2 }
}
```

### crates/omnigraph-gqt/cases/merged_idle_branch_deleted_and_recreated.gqt#20:mutate

```gq
query set_c_on_t() {
    update Person set { age: 1 } where name = "c"
}
```

```gq
gq 3;
query set_c_on_t() {
    $x: Person  $x.name = "c"
    update $x set { age: 1 }
}
```

### crates/omnigraph-gqt/cases/merged_idle_branch_deleted_and_recreated.gqt#34:mutate

```gq
query set_a_on_recreated_s() {
    update Person set { age: 5 } where name = "a"
}
```

```gq
gq 3;
query set_a_on_recreated_s() {
    $x: Person  $x.name = "a"
    update $x set { age: 5 }
}
```

### crates/omnigraph-gqt/cases/meta_fields_resolve_by_role.gqt#43:mutate

```gq
query birthday() { update Person set { age: 26 } where @id = "bob" }
```

```gq
gq 3;
query birthday() { $x: Person  $x.@id = "bob"  update $x set { age: 26 } }
```

### crates/omnigraph-gqt/cases/meta_fields_resolve_by_role.gqt#48:mutate

```gq
query absent_edge_identity() { delete Knows where @id = "absent" }
```

```gq
gq 3;
query absent_edge_identity() { $x: Knows  $x.@id = "absent"  delete $x }
```

### crates/omnigraph-gqt/cases/meta_fields_resolve_by_role.gqt#50:mutate

```gq
query absent_edge_endpoint() { delete Knows where @dst = "absent" }
```

```gq
gq 3;
query absent_edge_endpoint() { $x: Knows  $x.@dst = "absent"  delete $x }
```

### crates/omnigraph-gqt/cases/meta_fields_resolve_by_role.gqt#68:mutate

```gq
query update_node_id_property() { update Person set { id: "x" } where @id = "alice" }
```

```gq
gq 3;
query update_node_id_property() { $x: Person  $x.@id = "alice"  update $x set { id: "x" } }
```

### crates/omnigraph-gqt/cases/meta_fields_resolve_by_role.gqt#72:mutate

```gq
query delete_by_bare_source() { delete Bare where src = "alice" }
```

```gq
gq 3;
query delete_by_bare_source() { $x: Bare  $x.src = "alice"  delete $x }
```

### crates/omnigraph-gqt/cases/meta_fields_resolve_by_role.gqt#74:mutate

```gq
query delete_by_bare_destination() { delete Bare where dst = "bob" }
```

```gq
gq 3;
query delete_by_bare_destination() { $x: Bare  $x.dst = "bob"  delete $x }
```

### crates/omnigraph-gqt/cases/meta_fields_resolve_by_role.gqt#76:mutate

```gq
query delete_node_by_source() { delete Person where @src = "alice" }
```

```gq
gq 3;
query delete_node_by_source() { $x: Person  $x.@src = "alice"  delete $x }
```

### crates/omnigraph-gqt/cases/meta_fields_resolve_by_role.gqt#78:mutate

```gq
query delete_edge_by_owner() { delete Knows where @owner = "alice" }
```

```gq
gq 3;
query delete_edge_by_owner() { $x: Knows  $x.@owner = "alice"  delete $x }
```

### crates/omnigraph-gqt/cases/meta_fields_resolve_by_role.gqt#80:mutate

```gq
query wrong_identity_type($n: I32) { delete Person where @id = $n }
```

```gq
gq 3;
query wrong_identity_type($n: I32) { $x: Person  $x.@id = $n  delete $x }
```

### crates/omnigraph-gqt/cases/meta_fields_resolve_by_role.gqt#85:mutate

```gq
query unlink($who: String) {
    delete Knows where @src = $who
}
```

```gq
gq 3;
query unlink($who: String) {
    $x: Knows  $x.@src = $who
    delete $x
}
```

### crates/omnigraph-gqt/cases/meta_fields_resolve_by_role.gqt#88:mutate

```gq
query forget($who: String) {
    delete Person where @id = $who
}
```

```gq
gq 3;
query forget($who: String) {
    $x: Person  $x.@id = $who
    delete $x
}
```

### crates/omnigraph-gqt/cases/meta_fields_resolve_by_role.gqt#91:mutate

```gq
query forget_by_bare_id($who: String) {
    delete Person where id = $who
}
```

```gq
gq 3;
query forget_by_bare_id($who: String) {
    $x: Person  $x.id = $who
    delete $x
}
```

### crates/omnigraph-gqt/cases/mutation_error_typed.gqt#6:mutate

```gq
query delete_absent($name: String) {
    delete Person where name = $name
}
```

```gq
gq 3;
query delete_absent($name: String) {
    $x: Person  $x.name = $name
    delete $x
}
```

### crates/omnigraph-gqt/cases/nearest_alias_secondary_key.gqt#3:query

```gq
query nearest_then_alias($q: Vector(4)) {
    match { $d: Doc }
    return { $d.slug, $d.name as name }
    order { nearest($d.embedding, $q), name desc }
    limit 10
}
```

```gq
gq 3;
query nearest_then_alias($q: Vector(4)) {
    match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 10) }
    return { $d.slug, $d.name as name }
    order { $distance, name desc }
    limit 10
}
```

### crates/omnigraph-gqt/cases/nearest_bm25_and_rrf_rank_the_search_fixture.gqt#3:query

```gq
query vector_search($q: Vector(4)) {
    match { $d: Doc }
    return { $d.slug, $d.title }
    order { nearest($d.embedding, $q) }
    limit 3
}
```

```gq
gq 3;
query vector_search($q: Vector(4)) {
    match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 3) }
    return { $d.slug, $d.title }
    order { $distance }
    limit 3
}
```

### crates/omnigraph-gqt/cases/nearest_bm25_and_rrf_rank_the_search_fixture.gqt#7:query

```gq
query bm25_search($q: String) {
    match { $d: Doc }
    return { $d.slug, $d.title }
    order { bm25($d.title, $q) }
    limit 3
}
```

```gq
gq 3;
query bm25_search($q: String) {
    match { from bm25({ match { $d: Doc } return { $d } }, $d.title, $q) }
    return { $d.slug, $d.title }
    order { $score desc }
    limit 3
}
```

### crates/omnigraph-gqt/cases/nearest_bm25_and_rrf_rank_the_search_fixture.gqt#11:query

```gq
query bm25_then_slug($q: String) {
    match { $d: Doc }
    return { $d.slug }
    order { bm25($d.title, $q), $d.slug desc }
    limit 3
}
```

```gq
gq 3;
query bm25_then_slug($q: String) {
    match { from bm25({ match { $d: Doc } return { $d } }, $d.title, $q) }
    return { $d.slug }
    order { $score desc, $d.slug desc }
    limit 3
}
```

### crates/omnigraph-gqt/cases/nearest_bm25_and_rrf_rank_the_search_fixture.gqt#15:query

```gq
query hybrid_search($vq: Vector(4), $tq: String) {
    match { $d: Doc }
    return { $d.slug, $d.title }
    order { rrf(nearest($d.embedding, $vq), bm25($d.title, $tq)) }
    limit 3
}
```

```gq
gq 3;
query hybrid_search($vq: Vector(4), $tq: String) {
    match { let $p = { match { $d: Doc } return { $d } }  from rrf(nearest($p, $d.embedding, $vq, k: 3), bm25($p, $d.title, $tq), k: 60) }
    return { $d.slug, $d.title }
    order { $score desc }
    limit 3
}
```

### crates/omnigraph-gqt/cases/nearest_corpus_smaller_than_limit.gqt#3:query

```gq
query nearest_small_corpus($q: Vector(4)) {
    match { $d: Doc }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 10
}
```

```gq
gq 3;
query nearest_small_corpus($q: Vector(4)) {
    match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 10) }
    return { $d.slug }
    order { $distance }
    limit 10
}
```

### crates/omnigraph-gqt/cases/nearest_interleaves_rows_around_the_query.gqt#3:query

```gq
query nearest_around_query($q: Vector(4)) {
    match { $d: Doc }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 3
}
```

```gq
gq 3;
query nearest_around_query($q: Vector(4)) {
    match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 3) }
    return { $d.slug }
    order { $distance }
    limit 3
}
```

### crates/omnigraph-gqt/cases/nearest_rank_survives_edge_fanout_and_hops.gqt#3:query

```gq
query nearest_edges($q: Vector(4)) {
    match {
        $d: RankedDoc
        $d $w:rankedLink $target
    }
    return { $d.slug, $w.label }
    order { nearest($d.embedding, $q) }
    limit 4
}
```

```gq
gq 3;
query nearest_edges($q: Vector(4)) {
    match { from nearest({ match {
        $d: RankedDoc
        $d $w:rankedLink $target
    } return { $d, $w, $target } }, $d.embedding, $q, k: 4) }
    return { $d.slug, $w.label }
    order { $distance }
    limit 4
}
```

### crates/omnigraph-gqt/cases/nearest_rank_survives_edge_fanout_and_hops.gqt#7:query

```gq
query nearest_hops($q: Vector(4)) {
    match {
        $d: RankedDoc
        $d rankedLink{1,2} $target
    }
    return { $d.slug, $target.slug }
    order { nearest($d.embedding, $q) }
    limit 2
}
```

```gq
gq 3;
query nearest_hops($q: Vector(4)) {
    match { from nearest({ match {
        $d: RankedDoc
        $d rankedLink{1,2} $target
    } return { $d, $target } }, $d.embedding, $q, k: 2) }
    return { $d.slug, $target.slug }
    order { $distance }
    limit 2
}
```

### crates/omnigraph-gqt/cases/nearest_ranks_updated_embedding.gqt#3:mutate

```gq
query move_embedding($s: String, $e: Vector(4)) {
    update Doc set { embedding: $e } where slug = $s
}
```

```gq
gq 3;
query move_embedding($s: String, $e: Vector(4)) {
    $x: Doc  $x.slug = $s
    update $x set { embedding: $e }
}
```

### crates/omnigraph-gqt/cases/nearest_ranks_updated_embedding.gqt#6:query

```gq
query nearest_after_update($q: Vector(4)) {
    match { $d: Doc }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 6
}
```

```gq
gq 3;
query nearest_after_update($q: Vector(4)) {
    match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 6) }
    return { $d.slug }
    order { $distance }
    limit 6
}
```

### crates/omnigraph-gqt/cases/nearest_sees_rows_inserted_after_index_build.gqt#9:query

```gq
query nearest_after_insert($q: Vector(4)) {
    match { $d: Doc }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 4
}
```

```gq
gq 3;
query nearest_after_insert($q: Vector(4)) {
    match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 4) }
    return { $d.slug }
    order { $distance }
    limit 4
}
```

### crates/omnigraph-gqt/cases/nearest_sees_rows_inserted_after_index_build.gqt#14:query

```gq
query nearest_after_restart($q: Vector(4)) {
    match { $d: Doc }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 4
}
```

```gq
gq 3;
query nearest_after_restart($q: Vector(4)) {
    match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 4) }
    return { $d.slug }
    order { $distance }
    limit 4
}
```

### crates/omnigraph-gqt/cases/nearest_skips_deleted_rows_and_fills_limit.gqt#3:mutate

```gq
query delete_doc($s: String) {
    delete Doc where slug = $s
}
```

```gq
gq 3;
query delete_doc($s: String) {
    $x: Doc  $x.slug = $s
    delete $x
}
```

### crates/omnigraph-gqt/cases/nearest_skips_deleted_rows_and_fills_limit.gqt#6:mutate

```gq
query delete_doc($s: String) {
    delete Doc where slug = $s
}
```

```gq
gq 3;
query delete_doc($s: String) {
    $x: Doc  $x.slug = $s
    delete $x
}
```

### crates/omnigraph-gqt/cases/nearest_skips_deleted_rows_and_fills_limit.gqt#9:mutate

```gq
query delete_doc($s: String) {
    delete Doc where slug = $s
}
```

```gq
gq 3;
query delete_doc($s: String) {
    $x: Doc  $x.slug = $s
    delete $x
}
```

### crates/omnigraph-gqt/cases/nearest_skips_deleted_rows_and_fills_limit.gqt#12:query

```gq
query nearest_after_delete($q: Vector(4)) {
    match { $d: Doc }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 3
}
```

```gq
gq 3;
query nearest_after_delete($q: Vector(4)) {
    match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 3) }
    return { $d.slug }
    order { $distance }
    limit 3
}
```

### crates/omnigraph-gqt/cases/negated_filter_nearest_refetches_survivors.gqt#3:query

```gq
query nearest_after_negation($q: Vector(4)) {
    match {
        $d: Doc
        not { $d.name starts_with "doc-00" }
    }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 4
}
```

```gq
gq 3;
query nearest_after_negation($q: Vector(4)) {
    match { from nearest({ match {
        $d: Doc
        not { starts_with($d.name, "doc-00") }
    } return { $d } }, $d.embedding, $q, k: 4) }
    return { $d.slug }
    order { $distance }
    limit 4
}
```

### crates/omnigraph-gqt/cases/negated_filter_nearest_sparse_survivors.gqt#3:query

```gq
query nearest_sparse_survivors($q: Vector(4)) {
    match {
        $d: Doc
        not { $d.n < 49 }
        not { $d.n > 50 }
    }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 3
}
```

```gq
gq 3;
query nearest_sparse_survivors($q: Vector(4)) {
    match { from nearest({ match {
        $d: Doc
        not { $d.n < 49 }
        not { $d.n > 50 }
    } return { $d } }, $d.embedding, $q, k: 3) }
    return { $d.slug }
    order { $distance }
    limit 3
}
```

### crates/omnigraph-gqt/cases/nested_comparison_operands_in_mutation_where.gqt#3:mutate

```gq
query nested_comparison() {
    update Person set { adult: true } where (age > 30) = true
}
```

```gq
gq 3;
query nested_comparison() {
    $x: Person  ($x.age > 30) = true
    update $x set { adult: true }
}
```

### crates/omnigraph-gqt/cases/nested_comparison_operands_in_mutation_where.gqt#5:mutate

```gq
query nested_null_test() {
    update Person set { nick: "bee" } where (nick is null) = true
}
```

```gq
gq 3;
query nested_null_test() {
    $x: Person  ($x.nick is null) = true
    update $x set { nick: "bee" }
}
```

### crates/omnigraph-gqt/cases/nested_comparison_operands_in_mutation_where.gqt#7:mutate

```gq
query nested_not() {
    delete Person where (not (age > 30)) = true
}
```

```gq
gq 3;
query nested_not() {
    $x: Person  (not ($x.age > 30)) = true
    delete $x
}
```

### crates/omnigraph-gqt/cases/null_constant_refused_on_non_nullable_property.gqt#3:mutate

```gq
query null_comparison_on_non_nullable($slug: String, $age: I64?) {
    update Person set { adult: $age > 30 } where slug = $slug
}
```

```gq
gq 3;
query null_comparison_on_non_nullable($slug: String, $age: I64?) {
    $x: Person  $x.slug = $slug
    update $x set { adult: $age > 30 }
}
```

### crates/omnigraph-gqt/cases/null_constant_refused_on_non_nullable_property.gqt#6:mutate

```gq
query null_on_non_nullable_blob_matching_no_row($slug: String, $body: Blob?) {
    update Attachment set { body: $body } where slug = $slug
}
```

```gq
gq 3;
query null_on_non_nullable_blob_matching_no_row($slug: String, $body: Blob?) {
    $x: Attachment  $x.slug = $slug
    update $x set { body: $body }
}
```

### crates/omnigraph-gqt/cases/null_constant_refused_on_non_nullable_property.gqt#9:mutate

```gq
query comparison_on_nullable($slug: String, $age: I64?) {
    update Person set { maybe: $age > 30 } where slug = $slug
}
```

```gq
gq 3;
query comparison_on_nullable($slug: String, $age: I64?) {
    $x: Person  $x.slug = $slug
    update $x set { maybe: $age > 30 }
}
```

### crates/omnigraph-gqt/cases/null_constant_refused_on_non_nullable_property.gqt#15:mutate

```gq
query null_comparison_on_nullable($slug: String, $age: I64?) {
    update Person set { maybe: $age > 30 } where slug = $slug
}
```

```gq
gq 3;
query null_comparison_on_nullable($slug: String, $age: I64?) {
    $x: Person  $x.slug = $slug
    update $x set { maybe: $age > 30 }
}
```

### crates/omnigraph-gqt/cases/null_list_parameter_membership_is_null.gqt#3:query

```gq
set engine = v2;
query null_operands($tags: [String]?, $name: String?, $flag: Bool?) {
    match { $p: Person }
    return { $tags contains "a" as present, $name = "a" as same_name, $flag = true as same_flag, $name starts_with "a" as prefixed }
}
```

```gq
gq 3;
set engine = v2;
query null_operands($tags: [String]?, $name: String?, $flag: Bool?) {
    match { $p: Person }
    return { "a" in $tags as present, $name = "a" as same_name, $flag = true as same_flag, starts_with($name, "a") as prefixed }
}
```

### crates/omnigraph-gqt/cases/null_list_parameter_membership_is_null.gqt#7:query

```gq
set engine = v2;
query bound_list($tags: [String]?) {
    match { $p: Person }
    return { $tags contains "a" as present }
}
```

```gq
gq 3;
set engine = v2;
query bound_list($tags: [String]?) {
    match { $p: Person }
    return { "a" in $tags as present }
}
```

### crates/omnigraph-gqt/cases/numeric_comparison_keeps_the_fraction_across_integer_and_float.gqt#6:mutate

```gq
query assign($age: I64) {
    update Person set { adult: $age = 2.7 } where slug = "a"
}
```

```gq
gq 3;
query assign($age: I64) {
    $x: Person  $x.slug = "a"
    update $x set { adult: $age = 2.7 }
}
```

### crates/omnigraph-gqt/cases/numeric_comparison_keeps_the_fraction_across_integer_and_float.gqt#18:mutate

```gq
query pushed_mutation() {
    update Person set { adult: true } where age = 2.7
}
```

```gq
gq 3;
query pushed_mutation() {
    $x: Person  $x.age = 2.7
    update $x set { adult: true }
}
```

### crates/omnigraph-gqt/cases/omitted_required_param_refused.gqt#15:mutate

```gq
query delete_thing_by_id_without_params($id: String) {
    delete Thing where id = $id
}
```

```gq
gq 3;
query delete_thing_by_id_without_params($id: String) {
    $x: Thing  $x.id = $id
    delete $x
}
```

### crates/omnigraph-gqt/cases/omitted_required_param_refused.gqt#17:mutate

```gq
query rename_thing_by_id_with_empty_params($id: String, $name: String) {
    update Thing set { name: $name } where id = $id
}
```

```gq
gq 3;
query rename_thing_by_id_with_empty_params($id: String, $name: String) {
    $x: Thing  $x.id = $id
    update $x set { name: $name }
}
```

### crates/omnigraph-gqt/cases/omitted_required_param_refused.gqt#22:mutate

```gq
query unused_required_mutation_param($unused: I32) {
    update Thing set { name: "changed" } where id = "missing"
}
```

```gq
gq 3;
query unused_required_mutation_param($unused: I32) {
    $x: Thing  $x.id = "missing"
    update $x set { name: "changed" }
}
```

### crates/omnigraph-gqt/cases/owner_rewrites_after_pointer_switch.gqt#5:mutate

```gq
query feature_write() {
    update Person set { age: 31 } where name = "alice"
}
```

```gq
gq 3;
query feature_write() {
    $x: Person  $x.name = "alice"
    update $x set { age: 31 }
}
```

### crates/omnigraph-gqt/cases/owner_rewrites_after_pointer_switch.gqt#9:mutate

```gq
query main_write() {
    update Person set { age: 32 } where name = "alice"
}
```

```gq
gq 3;
query main_write() {
    $x: Person  $x.name = "alice"
    update $x set { age: 32 }
}
```

### crates/omnigraph-gqt/cases/owner_rewrites_after_pointer_switch.gqt#15:mutate

```gq
query feature_rewrites() {
    update Person set { age: 33 } where name = "alice"
}
```

```gq
gq 3;
query feature_rewrites() {
    $x: Person  $x.name = "alice"
    update $x set { age: 33 }
}
```

### crates/omnigraph-gqt/cases/owner_write_after_child_forked_from_former_ref.gqt#5:mutate

```gq
query feature_write() {
    update Person set { age: 31 } where name = "alice"
}
```

```gq
gq 3;
query feature_write() {
    $x: Person  $x.name = "alice"
    update $x set { age: 31 }
}
```

### crates/omnigraph-gqt/cases/owner_write_after_child_forked_from_former_ref.gqt#11:mutate

```gq
query main_write() {
    update Person set { age: 32 } where name = "alice"
}
```

```gq
gq 3;
query main_write() {
    $x: Person  $x.name = "alice"
    update $x set { age: 32 }
}
```

### crates/omnigraph-gqt/cases/owner_write_after_child_forked_from_former_ref.gqt#17:mutate

```gq
query owner_writes_after_child_wrote() {
    update Person set { age: 34 } where name = "alice"
}
```

```gq
gq 3;
query owner_writes_after_child_wrote() {
    $x: Person  $x.name = "alice"
    update $x set { age: 34 }
}
```

### crates/omnigraph-gqt/cases/owner_write_after_child_forked_from_former_ref.gqt#33:mutate

```gq
query owner_writes_after_child_deleted() {
    update Person set { age: 35 } where name = "alice"
}
```

```gq
gq 3;
query owner_writes_after_child_deleted() {
    $x: Person  $x.name = "alice"
    update $x set { age: 35 }
}
```

### crates/omnigraph-gqt/cases/param_filter_prefilters_nearest.gqt#3:query

```gq
query nearest_parameter_filter($q: Vector(4), $n: I64) {
    match {
        $d: Doc
        $d.n = $n
    }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 3
}
```

```gq
gq 3;
query nearest_parameter_filter($q: Vector(4), $n: I64) {
    match { from nearest({ match {
        $d: Doc
        $d.n = $n
    } return { $d } }, $d.embedding, $q, k: 3) }
    return { $d.slug }
    order { $distance }
    limit 3
}
```

### crates/omnigraph-gqt/cases/parent_retirement.gqt#5:mutate

```gq
query parent_writes() {
    update Person set { age: 31 } where name = "alice"
}
```

```gq
gq 3;
query parent_writes() {
    $x: Person  $x.name = "alice"
    update $x set { age: 31 }
}
```

### crates/omnigraph-gqt/cases/parent_retirement.gqt#31:mutate

```gq
query recreated_parent_writes() {
    update Person set { age: 90 } where name = "alice"
}
```

```gq
gq 3;
query recreated_parent_writes() {
    $x: Person  $x.name = "alice"
    update $x set { age: 90 }
}
```

### crates/omnigraph-gqt/cases/parent_retirement.gqt#39:mutate

```gq
query grandchild_writes_after_both_ancestor_names_are_reused() {
    update Person set { age: 32 } where name = "alice"
}
```

```gq
gq 3;
query grandchild_writes_after_both_ancestor_names_are_reused() {
    $x: Person  $x.name = "alice"
    update $x set { age: 32 }
}
```

### crates/omnigraph-gqt/cases/rank_call_nested_in_a_boolean_projection.gqt#3:query

```gq
set engine = v2;
query bm25_threshold($q: String) {
    match { $d: Doc }
    return { $d.slug, bm25($d.text, $q) > 0.0 as hit }
    order { bm25($d.text, $q) desc }
    limit 2
}
```

```gq
gq 3;
set engine = v2;
query bm25_threshold($q: String) {
    match { from bm25({ match { $d: Doc } return { $d } }, $d.text, $q) }
    return { $d.slug, $score > 0.0 as hit }
    order { $score desc }
    limit 2
}
```

### crates/omnigraph-gqt/cases/rank_call_nested_in_a_boolean_projection.gqt#7:query

```gq
set engine = v2;
query nearest_threshold($v: Vector(2)) {
    match { $d: Doc }
    return { $d.slug, nearest($d.embedding, $v) < 1.0 as close }
    order { nearest($d.embedding, $v) }
    limit 2
}
```

```gq
gq 3;
set engine = v2;
query nearest_threshold($v: Vector(2)) {
    match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $v, k: 2) }
    return { $d.slug, $distance < 1.0 as close }
    order { $distance }
    limit 2
}
```

### crates/omnigraph-gqt/cases/rank_call_nested_in_a_boolean_projection.gqt#11:query

```gq
set engine = v2;
query nearest_threshold_expression($v: Vector(2)) {
    match { $d: Doc }
    return { $d.slug, nearest($d.embedding, $v) < 1.0 as close }
    order { nearest($d.embedding, $v), (nearest($d.embedding, $v) < 1.0) asc }
    limit 2
}
```

```gq
gq 3;
set engine = v2;
query nearest_threshold_expression($v: Vector(2)) {
    match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $v, k: 2) }
    return { $d.slug, $distance < 1.0 as close }
    order { $distance, ($distance < 1.0) asc }
    limit 2
}
```

### crates/omnigraph-gqt/cases/rank_call_nested_in_a_boolean_projection.gqt#15:query

```gq
set engine = v2;
query nearest_threshold_alias($v: Vector(2)) {
    match { $d: Doc }
    return { $d.slug, nearest($d.embedding, $v) < 1.0 as close }
    order { nearest($d.embedding, $v), close asc }
    limit 2
}
```

```gq
gq 3;
set engine = v2;
query nearest_threshold_alias($v: Vector(2)) {
    match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $v, k: 2) }
    return { $d.slug, $distance < 1.0 as close }
    order { $distance, close asc }
    limit 2
}
```

### crates/omnigraph-gqt/cases/rank_call_nested_in_a_boolean_projection.gqt#19:query

```gq
set engine = v2;
query bm25_threshold_expression($q: String) {
    match { $d: Doc }
    return { $d.slug, bm25($d.text, $q) > 0.18 as hit }
    order { bm25($d.text, $q) desc, (bm25($d.text, $q) > 0.18) asc }
    limit 2
}
```

```gq
gq 3;
set engine = v2;
query bm25_threshold_expression($q: String) {
    match { from bm25({ match { $d: Doc } return { $d } }, $d.text, $q) }
    return { $d.slug, $score > 0.18 as hit }
    order { $score desc, ($score > 0.18) asc }
    limit 2
}
```

### crates/omnigraph-gqt/cases/rank_call_nested_in_a_boolean_projection.gqt#23:query

```gq
set engine = v2;
query bm25_threshold_alias($q: String) {
    match { $d: Doc }
    return { $d.slug, bm25($d.text, $q) > 0.18 as hit }
    order { bm25($d.text, $q) desc, hit asc }
    limit 2
}
```

```gq
gq 3;
set engine = v2;
query bm25_threshold_alias($q: String) {
    match { from bm25({ match { $d: Doc } return { $d } }, $d.text, $q) }
    return { $d.slug, $score > 0.18 as hit }
    order { $score desc, hit asc }
    limit 2
}
```

### crates/omnigraph-gqt/cases/rank_call_nested_in_a_boolean_projection.gqt#27:query

```gq
set engine = v2;
query rrf_threshold_expression($v: Vector(2), $q: String) {
    match { $d: Doc }
    return { $d.slug, rrf(nearest($d.embedding, $v), bm25($d.text, $q)) > 0.0325 as high }
    order { rrf(nearest($d.embedding, $v), bm25($d.text, $q)), (rrf(nearest($d.embedding, $v), bm25($d.text, $q)) > 0.0325) asc }
    limit 2
}
```

```gq
gq 3;
set engine = v2;
query rrf_threshold_expression($v: Vector(2), $q: String) {
    match { let $p = { match { $d: Doc } return { $d } }  from rrf(nearest($p, $d.embedding, $v, k: 2), bm25($p, $d.text, $q), k: 60) }
    return { $d.slug, $score > 0.0325 as high }
    order { $score desc, ($score > 0.0325) asc }
    limit 2
}
```

### crates/omnigraph-gqt/cases/rank_call_nested_in_a_boolean_projection.gqt#31:query

```gq
set engine = v2;
query rrf_threshold_alias($v: Vector(2), $q: String) {
    match { $d: Doc }
    return { $d.slug, rrf(nearest($d.embedding, $v), bm25($d.text, $q)) > 0.0325 as high }
    order { rrf(nearest($d.embedding, $v), bm25($d.text, $q)), high asc }
    limit 2
}
```

```gq
gq 3;
set engine = v2;
query rrf_threshold_alias($v: Vector(2), $q: String) {
    match { let $p = { match { $d: Doc } return { $d } }  from rrf(nearest($p, $d.embedding, $v, k: 2), bm25($p, $d.text, $q), k: 60) }
    return { $d.slug, $score > 0.0325 as high }
    order { $score desc, high asc }
    limit 2
}
```

### crates/omnigraph-gqt/cases/rank_call_nested_in_a_boolean_projection.gqt#35:query

```gq
set engine = v2;
query recursive_rank_keys($v: Vector(2)) {
    match { $d: Doc }
    return {
        $d.slug,
        not (nearest($d.embedding, $v) < 1.0) as far,
        nearest($d.embedding, $v) is null as missing,
        nearest($d.embedding, $v) in [0.0] as exact,
        nearest($d.embedding, $v) < 1.0 and $d.n > 5 as eligible
    }
    order {
        nearest($d.embedding, $v),
        (not (nearest($d.embedding, $v) < 1.0)) desc,
        (nearest($d.embedding, $v) is null) desc,
        (nearest($d.embedding, $v) in [0.0]) asc,
        (nearest($d.embedding, $v) < 1.0 and $d.n > 5) asc
    }
    limit 2
}
```

```gq
gq 3;
set engine = v2;
query recursive_rank_keys($v: Vector(2)) {
    match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $v, k: 2) }
    return {
        $d.slug,
        not ($distance < 1.0) as far,
        $distance is null as missing,
        $distance in [0.0] as exact,
        $distance < 1.0 and $d.n > 5 as eligible
    }
    order {
        $distance,
        (not ($distance < 1.0)) desc,
        ($distance is null) desc,
        ($distance in [0.0]) asc,
        ($distance < 1.0 and $d.n > 5) asc
    }
    limit 2
}
```

### crates/omnigraph-gqt/cases/rank_key_asc_direction_refused.gqt#3:query

```gq
query bm25_bare() {
    match { $d: Doc }
    return { $d.slug }
    order { bm25($d.text, "needle") }
    limit 2
}
```

```gq
gq 3;
query bm25_bare() {
    match { from bm25({ match { $d: Doc } return { $d } }, $d.text, "needle") }
    return { $d.slug }
    order { $score desc }
    limit 2
}
```

### crates/omnigraph-gqt/cases/rank_key_asc_direction_refused.gqt#11:query

```gq
query rrf_desc($v: Vector(2)) {
    match { $d: Doc }
    return { $d.slug }
    order { rrf(nearest($d.embedding, $v), bm25($d.text, "needle")) desc }
    limit 2
}
```

```gq
gq 3;
query rrf_desc($v: Vector(2)) {
    match { let $p = { match { $d: Doc } return { $d } }  from rrf(nearest($p, $d.embedding, $v, k: 2), bm25($p, $d.text, "needle"), k: 60) }
    return { $d.slug }
    order { $score desc }
    limit 2
}
```

### crates/omnigraph-gqt/cases/read_beside_the_history_release.gqt#8:mutate

```gq
query bump_alice($n: I32) {
    update Person set { age: $n } where name = "alice"
}
```

```gq
gq 3;
query bump_alice($n: I32) {
    $x: Person  $x.name = "alice"
    update $x set { age: $n }
}
```

### crates/omnigraph-gqt/cases/releasing_publish_dies_before_the_cas_and_a_fresh_process_retries.gqt#9:mutate

```gq
query set_bob_on_s() {
    update Person set { age: 7 } where name = "bob"
}
```

```gq
gq 3;
query set_bob_on_s() {
    $x: Person  $x.name = "bob"
    update $x set { age: 7 }
}
```

### crates/omnigraph-gqt/cases/releasing_publish_dies_before_the_cas_and_a_fresh_process_retries.gqt#12:mutate

```gq
query bump_alice($n: I32) {
    update Person set { age: $n } where name = "alice"
}
```

```gq
gq 3;
query bump_alice($n: I32) {
    $x: Person  $x.name = "alice"
    update $x set { age: $n }
}
```

### crates/omnigraph-gqt/cases/releasing_publish_loses_its_acknowledgement_and_a_fresh_process_merges.gqt#9:mutate

```gq
query set_bob_on_s() {
    update Person set { age: 7 } where name = "bob"
}
```

```gq
gq 3;
query set_bob_on_s() {
    $x: Person  $x.name = "bob"
    update $x set { age: 7 }
}
```

### crates/omnigraph-gqt/cases/releasing_publish_loses_its_acknowledgement_and_a_fresh_process_merges.gqt#12:mutate

```gq
query bump_alice($n: I32) {
    update Person set { age: $n } where name = "alice"
}
```

```gq
gq 3;
query bump_alice($n: I32) {
    $x: Person  $x.name = "alice"
    update $x set { age: $n }
}
```

### crates/omnigraph-gqt/cases/reserved_word_property_in_mutation_predicate.gqt#3:mutate

```gq
query delete_by_interval() {
    delete Span where interval = 1
}
```

```gq
gq 3;
query delete_by_interval() {
    $x: Span  $x.interval = 1
    delete $x
}
```

### crates/omnigraph-gqt/cases/reserved_word_property_in_mutation_predicate.gqt#5:mutate

```gq
query delete_by_exists() {
    delete Span where exists = 2
}
```

```gq
gq 3;
query delete_by_exists() {
    $x: Span  $x.exists = 2
    delete $x
}
```

### crates/omnigraph-gqt/cases/reverse_traversal_nearest_ranks_targets.gqt#3:query

```gq
query nearest_edge_targets($q: Vector(4)) {
    match {
        $d: Doc
        $t knows $d
    }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 2
}
```

```gq
gq 3;
query nearest_edge_targets($q: Vector(4)) {
    match { from nearest({ match {
        $d: Doc
        $t knows $d
    } return { $d, $t } }, $d.embedding, $q, k: 2) }
    return { $d.slug }
    order { $distance }
    limit 2
}
```

### crates/omnigraph-gqt/cases/rows_survive_history_release_and_reopen.gqt#8:mutate

```gq
query bump_alice($n: I32) {
    update Person set { age: $n } where name = "alice"
}
```

```gq
gq 3;
query bump_alice($n: I32) {
    $x: Person  $x.name = "alice"
    update $x set { age: $n }
}
```

### crates/omnigraph-gqt/cases/rrf_decoy_flood_does_not_flip_the_fused_winner.gqt#3:query

```gq
query recall_two_terms($q1: String, $q2: String) {
    match {
        $c: Chunk
        $a: Artifact
        $c chunkOfArtifact $a
        search($c.text, $q1)
    }
    return { $c.slug }
    order { rrf(bm25($c.text, $q1), bm25($c.text, $q2)) }
    limit 1
}
```

```gq
gq 3;
query recall_two_terms($q1: String, $q2: String) {
    match { let $p = { match {
        $c: Chunk
        $a: Artifact
        $c chunkOfArtifact $a
        search($c.text, $q1)
    } return { $c, $a } }  from rrf(bm25($p, $c.text, $q1), bm25($p, $c.text, $q2), k: 60) }
    return { $c.slug }
    order { $score desc }
    limit 1
}
```

### crates/omnigraph-gqt/cases/rrf_decoy_flood_does_not_flip_the_fused_winner.gqt#8:query

```gq
query recall_alternation($q1: String, $q2: String) {
    match {
        $c: Chunk
        $a: Artifact
        $c (chunkOfArtifact | chunkOfArtifact) $a
        search($c.text, $q1)
    }
    return { $c.slug }
    order { rrf(bm25($c.text, $q1), bm25($c.text, $q2)) }
    limit 1
}
```

```gq
gq 3;
query recall_alternation($q1: String, $q2: String) {
    match { let $p = { match {
        $c: Chunk
        $a: Artifact
        $c (chunkOfArtifact | chunkOfArtifact) $a
        search($c.text, $q1)
    } return { $c, $a } }  from rrf(bm25($p, $c.text, $q1), bm25($p, $c.text, $q2), k: 60) }
    return { $c.slug }
    order { $score desc }
    limit 1
}
```

### crates/omnigraph-gqt/cases/rrf_decoy_flood_does_not_flip_the_fused_winner.gqt#13:query

```gq
query recall_wildcard($q1: String, $q2: String) {
    match {
        $c: Chunk
        $a: Artifact
        $c * $a
        search($c.text, $q1)
    }
    return { $c.slug }
    order { rrf(bm25($c.text, $q1), bm25($c.text, $q2)) }
    limit 1
}
```

```gq
gq 3;
query recall_wildcard($q1: String, $q2: String) {
    match { let $p = { match {
        $c: Chunk
        $a: Artifact
        $c * $a
        search($c.text, $q1)
    } return { $c, $a } }  from rrf(bm25($p, $c.text, $q1), bm25($p, $c.text, $q2), k: 60) }
    return { $c.slug }
    order { $score desc }
    limit 1
}
```

### crates/omnigraph-gqt/cases/rrf_nearest_and_bm25_agreeing_arms.gqt#3:query

```gq
query fused($q: Vector(4), $t: String) {
    match { $d: Doc }
    return { $d.slug }
    order { rrf(nearest($d.embedding, $q), bm25($d.text, $t)) }
    limit 3
}
```

```gq
gq 3;
query fused($q: Vector(4), $t: String) {
    match { let $p = { match { $d: Doc } return { $d } }  from rrf(nearest($p, $d.embedding, $q, k: 3), bm25($p, $d.text, $t), k: 60) }
    return { $d.slug }
    order { $score desc }
    limit 3
}
```

### crates/omnigraph-gqt/cases/rrf_nearest_arm_under_traversal.gqt#3:query

```gq
query nearest_and_text_with_edges($q: Vector(4), $text: String) {
    match {
        $d: Doc
        $d knows $t
    }
    return { $d.slug }
    order { rrf(nearest($d.embedding, $q), bm25($d.text, $text)) }
    limit 2
}
```

```gq
gq 3;
query nearest_and_text_with_edges($q: Vector(4), $text: String) {
    match { let $p = { match {
        $d: Doc
        $d knows $t
    } return { $d, $t } }  from rrf(nearest($p, $d.embedding, $q, k: 2), bm25($p, $d.text, $text), k: 60) }
    return { $d.slug }
    order { $score desc }
    limit 2
}
```

### crates/omnigraph-gqt/cases/rrf_parameter_named_like_binding.gqt#3:query

```gq
set engine = v2;
query fused_v2($q: Vector(4), $t: String) {
    match {
        $d: Doc
        $d knows $t
    }
    return { $d.slug }
    order { rrf(nearest($d.embedding, $q), bm25($d.text, $t)) }
    limit 1
}
```

```gq
gq 3;
set engine = v2;
query fused_v2($q: Vector(4), $t: String) {
    match { let $p = { match {
        $d: Doc
        $d knows $t
    } return { $d, $t } }  from rrf(nearest($p, $d.embedding, $q, k: 1), bm25($p, $d.text, $t), k: 60) }
    return { $d.slug }
    order { $score desc }
    limit 1
}
```

### crates/omnigraph-gqt/cases/second_writer_queued_behind_the_releasing_publish_lands.gqt#9:mutate

```gq
query set_bob_on_s() {
    update Person set { age: 7 } where name = "bob"
}
```

```gq
gq 3;
query set_bob_on_s() {
    $x: Person  $x.name = "bob"
    update $x set { age: 7 }
}
```

### crates/omnigraph-gqt/cases/second_writer_queued_behind_the_releasing_publish_lands.gqt#12:mutate

```gq
query bump_alice($n: I32) {
    update Person set { age: $n } where name = "alice"
}
```

```gq
gq 3;
query bump_alice($n: I32) {
    $x: Person  $x.name = "alice"
    update $x set { age: $n }
}
```

### crates/omnigraph-gqt/cases/settings_merge_failure_scopes_to_one_step.gqt#7:mutate

```gq
query bump_on_source() {
    update Person set { age: 31 } where name = "alice"
}
```

```gq
gq 3;
query bump_on_source() {
    $x: Person  $x.name = "alice"
    update $x set { age: 31 }
}
```

### crates/omnigraph-gqt/cases/settings_merge_failure_scopes_to_one_step.gqt#9:mutate

```gq
query bump_on_main() {
    update Person set { age: 32 } where name = "alice"
}
```

```gq
gq 3;
query bump_on_main() {
    $x: Person  $x.name = "alice"
    update $x set { age: 32 }
}
```

### crates/omnigraph-gqt/cases/settings_merge_lineage_modes.gqt#9:mutate

```gq
reset all;
query birthday() {
    update Person set { age: 33 } where name = "alice"
}
```

```gq
gq 3;
reset all;
query birthday() {
    $x: Person  $x.name = "alice"
    update $x set { age: 33 }
}
```

### crates/omnigraph-gqt/cases/settings_merge_lineage_modes.gqt#28:mutate

```gq
query update_source() {
    update Person set { age: 31 } where name = "alice"
}
```

```gq
gq 3;
query update_source() {
    $x: Person  $x.name = "alice"
    update $x set { age: 31 }
}
```

### crates/omnigraph-gqt/cases/settings_merge_lineage_modes.gqt#30:mutate

```gq
query update_target() {
    update Person set { age: 41 } where name = "bob"
}
```

```gq
gq 3;
query update_target() {
    $x: Person  $x.name = "bob"
    update $x set { age: 41 }
}
```

### crates/omnigraph-gqt/cases/settings_merge_lineage_modes.gqt#32:mutate

```gq
query update_target() {
    update Person set { age: 41 } where name = "bob"
}
```

```gq
gq 3;
query update_target() {
    $x: Person  $x.name = "bob"
    update $x set { age: 41 }
}
```

### crates/omnigraph-gqt/cases/settings_merge_lineage_modes.gqt#34:mutate

```gq
query update_target() {
    update Person set { age: 41 } where name = "bob"
}
```

```gq
gq 3;
query update_target() {
    $x: Person  $x.name = "bob"
    update $x set { age: 41 }
}
```

### crates/omnigraph-gqt/cases/string_contains_in_mutation_where_is_a_substring_test.gqt#3:mutate

```gq
query mark_by_substring($needle: String) {
    update Person set { hit: $needle contains "li" } where name contains "o"
}
```

```gq
gq 3;
query mark_by_substring($needle: String) {
    $x: Person  contains($x.name, "o")
    update $x set { hit: contains($needle, "li") }
}
```

### crates/omnigraph-gqt/cases/string_contains_in_mutation_where_is_a_substring_test.gqt#9:mutate

```gq
query mark_by_membership($tags: [String]) {
    update Person set { hit: $tags contains "go" } where tags contains "rust"
}
```

```gq
gq 3;
query mark_by_membership($tags: [String]) {
    $x: Person  "rust" in $x.tags
    update $x set { hit: "go" in $tags }
}
```

### crates/omnigraph-gqt/cases/string_contains_in_mutation_where_is_a_substring_test.gqt#15:mutate

```gq
query delete_by_substring() {
    delete Person where name contains "ar"
}
```

```gq
gq 3;
query delete_by_substring() {
    $x: Person  contains($x.name, "ar")
    delete $x
}
```

### crates/omnigraph-gqt/cases/string_contains_in_return_is_a_substring_test.gqt#3:query

```gq
set engine = v2;
query substring_and_membership() {
    match { $p: Person }
    return { $p.slug, $p.name contains "li" as hit, $p.tags contains "rust" as tagged }
}
```

```gq
gq 3;
set engine = v2;
query substring_and_membership() {
    match { $p: Person }
    return { $p.slug, contains($p.name, "li") as hit, "rust" in $p.tags as tagged }
}
```

### crates/omnigraph-gqt/cases/text_search_fuzzy_and_match_text_filters.gqt#11:query

```gq
query fuzzy_search($q: String) {
    match {
        $d: Doc
        fuzzy($d.title, $q, 2)
    }
    return { $d.slug, $d.title }
}
```

```gq
gq 3;
query fuzzy_search($q: String) {
    match {
        $d: Doc
        fuzzy($d.title, $q, max_edits: 2)
    }
    return { $d.slug, $d.title }
}
```

### crates/omnigraph-gqt/cases/text_search_fuzzy_and_match_text_filters.gqt#33:mutate

```gq
query retitle_fresh() {
    update Fresh set { body: "Deep Learning Systems" } where slug = "fresh-1"
}
```

```gq
gq 3;
query retitle_fresh() {
    $x: Fresh  $x.slug = "fresh-1"
    update $x set { body: "Deep Learning Systems" }
}
```

### crates/omnigraph-gqt/cases/text_search_fuzzy_and_match_text_filters.gqt#41:mutate

```gq
query drop_first_fresh() {
    delete Fresh where slug = "fresh-1"
}
```

```gq
gq 3;
query drop_first_fresh() {
    $x: Fresh  $x.slug = "fresh-1"
    delete $x
}
```

### crates/omnigraph-gqt/cases/traversal_nearest_empty_after_sources_deleted.gqt#3:mutate

```gq
query delete_doc($s: String) {
    delete Doc where slug = $s
}
```

```gq
gq 3;
query delete_doc($s: String) {
    $x: Doc  $x.slug = $s
    delete $x
}
```

### crates/omnigraph-gqt/cases/traversal_nearest_empty_after_sources_deleted.gqt#6:query

```gq
query nearest_after_source_delete($q: Vector(4)) {
    match {
        $d: Doc
        $d knows $t
    }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 2
}
```

```gq
gq 3;
query nearest_after_source_delete($q: Vector(4)) {
    match { from nearest({ match {
        $d: Doc
        $d knows $t
    } return { $d, $t } }, $d.embedding, $q, k: 2) }
    return { $d.slug }
    order { $distance }
    limit 2
}
```

### crates/omnigraph-gqt/cases/traversal_nearest_fanout_counts_rows.gqt#3:query

```gq
query nearest_counts_rows($q: Vector(4)) {
    match {
        $d: Doc
        $d knows $t
    }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 3
}
```

```gq
gq 3;
query nearest_counts_rows($q: Vector(4)) {
    match { from nearest({ match {
        $d: Doc
        $d knows $t
    } return { $d, $t } }, $d.embedding, $q, k: 3) }
    return { $d.slug }
    order { $distance }
    limit 3
}
```

### crates/omnigraph-gqt/cases/traversal_nearest_fanout_counts_rows.gqt#7:query

```gq
query nearest_returns_pairs($q: Vector(4)) {
    match {
        $d: Doc
        $d knows $t
    }
    return { $d.slug, $t.slug }
    order { nearest($d.embedding, $q) }
    limit 4
}
```

```gq
gq 3;
query nearest_returns_pairs($q: Vector(4)) {
    match { from nearest({ match {
        $d: Doc
        $d knows $t
    } return { $d, $t } }, $d.embedding, $q, k: 4) }
    return { $d.slug, $t.slug }
    order { $distance }
    limit 4
}
```

### crates/omnigraph-gqt/cases/traversal_nearest_gate_ranks_eligible_docs.gqt#3:query

```gq
query nearest_eligible_docs($q: Vector(4)) {
    match {
        $d: Doc
        $d knows $t
    }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 3
}
```

```gq
gq 3;
query nearest_eligible_docs($q: Vector(4)) {
    match { from nearest({ match {
        $d: Doc
        $d knows $t
    } return { $d, $t } }, $d.embedding, $q, k: 3) }
    return { $d.slug }
    order { $distance }
    limit 3
}
```

### crates/omnigraph-gqt/cases/traversal_nearest_over_empty_corpus.gqt#3:query

```gq
query nearest_empty_graph($q: Vector(4)) {
    match {
        $d: Doc
        $d knows $t
    }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 10
}
```

```gq
gq 3;
query nearest_empty_graph($q: Vector(4)) {
    match { from nearest({ match {
        $d: Doc
        $d knows $t
    } return { $d, $t } }, $d.embedding, $q, k: 10) }
    return { $d.slug }
    order { $distance }
    limit 10
}
```

### crates/omnigraph-gqt/cases/traversal_nearest_overfetch_fills_limit.gqt#3:query

```gq
query nearest_fills_limit($q: Vector(4)) {
    match {
        $d: Doc
        $d knows $t
    }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 3
}
```

```gq
gq 3;
query nearest_fills_limit($q: Vector(4)) {
    match { from nearest({ match {
        $d: Doc
        $d knows $t
    } return { $d, $t } }, $d.embedding, $q, k: 3) }
    return { $d.slug }
    order { $distance }
    limit 3
}
```

### crates/omnigraph-gqt/cases/traversal_nearest_rare_survivors_exact_pass.gqt#3:query

```gq
query nearest_with_edge($q: Vector(4)) {
    match {
        $d: Doc
        $d knows $t
    }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 2
}
```

```gq
gq 3;
query nearest_with_edge($q: Vector(4)) {
    match { from nearest({ match {
        $d: Doc
        $d knows $t
    } return { $d, $t } }, $d.embedding, $q, k: 2) }
    return { $d.slug }
    order { $distance }
    limit 2
}
```

### crates/omnigraph-gqt/cases/traversal_nearest_rare_survivors_exact_pass.gqt#7:query

```gq
query nearest_with_edge_wide($q: Vector(4)) {
    match {
        $d: Doc
        $d knows $t
    }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 50
}
```

```gq
gq 3;
query nearest_with_edge_wide($q: Vector(4)) {
    match { from nearest({ match {
        $d: Doc
        $d knows $t
    } return { $d, $t } }, $d.embedding, $q, k: 50) }
    return { $d.slug }
    order { $distance }
    limit 50
}
```

### crates/omnigraph-gqt/cases/traversal_nearest_single_eligible_doc.gqt#3:query

```gq
query nearest_single_source($q: Vector(4)) {
    match {
        $d: Doc
        $d knows $t
    }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 3
}
```

```gq
gq 3;
query nearest_single_source($q: Vector(4)) {
    match { from nearest({ match {
        $d: Doc
        $d knows $t
    } return { $d, $t } }, $d.embedding, $q, k: 3) }
    return { $d.slug }
    order { $distance }
    limit 3
}
```

### crates/omnigraph-gqt/cases/traversal_nearest_with_source_and_target_filters.gqt#3:query

```gq
query nearest_with_source_filter($q: Vector(4)) {
    match {
        $d: Doc
        $d.n > 15
        $d knows $t
    }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 2
}
```

```gq
gq 3;
query nearest_with_source_filter($q: Vector(4)) {
    match { from nearest({ match {
        $d: Doc
        $d.n > 15
        $d knows $t
    } return { $d, $t } }, $d.embedding, $q, k: 2) }
    return { $d.slug }
    order { $distance }
    limit 2
}
```

### crates/omnigraph-gqt/cases/traversal_nearest_with_source_and_target_filters.gqt#7:query

```gq
query nearest_with_target_filter($q: Vector(4)) {
    match {
        $d: Doc
        $d knows $t
        $t.n > 25
    }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 2
}
```

```gq
gq 3;
query nearest_with_target_filter($q: Vector(4)) {
    match { from nearest({ match {
        $d: Doc
        $d knows $t
        $t.n > 25
    } return { $d, $t } }, $d.embedding, $q, k: 2) }
    return { $d.slug }
    order { $distance }
    limit 2
}
```

### crates/omnigraph-gqt/cases/traversal_nearest_with_source_and_target_filters.gqt#11:query

```gq
query nearest_with_source_filter_above_gate($q: Vector(4)) {
    match {
        $d: Doc
        $d.n > 15
        $d likes $t
    }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 2
}
```

```gq
gq 3;
query nearest_with_source_filter_above_gate($q: Vector(4)) {
    match { from nearest({ match {
        $d: Doc
        $d.n > 15
        $d likes $t
    } return { $d, $t } }, $d.embedding, $q, k: 2) }
    return { $d.slug }
    order { $distance }
    limit 2
}
```

### crates/omnigraph-gqt/cases/two_hop_nearest_ranks_chain_heads.gqt#3:query

```gq
query nearest_two_hop_sources($q: Vector(4)) {
    match {
        $d: Doc
        $d knows $t
        $t knows $u
    }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 2
}
```

```gq
gq 3;
query nearest_two_hop_sources($q: Vector(4)) {
    match { from nearest({ match {
        $d: Doc
        $d knows $t
        $t knows $u
    } return { $d, $t, $u } }, $d.embedding, $q, k: 2) }
    return { $d.slug }
    order { $distance }
    limit 2
}
```

### crates/omnigraph-gqt/cases/typed_comparison_domains.gqt#51:query

```gq
set engine = v2;
query signed_list_exact_literal() {
    match { $x: Number }
    return { $x.key, $x.ns contains 9007199254740992.0 as member }
}
```

```gq
gq 3;
set engine = v2;
query signed_list_exact_literal() {
    match { $x: Number }
    return { $x.key, 9007199254740992.0 in $x.ns as member }
}
```

### crates/omnigraph-gqt/cases/typed_comparison_domains.gqt#63:query

```gq
set engine = v2;
query unsigned_list_negative_needle($needle: I64) {
    match { $x: Number }
    return { $x.key, $x.us contains $needle as member }
}
```

```gq
gq 3;
set engine = v2;
query unsigned_list_negative_needle($needle: I64) {
    match { $x: Number }
    return { $x.key, $needle in $x.us as member }
}
```

### crates/omnigraph-gqt/cases/typed_comparison_domains.gqt#68:query

```gq
set engine = v2;
query f32_list_nonrepresentable_needle() {
    match { $x: Number $x.fs contains 0.1 }
    return { $x.key }
}
```

```gq
gq 3;
set engine = v2;
query f32_list_nonrepresentable_needle() {
    match { $x: Number 0.1 in $x.fs }
    return { $x.key }
}
```

### crates/omnigraph-gqt/cases/typed_mutation_domains.gqt#3:mutate

```gq
query literal_does_not_narrow_lossily() {
    update Number set { marked: true } where f = 0.1
}
```

```gq
gq 3;
query literal_does_not_narrow_lossily() {
    $x: Number  $x.f = 0.1
    update $x set { marked: true }
}
```

### crates/omnigraph-gqt/cases/typed_mutation_domains.gqt#5:mutate

```gq
query omitted_nullable_bound($bound: F64?) {
    update Number set { marked: true } where f = $bound
}
```

```gq
gq 3;
query omitted_nullable_bound($bound: F64?) {
    $x: Number  $x.f = $bound
    update $x set { marked: true }
}
```

### crates/omnigraph-gqt/cases/typed_mutation_domains.gqt#7:mutate

```gq
query declared_parameter($bound: F32) {
    update Number set { marked: true } where f = $bound
}
```

```gq
gq 3;
query declared_parameter($bound: F32) {
    $x: Number  $x.f = $bound
    update $x set { marked: true }
}
```

### crates/omnigraph-gqt/cases/typed_mutation_domains.gqt#10:mutate

```gq
query fractional_bound() {
    update Number set { marked: true } where small > 2.5
}
```

```gq
gq 3;
query fractional_bound() {
    $x: Number  $x.small > 2.5
    update $x set { marked: true }
}
```

### crates/omnigraph-gqt/cases/typed_mutation_domains.gqt#15:mutate

```gq
query parameter_assignment_rounding($small: F32, $wide: F64) {
    update Number set { rounded: $small = $wide } where key = "a"
}
```

```gq
gq 3;
query parameter_assignment_rounding($small: F32, $wide: F64) {
    $x: Number  $x.key = "a"
    update $x set { rounded: $small = $wide }
}
```

### crates/omnigraph-gqt/cases/typed_search_options.gqt#3:query

```gq
set engine = v2;
query omitted_nullable_fuzzy_option($edits: I64?) {
    match { $d: Doc fuzzy($d.text, "needle", $edits) }
    return { $d.key }
}
```

```gq
gq 3;
set engine = v2;
query omitted_nullable_fuzzy_option($edits: I64?) {
    match { $d: Doc fuzzy($d.text, "needle", max_edits: $edits) }
    return { $d.key }
}
```

### crates/omnigraph-gqt/cases/typed_search_options.gqt#6:query

```gq
set engine = v2;
query negative_fuzzy_option($edits: I64) {
    match { $d: Doc fuzzy($d.text, "needle", $edits) }
    return { $d.key }
}
```

```gq
gq 3;
set engine = v2;
query negative_fuzzy_option($edits: I64) {
    match { $d: Doc fuzzy($d.text, "needle", max_edits: $edits) }
    return { $d.key }
}
```

### crates/omnigraph-gqt/cases/typed_search_options.gqt#9:query

```gq
set engine = v2;
query oversized_fuzzy_option($edits: U64) {
    match { $d: Doc fuzzy($d.text, "needle", $edits) }
    return { $d.key }
}
```

```gq
gq 3;
set engine = v2;
query oversized_fuzzy_option($edits: U64) {
    match { $d: Doc fuzzy($d.text, "needle", max_edits: $edits) }
    return { $d.key }
}
```

### crates/omnigraph-gqt/cases/typed_search_options.gqt#28:query

```gq
set engine = v2;
query null_bm25_query($q: String?) {
    match { $d: Doc }
    return { $d.key }
    order { bm25($d.text, $q) }
    limit 1
}
```

```gq
gq 3;
set engine = v2;
query null_bm25_query($q: String?) {
    match { from bm25({ match { $d: Doc } return { $d } }, $d.text, $q) }
    return { $d.key }
    order { $score desc }
    limit 1
}
```

### crates/omnigraph-gqt/cases/typed_search_options.gqt#31:query

```gq
set engine = v2;
query null_nearest_query($q: Vector(2)?) {
    match { $d: Doc }
    return { $d.key }
    order { nearest($d.embedding, $q) }
    limit 1
}
```

```gq
gq 3;
set engine = v2;
query null_nearest_query($q: Vector(2)?) {
    match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 1) }
    return { $d.key }
    order { $distance }
    limit 1
}
```

### crates/omnigraph-gqt/cases/unattached_branch_noop_child_creates_no_snapshot.gqt#6:mutate

```gq
query noop_child() as unattached branch on "${r}" {
    update Person set { age: 99 } where name = "nobody"
}
```

```gq
gq 3;
query noop_child() as unattached branch on "${r}" {
    $x: Person  $x.name = "nobody"
    update $x set { age: 99 }
}
```

### crates/omnigraph-gqt/cases/unkeyed_edge_dup_delete_after_merge.gqt#16:mutate

```gq
query delete_alice_edges() { delete Knows where from = "Alice" }
```

```gq
gq 3;
query delete_alice_edges() { $x: Knows  $x.@src = "Alice"  delete $x }
```

### crates/omnigraph-gqt/cases/update_column_patch_matches_whole_row_update.gqt#8:mutate

```gq
query patch_scores() {
    update Doc set { score: 5 } where age >= 2
}
```

```gq
gq 3;
query patch_scores() {
    $x: Doc  $x.age >= 2
    update $x set { score: 5 }
}
```

### crates/omnigraph-gqt/cases/update_column_patch_matches_whole_row_update.gqt#24:query

```gq
query nearest_first($q: Vector(4)) {
    match { $d: Doc }
    return { $d.slug }
    order { nearest($d.emb, $q) }
    limit 6
}
```

```gq
gq 3;
query nearest_first($q: Vector(4)) {
    match { from nearest({ match { $d: Doc } return { $d } }, $d.emb, $q, k: 6) }
    return { $d.slug }
    order { $distance }
    limit 6
}
```

### crates/omnigraph-gqt/cases/update_column_patch_matches_whole_row_update.gqt#28:query

```gq
query nearest_first($q: Vector(4)) {
    match { $d: Doc }
    return { $d.slug }
    order { nearest($d.emb, $q) }
    limit 6
}
```

```gq
gq 3;
query nearest_first($q: Vector(4)) {
    match { from nearest({ match { $d: Doc } return { $d } }, $d.emb, $q, k: 6) }
    return { $d.slug }
    order { $distance }
    limit 6
}
```

### crates/omnigraph-gqt/cases/update_column_patch_matches_whole_row_update.gqt#36:mutate

```gq
query patch_out_of_range() {
    update Doc set { score: 500 } where age = 0
}
```

```gq
gq 3;
query patch_out_of_range() {
    $x: Doc  $x.age = 0
    update $x set { score: 500 }
}
```

### crates/omnigraph-gqt/cases/update_column_patch_matches_whole_row_update.gqt#40:mutate

```gq
query patch_duplicate_code() {
    update Doc set { code: "c-03" } where age = 0
}
```

```gq
gq 3;
query patch_duplicate_code() {
    $x: Doc  $x.age = 0
    update $x set { code: "c-03" }
}
```

### crates/omnigraph-gqt/cases/update_column_patch_matches_whole_row_update.gqt#44:mutate

```gq
query delete_one() {
    delete Doc where slug = "d-01"
}
```

```gq
gq 3;
query delete_one() {
    $x: Doc  $x.slug = "d-01"
    delete $x
}
```

### crates/omnigraph-gqt/cases/update_column_patch_matches_whole_row_update.gqt#46:mutate

```gq
query delete_one() {
    delete Doc where slug = "d-01"
}
```

```gq
gq 3;
query delete_one() {
    $x: Doc  $x.slug = "d-01"
    delete $x
}
```

### crates/omnigraph-gqt/cases/update_column_patch_matches_whole_row_update.gqt#48:mutate

```gq
query patch_again() {
    update Doc set { score: 7 } where age < 4
}
```

```gq
gq 3;
query patch_again() {
    $x: Doc  $x.age < 4
    update $x set { score: 7 }
}
```

### crates/omnigraph-gqt/cases/update_column_patch_matches_whole_row_update.gqt#60:mutate

```gq
query patch_on_feature() {
    update Doc set { score: 9 } where age = 5
}
```

```gq
gq 3;
query patch_on_feature() {
    $x: Doc  $x.age = 5
    update $x set { score: 9 }
}
```

### crates/omnigraph-gqt/cases/update_column_patch_matches_whole_row_update.gqt#62:mutate

```gq
query patch_on_main() {
    update Doc set { score: 8 } where age = 0
}
```

```gq
gq 3;
query patch_on_main() {
    $x: Doc  $x.age = 0
    update $x set { score: 8 }
}
```

### crates/omnigraph-gqt/cases/v2/bm25_score_ignores_a_filter_over_an_unindexed_fragment.gqt#3:query

```gq
query covered() {
    match { $d: Doc $d.year >= 2020 }
    return { $d.slug, bm25($d.text, "graph") as score }
    order { bm25($d.text, "graph") }
}
```

```gq
gq 3;
query covered() {
    match { from bm25({ match { $d: Doc $d.year >= 2020 } return { $d } }, $d.text, "graph") }
    return { $d.slug, $score as score }
    order { $score desc }
}
```

### crates/omnigraph-gqt/cases/v2/bm25_score_ignores_a_filter_over_an_unindexed_fragment.gqt#9:query

```gq
query unfiltered() {
    match { $d: Doc }
    return { $d.slug, bm25($d.text, "graph") as score }
    order { bm25($d.text, "graph") }
}
```

```gq
gq 3;
query unfiltered() {
    match { from bm25({ match { $d: Doc } return { $d } }, $d.text, "graph") }
    return { $d.slug, $score as score }
    order { $score desc }
}
```

### crates/omnigraph-gqt/cases/v2/bm25_score_ignores_a_filter_over_an_unindexed_fragment.gqt#12:query

```gq
query filtered() {
    match { $d: Doc $d.year >= 2000 }
    return { $d.slug, bm25($d.text, "graph") as score }
    order { bm25($d.text, "graph") }
}
```

```gq
gq 3;
query filtered() {
    match { from bm25({ match { $d: Doc $d.year >= 2000 } return { $d } }, $d.text, "graph") }
    return { $d.slug, $score as score }
    order { $score desc }
}
```

### crates/omnigraph-gqt/cases/v2/bm25_ties_after_traversal.gqt#3:query

```gq
set engine = v2;
query recall($q: String) {
    match {
        $c: Chunk
        $c chunkOfArtifact $a
        search($c.text, $q)
    }
    return { $c.slug, $a.slug }
    order { bm25($c.text, $q) }
    limit 1
}
```

```gq
gq 3;
set engine = v2;
query recall($q: String) {
    match { from bm25({ match {
        $c: Chunk
        $c chunkOfArtifact $a
        search($c.text, $q)
    } return { $c, $a } }, $c.text, $q) }
    return { $c.slug, $a.slug }
    order { $score desc }
    limit 1
}
```

### crates/omnigraph-gqt/cases/v2/bm25_ties_after_traversal.gqt#7:query

```gq
set engine = v2;
query root_tie($q: String) {
    match { $c: Chunk }
    return { $c.slug }
    order { bm25($c.text, $q) }
    limit 1
}
```

```gq
gq 3;
set engine = v2;
query root_tie($q: String) {
    match { from bm25({ match { $c: Chunk } return { $c } }, $c.text, $q) }
    return { $c.slug }
    order { $score desc }
    limit 1
}
```

### crates/omnigraph-gqt/cases/v2/issue_747_full_text_calls_need_a_full_text_index.gqt#12:query

```gq
query ranked($q: String) {
    match { $d: Doc }
    return { $d.slug }
    order { bm25($d.notes, $q) }
    limit 2
}
```

```gq
gq 3;
query ranked($q: String) {
    match { from bm25({ match { $d: Doc } return { $d } }, $d.notes, $q) }
    return { $d.slug }
    order { $score desc }
    limit 2
}
```

### crates/omnigraph-gqt/cases/v2/order_key_bound_to_an_equally_lowered_return_item.gqt#3:query

```gq
query by_contains() {
    match { $d: Doc }
    return { "a" in $d.tags as a, $d.tags contains "a" as b }
    order { $d.tags contains "a" }
}
```

```gq
gq 3;
query by_contains() {
    match { $d: Doc }
    return { "a" in $d.tags as a, "a" in $d.tags as b }
    order { "a" in $d.tags }
}
```

### crates/omnigraph-gqt/cases/v2/order_key_bound_to_an_equally_lowered_return_item.gqt#6:query

```gq
query by_alias() {
    match { $d: Doc }
    return { "a" in $d.tags as a, $d.tags contains "a" as b }
    order { a desc }
}
```

```gq
gq 3;
query by_alias() {
    match { $d: Doc }
    return { "a" in $d.tags as a, "a" in $d.tags as b }
    order { a desc }
}
```

### crates/omnigraph-gqt/cases/v2/planner/anti_join_correlated_filter_keeps_the_plan.gqt#3:query

```gq
set engine = v2;
query count_non_x_docs() {
    match {
        $d: Doc
        not { $d.slug starts_with "x" }
    }
    return { count($d) as n }
}
```

```gq
gq 3;
set engine = v2;
query count_non_x_docs() {
    match {
        $d: Doc
        not { starts_with($d.slug, "x") }
    }
    return { count($d) as n }
}
```

### crates/omnigraph-gqt/cases/v2/planner/contains_beside_search_and_ranking.gqt#3:query

```gq
set engine = v2;
query bm25_order() {
    match {
        $m: Matter
        $p: Passage
        $p.text contains $m.number
        $m.number != $p.pid
    }
    return { $p.pid }
    order { bm25($p.text, "law") }
    limit 10
}
```

```gq
gq 3;
set engine = v2;
query bm25_order() {
    match { from bm25({ match {
        $m: Matter
        $p: Passage
        contains($p.text, $m.number)
        $m.number != $p.pid
    } return { $m, $p } }, $p.text, "law") }
    return { $p.pid }
    order { $score desc }
    limit 10
}
```

### crates/omnigraph-gqt/cases/v2/planner/contains_beside_search_and_ranking.gqt#7:query

```gq
set engine = v2;
query nearest_order() {
    match {
        $m: Matter
        $p: Passage
        $p.text contains $m.number
        $m.number != $p.pid
    }
    return { $p.pid }
    order { nearest($p.embedding, [1.0, 0.0]) }
    limit 10
}
```

```gq
gq 3;
set engine = v2;
query nearest_order() {
    match { from nearest({ match {
        $m: Matter
        $p: Passage
        contains($p.text, $m.number)
        $m.number != $p.pid
    } return { $m, $p } }, $p.embedding, [1.0, 0.0], k: 10) }
    return { $p.pid }
    order { $distance }
    limit 10
}
```

### crates/omnigraph-gqt/cases/v2/planner/contains_beside_search_and_ranking.gqt#11:query

```gq
set engine = v2;
query rrf_order() {
    match {
        $m: Matter
        $p: Passage
        $p.text contains $m.number
        $m.number != $p.pid
    }
    return { $p.pid }
    order { rrf(nearest($p.embedding, [1.0, 0.0]), bm25($p.text, "law")) }
    limit 10
}
```

```gq
gq 3;
set engine = v2;
query rrf_order() {
    match { let $p2 = { match {
        $m: Matter
        $p: Passage
        contains($p.text, $m.number)
        $m.number != $p.pid
    } return { $m, $p } }  from rrf(nearest($p2, $p.embedding, [1.0, 0.0], k: 10), bm25($p2, $p.text, "law"), k: 60) }
    return { $p.pid }
    order { $score desc }
    limit 10
}
```

### crates/omnigraph-gqt/cases/v2/planner/contains_beside_search_and_ranking.gqt#15:query

```gq
set engine = v2;
query search_filter_unranked() {
    match {
        $m: Matter
        $p: Passage
        $p.text contains $m.number
        search($p.text, "law")
        $m.number != $p.pid
    }
    return { $p.pid }
}
```

```gq
gq 3;
set engine = v2;
query search_filter_unranked() {
    match {
        $m: Matter
        $p: Passage
        contains($p.text, $m.number)
        search($p.text, "law")
        $m.number != $p.pid
    }
    return { $p.pid }
}
```

### crates/omnigraph-gqt/cases/v2/planner/contains_beside_search_and_ranking.gqt#19:query

```gq
set engine = v2;
query bm25_order_on_the_needles() {
    match {
        $m: Matter
        $p: Passage
        $p.text contains $m.number
        $m.number != $p.pid
    }
    return { $p.pid }
    order { bm25($m.number, "case") }
    limit 10
}
```

```gq
gq 3;
set engine = v2;
query bm25_order_on_the_needles() {
    match { from bm25({ match {
        $m: Matter
        $p: Passage
        contains($p.text, $m.number)
        $m.number != $p.pid
    } return { $m, $p } }, $m.number, "case") }
    return { $p.pid }
    order { $score desc }
    limit 10
}
```

### crates/omnigraph-gqt/cases/v2/planner/contains_join_pairs_each_needle_a_text_holds.gqt#3:query

```gq
set engine = v2;
query found_numbers() {
    match {
        $m: Matter
        $p: Passage
        $p.text contains $m.number
        $m.mid != "m-empty"
    }
    return { $m.mid, $p.pid }
}
```

```gq
gq 3;
set engine = v2;
query found_numbers() {
    match {
        $m: Matter
        $p: Passage
        contains($p.text, $m.number)
        $m.mid != "m-empty"
    }
    return { $m.mid, $p.pid }
}
```

### crates/omnigraph-gqt/cases/v2/planner/contains_join_pairs_each_needle_a_text_holds.gqt#7:query

```gq
set engine = v2;
query found_numbers_and_the_empty_one() {
    match {
        $m: Matter
        $p: Passage
        $p.text contains $m.number
    }
    return { $m.mid, $p.pid }
}
```

```gq
gq 3;
set engine = v2;
query found_numbers_and_the_empty_one() {
    match {
        $m: Matter
        $p: Passage
        contains($p.text, $m.number)
    }
    return { $m.mid, $p.pid }
}
```

### crates/omnigraph-gqt/cases/v2/planner/contains_join_pairs_each_needle_a_text_holds.gqt#10:query

```gq
set engine = v2;
query found_numbers_by_every_pair() {
    match {
        $m: Matter
        $p: Passage
        ($p.text contains $m.number) or false
    }
    return { $m.mid, $p.pid }
}
```

```gq
gq 3;
set engine = v2;
query found_numbers_by_every_pair() {
    match {
        $m: Matter
        $p: Passage
        (contains($p.text, $m.number)) or false
    }
    return { $m.mid, $p.pid }
}
```

### crates/omnigraph-gqt/cases/v2/planner/contains_join_pairs_each_needle_a_text_holds.gqt#14:query

```gq
set engine = v2;
query found_numbers_under_a_second_filter() {
    match {
        $m: Matter
        $p: Passage
        $p.text contains $m.number
        $m.number != $p.text and ($m.mid != "m-16" or $p.pid != "p-overlap")
    }
    return { $m.mid, $p.pid }
}
```

```gq
gq 3;
set engine = v2;
query found_numbers_under_a_second_filter() {
    match {
        $m: Matter
        $p: Passage
        contains($p.text, $m.number)
        $m.number != $p.text and ($m.mid != "m-16" or $p.pid != "p-overlap")
    }
    return { $m.mid, $p.pid }
}
```

### crates/omnigraph-gqt/cases/v2/planner/contains_join_pairs_each_needle_a_text_holds.gqt#18:query

```gq
set engine = v2;
query passage_written_first() {
    match {
        $p: Passage
        $m: Matter
        $p.text contains $m.number
        $m.mid != "m-empty"
    }
    return { $m.mid, $p.pid }
}
```

```gq
gq 3;
set engine = v2;
query passage_written_first() {
    match {
        $p: Passage
        $m: Matter
        contains($p.text, $m.number)
        $m.mid != "m-empty"
    }
    return { $m.mid, $p.pid }
}
```

### crates/omnigraph-gqt/cases/v2/planner/contains_join_sieves_a_haystack_with_blob_and_vector_properties.gqt#3:query

```gq
set engine = v2;
query found_numbers() {
    match {
        $m: Matter
        $p: Passage
        $p.text contains $m.number
    }
    return { $m.mid, $p, $p.embedding }
}
```

```gq
gq 3;
set engine = v2;
query found_numbers() {
    match {
        $m: Matter
        $p: Passage
        contains($p.text, $m.number)
    }
    return { $m.mid, $p, $p.embedding }
}
```

### crates/omnigraph-gqt/cases/v2/planner/contains_needle_filter_keeps_every_row.gqt#3:query

```gq
set engine = v2;
query literal_needles_with_passage_written_first() {
    match {
        $p: Passage
        $m: Matter
        $p.text contains $m.number
        $m.mid != "m-empty"
    }
    return { $m.mid, $p.pid }
}
```

```gq
gq 3;
set engine = v2;
query literal_needles_with_passage_written_first() {
    match {
        $p: Passage
        $m: Matter
        contains($p.text, $m.number)
        $m.mid != "m-empty"
    }
    return { $m.mid, $p.pid }
}
```

### crates/omnigraph-gqt/cases/v2/planner/contains_needle_filter_keeps_every_row.gqt#7:query

```gq
set engine = v2;
query empty_needle() {
    match {
        $m: Matter
        $p: Passage
        $p.text contains $m.number
        $m.mid = "m-empty"
    }
    return { $m.mid, $p.pid }
}
```

```gq
gq 3;
set engine = v2;
query empty_needle() {
    match {
        $m: Matter
        $p: Passage
        contains($p.text, $m.number)
        $m.mid = "m-empty"
    }
    return { $m.mid, $p.pid }
}
```

### crates/omnigraph-gqt/cases/v2/planner/contains_needle_filter_keeps_every_row.gqt#10:query

```gq
set engine = v2;
query null_needles_only() {
    match {
        $m: Matter
        $p: Passage
        $p.text contains $m.number
        $m.mid = "m-null"
    }
    return { $m.mid, $p.pid }
}
```

```gq
gq 3;
set engine = v2;
query null_needles_only() {
    match {
        $m: Matter
        $p: Passage
        contains($p.text, $m.number)
        $m.mid = "m-null"
    }
    return { $m.mid, $p.pid }
}
```

### crates/omnigraph-gqt/cases/v2/planner/contains_needle_filter_keeps_every_row.gqt#13:query

```gq
set engine = v2;
query contains_inside_an_or() {
    match {
        $m: Matter
        $p: Passage
        $p.text contains $m.number or $p.pid = "p-digit"
        $m.mid != "m-empty"
        $m.mid != "m-null"
    }
    return { $m.mid, $p.pid }
}
```

```gq
gq 3;
set engine = v2;
query contains_inside_an_or() {
    match {
        $m: Matter
        $p: Passage
        contains($p.text, $m.number) or $p.pid = "p-digit"
        $m.mid != "m-empty"
        $m.mid != "m-null"
    }
    return { $m.mid, $p.pid }
}
```

### crates/omnigraph-gqt/cases/v2/planner/explain_pipelines_collected_join_sides.gqt#3:query

```gq
set engine = v2;
query cites() {
    match {
        $p: Paper
        $m: Matter
        $p.text contains $m.number
    }
    return { $p.slug, $m.number }
}
```

```gq
gq 3;
set engine = v2;
query cites() {
    match {
        $p: Paper
        $m: Matter
        contains($p.text, $m.number)
    }
    return { $p.slug, $m.number }
}
```

### crates/omnigraph-gqt/cases/v2/planner/explain_pipelines_contains_join_empty_collected_side.gqt#3:query

```gq
set engine = v2;
query cites() {
    match {
        $p: Paper
        $m: Matter
        $p.text contains $m.number
    }
    return { $p.slug, $m.number }
}
```

```gq
gq 3;
set engine = v2;
query cites() {
    match {
        $p: Paper
        $m: Matter
        contains($p.text, $m.number)
    }
    return { $p.slug, $m.number }
}
```

### crates/omnigraph-gqt/cases/v2/planner/explain_pipelines_rank_fuse_arms.gqt#3:query

```gq
set engine = v2;
query fused($q: Vector(4), $t: String) {
    match { $d: Doc }
    return { $d.slug }
    order { rrf(nearest($d.embedding, $q), bm25($d.text, $t)) }
    limit 3
}
```

```gq
gq 3;
set engine = v2;
query fused($q: Vector(4), $t: String) {
    match { let $p = { match { $d: Doc } return { $d } }  from rrf(nearest($p, $d.embedding, $q, k: 3), bm25($p, $d.text, $t), k: 60) }
    return { $d.slug }
    order { $score desc }
    limit 3
}
```

### crates/omnigraph-gqt/cases/v2/planner/hydrate_return_columns_above_a_fusion.gqt#3:query

```gq
set engine = v2;
query fused_top_two($q: Vector(2), $t: String) {
    match { $d: Doc }
    return { $d.slug, $d.body, $d.title }
    order { rrf(nearest($d.embedding, $q), bm25($d.text, $t)) }
    limit 2
}
```

```gq
gq 3;
set engine = v2;
query fused_top_two($q: Vector(2), $t: String) {
    match { let $p = { match { $d: Doc } return { $d } }  from rrf(nearest($p, $d.embedding, $q, k: 2), bm25($p, $d.text, $t), k: 60) }
    return { $d.slug, $d.body, $d.title }
    order { $score desc }
    limit 2
}
```

### crates/omnigraph-gqt/cases/v2/planner/hydrate_return_columns_above_a_fusion.gqt#8:query

```gq
set engine = v2;
query ranked_top_two($t: String) {
    match { $d: Doc }
    return { $d.slug, $d.body, $d.title }
    order { bm25($d.text, $t) }
    limit 2
}
```

```gq
gq 3;
set engine = v2;
query ranked_top_two($t: String) {
    match { from bm25({ match { $d: Doc } return { $d } }, $d.text, $t) }
    return { $d.slug, $d.body, $d.title }
    order { $score desc }
    limit 2
}
```

### crates/omnigraph-gqt/cases/v2/planner/input_ann_nprobes.gqt#3:query

```gq
set engine = v2;
set ann_nprobes = 1;
query by_vector($q: Vector(4)) {
    match { $d: Doc }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 2
}
```

```gq
gq 3;
set engine = v2;
set ann_nprobes = 1;
query by_vector($q: Vector(4)) {
    match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 2) }
    return { $d.slug }
    order { $distance }
    limit 2
}
```

### crates/omnigraph-gqt/cases/v2/planner/input_ann_nprobes.gqt#8:query

```gq
set engine = v2;
query by_vector($q: Vector(4)) {
    match { $d: Doc }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 2
}
```

```gq
gq 3;
set engine = v2;
query by_vector($q: Vector(4)) {
    match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 2) }
    return { $d.slug }
    order { $distance }
    limit 2
}
```

### crates/omnigraph-gqt/cases/v2/planner/issue_762_rrf_over_a_reached_binding.gqt#3:query

```gq
query ranked_destination() {
    match { $t: Topic { name: "storage" } $d about $t }
    return { $d.slug }
    order { rrf(nearest($d.embedding, [1.0, 0.0]), bm25($d.body, "needle")) }
    limit 1
}
```

```gq
gq 3;
query ranked_destination() {
    match { let $p = { match { $t: Topic { name: "storage" } $d about $t } return { $t, $d } }  from rrf(nearest($p, $d.embedding, [1.0, 0.0], k: 1), bm25($p, $d.body, "needle"), k: 60) }
    return { $d.slug }
    order { $score desc }
    limit 1
}
```

### crates/omnigraph-gqt/cases/v2/planner/issue_775_cross_binding_filter_on_the_contains_join.gqt#3:query

```gq
set engine = v2;
query cross_binding_filter_stays_below_traversal() {
    match {
        $m: Matter
        $p: Passage
        $p.text contains $m.number
        $p passageOfRevision $r
        $r: SourceRevision
        $r.matter_number != $m.number
    }
    return { $r.matter_number, $m.number as cited, count($p) as n }
    order { $r.matter_number asc }
    limit 30
}
```

```gq
gq 3;
set engine = v2;
query cross_binding_filter_stays_below_traversal() {
    match {
        $m: Matter
        $p: Passage
        contains($p.text, $m.number)
        $p passageOfRevision $r
        $r: SourceRevision
        $r.matter_number != $m.number
    }
    return { $r.matter_number, $m.number as cited, count($p) as n }
    order { $r.matter_number asc }
    limit 30
}
```

### crates/omnigraph-gqt/cases/v2/planner/issue_775_cross_binding_filter_on_the_contains_join.gqt#7:query

```gq
set engine = v2;
query passage_reached_by_the_traversal() {
    match {
        $m: Matter
        $r: SourceRevision
        $p passageOfRevision $r
        $p.text contains $m.number
        $r.matter_number != $m.number
    }
    return { $r.matter_number, $m.number as cited, count($p) as n }
    order { $r.matter_number asc }
    limit 30
}
```

```gq
gq 3;
set engine = v2;
query passage_reached_by_the_traversal() {
    match {
        $m: Matter
        $r: SourceRevision
        $p passageOfRevision $r
        contains($p.text, $m.number)
        $r.matter_number != $m.number
    }
    return { $r.matter_number, $m.number as cited, count($p) as n }
    order { $r.matter_number asc }
    limit 30
}
```

### crates/omnigraph-gqt/cases/v2/planner/issue_789_nearest_and_rrf_rank_a_reached_binding.gqt#3:query

```gq
query nearest_on_an_undeclared_destination($q: Vector(2)) {
    match {
        $p: Person { name: "p1" }
        $p knows $d
    }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 1
}
```

```gq
gq 3;
query nearest_on_an_undeclared_destination($q: Vector(2)) {
    match { from nearest({ match {
        $p: Person { name: "p1" }
        $p knows $d
    } return { $p, $d } }, $d.embedding, $q, k: 1) }
    return { $d.slug }
    order { $distance }
    limit 1
}
```

### crates/omnigraph-gqt/cases/v2/planner/issue_789_nearest_and_rrf_rank_a_reached_binding.gqt#9:query

```gq
query nearest_on_a_destination_declared_second($q: Vector(2)) {
    match {
        $p: Person { name: "p1" }
        $d: Doc
        $p knows $d
    }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 2
}
```

```gq
gq 3;
query nearest_on_a_destination_declared_second($q: Vector(2)) {
    match { from nearest({ match {
        $p: Person { name: "p1" }
        $d: Doc
        $p knows $d
    } return { $p, $d } }, $d.embedding, $q, k: 2) }
    return { $d.slug }
    order { $distance }
    limit 2
}
```

### crates/omnigraph-gqt/cases/v2/planner/issue_789_nearest_and_rrf_rank_a_reached_binding.gqt#15:query

```gq
query rrf_over_one_destination($q: Vector(2)) {
    match {
        $p: Person { name: "p1" }
        $p knows $d
    }
    return { $d.slug }
    order { rrf(nearest($d.embedding, $q), bm25($d.body, "needle")) }
    limit 2
}
```

```gq
gq 3;
query rrf_over_one_destination($q: Vector(2)) {
    match { let $p2 = { match {
        $p: Person { name: "p1" }
        $p knows $d
    } return { $p, $d } }  from rrf(nearest($p2, $d.embedding, $q, k: 2), bm25($p2, $d.body, "needle"), k: 60) }
    return { $d.slug }
    order { $score desc }
    limit 2
}
```

### crates/omnigraph-gqt/cases/v2/planner/issue_789_nearest_and_rrf_rank_a_reached_binding.gqt#20:query

```gq
query nearest_from_a_source_with_no_edges($q: Vector(2)) {
    match {
        $p: Person { name: "p3" }
        $p knows $d
    }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 2
}
```

```gq
gq 3;
query nearest_from_a_source_with_no_edges($q: Vector(2)) {
    match { from nearest({ match {
        $p: Person { name: "p3" }
        $p knows $d
    } return { $p, $d } }, $d.embedding, $q, k: 2) }
    return { $d.slug }
    order { $distance }
    limit 2
}
```

### crates/omnigraph-gqt/cases/v2/planner/issue_789_ranked_binding_roots_its_component.gqt#3:query

```gq
query bm25_on_an_undeclared_destination() {
    match {
        $p: Person { name: "p1" }
        $p knows $d
    }
    return { $d.slug }
    order { bm25($d.body, "needle") }
    limit 3
}
```

```gq
gq 3;
query bm25_on_an_undeclared_destination() {
    match { from bm25({ match {
        $p: Person { name: "p1" }
        $p knows $d
    } return { $p, $d } }, $d.body, "needle") }
    return { $d.slug }
    order { $score desc }
    limit 3
}
```

### crates/omnigraph-gqt/cases/v2/planner/issue_789_ranked_binding_roots_its_component.gqt#8:query

```gq
query bm25_two_hops_from_the_source() {
    match {
        $p: Person { name: "p1" }
        $p knows $x
        $x cites $d
    }
    return { $x.slug, $d.slug }
    order { bm25($d.body, "needle") }
    limit 3
}
```

```gq
gq 3;
query bm25_two_hops_from_the_source() {
    match { from bm25({ match {
        $p: Person { name: "p1" }
        $p knows $x
        $x cites $d
    } return { $p, $x, $d } }, $d.body, "needle") }
    return { $x.slug, $d.slug }
    order { $score desc }
    limit 3
}
```

### crates/omnigraph-gqt/cases/v2/planner/issue_789_ranked_binding_roots_its_component.gqt#13:query

```gq
query bm25_on_a_traversal_source() {
    match {
        $t: Doc { slug: "d3" }
        $d cites $t
    }
    return { $d.slug }
    order { bm25($d.body, "needle") }
    limit 3
}
```

```gq
gq 3;
query bm25_on_a_traversal_source() {
    match { from bm25({ match {
        $t: Doc { slug: "d3" }
        $d cites $t
    } return { $t, $d } }, $d.body, "needle") }
    return { $d.slug }
    order { $score desc }
    limit 3
}
```

### crates/omnigraph-gqt/cases/v2/planner/issue_789_ranked_binding_roots_its_component.gqt#18:query

```gq
query bm25_over_an_undirected_traversal() {
    match {
        $a: Doc { slug: "d3" }
        $a <cites> $d
    }
    return { $d.slug }
    order { bm25($d.body, "needle") }
    limit 3
}
```

```gq
gq 3;
query bm25_over_an_undirected_traversal() {
    match { from bm25({ match {
        $a: Doc { slug: "d3" }
        $a <cites> $d
    } return { $a, $d } }, $d.body, "needle") }
    return { $d.slug }
    order { $score desc }
    limit 3
}
```

### crates/omnigraph-gqt/cases/v2/planner/key_probe_after_branch_merges.gqt#14:mutate

```gq
query bump_alpha() {
    update Person set { age: 44 } where slug = "alpha"
}
```

```gq
gq 3;
query bump_alpha() {
    $x: Person  $x.slug = "alpha"
    update $x set { age: 44 }
}
```

### crates/omnigraph-gqt/cases/v2/planner/metadata_count_branches.gqt#5:mutate

```gq
query delete_main() { delete Doc where slug = "a" }
```

```gq
gq 3;
query delete_main() { $x: Doc  $x.slug = "a"  delete $x }
```

### crates/omnigraph-gqt/cases/v2/planner/metadata_count_branches.gqt#15:mutate

```gq
query delete_saved() { delete Doc where slug = "b" }
```

```gq
gq 3;
query delete_saved() { $x: Doc  $x.slug = "b"  delete $x }
```

### crates/omnigraph-gqt/cases/v2/planner/metadata_count_branches.gqt#21:mutate

```gq
query delete_remaining() { delete Doc where slug != "a" }
```

```gq
gq 3;
query delete_remaining() { $x: Doc  $x.slug != "a"  delete $x }
```

### crates/omnigraph-gqt/cases/v2/planner/nearest_pushes_every_grammar_filter_shape_into_the_scan.gqt#3:query

```gq
query f_eq($q: Vector(4)) {
    match { $d: Doc
        $d.n = 7 }
    return { $d.slug } order { nearest($d.embedding, $q) } limit 3
}
```

```gq
gq 3;
query f_eq($q: Vector(4)) {
    match { from nearest({ match { $d: Doc
        $d.n = 7 } return { $d } }, $d.embedding, $q, k: 3) }
    return { $d.slug } order { $distance } limit 3
}
```

### crates/omnigraph-gqt/cases/v2/planner/nearest_pushes_every_grammar_filter_shape_into_the_scan.gqt#8:query

```gq
query f_ne($q: Vector(4)) {
    match { $d: Doc
        $d.n != 0 }
    return { $d.slug } order { nearest($d.embedding, $q) } limit 3
}
```

```gq
gq 3;
query f_ne($q: Vector(4)) {
    match { from nearest({ match { $d: Doc
        $d.n != 0 } return { $d } }, $d.embedding, $q, k: 3) }
    return { $d.slug } order { $distance } limit 3
}
```

### crates/omnigraph-gqt/cases/v2/planner/nearest_pushes_every_grammar_filter_shape_into_the_scan.gqt#13:query

```gq
query f_gt($q: Vector(4)) {
    match { $d: Doc
        $d.n > 10 }
    return { $d.slug } order { nearest($d.embedding, $q) } limit 3
}
```

```gq
gq 3;
query f_gt($q: Vector(4)) {
    match { from nearest({ match { $d: Doc
        $d.n > 10 } return { $d } }, $d.embedding, $q, k: 3) }
    return { $d.slug } order { $distance } limit 3
}
```

### crates/omnigraph-gqt/cases/v2/planner/nearest_pushes_every_grammar_filter_shape_into_the_scan.gqt#18:query

```gq
query f_lt($q: Vector(4)) {
    match { $d: Doc
        $d.n < 3 }
    return { $d.slug } order { nearest($d.embedding, $q) } limit 3
}
```

```gq
gq 3;
query f_lt($q: Vector(4)) {
    match { from nearest({ match { $d: Doc
        $d.n < 3 } return { $d } }, $d.embedding, $q, k: 3) }
    return { $d.slug } order { $distance } limit 3
}
```

### crates/omnigraph-gqt/cases/v2/planner/nearest_pushes_every_grammar_filter_shape_into_the_scan.gqt#23:query

```gq
query f_ge($q: Vector(4)) {
    match { $d: Doc
        $d.n >= 10 }
    return { $d.slug } order { nearest($d.embedding, $q) } limit 3
}
```

```gq
gq 3;
query f_ge($q: Vector(4)) {
    match { from nearest({ match { $d: Doc
        $d.n >= 10 } return { $d } }, $d.embedding, $q, k: 3) }
    return { $d.slug } order { $distance } limit 3
}
```

### crates/omnigraph-gqt/cases/v2/planner/nearest_pushes_every_grammar_filter_shape_into_the_scan.gqt#28:query

```gq
query f_le($q: Vector(4)) {
    match { $d: Doc
        $d.n <= 2 }
    return { $d.slug } order { nearest($d.embedding, $q) } limit 3
}
```

```gq
gq 3;
query f_le($q: Vector(4)) {
    match { from nearest({ match { $d: Doc
        $d.n <= 2 } return { $d } }, $d.embedding, $q, k: 3) }
    return { $d.slug } order { $distance } limit 3
}
```

### crates/omnigraph-gqt/cases/v2/planner/nearest_pushes_every_grammar_filter_shape_into_the_scan.gqt#33:query

```gq
query f_starts_with($q: Vector(4)) {
    match { $d: Doc
        $d.name starts_with "doc-2" }
    return { $d.slug } order { nearest($d.embedding, $q) } limit 3
}
```

```gq
gq 3;
query f_starts_with($q: Vector(4)) {
    match { from nearest({ match { $d: Doc
        starts_with($d.name, "doc-2") } return { $d } }, $d.embedding, $q, k: 3) }
    return { $d.slug } order { $distance } limit 3
}
```

### crates/omnigraph-gqt/cases/v2/planner/nearest_pushes_every_grammar_filter_shape_into_the_scan.gqt#38:query

```gq
query f_contains($q: Vector(4)) {
    match { $d: Doc
        $d.name contains "-3" }
    return { $d.slug } order { nearest($d.embedding, $q) } limit 3
}
```

```gq
gq 3;
query f_contains($q: Vector(4)) {
    match { from nearest({ match { $d: Doc
        contains($d.name, "-3") } return { $d } }, $d.embedding, $q, k: 3) }
    return { $d.slug } order { $distance } limit 3
}
```

### crates/omnigraph-gqt/cases/v2/planner/nearest_pushes_every_grammar_filter_shape_into_the_scan.gqt#43:query

```gq
query f_param($q: Vector(4), $n: I64) {
    match { $d: Doc
        $d.n = $n }
    return { $d.slug } order { nearest($d.embedding, $q) } limit 3
}
```

```gq
gq 3;
query f_param($q: Vector(4), $n: I64) {
    match { from nearest({ match { $d: Doc
        $d.n = $n } return { $d } }, $d.embedding, $q, k: 3) }
    return { $d.slug } order { $distance } limit 3
}
```

### crates/omnigraph-gqt/cases/v2/planner/nearest_pushes_every_grammar_filter_shape_into_the_scan.gqt#48:query

```gq
query f_flipped($q: Vector(4)) {
    match { $d: Doc
        10 < $d.n }
    return { $d.slug } order { nearest($d.embedding, $q) } limit 3
}
```

```gq
gq 3;
query f_flipped($q: Vector(4)) {
    match { from nearest({ match { $d: Doc
        10 < $d.n } return { $d } }, $d.embedding, $q, k: 3) }
    return { $d.slug } order { $distance } limit 3
}
```

### crates/omnigraph-gqt/cases/v2/planner/not_over_contains_null_operand_both_arms.gqt#3:query

```gq
set engine = v2;
query pushed_not_contains() {
    match { $p: Person  not ($p.name contains "li") }
    return { $p.slug }
}
```

```gq
gq 3;
set engine = v2;
query pushed_not_contains() {
    match { $p: Person  not (contains($p.name, "li")) }
    return { $p.slug }
}
```

### crates/omnigraph-gqt/cases/v2/planner/not_over_contains_null_operand_both_arms.gqt#7:query

```gq
set engine = v2;
query pushed_not_starts_with() {
    match { $p: Person  not ($p.name starts_with "a") }
    return { $p.slug }
}
```

```gq
gq 3;
set engine = v2;
query pushed_not_starts_with() {
    match { $p: Person  not (starts_with($p.name, "a")) }
    return { $p.slug }
}
```

### crates/omnigraph-gqt/cases/v2/planner/not_over_contains_null_operand_both_arms.gqt#10:query

```gq
set engine = v2;
query pushed_not_list_contains() {
    match { $p: Person  not ($p.tags contains "rust") }
    return { $p.slug }
}
```

```gq
gq 3;
set engine = v2;
query pushed_not_list_contains() {
    match { $p: Person  not ("rust" in $p.tags) }
    return { $p.slug }
}
```

### crates/omnigraph-gqt/cases/v2/planner/not_over_contains_null_operand_both_arms.gqt#13:query

```gq
set engine = v2;
query null_parameter_needle($needle: String?) {
    match { $p: Person  not ($p.name contains $needle) }
    return { $p.slug }
}
```

```gq
gq 3;
set engine = v2;
query null_parameter_needle($needle: String?) {
    match { $p: Person  not (contains($p.name, $needle)) }
    return { $p.slug }
}
```

### crates/omnigraph-gqt/cases/v2/planner/not_over_contains_null_operand_both_arms.gqt#17:query

```gq
set engine = v2;
query residual_not_contains() {
    match {
        $p: Person
        $q: Person { slug: "a" }
        not ($p.name contains $q.name)
    }
    return { $p.slug }
}
```

```gq
gq 3;
set engine = v2;
query residual_not_contains() {
    match {
        $p: Person
        $q: Person { slug: "a" }
        not (contains($p.name, $q.name))
    }
    return { $p.slug }
}
```

### crates/omnigraph-gqt/cases/v2/planner/not_over_contains_null_operand_both_arms.gqt#21:query

```gq
set engine = v2;
query residual_not_starts_with() {
    match {
        $p: Person
        $q: Person { slug: "b" }
        not ($p.name starts_with $q.name)
    }
    return { $p.slug }
}
```

```gq
gq 3;
set engine = v2;
query residual_not_starts_with() {
    match {
        $p: Person
        $q: Person { slug: "b" }
        not (starts_with($p.name, $q.name))
    }
    return { $p.slug }
}
```

### crates/omnigraph-gqt/cases/v2/planner/not_over_contains_null_operand_both_arms.gqt#24:query

```gq
set engine = v2;
query residual_not_list_contains() {
    match {
        $p: Person
        $q: Person { slug: "a" }
        not ($p.tags contains $q.name)
    }
    return { $p.slug }
}
```

```gq
gq 3;
set engine = v2;
query residual_not_list_contains() {
    match {
        $p: Person
        $q: Person { slug: "a" }
        not ($q.name in $p.tags)
    }
    return { $p.slug }
}
```

### crates/omnigraph-gqt/cases/v2/planner/overfetch_ladder_completes_a_traversal_source.gqt#3:query

```gq
set engine = v2;
query nearest_citing_docs($q: Vector(2)) {
    match { $d: Doc $d cites $t }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 3
}
```

```gq
gq 3;
set engine = v2;
query nearest_citing_docs($q: Vector(2)) {
    match { from nearest({ match { $d: Doc $d cites $t } return { $d, $t } }, $d.embedding, $q, k: 3) }
    return { $d.slug }
    order { $distance }
    limit 3
}
```

### crates/omnigraph-gqt/cases/v2/planner/projection_demand.gqt#15:query

```gq
set engine = v2;
query leading_nearest() {
    match { $c: T }
    return { $c.slug }
    order { nearest($c.embedding, [1.0, 0.0]) }
    limit 1
}
```

```gq
gq 3;
set engine = v2;
query leading_nearest() {
    match { from nearest({ match { $c: T } return { $c } }, $c.embedding, [1.0, 0.0], k: 1) }
    return { $c.slug }
    order { $distance }
    limit 1
}
```

### crates/omnigraph-gqt/cases/v2/planner/ranked_access_on_the_scan.gqt#3:query

```gq
set engine = v2;
query by_text($t: String) {
    match { $d: Doc }
    return { $d.slug }
    order { bm25($d.text, $t) }
    limit 2
}
```

```gq
gq 3;
set engine = v2;
query by_text($t: String) {
    match { from bm25({ match { $d: Doc } return { $d } }, $d.text, $t) }
    return { $d.slug }
    order { $score desc }
    limit 2
}
```

### crates/omnigraph-gqt/cases/v2/planner/ranked_access_on_the_scan.gqt#8:query

```gq
set engine = v2;
query by_vector($q: Vector(4)) {
    match { $d: Doc }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 2
}
```

```gq
gq 3;
set engine = v2;
query by_vector($q: Vector(4)) {
    match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 2) }
    return { $d.slug }
    order { $distance }
    limit 2
}
```

### crates/omnigraph-gqt/cases/v2/planner/ranked_access_on_the_scan.gqt#13:query

```gq
set engine = v2;
query by_vector_among_members($q: Vector(4), $members: [String]) {
    match { $d: Doc $d.slug in $members }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 1
}
```

```gq
gq 3;
set engine = v2;
query by_vector_among_members($q: Vector(4), $members: [String]) {
    match { from nearest({ match { $d: Doc $d.slug in $members } return { $d } }, $d.embedding, $q, k: 1) }
    return { $d.slug }
    order { $distance }
    limit 1
}
```

### crates/omnigraph-gqt/cases/v2/planner/ranked_access_on_the_scan.gqt#18:query

```gq
set engine = v2;
query fused($q: Vector(4), $t: String) {
    match { $d: Doc }
    return { $d.slug }
    order { rrf(nearest($d.embedding, $q), bm25($d.text, $t)) }
    limit 3
}
```

```gq
gq 3;
set engine = v2;
query fused($q: Vector(4), $t: String) {
    match { let $p = { match { $d: Doc } return { $d } }  from rrf(nearest($p, $d.embedding, $q, k: 3), bm25($p, $d.text, $t), k: 60) }
    return { $d.slug }
    order { $score desc }
    limit 3
}
```

### crates/omnigraph-gqt/cases/v2/planner/ranked_source_over_a_hash_join.gqt#3:query

```gq
set engine = v2;
query nearest_person_docs($q: Vector(2)) {
    match { $p: Person $p knows $d }
    return { $p.name, $d.slug }
    order { nearest($p.embedding, $q) }
    limit 2
}
```

```gq
gq 3;
set engine = v2;
query nearest_person_docs($q: Vector(2)) {
    match { from nearest({ match { $p: Person $p knows $d } return { $p, $d } }, $p.embedding, $q, k: 2) }
    return { $p.name, $d.slug }
    order { $distance }
    limit 2
}
```

### crates/omnigraph-gqt/cases/v2/planner/search_order_on_an_unranked_binding_and_a_destination.gqt#9:query

```gq
set engine = v2;
query search_only_order_with_an_unranked_binding($q: Vector(4)) {
    match { $c: Doc $z: Zone }
    return { $c.slug, $z.name }
    order { nearest($c.embedding, $q) }
    limit 3
}
```

```gq
gq 3;
set engine = v2;
query search_only_order_with_an_unranked_binding($q: Vector(4)) {
    match { from nearest({ match { $c: Doc $z: Zone } return { $c, $z } }, $c.embedding, $q, k: 3) }
    return { $c.slug, $z.name }
    order { $distance }
    limit 3
}
```

### crates/omnigraph-gqt/cases/v2/planner/search_order_on_an_unranked_binding_and_a_destination.gqt#14:query

```gq
set engine = v2;
query nearest_on_a_traversal_destination($q: Vector(4)) {
    match { $p: Person $p knows $d }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 1
}
```

```gq
gq 3;
set engine = v2;
query nearest_on_a_traversal_destination($q: Vector(4)) {
    match { from nearest({ match { $p: Person $p knows $d } return { $p, $d } }, $d.embedding, $q, k: 1) }
    return { $d.slug }
    order { $distance }
    limit 1
}
```

### crates/omnigraph-gqt/cases/v2/planner/search_scans_project_needed_columns_and_the_scoring_column.gqt#3:query

```gq
query nearest_slug($q: Vector(4)) {
    match { $d: Doc }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 10
}
```

```gq
gq 3;
query nearest_slug($q: Vector(4)) {
    match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 10) }
    return { $d.slug }
    order { $distance }
    limit 10
}
```

### crates/omnigraph-gqt/cases/v2/planner/search_scans_project_needed_columns_and_the_scoring_column.gqt#8:query

```gq
query bm25_slug($q: String) {
    match { $d: Doc }
    return { $d.slug }
    order { bm25($d.title, $q) }
    limit 10
}
```

```gq
gq 3;
query bm25_slug($q: String) {
    match { from bm25({ match { $d: Doc } return { $d } }, $d.title, $q) }
    return { $d.slug }
    order { $score desc }
    limit 10
}
```

### crates/omnigraph-gqt/cases/v2/planner/search_scans_project_needed_columns_and_the_scoring_column.gqt#13:query

```gq
query nearest_whole($q: Vector(4)) {
    match { $d: Doc }
    return { $d }
    order { nearest($d.embedding, $q) }
    limit 10
}
```

```gq
gq 3;
query nearest_whole($q: Vector(4)) {
    match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 10) }
    return { $d }
    order { $distance }
    limit 10
}
```

### crates/omnigraph-gqt/cases/v2/planner/traversal_destination_compound_filter.gqt#3:query

```gq
set engine = v2;
query adult_friends_named_a() {
    match {
        $p: Person { slug: "r" }
        $p knows $f
        $f.age > 30 and $f.name starts_with "a"
    }
    return { $f.slug }
}
```

```gq
gq 3;
set engine = v2;
query adult_friends_named_a() {
    match {
        $p: Person { slug: "r" }
        $p knows $f
        $f.age > 30 and starts_with($f.name, "a")
    }
    return { $f.slug }
}
```

### crates/omnigraph-gqt/cases/v2/search_filter_under_bm25_order.gqt#3:query

```gq
set engine = v2;
query filtered_rank($t: String, $q: String) {
    match {
        $d: Doc
        search($d.title, $t)
    }
    return { $d.slug }
    order { bm25($d.body, $q) }
    limit 5
}
```

```gq
gq 3;
set engine = v2;
query filtered_rank($t: String, $q: String) {
    match { from bm25({ match {
        $d: Doc
        search($d.title, $t)
    } return { $d } }, $d.body, $q) }
    return { $d.slug }
    order { $score desc }
    limit 5
}
```

### crates/omnigraph-gqt/cases/write_max_bytes.gqt#21:mutate

```gq
set write_max_bytes = 4093;
query carry_payload() {
    update Doc set { note: "updated" } where slug = "accepted"
}
```

```gq
gq 3;
set write_max_bytes = 4093;
query carry_payload() {
    $x: Doc  $x.slug = "accepted"
    update $x set { note: "updated" }
}
```

### crates/omnigraph-gqt/cases/write_max_bytes.gqt#23:mutate

```gq
set write_max_bytes = 1;
query removed_ids() {
    delete Doc where slug = "accepted"
}
```

```gq
gq 3;
set write_max_bytes = 1;
query removed_ids() {
    $x: Doc  $x.slug = "accepted"
    delete $x
}
```

### crates/omnigraph-gqt/cases/write_max_bytes_merge_pointer_adoption.gqt#7:mutate

```gq
query lengthen() {
    update Doc set { note: "long" } where slug = "w"
}
```

```gq
gq 3;
query lengthen() {
    $x: Doc  $x.slug = "w"
    update $x set { note: "long" }
}
```

### crates/omnigraph-gqt/cases/write_max_bytes_removed_id_charge.gqt#3:mutate

```gq
set write_max_bytes = 26;
query delete_multibyte_under() {
    delete Doc where slug = "日"
}
```

```gq
gq 3;
set write_max_bytes = 26;
query delete_multibyte_under() {
    $x: Doc  $x.slug = "日"
    delete $x
}
```

### crates/omnigraph-gqt/cases/write_max_bytes_removed_id_charge.gqt#5:mutate

```gq
set write_max_bytes = 27;
query delete_multibyte_exact() {
    delete Doc where slug = "日"
}
```

```gq
gq 3;
set write_max_bytes = 27;
query delete_multibyte_exact() {
    $x: Doc  $x.slug = "日"
    delete $x
}
```

### crates/omnigraph-gqt/cases/write_max_bytes_removed_id_charge.gqt#7:mutate

```gq
set write_max_bytes = 49;
query delete_pair_under() {
    delete Doc where grp = "pair"
}
```

```gq
gq 3;
set write_max_bytes = 49;
query delete_pair_under() {
    $x: Doc  $x.grp = "pair"
    delete $x
}
```

### crates/omnigraph-gqt/cases/write_max_bytes_removed_id_charge.gqt#9:mutate

```gq
set write_max_bytes = 50;
query delete_pair_exact() {
    delete Doc where grp = "pair"
}
```

```gq
gq 3;
set write_max_bytes = 50;
query delete_pair_exact() {
    $x: Doc  $x.grp = "pair"
    delete $x
}
```

### crates/omnigraph-gqt/cases/write_max_bytes_removed_id_charge.gqt#11:mutate

```gq
set write_max_bytes = 74;
query delete_hub_under() {
    delete Doc where slug = "x"
}
```

```gq
gq 3;
set write_max_bytes = 74;
query delete_hub_under() {
    $x: Doc  $x.slug = "x"
    delete $x
}
```

### crates/omnigraph-gqt/cases/write_max_bytes_removed_id_charge.gqt#19:mutate

```gq
set write_max_bytes = 75;
query delete_hub_exact() {
    delete Doc where slug = "x"
}
```

```gq
gq 3;
set write_max_bytes = 75;
query delete_hub_exact() {
    $x: Doc  $x.slug = "x"
    delete $x
}
```

### crates/omnigraph-gqt/cases/write_max_bytes_update_assignment_aggregate_name.gqt#5:mutate

```gq
set write_max_bytes = 30000;
query assign_both_rows($payload: Blob) {
    update Doc set { content: $payload } where note = "n"
}
```

```gq
gq 3;
set write_max_bytes = 30000;
query assign_both_rows($payload: Blob) {
    $x: Doc  $x.note = "n"
    update $x set { content: $payload }
}
```

### crates/omnigraph-gqt/cases/write_max_bytes_update_carried_blob.gqt#13:mutate

```gq
set write_max_bytes = 2047;
query retitle_alone() {
    update Doc set { title: "patched" } where slug = "big"
}
```

```gq
gq 3;
set write_max_bytes = 2047;
query retitle_alone() {
    $x: Doc  $x.slug = "big"
    update $x set { title: "patched" }
}
```

### crates/omnigraph-gqt/cases/write_max_bytes_update_carried_blob.gqt#20:mutate

```gq
set write_max_bytes = 2047;
query clear_and_retitle($c: Blob?) {
    update Doc set { content: $c, title: "cleared" } where slug = "big"
}
```

```gq
gq 3;
set write_max_bytes = 2047;
query clear_and_retitle($c: Blob?) {
    $x: Doc  $x.slug = "big"
    update $x set { content: $c, title: "cleared" }
}
```

### crates/omnigraph-gqt/cases/write_max_bytes_update_carried_blob.gqt#26:mutate

```gq
set write_max_bytes = 2047;
query retitle_indexed_alone() {
    update Indexed set { title: "patched" } where slug = "big"
}
```

```gq
gq 3;
set write_max_bytes = 2047;
query retitle_indexed_alone() {
    $x: Indexed  $x.slug = "big"
    update $x set { title: "patched" }
}
```

### crates/omnigraph-gqt/cases/write_max_bytes_update_carried_blob.gqt#28:mutate

```gq
set write_max_bytes = 8191;
query retitle_packed_alone() {
    update Packed set { title: "tttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttt" } where slug = "big"
}
```

```gq
gq 3;
set write_max_bytes = 8191;
query retitle_packed_alone() {
    $x: Packed  $x.slug = "big"
    update $x set { title: "tttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttt" }
}
```

### crates/omnigraph-gqt/cases/write_max_bytes_update_carried_blob.gqt#38:mutate

```gq
set write_max_bytes = 4096;
query retitle_shared_fragment() {
    update Shared set { title: "patched" } where slug = "big"
}
```

```gq
gq 3;
set write_max_bytes = 4096;
query retitle_shared_fragment() {
    $x: Shared  $x.slug = "big"
    update $x set { title: "patched" }
}
```

### crates/omnigraph-gqt/cases/write_max_bytes_update_carried_blob.gqt#40:mutate

```gq
set write_max_bytes = 4096;
query retitle_apart_fragment() {
    update Apart set { title: "patched" } where slug = "big"
}
```

```gq
gq 3;
set write_max_bytes = 4096;
query retitle_apart_fragment() {
    $x: Apart  $x.slug = "big"
    update $x set { title: "patched" }
}
```

### crates/omnigraph-gqt/cases/write_max_bytes_update_oversize_value_no_match.gqt#5:mutate

```gq
set write_max_bytes = 4093;
query assign_missing($payload: Blob) {
    update Doc set { content: $payload } where slug = "missing"
}
```

```gq
gq 3;
set write_max_bytes = 4093;
query assign_missing($payload: Blob) {
    $x: Doc  $x.slug = "missing"
    update $x set { content: $payload }
}
```

### benchmarks/fixtures/branch_merge_d50.gqt#9:mutate

```gq
query delete_src_0($c: String) { delete BenchE000 where cohort = $c }
```

```gq
gq 3;
query delete_src_0($c: String) { $x: BenchE000  $x.cohort = $c  delete $x }
```

### benchmarks/fixtures/branch_merge_d50.gqt#14:mutate

```gq
query delete_src_1($c: String) { delete BenchE001 where cohort = $c }
```

```gq
gq 3;
query delete_src_1($c: String) { $x: BenchE001  $x.cohort = $c  delete $x }
```

### benchmarks/fixtures/branch_merge_d50.gqt#19:mutate

```gq
query delete_src_2($c: String) { delete BenchE002 where cohort = $c }
```

```gq
gq 3;
query delete_src_2($c: String) { $x: BenchE002  $x.cohort = $c  delete $x }
```

### benchmarks/fixtures/branch_merge_d50.gqt#24:mutate

```gq
query delete_src_3($c: String) { delete BenchE003 where cohort = $c }
```

```gq
gq 3;
query delete_src_3($c: String) { $x: BenchE003  $x.cohort = $c  delete $x }
```

### benchmarks/fixtures/branch_merge_d50.gqt#37:mutate

```gq
query delete_tgt_0($c: String) { delete BenchE000 where cohort = $c }
```

```gq
gq 3;
query delete_tgt_0($c: String) { $x: BenchE000  $x.cohort = $c  delete $x }
```

### benchmarks/fixtures/branch_merge_d50.gqt#42:mutate

```gq
query delete_tgt_1($c: String) { delete BenchE001 where cohort = $c }
```

```gq
gq 3;
query delete_tgt_1($c: String) { $x: BenchE001  $x.cohort = $c  delete $x }
```

### benchmarks/fixtures/branch_merge_d50.gqt#47:mutate

```gq
query delete_tgt_2($c: String) { delete BenchE002 where cohort = $c }
```

```gq
gq 3;
query delete_tgt_2($c: String) { $x: BenchE002  $x.cohort = $c  delete $x }
```

### benchmarks/fixtures/branch_merge_d50.gqt#52:mutate

```gq
query delete_tgt_3($c: String) { delete BenchE003 where cohort = $c }
```

```gq
gq 3;
query delete_tgt_3($c: String) { $x: BenchE003  $x.cohort = $c  delete $x }
```

### benchmarks/fixtures/branch_merge_d50_history64.gqt#4:mutate

```gq
query age_set_one() { update BenchN000 set { val: 1 } where name = "n000_r0000000" }
```

```gq
gq 3;
query age_set_one() { $x: BenchN000  $x.name = "n000_r0000000"  update $x set { val: 1 } }
```

### benchmarks/fixtures/branch_merge_d50_history64.gqt#6:mutate

```gq
query age_restore_zero() { update BenchN000 set { val: 0 } where name = "n000_r0000000" }
```

```gq
gq 3;
query age_restore_zero() { $x: BenchN000  $x.name = "n000_r0000000"  update $x set { val: 0 } }
```

### benchmarks/fixtures/branch_merge_d50_history64.gqt#15:mutate

```gq
query delete_src_0($c: String) { delete BenchE000 where cohort = $c }
```

```gq
gq 3;
query delete_src_0($c: String) { $x: BenchE000  $x.cohort = $c  delete $x }
```

### benchmarks/fixtures/branch_merge_d50_history64.gqt#20:mutate

```gq
query delete_src_1($c: String) { delete BenchE001 where cohort = $c }
```

```gq
gq 3;
query delete_src_1($c: String) { $x: BenchE001  $x.cohort = $c  delete $x }
```

### benchmarks/fixtures/branch_merge_d50_history64.gqt#25:mutate

```gq
query delete_src_2($c: String) { delete BenchE002 where cohort = $c }
```

```gq
gq 3;
query delete_src_2($c: String) { $x: BenchE002  $x.cohort = $c  delete $x }
```

### benchmarks/fixtures/branch_merge_d50_history64.gqt#30:mutate

```gq
query delete_src_3($c: String) { delete BenchE003 where cohort = $c }
```

```gq
gq 3;
query delete_src_3($c: String) { $x: BenchE003  $x.cohort = $c  delete $x }
```

### benchmarks/fixtures/branch_merge_d50_history64.gqt#43:mutate

```gq
query delete_tgt_0($c: String) { delete BenchE000 where cohort = $c }
```

```gq
gq 3;
query delete_tgt_0($c: String) { $x: BenchE000  $x.cohort = $c  delete $x }
```

### benchmarks/fixtures/branch_merge_d50_history64.gqt#48:mutate

```gq
query delete_tgt_1($c: String) { delete BenchE001 where cohort = $c }
```

```gq
gq 3;
query delete_tgt_1($c: String) { $x: BenchE001  $x.cohort = $c  delete $x }
```

### benchmarks/fixtures/branch_merge_d50_history64.gqt#53:mutate

```gq
query delete_tgt_2($c: String) { delete BenchE002 where cohort = $c }
```

```gq
gq 3;
query delete_tgt_2($c: String) { $x: BenchE002  $x.cohort = $c  delete $x }
```

### benchmarks/fixtures/branch_merge_d50_history64.gqt#58:mutate

```gq
query delete_tgt_3($c: String) { delete BenchE003 where cohort = $c }
```

```gq
gq 3;
query delete_tgt_3($c: String) { $x: BenchE003  $x.cohort = $c  delete $x }
```

### benchmarks/fixtures/e2e_nearest_nprobes_one.gqt#3:query

```gq
set ann_nprobes = 1;
query nearest_matching_rows($q: Vector(4)) {
    match { $d: Doc $d.n > 3 }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 3
}
```

```gq
gq 3;
set ann_nprobes = 1;
query nearest_matching_rows($q: Vector(4)) {
    match { from nearest({ match { $d: Doc $d.n > 3 } return { $d } }, $d.embedding, $q, k: 3) }
    return { $d.slug }
    order { $distance }
    limit 3
}
```

### benchmarks/fixtures/e2e_nearest_prefilter.gqt#3:query

```gq
query filtered_nearest($q: Vector(4)) {
    match { $d: Doc { status: "hit" } }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 3
}
```

```gq
gq 3;
query filtered_nearest($q: Vector(4)) {
    match { from nearest({ match { $d: Doc { status: "hit" } } return { $d } }, $d.embedding, $q, k: 3) }
    return { $d.slug }
    order { $distance }
    limit 3
}
```

### benchmarks/fixtures/e2e_rrf_traversal.gqt#3:query

```gq
query recall_two_terms($q1: String, $q2: String) {
    match {
        $c: Chunk
        $a: Artifact
        $c chunkOfArtifact $a
        search($c.text, $q1)
    }
    return { $c.slug }
    order { rrf(bm25($c.text, $q1), bm25($c.text, $q2)) }
    limit 1
}
```

```gq
gq 3;
query recall_two_terms($q1: String, $q2: String) {
    match { let $p = { match {
        $c: Chunk
        $a: Artifact
        $c chunkOfArtifact $a
        search($c.text, $q1)
    } return { $c, $a } }  from rrf(bm25($p, $c.text, $q1), bm25($p, $c.text, $q2), k: 60) }
    return { $c.slug }
    order { $score desc }
    limit 1
}
```

### benchmarks/fixtures/finbench/finbench_disjoint.gqt#1:query

```gq
query reserved_accounts() {
    match { $a: Account
        $a.accountId starts_with "omnigraph-bench-fin-v1-" }
    return { $a.accountId as account, $a.createTime as created, $a.isBlocked as blocked, $a.accountType as kind, $a.freqLoginType as login, $a.accountLevel as level, $a.nickname as nickname, $a.phonenum as phonenum, $a.email as email, $a.lastLoginTime as last_login }
}
```

```gq
gq 3;
query reserved_accounts() {
    match { $a: Account
        starts_with($a.accountId, "omnigraph-bench-fin-v1-") }
    return { $a.accountId as account, $a.createTime as created, $a.isBlocked as blocked, $a.accountType as kind, $a.freqLoginType as login, $a.accountLevel as level, $a.nickname as nickname, $a.phonenum as phonenum, $a.email as email, $a.lastLoginTime as last_login }
}
```

### benchmarks/fixtures/finbench/finbench_disjoint.gqt#4:query

```gq
query reserved_transfers() {
    match { $a: Account
        $a $e:accounttransferaccount $b
        $e.orderNum starts_with "omnigraph-bench-fin-v1-" }
    return { $e.@src as src, $e.@dst as dst, $e.amount as amount, $e.createTime as created, $e.orderNum as order_num, $e.payType as pay, $e.goodsType as goods, $e.comment as comment }
}
```

```gq
gq 3;
query reserved_transfers() {
    match { $a: Account
        $a $e:accounttransferaccount $b
        starts_with($e.orderNum, "omnigraph-bench-fin-v1-") }
    return { $e.@src as src, $e.@dst as dst, $e.amount as amount, $e.createTime as created, $e.orderNum as order_num, $e.payType as pay, $e.goodsType as goods, $e.comment as comment }
}
```

### benchmarks/fixtures/finbench/finbench_disjoint.gqt#11:query

```gq
query reserved_accounts() {
    match { $a: Account
        $a.accountId starts_with "omnigraph-bench-fin-v1-" }
    return { $a.accountId as account, $a.createTime as created, $a.isBlocked as blocked, $a.accountType as kind, $a.freqLoginType as login, $a.accountLevel as level, $a.nickname as nickname, $a.phonenum as phonenum, $a.email as email, $a.lastLoginTime as last_login }
}
```

```gq
gq 3;
query reserved_accounts() {
    match { $a: Account
        starts_with($a.accountId, "omnigraph-bench-fin-v1-") }
    return { $a.accountId as account, $a.createTime as created, $a.isBlocked as blocked, $a.accountType as kind, $a.freqLoginType as login, $a.accountLevel as level, $a.nickname as nickname, $a.phonenum as phonenum, $a.email as email, $a.lastLoginTime as last_login }
}
```

### benchmarks/fixtures/finbench/finbench_disjoint.gqt#14:query

```gq
query reserved_transfers() {
    match { $a: Account
        $a $e:accounttransferaccount $b
        $e.orderNum starts_with "omnigraph-bench-fin-v1-" }
    return { $e.@src as src, $e.@dst as dst, $e.amount as amount, $e.createTime as created, $e.orderNum as order_num, $e.payType as pay, $e.goodsType as goods, $e.comment as comment }
}
```

```gq
gq 3;
query reserved_transfers() {
    match { $a: Account
        $a $e:accounttransferaccount $b
        starts_with($e.orderNum, "omnigraph-bench-fin-v1-") }
    return { $e.@src as src, $e.@dst as dst, $e.amount as amount, $e.createTime as created, $e.orderNum as order_num, $e.payType as pay, $e.goodsType as goods, $e.comment as comment }
}
```

### benchmarks/fixtures/finbench/finbench_disjoint.gqt#27:query

```gq
query reserved_accounts() {
    match { $a: Account
        $a.accountId starts_with "omnigraph-bench-fin-v1-" }
    return { $a.accountId as account, $a.createTime as created, $a.isBlocked as blocked, $a.accountType as kind, $a.freqLoginType as login, $a.accountLevel as level, $a.nickname as nickname, $a.phonenum as phonenum, $a.email as email, $a.lastLoginTime as last_login }
}
```

```gq
gq 3;
query reserved_accounts() {
    match { $a: Account
        starts_with($a.accountId, "omnigraph-bench-fin-v1-") }
    return { $a.accountId as account, $a.createTime as created, $a.isBlocked as blocked, $a.accountType as kind, $a.freqLoginType as login, $a.accountLevel as level, $a.nickname as nickname, $a.phonenum as phonenum, $a.email as email, $a.lastLoginTime as last_login }
}
```

### benchmarks/fixtures/finbench/finbench_disjoint.gqt#30:query

```gq
query reserved_transfers() {
    match { $a: Account
        $a $e:accounttransferaccount $b
        $e.orderNum starts_with "omnigraph-bench-fin-v1-" }
    return { $e.@src as src, $e.@dst as dst, $e.amount as amount, $e.createTime as created, $e.orderNum as order_num, $e.payType as pay, $e.goodsType as goods, $e.comment as comment }
}
```

```gq
gq 3;
query reserved_transfers() {
    match { $a: Account
        $a $e:accounttransferaccount $b
        starts_with($e.orderNum, "omnigraph-bench-fin-v1-") }
    return { $e.@src as src, $e.@dst as dst, $e.amount as amount, $e.createTime as created, $e.orderNum as order_num, $e.payType as pay, $e.goodsType as goods, $e.comment as comment }
}
```

### benchmarks/fixtures/finbench/finbench_disjoint.gqt#39:query

```gq
query reserved_accounts() {
    match { $a: Account
        $a.accountId starts_with "omnigraph-bench-fin-v1-" }
    return { $a.accountId as account, $a.createTime as created, $a.isBlocked as blocked, $a.accountType as kind, $a.freqLoginType as login, $a.accountLevel as level, $a.nickname as nickname, $a.phonenum as phonenum, $a.email as email, $a.lastLoginTime as last_login }
}
```

```gq
gq 3;
query reserved_accounts() {
    match { $a: Account
        starts_with($a.accountId, "omnigraph-bench-fin-v1-") }
    return { $a.accountId as account, $a.createTime as created, $a.isBlocked as blocked, $a.accountType as kind, $a.freqLoginType as login, $a.accountLevel as level, $a.nickname as nickname, $a.phonenum as phonenum, $a.email as email, $a.lastLoginTime as last_login }
}
```

### benchmarks/fixtures/finbench/finbench_disjoint.gqt#42:query

```gq
query reserved_transfers() {
    match { $a: Account
        $a $e:accounttransferaccount $b
        $e.orderNum starts_with "omnigraph-bench-fin-v1-" }
    return { $e.@src as src, $e.@dst as dst, $e.amount as amount, $e.createTime as created, $e.orderNum as order_num, $e.payType as pay, $e.goodsType as goods, $e.comment as comment }
}
```

```gq
gq 3;
query reserved_transfers() {
    match { $a: Account
        $a $e:accounttransferaccount $b
        starts_with($e.orderNum, "omnigraph-bench-fin-v1-") }
    return { $e.@src as src, $e.@dst as dst, $e.amount as amount, $e.createTime as created, $e.orderNum as order_num, $e.payType as pay, $e.goodsType as goods, $e.comment as comment }
}
```

### benchmarks/fixtures/hot_table_idle_branches_4_aged.gqt#12:mutate

```gq
query unrelated_one() { update Hot set { value: 1 } where key = "hot" }
```

```gq
gq 3;
query unrelated_one() { $x: Hot  $x.key = "hot"  update $x set { value: 1 } }
```

### benchmarks/fixtures/hot_table_idle_branches_4_aged.gqt#14:mutate

```gq
query unrelated_zero() { update Hot set { value: 0 } where key = "hot" }
```

```gq
gq 3;
query unrelated_zero() { $x: Hot  $x.key = "hot"  update $x set { value: 0 } }
```

### benchmarks/fixtures/nearest_ranks_updated_embedding.gqt#3:mutate

```gq
query move_embedding($s: String, $e: Vector(4)) {
    update Doc set { embedding: $e } where slug = $s
}
```

```gq
gq 3;
query move_embedding($s: String, $e: Vector(4)) {
    $x: Doc  $x.slug = $s
    update $x set { embedding: $e }
}
```

### benchmarks/fixtures/nearest_skips_deleted_rows_and_fills_limit.gqt#3:mutate

```gq
query delete_doc($s: String) {
    delete Doc where slug = $s
}
```

```gq
gq 3;
query delete_doc($s: String) {
    $x: Doc  $x.slug = $s
    delete $x
}
```

### benchmarks/fixtures/nearest_skips_deleted_rows_and_fills_limit.gqt#6:mutate

```gq
query delete_doc($s: String) {
    delete Doc where slug = $s
}
```

```gq
gq 3;
query delete_doc($s: String) {
    $x: Doc  $x.slug = $s
    delete $x
}
```

### benchmarks/fixtures/nearest_skips_deleted_rows_and_fills_limit.gqt#9:mutate

```gq
query delete_doc($s: String) {
    delete Doc where slug = $s
}
```

```gq
gq 3;
query delete_doc($s: String) {
    $x: Doc  $x.slug = $s
    delete $x
}
```

### benchmarks/fixtures/repeated_deletion_recreation_rows.gqt#10:mutate

```gq
query delete_temporary() { delete Item where key = "temporary" }
```

```gq
gq 3;
query delete_temporary() { $x: Item  $x.key = "temporary"  delete $x }
```

### benchmarks/fixtures/threshold_crossings_after.gqt#8:mutate

```gq
query bump($n: I32) { update Person set { age: $n } where name = "alice" }
```

```gq
gq 3;
query bump($n: I32) { $x: Person  $x.name = "alice"  update $x set { age: $n } }
```

### benchmarks/fixtures/threshold_crossings_at.gqt#8:mutate

```gq
query bump($n: I32) { update Person set { age: $n } where name = "alice" }
```

```gq
gq 3;
query bump($n: I32) { $x: Person  $x.name = "alice"  update $x set { age: $n } }
```

### benchmarks/fixtures/threshold_crossings_before.gqt#8:mutate

```gq
query bump($n: I32) { update Person set { age: $n } where name = "alice" }
```

```gq
gq 3;
query bump($n: I32) { $x: Person  $x.name = "alice"  update $x set { age: $n } }
```

### benchmarks/fixtures/threshold_crossings_production_control.gqt#8:mutate

```gq
query bump($n: I32) { update Person set { age: $n } where name = "alice" }
```

```gq
gq 3;
query bump($n: I32) { $x: Person  $x.name = "alice"  update $x set { age: $n } }
```

### benchmarks/fixtures/unattached_chain_32.gqt#3:mutate

```gq
query set_one() as unattached branch { update HistoryRow set { value: 1 } where key = "hot" }
```

```gq
gq 3;
query set_one() as unattached branch { $x: HistoryRow  $x.key = "hot"  update $x set { value: 1 } }
```

### benchmarks/fixtures/unattached_chain_32.gqt#6:mutate

```gq
query restore_zero() as unattached branch on "${c00}" { update HistoryRow set { value: 0 } where key = "hot" }
```

```gq
gq 3;
query restore_zero() as unattached branch on "${c00}" { $x: HistoryRow  $x.key = "hot"  update $x set { value: 0 } }
```

### benchmarks/fixtures/unattached_chain_32.gqt#9:mutate

```gq
query set_one() as unattached branch on "${c01}" { update HistoryRow set { value: 1 } where key = "hot" }
```

```gq
gq 3;
query set_one() as unattached branch on "${c01}" { $x: HistoryRow  $x.key = "hot"  update $x set { value: 1 } }
```

### benchmarks/fixtures/unattached_chain_32.gqt#12:mutate

```gq
query restore_zero() as unattached branch on "${c02}" { update HistoryRow set { value: 0 } where key = "hot" }
```

```gq
gq 3;
query restore_zero() as unattached branch on "${c02}" { $x: HistoryRow  $x.key = "hot"  update $x set { value: 0 } }
```

### benchmarks/fixtures/unattached_chain_32.gqt#15:mutate

```gq
query set_one() as unattached branch on "${c03}" { update HistoryRow set { value: 1 } where key = "hot" }
```

```gq
gq 3;
query set_one() as unattached branch on "${c03}" { $x: HistoryRow  $x.key = "hot"  update $x set { value: 1 } }
```

### benchmarks/fixtures/unattached_chain_32.gqt#18:mutate

```gq
query restore_zero() as unattached branch on "${c04}" { update HistoryRow set { value: 0 } where key = "hot" }
```

```gq
gq 3;
query restore_zero() as unattached branch on "${c04}" { $x: HistoryRow  $x.key = "hot"  update $x set { value: 0 } }
```

### benchmarks/fixtures/unattached_chain_32.gqt#21:mutate

```gq
query set_one() as unattached branch on "${c05}" { update HistoryRow set { value: 1 } where key = "hot" }
```

```gq
gq 3;
query set_one() as unattached branch on "${c05}" { $x: HistoryRow  $x.key = "hot"  update $x set { value: 1 } }
```

### benchmarks/fixtures/unattached_chain_32.gqt#24:mutate

```gq
query restore_zero() as unattached branch on "${c06}" { update HistoryRow set { value: 0 } where key = "hot" }
```

```gq
gq 3;
query restore_zero() as unattached branch on "${c06}" { $x: HistoryRow  $x.key = "hot"  update $x set { value: 0 } }
```

### benchmarks/fixtures/unattached_chain_32.gqt#27:mutate

```gq
query set_one() as unattached branch on "${c07}" { update HistoryRow set { value: 1 } where key = "hot" }
```

```gq
gq 3;
query set_one() as unattached branch on "${c07}" { $x: HistoryRow  $x.key = "hot"  update $x set { value: 1 } }
```

### benchmarks/fixtures/unattached_chain_32.gqt#30:mutate

```gq
query restore_zero() as unattached branch on "${c08}" { update HistoryRow set { value: 0 } where key = "hot" }
```

```gq
gq 3;
query restore_zero() as unattached branch on "${c08}" { $x: HistoryRow  $x.key = "hot"  update $x set { value: 0 } }
```

### benchmarks/fixtures/unattached_chain_32.gqt#33:mutate

```gq
query set_one() as unattached branch on "${c09}" { update HistoryRow set { value: 1 } where key = "hot" }
```

```gq
gq 3;
query set_one() as unattached branch on "${c09}" { $x: HistoryRow  $x.key = "hot"  update $x set { value: 1 } }
```

### benchmarks/fixtures/unattached_chain_32.gqt#36:mutate

```gq
query restore_zero() as unattached branch on "${c10}" { update HistoryRow set { value: 0 } where key = "hot" }
```

```gq
gq 3;
query restore_zero() as unattached branch on "${c10}" { $x: HistoryRow  $x.key = "hot"  update $x set { value: 0 } }
```

### benchmarks/fixtures/unattached_chain_32.gqt#39:mutate

```gq
query set_one() as unattached branch on "${c11}" { update HistoryRow set { value: 1 } where key = "hot" }
```

```gq
gq 3;
query set_one() as unattached branch on "${c11}" { $x: HistoryRow  $x.key = "hot"  update $x set { value: 1 } }
```

### benchmarks/fixtures/unattached_chain_32.gqt#42:mutate

```gq
query restore_zero() as unattached branch on "${c12}" { update HistoryRow set { value: 0 } where key = "hot" }
```

```gq
gq 3;
query restore_zero() as unattached branch on "${c12}" { $x: HistoryRow  $x.key = "hot"  update $x set { value: 0 } }
```

### benchmarks/fixtures/unattached_chain_32.gqt#45:mutate

```gq
query set_one() as unattached branch on "${c13}" { update HistoryRow set { value: 1 } where key = "hot" }
```

```gq
gq 3;
query set_one() as unattached branch on "${c13}" { $x: HistoryRow  $x.key = "hot"  update $x set { value: 1 } }
```

### benchmarks/fixtures/unattached_chain_32.gqt#48:mutate

```gq
query restore_zero() as unattached branch on "${c14}" { update HistoryRow set { value: 0 } where key = "hot" }
```

```gq
gq 3;
query restore_zero() as unattached branch on "${c14}" { $x: HistoryRow  $x.key = "hot"  update $x set { value: 0 } }
```

### benchmarks/fixtures/unattached_chain_32.gqt#51:mutate

```gq
query set_one() as unattached branch on "${c15}" { update HistoryRow set { value: 1 } where key = "hot" }
```

```gq
gq 3;
query set_one() as unattached branch on "${c15}" { $x: HistoryRow  $x.key = "hot"  update $x set { value: 1 } }
```

### benchmarks/fixtures/unattached_chain_32.gqt#54:mutate

```gq
query restore_zero() as unattached branch on "${c16}" { update HistoryRow set { value: 0 } where key = "hot" }
```

```gq
gq 3;
query restore_zero() as unattached branch on "${c16}" { $x: HistoryRow  $x.key = "hot"  update $x set { value: 0 } }
```

### benchmarks/fixtures/unattached_chain_32.gqt#57:mutate

```gq
query set_one() as unattached branch on "${c17}" { update HistoryRow set { value: 1 } where key = "hot" }
```

```gq
gq 3;
query set_one() as unattached branch on "${c17}" { $x: HistoryRow  $x.key = "hot"  update $x set { value: 1 } }
```

### benchmarks/fixtures/unattached_chain_32.gqt#60:mutate

```gq
query restore_zero() as unattached branch on "${c18}" { update HistoryRow set { value: 0 } where key = "hot" }
```

```gq
gq 3;
query restore_zero() as unattached branch on "${c18}" { $x: HistoryRow  $x.key = "hot"  update $x set { value: 0 } }
```

### benchmarks/fixtures/unattached_chain_32.gqt#63:mutate

```gq
query set_one() as unattached branch on "${c19}" { update HistoryRow set { value: 1 } where key = "hot" }
```

```gq
gq 3;
query set_one() as unattached branch on "${c19}" { $x: HistoryRow  $x.key = "hot"  update $x set { value: 1 } }
```

### benchmarks/fixtures/unattached_chain_32.gqt#66:mutate

```gq
query restore_zero() as unattached branch on "${c20}" { update HistoryRow set { value: 0 } where key = "hot" }
```

```gq
gq 3;
query restore_zero() as unattached branch on "${c20}" { $x: HistoryRow  $x.key = "hot"  update $x set { value: 0 } }
```

### benchmarks/fixtures/unattached_chain_32.gqt#69:mutate

```gq
query set_one() as unattached branch on "${c21}" { update HistoryRow set { value: 1 } where key = "hot" }
```

```gq
gq 3;
query set_one() as unattached branch on "${c21}" { $x: HistoryRow  $x.key = "hot"  update $x set { value: 1 } }
```

### benchmarks/fixtures/unattached_chain_32.gqt#72:mutate

```gq
query restore_zero() as unattached branch on "${c22}" { update HistoryRow set { value: 0 } where key = "hot" }
```

```gq
gq 3;
query restore_zero() as unattached branch on "${c22}" { $x: HistoryRow  $x.key = "hot"  update $x set { value: 0 } }
```

### benchmarks/fixtures/unattached_chain_32.gqt#75:mutate

```gq
query set_one() as unattached branch on "${c23}" { update HistoryRow set { value: 1 } where key = "hot" }
```

```gq
gq 3;
query set_one() as unattached branch on "${c23}" { $x: HistoryRow  $x.key = "hot"  update $x set { value: 1 } }
```

### benchmarks/fixtures/unattached_chain_32.gqt#78:mutate

```gq
query restore_zero() as unattached branch on "${c24}" { update HistoryRow set { value: 0 } where key = "hot" }
```

```gq
gq 3;
query restore_zero() as unattached branch on "${c24}" { $x: HistoryRow  $x.key = "hot"  update $x set { value: 0 } }
```

### benchmarks/fixtures/unattached_chain_32.gqt#81:mutate

```gq
query set_one() as unattached branch on "${c25}" { update HistoryRow set { value: 1 } where key = "hot" }
```

```gq
gq 3;
query set_one() as unattached branch on "${c25}" { $x: HistoryRow  $x.key = "hot"  update $x set { value: 1 } }
```

### benchmarks/fixtures/unattached_chain_32.gqt#84:mutate

```gq
query restore_zero() as unattached branch on "${c26}" { update HistoryRow set { value: 0 } where key = "hot" }
```

```gq
gq 3;
query restore_zero() as unattached branch on "${c26}" { $x: HistoryRow  $x.key = "hot"  update $x set { value: 0 } }
```

### benchmarks/fixtures/unattached_chain_32.gqt#87:mutate

```gq
query set_one() as unattached branch on "${c27}" { update HistoryRow set { value: 1 } where key = "hot" }
```

```gq
gq 3;
query set_one() as unattached branch on "${c27}" { $x: HistoryRow  $x.key = "hot"  update $x set { value: 1 } }
```

### benchmarks/fixtures/unattached_chain_32.gqt#90:mutate

```gq
query restore_zero() as unattached branch on "${c28}" { update HistoryRow set { value: 0 } where key = "hot" }
```

```gq
gq 3;
query restore_zero() as unattached branch on "${c28}" { $x: HistoryRow  $x.key = "hot"  update $x set { value: 0 } }
```

### benchmarks/fixtures/unattached_chain_32.gqt#93:mutate

```gq
query set_one() as unattached branch on "${c29}" { update HistoryRow set { value: 1 } where key = "hot" }
```

```gq
gq 3;
query set_one() as unattached branch on "${c29}" { $x: HistoryRow  $x.key = "hot"  update $x set { value: 1 } }
```

### benchmarks/fixtures/unattached_chain_32.gqt#96:mutate

```gq
query restore_zero() as unattached branch on "${c30}" { update HistoryRow set { value: 0 } where key = "hot" }
```

```gq
gq 3;
query restore_zero() as unattached branch on "${c30}" { $x: HistoryRow  $x.key = "hot"  update $x set { value: 0 } }
```

### benchmarks/fixtures/very_long_history_1000.gqt#4:mutate

```gq
query set_one() { update HistoryRow set { value: 1 } where key = "hot" }
```

```gq
gq 3;
query set_one() { $x: HistoryRow  $x.key = "hot"  update $x set { value: 1 } }
```

### benchmarks/fixtures/very_long_history_1000.gqt#6:mutate

```gq
query restore_zero() { update HistoryRow set { value: 0 } where key = "hot" }
```

```gq
gq 3;
query restore_zero() { $x: HistoryRow  $x.key = "hot"  update $x set { value: 0 } }
```

### benchmarks/fixtures/very_long_history_10000.gqt#4:mutate

```gq
query set_one() { update HistoryRow set { value: 1 } where key = "hot" }
```

```gq
gq 3;
query set_one() { $x: HistoryRow  $x.key = "hot"  update $x set { value: 1 } }
```

### benchmarks/fixtures/very_long_history_10000.gqt#6:mutate

```gq
query restore_zero() { update HistoryRow set { value: 0 } where key = "hot" }
```

```gq
gq 3;
query restore_zero() { $x: HistoryRow  $x.key = "hot"  update $x set { value: 0 } }
```

### benchmarks/fixtures/very_long_history_100000.gqt#4:mutate

```gq
query set_one() { update HistoryRow set { value: 1 } where key = "hot" }
```

```gq
gq 3;
query set_one() { $x: HistoryRow  $x.key = "hot"  update $x set { value: 1 } }
```

### benchmarks/fixtures/very_long_history_100000.gqt#6:mutate

```gq
query restore_zero() { update HistoryRow set { value: 0 } where key = "hot" }
```

```gq
gq 3;
query restore_zero() { $x: HistoryRow  $x.key = "hot"  update $x set { value: 0 } }
```

### benchmarks/fixtures/very_long_history_100000.gqt#10:mutate

```gq
query set_one() { update HistoryRow set { value: 1 } where key = "hot" }
```

```gq
gq 3;
query set_one() { $x: HistoryRow  $x.key = "hot"  update $x set { value: 1 } }
```

### benchmarks/fixtures/very_long_history_100000.gqt#12:mutate

```gq
query restore_zero() { update HistoryRow set { value: 0 } where key = "hot" }
```

```gq
gq 3;
query restore_zero() { $x: HistoryRow  $x.key = "hot"  update $x set { value: 0 } }
```

### benchmarks/fixtures/very_long_history_100000.gqt#16:mutate

```gq
query set_one() { update HistoryRow set { value: 1 } where key = "hot" }
```

```gq
gq 3;
query set_one() { $x: HistoryRow  $x.key = "hot"  update $x set { value: 1 } }
```

### benchmarks/fixtures/very_long_history_100000.gqt#18:mutate

```gq
query restore_zero() { update HistoryRow set { value: 0 } where key = "hot" }
```

```gq
gq 3;
query restore_zero() { $x: HistoryRow  $x.key = "hot"  update $x set { value: 0 } }
```

### benchmarks/fixtures/very_long_history_100000.gqt#22:mutate

```gq
query set_one() { update HistoryRow set { value: 1 } where key = "hot" }
```

```gq
gq 3;
query set_one() { $x: HistoryRow  $x.key = "hot"  update $x set { value: 1 } }
```

### benchmarks/fixtures/very_long_history_100000.gqt#24:mutate

```gq
query restore_zero() { update HistoryRow set { value: 0 } where key = "hot" }
```

```gq
gq 3;
query restore_zero() { $x: HistoryRow  $x.key = "hot"  update $x set { value: 0 } }
```

### benchmarks/fixtures/very_long_history_100000.gqt#28:mutate

```gq
query set_one() { update HistoryRow set { value: 1 } where key = "hot" }
```

```gq
gq 3;
query set_one() { $x: HistoryRow  $x.key = "hot"  update $x set { value: 1 } }
```

### benchmarks/fixtures/very_long_history_100000.gqt#30:mutate

```gq
query restore_zero() { update HistoryRow set { value: 0 } where key = "hot" }
```

```gq
gq 3;
query restore_zero() { $x: HistoryRow  $x.key = "hot"  update $x set { value: 0 } }
```

### benchmarks/fixtures/very_long_history_32.gqt#4:mutate

```gq
query set_one() { update HistoryRow set { value: 1 } where key = "hot" }
```

```gq
gq 3;
query set_one() { $x: HistoryRow  $x.key = "hot"  update $x set { value: 1 } }
```

### benchmarks/fixtures/very_long_history_32.gqt#6:mutate

```gq
query restore_zero() { update HistoryRow set { value: 0 } where key = "hot" }
```

```gq
gq 3;
query restore_zero() { $x: HistoryRow  $x.key = "hot"  update $x set { value: 0 } }
```

### benchmarks/workloads/e2e_nearest_nprobes_one.gqt#1:query

```gq
set ann_nprobes = 1;
query nearest_matching_rows($q: Vector(4)) {
    match { $d: Doc $d.n > 3 }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 3
}
```

```gq
gq 3;
set ann_nprobes = 1;
query nearest_matching_rows($q: Vector(4)) {
    match { from nearest({ match { $d: Doc $d.n > 3 } return { $d } }, $d.embedding, $q, k: 3) }
    return { $d.slug }
    order { $distance }
    limit 3
}
```

### benchmarks/workloads/e2e_nearest_nprobes_one.gqt#6:query

```gq
set ann_nprobes = 1;
query nearest_matching_rows($q: Vector(4)) {
    match { $d: Doc $d.n > 3 }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 3
}
```

```gq
gq 3;
set ann_nprobes = 1;
query nearest_matching_rows($q: Vector(4)) {
    match { from nearest({ match { $d: Doc $d.n > 3 } return { $d } }, $d.embedding, $q, k: 3) }
    return { $d.slug }
    order { $distance }
    limit 3
}
```

### benchmarks/workloads/e2e_nearest_prefilter.gqt#1:query

```gq
query filtered_nearest($q: Vector(4)) {
    match { $d: Doc { status: "hit" } }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 3
}
```

```gq
gq 3;
query filtered_nearest($q: Vector(4)) {
    match { from nearest({ match { $d: Doc { status: "hit" } } return { $d } }, $d.embedding, $q, k: 3) }
    return { $d.slug }
    order { $distance }
    limit 3
}
```

### benchmarks/workloads/e2e_nearest_prefilter.gqt#6:query

```gq
query filtered_nearest($q: Vector(4)) {
    match { $d: Doc { status: "hit" } }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 3
}
```

```gq
gq 3;
query filtered_nearest($q: Vector(4)) {
    match { from nearest({ match { $d: Doc { status: "hit" } } return { $d } }, $d.embedding, $q, k: 3) }
    return { $d.slug }
    order { $distance }
    limit 3
}
```

### benchmarks/workloads/e2e_rrf_traversal.gqt#1:query

```gq
query recall_two_terms($q1: String, $q2: String) {
    match {
        $c: Chunk
        $a: Artifact
        $c chunkOfArtifact $a
        search($c.text, $q1)
    }
    return { $c.slug }
    order { rrf(bm25($c.text, $q1), bm25($c.text, $q2)) }
    limit 1
}
```

```gq
gq 3;
query recall_two_terms($q1: String, $q2: String) {
    match { let $p = { match {
        $c: Chunk
        $a: Artifact
        $c chunkOfArtifact $a
        search($c.text, $q1)
    } return { $c, $a } }  from rrf(bm25($p, $c.text, $q1), bm25($p, $c.text, $q2), k: 60) }
    return { $c.slug }
    order { $score desc }
    limit 1
}
```

### benchmarks/workloads/e2e_rrf_traversal.gqt#7:query

```gq
query recall_two_terms($q1: String, $q2: String) {
    match {
        $c: Chunk
        $a: Artifact
        $c chunkOfArtifact $a
        search($c.text, $q1)
    }
    return { $c.slug }
    order { rrf(bm25($c.text, $q1), bm25($c.text, $q2)) }
    limit 1
}
```

```gq
gq 3;
query recall_two_terms($q1: String, $q2: String) {
    match { let $p = { match {
        $c: Chunk
        $a: Artifact
        $c chunkOfArtifact $a
        search($c.text, $q1)
    } return { $c, $a } }  from rrf(bm25($p, $c.text, $q1), bm25($p, $c.text, $q2), k: 60) }
    return { $c.slug }
    order { $score desc }
    limit 1
}
```

### benchmarks/workloads/finbench_disjoint_merge.gqt#3:query

```gq
query reserved_accounts() {
    match { $a: Account
        $a.accountId starts_with "omnigraph-bench-fin-v1-" }
    return { $a.accountId as account, $a.createTime as created, $a.isBlocked as blocked, $a.accountType as kind, $a.freqLoginType as login, $a.accountLevel as level, $a.nickname as nickname, $a.phonenum as phonenum, $a.email as email, $a.lastLoginTime as last_login }
}
```

```gq
gq 3;
query reserved_accounts() {
    match { $a: Account
        starts_with($a.accountId, "omnigraph-bench-fin-v1-") }
    return { $a.accountId as account, $a.createTime as created, $a.isBlocked as blocked, $a.accountType as kind, $a.freqLoginType as login, $a.accountLevel as level, $a.nickname as nickname, $a.phonenum as phonenum, $a.email as email, $a.lastLoginTime as last_login }
}
```

### benchmarks/workloads/finbench_disjoint_merge.gqt#6:query

```gq
query reserved_transfers() {
    match { $a: Account
        $a $e:accounttransferaccount $b
        $e.orderNum starts_with "omnigraph-bench-fin-v1-" }
    return { $e.@src as src, $e.@dst as dst, $e.amount as amount, $e.createTime as created, $e.orderNum as order_num, $e.payType as pay, $e.goodsType as goods, $e.comment as comment }
}
```

```gq
gq 3;
query reserved_transfers() {
    match { $a: Account
        $a $e:accounttransferaccount $b
        starts_with($e.orderNum, "omnigraph-bench-fin-v1-") }
    return { $e.@src as src, $e.@dst as dst, $e.amount as amount, $e.createTime as created, $e.orderNum as order_num, $e.payType as pay, $e.goodsType as goods, $e.comment as comment }
}
```

### benchmarks/workloads/finbench_disjoint_merge.gqt#15:query

```gq
query reserved_accounts() {
    match { $a: Account
        $a.accountId starts_with "omnigraph-bench-fin-v1-" }
    return { $a.accountId as account, $a.createTime as created, $a.isBlocked as blocked, $a.accountType as kind, $a.freqLoginType as login, $a.accountLevel as level, $a.nickname as nickname, $a.phonenum as phonenum, $a.email as email, $a.lastLoginTime as last_login }
}
```

```gq
gq 3;
query reserved_accounts() {
    match { $a: Account
        starts_with($a.accountId, "omnigraph-bench-fin-v1-") }
    return { $a.accountId as account, $a.createTime as created, $a.isBlocked as blocked, $a.accountType as kind, $a.freqLoginType as login, $a.accountLevel as level, $a.nickname as nickname, $a.phonenum as phonenum, $a.email as email, $a.lastLoginTime as last_login }
}
```

### benchmarks/workloads/finbench_disjoint_merge.gqt#18:query

```gq
query reserved_transfers() {
    match { $a: Account
        $a $e:accounttransferaccount $b
        $e.orderNum starts_with "omnigraph-bench-fin-v1-" }
    return { $e.@src as src, $e.@dst as dst, $e.amount as amount, $e.createTime as created, $e.orderNum as order_num, $e.payType as pay, $e.goodsType as goods, $e.comment as comment }
}
```

```gq
gq 3;
query reserved_transfers() {
    match { $a: Account
        $a $e:accounttransferaccount $b
        starts_with($e.orderNum, "omnigraph-bench-fin-v1-") }
    return { $e.@src as src, $e.@dst as dst, $e.amount as amount, $e.createTime as created, $e.orderNum as order_num, $e.payType as pay, $e.goodsType as goods, $e.comment as comment }
}
```

### benchmarks/workloads/finbench_disjoint_merge.gqt#27:query

```gq
query reserved_accounts() {
    match { $a: Account
        $a.accountId starts_with "omnigraph-bench-fin-v1-" }
    return { $a.accountId as account, $a.createTime as created, $a.isBlocked as blocked, $a.accountType as kind, $a.freqLoginType as login, $a.accountLevel as level, $a.nickname as nickname, $a.phonenum as phonenum, $a.email as email, $a.lastLoginTime as last_login }
}
```

```gq
gq 3;
query reserved_accounts() {
    match { $a: Account
        starts_with($a.accountId, "omnigraph-bench-fin-v1-") }
    return { $a.accountId as account, $a.createTime as created, $a.isBlocked as blocked, $a.accountType as kind, $a.freqLoginType as login, $a.accountLevel as level, $a.nickname as nickname, $a.phonenum as phonenum, $a.email as email, $a.lastLoginTime as last_login }
}
```

### benchmarks/workloads/finbench_disjoint_merge.gqt#30:query

```gq
query reserved_transfers() {
    match { $a: Account
        $a $e:accounttransferaccount $b
        $e.orderNum starts_with "omnigraph-bench-fin-v1-" }
    return { $e.@src as src, $e.@dst as dst, $e.amount as amount, $e.createTime as created, $e.orderNum as order_num, $e.payType as pay, $e.goodsType as goods, $e.comment as comment }
}
```

```gq
gq 3;
query reserved_transfers() {
    match { $a: Account
        $a $e:accounttransferaccount $b
        starts_with($e.orderNum, "omnigraph-bench-fin-v1-") }
    return { $e.@src as src, $e.@dst as dst, $e.amount as amount, $e.createTime as created, $e.orderNum as order_num, $e.payType as pay, $e.goodsType as goods, $e.comment as comment }
}
```

### benchmarks/workloads/hot_table_idle_base.gqt#1:mutate

```gq
query hot_write() { update Hot set { value: 1 } where key = "hot" }
```

```gq
gq 3;
query hot_write() { $x: Hot  $x.key = "hot"  update $x set { value: 1 } }
```

### benchmarks/workloads/hot_table_idle_branches_4.gqt#1:mutate

```gq
query hot_write() { update Hot set { value: 1 } where key = "hot" }
```

```gq
gq 3;
query hot_write() { $x: Hot  $x.key = "hot"  update $x set { value: 1 } }
```

### benchmarks/workloads/hot_table_idle_branches_4_aged.gqt#1:mutate

```gq
query hot_write() { update Hot set { value: 1 } where key = "hot" }
```

```gq
gq 3;
query hot_write() { $x: Hot  $x.key = "hot"  update $x set { value: 1 } }
```

### benchmarks/workloads/hot_table_idle_tables_4.gqt#1:mutate

```gq
query hot_write() { update Hot set { value: 1 } where key = "hot" }
```

```gq
gq 3;
query hot_write() { $x: Hot  $x.key = "hot"  update $x set { value: 1 } }
```

### benchmarks/workloads/identity_lifecycle.gqt#1:mutate

```gq
query new_lifetime_write() { update Item set { value: 1 } where key = "alice" }
```

```gq
gq 3;
query new_lifetime_write() { $x: Item  $x.key = "alice"  update $x set { value: 1 } }
```

### benchmarks/workloads/nearest_ranks_updated_embedding.gqt#1:query

```gq
query nearest_after_update($q: Vector(4)) {
    match { $d: Doc }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 6
}
```

```gq
gq 3;
query nearest_after_update($q: Vector(4)) {
    match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 6) }
    return { $d.slug }
    order { $distance }
    limit 6
}
```

### benchmarks/workloads/nearest_ranks_updated_embedding.gqt#5:query

```gq
query nearest_after_update($q: Vector(4)) {
    match { $d: Doc }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 6
}
```

```gq
gq 3;
query nearest_after_update($q: Vector(4)) {
    match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 6) }
    return { $d.slug }
    order { $distance }
    limit 6
}
```

### benchmarks/workloads/nearest_skips_deleted_rows_and_fills_limit.gqt#1:query

```gq
query nearest_after_delete($q: Vector(4)) {
    match { $d: Doc }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 3
}
```

```gq
gq 3;
query nearest_after_delete($q: Vector(4)) {
    match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 3) }
    return { $d.slug }
    order { $distance }
    limit 3
}
```

### benchmarks/workloads/nearest_skips_deleted_rows_and_fills_limit.gqt#5:query

```gq
query nearest_after_delete($q: Vector(4)) {
    match { $d: Doc }
    return { $d.slug }
    order { nearest($d.embedding, $q) }
    limit 3
}
```

```gq
gq 3;
query nearest_after_delete($q: Vector(4)) {
    match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 3) }
    return { $d.slug }
    order { $distance }
    limit 3
}
```

### benchmarks/workloads/parallel_tables_1.gqt#1:mutate

```gq
query update_tables() {
    update T0 set { value: 1 } where key = "row"
}
```

```gq
gq 3;
query update_tables() {
    $x: T0  $x.key = "row"
    update $x set { value: 1 }
}
```

### benchmarks/workloads/very_long_history_write.gqt#1:mutate

```gq
query next_write() { update HistoryRow set { value: 1 } where key = "hot" }
```

```gq
gq 3;
query next_write() { $x: HistoryRow  $x.key = "hot"  update $x set { value: 1 } }
```

### benchmarks/workloads/very_long_history_write_unattached.gqt#1:mutate

```gq
query next_write() as unattached branch { update HistoryRow set { value: 1 } where key = "hot" }
```

```gq
gq 3;
query next_write() as unattached branch { $x: HistoryRow  $x.key = "hot"  update $x set { value: 1 } }
```

### docs/rfcs/0031-comparative-cost-harness.md#0

```gq
query set_age($name: String, $age: I32) {
    update Person set { age: $age } where name = $name
}
```

```gq
gq 3;
query set_age($name: String, $age: I32) {
    $x: Person  $x.name = $name
    update $x set { age: $age }
}
```

### docs/rfcs/0047-search-plan-truth.md#1

```gq
query passages($matter: String, $q: String) {
  match {
    $r: SourceRevision { matter_number: $matter }
    $p: Passage
    $p passageOfRevision $r
  }
  return { $r.path, $p.locator }
  order { bm25($p.text, $q) }
  limit 5
}
```

```gq
gq 3;
query passages($matter: String, $q: String) {
  match { from bm25({ match {
    $r: SourceRevision { matter_number: $matter }
    $p: Passage
    $p passageOfRevision $r
  } return { $r, $p } }, $p.text, $q) }
  return { $r.path, $p.locator }
  order { $score desc }
  limit 5
}
```

### docs/rfcs/2026-09-24-shared-expression-model.md#1

```gq
query exact() { delete Knows where @src = "a" and @dst = "b" }
```

```gq
gq 3;
query exact() { $x: Knows  $x.@src = "a" and $x.@dst = "b"  delete $x }
```

### docs/user/mutations/index.md#2

```gq
query exact() { delete Knows where @src = "a" and @dst = "b" }
```

```gq
gq 3;
query exact() { $x: Knows  $x.@src = "a" and $x.@dst = "b"  delete $x }
```

### docs/user/search/index.md#0

```gq
query similar($q: Vector(4)) {
  match { $d: Document }
  return { $d.slug, $d.title }
  order { nearest($d.embedding, $q) }
  limit 10
}
```

```gq
gq 3;
query similar($q: Vector(4)) {
  match { from nearest({ match { $d: Document } return { $d } }, $d.embedding, $q, k: 10) }
  return { $d.slug, $d.title }
  order { $distance }
  limit 10
}
```

### docs/user/search/index.md#1

```gq
query relevant($q: String) {
  match { $d: Document }
  return { $d.slug, bm25($d.body, $q) as score }
  order { bm25($d.body, $q) desc }
  limit 10
}
```

```gq
gq 3;
query relevant($q: String) {
  match { from bm25({ match { $d: Document } return { $d } }, $d.body, $q) }
  return { $d.slug, $score as score }
  order { $score desc }
  limit 10
}
```

### docs/user/search/index.md#2

```gq
query hybrid($vector: Vector(4), $text: String) {
  match { $d: Document }
  return { $d.slug, $d.title }
  order { rrf(nearest($d.embedding, $vector), bm25($d.body, $text)) }
  limit 10
}
```

```gq
gq 3;
query hybrid($vector: Vector(4), $text: String) {
  match { let $p = { match { $d: Document } return { $d } }  from rrf(nearest($p, $d.embedding, $vector, k: 10), bm25($p, $d.body, $text), k: 60) }
  return { $d.slug, $d.title }
  order { $score desc }
  limit 10
}
```

### docs/user/search/index.md#3

```gq
query passages($matter: String, $q: String) {
  match {
    $r: SourceRevision { matter_number: $matter }
    $r hasPassage $p
  }
  return { $r.path, $p.locator }
  order { bm25($p.text, $q) }
  limit 5
}
```

```gq
gq 3;
query passages($matter: String, $q: String) {
  match { from bm25({ match {
    $r: SourceRevision { matter_number: $matter }
    $r hasPassage $p
  } return { $r, $p } }, $p.text, $q) }
  return { $r.path, $p.locator }
  order { $score desc }
  limit 5
}
```

### skills/omnigraph/SKILL.md#1

```gq
query add_signal($slug: String, $name: String, $brief: String, $createdAt: DateTime) {
    insert Signal { slug: $slug, name: $name, brief: $brief,
                    stagingTimestamp: $createdAt, createdAt: $createdAt, updatedAt: $createdAt }
}
query link($from: String, $to: String) { insert FormsPattern { from: $from, to: $to } }
query retitle($slug: String, $t: String) { update Signal set { name: $t } where slug = $slug }
query remove($slug: String)              { delete Signal where slug = $slug }
```

```gq
gq 3;
query add_signal($slug: String, $name: String, $brief: String, $createdAt: DateTime) {
    insert Signal { slug: $slug, name: $name, brief: $brief,
                    stagingTimestamp: $createdAt, createdAt: $createdAt, updatedAt: $createdAt }
}
query link($from: String, $to: String) { insert FormsPattern { from: $from, to: $to } }
query retitle($slug: String, $t: String) { $x: Signal  $x.slug = $slug  update $x set { name: $t } }
query remove($slug: String)              { $x: Signal  $x.slug = $slug  delete $x }
```

### skills/omnigraph/references/queries.md#14

```gq
query vector_search($q: Vector(3072)) {
    match { $d: Doc }
    return { $d.slug, $d.title }
    order { nearest($d.embedding, $q) }
    limit 10
}
```

```gq
gq 3;
query vector_search($q: Vector(3072)) {
    match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 10) }
    return { $d.slug, $d.title }
    order { $distance }
    limit 10
}
```

### skills/omnigraph/references/queries.md#15

```gq
query hybrid_search($vq: Vector(3072), $tq: String) {
    match { $d: Doc }
    return { $d.slug, $d.title }
    order { rrf(nearest($d.embedding, $vq), bm25($d.title, $tq)) }
    limit 10
}
```

```gq
gq 3;
query hybrid_search($vq: Vector(3072), $tq: String) {
    match { let $p = { match { $d: Doc } return { $d } }  from rrf(nearest($p, $d.embedding, $vq, k: 10), bm25($p, $d.title, $tq), k: 60) }
    return { $d.slug, $d.title }
    order { $score desc }
    limit 10
}
```

### skills/omnigraph/references/queries.md#21

```gq
query retitle_signal($slug: String, $new_title: String) {
    update Signal set { name: $new_title } where slug = $slug
}
```

```gq
gq 3;
query retitle_signal($slug: String, $new_title: String) {
    $x: Signal  $x.slug = $slug
    update $x set { name: $new_title }
}
```

### skills/omnigraph/references/queries.md#22

```gq
query remove_signal($slug: String) {
    delete Signal where slug = $slug
}
```

```gq
gq 3;
query remove_signal($slug: String) {
    $x: Signal  $x.slug = $slug
    delete $x
}
```

### skills/omnigraph/references/schema-design.md#13

```gq
query customer_evidence($slug: String, $q: String) {
    match {
        $c: Customer { slug: $slug }
        $a: Assertion
        $a about $c
    }
    return { $a.statement }
    order { nearest($a.embedding, $q) }
    limit 10
}
```

```gq
gq 3;
query customer_evidence($slug: String, $q: String) {
    match { from nearest({ match {
        $c: Customer { slug: $slug }
        $a: Assertion
        $a about $c
    } return { $c, $a } }, $a.embedding, $q, k: 10) }
    return { $a.statement }
    order { $distance }
    limit 10
}
```

### skills/omnigraph/references/search.md#4

```gq
query nearest_chunks($q: Vector(1536)) {
    match { $c: Chunk }
    return { $c.text }
    order { nearest($c.embedding, $q) }
    limit 10
}
```

```gq
gq 3;
query nearest_chunks($q: Vector(1536)) {
    match { from nearest({ match { $c: Chunk } return { $c } }, $c.embedding, $q, k: 10) }
    return { $c.text }
    order { $distance }
    limit 10
}
```

### skills/omnigraph/references/search.md#5

```gq
query top_titles($q: String) {
    match { $d: Doc }
    return { $d.slug, $d.title }
    order { bm25($d.title, $q) }
    limit 10
}
```

```gq
gq 3;
query top_titles($q: String) {
    match { from bm25({ match { $d: Doc } return { $d } }, $d.title, $q) }
    return { $d.slug, $d.title }
    order { $score desc }
    limit 10
}
```

### skills/omnigraph/references/search.md#6

```gq
query hybrid($vq: Vector(1536), $tq: String) {
    match { $d: Doc }
    return { $d.slug, $d.title }
    order { rrf(nearest($d.embedding, $vq), bm25($d.title, $tq)) }
    limit 10
}
```

```gq
gq 3;
query hybrid($vq: Vector(1536), $tq: String) {
    match { let $p = { match { $d: Doc } return { $d } }  from rrf(nearest($p, $d.embedding, $vq, k: 10), bm25($p, $d.title, $tq), k: 60) }
    return { $d.slug, $d.title }
    order { $score desc }
    limit 10
}
```

### skills/omnigraph/references/search.md#7

```gq
query scored_titles($q: String) {
    match { $d: Doc }
    return { $d.slug, bm25($d.title, $q) as score }
    order { bm25($d.title, $q) }
    limit 10
}
```

```gq
gq 3;
query scored_titles($q: String) {
    match { from bm25({ match { $d: Doc } return { $d } }, $d.title, $q) }
    return { $d.slug, $score as score }
    order { $score desc }
    limit 10
}
```

### skills/omnigraph/references/search.md#10

```gq
query related_chunks($artifact_slug: String, $q: Vector(1536)) {
    match {
        $c: Chunk                                 // the ranked binding
        $c partOfArtifact $a                      // scope: only this artifact's chunks
        $a.slug = $artifact_slug
    }
    return { $c.text }
    order { nearest($c.embedding, $q) }           // rank: vector similarity within scope
    limit 10
}
```

```gq
gq 3;
query related_chunks($artifact_slug: String, $q: Vector(1536)) {
    match { from nearest({ match {
        $c: Chunk                                 // the ranked binding
        $c partOfArtifact $a                      // scope: only this artifact's chunks
        $a.slug = $artifact_slug
    } return { $c, $a } }, $c.embedding, $q, k: 10) }
    return { $c.text }
    order { $distance }           // rank: vector similarity within scope
    limit 10
}
```

