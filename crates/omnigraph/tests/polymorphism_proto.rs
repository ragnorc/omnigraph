//! Polymorphic types prototype (RFC 2026-10-07): interface endpoints on edges.
//!
//! Person "alice" and Organization "alice" share an id, so every assertion
//! below fails if a layer matches an endpoint on its id alone.

mod helpers;

use omnigraph::db::Omnigraph;
use omnigraph::loader::LoadMode;
use omnigraph::Session;
use omnigraph_compiler::ir::ParamMap;
use omnigraph_compiler::query::ast::Literal;

use helpers::*;

const SCHEMA: &str = r#"
interface Identifiable {
    slug: String @key
    name: String
}
node Person implements Identifiable {
    email: String?
}
node Organization implements Identifiable {
    website: String?
}
node ExternalID {
    value: String @key
}
interface Solo {
    code: String @key
}
node Badge implements Solo {
    label: String?
}
edge Identifies: ExternalID -> Identifiable
edge Holds: ExternalID -> Solo
edge Knows: Person -> Person
"#;

const NODES: &str = r#"{"type":"Person","data":{"slug":"alice","name":"Alice Person","email":"alice@example.com"}}
{"type":"Person","data":{"slug":"bob","name":"Bob"}}
{"type":"Person","data":{"slug":"acme","name":"Acme Person"}}
{"type":"Organization","data":{"slug":"alice","name":"Alice Org"}}
{"type":"Organization","data":{"slug":"acme","name":"Acme"}}
{"type":"ExternalID","data":{"value":"li:alice"}}
{"type":"ExternalID","data":{"value":"web:alice"}}
{"type":"ExternalID","data":{"value":"web:acme"}}
{"type":"Badge","data":{"code":"b1","label":"First"}}
"#;

const EDGES: &str = r#"{"edge":"Identifies","from":"li:alice","to":"alice","to_type":"Person"}
{"edge":"Identifies","from":"web:alice","to":"alice","to_type":"Organization"}
{"edge":"Identifies","from":"web:acme","to":"acme","to_type":"Organization"}
"#;

const QUERIES: &str = r#"
query identified($value: String) {
    match {
        $e: ExternalID { value: $value }
        $e identifies $x
    }
    return { $x.@type as type, $x.name as name }
}

query owners_of($slug: String) {
    match {
        $x: Identifiable { slug: $slug }
        $x identifies $e
    }
    return { $e.value as value, $x.@type as type }
}

query two_hop($value: String) {
    match {
        $e: ExternalID { value: $value }
        $e identifies $x
        $x identifies $f
    }
    return { $f.value as value }
}

query unidentified_people() {
    match {
        $p: Person
        not {
            $p identifies $_
        }
    }
    return { $p.slug as slug }
}

query not_identified_by($slug: String, $value: String) {
    match {
        $e: ExternalID { value: $value }
        $x: Identifiable { slug: $slug }
        not {
            $e identifies $x
        }
    }
    return { $x.@type as type }
}

query delete_org($slug: String) {
    delete Organization where slug = $slug
}
"#;

async fn graph(dir: &tempfile::TempDir) -> Session {
    let uri = dir.path().to_str().unwrap();
    let db = session(Omnigraph::init(uri, SCHEMA).await.unwrap());
    db.load_jsonl(NODES, LoadMode::Overwrite).await.unwrap();
    db.load_jsonl(EDGES, LoadMode::Append).await.unwrap();
    db
}

fn value(name: &str, v: &str) -> ParamMap {
    let mut params = ParamMap::new();
    params.insert(name.to_string(), Literal::String(v.to_string()));
    params
}

fn rows(result: &omnigraph_compiler::result::QueryResult) -> Vec<String> {
    let json = result.to_rust_json().unwrap();
    let mut out: Vec<String> = json
        .as_array()
        .unwrap()
        .iter()
        // Key order follows the projection; compare rows with sorted keys.
        .map(|row| {
            let sorted: std::collections::BTreeMap<_, _> =
                row.as_object().unwrap().iter().collect();
            serde_json::to_string(&sorted).unwrap()
        })
        .collect();
    out.sort();
    out
}

