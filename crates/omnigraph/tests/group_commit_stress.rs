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

const PERSON_SCHEMA: &str =
    "node Person {\n    name: String @key\n}\nedge Knows: Person -> Person\n";
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
    let parked =
        helpers::failpoint::Rendezvous::park_first(&catalog::MUTATION_POST_STAGE_PRE_EFFECT_GATE);
    let e = {
        let db = Arc::clone(&db);
        tokio::spawn(async move {
            db.mutate_with_receipt("main", ITEM_QUERIES, "put", &put("k", 1))
                .await
        })
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
        e.as_ref()
            .map(|r| r.commit.as_ref().map(|c| c.graph_manifest_version)),
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

    let parked =
        helpers::failpoint::Rendezvous::park_first(&catalog::MUTATION_POST_STAGE_PRE_EFFECT_GATE);
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
    eprintln!(
        "[gc-repro] two actors: e ok = {}, rows with slug k = {k_rows}",
        e.is_ok()
    );
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

    let parked =
        helpers::failpoint::Rendezvous::park_first(&catalog::MUTATION_POST_STAGE_PRE_EFFECT_GATE);
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
    db.mutate(
        "main",
        PERSON_QUERIES,
        "add_person",
        &params(&[("$name", "x")]),
    )
    .await
    .unwrap();
    db.mutate(
        "main",
        PERSON_QUERIES,
        "remove_person",
        &params(&[("$name", "b")]),
    )
    .await
    .unwrap();
    db.mutate(
        "main",
        PERSON_QUERIES,
        "add_person",
        &params(&[("$name", "y")]),
    )
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
        tokio::spawn(async move {
            db.mutate_with_receipt("main", ITEM_QUERIES, "put", &put("l", 1))
                .await
        })
    };
    tokio::time::sleep(Duration::from_millis(500)).await;
    // F queues behind L.
    let follower = {
        let db = Arc::clone(&db);
        tokio::spawn(async move {
            db.mutate_with_receipt("main", ITEM_QUERIES, "put", &put("f", 2))
                .await
        })
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
    assert!(
        follower.is_ok(),
        "the follower of a dropped leader never finished"
    );
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
        tokio::spawn(async move {
            db.mutate_with_receipt("main", ITEM_QUERIES, "put", &put("l", 1))
                .await
        })
    };
    publish_park.wait_until_reached().await;
    let f1 = {
        let db = Arc::clone(&db);
        tokio::spawn(async move {
            db.mutate_with_receipt("main", ITEM_QUERIES, "put", &put("f1", 2))
                .await
        })
    };
    let f2 = {
        let db = Arc::clone(&db);
        tokio::spawn(async move {
            db.mutate_with_receipt("main", ITEM_QUERIES, "put", &put("f2", 3))
                .await
        })
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
        tokio::spawn(async move {
            db.mutate_with_receipt("main", ITEM_QUERIES, "put", &put("l", 1))
                .await
        })
    };
    publish_park.wait_until_reached().await;
    let follower = {
        let db = Arc::clone(&db);
        tokio::spawn(async move {
            db.mutate_with_receipt("main", ITEM_QUERIES, "put", &put("f", 2))
                .await
        })
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
    assert!(
        follower.is_ok(),
        "the follower of a dropped leader never finished"
    );
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
                let bytes: Vec<u8> = (0..len)
                    .map(|b| ((b * 31 + w * 7 + i) % 251) as u8)
                    .collect();
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
                    let expected: Vec<u8> = (0..len)
                        .map(|b| ((b * 31 + w * 7 + i) % 251) as u8)
                        .collect();
                    let got = helpers::read_managed_blob_bytes(
                        &fresh,
                        "main",
                        helpers::node_blob_cell("Doc", format!("d-{w}-{i}"), "content"),
                    )
                    .await;
                    if got != expected {
                        bad.push(format!(
                            "d-{w}-{i} ({} vs {} bytes)",
                            got.len(),
                            expected.len()
                        ));
                    }
                }
            }
            eprintln!(
                "[gc-repro] blob check {label}: {} mismatches {bad:?}",
                bad.len()
            );
            bad
        }
    };
    let before = check("after writes").await;
    db.optimize_as(None, Default::default()).await.unwrap();
    let after = check("after optimize").await;
    assert!(
        before.is_empty() && after.is_empty(),
        "Blob values changed through composition"
    );
    assert!(
        commits.len() < writers * per_writer,
        "no two Blob writes shared a commit, so composition was not exercised"
    );
}

