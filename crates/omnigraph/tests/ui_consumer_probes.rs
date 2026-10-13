//! Probes for UI components bound to interfaces (RFC 2026-10-07 review).
//! Each probe prints `PROBE <id>: <outcome>`; run with
//! `cargo test -p omnigraph-engine --test ui_consumer_probes -- --nocapture --test-threads 1`.

mod helpers;

use std::time::Instant;

use omnigraph::Session;
use omnigraph::db::Omnigraph;
use omnigraph::loader::LoadMode;
use omnigraph_compiler::ir::ParamMap;
use omnigraph_compiler::query::ast::Literal;

use helpers::*;

fn p(name: &str, v: &str) -> ParamMap {
    let mut params = ParamMap::new();
    params.insert(name.to_string(), Literal::String(v.to_string()));
    params
}

fn rows(result: &omnigraph_compiler::result::QueryResult) -> Vec<String> {
    let json = result.to_rust_json().unwrap();
    json.as_array()
        .unwrap()
        .iter()
        .map(|row| {
            let sorted: std::collections::BTreeMap<_, _> = row.as_object().unwrap().iter().collect();
            serde_json::to_string(&sorted).unwrap()
        })
        .collect()
}

fn shape(result: &omnigraph_compiler::result::QueryResult) -> String {
    result
        .schema()
        .fields()
        .iter()
        .map(|f| format!("{}:{}{}", f.name(), f.data_type(), if f.is_nullable() { "?" } else { "" }))
        .collect::<Vec<_>>()
        .join(", ")
}

async fn init(dir: &tempfile::TempDir, schema: &str) -> Result<Session, String> {
    Omnigraph::init(dir.path().to_str().unwrap(), schema)
        .await
        .map(session)
        .map_err(|e| e.to_string())
}

async fn run(db: &Session, src: &str, name: &str, params: &ParamMap) -> String {
    match query_main(db, src, name, params).await {
        Ok(result) => format!("rows={:?} shape=[{}]", rows(&result), shape(&result)),
        Err(error) => format!("ERROR {error}"),
    }
}

const SCHED: &str = r#"
interface Schedulable {
    slug: String @key
    title: String
    startsAt: DateTime
    endsAt: DateTime?
    rank: I32
}
node Meeting implements Schedulable {
    room: String?
}
node Task implements Schedulable {
    status: enum(todo, done)
}
"#;

const SCHED_DATA: &str = r#"{"type":"Meeting","data":{"slug":"m1","title":"Standup","startsAt":"2026-10-12T09:00:00Z","endsAt":"2026-10-12T09:15:00Z","rank":2}}
{"type":"Meeting","data":{"slug":"m2","title":"Review","startsAt":"2026-10-12T14:00:00Z","rank":1}}
{"type":"Task","data":{"slug":"m1","title":"Ship","startsAt":"2026-10-12T11:00:00Z","rank":2,"status":"todo"}}
{"type":"Task","data":{"slug":"t2","title":"Write","startsAt":"2026-10-12T08:00:00Z","rank":1,"status":"done"}}
"#;

const SCHED_QUERIES: &str = r#"
query calendar() {
    match { $e: Schedulable }
    return { $e.@type as type, $e.@id as id, $e.title as title, $e.startsAt as start, $e.endsAt as end }
    order { $e.startsAt asc }
}
query first_two() {
    match { $e: Schedulable }
    return { $e.@type as type, $e.slug as slug, $e.startsAt as start }
    order { $e.startsAt asc }
    limit 2
}
query by_rank_limit() {
    match { $e: Schedulable }
    return { $e.@type as type, $e.slug as slug, $e.rank as rank }
    order { $e.rank asc }
    limit 3
}
query count_by_type() {
    match { $e: Schedulable }
    return { $e.@type as type, count($e) as n }
}
query count_by_rank() {
    match { $e: Schedulable }
    return { $e.rank as rank, count($e) as n }
}
query only_tasks() {
    match {
        $e: Schedulable
        $e.@type = "Task"
    }
    return { $e.slug as slug }
}
query order_by_type() {
    match { $e: Schedulable }
    return { $e.@type as type, $e.slug as slug }
    order { $e.@type desc, $e.slug asc }
}
query after_ten($t: DateTime) {
    match {
        $e: Schedulable
        $e.startsAt > $t
    }
    return { $e.@type as type, $e.slug as slug }
}
"#;

