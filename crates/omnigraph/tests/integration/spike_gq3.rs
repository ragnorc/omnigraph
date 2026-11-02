//! SPIKE (RFC PR #1202, branch `spike/gq3-1202`): edition-3 queries end to
//! end on real Lance tables, compared with their edition-2 spellings.

use omnigraph::loader::LoadMode;
use omnigraph_compiler::ir::ParamMap;
use omnigraph_compiler::query::ast::Literal;
use serde_json::Value;

use super::helpers::*;

const SCHEMA: &str = r#"
node Doc {
    slug: String @key
    body: String @index
    embedding: Vector(2) @index
    topic: String?
}
node Person { name: String @key  age: I32? }
node Company { name: String @key }
edge WorksAt: Person -> Company
edge Wrote: Person -> Doc
"#;

const DATA: &str = r#"{"type":"Doc","data":{"slug":"d1","body":"graph database engine","embedding":[1.0,0.0],"topic":"db"}}
{"type":"Doc","data":{"slug":"d2","body":"graph query language","embedding":[0.9,0.1],"topic":"lang"}}
{"type":"Doc","data":{"slug":"d3","body":"vector search engine","embedding":[0.7,0.3],"topic":"db"}}
{"type":"Doc","data":{"slug":"d4","body":"cooking with garlic","embedding":[0.0,1.0],"topic":"food"}}
{"type":"Doc","data":{"slug":"d5","body":"graph graph graph","embedding":[0.5,0.5],"topic":"db"}}
{"type":"Doc","data":{"slug":"d6","body":"engine oil","embedding":[0.2,0.8],"topic":"cars"}}
{"type":"Person","data":{"name":"alice","age":30}}
{"type":"Person","data":{"name":"bob","age":40}}
{"type":"Person","data":{"name":"carol"}}
{"type":"Company","data":{"name":"acme"}}
{"edge":"WorksAt","from":"alice","to":"acme","data":{}}
{"edge":"WorksAt","from":"bob","to":"acme","data":{}}
{"edge":"Wrote","from":"alice","to":"d1","data":{}}
{"edge":"Wrote","from":"alice","to":"d2","data":{}}
{"edge":"Wrote","from":"alice","to":"d3","data":{}}
{"edge":"Wrote","from":"bob","to":"d4","data":{}}
{"edge":"Wrote","from":"carol","to":"d5","data":{}}
{"edge":"Wrote","from":"carol","to":"d6","data":{}}
"#;

async fn graph(dir: &tempfile::TempDir) -> omnigraph::Session {
    let db = session(
        omnigraph::db::Omnigraph::init(dir.path().to_str().unwrap(), SCHEMA)
            .await
            .unwrap(),
    );
    db.load_jsonl(DATA, LoadMode::Overwrite).await.unwrap();
    db.ensure_indices().await.unwrap();
    db
}

fn params(pairs: &[(&str, Literal)]) -> ParamMap {
    pairs
        .iter()
        .map(|(k, v)| (k.to_string(), v.clone()))
        .collect()
}

fn vector(a: f64, b: f64) -> Literal {
    Literal::List(vec![Literal::Float(a), Literal::Float(b)])
}

async fn rows(db: &omnigraph::Session, src: &str, name: &str, p: &ParamMap) -> Vec<Value> {
    let result = query_main(db, src, name, p)
        .await
        .unwrap_or_else(|e| panic!("{src}\n=> {e}"));
    let bytes = result.to_json_bytes().unwrap();
    serde_json::from_slice::<Vec<Value>>(&bytes).unwrap()
}

fn record(name: &str, value: &impl serde::Serialize) {
    let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../target/spike-gq3");
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join(name), serde_json::to_string_pretty(value).unwrap()).unwrap();
}

