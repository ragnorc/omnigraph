#![cfg(feature = "failpoints")]
#![recursion_limit = "512"]
//! Group-commit stress: focused reproductions (research,
//! `research/group-commit-stress`). Every test switches the prototype's group
//! commit on for this process (`OMNIGRAPH_GROUP_COMMIT=1`, read per call) and
//! runs serially. Each test states the RFC rule it checks; a failing test is
//! a reproduction of a prototype bug, not a regression of `main`.

mod helpers;

use std::sync::Arc;
use std::time::Duration;

use omnigraph::db::Omnigraph;
use omnigraph::seams::FailScenario;
use omnigraph::seams::catalog;
use serial_test::serial;

use helpers::{Session, collect_column_strings, mixed_params, params};

const ITEM_SCHEMA: &str = "node Item {\n    slug: String @key\n    n: I64\n}\n";
const ITEM_QUERIES: &str = r#"
query put($slug: String, $n: I64) {
    insert Item { slug: $slug, n: $n }
}
query foreign_put($slug: String, $n: I64) {
    insert Item { slug: $slug, n: $n }
    update Item set { n: 0 } where slug = "__never__"
}
"#;

const PERSON_SCHEMA: &str = "node Person {\n    name: String @key\n}\nedge Knows: Person -> Person\n";
const PERSON_QUERIES: &str = r#"
query add_person($name: String) {
    insert Person { name: $name }
}
query knows($from: String, $to: String) {
    insert Knows { from: $from, to: $to }
}
query remove_person($name: String) {
    delete Person where name = $name
}
"#;

const DOC_SCHEMA: &str = "node Doc {\n    title: String @key\n    content: Blob?\n}\n";
const DOC_QUERIES: &str = r#"
query put_doc($title: String, $content: Blob) {
    insert Doc { title: $title, content: $content }
}
"#;

fn group_commit_on() {
    // SAFETY: every test here is #[serial]; the prototype reads the switch on
    // each call.
    unsafe { std::env::set_var("OMNIGRAPH_GROUP_COMMIT", "1") };
}

async fn seeded(uri: &str, schema: &str, seed: &str) -> Arc<Session> {
    let db = helpers::session(Omnigraph::init(uri, schema).await.unwrap());
    if !seed.is_empty() {
        db.load_jsonl(seed, omnigraph::loader::LoadMode::Overwrite)
            .await
            .unwrap();
    }
    db.ensure_indices_as(None).await.unwrap();
    Arc::new(db)
}

fn item_seed() -> String {
    (0..8)
        .map(|i| format!("{{\"type\":\"Item\",\"data\":{{\"slug\":\"seed-{i}\",\"n\":{i}}}}}\n"))
        .collect()
}

async fn fresh_slugs(uri: &str) -> Vec<String> {
    let fresh = Omnigraph::open(uri).await.unwrap();
    let batches = helpers::read_table(&fresh, "node:Item").await;
    collect_column_strings(&batches, "slug")
}

fn put(slug: &str, n: i64) -> omnigraph_compiler::ir::ParamMap {
    mixed_params(&[("$slug", slug)], &[("$n", n)])
}