#[tokio::test]
async fn q1_result_shape_is_stable_when_a_member_is_added() {
    let dir = tempfile::tempdir().unwrap();
    let db = init(&dir, SCHED).await.unwrap();
    db.load_jsonl(SCHED_DATA, LoadMode::Overwrite).await.unwrap();
    println!("PROBE q1.before: {}", run(&db, SCHED_QUERIES, "calendar", &ParamMap::new()).await);
    let desired = format!(
        "{SCHED}\nnode Deadline implements Schedulable {{\n    hard: Bool?\n}}\n"
    );
    match db.apply_schema(&desired).await {
        Ok(_) => println!("PROBE q1.apply_new_member: ok"),
        Err(e) => println!("PROBE q1.apply_new_member: ERROR {e}"),
    }
    println!("PROBE q1.after: {}", run(&db, SCHED_QUERIES, "calendar", &ParamMap::new()).await);
}

#[tokio::test]
async fn q1_member_property_variance() {
    let cases = [
        (
            "member_nullable_where_interface_required",
            "interface I { slug: String @key\n title: String }\nnode A implements I { title: String? }\nnode B implements I { x: I32? }",
        ),
        (
            "member_required_where_interface_nullable",
            "interface I { slug: String @key\n title: String? }\nnode A implements I { title: String }\nnode B implements I { x: I32? }",
        ),
        (
            "member_enum_where_interface_string",
            "interface I { slug: String @key\n status: String }\nnode A implements I { status: enum(todo, done) }\nnode B implements I { x: I32? }",
        ),
        (
            "member_other_scalar",
            "interface I { slug: String @key\n rank: I32 }\nnode A implements I { rank: I64 }\nnode B implements I { x: I32? }",
        ),
        (
            "member_redeclares_identically",
            "interface I { slug: String @key\n title: String }\nnode A implements I { title: String }\nnode B implements I { x: I32? }",
        ),
        (
            "member_redeclares_with_extra_annotation",
            "interface I { slug: String @key\n title: String }\nnode A implements I { title: String @index }\nnode B implements I { x: I32? }",
        ),
    ];
    for (label, schema) in cases {
        let dir = tempfile::tempdir().unwrap();
        match init(&dir, schema).await {
            Ok(db) => {
                let shape_q = "query q() { match { $x: I } return { $x.@type as type, $x.slug as slug } }";
                let first_prop = if schema.contains("title") { "title" } else if schema.contains("status") { "status" } else { "rank" };
                let q = format!(
                    "query q() {{ match {{ $x: I }} return {{ $x.@type as type, $x.{first_prop} as v }} }}"
                );
                let _ = shape_q;
                println!("PROBE q1.{label}: accepted; {}", run(&db, &q, "q", &ParamMap::new()).await);
            }
            Err(e) => println!("PROBE q1.{label}: refused: {e}"),
        }
    }
}

const BASE: &str = "interface Named {\n    slug: String @key\n    title: String\n}\nnode Person implements Named {\n    email: String?\n}\nnode Organization implements Named {\n    website: String?\n}\n";