#[tokio::test]
async fn retrieve_matches_the_edition_2_ranking_on_lance_indexes() {
    let dir = tempfile::tempdir().unwrap();
    let db = graph(&dir).await;
    let p = params(&[
        ("v", vector(1.0, 0.0)),
        ("t", Literal::String("graph engine".into())),
    ]);
    let pairs = [
        (
            "query q($v: Vector(2), $t: String) { match { $d: Doc } return { $d.slug, nearest($d.embedding, $v) as score } order { nearest($d.embedding, $v) } limit 3 }",
            "gq 3;\nquery q($v: Vector(2), $t: String) { match { $d: Doc } retrieve { $d.vec: nearest($d.embedding, $v) } return { $d.slug, $d.vec as score } order { $d.vec } limit 3 }",
        ),
        (
            "query q($v: Vector(2), $t: String) { match { $d: Doc } return { $d.slug, bm25($d.body, $t) as score } order { bm25($d.body, $t) desc } limit 4 }",
            "gq 3;\nquery q($v: Vector(2), $t: String) { match { $d: Doc } retrieve { $d.lex: bm25($d.body, $t) } return { $d.slug, $d.lex as score } order { $d.lex desc } limit 4 }",
        ),
        (
            "query q($v: Vector(2), $t: String) { match { $d: Doc } return { $d.slug } order { rrf(nearest($d.embedding, $v), bm25($d.body, $t), 60) } limit 4 }",
            "gq 3;\nquery q($v: Vector(2), $t: String) { match { $d: Doc } retrieve { $d.vec: nearest($d.embedding, $v), $d.lex: bm25($d.body, $t) } return { $d.slug } order { rrf($d.vec, $d.lex, k: 60) } limit 4 }",
        ),
        (
            // A filter in `match` scopes the ranking (prefilter), as today.
            "query q($v: Vector(2), $t: String) { match { $d: Doc { topic: \"db\" } } return { $d.slug } order { nearest($d.embedding, $v) } limit 2 }",
            "gq 3;\nquery q($v: Vector(2), $t: String) { match { $d: Doc { topic: \"db\" } } retrieve { $d.vec: nearest($d.embedding, $v) } return { $d.slug } order { $d.vec } limit 2 }",
        ),
        (
            // A traversal scopes the ranking.
            "query q($v: Vector(2), $t: String) { match { $p: Person { name: \"alice\" } $p wrote $d } return { $d.slug } order { bm25($d.body, $t) desc } limit 5 }",
            "gq 3;\nquery q($v: Vector(2), $t: String) { match { $p: Person { name: \"alice\" } $p wrote $d } retrieve { $d.lex: bm25($d.body, $t) } return { $d.slug } order { $d.lex desc } limit 5 }",
        ),
    ];
    let mut evidence = Vec::new();
    for (two, three) in pairs {
        let a = rows(&db, two, "q", &p).await;
        let b = rows(&db, three, "q", &p).await;
        assert_eq!(a, b, "\n{two}\n{three}");
        assert!(!a.is_empty());
        evidence.push(serde_json::json!({ "edition2": two, "edition3": three, "rows": b }));
    }
    record("retrieve_rows.json", &evidence);
}

#[tokio::test]
async fn arm_columns_keep_todays_score_types_and_names() {
    let dir = tempfile::tempdir().unwrap();
    let db = graph(&dir).await;
    let p = params(&[
        ("v", vector(1.0, 0.0)),
        ("t", Literal::String("graph".into())),
    ]);
    let src = "gq 3;\nquery q($v: Vector(2), $t: String) { match { $d: Doc } retrieve { $d.vec: nearest($d.embedding, $v) } return { $d.slug, $d.vec } order { $d.vec } limit 2 }";
    let result = query_main(&db, src, "q", &p).await.unwrap();
    let fields: Vec<(String, String)> = result
        .schema()
        .fields()
        .iter()
        .map(|f| {
            (
                f.name().clone(),
                format!(
                    "{:?}{}",
                    f.data_type(),
                    if f.is_nullable() { "?" } else { "" }
                ),
            )
        })
        .collect();
    // The column is today's: named `d._distance`, typed Float32 (the RFC's
    // freeze table says `F64?` and names the column after the arm).
    assert_eq!(fields[1].0, "d._distance");
    assert!(fields[1].1.starts_with("Float32"), "{fields:?}");
    let src = "gq 3;\nquery q($v: Vector(2), $t: String) { match { $d: Doc } retrieve { $d.lex: bm25($d.body, $t) } return { $d.slug, $d.lex } order { $d.lex desc } limit 2 }";
    let result = query_main(&db, src, "q", &p).await.unwrap();
    let lex = result.schema().field(1).clone();
    assert_eq!(lex.name(), "d._score");
    assert!(format!("{:?}", lex.data_type()).starts_with("Float32"));
    record(
        "arm_columns.json",
        &serde_json::json!({ "nearest": fields, "bm25": [lex.name(), format!("{:?}", lex.data_type())] }),
    );
}