/// RFC §Admission rule 2 (explained interval) and §The effect log: an entry
/// captured before a commit the group did not make is never admitted across
/// it. The prototype's log keeps a hole: a batch that found the log stale
/// admits by its own capture but appends its effect after the stale records,
/// and the walk back to the entry's capture does not check contiguity.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[serial]
async fn gc_log_hole_admits_an_entry_across_a_foreign_insert_of_its_key() {
    let _scenario = FailScenario::setup();
    group_commit_on();
    let dir = tempfile::tempdir().unwrap();
    let uri = dir.path().to_str().unwrap().to_string();
    let db = seeded(&uri, ITEM_SCHEMA, &item_seed()).await;

    // e: captures H0, proves `k` absent there, parks after staging.
    let parked = helpers::failpoint::Rendezvous::park_first(
        &catalog::MUTATION_POST_STAGE_PRE_EFFECT_GATE,
    );
    let e = {
        let db = Arc::clone(&db);
        tokio::spawn(async move { db.mutate_with_receipt("main", ITEM_QUERIES, "put", &put("k", 1)).await })
    };
    parked.wait_until_reached().await;
    // A: a group batch, H0 -> H1 (logged).
    let a = db
        .mutate_with_receipt("main", ITEM_QUERIES, "put", &put("a", 2))
        .await
        .unwrap();
    // F: an ineligible insert of `k` on the ordinary path, H1 -> H2 (not logged).
    let f = db
        .mutate_with_receipt("main", ITEM_QUERIES, "foreign_put", &put("k", 3))
        .await
        .unwrap();
    // B: a group batch, H2 -> H3, appended after A's record.
    let b = db
        .mutate_with_receipt("main", ITEM_QUERIES, "put", &put("b", 4))
        .await
        .unwrap();
    parked.release();
    let e = e.await.unwrap();
    let slugs = fresh_slugs(&uri).await;
    let k_rows = slugs.iter().filter(|slug| *slug == "k").count();
    eprintln!(
        "[gc-repro] A {:?} F {:?} B {:?} e {:?}: rows with slug k = {k_rows}",
        a.commit.as_ref().map(|c| c.graph_manifest_version),
        f.commit.as_ref().map(|c| c.graph_manifest_version),
        b.commit.as_ref().map(|c| c.graph_manifest_version),
        e.as_ref().map(|r| r.commit.as_ref().map(|c| c.graph_manifest_version)),
    );
    assert!(k_rows <= 1, "duplicate key `k`: {k_rows} rows");
}

/// The same hole reached through a second actor's group (RFC §The write
/// group: one group per root and branch, the log records every publication
/// of the process on the branch). Actor y's batch is foreign to actor x's
/// group in the prototype.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[serial]
async fn gc_second_actor_batch_is_a_hole_in_the_first_actors_log() {
    let _scenario = FailScenario::setup();
    group_commit_on();
    let dir = tempfile::tempdir().unwrap();
    let uri = dir.path().to_str().unwrap().to_string();
    let db = seeded(&uri, ITEM_SCHEMA, &item_seed()).await;

    let parked = helpers::failpoint::Rendezvous::park_first(
        &catalog::MUTATION_POST_STAGE_PRE_EFFECT_GATE,
    );
    let e = {
        let db = Arc::clone(&db);
        tokio::spawn(async move {
            db.mutate_as_with_receipt("main", ITEM_QUERIES, "put", &put("k", 1), Some("act-x"))
                .await
        })
    };
    parked.wait_until_reached().await;
    db.mutate_as_with_receipt("main", ITEM_QUERIES, "put", &put("a", 2), Some("act-x"))
        .await
        .unwrap();
    // An eligible insert of `k` by another actor: its own group, its own batch.
    db.mutate_as_with_receipt("main", ITEM_QUERIES, "put", &put("k", 3), Some("act-y"))
        .await
        .unwrap();
    db.mutate_as_with_receipt("main", ITEM_QUERIES, "put", &put("b", 4), Some("act-x"))
        .await
        .unwrap();
    parked.release();
    let e = e.await.unwrap();
    let slugs = fresh_slugs(&uri).await;
    let k_rows = slugs.iter().filter(|slug| *slug == "k").count();
    eprintln!("[gc-repro] two actors: e ok = {}, rows with slug k = {k_rows}", e.is_ok());
    assert!(k_rows <= 1, "duplicate key `k`: {k_rows} rows");
}

