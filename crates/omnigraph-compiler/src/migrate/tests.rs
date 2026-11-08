use super::*;
use crate::catalog::build_catalog;
use crate::schema::parser::parse_schema;

const SCHEMA: &str = r#"
node Person { handle: String @key  name: String  age: I32?  city: String?  tags: [String]? }
node Company { slug: String @key  name: String }
node Doc { slug: String @key  title: String @index  body: String @index  tags: [String]  embedding: Vector(3) @index }
edge WorksAt: Person -> Company
edge Knows: Person -> Person
edge Authored: Person -> Doc
"#;

fn catalog() -> Catalog {
    build_catalog(&parse_schema(SCHEMA).unwrap()).unwrap()
}

fn run(source: &str) -> Migration {
    let m = migrate(source, Some(&catalog())).unwrap();
    if m.refusals.is_empty() {
        assert!(m.proof.holds(), "{source}\n{:#?}\n{}", m.proof, m.text);
    } else {
        // A refused construct stays in edition 2, which the residue check sees.
        assert!(
            m.proof.non_overlapping && m.proof.each_edit_rendered_once && m.proof.confined,
            "{:#?}",
            m.proof
        );
    }
    m
}

fn body(m: &Migration) -> &str {
    m.text.strip_prefix("gq 3;\n").unwrap()
}

#[test]
fn infix_string_predicates_become_calls_and_list_contains_becomes_in() {
    let m = run(r#"query q() {
    match {
        $d: Doc
        $d.title contains "graph"   // keep me
        $d.title starts_with "The"
        $d.tags contains "rust"
    }
    return { $d.slug }
}"#);
    assert_eq!(
        body(&m),
        r#"query q() {
    match {
        $d: Doc
        contains($d.title, "graph")   // keep me
        starts_with($d.title, "The")
        "rust" in $d.tags
    }
    return { $d.slug }
}"#
    );
    let rows = &m.decls[0].rows;
    assert_eq!(rows.get(&Row::InfixContains), Some(&1));
    assert_eq!(rows.get(&Row::InfixStartsWith), Some(&1));
    assert_eq!(rows.get(&Row::ListContains), Some(&1));
}

#[test]
fn a_contains_inside_a_block_and_a_list_parameter() {
    let m = run(r#"query q($tags: [String]) {
    match { $p: Person  not { $p knows $f  $f.name contains "x" }  $tags contains "x" }
    return { $p.handle }
}"#);
    assert!(
        body(&m).contains(r#"not { $p knows $f  contains($f.name, "x") }"#),
        "{}",
        m.text
    );
    assert!(body(&m).contains(r#""x" in $tags"#), "{}", m.text);
}

#[test]
fn fuzzy_third_argument_becomes_max_edits() {
    let m = run(
        r#"query q($q: String) { match { $d: Doc  fuzzy($d.title, $q, 2) } return { $d.slug } }"#,
    );
    assert!(
        body(&m).contains("fuzzy($d.title, $q, max_edits: 2)"),
        "{}",
        m.text
    );
}

#[test]
fn nearest_moves_into_from_with_k_and_the_distance_column() {
    let m = run(r#"query similar($q: Vector(3)) {
    match { $d: Doc }
    return { $d.slug, nearest($d.embedding, $q) as s }
    order { nearest($d.embedding, $q) }
    limit 20
}"#);
    assert_eq!(
        body(&m),
        r#"query similar($q: Vector(3)) {
    match { from nearest({ match { $d: Doc } return { $d } }, embedding, $q, k: 20) }
    return { $d.slug, $distance as s }
    order { $distance }
    limit 20
}"#
    );
}

#[test]
fn an_unaliased_ranking_gets_the_generated_alias_and_a_note() {
    let m = run(r#"query q($q: Vector(3)) {
    match { $d: Doc }
    return { $d.slug, nearest($d.embedding, $q) }
    order { nearest($d.embedding, $q) }
    limit 2
}"#);
    assert!(
        body(&m).contains("return { $d.slug, $distance as _distance }"),
        "{}",
        m.text
    );
    assert!(
        m.notes
            .iter()
            .any(|n| n.kind == "result-column-renamed" && n.detail.contains("d._distance"))
    );
}

#[test]
fn a_bare_bm25_key_keeps_its_descending_direction() {
    let m = run(r#"query q($t: String) {
    match { $d: Doc  $d.title contains "x" }
    return { $d.slug }
    order { bm25($d.body, $t) }
    limit 3
}"#);
    assert_eq!(
        body(&m),
        r#"query q($t: String) {
    match { from bm25({ match { $d: Doc  contains($d.title, "x") } return { $d } }, body, $t) }
    return { $d.slug }
    order { $score desc }
    limit 3
}"#
    );
}

#[test]
fn rrf_names_the_population_once_and_sizes_the_nearest_arm() {
    let m = run(r#"query q($v: Vector(3), $t: String) {
    match { $d: Doc }
    return { $d.slug }
    order { rrf(nearest($d.embedding, $v), bm25($d.body, $t), 30) }
    limit 4
}"#);
    assert_eq!(
        body(&m),
        r#"query q($v: Vector(3), $t: String) {
    match { let $p = { match { $d: Doc } return { $d } }  from rrf(nearest($p, embedding, $v, k: 4), bm25($p, body, $t), k: 30) }
    return { $d.slug }
    order { $score desc }
    limit 4
}"#
    );
}