#[tokio::test]
async fn having_filters_groups_and_scores() {
    let dir = tempfile::tempdir().unwrap();
    let db = graph(&dir).await;
    let none = ParamMap::new();
    let src = "gq 3;\nquery q() { match { $p: Person $p wrote $d } return { $p.name, count($d) as docs } having { docs > 1 } order { $p.name } }";
    let got = rows(&db, src, "q", &none).await;
    assert_eq!(
        got,
        vec![
            serde_json::json!({"p.name": "alice", "docs": 3}),
            serde_json::json!({"p.name": "carol", "docs": 2})
        ]
    );
    // The same filter written against the aggregate, not its alias.
    let src2 = "gq 3;\nquery q() { match { $p: Person $p wrote $d } return { $p.name, count($d) as docs } having { count($d) >= 3 } order { $p.name } }";
    assert_eq!(
        rows(&db, src2, "q", &none).await,
        vec![serde_json::json!({"p.name": "alice", "docs": 3})]
    );
    // A group key beside an aggregate in `having` is refused: today's rule
    // for a Boolean return item (T58) compares aggregates with constants
    // only, so `having` needs a type rule of its own.
    let src3 = "gq 3;\nquery q() { match { $p: Person $p wrote $d } return { $p.name, count($d) as docs } having { count($d) >= 2 and $p.name != \"carol\" } order { $p.name } }";
    let error = query_main(&db, src3, "q", &none)
        .await
        .unwrap_err()
        .to_string();
    assert!(error.contains("T67") && error.contains("T58"), "{error}");

    // A score threshold over one `nearest` arm, ordered by that arm: the
    // predicate is monotone in the ranking key, so filtering the window
    // equals windowing the filtered population.
    let p = params(&[
        ("v", vector(1.0, 0.0)),
        ("t", Literal::String("graph engine".into())),
    ]);
    let src = "gq 3;\nquery q($v: Vector(2), $t: String) { match { $d: Doc } retrieve { $d.vec: nearest($d.embedding, $v) } return { $d.slug, $d.vec as dist } having { $d.vec < 0.2 } order { $d.vec } limit 3 }";
    let got = rows(&db, src, "q", &p).await;
    let slugs: Vec<_> = got.iter().map(|r| r["d.slug"].clone()).collect();
    // Squared L2: d1 0, d2 0.02, d3 0.18, d5 0.5.
    assert_eq!(
        slugs,
        vec![Value::from("d1"), Value::from("d2"), Value::from("d3")]
    );

    // A lower bound is not monotone with the window. A post-window filter
    // would return 2 of the 3 requested rows; the engine's overfetch ladder
    // treats the `having` filter as any filter above a ranking and widens
    // the search until `limit` rows pass, so the result is the top 3 of the
    // passing population: the rows Lance's `distance_range` would return.
    let src = "gq 3;\nquery q($v: Vector(2), $t: String) { match { $d: Doc } retrieve { $d.vec: nearest($d.embedding, $v) } return { $d.slug, $d.vec as dist } having { $d.vec > 0.01 } order { $d.vec } limit 3 }";
    let post = rows(&db, src, "q", &p).await;
    let population = rows(
        &db,
        "gq 3;\nquery q($v: Vector(2), $t: String) { match { $d: Doc } retrieve { $d.vec: nearest($d.embedding, $v) } return { $d.slug, $d.vec as dist } having { $d.vec > 0.01 } order { $d.vec } limit 6 }",
        "q",
        &p,
    )
    .await;
    record(
        "having_window.json",
        &serde_json::json!({ "limit_3_post_window": post, "passing_population": population }),
    );
    assert_eq!(post, population[..3].to_vec());

    // Under `rrf`, an arm's own score cannot be projected or filtered: the
    // fusion carries only the fused score (today's T33).
    let src = "gq 3;\nquery q($v: Vector(2), $t: String) { match { $d: Doc } retrieve { $d.vec: nearest($d.embedding, $v), $d.lex: bm25($d.body, $t) } return { $d.slug, $d.vec as dist } having { $d.vec < 0.6 } order { rrf($d.vec, $d.lex, k: 60) } limit 3 }";
    let error = query_main(&db, src, "q", &p).await.unwrap_err().to_string();
    assert!(error.contains("T33"), "{error}");
}