/// RFC §Admission rule 1: an entry captured on a branch incarnation that was
/// deleted and created again under its name is never admitted into the new
/// one. Group commit on a named branch, which the prototype serves through a
/// held coordinator and the per-branch writer-state cache.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[serial]
async fn gc_entry_captured_on_a_recreated_branch_is_not_admitted() {
    let _scenario = FailScenario::setup();
    group_commit_on();
    let dir = tempfile::tempdir().unwrap();
    let uri = dir.path().to_str().unwrap().to_string();
    let db = seeded(&uri, ITEM_SCHEMA, &item_seed()).await;
    db.branch_create("b1").await.unwrap();
    // A batch on b1 so the group holds b1's coordinator and log.
    db.mutate_with_receipt("b1", ITEM_QUERIES, "put", &put("b1-first", 1))
        .await
        .unwrap();
    let parked =
        helpers::failpoint::Rendezvous::park_first(&catalog::MUTATION_POST_STAGE_PRE_EFFECT_GATE);
    let e = {
        let db = Arc::clone(&db);
        tokio::spawn(async move {
            db.mutate_with_receipt("b1", ITEM_QUERIES, "put", &put("stale", 2))
                .await
        })
    };
    parked.wait_until_reached().await;
    db.branch_delete("b1").await.unwrap();
    db.branch_create("b1").await.unwrap();
    db.mutate_with_receipt("b1", ITEM_QUERIES, "put", &put("b1-new", 3))
        .await
        .unwrap();
    parked.release();
    let e = e.await.unwrap();
    let fresh = Omnigraph::open(&uri).await.unwrap();
    let rows = collect_column_strings(
        &helpers::read_table_branch(&fresh, "b1", "node:Item").await,
        "slug",
    );
    let stale = rows.iter().filter(|slug| *slug == "stale").count();
    let old_first = rows.iter().any(|slug| slug == "b1-first");
    eprintln!(
        "[gc-repro] recreated branch: e {:?}; new b1 rows {} (stale x{stale}, old incarnation row present {old_first})",
        e.as_ref()
            .map(|r| r.commit.as_ref().map(|c| c.graph_manifest_version)),
        rows.len()
    );
    assert!(
        !old_first,
        "the recreated branch shows a row of the deleted incarnation"
    );
    // The request may rerun on the new incarnation (a fresh preparation), so
    // `stale` is there at most once.
    assert!(stale <= 1, "duplicate rows for `stale`");
}

#[path = "group_commit_stress/attribution.rs"]
mod attribution;
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