#[tokio::test]
async fn q2_interface_property_migrations() {
    let inherited = "interface Named {\n    slug: String @key\n}\nnode Person implements Named {\n    email: String?\n}\n";
    let dir = tempfile::tempdir().unwrap();
    match init(&dir, inherited).await {
        Ok(db) => {
            let q = "query q() { match { $x: Person } return { $x.slug as slug } }";
            println!("PROBE q2.member_inherits_without_redeclaring: accepted; {}", run(&db, q, "q", &ParamMap::new()).await);
        }
        Err(e) => println!("PROBE q2.member_inherits_without_redeclaring: refused: {e}"),
    }
    let variants = [
        ("add_nullable_interface_property", BASE.replace("    title: String\n}\nnode Person", "    title: String\n    nickname: String?\n}\nnode Person")),
        ("add_required_interface_property", BASE.replace("    title: String\n}\nnode Person", "    title: String\n    code: String\n}\nnode Person")),
        ("remove_interface_property", BASE.replace("    title: String\n}\nnode Person", "}\nnode Person")),
        ("rename_interface_property", BASE.replace("    title: String\n}\nnode Person", "    name: String @rename_from(\"title\")\n}\nnode Person")),
        ("interface_property_to_nullable", BASE.replace("    title: String\n}\nnode Person", "    title: String?\n}\nnode Person")),
        ("member_redeclares_conflicting", BASE.replace("    email: String?\n}", "    email: String?\n    title: I32\n}")),
        ("add_implements_to_existing_node", format!("{BASE}node Place {{\n    slug: String @key\n    title: String\n}}\n").replace("node Place {", "node Place implements Named {")),
    ];
    for (label, desired) in variants {
        let dir = tempfile::tempdir().unwrap();
        let db = init(&dir, &format!("{BASE}node Place {{\n    slug: String @key\n    title: String\n}}\n")).await.unwrap();
        let desired = if label == "add_implements_to_existing_node" {
            desired
        } else {
            format!("{desired}node Place {{\n    slug: String @key\n    title: String\n}}\n")
        };
        match db.plan_schema(&desired).await {
            Ok(plan) => println!("PROBE q2.{label}: supported={} steps={:?}", plan.supported, plan.steps),
            Err(e) => println!("PROBE q2.{label}: ERROR {e}"),
        }
    }
}

#[tokio::test]
async fn q4_q5_q6_reads() {
    let dir = tempfile::tempdir().unwrap();
    let db = init(&dir, SCHED).await.unwrap();
    db.load_jsonl(SCHED_DATA, LoadMode::Overwrite).await.unwrap();
    for name in ["first_two", "count_by_type", "count_by_rank", "only_tasks", "order_by_type"] {
        println!("PROBE q4q5.{name}: {}", run(&db, SCHED_QUERIES, name, &ParamMap::new()).await);
    }
    // Ties on rank across types, repeated: is the order deterministic?
    for i in 0..3 {
        println!("PROBE q4.by_rank_limit#{i}: {}", run(&db, SCHED_QUERIES, "by_rank_limit", &ParamMap::new()).await);
    }
    let mut params = ParamMap::new();
    params.insert("t".to_string(), Literal::DateTime("2026-10-12T10:00:00Z".to_string()));
    println!("PROBE q6.after_ten: {}", run(&db, SCHED_QUERIES, "after_ten", &params).await);
}

#[tokio::test]
async fn q6_index_on_one_member_only() {
    let schema = SCHED.replace("    room: String?\n", "    room: String?\n    rank: I32 @index\n");
    let dir = tempfile::tempdir().unwrap();
    match init(&dir, &schema).await {
        Ok(db) => {
            db.load_jsonl(SCHED_DATA, LoadMode::Overwrite).await.unwrap();
            db.ensure_indices().await.unwrap();
            let q = "query q() { match { $e: Schedulable\n $e.rank = 2 } return { $e.@type as type, $e.slug as slug } }";
            println!("PROBE q6.partial_index: {}", run(&db, q, "q", &ParamMap::new()).await);
        }
        Err(e) => println!("PROBE q6.partial_index: schema refused: {e}"),
    }
}

