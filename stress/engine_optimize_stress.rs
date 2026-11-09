//! Campaign 1: engine optimize beside live writers, embedded, one process.
//!
//! Writers load, mutate and merge branches into `main` while optimize runs
//! repeatedly with random bounds (`max_gate_hold`, `tables`, `start_at`,
//! per-table ceilings, the run byte budget), random cancellation and both
//! `AuthorityCapture` modes; a schema-apply phase runs without branches.
//! After every phase it checks:
//!
//! - every write the engine acknowledged is present (keys and counts);
//! - each optimize publishes at most once, and its reported commit is on
//!   main's first-parent chain as a child of its input commit, by its actor;
//!   no other commit carries the optimizer actor;
//! - a run reported cancelled published nothing; every error carries
//!   completion evidence (BeforeEffect / Unpublished) or is a policy denial,
//!   and an `UnderGates` run is never refused for a moved head;
//! - each run reports every table it considered exactly once; a full-catalog
//!   run visits every key once, starting at `start_at`; `resume_at` names the
//!   first table a gate-hold or byte-budget deferral stopped;
//! - writer wait against the run's gate hold;
//! - `cleanup` leaves main and every live branch readable.
//!
//! Run: STRESS_SECONDS=120 cargo dev-nextest -E 'binary(=engine_optimize_stress)' --run-ignored only --no-capture

use std::collections::{BTreeMap, BTreeSet};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use arrow_array::Array as _;
use omnigraph::Session;
use omnigraph::db::{
    AuthorityCapture, DeferReason, Omnigraph, OptimizeCancellation, OptimizeConcurrency,
    OptimizeLimits, OptimizeRequest, RunCancellation, TableCeilings, TableOptimizeOutcome,
};
use omnigraph::error::CompletionEvidence;
use omnigraph::loader::LoadMode;
use omnigraph::settings::SessionSettings;
use omnigraph_compiler::ir::ParamMap;
use omnigraph_compiler::query::ast::Literal;

const SCHEMA: &str = include_str!("fixture/kg.pg");
const OPTIMIZER: &str = "optimizer";

fn session(db: Omnigraph) -> Session {
    Session::from_defaults(Arc::new(db), SessionSettings::default())
}

/// A small deterministic generator (xorshift64*), seeded per task.
struct Rng(u64);
impl Rng {
    fn next(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        self.0 = x;
        x.wrapping_mul(0x2545_F491_4F6C_DD1D)
    }
    fn below(&mut self, n: u64) -> u64 {
        self.next() % n.max(1)
    }
    fn chance(&mut self, percent: u64) -> bool {
        self.below(100) < percent
    }
}