/// RFC §Attribution and rules 6 and 19: one batch holds writes of several
/// actors (and one without an actor); its commit records each write with its
/// own actor and row-id range per table, commit get and the change feed
/// report each row with its write's actor, and the attribution holds after
/// an update, a delete and a compaction of batched rows.
#[tokio::test(flavor = "multi_thread", worker_threads = 8)]
#[serial]
async fn gc_mixed_actor_batch_attributes_every_row() {
    use harness::{QUERIES, SCHEMA};
    let _scenario = FailScenario::setup();
    group_commit_on();
    let dir = tempfile::tempdir().unwrap();
    let uri = dir.path().to_str().unwrap().to_string();
    let mut seed = String::new();
    for i in 0..4 {
        seed.push_str(&format!(
            "{{\"type\":\"Item\",\"data\":{{\"slug\":\"seed-{i}\",\"n\":{i}}}}}\n"
        ));
        seed.push_str(&format!(
            "{{\"type\":\"Person\",\"data\":{{\"name\":\"p{i}\"}}}}\n"
        ));
    }
    let db = seeded(&uri, SCHEMA, &seed).await;

    // An ordinary-path write parked after its table commit holds the gates,
    // so the group's writes queue behind one leader.
    let holder_park =
        helpers::failpoint::Rendezvous::park_first(&catalog::MUTATION_POST_TABLE_COMMIT);
    let holder = {
        let db = Arc::clone(&db);
        tokio::spawn(async move {
            // A Tag insert (a non-key `@unique`: ineligible) touches no
            // table the batch writes.
            db.mutate_with_receipt(
                "main",
                QUERIES,
                "tag",
                &params(&[("$slug", "hold"), ("$label", "hold")]),
            )
            .await
        })
    };
    holder_park.wait_until_reached().await;

    type Spec = (
        &'static str,
        Option<&'static str>,
        &'static str,
        omnigraph_compiler::ir::ParamMap,
    );
    let specs: Vec<Spec> = vec![
        (
            "fresh",
            Some("act-a"),
            "ins",
            mixed_params(&[("$slug", "f1")], &[("$n", 1)]),
        ),
        (
            "pair",
            Some("act-b"),
            "ins2",
            mixed_params(&[("$a", "f2a"), ("$b", "f2b")], &[("$n", 2)]),
        ),
        (
            "multi",
            Some("act-c"),
            "multi",
            mixed_params(&[("$slug", "f3"), ("$name", "q3")], &[("$n", 3)]),
        ),
        (
            "knows",
            Some("act-a"),
            "knows",
            params(&[("$from", "p0"), ("$to", "p1")]),
        ),
        (
            "fresh",
            None,
            "ins",
            mixed_params(&[("$slug", "f5")], &[("$n", 5)]),
        ),
        (
            "person",
            Some("act-b"),
            "person",
            params(&[("$name", "q6")]),
        ),
        (
            "knows",
            Some("act-d"),
            "knows",
            params(&[("$from", "p2"), ("$to", "p3")]),
        ),
        (
            "fresh",
            Some("act-c"),
            "ins",
            mixed_params(&[("$slug", "f8")], &[("$n", 8)]),
        ),
    ];
    let mut tasks = Vec::new();
    for (kind, actor, name, p) in specs.clone() {
        let db = Arc::clone(&db);
        tasks.push(tokio::spawn(async move {
            db.mutate_as_with_receipt("main", QUERIES, name, &p, actor)
                .await
        }));
        let _ = kind;
        tokio::time::sleep(Duration::from_millis(60)).await;
    }
    tokio::time::sleep(Duration::from_millis(200)).await;
    holder_park.release();
    holder.await.unwrap().unwrap();
    let mut ops = Vec::new();
    let mut per_commit: std::collections::HashMap<
        String,
        std::collections::BTreeSet<Option<&str>>,
    > = Default::default();
    for (index, task) in tasks.into_iter().enumerate() {
        let receipt = task.await.unwrap().expect("every write publishes");
        let (kind, actor, _, p) = &specs[index];
        let commit = receipt.commit.expect("a receipt names its commit");
        let mut items = Vec::new();
        let mut person = None;
        let mut edge = None;
        let s = |key: &str| match p.get(key.trim_start_matches('$')) {
            Some(omnigraph_compiler::query::ast::Literal::String(s)) => Some(s.clone()),
            _ => None,
        };
        match *kind {
            "fresh" => items.push(s("$slug").unwrap()),
            "pair" => items.extend([s("$a").unwrap(), s("$b").unwrap()]),
            "multi" => {
                items.push(s("$slug").unwrap());
                person = s("$name");
            }
            "person" => person = s("$name"),
            "knows" => edge = Some((s("$from").unwrap(), s("$to").unwrap())),
            _ => {}
        }
        if !commit.writes.is_empty() {
            assert!(
                commit
                    .writes
                    .iter()
                    .any(|w| w.actor_id.as_deref() == *actor),
                "the receipt's decoded write list names its actor"
            );
        }
        per_commit
            .entry(commit.graph_commit_id.clone())
            .or_default()
            .insert(*actor);
        ops.push(attribution::AttrOp {
            id: index as u64,
            actor: actor.map(str::to_string),
            outcome: "ok".into(),
            commit: Some(commit.graph_commit_id.clone()),
            rows: attribution::inserted_rows(
                kind,
                &items,
                person.as_deref(),
                edge.as_ref().map(|(a, b)| (a.as_str(), b.as_str())),
                None,
            ),
        });
    }
    let widest = per_commit.values().map(|a| a.len()).max().unwrap_or(0);
    eprintln!("[gc-repro] mixed actors: commits {per_commit:?}");
    assert!(widest >= 4, "no batch held four actors: {per_commit:?}");

    let fresh = Omnigraph::open(&uri).await.unwrap();
    let report = attribution::check(&fresh, &ops).await;
    eprintln!(
        "[gc-repro] attribution after the batch: {} {:?}",
        report.summary, report.violations
    );
    assert!(report.violations.is_empty());

    // Later versions: update one batched row, delete another, compact.
    db.mutate_as_with_receipt(
        "main",
        QUERIES,
        "upd",
        &mixed_params(&[("$slug", "f1")], &[("$n", 11)]),
        Some("act-z"),
    )
    .await
    .unwrap();
    db.mutate_as_with_receipt(
        "main",
        QUERIES,
        "del",
        &params(&[("$slug", "f2a")]),
        Some("act-z"),
    )
    .await
    .unwrap();
    db.optimize_as(None, Default::default()).await.unwrap();
    let fresh = Omnigraph::open(&uri).await.unwrap();
    let report = attribution::check(&fresh, &ops).await;
    eprintln!(
        "[gc-repro] attribution after update, delete, optimize: {} {:?}",
        report.summary, report.violations
    );
    assert!(report.violations.is_empty());
    assert!(report.summary["head_rows_checked"].as_u64().unwrap() > 0);
}