#[test]
fn rrf_let_name_is_fresh_and_duplicate_arms_are_refused() {
    let m = run(r#"query q($v: Vector(3), $t: String) {
    match { $p: Person  $p authored $d }
    return { $d.slug }
    order { rrf(nearest($d.embedding, $v), bm25($d.body, $t)) }
    limit 4
}"#);
    assert!(body(&m).contains("let $p2 = { match {"), "{}", m.text);
    assert!(body(&m).contains("return { $p, $d } }"), "{}", m.text);
    assert!(m.notes.iter().any(|n| n.kind == "population-node-columns"));
    let m = run(r#"query q($t: String) {
    match { $d: Doc }
    return { $d.slug }
    order { rrf(bm25($d.body, $t), bm25($d.body, $t)) }
    limit 4
}"#);
    assert_eq!(m.refusals.len(), 1, "{:?}", m.refusals);
}

#[test]
fn delete_and_update_bind_the_target_and_prefix_the_predicate() {
    let m = run(r#"query q($h: String) {
    delete Person where handle = $h and tags contains "x"
}"#);
    assert_eq!(
        body(&m),
        r#"query q($h: String) {
    $x: Person  $x.handle = $h and "x" in $x.tags
    delete $x
}"#
    );
    let m = run(r#"query q($h: String) {
    update Person set { city: "Berlin", name: $h } where @id = $h
}"#);
    assert_eq!(
        body(&m),
        r#"query q($h: String) {
    $x: Person  $x.@id = $h
    update $x set { city: "Berlin", name: $h }
}"#
    );
    assert_eq!(m.decls[0].rows.get(&Row::Update), Some(&1));
}