#[tokio::test]
async fn q8_wide_interface_local_fs() {
    let members: usize = std::env::var("PROTO_MEMBERS").ok().and_then(|v| v.parse().ok()).unwrap_or(75);
    let mut schema = String::from("interface Entity {\n    slug: String @key\n    name: String?\n}\n");
    let mut data = String::new();
    for i in 0..members {
        schema.push_str(&format!("node T{i:02} implements Entity {{\n    v{i}: I32?\n}}\n"));
        data.push_str(&format!("{{\"type\":\"T{i:02}\",\"data\":{{\"slug\":\"k{i}\"}}}}\n"));
    }
    let dir = tempfile::tempdir().unwrap();
    let uri = dir.path().to_str().unwrap().to_string();
    {
        let db = init(&dir, &schema).await.unwrap();
        db.load_jsonl(&data, LoadMode::Overwrite).await.unwrap();
        db.ensure_indices().await.unwrap();
    }
    let wide = "query q($s: String) { match { $x: Entity { slug: $s } } return { $x.@type as type } }";
    let narrow = "query q($s: String) { match { $x: T40 { slug: $s } } return { $x.slug as slug } }";
    for (label, q) in [("entity_wide", wide), ("one_type", narrow)] {
        let mut colds = Vec::new();
        let mut warms = Vec::new();
        for _ in 0..5 {
            let db = session(Omnigraph::open(&uri).await.unwrap());
            let t = Instant::now();
            let first = run(&db, q, "q", &p("s", "k40")).await;
            colds.push(t.elapsed());
            assert!(first.contains("40"), "{first}");
            for _ in 0..10 {
                let t = Instant::now();
                let _ = run(&db, q, "q", &p("s", "k40")).await;
                warms.push(t.elapsed());
            }
        }
        colds.sort();
        warms.sort();
        println!(
            "PROBE q8.{label}: cold_median={:?} warm_median={:?} warm_p90={:?} (cold n={}, warm n={})",
            colds[colds.len() / 2],
            warms[warms.len() / 2],
            warms[warms.len() * 9 / 10],
            colds.len(),
            warms.len()
        );
    }
}

#[tokio::test]
async fn q9_multi_table_update_zero_match() {
    let dir = tempfile::tempdir().unwrap();
    let db = init(&dir, BASE).await.unwrap();
    db.load_jsonl(
        "{\"type\":\"Person\",\"data\":{\"slug\":\"alice\",\"title\":\"A\"}}\n{\"type\":\"Organization\",\"data\":{\"slug\":\"acme\",\"title\":\"B\"}}\n",
        LoadMode::Overwrite,
    )
    .await
    .unwrap();
    let before_p = pinned_version(db.db(), "main", "node:Person").await;
    let before_o = pinned_version(db.db(), "main", "node:Organization").await;
    let before_m = version_main(db.db()).await.unwrap();
    // Two statements, as an interface-wide update would fan out; only Person matches.
    let src = "query both($s: String) {\n  update Person set { title: \"X\" } where slug = $s\n  update Organization set { title: \"X\" } where slug = $s\n}";
    match mutate_main(&db, src, "both", &p("s", "alice")).await {
        Ok(r) => println!("PROBE q9.two_statement_update: affected_nodes={}", r.affected_nodes),
        Err(e) => println!("PROBE q9.two_statement_update: ERROR {e}"),
    }
    let after_p = pinned_version(db.db(), "main", "node:Person").await;
    let after_o = pinned_version(db.db(), "main", "node:Organization").await;
    let after_m = version_main(db.db()).await.unwrap();
    println!(
        "PROBE q9.versions: Person {before_p}->{after_p} Organization {before_o}->{after_o} manifest {before_m}->{after_m}"
    );
    let single = "query one($s: String) { update Organization set { title: \"Y\" } where slug = $s }";
    let before_o = pinned_version(db.db(), "main", "node:Organization").await;
    let before_m = version_main(db.db()).await.unwrap();
    let r = mutate_main(&db, single, "one", &p("s", "nobody")).await;
    let after_o = pinned_version(db.db(), "main", "node:Organization").await;
    let after_m = version_main(db.db()).await.unwrap();
    println!(
        "PROBE q9.zero_match_single: result={:?} Organization {before_o}->{after_o} manifest {before_m}->{after_m}",
        r.map(|r| r.affected_nodes).map_err(|e| e.to_string())
    );
}