/// Offline attribution check of a finished run (served or embedded):
/// `OG_GS_URI` the graph root, `OG_GS_OPS` comma-separated JSONL histories.
#[tokio::test(flavor = "multi_thread", worker_threads = 8)]
#[serial]
#[ignore = "instrument: offline attribution oracle for a finished run, configured by OG_GS_URI and OG_GS_OPS (research)"]
async fn gc_attribution_offline() {
    let uri = std::env::var("OG_GS_URI").expect("OG_GS_URI");
    let paths: Vec<String> = std::env::var("OG_GS_OPS")
        .expect("OG_GS_OPS")
        .split(',')
        .map(str::to_string)
        .collect();
    let ops = attribution::load_ops(&paths);
    let db = Omnigraph::open(&uri).await.unwrap();
    let report = attribution::check(&db, &ops).await;
    eprintln!("[attr-summary] ops {} {}", ops.len(), report.summary);
    for violation in report.violations.iter().take(40) {
        eprintln!("[attr-violation] {violation}");
    }
    assert!(
        report.violations.is_empty(),
        "{} attribution violations",
        report.violations.len()
    );
}

/// RFC §Attribution: "The list counts toward the commit record's byte
/// budget. Admission stops adding members when the next one would exceed
/// it." Writers with long actor ids and a lowered budget
/// (`OMNIGRAPH_GROUP_COMMIT_WRITE_LIST_BYTES`): every write publishes, no
/// write list exceeds the budget, the members left out publish in later
/// batches in arrival order, and attribution stays exact.
#[tokio::test(flavor = "multi_thread", worker_threads = 8)]
#[serial]
async fn gc_write_list_budget_splits_batches_of_long_actor_ids() {
    use harness::{QUERIES, SCHEMA};
    let _scenario = FailScenario::setup();
    group_commit_on();
    let budget = 1_400usize;
    // SAFETY: serial test; read per batch.
    unsafe {
        std::env::set_var(
            "OMNIGRAPH_GROUP_COMMIT_WRITE_LIST_BYTES",
            budget.to_string(),
        )
    };
    let dir = tempfile::tempdir().unwrap();
    let uri = dir.path().to_str().unwrap().to_string();
    let db = seeded(
        &uri,
        SCHEMA,
        "{\"type\":\"Item\",\"data\":{\"slug\":\"seed\",\"n\":0}}\n",
    )
    .await;
    let holder_park =
        helpers::failpoint::Rendezvous::park_first(&catalog::MUTATION_POST_TABLE_COMMIT);
    let holder = {
        let db = Arc::clone(&db);
        tokio::spawn(async move {
            db.mutate_with_receipt(
                "main",
                QUERIES,
                "tag",
                &params(&[("$slug", "hold"), ("$label", "hold")]),
            )
            .await
        })
    };
    holder_park.wait_until_reached().await;
    let writers = 12usize;
    let actor_of = |w: usize| format!("act-long-{w:02}-{}", "x".repeat(300));
    let mut tasks = Vec::new();
    for w in 0..writers {
        let db = Arc::clone(&db);
        let actor = actor_of(w);
        tasks.push(tokio::spawn(async move {
            let slug = format!("b{w}");
            db.mutate_as_with_receipt(
                "main",
                QUERIES,
                "ins",
                &mixed_params(&[("$slug", &slug)], &[("$n", w as i64)]),
                Some(&actor),
            )
            .await
        }));
        tokio::time::sleep(Duration::from_millis(40)).await;
    }
    tokio::time::sleep(Duration::from_millis(200)).await;
    holder_park.release();
    holder.await.unwrap().unwrap();
    let mut ops = Vec::new();
    let mut versions = Vec::new();
    for (w, task) in tasks.into_iter().enumerate() {
        let receipt = task.await.unwrap().expect("every write publishes");
        let commit = receipt.commit.unwrap();
        versions.push(commit.graph_manifest_version);
        ops.push(attribution::AttrOp {
            id: w as u64,
            actor: Some(actor_of(w)),
            outcome: "ok".into(),
            commit: Some(commit.graph_commit_id.clone()),
            rows: attribution::inserted_rows("fresh", &[format!("b{w}")], None, None, None),
        });
    }
    // SAFETY: serial test.
    unsafe { std::env::remove_var("OMNIGRAPH_GROUP_COMMIT_WRITE_LIST_BYTES") };
    let fresh = Omnigraph::open(&uri).await.unwrap();
    let commits = fresh.list_commits(None).await.unwrap();
    let mut sizes = Vec::new();
    for commit in commits.iter().filter(|c| !c.writes.is_empty()) {
        let encoded = omnigraph_catalog::commit_graph::encode_write_list(
            commit.actor_id.as_deref(),
            &commit.writes,
        );
        sizes.push((commit.writes.len(), encoded.len()));
        assert!(
            encoded.len() <= budget,
            "a write list of {} bytes exceeds the budget {budget}",
            encoded.len()
        );
    }
    eprintln!(
        "[gc-repro] budget {budget}: write lists (members, bytes) {sizes:?}; versions {versions:?}"
    );
    assert!(
        sizes.iter().any(|(members, _)| *members >= 2),
        "no batch held two writes"
    );
    // Arrival order: versions never decrease along submission order.
    assert!(
        versions.windows(2).all(|pair| pair[0] <= pair[1]),
        "a later write overtook an earlier one: {versions:?}"
    );
    let report = attribution::check(&fresh, &ops).await;
    eprintln!(
        "[gc-repro] attribution: {} {:?}",
        report.summary, report.violations
    );
    assert!(report.violations.is_empty());
}