/// RFC §Admission rule 3: an edge insert is never admitted across a commit
/// that deleted its endpoint. Through the log hole, it is.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[serial]
async fn gc_log_hole_admits_an_edge_across_a_delete_of_its_endpoint() {
    let _scenario = FailScenario::setup();
    group_commit_on();
    let dir = tempfile::tempdir().unwrap();
    let uri = dir.path().to_str().unwrap().to_string();
    let seed = "{\"type\":\"Person\",\"data\":{\"name\":\"a\"}}\n{\"type\":\"Person\",\"data\":{\"name\":\"b\"}}\n";
    let db = seeded(&uri, PERSON_SCHEMA, seed).await;

    let parked = helpers::failpoint::Rendezvous::park_first(
        &catalog::MUTATION_POST_STAGE_PRE_EFFECT_GATE,
    );
    let e = {
        let db = Arc::clone(&db);
        tokio::spawn(async move {
            db.mutate_with_receipt(
                "main",
                PERSON_QUERIES,
                "knows",
                &params(&[("$from", "a"), ("$to", "b")]),
            )
            .await
        })
    };
    parked.wait_until_reached().await;
    db.mutate("main", PERSON_QUERIES, "add_person", &params(&[("$name", "x")]))
        .await
        .unwrap();
    db.mutate("main", PERSON_QUERIES, "remove_person", &params(&[("$name", "b")]))
        .await
        .unwrap();
    db.mutate("main", PERSON_QUERIES, "add_person", &params(&[("$name", "y")]))
        .await
        .unwrap();
    parked.release();
    let e = e.await.unwrap();
    let fresh = Omnigraph::open(&uri).await.unwrap();
    let people = collect_column_strings(&helpers::read_table(&fresh, "node:Person").await, "name");
    let edges = helpers::read_table(&fresh, "edge:Knows").await;
    let dst_column = if edges
        .first()
        .is_some_and(|batch| batch.schema().column_with_name("__dst").is_some())
    {
        "__dst"
    } else {
        "dst"
    };
    let dsts = collect_column_strings(&edges, dst_column);
    let dangling: Vec<_> = dsts.iter().filter(|dst| !people.contains(dst)).collect();
    eprintln!(
        "[gc-repro] edge e ok = {}, people {people:?}, edge dsts {dsts:?}, dangling {dangling:?}",
        e.is_ok()
    );
    assert!(dangling.is_empty(), "dangling edges to {dangling:?}");
}

/// RFC outcome table: "The leader's future is dropped ... The next waiter
/// leads either way." The prototype leaves `leading` set, so the follower and
/// every later write of the group wait forever.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[serial]
async fn gc_dropped_leader_hands_the_lead_on() {
    let _scenario = FailScenario::setup();
    group_commit_on();
    let dir = tempfile::tempdir().unwrap();
    let uri = dir.path().to_str().unwrap().to_string();
    let db = seeded(&uri, ITEM_SCHEMA, &item_seed()).await;

    // A gate holder: an ordinary-path write parked after its detached commit,
    // holding the shared schema permit and the branch gate.
    let holder_park =
        helpers::failpoint::Rendezvous::park_first(&catalog::MUTATION_POST_TABLE_COMMIT);
    let holder = {
        let db = Arc::clone(&db);
        tokio::spawn(async move {
            db.mutate_with_receipt("main", ITEM_QUERIES, "foreign_put", &put("holder", 0))
                .await
        })
    };
    holder_park.wait_until_reached().await;
    // L leads and waits for the branch gate.
    let leader = {
        let db = Arc::clone(&db);
        tokio::spawn(async move { db.mutate_with_receipt("main", ITEM_QUERIES, "put", &put("l", 1)).await })
    };
    tokio::time::sleep(Duration::from_millis(500)).await;
    // F queues behind L.
    let follower = {
        let db = Arc::clone(&db);
        tokio::spawn(async move { db.mutate_with_receipt("main", ITEM_QUERIES, "put", &put("f", 2)).await })
    };
    tokio::time::sleep(Duration::from_millis(300)).await;
    leader.abort();
    let _ = leader.await;
    holder_park.release();
    holder.await.unwrap().unwrap();
    let follower = tokio::time::timeout(Duration::from_secs(15), follower).await;
    let later = tokio::time::timeout(
        Duration::from_secs(15),
        db.mutate_with_receipt("main", ITEM_QUERIES, "put", &put("w", 3)),
    )
    .await;
    eprintln!(
        "[gc-repro] dropped leader: follower {}, later write {}",
        match &follower {
            Ok(Ok(Ok(_))) => "published".to_string(),
            Ok(Ok(Err(error))) => format!("error: {error}"),
            Ok(Err(join)) => format!("join error: {join}"),
            Err(_) => "hung (15 s)".to_string(),
        },
        match &later {
            Ok(Ok(_)) => "published".to_string(),
            Ok(Err(error)) => format!("error: {error}"),
            Err(_) => "hung (15 s)".to_string(),
        },
    );
    assert!(follower.is_ok(), "the follower of a dropped leader never finished");
    assert!(later.is_ok(), "a later write of the group never finished");
}

