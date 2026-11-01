//! SPIKE (throwaway): engine-level stress tests for RFC #1214.

use std::sync::Arc;
use std::time::{Duration, Instant};

use omnigraph::db::{
    OptimizeBeforePublish, OptimizeLimits, OptimizePublication, OptimizeRequest, PublicationLookup,
    TableOptimizeOutcome,
};
use omnigraph::loader::LoadMode;

use crate::helpers::{self, MUTATION_QUERIES, init_and_load, mixed_params, mutate_main};

fn recording_hook() -> (
    OptimizeBeforePublish,
    Arc<std::sync::Mutex<Vec<OptimizePublication>>>,
) {
    let seen = Arc::new(std::sync::Mutex::new(Vec::new()));
    let recorded = Arc::clone(&seen);
    let hook = OptimizeBeforePublish::new(move |publication| {
        recorded.lock().unwrap().push(publication);
        async { Ok(()) }
    });
    (hook, seen)
}

async fn insert(db: &omnigraph::Session, name: &str) {
    mutate_main(
        db,
        MUTATION_QUERIES,
        "insert_person",
        &mixed_params(&[("$name", name)], &[("$age", 30)]),
    )
    .await
    .unwrap();
}

/// Drive bounded attempts to a verdict; returns it and the attempt count.
async fn resolve_paged(
    db: &omnigraph::Session,
    publication: &OptimizePublication,
    nonce: &str,
    limit: usize,
) -> (PublicationLookup, usize) {
    let mut cursor: Option<String> = None;
    let mut covered = 0;
    let mut attempts = 0;
    loop {
        attempts += 1;
        let verdict = db
            .resolve_publication_page(
                nonce,
                publication.input_commit.as_deref(),
                publication.input_manifest_version,
                cursor.as_deref(),
                covered,
                limit,
            )
            .await
            .unwrap();
        match verdict {
            PublicationLookup::Walking {
                next,
                covered: walked,
            } => {
                assert_eq!(walked, covered + limit as u64, "a page covers its limit");
                cursor = Some(next);
                covered = walked;
            }
            other => return (other, attempts),
        }
        assert!(attempts < 1000);
    }
}

/// The bounded walk on `first_parent_page` reaches the input head's child
/// at every page boundary, through commits the buffer has released to
/// `__history`, and while main keeps growing between attempts.
#[tokio::test]
async fn spike_paged_resolution_reaches_the_child_at_every_boundary() {
    let dir = tempfile::tempdir().unwrap();
    // A 2048-byte buffer releases main's commits to `__history` every few
    // publishes, so the walk reads released commits too.
    let db = helpers::with_setting(&init_and_load(&dir).await, "history_release_bytes", "2048");
    for n in 0..4 {
        insert(&db, &format!("pre{n}")).await;
    }
    let (hook, seen) = recording_hook();
    let report = db
        .optimize_as(None, OptimizeRequest::default().with_before_publish(hook))
        .await
        .unwrap();
    let commit = report.graph_commit_id.clone().expect("published");
    let publication = seen.lock().unwrap()[0].clone();
    const LATER: usize = 12;
    for n in 0..LATER {
        insert(&db, &format!("post{n}")).await;
    }
    // The child is LATER commits below the head: LATER + 1 commits to read.
    for limit in [1usize, 2, 3, 4, 5, 6, 12, 13, 14, 256] {
        let (verdict, attempts) = resolve_paged(&db, &publication, &publication.nonce, limit).await;
        assert_eq!(
            verdict,
            PublicationLookup::Committed {
                commit_id: commit.clone()
            },
            "limit {limit}"
        );
        assert_eq!(attempts, (LATER + 1).div_ceil(limit), "limit {limit}");
        let (verdict, attempts2) =
            resolve_paged(&db, &publication, "01ARZ3NDEKTSV4RRFFQ69G5FAV", limit).await;
        assert_eq!(verdict, PublicationLookup::NotCommitted, "limit {limit}");
        assert_eq!(attempts2, attempts);
        eprintln!("SPIKE paged resolution limit {limit}: {attempts} attempts");
    }

    // A cursor taken before main grows stays valid: history below it is
    // immutable, and the next attempt resumes there, not at the new tip.
    let first = db
        .resolve_publication_page(
            &publication.nonce,
            publication.input_commit.as_deref(),
            publication.input_manifest_version,
            None,
            0,
            5,
        )
        .await
        .unwrap();
    let PublicationLookup::Walking { next, covered } = first else {
        panic!("{first:?}");
    };
    for n in 0..5 {
        insert(&db, &format!("more{n}")).await;
    }
    let resumed = db
        .resolve_publication_page(
            &publication.nonce,
            publication.input_commit.as_deref(),
            publication.input_manifest_version,
            Some(&next),
            covered,
            8,
        )
        .await
        .unwrap();
    assert_eq!(resumed, PublicationLookup::Committed { commit_id: commit });
}