#[tokio::test]
async fn q14_interface_and_node_name_collision() {
    let both = "interface Event {\n    slug: String @key\n}\nnode Event {\n    slug: String @key\n}\n";
    let dir = tempfile::tempdir().unwrap();
    match init(&dir, both).await {
        Ok(_) => println!("PROBE q14.same_name_interface_and_node: accepted"),
        Err(e) => println!("PROBE q14.same_name_interface_and_node: refused: {e}"),
    }
    let fork = "node Event {\n    slug: String @key\n    title: String?\n}\n";
    let dir = tempfile::tempdir().unwrap();
    let db = init(&dir, fork).await.unwrap();
    db.load_jsonl("{\"type\":\"Event\",\"data\":{\"slug\":\"e1\"}}\n", LoadMode::Overwrite)
        .await
        .unwrap();
    let adopt = "interface Event {\n    slug: String @key\n}\nnode CalendarEvent @rename_from(\"Event\") implements Event {\n    title: String?\n}\n";
    match db.plan_schema(adopt).await {
        Ok(plan) => println!("PROBE q14.rename_node_and_reuse_name_for_interface: supported={} steps={:?}", plan.supported, plan.steps),
        Err(e) => println!("PROBE q14.rename_node_and_reuse_name_for_interface: ERROR {e}"),
    }
    let q = "query q() { match { $e: Event } return { $e.slug as slug } }";
    println!("PROBE q14.query_before: {}", run(&db, q, "q", &ParamMap::new()).await);
    match db.apply_schema(adopt).await {
        Ok(_) => println!("PROBE q14.apply: ok; query_after: {}", run(&db, q, "q", &ParamMap::new()).await),
        Err(e) => println!("PROBE q14.apply: ERROR {e}"),
    }
}

#[tokio::test]
async fn q1_redeclared_key_property_keeps_or_drops_key() {
    for (label, schema) in [
        ("inherited", "interface I {\n    slug: String @key\n}\nnode A implements I {\n    x: I32?\n}\n"),
        ("redeclared", "interface I {\n    slug: String @key\n}\nnode A implements I {\n    slug: String\n    x: I32?\n}\n"),
    ] {
        let dir = tempfile::tempdir().unwrap();
        let db = match init(&dir, schema).await {
            Ok(db) => db,
            Err(e) => {
                println!("PROBE q1.key_{label}: schema refused: {e}");
                continue;
            }
        };
        let line = "{\"type\":\"A\",\"data\":{\"slug\":\"x\"}}\n";
        let first = db.load_jsonl(line, LoadMode::Merge).await.map(|_| ()).map_err(|e| e.to_string());
        let second = db.load_jsonl(line, LoadMode::Merge).await.map(|_| ()).map_err(|e| e.to_string());
        let n = count_rows(db.db(), "node:A").await;
        println!("PROBE q1.key_{label}: first={first:?} second={second:?} rows={n}");
    }
}

#[tokio::test]
async fn q5_bare_count_over_interface() {
    let dir = tempfile::tempdir().unwrap();
    let db = init(&dir, SCHED).await.unwrap();
    db.load_jsonl(SCHED_DATA, LoadMode::Overwrite).await.unwrap();
    let q = "query q() { match { $e: Schedulable } return { count($e) as n } }";
    println!("PROBE q5.bare_count: {}", run(&db, q, "q", &ParamMap::new()).await);
    let q = "query q() { match { $e: Schedulable\n $e.rank = 2 } return { count($e) as n } }";
    println!("PROBE q5.filtered_count: {}", run(&db, q, "q", &ParamMap::new()).await);
}

const VEC_SCHED: &str = r#"
interface Schedulable {
    slug: String @key
    title: String
    rank: I32 @index
    embedding: Vector(3)?
}
node Meeting implements Schedulable {
    room: String?
}
node Task implements Schedulable {
    status: String?
}
"#;

const VEC_DATA: &str = r#"{"type":"Meeting","data":{"slug":"m1","title":"Standup","rank":2,"embedding":[1.0,0.0,0.0]}}
{"type":"Meeting","data":{"slug":"m2","title":"Review","rank":1,"embedding":[0.0,1.0,0.0]}}
{"type":"Task","data":{"slug":"m1","title":"Ship","rank":3,"embedding":[0.0,0.0,1.0]}}
{"type":"Task","data":{"slug":"t2","title":"Write","rank":0,"embedding":[1.0,1.0,0.0]}}
"#;