#[derive(Default)]
struct Ledger {
    persons: BTreeSet<String>,
    /// The last acknowledged age of each person (its writer is the only one
    /// that updates it), or None when an outcome was not definite.
    ages: BTreeMap<String, Option<i64>>,
    docs: BTreeSet<String>,
    companies: BTreeSet<String>,
    /// (start, latency, kind) of every acknowledged write.
    writes: Vec<(Instant, Duration, &'static str)>,
    write_errors: BTreeMap<String, u64>,
}

#[derive(Debug, Clone)]
#[allow(dead_code)]
struct RunRecord {
    id: u64,
    started: Instant,
    ended: Instant,
    authority: AuthorityCapture,
    hold: Option<Duration>,
    tables: Option<Vec<String>>,
    start_at: Option<String>,
    cancelled_after: Option<Duration>,
    outcome: Result<RunOk, String>,
}

#[derive(Debug, Clone)]
#[allow(dead_code)]
struct RunOk {
    input_commit: Option<String>,
    commit: Option<String>,
    cancellation: RunCancellation,
    gate_hold: Duration,
    visited: Vec<(String, String, Duration)>,
    resume_at: Option<String>,
    first_stop: Option<String>,
    max_table: Duration,
}

fn person(tag: &str, i: u64) -> (String, String) {
    let name = format!("{tag}-p{i}");
    let row = serde_json::json!({"type": "Person", "data": {"name": name, "age": (i % 90) as i32, "bio": format!("person {i} of {tag} writes about graphs")}});
    (name, row.to_string())
}

fn doc(tag: &str, i: u64, rng: &mut Rng) -> (String, String) {
    let slug = format!("{tag}-d{i}");
    let bytes: Vec<u8> = (0..(256 + rng.below(4096))).map(|_| rng.next() as u8).collect();
    use base64::Engine as _;
    let content = format!("base64:{}", base64::engine::general_purpose::STANDARD.encode(bytes));
    let emb: Vec<f32> = (0..8).map(|_| (rng.below(1000) as f32) / 1000.0).collect();
    let row = serde_json::json!({"type": "Doc", "data": {"slug": slug, "title": format!("doc {i} on compaction"), "content": content, "embedding": emb}});
    (slug, row.to_string())
}

fn bump_params(name: &str, age: i64) -> ParamMap {
    let mut map = ParamMap::new();
    map.insert("n".to_string(), Literal::String(name.to_string()));
    map.insert("a".to_string(), Literal::Integer(i128::from(age)));
    map
}

const BUMP: &str = "query bump($n: String, $a: I32) { update Person set { age: $a } where name = $n }";

async fn writer(db: Session, ledger: Arc<Mutex<Ledger>>, stop: Arc<AtomicBool>, id: u64) {
    let mut rng = Rng(0x9E37_79B9_7F4A_7C15 ^ (id + 1) * 7919);
    let tag = format!("w{id}");
    let mut i = 0u64;
    while !stop.load(Ordering::SeqCst) {
        i += 1;
        let t0 = Instant::now();
        let roll = rng.below(100);
        let mut bumped: Option<(String, i64)> = None;
        let (kind, result, keys): (&'static str, omnigraph::error::Result<()>, Vec<(u8, String)>) = if roll < 45 {
            let rows: Vec<_> = (0..1 + rng.below(20)).map(|k| person(&tag, i * 100 + k)).collect();
            let data = rows.iter().map(|(_, r)| r.as_str()).collect::<Vec<_>>().join("\n");
            let r = db.load_as("main", None, &data, LoadMode::Merge, Some("writer")).await.map(|_| ());
            ("load_person", r, rows.into_iter().map(|(k, _)| (0, k)).collect())
        } else if roll < 60 {
            let rows: Vec<_> = (0..1 + rng.below(3)).map(|k| doc(&tag, i * 10 + k, &mut rng)).collect();
            let data = rows.iter().map(|(_, r)| r.as_str()).collect::<Vec<_>>().join("\n");
            let r = db.load_as("main", None, &data, LoadMode::Merge, Some("writer")).await.map(|_| ());
            ("load_doc", r, rows.into_iter().map(|(k, _)| (1, k)).collect())
        } else if roll < 68 {
            let name = format!("{tag}-c{i}");
            let row = serde_json::json!({"type": "Company", "data": {"name": name, "industry": "infra"}}).to_string();
            let r = db.load_as("main", None, &row, LoadMode::Merge, Some("writer")).await.map(|_| ());
            ("load_company", r, vec![(2, name)])
        } else if roll < 85 {
            let people: Vec<String> = ledger.lock().unwrap().persons.iter().cloned().collect();
            if people.len() < 2 {
                continue;
            }
            let a = &people[rng.below(people.len() as u64) as usize];
            let b = &people[rng.below(people.len() as u64) as usize];
            let row = serde_json::json!({"edge": "Knows", "from": a, "to": b, "data": {"since": 2000 + (i % 25) as i32}}).to_string();
            let r = db.load_as("main", None, &row, LoadMode::Merge, Some("writer")).await.map(|_| ());
            ("load_edge", r, vec![])
        } else {
            let mine = format!("{tag}-p");
            let people: Vec<String> = ledger.lock().unwrap().persons.iter().filter(|p| p.starts_with(&mine)).cloned().collect();
            if people.is_empty() {
                continue;
            }
            let name = people[rng.below(people.len() as u64) as usize].clone();
            let age = rng.below(99) as i64;
            let r = db
                .mutate_as("main", BUMP, "bump", &bump_params(&name, age), Some("writer"))
                .await
                .map(|_| ());
            bumped = Some((name, age));
            ("mutate", r, vec![])
        };
        let latency = t0.elapsed();
        let mut l = ledger.lock().unwrap();
        match result {
            Ok(()) => {
                l.writes.push((t0, latency, kind));
                if let Some((name, age)) = &bumped {
                    l.ages.insert(name.clone(), Some(*age));
                }
                for (set, key) in keys {
                    if set == 0 {
                        let i: u64 = key.rsplit("-p").next().and_then(|n| n.parse().ok()).unwrap_or(0);
                        l.ages.insert(key.clone(), Some((i % 90) as i64));
                    }
                    match set {
                        0 => l.persons.insert(key),
                        1 => l.docs.insert(key),
                        _ => l.companies.insert(key),
                    };
                }
            }
            Err(error) => {
                if let Some((name, _)) = &bumped
                    && !evidence_ok(&error)
                {
                    l.ages.insert(name.clone(), None);
                }
                let code = format!("{kind}: {}", error.to_string().chars().take(90).collect::<String>());
                *l.write_errors.entry(code).or_default() += 1;
            }
        }
    }
}

async fn brancher(db: Session, ledger: Arc<Mutex<Ledger>>, stop: Arc<AtomicBool>) {
    let mut n = 0u64;
    while !stop.load(Ordering::SeqCst) {
        n += 1;
        let name = format!("feat{n}");
        if let Err(e) = db.branch_create_as(&name, Some("writer")).await {
            *ledger.lock().unwrap().write_errors.entry(format!("branch_create: {e}")).or_default() += 1;
            tokio::time::sleep(Duration::from_millis(200)).await;
            continue;
        }
        let rows: Vec<_> = (0..5).map(|k| person(&format!("b{n}"), k)).collect();
        let data = rows.iter().map(|(_, r)| r.as_str()).collect::<Vec<_>>().join("\n");
        if db.load_as(&name, None, &data, LoadMode::Merge, Some("writer")).await.is_err() {
            continue;
        }
        let t0 = Instant::now();
        match db.branch_merge_as(&name, "main", Some("writer")).await {
            Ok(_) => {
                let mut l = ledger.lock().unwrap();
                l.writes.push((t0, t0.elapsed(), "merge"));
                l.persons.extend(rows.into_iter().map(|(k, _)| k));
            }
            Err(e) => {
                *ledger.lock().unwrap().write_errors.entry(format!("merge: {}", e.to_string().chars().take(90).collect::<String>())).or_default() += 1;
            }
        }
        let _ = db.branch_delete_as(&name, Some("writer")).await;
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
}

fn evidence_ok(error: &omnigraph::error::OmniError) -> bool {
    matches!(
        error.completion_evidence(),
        Some(CompletionEvidence::BeforeEffect) | Some(CompletionEvidence::Unpublished)
    )
}

async fn optimizer(
    db: Session,
    keys: Vec<String>,
    stop: Arc<AtomicBool>,
    runs: Arc<Mutex<Vec<RunRecord>>>,
    violations: Arc<Mutex<Vec<String>>>,
    seq: Arc<AtomicU64>,
    seed: u64,
) {
    let mut rng = Rng(seed);
    while !stop.load(Ordering::SeqCst) {
        let id = seq.fetch_add(1, Ordering::SeqCst);
        let authority = if rng.chance(50) { AuthorityCapture::UnderGates } else { AuthorityCapture::BeforeGates };
        let hold = if rng.chance(70) { Some(Duration::from_millis(rng.below(300))) } else { None };
        let mut limits = OptimizeLimits::default();
        if let Some(hold) = hold {
            limits = limits.with_max_gate_hold(hold);
        }
        let mut ceilings = TableCeilings::default();
        if rng.chance(25) {
            ceilings = ceilings.with_source_fragments(1 + rng.below(30));
        }
        if rng.chance(15) {
            ceilings = ceilings.with_source_rows(50 + rng.below(5000));
        }
        if rng.chance(15) {
            ceilings = ceilings.with_blob_bytes(rng.below(200_000));
        }
        if rng.chance(10) {
            ceilings = ceilings.with_index_rows(rng.below(5000));
        }
        let table_bytes = if rng.chance(20) { Some(10_000 + rng.below(2_000_000)) } else { None };
        if let Some(b) = table_bytes {
            ceilings = ceilings.with_source_bytes(b);
        }
        limits = limits.with_table_ceilings(ceilings);
        if rng.chance(20) {
            let budget = table_bytes.unwrap_or(0) + rng.below(4_000_000);
            limits = limits.with_run_source_bytes(budget);
        }
        let planning_c = 1 + rng.below(8) as usize;
        let mut request = OptimizeRequest::default()
            .with_limits(limits)
            .with_authority(authority)
            .with_concurrency(OptimizeConcurrency::new(planning_c, 1));
        let (tables, start_at) = if rng.chance(25) {
            let mut pick: Vec<String> = keys.iter().filter(|_| rng.chance(50)).cloned().collect();
            if pick.is_empty() {
                pick.push(keys[0].clone());
            }
            // shuffle
            for i in (1..pick.len()).rev() {
                let j = rng.below(i as u64 + 1) as usize;
                pick.swap(i, j);
            }
            request = request.with_tables(pick.clone());
            (Some(pick), None)
        } else if rng.chance(50) {
            let s = if rng.chance(80) { keys[rng.below(keys.len() as u64) as usize].clone() } else { "node:M".to_string() };
            request = request.with_start_at(s.clone());
            (None, Some(s))
        } else {
            (None, None)
        };
        let cancellation = OptimizeCancellation::new();
        let cancelled_after = if rng.chance(25) { Some(Duration::from_millis(rng.below(400))) } else { None };
        request = request.with_cancellation(cancellation.clone());
        if let Some(after) = cancelled_after {
            let c = cancellation.clone();
            tokio::spawn(async move {
                tokio::time::sleep(after).await;
                c.cancel();
            });
        }
        let started = Instant::now();
        let result = db.optimize_as(Some(OPTIMIZER), request).await;
        let ended = Instant::now();
        let outcome = match result {
            Ok(report) => {
                let visited: Vec<(String, String, Duration)> = report
                    .tables
                    .iter()
                    .map(|t| (t.type_key.clone(), format!("{:?}", t.outcome), t.duration))
                    .collect();
                let first_stop = report
                    .tables
                    .iter()
                    .find(|t| matches!(t.outcome, TableOptimizeOutcome::Deferred(DeferReason::GateHoldLimit) | TableOptimizeOutcome::Deferred(DeferReason::ByteBudget)))
                    .map(|t| t.type_key.clone());
                let max_table = report.tables.iter().map(|t| t.duration).max().unwrap_or_default();
                let mut v = violations.lock().unwrap();
                // every considered table exactly once
                let mut seen = BTreeSet::new();
                for (k, _, _) in &visited {
                    if !seen.insert(k.clone()) {
                        v.push(format!("run {id}: table {k} reported twice"));
                    }
                }
                match &tables {
                    Some(list) => {
                        let got: Vec<String> = visited.iter().map(|t| t.0.clone()).collect();
                        if &got != list {
                            v.push(format!("run {id}: table list {list:?} visited {got:?}"));
                        }
                    }
                    None => {
                        let got: Vec<String> = visited.iter().map(|t| t.0.clone()).collect();
                        let start = start_at.as_ref().map_or(0, |s| keys.partition_point(|k| k < s));
                        let mut want = keys[start..].to_vec();
                        want.extend_from_slice(&keys[..start]);
                        if got != want {
                            v.push(format!("run {id}: full catalog from {start_at:?} visited {got:?}, want {want:?}"));
                        }
                    }
                }
                if report.cancellation == RunCancellation::Cancelled && report.graph_commit_id.is_some() {
                    v.push(format!("run {id}: cancelled but published {:?}", report.graph_commit_id));
                }
                let full = tables.is_none() && report.cancellation != RunCancellation::Cancelled;
                if full && report.resume_at != first_stop {
                    v.push(format!("run {id}: resume_at {:?} but first gate/budget deferral {:?}; cancellation {:?} cancelled_after {:?} hold {:?} start_at {:?} gate_hold {:?} planning {planning_c} commit {:?} tables {:?}", report.resume_at, first_stop, report.cancellation, cancelled_after, hold, start_at, report.gate_hold, report.graph_commit_id, visited.iter().map(|t| (t.0.as_str(), t.1.as_str())).collect::<Vec<_>>()));
                }
                if !full && report.resume_at.is_some() {
                    v.push(format!("run {id}: resume_at {:?} on a table-list or cancelled run", report.resume_at));
                }
                // the first table always starts: never deferred by gate hold or byte budget
                if let Some(first) = report.tables.first()
                    && matches!(first.outcome, TableOptimizeOutcome::Deferred(DeferReason::GateHoldLimit) | TableOptimizeOutcome::Deferred(DeferReason::ByteBudget))
                {
                    v.push(format!("run {id}: first table {} deferred {:?}", first.type_key, first.outcome));
                }
                for t in &report.tables {
                    if matches!(t.outcome, TableOptimizeOutcome::Failed) {
                        let ok = t.failure.as_deref().is_some_and(evidence_ok);
                        if !ok {
                            v.push(format!("run {id}: table {} failed without definite evidence: {:?}", t.type_key, t.failure));
                        }
                    }
                }
                Ok(RunOk {
                    input_commit: report.input_commit.clone(),
                    commit: report.graph_commit_id.clone(),
                    cancellation: report.cancellation,
                    gate_hold: report.gate_hold,
                    visited,
                    resume_at: report.resume_at.clone(),
                    first_stop,
                    max_table,
                })
            }
            Err(error) => {
                let msg = error.to_string();
                let mut v = violations.lock().unwrap();
                if !evidence_ok(&error) && !msg.contains("policy") {
                    v.push(format!("run {id}: error without BeforeEffect/Unpublished evidence ({:?}): {msg}", error.completion_evidence()));
                }
                if authority == AuthorityCapture::UnderGates && error.is_read_set_changed() {
                    v.push(format!("run {id}: UnderGates run refused for a moved head: {msg}"));
                }
                Err(msg.chars().take(200).collect())
            }
        };
        runs.lock().unwrap().push(RunRecord {
            id,
            started,
            ended,
            authority,
            hold,
            tables,
            start_at,
            cancelled_after,
            outcome,
        });
        tokio::time::sleep(Duration::from_millis(rng.below(200))).await;
    }
}

async fn check_phase(db: &Session, ledger: &Mutex<Ledger>, runs: &Mutex<Vec<RunRecord>>, violations: &Mutex<Vec<String>>, phase: &str) {
    let check_unclaimed = !runs.lock().unwrap().is_empty();
    let l = ledger.lock().unwrap();
    let persons = l.persons.clone();
    let docs = l.docs.clone();
    let companies = l.companies.clone();
    drop(l);
    // acknowledged rows present
    for (table, want, prop) in [("node:Person", &persons, "name"), ("node:Doc", &docs, "slug"), ("node:Company", &companies, "name")] {
        let snapshot = db.snapshot_of(omnigraph::db::ReadTarget::branch("main")).await.unwrap();
        let mut have = BTreeSet::new();
        let ds = snapshot.open_dataset(table).await.unwrap();
        use futures::TryStreamExt;
        let mut scan = ds.scan();
        scan.project(&[prop]).unwrap();
        let batches: Vec<_> = scan.try_into_stream().await.unwrap().try_collect().await.unwrap();
        for b in batches {
            let col = b.column(0).as_any().downcast_ref::<arrow_array::StringArray>().unwrap();
            for i in 0..col.len() {
                have.insert(col.value(i).to_string());
            }
        }
        let missing: Vec<_> = want.difference(&have).take(5).collect();
        if !missing.is_empty() {
            violations.lock().unwrap().push(format!("{phase}: {} acknowledged {table} rows missing, e.g. {missing:?}", want.difference(&have).count()));
        }
    }
    // the last acknowledged value of every person a writer updated
    let ages = ledger.lock().unwrap().ages.clone();
    {
        let snapshot = db.snapshot_of(omnigraph::db::ReadTarget::branch("main")).await.unwrap();
        let ds = snapshot.open_dataset("node:Person").await.unwrap();
        use futures::TryStreamExt;
        let mut scan = ds.scan();
        scan.project(&["name", "age"]).unwrap();
        let batches: Vec<arrow_array::RecordBatch> = scan.try_into_stream().await.unwrap().try_collect().await.unwrap();
        let mut stale = Vec::new();
        for b in &batches {
            let names = b.column(0).as_any().downcast_ref::<arrow_array::StringArray>().unwrap();
            let vals = b.column(1).as_any().downcast_ref::<arrow_array::Int32Array>().unwrap();
            for i in 0..b.num_rows() {
                if let Some(Some(want)) = ages.get(names.value(i)) {
                    let got = (!vals.is_null(i)).then(|| i64::from(vals.value(i)));
                    if got != Some(*want) {
                        stale.push(format!("{}: {:?} != {}", names.value(i), got, want));
                    }
                }
            }
        }
        if !stale.is_empty() {
            violations.lock().unwrap().push(format!("{phase}: {} persons lost their last acknowledged age, e.g. {:?}", stale.len(), &stale[..stale.len().min(3)]));
        }
    }
    // publications against main's history
    let commits = db.list_commits(Some("main")).await.unwrap();
    let by_id: BTreeMap<String, _> = commits.iter().map(|c| (c.graph_commit_id.clone(), c.clone())).collect();
    let head = commits.iter().max_by_key(|c| c.graph_manifest_version).unwrap();
    let mut chain = BTreeSet::new();
    let mut cur = Some(head.clone());
    while let Some(c) = cur {
        chain.insert(c.graph_commit_id.clone());
        cur = c.parent_commit_id.as_ref().and_then(|p| by_id.get(p)).cloned();
    }
    let runs = runs.lock().unwrap();
    let mut claimed = BTreeSet::new();
    for r in runs.iter() {
        if let Ok(ok) = &r.outcome
            && let Some(commit) = &ok.commit
        {
            if !claimed.insert(commit.clone()) {
                violations.lock().unwrap().push(format!("{phase}: commit {commit} reported by two runs"));
            }
            match by_id.get(commit) {
                None => violations.lock().unwrap().push(format!("{phase}: run {} commit {commit} not in main's history", r.id)),
                Some(c) => {
                    if !chain.contains(commit) {
                        violations.lock().unwrap().push(format!("{phase}: run {} commit {commit} not on main's first-parent chain", r.id));
                    }
                    if c.parent_commit_id != ok.input_commit {
                        violations.lock().unwrap().push(format!("{phase}: run {} commit parent {:?} != input {:?}", r.id, c.parent_commit_id, ok.input_commit));
                    }
                    if c.actor_id.as_deref() != Some(OPTIMIZER) {
                        violations.lock().unwrap().push(format!("{phase}: run {} commit actor {:?}", r.id, c.actor_id));
                    }
                }
            }
        }
    }
    for c in &commits {
        if check_unclaimed && c.actor_id.as_deref() == Some(OPTIMIZER) && !claimed.contains(&c.graph_commit_id) {
            violations.lock().unwrap().push(format!("{phase}: optimizer commit {} claimed by no successful run", c.graph_commit_id));
        }
    }
}

fn summarize(ledger: &Mutex<Ledger>, runs: &Mutex<Vec<RunRecord>>) -> String {
    let l = ledger.lock().unwrap();
    let runs = runs.lock().unwrap();
    let mut out = String::new();
    let ok = runs.iter().filter(|r| r.outcome.is_ok()).count();
    let published = runs.iter().filter(|r| matches!(&r.outcome, Ok(o) if o.commit.is_some())).count();
    let mut errs: BTreeMap<String, u64> = BTreeMap::new();
    for r in runs.iter() {
        if let Err(e) = &r.outcome {
            let key = format!("{:?}: {}", r.authority, e.chars().take(80).collect::<String>());
            *errs.entry(key).or_default() += 1;
        }
    }
    // writer wait vs gate hold: for each write, the gate hold of any run that overlapped it
    let mut worst = Vec::new();
    for (start, lat, kind) in &l.writes {
        let end = *start + *lat;
        let overlapping: Option<(&RunRecord, &RunOk)> = runs
            .iter()
            .filter_map(|r| r.outcome.as_ref().ok().map(|o| (r, o)))
            .filter(|(r, _)| r.started < end && r.ended > *start)
            .max_by_key(|(_, o)| o.gate_hold);
        let bound = overlapping.map(|(r, o)| r.hold.unwrap_or(o.gate_hold) + o.max_table);
        worst.push((*lat, *kind, bound, overlapping.map(|(r, o)| (r.id, o.gate_hold))));
    }
    worst.sort_by(|a, b| b.0.cmp(&a.0));
    let lats: Vec<f64> = { let mut v: Vec<f64> = l.writes.iter().map(|w| w.1.as_secs_f64()).collect(); v.sort_by(|a, b| a.partial_cmp(b).unwrap()); v };
    let p = |q: f64| lats.get(((lats.len() as f64 - 1.0) * q) as usize).copied().unwrap_or(0.0);
    let holds: Vec<f64> = runs.iter().filter_map(|r| r.outcome.as_ref().ok()).map(|o| o.gate_hold.as_secs_f64()).collect();
    let excess: Vec<f64> = runs.iter().filter_map(|r| r.outcome.as_ref().ok().map(|o| (r, o))).filter_map(|(r, o)| r.hold.map(|h| o.gate_hold.as_secs_f64() - h.as_secs_f64() - o.max_table.as_secs_f64())).collect();
    out += &format!(
        "runs {} ok {ok} published {published}; writes {} (p50 {:.3}s p99 {:.3}s max {:.3}s); persons {} docs {} companies {}\n",
        runs.len(), l.writes.len(), p(0.5), p(0.99), p(1.0), l.persons.len(), l.docs.len(), l.companies.len()
    );
    out += &format!(
        "gate_hold max {:.3}s; gate_hold - (max_gate_hold + longest table) max {:.3}s\n",
        holds.iter().cloned().fold(0.0, f64::max), excess.iter().cloned().fold(f64::MIN, f64::max)
    );
    if let Some((r, o)) = runs
        .iter()
        .filter_map(|r| r.outcome.as_ref().ok().map(|o| (r, o)))
        .filter(|(r, _)| r.hold.is_some())
        .max_by(|(ra, oa), (rb, ob)| {
            let ea = oa.gate_hold.as_secs_f64() - ra.hold.unwrap().as_secs_f64() - oa.max_table.as_secs_f64();
            let eb = ob.gate_hold.as_secs_f64() - rb.hold.unwrap().as_secs_f64() - ob.max_table.as_secs_f64();
            ea.partial_cmp(&eb).unwrap()
        })
    {
        out += &format!(
            "worst excess run {}: hold {:?} gate_hold {:?} wall {:?} commit {:?} cancellation {:?} tables {:?}\n",
            r.id, r.hold, o.gate_hold, r.ended - r.started, o.commit.is_some(), o.cancellation,
            o.visited.iter().map(|(k, out, d)| format!("{k}={out}@{}ms", d.as_millis())).collect::<Vec<_>>()
        );
    }
    out += &format!("run errors: {errs:#?}\nwrite errors: {:#?}\n", l.write_errors);
    out += "slowest writes (latency, kind, bound from overlapping run, (run, gate_hold)):\n";
    for w in worst.iter().take(8) {
        out += &format!("  {:?}\n", w);
    }
    out
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "hunt: stress/ campaign 1, engine optimize beside live writers; STRESS_SECONDS sets each phase's length"]
async fn engine_optimize_beside_live_writers() {
    let seconds: u64 = std::env::var("STRESS_SECONDS").ok().and_then(|v| v.parse().ok()).unwrap_or(60);
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().join("g.omni");
    let db = session(Omnigraph::init(root.to_str().unwrap(), SCHEMA).await.unwrap());
    let keys = {
        let mut k = vec![
            "edge:Authored".to_string(), "edge:Knows".to_string(), "edge:WorksAt".to_string(),
            "node:Company".to_string(), "node:Doc".to_string(), "node:Person".to_string(),
        ];
        k.sort();
        k
    };
    let ledger = Arc::new(Mutex::new(Ledger::default()));
    let runs = Arc::new(Mutex::new(Vec::new()));
    let violations = Arc::new(Mutex::new(Vec::new()));
    let seq = Arc::new(AtomicU64::new(0));

    // phase A: writers, branch merges and two optimizers
    let stop = Arc::new(AtomicBool::new(false));
    let mut tasks = Vec::new();
    for w in 0..4 {
        tasks.push(tokio::spawn(writer(db.clone(), Arc::clone(&ledger), Arc::clone(&stop), w)));
    }
    tasks.push(tokio::spawn(brancher(db.clone(), Arc::clone(&ledger), Arc::clone(&stop))));
    let optimizers: u64 = std::env::var("STRESS_OPTIMIZERS").ok().and_then(|v| v.parse().ok()).unwrap_or(2);
    for o in 0..optimizers {
        tasks.push(tokio::spawn(optimizer(db.clone(), keys.clone(), Arc::clone(&stop), Arc::clone(&runs), Arc::clone(&violations), Arc::clone(&seq), 1000 + o)));
    }
    tokio::time::sleep(Duration::from_secs(seconds)).await;
    stop.store(true, Ordering::SeqCst);
    for t in tasks {
        t.await.unwrap();
    }
    check_phase(&db, &ledger, &runs, &violations, "phase A").await;
    eprintln!("=== phase A\n{}", summarize(&ledger, &runs));

    // phase B: schema apply beside writers and optimize (no branches)
    let stop = Arc::new(AtomicBool::new(false));
    let mut tasks = Vec::new();
    for w in 10..13 {
        tasks.push(tokio::spawn(writer(db.clone(), Arc::clone(&ledger), Arc::clone(&stop), w)));
    }
    tasks.push(tokio::spawn(optimizer(db.clone(), keys.clone(), Arc::clone(&stop), Arc::clone(&runs), Arc::clone(&violations), Arc::clone(&seq), 2000)));
    let mut schema = SCHEMA.to_string();
    for n in 0..5 {
        tokio::time::sleep(Duration::from_secs((seconds / 5).max(2))).await;
        schema = schema.replacen("    industry: String?", &format!("    industry: String?\n    extra{n}: String?"), 1);
        if let Err(e) = db.apply_schema_as(&schema, Some("schema-writer")).await {
            violations.lock().unwrap().push(format!("phase B: schema apply {n} failed: {e}"));
        }
    }
    stop.store(true, Ordering::SeqCst);
    for t in tasks {
        t.await.unwrap();
    }
    check_phase(&db, &ledger, &runs, &violations, "phase B").await;
    eprintln!("=== phase B\n{}", summarize(&ledger, &runs));

    // cleanup, then every live branch and main still read
    let stats = db.cleanup_as(omnigraph::db::CleanupPolicyOptions { keep_versions: Some(1), older_than: None }, Some(OPTIMIZER)).await;
    match stats {
        Ok(stats) => {
            for s in stats.iter().filter(|s| s.error.is_some()) {
                violations.lock().unwrap().push(format!("cleanup error on {}: {:?}", s.type_key, s.error));
            }
        }
        Err(e) => violations.lock().unwrap().push(format!("cleanup failed: {e}")),
    }
    let reopened = session(Omnigraph::open(root.to_str().unwrap()).await.unwrap());
    check_phase(&reopened, &ledger, &Mutex::new(Vec::new()), &violations, "after cleanup").await;
    for branch in reopened.branch_list().await.unwrap() {
        if let Err(e) = reopened.snapshot_of(omnigraph::db::ReadTarget::branch(branch.as_str())).await {
            violations.lock().unwrap().push(format!("after cleanup: branch {branch} unreadable: {e}"));
        }
    }
    let v = violations.lock().unwrap();
    eprintln!("=== violations: {}", v.len());
    for x in v.iter().take(60) {
        eprintln!("  {x}");
    }
    assert!(v.is_empty(), "{} violations", v.len());
}

/// Deterministic reproduction of the early pipeline exit: one table's rewrite
/// is held while the planner runs `planning` tables ahead; those are no-ops,
/// so the stage loop drains them without staging and the loop ends with the
/// rest of the catalog never planned. Expected (RFC §1.3): every table of a
/// full-catalog run with no bound reached is visited, and `resume_at` names the
/// first table a bound stopped. Observed: the tail is reported
/// `deferred(gate_hold_limit)` with no `resume_at`, with no gate hold set.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "hunt: stress/ deterministic repro of the optimize pipeline's early exit"]
async fn stress_repro_pipeline_exits_before_planning_the_tail() {
    let _scenario = omnigraph::seams::FailScenario::setup();
    let mut schema = String::new();
    for n in 0..12 {
        schema += &format!("node T{n:02} {{\n    k: String @key\n}}\n");
    }
    let dir = tempfile::tempdir().unwrap();
    let db = session(Omnigraph::init(dir.path().to_str().unwrap(), &schema).await.unwrap());
    for n in 0..12 {
        let row = serde_json::json!({"type": format!("T{n:02}"), "data": {"k": "a"}}).to_string();
        db.load_as("main", None, &row, LoadMode::Merge, None).await.unwrap();
    }
    db.optimize_as(None, OptimizeRequest::default()).await.unwrap();
    // three small fragments on the first table only
    for i in 0..3 {
        let row = serde_json::json!({"type": "T00", "data": {"k": format!("x{i}")}}).to_string();
        db.load_as("main", None, &row, LoadMode::Merge, None).await.unwrap();
    }
    let mut failures = Vec::new();
    for planning in [1usize, 2, 4, 8] {
        let (installed, hold) = omnigraph::seams::catalog::OPTIMIZE_BEFORE_COMPACT.hold();
        let release = std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(500));
            hold.release();
        });
        let report = db
            .optimize_as(None, OptimizeRequest::default().with_concurrency(OptimizeConcurrency::new(planning, 1)))
            .await
            .unwrap();
        release.join().unwrap();
        drop(installed);
        let outcomes: Vec<String> = report.tables.iter().map(|t| format!("{}={}", t.type_key, t.outcome.as_str())).collect();
        eprintln!("planning {planning}: resume_at {:?} gate_hold {:?}\n  {}", report.resume_at, report.gate_hold, outcomes.join(" "));
        let deferred = report.tables.iter().filter(|t| matches!(t.outcome, TableOptimizeOutcome::Deferred(_))).count();
        if deferred > 0 {
            failures.push(format!("planning {planning}: no bound was set, yet {deferred} tables were deferred (resume_at {:?}): {outcomes:?}", report.resume_at));
        }
        // the next run's commit is new fragments, so reload them
        for i in 0..3 {
            let row = serde_json::json!({"type": "T00", "data": {"k": format!("p{planning}x{i}")}}).to_string();
            db.load_as("main", None, &row, LoadMode::Merge, None).await.unwrap();
        }
    }
    assert!(failures.is_empty(), "{failures:#?}");
}