/// RFC admission rule 4 across actors: two actors insert the same absent key
/// into one batch, and two actors insert Tags with one `@unique` label. The
/// key's second insert is sent back and publishes alone, under its own actor;
/// the batch's write list never names it. One Tag wins; the other gets a
/// typed refusal with definite evidence and appears in no commit.
#[tokio::test(flavor = "multi_thread", worker_threads = 8)]
#[serial]
async fn gc_cross_actor_conflicts_in_one_batch() {
    use harness::{QUERIES, SCHEMA};
    let _scenario = FailScenario::setup();
    group_commit_on();
    let dir = tempfile::tempdir().unwrap();
    let uri = dir.path().to_str().unwrap().to_string();
    let db = seeded(
        &uri,
        SCHEMA,
        "{\"type\":\"Item\",\"data\":{\"slug\":\"seed\",\"n\":0}}\n",
    )
    .await;
    let holder_park =
        helpers::failpoint::Rendezvous::park_first(&catalog::MUTATION_POST_TABLE_COMMIT);
    let holder = {
        let db = Arc::clone(&db);
        tokio::spawn(async move {
            db.mutate_with_receipt(
                "main",
                QUERIES,
                "tag",
                &params(&[("$slug", "hold"), ("$label", "hold")]),
            )
            .await
        })
    };
    holder_park.wait_until_reached().await;
    let specs: Vec<(&'static str, &'static str, omnigraph_compiler::ir::ParamMap)> = vec![
        // The first write leads alone; the rest queue behind it as one batch.
        (
            "act-z",
            "ins",
            mixed_params(&[("$slug", "z1")], &[("$n", 9)]),
        ),
        (
            "act-a",
            "ins",
            mixed_params(&[("$slug", "k")], &[("$n", 1)]),
        ),
        (
            "act-e",
            "ins",
            mixed_params(&[("$slug", "e1")], &[("$n", 5)]),
        ),
        (
            "act-b",
            "ins",
            mixed_params(&[("$slug", "k")], &[("$n", 2)]),
        ),
        (
            "act-f",
            "ins",
            mixed_params(&[("$slug", "f1")], &[("$n", 6)]),
        ),
        ("act-c", "tag", params(&[("$slug", "t1"), ("$label", "L")])),
        ("act-d", "tag", params(&[("$slug", "t2"), ("$label", "L")])),
    ];
    let mut tasks = Vec::new();
    for (actor, name, p) in specs.clone() {
        let db = Arc::clone(&db);
        tasks.push(tokio::spawn(async move {
            db.mutate_as_with_receipt("main", QUERIES, name, &p, Some(actor))
                .await
        }));
        tokio::time::sleep(Duration::from_millis(60)).await;
    }
    tokio::time::sleep(Duration::from_millis(200)).await;
    holder_park.release();
    holder.await.unwrap().unwrap();
    let mut results = Vec::new();
    for task in tasks {
        results.push(task.await.unwrap());
    }
    let fresh = Omnigraph::open(&uri).await.unwrap();
    let commits = fresh.list_commits(None).await.unwrap();
    let by_id: std::collections::HashMap<&str, &omnigraph::db::GraphCommit> = commits
        .iter()
        .map(|c| (c.graph_commit_id.as_str(), c))
        .collect();
    let mut ops = Vec::new();
    for (index, result) in results.iter().enumerate() {
        let (actor, name, _) = &specs[index];
        match result {
            Ok(receipt) => {
                let commit = receipt.commit.as_ref().unwrap();
                let stored = by_id[commit.graph_commit_id.as_str()];
                eprintln!(
                    "[gc-repro] {actor} {name}: v{} writes {:?} actor {:?}",
                    commit.graph_manifest_version,
                    stored
                        .writes
                        .iter()
                        .map(|w| w.actor_id.clone())
                        .collect::<Vec<_>>(),
                    stored.actor_id
                );
                let slug = match specs[index].2.get("slug") {
                    Some(omnigraph_compiler::query::ast::Literal::String(s)) => s.clone(),
                    _ => unreachable!(),
                };
                let rows = if *name == "tag" {
                    attribution::inserted_rows("tag", &[], None, None, Some(&slug))
                } else {
                    attribution::inserted_rows("fresh", &[slug], None, None, None)
                };
                ops.push(attribution::AttrOp {
                    id: index as u64,
                    actor: Some(actor.to_string()),
                    outcome: "ok".into(),
                    commit: Some(commit.graph_commit_id.clone()),
                    rows,
                });
            }
            Err(error) => {
                eprintln!(
                    "[gc-repro] {actor} {name}: refused: {error} (evidence {:?})",
                    error.completion_evidence()
                );
                // The ordinary path's validation refusal (Tags never batch):
                // typed, and nothing of it is in any commit (checked below).
                assert!(
                    format!("{error}").contains("@unique violation"),
                    "the refusal is the typed @unique violation"
                );
                ops.push(attribution::AttrOp {
                    id: index as u64,
                    actor: Some(actor.to_string()),
                    outcome: "refused".into(),
                    commit: None,
                    rows: Default::default(),
                });
            }
        }
    }
    // Both key inserts publish; act-b's lands in a commit of its own, as an
    // update of `k`, and no write list names act-b.
    let a = results[1].as_ref().expect("act-a publishes");
    let b = results[3]
        .as_ref()
        .expect("act-b publishes (sent back, then alone)");
    let a_commit = a.commit.as_ref().unwrap();
    let b_commit = b.commit.as_ref().unwrap();
    assert_ne!(
        a_commit.graph_commit_id, b_commit.graph_commit_id,
        "one batch holds two writes of one key"
    );
    let b_stored = by_id[b_commit.graph_commit_id.as_str()];
    assert!(b_stored.writes.is_empty() && b_stored.actor_id.as_deref() == Some("act-b"));
    for commit in &commits {
        assert!(
            commit
                .writes
                .iter()
                .all(|w| w.actor_id.as_deref() != Some("act-b"))
        );
    }
    // Exactly one Tag publishes.
    let tags_ok = [&results[5], &results[6]]
        .iter()
        .filter(|r| r.is_ok())
        .count();
    assert_eq!(
        tags_ok, 1,
        "exactly one of two equal @unique labels publishes"
    );
    // act-b's insert of an existing key is not an insert in its own commit:
    // the attribution oracle sees only act-a's row of `k` as a batch insert.
    ops[3].rows = Default::default();
    let a_stored = by_id[a_commit.graph_commit_id.as_str()];
    assert!(
        a_stored.writes.len() >= 2,
        "act-a's write did not share a batch: {:?}",
        a_stored.writes
    );
    let report = attribution::check(&fresh, &ops).await;
    eprintln!(
        "[gc-repro] attribution: {} {:?}",
        report.summary, report.violations
    );
    assert!(report.violations.is_empty());
}