#[tokio::test]
async fn q4_q6_rebase_only_paths() {
    let dir = tempfile::tempdir().unwrap();
    let db = init(&dir, VEC_SCHED).await.unwrap();
    db.load_jsonl(VEC_DATA, LoadMode::Overwrite).await.unwrap();
    db.ensure_indices().await.unwrap();
    // Late materialization by row address: ordered + limited, returning a vector.
    let late = "query q() { match { $e: Schedulable } return { $e.@type as type, $e.slug as slug, $e.embedding as v } order { $e.rank asc } limit 2 }";
    println!("PROBE q4.late_materialization: {}", run(&db, late, "q", &ParamMap::new()).await);
    let single = "query q() { match { $e: Meeting } return { $e.slug as slug, $e.embedding as v } order { $e.rank asc } limit 1 }";
    println!("PROBE q4.late_materialization_single_type: {}", run(&db, single, "q", &ParamMap::new()).await);
    // Per-arm index use: explain an indexed equality over the interface and over one member.
    for (label, q) in [
        ("interface", "explain query q() { match { $e: Schedulable\n $e.rank = 2 } return { $e.slug as slug } }"),
        ("member", "explain query q() { match { $e: Meeting\n $e.rank = 2 } return { $e.slug as slug } }"),
    ] {
        match query_main(&db, q, "q", &ParamMap::new()).await {
            Ok(result) => {
                let text = result.to_rust_json().unwrap().to_string();
                let access: Vec<&str> = ["sequential", "Sequential", "indexed", "Indexed", "scalar_index", "ScalarIndex", "index"]
                    .into_iter()
                    .filter(|needle| text.contains(needle))
                    .collect();
                println!("PROBE q6.explain_{label}: access_words={access:?}");
                let snippet: String = text.clone();
                println!("PROBE q6.explain_{label}_text: {snippet}");
            }
            Err(e) => println!("PROBE q6.explain_{label}: ERROR {e}"),
        }
    }
    let q = "query q() { match { $e: Schedulable\n $e.rank = 2 } return { $e.@type as type, $e.slug as slug } }";
    println!("PROBE q6.indexed_equality_rows: {}", run(&db, q, "q", &ParamMap::new()).await);
}

#[tokio::test]
async fn q8_request_counts() {
    use helpers::cost::{cost_harness, measure};
    for members in [60usize, 75] {
        let mut schema = String::from("interface Entity {\n    slug: String @key\n    name: String?\n}\n");
        let mut data = String::new();
        for i in 0..members {
            schema.push_str(&format!("node T{i:02} implements Entity {{\n    v{i}: I32?\n}}\n"));
            data.push_str(&format!("{{\"type\":\"T{i:02}\",\"data\":{{\"slug\":\"k{i}\"}}}}\n"));
        }
        let dir = tempfile::tempdir().unwrap();
        let uri = dir.path().to_str().unwrap().to_string();
        {
            let db = init(&dir, &schema).await.unwrap();
            db.load_jsonl(&data, LoadMode::Overwrite).await.unwrap();
            db.ensure_indices().await.unwrap();
        }
        let wide = "query q($s: String) { match { $x: Entity { slug: $s } } return { $x.@type as type } }";
        let narrow = "query q($s: String) { match { $x: T40 { slug: $s } } return { $x.slug as slug } }";
        for (label, q) in [("entity_wide", wide), ("one_type", narrow)] {
            let uri = uri.clone();
            let (cold, warm) = cost_harness(async move {
                let db = session(Omnigraph::open(&uri).await.unwrap());
                let (r, cold) = measure(query_main(&db, q, "q", &p("s", "k40"))).await;
                r.unwrap();
                let (r, warm) = measure(query_main(&db, q, "q", &p("s", "k40"))).await;
                r.unwrap();
                (cold, warm)
            })
            .await;
            println!(
                "PROBE q8.requests members={members} {label}: cold data_open={} data_reads={} opener={} scan={} manifest={} | warm data_open={} data_reads={} opener={} scan={} manifest={}",
                cold.data_open_count, cold.data_reads, cold.data_opener_reads, cold.data_scan_reads, cold.manifest_reads,
                warm.data_open_count, warm.data_reads, warm.data_opener_reads, warm.data_scan_reads, warm.manifest_reads
            );
        }
    }
}