async fn refused(db: &Session, line: &str) -> String {
    match db.load_jsonl(line, LoadMode::Append).await {
        Ok(_) => panic!("load must refuse: {line}"),
        Err(error) => error.to_string(),
    }
}

#[tokio::test]
async fn a_polymorphic_endpoint_without_a_type_is_refused() {
    let dir = tempfile::tempdir().unwrap();
    let db = graph(&dir).await;
    let error = refused(&db, r#"{"edge":"Identifies","from":"li:alice","to":"bob"}"#).await;
    assert!(error.contains("to_type"), "{error}");
}

#[tokio::test]
async fn an_endpoint_type_outside_the_interface_is_refused() {
    let dir = tempfile::tempdir().unwrap();
    let db = graph(&dir).await;
    let error = refused(
        &db,
        r#"{"edge":"Identifies","from":"li:alice","to":"li:alice","to_type":"ExternalID"}"#,
    )
    .await;
    assert!(error.contains("ExternalID"), "{error}");
}

#[tokio::test]
async fn a_type_on_a_concrete_endpoint_is_refused() {
    let dir = tempfile::tempdir().unwrap();
    let db = graph(&dir).await;
    let error = refused(
        &db,
        r#"{"edge":"Identifies","from":"li:alice","from_type":"ExternalID","to":"bob","to_type":"Person"}"#,
    )
    .await;
    assert!(error.contains("from_type"), "{error}");
    let error = refused(
        &db,
        r#"{"edge":"Knows","from":"alice","to":"bob","to_type":"Person"}"#,
    )
    .await;
    assert!(error.contains("to_type"), "{error}");
}

#[tokio::test]
async fn a_user_supplied_tag_column_is_refused() {
    let dir = tempfile::tempdir().unwrap();
    let db = graph(&dir).await;
    let error = refused(
        &db,
        r#"{"edge":"Identifies","from":"li:alice","to":"bob","to_type":"Person","data":{"__dst_type":1}}"#,
    )
    .await;
    assert!(error.contains("__dst_type"), "{error}");
}

#[tokio::test]
async fn a_single_member_interface_infers_the_endpoint_type() {
    let dir = tempfile::tempdir().unwrap();
    let db = graph(&dir).await;
    db.load_jsonl(
        r#"{"edge":"Holds","from":"li:alice","to":"b1"}"#,
        LoadMode::Append,
    )
    .await
    .unwrap();
}

#[tokio::test]
async fn an_edge_to_the_wrong_member_is_an_orphan() {
    let dir = tempfile::tempdir().unwrap();
    let db = graph(&dir).await;
    // "bob" exists as a Person, never as an Organization.
    let error = refused(
        &db,
        r#"{"edge":"Identifies","from":"web:acme","to":"bob","to_type":"Organization"}"#,
    )
    .await;
    assert!(error.to_lowercase().contains("orphan") || error.contains("bob"), "{error}");
}

#[tokio::test]
async fn a_typed_traversal_keeps_colliding_ids_apart_on_every_path() {
    let dir = tempfile::tempdir().unwrap();
    let db = graph(&dir).await;
    // The CSR has no typed endpoints: forcing it is refused (RFC, before phase 3).
    let csr = with_traversal(&db, Traversal::Csr);
    let error = query_main(&csr, QUERIES, "identified", &value("value", "li:alice"))
        .await
        .expect_err("a polymorphic traversal refuses traversal = csr");
    assert!(error.to_string().contains("csr"), "{error}");
    for mode in [Traversal::Auto, Traversal::Indexed] {
        let db = with_traversal(&db, mode);
        let person = rows(
            &query_main(&db, QUERIES, "identified", &value("value", "li:alice"))
                .await
                .unwrap(),
        );
        assert_eq!(
            person,
            vec![r#"{"name":"Alice Person","type":"Person"}"#.to_string()],
            "{mode:?}"
        );
        let owners = rows(
            &query_main(&db, QUERIES, "owners_of", &value("slug", "alice"))
                .await
                .unwrap(),
        );
        assert_eq!(
            owners,
            vec![
                r#"{"type":"Organization","value":"web:alice"}"#.to_string(),
                r#"{"type":"Person","value":"li:alice"}"#.to_string(),
            ],
            "{mode:?}"
        );
        // ExternalID -> Identifiable -> ExternalID: the middle hop must stay on
        // the Person "alice", not fan out to the Organization "alice".
        let back = rows(
            &query_main(&db, QUERIES, "two_hop", &value("value", "li:alice"))
                .await
                .unwrap(),
        );
        assert_eq!(back, vec![r#"{"value":"li:alice"}"#.to_string()], "{mode:?}");
        // Organization "acme" has an edge, Person "acme" has none.
        let unidentified = rows(
            &query_main(&db, QUERIES, "unidentified_people", &ParamMap::new())
                .await
                .unwrap(),
        );
        assert_eq!(
            unidentified,
            vec![r#"{"slug":"acme"}"#.to_string(), r#"{"slug":"bob"}"#.to_string()],
            "{mode:?}"
        );
        // Both ends bound outside the negation: the inner traversal closes a
        // cycle and must compare the type as well as the id.
        let mut params = value("slug", "alice");
        params.extend(value("value", "li:alice"));
        let not_by = rows(
            &query_main(&db, QUERIES, "not_identified_by", &params)
                .await
                .unwrap(),
        );
        assert_eq!(not_by, vec![r#"{"type":"Organization"}"#.to_string()], "{mode:?}");
    }
}

#[tokio::test]
async fn deleting_one_member_cascades_only_its_own_edges() {
    let dir = tempfile::tempdir().unwrap();
    let db = graph(&dir).await;
    let result = mutate_main(&db, QUERIES, "delete_org", &value("slug", "alice"))
        .await
        .unwrap();
    assert_eq!(result.affected_nodes, 1);
    assert_eq!(result.affected_edges, 1);
    let person = rows(
        &query_main(&db, QUERIES, "identified", &value("value", "li:alice"))
            .await
            .unwrap(),
    );
    assert_eq!(
        person,
        vec![r#"{"name":"Alice Person","type":"Person"}"#.to_string()]
    );
    assert_eq!(count_rows(db.db(), "edge:Identifies").await, 2);
}

const MONOMORPHIC: &str = r#"
interface Identifiable {
    slug: String @key
    name: String
}
node Person implements Identifiable {
    email: String?
}
node Organization implements Identifiable {
    website: String?
}
node ExternalID {
    value: String @key
}
edge Identifies: ExternalID -> Person
"#;

#[tokio::test]
async fn generalizing_an_endpoint_types_every_existing_row_as_the_old_node_type() {
    use omnigraph_compiler::{EndpointSide, SchemaMigrationStep};

    let dir = tempfile::tempdir().unwrap();
    let uri = dir.path().to_str().unwrap();
    let db = session(Omnigraph::init(uri, MONOMORPHIC).await.unwrap());
    db.load_jsonl(NODES.lines().filter(|line| !line.contains("Badge")).collect::<Vec<_>>().join("\n").as_str(), LoadMode::Overwrite)
        .await
        .unwrap();
    db.load_jsonl(
        r#"{"edge":"Identifies","from":"li:alice","to":"alice"}"#,
        LoadMode::Append,
    )
    .await
    .unwrap();

    let desired = MONOMORPHIC.replace(
        "edge Identifies: ExternalID -> Person",
        "edge Identifies: ExternalID -> Identifiable",
    );
    let plan = db.plan_schema(&desired).await.unwrap();
    assert!(plan.supported, "{plan:?}");
    assert!(plan.steps.iter().any(|step| matches!(
        step,
        SchemaMigrationStep::GeneralizeEndpoint { edge_name, side: EndpointSide::Destination, from, to }
            if edge_name == "Identifies" && from == "Person" && to == "Identifiable"
    )), "{plan:?}");
    db.apply_schema(&desired).await.unwrap();

    // The old row reads as Person; a new row may now name Organization.
    let before = rows(
        &query_main(&db, QUERIES, "identified", &value("value", "li:alice"))
            .await
            .unwrap(),
    );
    assert_eq!(before, vec![r#"{"name":"Alice Person","type":"Person"}"#.to_string()]);
    db.load_jsonl(
        r#"{"edge":"Identifies","from":"web:alice","to":"alice","to_type":"Organization"}"#,
        LoadMode::Append,
    )
    .await
    .unwrap();
    let owners = rows(
        &query_main(&db, QUERIES, "owners_of", &value("slug", "alice"))
            .await
            .unwrap(),
    );
    assert_eq!(
        owners,
        vec![
            r#"{"type":"Organization","value":"web:alice"}"#.to_string(),
            r#"{"type":"Person","value":"li:alice"}"#.to_string(),
        ]
    );

    // Narrowing back is not a generalization.
    let narrowed = db.plan_schema(MONOMORPHIC).await.unwrap();
    assert!(!narrowed.supported, "{narrowed:?}");
}

#[tokio::test]
async fn a_keyed_polymorphic_edge_keys_on_the_endpoint_type() {
    let dir = tempfile::tempdir().unwrap();
    let uri = dir.path().to_str().unwrap();
    let schema = SCHEMA.replace(
        "edge Identifies: ExternalID -> Identifiable",
        "edge Identifies: ExternalID -> Identifiable { @key(@src, @dst) }",
    );
    let db = session(Omnigraph::init(uri, &schema).await.unwrap());
    db.load_jsonl(NODES, LoadMode::Overwrite).await.unwrap();
    let both = r#"{"edge":"Identifies","from":"li:alice","to":"alice","to_type":"Person"}
{"edge":"Identifies","from":"li:alice","to":"alice","to_type":"Organization"}"#;
    db.load_jsonl(both, LoadMode::Merge).await.unwrap();
    assert_eq!(count_rows(db.db(), "edge:Identifies").await, 2);
    // The same two rows again are upserts on their (src, type, dst, type) key.
    db.load_jsonl(both, LoadMode::Merge).await.unwrap();
    assert_eq!(count_rows(db.db(), "edge:Identifies").await, 2);
    let owners = rows(
        &query_main(&db, QUERIES, "owners_of", &value("slug", "alice"))
            .await
            .unwrap(),
    );
    assert_eq!(
        owners,
        vec![
            r#"{"type":"Organization","value":"li:alice"}"#.to_string(),
            r#"{"type":"Person","value":"li:alice"}"#.to_string(),
        ]
    );
}

#[tokio::test]
async fn generalizing_a_keyed_edge_is_refused() {
    let dir = tempfile::tempdir().unwrap();
    let uri = dir.path().to_str().unwrap();
    let keyed = MONOMORPHIC.replace(
        "edge Identifies: ExternalID -> Person",
        "edge Identifies: ExternalID -> Person { @key(@src, @dst) }",
    );
    let db = session(Omnigraph::init(uri, &keyed).await.unwrap());
    let desired = keyed.replace("ExternalID -> Person", "ExternalID -> Identifiable");
    let plan = db.plan_schema(&desired).await.unwrap();
    assert!(!plan.supported, "{plan:?}");
}

const DIRECTION_SCHEMA: &str = r#"
interface Named {
    slug: String @key
}
node Person implements Named {
    email: String?
}
node Organization implements Named {
    website: String?
}
node Note implements Named {
    text: String?
}
edge RelatedTo: Named -> Named
edge Mentions: Named -> Note
"#;

const DIRECTION_DATA: &str = r#"{"type":"Person","data":{"slug":"alice"}}
{"type":"Person","data":{"slug":"bob"}}
{"type":"Person","data":{"slug":"carol"}}
{"type":"Organization","data":{"slug":"alice"}}
{"type":"Organization","data":{"slug":"acme"}}
{"type":"Note","data":{"slug":"n1"}}
{"type":"Note","data":{"slug":"n2"}}
{"edge":"RelatedTo","from":"alice","from_type":"Person","to":"acme","to_type":"Organization"}
{"edge":"RelatedTo","from":"acme","from_type":"Organization","to":"bob","to_type":"Person"}
{"edge":"RelatedTo","from":"alice","from_type":"Organization","to":"carol","to_type":"Person"}
{"edge":"RelatedTo","from":"bob","from_type":"Person","to":"n1","to_type":"Note"}
{"edge":"Mentions","from":"n2","from_type":"Note","to":"n1"}
{"edge":"Mentions","from":"alice","from_type":"Person","to":"n2"}
"#;

const DIRECTION_QUERIES: &str = r#"
query related($slug: String) {
    match {
        $a: Person { slug: $slug }
        $a relatedTo $b
    }
    return { $b.@type as type, $b.slug as slug }
}

query related_within_three($slug: String) {
    match {
        $a: Person { slug: $slug }
        $a relatedTo{1,3} $b
    }
    return { $b.@type as type, $b.slug as slug }
}

query note_mentions($slug: String) {
    match {
        $n: Note { slug: $slug }
        $n mentions $x
    }
    return { $x.slug as slug }
}

query people_mentioning($slug: String) {
    match {
        $n: Note { slug: $slug }
        $p: Person
        $n mentions $p
    }
    return { $p.slug as slug }
}
"#;

async fn direction_graph(dir: &tempfile::TempDir) -> Session {
    let uri = dir.path().to_str().unwrap();
    let db = session(Omnigraph::init(uri, DIRECTION_SCHEMA).await.unwrap());
    db.load_jsonl(DIRECTION_DATA, LoadMode::Overwrite).await.unwrap();
    db
}

#[tokio::test]
async fn equal_endpoint_sets_traverse_outgoing_for_one_hop_and_recursion() {
    let dir = tempfile::tempdir().unwrap();
    let db = direction_graph(&dir).await;
    // Person "alice" relates to Organization "acme"; Organization "alice"'s
    // edge to Person "carol" must not leak in through the shared id.
    let one = rows(
        &query_main(&db, DIRECTION_QUERIES, "related", &value("slug", "alice"))
            .await
            .unwrap(),
    );
    assert_eq!(one, vec![r#"{"slug":"acme","type":"Organization"}"#.to_string()]);
    // Person alice -> Organization acme -> Person bob -> Note n1.
    let three = rows(
        &query_main(&db, DIRECTION_QUERIES, "related_within_three", &value("slug", "alice"))
            .await
            .unwrap(),
    );
    assert_eq!(
        three,
        vec![
            r#"{"slug":"acme","type":"Organization"}"#.to_string(),
            r#"{"slug":"bob","type":"Person"}"#.to_string(),
            r#"{"slug":"n1","type":"Note"}"#.to_string(),
        ]
    );
}

#[tokio::test]
async fn unequal_overlapping_endpoint_sets_need_the_other_endpoint_to_decide() {
    let dir = tempfile::tempdir().unwrap();
    let db = direction_graph(&dir).await;
    // A Note fits both ends of `Mentions: Named -> Note`.
    let error = query_main(&db, DIRECTION_QUERIES, "note_mentions", &value("slug", "n2"))
        .await
        .expect_err("an undecided direction is refused");
    assert!(error.to_string().contains("ambiguous"), "{error}");
    // A Person destination can only be the source end: the traversal is inbound.
    let people = rows(
        &query_main(&db, DIRECTION_QUERIES, "people_mentioning", &value("slug", "n2"))
            .await
            .unwrap(),
    );
    assert_eq!(people, vec![r#"{"slug":"alice"}"#.to_string()]);
}