#[tokio::test]
async fn limit_binds_a_parameter() {
    let dir = tempfile::tempdir().unwrap();
    let db = graph(&dir).await;
    let src = "gq 3;\nquery q($v: Vector(2), $k: I64) { match { $d: Doc } retrieve { $d.vec: nearest($d.embedding, $v) } return { $d.slug } order { $d.vec } limit $k }";
    for k in [1, 2, 4] {
        let p = params(&[("v", vector(1.0, 0.0)), ("k", Literal::Integer(k))]);
        assert_eq!(rows(&db, src, "q", &p).await.len(), k as usize);
    }
    let p = params(&[("v", vector(1.0, 0.0)), ("k", Literal::Integer(-1))]);
    assert!(query_main(&db, src, "q", &p).await.is_err());
}

#[tokio::test]
async fn mutation_keyword_writes_and_query_keyword_refuses_a_write() {
    let dir = tempfile::tempdir().unwrap();
    let db = graph(&dir).await;
    let p = params(&[("n", Literal::String("dave".into()))]);
    mutate_main(
        &db,
        "gq 3;\nmutation add($n: String) { insert Person { name: $n, age: null } }",
        "add",
        &p,
    )
    .await
    .unwrap();
    let got = rows(
        &db,
        "gq 3;\nquery q($n: String) { match { $p: Person { name: $n } } return { $p.name } }",
        "q",
        &p,
    )
    .await;
    assert_eq!(got.len(), 1);
    let error = mutate_main(
        &db,
        "gq 3;\nquery add($n: String) { insert Person { name: $n } }",
        "add",
        &p,
    )
    .await
    .unwrap_err()
    .to_string();
    assert!(error.contains("T62"), "{error}");
}

#[tokio::test]
async fn an_incoming_triple_and_its_swap_return_the_same_rows() {
    let dir = tempfile::tempdir().unwrap();
    let db = graph(&dir).await;
    let none = ParamMap::new();
    let two = rows(
        &db,
        "query q() { match { $c: Company $c worksAt $p } return { $p.name } order { $p.name } }",
        "q",
        &none,
    )
    .await;
    let three = rows(&db, "gq 3;\nquery q() { match { $c: Company $p worksAt $c } return { $p.name } order { $p.name } }", "q", &none).await;
    assert_eq!(two, three);
    let error = query_main(
        &db,
        "gq 3;\nquery q() { match { $c: Company $c worksAt $p } return { $p.name } }",
        "q",
        &none,
    )
    .await
    .unwrap_err()
    .to_string();
    assert!(error.contains("T68"), "{error}");
}

#[tokio::test]
async fn explain_pushes_filters_and_limits_into_lance_and_places_having() {
    let dir = tempfile::tempdir().unwrap();
    let db = graph(&dir).await;
    let p = params(&[
        ("v", vector(1.0, 0.0)),
        ("t", Literal::String("graph".into())),
        ("k", Literal::Integer(2)),
    ]);
    let src = "gq 3;\nquery q($v: Vector(2), $t: String, $k: I64) { match { $d: Doc { topic: \"db\" } } retrieve { $d.vec: nearest($d.embedding, $v) } return { $d.slug, $d.vec as dist } having { $d.vec < 0.5 } order { $d.vec } limit $k }";
    let explain = db.explain_query("main", src, "q", &p).await.unwrap();
    let text = explain.to_string();
    record("explain_retrieve_having.json", &explain);
    assert!(text.contains("Nearest"), "{text}");
    assert!(text.contains("\"Filter\""), "{text}");
    let src = "gq 3;\nquery q() { match { $p: Person $p wrote $d } return { $p.name, count($d) as docs } having { docs > 1 } order { docs desc } limit 5 }";
    let explain = db
        .explain_query("main", src, "q", &ParamMap::new())
        .await
        .unwrap();
    record("explain_aggregate_having.json", &explain);
    assert!(explain.to_string().contains("Aggregate"));
}