/// Two-process cold measurement: `PROTO_SETUP=1` builds the graph at
/// `PROTO_GRAPH_DIR`; a later process without it measures a truly cold read.
#[tokio::test]
#[ignore]
async fn q8_cold_process_requests() {
    use helpers::cost::{cost_harness, measure};
    let root = std::env::var("PROTO_GRAPH_DIR").expect("PROTO_GRAPH_DIR");
    let uri = format!("{root}/graph");
    if std::env::var("PROTO_SETUP").is_ok() {
        let mut schema = String::from("interface Entity {\n    slug: String @key\n    name: String?\n}\n");
        let mut data = String::new();
        for i in 0..75 {
            schema.push_str(&format!("node T{i:02} implements Entity {{\n    v{i}: I32?\n}}\n"));
            for r in 0..200 {
                data.push_str(&format!("{{\"type\":\"T{i:02}\",\"data\":{{\"slug\":\"k{i}-{r}\"}}}}\n"));
            }
        }
        std::fs::create_dir_all(&uri).unwrap();
        let db = session(Omnigraph::init(&uri, &schema).await.unwrap());
        db.load_jsonl(&data, LoadMode::Overwrite).await.unwrap();
        db.ensure_indices().await.unwrap();
        println!("PROBE q8.cold_setup: done");
        return;
    }
    let label = std::env::var("PROTO_LABEL").unwrap_or_default();
    let q = if label == "one_type" {
        "query q($s: String) { match { $x: T40 { slug: $s } } return { $x.slug as slug } }"
    } else {
        "query q($s: String) { match { $x: Entity { slug: $s } } return { $x.@type as type } }"
    };
    let (cold, warm, cold_t, warm_t) = cost_harness(async move {
        let db = session(Omnigraph::open(&uri).await.unwrap());
        let t = Instant::now();
        let (r, cold) = measure(query_main(&db, q, "q", &p("s", "k40-7"))).await;
        let cold_t = t.elapsed();
        assert_eq!(r.unwrap().num_rows(), 1);
        let t = Instant::now();
        let (r, warm) = measure(query_main(&db, q, "q", &p("s", "k40-7"))).await;
        let warm_t = t.elapsed();
        r.unwrap();
        (cold, warm, cold_t, warm_t)
    })
    .await;
    println!(
        "PROBE q8.cold_process {label}: cold {cold_t:?} data_open={} data_reads={} opener={} scan={} | warm {warm_t:?} data_open={} data_reads={} opener={} scan={}",
        cold.data_open_count, cold.data_reads, cold.data_opener_reads, cold.data_scan_reads,
        warm.data_open_count, warm.data_reads, warm.data_opener_reads, warm.data_scan_reads
    );
}

/// Today's write reads: what a keyed node insert and an edge insert between
/// existing keyed nodes already read, the baseline for integer identity's key
/// resolution.
#[tokio::test]
async fn write_path_reads_today() {
    use helpers::cost::{cost_harness, measure};
    let schema = "node Person {\n    name: String @key\n    age: I32?\n}\nedge Knows: Person -> Person\n";
    let mut data = String::new();
    for i in 0..500 {
        data.push_str(&format!("{{\"type\":\"Person\",\"data\":{{\"name\":\"p{i}\"}}}}\n"));
    }
    let dir = tempfile::tempdir().unwrap();
    let uri = dir.path().to_str().unwrap().to_string();
    {
        let db = init(&dir, schema).await.unwrap();
        db.load_jsonl(&data, LoadMode::Overwrite).await.unwrap();
        db.ensure_indices().await.unwrap();
    }
    let node = "query q($n: String) { insert Person { name: $n } }";
    let edge = "query q($a: String, $b: String) { insert Knows { from: $a, to: $b } }";
    let update = "query q($n: String) { update Person set { age: 3 } where name = $n }";
    let (node_io, edge_io, update_io) = cost_harness(async move {
        let db = session(Omnigraph::open(&uri).await.unwrap());
        // Warm the handles once so the counts are per-write, not per-open.
        let _ = mutate_main(&db, node, "q", &p("n", "warm")).await.unwrap();
        let (r, node_io) = measure(mutate_main(&db, node, "q", &p("n", "fresh"))).await;
        r.unwrap();
        let mut params = p("a", "p1");
        params.extend(p("b", "p2"));
        let (r, edge_io) = measure(mutate_main(&db, edge, "q", &params)).await;
        r.unwrap();
        let (r, update_io) = measure(mutate_main(&db, update, "q", &p("n", "p3"))).await;
        r.unwrap();
        (node_io, edge_io, update_io)
    })
    .await;
    for (label, io) in [("keyed_node_insert", node_io), ("edge_insert", edge_io), ("keyed_update", update_io)] {
        println!(
            "PROBE writes.{label}: data_reads={} opener={} scan={} data_writes={} data_open={} manifest_reads={}",
            io.data_reads, io.data_opener_reads, io.data_scan_reads, io.data_writes, io.data_open_count, io.manifest_reads
        );
    }
}