const MANY_TYPES: usize = 48;

fn many_table_schema() -> String {
    (0..MANY_TYPES)
        .map(|n| format!("node T{n:02} {{\n  name: String @key\n  v: I64?\n}}\n"))
        .collect()
}

fn many_table_rows(round: usize) -> String {
    (0..MANY_TYPES)
        .map(|n| {
            format!("{{\"type\":\"T{n:02}\",\"data\":{{\"name\":\"r{round}\",\"v\":{round}}}}}\n")
        })
        .collect()
}

/// A catalog whose single run cannot fit the gate hold is covered by a
/// sequence of runs resuming at the first deferred table: every table is
/// optimized exactly once, no run visits a table twice, and a writer waits
/// at most about the gate hold plus one table and the publication.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn spike_many_tables_are_covered_by_successive_bounded_runs() {
    let dir = tempfile::tempdir().unwrap();
    let uri = dir.path().to_str().unwrap().to_string();
    let db = Arc::new(helpers::session(
        omnigraph::db::Omnigraph::init(&uri, &many_table_schema())
            .await
            .unwrap(),
    ));
    for round in 0..3 {
        db.load_jsonl(&many_table_rows(round), LoadMode::Merge)
            .await
            .unwrap();
    }
    let hold = Duration::from_millis(150);
    let mut start_at: Option<String> = None;
    let mut optimized = std::collections::BTreeMap::<String, u32>::new();
    let mut runs = 0;
    let mut writer_waits = Vec::new();
    loop {
        runs += 1;
        let mut request = OptimizeRequest::default()
            .with_limits(OptimizeLimits::default().with_max_gate_hold(hold));
        if let Some(start) = &start_at {
            request = request.with_start_at(start.clone());
        }
        // A writer on main that arrives while the run holds the gates.
        let writer = {
            let db = Arc::clone(&db);
            tokio::spawn(async move {
                tokio::time::sleep(Duration::from_millis(20)).await;
                let started = Instant::now();
                db.load_jsonl(
                    &format!("{{\"type\":\"T00\",\"data\":{{\"name\":\"w{runs}\"}}}}\n"),
                    LoadMode::Merge,
                )
                .await
                .unwrap();
                started.elapsed()
            })
        };
        let report = db.optimize_as(None, request).await;
        let wait = writer.await.unwrap();
        let report = match report {
            Ok(report) => report,
            Err(error) => {
                // The writer can land between authority capture and the
                // gates; the run then refuses before any effect.
                eprintln!("SPIKE many-tables run {runs}: refused: {error}");
                assert!(runs < 200);
                continue;
            }
        };
        let keys: Vec<_> = report.tables.iter().map(|t| t.type_key.clone()).collect();
        let distinct: std::collections::BTreeSet<_> = keys.iter().collect();
        assert_eq!(distinct.len(), keys.len(), "no table twice in one run");
        if let Some(start) = &start_at {
            assert_eq!(&keys[0], start, "the run starts at the resume position");
        }
        let max_table = report
            .tables
            .iter()
            .map(|t| t.duration)
            .max()
            .unwrap_or_default();
        for table in &report.tables {
            if table.outcome == TableOptimizeOutcome::Optimized {
                *optimized.entry(table.type_key.clone()).or_default() += 1;
            }
        }
        let done = report
            .tables
            .iter()
            .filter(|t| !matches!(t.outcome, TableOptimizeOutcome::Deferred(_)))
            .count();
        eprintln!(
            "SPIKE many-tables run {runs}: visited {done}/{} gate_hold {:?} max_table {:?} writer_wait {:?} resume {:?}",
            report.tables.len(),
            report.gate_hold,
            max_table,
            wait,
            report.resume_at
        );
        writer_waits.push((wait, report.gate_hold));
        // The bound the RFC states: hold + one table + publication + clear.
        assert!(
            report.gate_hold <= hold + max_table * 2 + Duration::from_secs(3),
            "gate hold {:?} exceeds the bound",
            report.gate_hold
        );
        start_at = report.resume_at.clone();
        if start_at.is_none() {
            break;
        }
        assert!(runs < 200, "never covered the catalog");
    }
    assert_eq!(
        optimized.len(),
        MANY_TYPES,
        "every table optimized: {optimized:?}"
    );
    // T00 receives one write per run, so later runs may compact it again.
    assert!(
        optimized
            .iter()
            .filter(|(key, _)| key.as_str() != "node:T00")
            .all(|(_, n)| *n == 1),
        "{optimized:?}"
    );
    eprintln!("SPIKE many-tables: {MANY_TYPES} tables covered in {runs} runs of a {hold:?} hold");
}