/// RFC §Outcomes per member: "An embedded caller that drops its future before
/// admission leaves the queue." The leader is parked inside its publication
/// (`publish.pre_merge`) while two followers queue on its base; the second is
/// cancelled before the next batch admits it.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[serial]
async fn gc_follower_dropped_before_admission_leaves_the_queue() {
    let _scenario = FailScenario::setup();
    group_commit_on();
    let dir = tempfile::tempdir().unwrap();
    let uri = dir.path().to_str().unwrap().to_string();
    let db = seeded(&uri, ITEM_SCHEMA, &item_seed()).await;

    let publish_park = helpers::failpoint::Rendezvous::park_first(&catalog::PUBLISH_PRE_MERGE);
    let leader = {
        let db = Arc::clone(&db);
        tokio::spawn(async move { db.mutate_with_receipt("main", ITEM_QUERIES, "put", &put("l", 1)).await })
    };
    publish_park.wait_until_reached().await;
    let f1 = {
        let db = Arc::clone(&db);
        tokio::spawn(async move { db.mutate_with_receipt("main", ITEM_QUERIES, "put", &put("f1", 2)).await })
    };
    let f2 = {
        let db = Arc::clone(&db);
        tokio::spawn(async move { db.mutate_with_receipt("main", ITEM_QUERIES, "put", &put("f2", 3)).await })
    };
    // Long enough for both followers to prepare and queue behind the leader.
    tokio::time::sleep(Duration::from_millis(1500)).await;
    f2.abort();
    let _ = f2.await;
    publish_park.release();
    let l = leader.await.unwrap().unwrap();
    let f1 = f1.await.unwrap().unwrap();
    let slugs = fresh_slugs(&uri).await;
    let ghost = slugs.iter().any(|slug| slug == "f2");
    eprintln!(
        "[gc-repro] leader v{:?}, f1 v{:?}; cancelled follower published anyway: {ghost}",
        l.commit.map(|c| c.graph_manifest_version),
        f1.commit.map(|c| c.graph_manifest_version)
    );
    assert!(!ghost, "a write cancelled before admission was published");
}

/// RFC outcome table, the leader's future dropped after the batch's first
/// effect: the members get uncertain evidence and the next waiter leads. The
/// leader is parked inside its publication, then dropped.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[serial]
async fn gc_leader_dropped_inside_its_publication() {
    let _scenario = FailScenario::setup();
    group_commit_on();
    let dir = tempfile::tempdir().unwrap();
    let uri = dir.path().to_str().unwrap().to_string();
    let db = seeded(&uri, ITEM_SCHEMA, &item_seed()).await;

    let publish_park = helpers::failpoint::Rendezvous::park_first(&catalog::PUBLISH_PRE_MERGE);
    let leader = {
        let db = Arc::clone(&db);
        tokio::spawn(async move { db.mutate_with_receipt("main", ITEM_QUERIES, "put", &put("l", 1)).await })
    };
    publish_park.wait_until_reached().await;
    let follower = {
        let db = Arc::clone(&db);
        tokio::spawn(async move { db.mutate_with_receipt("main", ITEM_QUERIES, "put", &put("f", 2)).await })
    };
    tokio::time::sleep(Duration::from_millis(1500)).await;
    leader.abort();
    let _ = leader.await;
    publish_park.release();
    let follower = tokio::time::timeout(Duration::from_secs(15), follower).await;
    let later = tokio::time::timeout(
        Duration::from_secs(15),
        db.mutate_with_receipt("main", ITEM_QUERIES, "put", &put("w", 3)),
    )
    .await;
    let slugs = fresh_slugs(&uri).await;
    eprintln!(
        "[gc-repro] leader dropped in publication: follower {}, later write {}, l present {}, f present {}",
        match &follower {
            Ok(Ok(Ok(_))) => "published".to_string(),
            Ok(Ok(Err(error))) => format!("error: {error}"),
            Ok(Err(join)) => format!("join error: {join}"),
            Err(_) => "hung (15 s)".to_string(),
        },
        match &later {
            Ok(Ok(_)) => "published".to_string(),
            Ok(Err(error)) => format!("error: {error}"),
            Err(_) => "hung (15 s)".to_string(),
        },
        slugs.iter().any(|slug| slug == "l"),
        slugs.iter().any(|slug| slug == "f"),
    );
    assert!(follower.is_ok(), "the follower of a dropped leader never finished");
    assert!(later.is_ok(), "a later write of the group never finished");
}