#[test]
fn edge_targets_spell_their_legacy_endpoints_as_meta_fields() {
    let m = run(r#"query q($x: String) { delete WorksAt where from = $x }"#);
    assert_eq!(
        body(&m),
        r#"query q($x: String) { $x2: WorksAt  $x2.@src = $x  delete $x2 }"#
    );
    assert!(m.notes.iter().any(|n| n.kind == "edge-binding"));
    assert!(m.notes.iter().any(|n| n.kind == "legacy-endpoint"));
}

#[test]
fn several_statements_are_refused() {
    let m = run(r#"query q($h: String) {
    delete Knows where @src = $h
    delete Person where handle = $h
}"#);
    assert_eq!(m.refusals.len(), 2);
}

#[test]
fn an_edition_3_source_is_left_alone() {
    let m = run(r#"query q() { match { $d: Doc  $d.title contains "x" } return { $d.slug } }"#);
    let again = migrate(&m.text, Some(&catalog())).unwrap();
    assert!(again.already_edition_3);
    assert_eq!(again.text, m.text);
}

#[test]
fn the_residue_check_sees_an_unrewritten_spelling() {
    let mut failures = Vec::new();
    assert!(!check_no_residue(
        r#"query q() { match { $d: Doc  $d.title contains "x" } return { $d.slug } }"#,
        &mut failures
    ));
    assert!(!check_no_residue(
        r#"query q() { match { $d: Doc } return { $d.slug } order { nearest($d.e, $q) } limit 1 }"#,
        &mut failures
    ));
    assert!(!check_no_residue(
        r#"query q() { delete Person where handle = "a" }"#,
        &mut failures
    ));
    assert!(!check_no_residue(
        r#"query q() { match { $d: Doc  fuzzy($d.t, "a", 1) } return { $d.slug } }"#,
        &mut failures
    ));
    let mut none = Vec::new();
    assert!(
        check_no_residue(
            r#"query q() { match { from nearest({ match { $d: Doc } return { $d } }, e, $q, k: 1)  $a contains $b } return { $distance as d } order { $distance } limit 1 }"#,
            &mut none
        ),
        "{none:?}"
    );
}

#[test]
fn overlapping_edits_fail_the_proof() {
    let source = "abcdef";
    let edits = vec![
        Edit {
            span: Span::new(0, 3),
            pieces: vec![lit("x")],
            row: Row::InfixContains,
            construct: Span::new(0, 6),
            generated: vec![],
            seq: 1,
        },
        Edit {
            span: Span::new(2, 5),
            pieces: vec![lit("y")],
            row: Row::InfixContains,
            construct: Span::new(0, 6),
            generated: vec![],
            seq: 2,
        },
    ];
    let (_, proof) = apply(source, &edits);
    assert!(!proof.non_overlapping);
}

#[test]
fn an_edit_inside_a_deleted_range_fails_the_proof() {
    let source = "abcdef";
    let edits = vec![
        Edit {
            span: Span::new(1, 2),
            pieces: vec![lit("x")],
            row: Row::InfixContains,
            construct: Span::new(0, 6),
            generated: vec![],
            seq: 1,
        },
        Edit {
            span: Span::new(0, 4),
            pieces: vec![],
            row: Row::Delete,
            construct: Span::new(0, 6),
            generated: vec![],
            seq: 2,
        },
    ];
    let (_, proof) = apply(source, &edits);
    assert!(!proof.each_edit_rendered_once);
}

#[test]
fn spans_do_not_change_parse_results() {
    // Two layouts of one declaration check and lower alike.
    let a = "query q($h: String) { match { $p: Person { handle: $h } } return { $p.name } }";
    let b = "query q($h: String) {\n  match {\n    $p: Person { handle: $h }   // c\n  }\n  return { $p.name }\n}";
    let cat = catalog();
    let parse = |s: &str| match parse_query_diagnostic(s).unwrap().body {
        FileBody::Queries(mut d) => d.remove(0),
        _ => unreachable!(),
    };
    let (da, db) = (parse(a), parse(b));
    let la = crate::ir::lower::lower_query(
        &cat,
        &crate::query::typecheck::typecheck_query(&cat, &da).unwrap(),
    )
    .unwrap();
    let lb = crate::ir::lower::lower_query(
        &cat,
        &crate::query::typecheck::typecheck_query(&cat, &db).unwrap(),
    )
    .unwrap();
    assert_eq!(format!("{la:?}"), format!("{lb:?}"));
    assert_ne!(da.spans.decl, db.spans.decl);
}

#[test]
fn a_ranking_key_under_an_aggregate_leaves_order() {
    let m = run(r#"query q($t: String) {
    match { $d: Doc }
    return { $d.title, count($d) as n }
    order { bm25($d.body, $t), n desc }
    limit 1
}"#);
    assert!(body(&m).contains("order { n desc }"), "{}", m.text);
    let m = run(r#"query q($v: Vector(3)) {
    match { $p: Person  $p authored $d }
    return { count($d) as n }
    order { nearest($d.embedding, $v) }
    limit 2
}"#);
    assert!(body(&m).contains("return { count($d) as n }\n    limit 2\n}"), "{}", m.text);
    assert!(body(&m).contains("k: 2)"), "{}", m.text);
}

#[test]
fn variables_bound_inside_a_block_stay_out_of_the_population() {
    let m = run(r#"query q($v: Vector(3)) {
    match { $d: Doc  not { $p authored $d  $p.age > 3 } }
    return { $d.slug }
    order { nearest($d.embedding, $v) }
    limit 2
}"#);
    assert!(body(&m).contains("return { $d } }, embedding"), "{}", m.text);
    assert!(m.notes.iter().any(|n| n.kind == "population-block-locals"));
}