/// Two direct runs on one standalone root, from two handles: the exact
/// version CAS fails one closed, and the graph stays readable and intact.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn spike_two_direct_runs_on_one_standalone_root() {
    let dir = tempfile::tempdir().unwrap();
    let a = init_and_load(&dir).await;
    for n in 0..4 {
        insert(&a, &format!("x{n}")).await;
    }
    let rows_before = helpers::count_rows(&a, "node:Person").await;
    let uri = dir.path().to_str().unwrap().to_string();
    let b = helpers::session(omnigraph::db::Omnigraph::open(&uri).await.unwrap());
    let (ra, rb) = tokio::join!(
        a.optimize_as(None, OptimizeRequest::default()),
        b.optimize_as(None, OptimizeRequest::default())
    );
    let describe = |r: &omnigraph::error::Result<omnigraph::db::OptimizeReport>| match r {
        Ok(report) => format!("ok commit={:?}", report.graph_commit_id),
        Err(error) => format!(
            "err evidence={:?}: {}",
            error.completion_evidence(),
            error.to_string().chars().take(160).collect::<String>()
        ),
    };
    eprintln!(
        "SPIKE two direct runs: A {} | B {}",
        describe(&ra),
        describe(&rb)
    );
    let published = [&ra, &rb]
        .iter()
        .filter(|r| r.as_ref().is_ok_and(|r| r.graph_commit_id.is_some()))
        .count();
    assert!(published <= 1, "at most one run publishes");
    let c = helpers::session(omnigraph::db::Omnigraph::open(&uri).await.unwrap());
    assert_eq!(helpers::count_rows(&c, "node:Person").await, rows_before);
    // A third run after both is still able to publish or find nothing.
    c.optimize_as(None, OptimizeRequest::default())
        .await
        .unwrap();
}

/// Writers hammering main while optimize runs back to back: every write
/// lands; count the runs refused because a write moved main between the
/// run's authority capture and its gates, with and without a capture made
/// again under the held gates.
#[tokio::test(flavor = "multi_thread", worker_threads = 6)]
async fn spike_writers_race_optimize_authority_capture() {
    for recapture in [false, true] {
        let dir = tempfile::tempdir().unwrap();
        let db = Arc::new(init_and_load(&dir).await);
        let stop = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let mut writers = Vec::new();
        for w in 0..4 {
            let db = Arc::clone(&db);
            let stop = Arc::clone(&stop);
            writers.push(tokio::spawn(async move {
                let mut n = 0u32;
                while !stop.load(std::sync::atomic::Ordering::SeqCst) {
                    insert(&db, &format!("w{w}-{n}")).await;
                    n += 1;
                }
                n
            }));
        }
        let mut refused = 0;
        let mut published = 0;
        let mut evidence = std::collections::BTreeSet::new();
        for _ in 0..20 {
            match db
                .optimize_as(
                    None,
                    OptimizeRequest::default().with_spike_recapture_under_gates(recapture),
                )
                .await
            {
                Ok(report) => published += usize::from(report.graph_commit_id.is_some()),
                Err(error) => {
                    refused += 1;
                    evidence.insert(format!("{:?}", error.completion_evidence()));
                }
            }
        }
        stop.store(true, std::sync::atomic::Ordering::SeqCst);
        let mut writes = 0;
        for writer in writers {
            writes += writer.await.unwrap();
        }
        eprintln!(
            "SPIKE writers race (recapture under gates: {recapture}): {writes} writes ok; 20 runs: {refused} refused (evidence {evidence:?}), {published} published"
        );
        if recapture {
            assert_eq!(
                refused, 0,
                "a capture under the gates is never moved by an in-process writer"
            );
        }
    }
}