/// RFC eligibility rule 4 keeps Blob tables out until the composition guard
/// pins a Blob v2 column. The prototype batches them; this checks that every
/// composed Blob reads back, before and after optimize.
#[tokio::test(flavor = "multi_thread", worker_threads = 8)]
#[serial]
async fn gc_blob_inserts_compose_and_read_back() {
    use base64::Engine as _;
    let _scenario = FailScenario::setup();
    group_commit_on();
    let dir = tempfile::tempdir().unwrap();
    let uri = dir.path().to_str().unwrap().to_string();
    let seed = "{\"type\":\"Doc\",\"data\":{\"title\":\"seed\",\"content\":\"base64:AAAA\"}}\n";
    let db = seeded(&uri, DOC_SCHEMA, seed).await;
    let writers = 24usize;
    let per_writer = 4usize;
    let mut tasks = Vec::new();
    for w in 0..writers {
        let db = Arc::clone(&db);
        tasks.push(tokio::spawn(async move {
            let mut commits = Vec::new();
            for i in 0..per_writer {
                // Sizes spread over inline and dedicated Blob v2 storage.
                let len = [5usize, 600, 70_000, 300_000][(w + i) % 4];
                let bytes: Vec<u8> = (0..len).map(|b| ((b * 31 + w * 7 + i) % 251) as u8).collect();
                let content = format!(
                    "base64:{}",
                    base64::engine::general_purpose::STANDARD.encode(&bytes)
                );
                let title = format!("d-{w}-{i}");
                let receipt = db
                    .mutate_with_receipt(
                        "main",
                        DOC_QUERIES,
                        "put_doc",
                        &params(&[("$title", &title), ("$content", &content)]),
                    )
                    .await
                    .unwrap();
                commits.push(receipt.commit.map(|c| c.graph_commit_id));
            }
            commits
        }));
    }
    let mut commits = std::collections::HashSet::new();
    for task in tasks {
        for commit in task.await.unwrap() {
            commits.insert(commit);
        }
    }
    eprintln!(
        "[gc-repro] blob writes {} in {} commits",
        writers * per_writer,
        commits.len()
    );
    let check = |label: &'static str| {
        let uri = uri.clone();
        async move {
            let fresh = Omnigraph::open(&uri).await.unwrap();
            let mut bad = Vec::new();
            for w in 0..writers {
                for i in 0..per_writer {
                    let len = [5usize, 600, 70_000, 300_000][(w + i) % 4];
                    let expected: Vec<u8> =
                        (0..len).map(|b| ((b * 31 + w * 7 + i) % 251) as u8).collect();
                    let got = helpers::read_managed_blob_bytes(
                        &fresh,
                        "main",
                        helpers::node_blob_cell("Doc", format!("d-{w}-{i}"), "content"),
                    )
                    .await;
                    if got != expected {
                        bad.push(format!("d-{w}-{i} ({} vs {} bytes)", got.len(), expected.len()));
                    }
                }
            }
            eprintln!("[gc-repro] blob check {label}: {} mismatches {bad:?}", bad.len());
            bad
        }
    };
    let before = check("after writes").await;
    db.optimize_as(None, Default::default()).await.unwrap();
    let after = check("after optimize").await;
    assert!(before.is_empty() && after.is_empty(), "Blob values changed through composition");
    assert!(
        commits.len() < writers * per_writer,
        "no two Blob writes shared a commit, so composition was not exercised"
    );
}

#[path = "group_commit_stress/harness.rs"]
mod harness;

/// The randomized stress harness (`group_commit_stress/harness.rs`), configured
/// by `OG_GS_*`. Fails on any checker violation.
#[tokio::test(flavor = "multi_thread", worker_threads = 8)]
#[serial]
#[ignore = "instrument: group-commit stress harness, configured by OG_GS_* (research)"]
async fn gc_stress_harness() {
    if std::env::var("OG_GS_GROUP_COMMIT").map_or(true, |value| value != "0") {
        group_commit_on();
    } else {
        // SAFETY: serial test.
        unsafe { std::env::remove_var("OMNIGRAPH_GROUP_COMMIT") };
    }
    let cfg = harness::Config::from_env();
    let report = harness::run(cfg).await;
    eprintln!("[gs-summary] {}", report.summary);
    for violation in report.violations.iter().take(40) {
        eprintln!("[gs-violation] {violation}");
    }
    assert!(
        report.violations.is_empty(),
        "{} checker violations",
        report.violations.len()
    );
}