#[tokio::test]
async fn q2_interface_enum_widening() {
    let base = "interface Stateful {\n    slug: String @key\n    status: enum(todo, done)\n}\nnode Task implements Stateful {\n    x: I32?\n}\nnode Bug implements Stateful {\n    y: I32?\n}\n";
    let dir = tempfile::tempdir().unwrap();
    let db = init(&dir, base).await.unwrap();
    let desired = base.replace("status: enum(todo, done)", "status: enum(todo, doing, done)");
    match db.plan_schema(&desired).await {
        Ok(plan) => println!("PROBE q2.widen_interface_enum: supported={} steps={:?}", plan.supported, plan.steps),
        Err(e) => println!("PROBE q2.widen_interface_enum: ERROR {e}"),
    }
}

/// Today's bulk-load reads: 1,000 keyed nodes (merge) and 1,000 edges between
/// existing keyed nodes (append), the baseline for integer identity's batched
/// key resolution.
#[tokio::test]
async fn bulk_load_reads_today() {
    use helpers::cost::{cost_harness, measure};
    let schema = "node Person {\n    name: String @key\n    age: I32?\n}\nedge Knows: Person -> Person\n";
    let mut seed = String::new();
    for i in 0..2000 {
        seed.push_str(&format!("{{\"type\":\"Person\",\"data\":{{\"name\":\"p{i}\"}}}}\n"));
    }
    let dir = tempfile::tempdir().unwrap();
    let uri = dir.path().to_str().unwrap().to_string();
    {
        let db = init(&dir, schema).await.unwrap();
        db.load_jsonl(&seed, LoadMode::Overwrite).await.unwrap();
        db.ensure_indices().await.unwrap();
    }
    let mut nodes = String::new();
    for i in 2000..3000 {
        nodes.push_str(&format!("{{\"type\":\"Person\",\"data\":{{\"name\":\"p{i}\"}}}}\n"));
    }
    let mut edges = String::new();
    for i in 0..1000 {
        edges.push_str(&format!("{{\"edge\":\"Knows\",\"from\":\"p{i}\",\"to\":\"p{}\"}}\n", i + 1000));
    }
    let (node_io, edge_io) = cost_harness(async move {
        let db = session(Omnigraph::open(&uri).await.unwrap());
        let (r, node_io) = measure(db.load_jsonl(&nodes, LoadMode::Merge)).await;
        r.unwrap();
        let (r, edge_io) = measure(db.load_jsonl(&edges, LoadMode::Append)).await;
        r.unwrap();
        (node_io, edge_io)
    })
    .await;
    for (label, io) in [("1000_keyed_nodes_merge", node_io), ("1000_edges_append", edge_io)] {
        println!(
            "PROBE bulk.{label}: data_reads={} opener={} scan={} data_writes={} data_open={} manifest_reads={}",
            io.data_reads, io.data_opener_reads, io.data_scan_reads, io.data_writes, io.data_open_count, io.manifest_reads
        );
    }
}