/// RFC evidence row "logical data, row ids, schema, indexes, retained history,
/// live branches and Blob values are unchanged": every row of every table
/// (all non-Blob columns and `_rowid`) on main, on a branch and at five
/// historical commits, and every Doc's Blob bytes, before and after a sequence
/// of bounded optimizes over a history with updates, deletes and a branch.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "hunt: stress/ before/after equivalence of optimize"]
async fn stress_optimize_preserves_logical_state() {
    use arrow_cast::display::array_value_to_string;
    use futures::TryStreamExt;
    use omnigraph::db::ReadTarget;
    use omnigraph::{BlobCell, BlobContent, EntityKind};

    const TABLES: [&str; 6] = ["node:Person", "node:Company", "node:Doc", "edge:Knows", "edge:WorksAt", "edge:Authored"];
    async fn capture(db: &Session, target: ReadTarget) -> BTreeMap<String, Vec<String>> {
        let snap = db.snapshot_of(target).await.unwrap();
        let mut out = BTreeMap::new();
        for table in TABLES {
            let ds = snap.open_dataset(table).await.unwrap();
            let cols: Vec<String> = ds
                .schema()
                .fields
                .iter()
                .filter(|f| f.name != "content")
                .map(|f| f.name.clone())
                .collect();
            let mut scan = ds.scan();
            scan.project(&cols).unwrap();
            scan.with_row_id();
            let batches: Vec<arrow_array::RecordBatch> = scan.try_into_stream().await.unwrap().try_collect().await.unwrap();
            let mut rows = Vec::new();
            for b in &batches {
                for i in 0..b.num_rows() {
                    let mut row = Vec::new();
                    for (c, field) in b.schema().fields().iter().enumerate() {
                        row.push(format!("{}={}", field.name(), array_value_to_string(b.column(c), i).unwrap()));
                    }
                    rows.push(row.join("|"));
                }
            }
            rows.sort();
            out.insert(table.to_string(), rows);
        }
        out
    }
    async fn blobs(db: &Session, target: ReadTarget, slugs: &[String]) -> BTreeMap<String, Vec<u8>> {
        let mut out = BTreeMap::new();
        for slug in slugs {
            let cell = BlobCell { entity: EntityKind::Node, type_name: "Doc".into(), id: slug.clone(), property: "content".into() };
            let Ok(read) = db.read_blob_at(target.clone(), cell).await else {
                out.insert(slug.clone(), b"<absent>".to_vec());
                continue;
            };
            let bytes = match read.content {
                BlobContent::Managed { reader, .. } => {
                    let mut v = Vec::new();
                    let mut start = 0;
                    while start < reader.len() {
                        let end = (start + omnigraph::BLOB_READ_RANGE_MAX_BYTES).min(reader.len());
                        v.extend_from_slice(&reader.read_range(start..end).await.unwrap());
                        start = end;
                    }
                    v
                }
                _ => b"<not managed>".to_vec(),
            };
            out.insert(slug.clone(), bytes);
        }
        out
    }

    let dir = tempfile::tempdir().unwrap();
    let db = session(Omnigraph::init(dir.path().to_str().unwrap(), SCHEMA).await.unwrap());
    let mut rng = Rng(42);
    let mut slugs = Vec::new();
    for b in 0..12 {
        let rows: Vec<_> = (0..40).map(|k| person("eq", b * 100 + k)).collect();
        db.load_as("main", None, &rows.iter().map(|(_, r)| r.as_str()).collect::<Vec<_>>().join("\n"), LoadMode::Merge, None).await.unwrap();
        let docs: Vec<_> = (0..3).map(|k| doc("eq", b * 10 + k, &mut rng)).collect();
        slugs.extend(docs.iter().map(|(s, _)| s.clone()));
        db.load_as("main", None, &docs.iter().map(|(_, r)| r.as_str()).collect::<Vec<_>>().join("\n"), LoadMode::Merge, None).await.unwrap();
        let company = serde_json::json!({"type": "Company", "data": {"name": format!("co{b}"), "industry": "x"}}).to_string();
        db.load_as("main", None, &company, LoadMode::Merge, None).await.unwrap();
        let mut edges = Vec::new();
        for k in 0..20u64 {
            edges.push(serde_json::json!({"edge": "Knows", "from": format!("eq-p{}", b * 100 + k), "to": format!("eq-p{}", b * 100 + k + 1), "data": {"since": 2000 + k as i32}}).to_string());
            edges.push(serde_json::json!({"edge": "WorksAt", "from": format!("eq-p{}", b * 100 + k), "to": format!("co{b}")}).to_string());
        }
        edges.push(serde_json::json!({"edge": "Authored", "from": format!("eq-p{}", b * 100), "to": format!("eq-d{}", b * 10)}).to_string());
        db.load_as("main", None, &edges.join("\n"), LoadMode::Merge, None).await.unwrap();
        for k in 0..5 {
            db.mutate_as("main", BUMP, "bump", &bump_params(&format!("eq-p{}", b * 100 + k), (b * 7 + k) as i64), None).await.unwrap();
        }
        let mut del = ParamMap::new();
        del.insert("n".to_string(), Literal::String(format!("eq-p{}", b * 100 + 39)));
        db.mutate_as("main", "query del($n: String) { delete Person where name = $n }", "del", &del, None).await.unwrap();
        if b == 5 {
            db.branch_create_as("side", None).await.unwrap();
            let rows: Vec<_> = (0..10).map(|k| person("side", k)).collect();
            db.load_as("side", None, &rows.iter().map(|(_, r)| r.as_str()).collect::<Vec<_>>().join("\n"), LoadMode::Merge, None).await.unwrap();
        }
    }
    let commits = db.list_commits(Some("main")).await.unwrap();
    let historical: Vec<String> = commits.iter().step_by((commits.len() / 5).max(1)).take(5).map(|c| c.graph_commit_id.clone()).collect();
    let targets: Vec<(String, ReadTarget)> = std::iter::once(("main".to_string(), ReadTarget::branch("main")))
        .chain(std::iter::once(("side".to_string(), ReadTarget::branch("side"))))
        .chain(historical.iter().map(|id| (id.clone(), ReadTarget::snapshot(omnigraph::db::SnapshotId::new(id.clone())))))
        .collect();
    let mut before = BTreeMap::new();
    for (name, target) in &targets {
        before.insert(name.clone(), (capture(&db, target.clone()).await, blobs(&db, target.clone(), &slugs).await));
    }
    // bounded runs until nothing is left to do, then one unbounded run
    let mut published = 0;
    for hold in [0u64, 0, 5, 50, 0] {
        let report = db
            .optimize_as(None, OptimizeRequest::default().with_limits(OptimizeLimits::default().with_max_gate_hold(Duration::from_millis(hold))))
            .await
            .unwrap();
        published += usize::from(report.graph_commit_id.is_some());
    }
    let report = db.optimize_as(None, OptimizeRequest::default()).await.unwrap();
    published += usize::from(report.graph_commit_id.is_some());
    let reopened = session(Omnigraph::open(dir.path().to_str().unwrap()).await.unwrap());
    let mut failures = Vec::new();
    for (name, target) in &targets {
        let after = (capture(&reopened, target.clone()).await, blobs(&reopened, target.clone(), &slugs).await);
        let (rows_before, blobs_before) = &before[name];
        for table in TABLES {
            if rows_before[table] != after.0[table] {
                let missing = rows_before[table].iter().filter(|r| !after.0[table].contains(r)).count();
                failures.push(format!("{name} {table}: {} rows before, {} after, {missing} differ", rows_before[table].len(), after.0[table].len()));
            }
        }
        if blobs_before != &after.1 {
            failures.push(format!("{name}: Blob bytes differ"));
        }
    }
    eprintln!("optimize published {published} commits; compared {} targets", targets.len());
    assert!(published > 0, "the fixture left optimize nothing to do");
    assert!(failures.is_empty(), "{failures:#?}");
}
