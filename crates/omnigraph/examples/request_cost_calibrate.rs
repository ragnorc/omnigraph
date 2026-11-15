#![recursion_limit = "256"]
//! Calibration instrument for request-aware plan cost: measures, on this
//! host, the CPU cost of the row units the planner's cost model counts, so
//! a request latency can be expressed in them.
//!
//! ```text
//! cargo run --release -p omnigraph-engine --example request_cost_calibrate -- build <dir> [persons]
//! cargo run --release -p omnigraph-engine --example request_cost_calibrate -- time <dir> [reps]
//! cargo run --release -p omnigraph-engine --example request_cost_calibrate -- shapes <dir> [reps] [latency_units...]
//! ```
//!
//! `shapes` runs the request-cost fixture queries warm under the local
//! profile and the object-store profile at each given latency, printing each
//! plan's choices, rows and median wall time.
//!
//! `build` writes a local graph shaped like the request-cost fixtures
//! (Person in 50 fragments, Company 200, WorksAt one edge per person,
//! uniform). `time` runs each probe warm, `reps` times after three
//! warm-ups, and prints the median wall time per frontier size `k`
//! companies (`k × |Person| / 200` edges) and the least-squares slope per
//! row of each operator the model prices:
//!
//! - `indexed`: an indexed hop from `k` companies, count only, traversal
//!   pinned `indexed`, minus the same query on a warm pinned CSR: both read
//!   the destination by `id_lookup` (the planner always scans it), so the
//!   difference's slope per edge row is one indexed-probe row (less a CSR
//!   neighbour read, nanoseconds);
//! - `id_lookup`: the warm-CSR query's own slope per destination row, count
//!   only and with three columns;
//! - `scan`: an aggregate over the whole Person table against the same over
//!   Company, per scanned row;
//! - `csr_build`: a fresh handle's first CSR-pinned hop against its first
//!   indexed hop, per edge row of the build.

use std::sync::Arc;
use std::time::{Duration, Instant};

use omnigraph::Session;
use omnigraph::db::{Omnigraph, ReadTarget};
use omnigraph::loader::LoadMode;
use omnigraph::settings::{SessionSettings, Traversal};
use omnigraph_compiler::ir::ParamMap;
use omnigraph_compiler::query::ast::Literal;

const SCHEMA: &str = "node City {\n    slug: String @key\n    name: String\n}\nnode Company {\n    slug: String @key\n    name: String\n}\nnode Person {\n    slug: String @key\n    name: String\n    age: I32\n}\nedge WorksAt: Person -> Company\nedge LocatedIn: Company -> City\n";
const CITIES: u64 = 20;
const COMPANIES: u64 = 200;
const FRAGMENTS: u64 = 50;

const QUERIES: &str = r#"
query hop($cs: [String]) {
    match {
        $c: Company
        $c.slug in $cs
        $p worksAt $c
    }
    return { count($p) as n }
}
query hop_columns($cs: [String]) {
    match {
        $c: Company
        $c.slug in $cs
        $p worksAt $c
        $p: Person
    }
    return { count($p) as n, sum($p.age) as total, min($p.name) as first }
}
query a_count_c_last() {
    match { $p: Person $p worksAt $c $c: Company { slug: "c-007" } }
    return { count($p) as n }
}
query a_count_c_first() {
    match { $c: Company { slug: "c-007" } $p worksAt $c $p: Person }
    return { count($p) as n }
}
query b_columns_c_last() {
    match { $p: Person $p worksAt $c $c: Company { slug: "c-007" } }
    return { count($p) as n, sum($p.age) as total, min($p.name) as first }
}
query b_columns_c_first() {
    match { $c: Company { slug: "c-007" } $p worksAt $c $p: Person }
    return { count($p) as n, sum($p.age) as total, min($p.name) as first }
}
query c_chain_city_last() {
    match { $p: Person $p worksAt $c $c locatedIn $t $t: City { slug: "city-03" } }
    return { count($p) as n }
}
query c_chain_city_first() {
    match { $t: City { slug: "city-03" } $c locatedIn $t $p worksAt $c $p: Person }
    return { count($p) as n }
}
query d_not_works_at() {
    match { $p: Person not { $p worksAt $c $c: Company { slug: "c-007" } } }
    return { count($p) as n }
}
query scan_person() {
    match { $p: Person }
    return { sum($p.age) as total, min($p.name) as first }
}
query scan_company() {
    match { $c: Company }
    return { count($c) as n, min($c.name) as first }
}
"#;

fn splitmix(mut x: u64) -> u64 {
    x = x.wrapping_add(0x9E37_79B9_7F4A_7C15);
    x = (x ^ (x >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    x = (x ^ (x >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    x ^ (x >> 31)
}

fn session(db: Omnigraph, traversal: Traversal) -> Session {
    Session::from_defaults(
        Arc::new(db),
        SessionSettings::default().with_traversal(traversal),
    )
}

async fn build(dir: &str, persons: u64) {
    let db = session(Omnigraph::init(dir, SCHEMA).await.unwrap(), Traversal::Auto);
    let mut companies = String::new();
    for c in 0..COMPANIES {
        companies.push_str(&format!(
            "{{\"type\":\"Company\",\"data\":{{\"slug\":\"c-{c:03}\",\"name\":\"Company {c:03}\"}}}}\n"
        ));
    }
    for t in 0..CITIES {
        companies.push_str(&format!(
            "{{\"type\":\"City\",\"data\":{{\"slug\":\"city-{t:02}\",\"name\":\"City {t:02}\"}}}}\n"
        ));
    }
    db.load_jsonl(&companies, LoadMode::Append).await.unwrap();
    let mut located = String::new();
    for c in 0..COMPANIES {
        located.push_str(&format!(
            "{{\"edge\":\"LocatedIn\",\"from\":\"c-{c:03}\",\"to\":\"city-{:02}\"}}\n",
            c % CITIES
        ));
    }
    let per = persons.div_ceil(FRAGMENTS);
    for fragment in 0..FRAGMENTS {
        let (start, end) = (fragment * per, ((fragment + 1) * per).min(persons));
        let mut rows = String::new();
        for i in start..end {
            rows.push_str(&format!(
                "{{\"type\":\"Person\",\"data\":{{\"slug\":\"p-{i:06}\",\"name\":\"Person {i:06}\",\"age\":{}}}}}\n",
                i % 60
            ));
        }
        db.load_jsonl(&rows, LoadMode::Append).await.unwrap();
    }
    for fragment in 0..FRAGMENTS {
        let (start, end) = (fragment * per, ((fragment + 1) * per).min(persons));
        let mut rows = String::new();
        for i in start..end {
            let c = splitmix(i) % COMPANIES;
            rows.push_str(&format!(
                "{{\"edge\":\"WorksAt\",\"from\":\"p-{i:06}\",\"to\":\"c-{c:03}\"}}\n"
            ));
        }
        db.load_jsonl(&rows, LoadMode::Append).await.unwrap();
    }
    db.load_jsonl(&located, LoadMode::Append).await.unwrap();
    db.ensure_indices_as(None).await.unwrap();
    println!("built {dir}: {persons} persons in {FRAGMENTS} fragments");
}

fn companies(k: u64) -> ParamMap {
    ParamMap::from([(
        "cs".to_string(),
        Literal::List(
            (0..k)
                .map(|c| Literal::String(format!("c-{c:03}")))
                .collect(),
        ),
    )])
}

async fn run(db: &Session, name: &str, params: &ParamMap) -> Duration {
    let started = Instant::now();
    let result = db
        .query(ReadTarget::branch("main"), QUERIES, name, params)
        .await
        .unwrap();
    let elapsed = started.elapsed();
    std::hint::black_box(result);
    elapsed
}

/// The plan's choices, once per probe, so the timing names its plan.
async fn plan_of(db: &Session, name: &str, params: &ParamMap) -> String {
    let run = db
        .query_inspected(ReadTarget::branch("main"), QUERIES, name, params)
        .await
        .unwrap();
    let explain = run.explain.to_value();
    let mut out = Vec::new();
    let mut pending = vec![&explain["physical_plan"]];
    while let Some(node) = pending.pop() {
        match node["node"].as_str() {
            Some("Expand") => out.push(format!(
                "expand ${}->${} {}",
                node["src"].as_str().unwrap_or(""),
                node["dst"].as_str().unwrap_or(""),
                node["mode"].as_str().unwrap_or("")
            )),
            Some("HashJoin") => out.push(format!(
                "hash_join ${}",
                node["binding"].as_str().unwrap_or("")
            )),
            Some("Scan") => out.push(format!(
                "scan ${} {}",
                node["binding"].as_str().unwrap_or(""),
                node["access"].as_str().unwrap_or("")
            )),
            _ => {}
        }
        if let Some(inputs) = node["inputs"].as_array() {
            pending.extend(inputs.iter());
        }
    }
    out.join(", ")
}

async fn median(db: &Session, name: &str, params: &ParamMap, reps: usize) -> f64 {
    for _ in 0..3 {
        run(db, name, params).await;
    }
    let mut times: Vec<f64> = Vec::with_capacity(reps);
    for _ in 0..reps {
        times.push(run(db, name, params).await.as_secs_f64() * 1e6);
    }
    times.sort_by(f64::total_cmp);
    times[reps / 2]
}

/// Least-squares slope and intercept of `y` over `x`.
fn fit(points: &[(f64, f64)]) -> (f64, f64) {
    let n = points.len() as f64;
    let (sx, sy) = points
        .iter()
        .fold((0.0, 0.0), |(a, b), (x, y)| (a + x, b + y));
    let (mx, my) = (sx / n, sy / n);
    let (num, den) = points.iter().fold((0.0, 0.0), |(a, b), (x, y)| {
        (a + (x - mx) * (y - my), b + (x - mx) * (x - mx))
    });
    let slope = num / den;
    (slope, my - slope * mx)
}

async fn time(dir: &str, reps: usize) {
    let indexed = session(Omnigraph::open(dir).await.unwrap(), Traversal::Indexed);
    let persons = {
        let r = indexed
            .query(
                ReadTarget::branch("main"),
                QUERIES,
                "scan_person",
                &ParamMap::new(),
            )
            .await
            .unwrap();
        std::hint::black_box(r);
        let snapshot = indexed
            .snapshot_of(ReadTarget::branch("main"))
            .await
            .unwrap();
        snapshot
            .open_dataset("node:Person")
            .await
            .unwrap()
            .count_rows(None)
            .await
            .unwrap() as f64
    };
    let per_company = persons / COMPANIES as f64;
    println!("graph {dir}: {persons} persons, {per_company} edges per company, {reps} reps");
    let csr = session(Omnigraph::open(dir).await.unwrap(), Traversal::Csr);
    run(&csr, "hop", &companies(1)).await;
    let ks = [1u64, 2, 4, 8, 16, 24];
    let (mut hop, mut lookup, mut cols) = (Vec::new(), Vec::new(), Vec::new());
    println!("k  edges  indexed_us  csr_us  csr_columns_us  plans");
    for &k in &ks {
        let params = companies(k);
        let edges = k as f64 * per_company;
        let i = median(&indexed, "hop", &params, reps).await;
        let c = median(&csr, "hop", &params, reps).await;
        let cc = median(&csr, "hop_columns", &params, reps).await;
        let (indexed_plan, csr_plan) = (
            plan_of(&indexed, "hop", &params).await,
            plan_of(&csr, "hop_columns", &params).await,
        );
        println!("{k:>3} {edges:>6} {i:>10.0} {c:>8.0} {cc:>10.0}  [{indexed_plan}] [{csr_plan}]");
        if indexed_plan.contains("hash_join") || csr_plan.contains("hash_join") {
            // The destination's access path changed: not the same work.
            continue;
        }
        hop.push((edges, i - c));
        lookup.push((edges, c));
        cols.push((edges, cc));
    }
    let (indexed_row, indexed_fixed) = fit(&hop);
    let (lookup_row, lookup_fixed) = fit(&lookup);
    let (cols_row, cols_fixed) = fit(&cols);
    let scan_p = median(&indexed, "scan_person", &ParamMap::new(), reps).await;
    let scan_c = median(&indexed, "scan_company", &ParamMap::new(), reps).await;
    let scan_row = (scan_p - scan_c) / (persons - COMPANIES as f64);

    // The CSR build: a fresh handle's first pinned-CSR hop against a fresh
    // handle's first pinned-indexed hop, each over all 200 companies.
    let all = companies(COMPANIES);
    let mut builds = Vec::new();
    for _ in 0..reps.min(7) {
        let csr = session(Omnigraph::open(dir).await.unwrap(), Traversal::Csr);
        let fresh = session(Omnigraph::open(dir).await.unwrap(), Traversal::Indexed);
        let one = companies(1);
        // Warm everything but the CSR: the hop over one company in indexed
        // mode reads the same tables.
        run(&csr, "scan_company", &ParamMap::new()).await;
        run(&fresh, "scan_company", &ParamMap::new()).await;
        let c = run(&csr, "hop", &one).await.as_secs_f64() * 1e6;
        let i = run(&fresh, "hop", &one).await.as_secs_f64() * 1e6;
        builds.push(c - i);
    }
    builds.sort_by(f64::total_cmp);
    let csr_build = builds[builds.len() / 2];
    let csr_row = csr_build / persons;
    let csr_warm = {
        let csr = session(Omnigraph::open(dir).await.unwrap(), Traversal::Csr);
        median(&csr, "hop", &all, reps).await
    };

    println!();
    println!("indexed-probe row: {indexed_row:.3} us (fixed {indexed_fixed:.0} us)");
    println!(
        "id_lookup row:     {lookup_row:.3} us (fixed {lookup_fixed:.0} us) = {:.2} indexed rows (count only; warm csr hop included)",
        lookup_row / indexed_row
    );
    println!(
        "id_lookup row, 3 columns: {cols_row:.3} us (fixed {cols_fixed:.0} us) = {:.2} indexed rows",
        cols_row / indexed_row
    );
    println!(
        "scan row:          {scan_row:.3} us = {:.2} indexed rows (Person {scan_p:.0} us, Company {scan_c:.0} us)",
        scan_row / indexed_row
    );
    println!(
        "csr build row:     {csr_row:.3} us = {:.2} indexed rows (build {csr_build:.0} us; warm csr hop over all {csr_warm:.0} us)",
        csr_row / indexed_row
    );
    println!(
        "latency_units for 20 ms: {:.0} indexed-probe rows",
        20_000.0 / indexed_row
    );
}

/// Every fixture shape, warm, under each storage profile pin: the plan's
/// choices, its rows and its median wall time on this host. The local wall
/// time is the plan's CPU (and local I/O) cost, which the DST measure omits.
async fn shapes(dir: &str, reps: usize, latencies: &[u64]) {
    use omnigraph::settings::StorageProfilePin;
    let names = [
        "a_count_c_last",
        "a_count_c_first",
        "b_columns_c_last",
        "b_columns_c_first",
        "c_chain_city_last",
        "c_chain_city_first",
        "d_not_works_at",
    ];
    let db = Arc::new(Omnigraph::open(dir).await.unwrap());
    let mut pins = vec![("local".to_string(), StorageProfilePin::Local)];
    for &latency in latencies {
        pins.push((
            format!("object_store@{latency}"),
            StorageProfilePin::ObjectStore {
                latency_units: Some(latency),
            },
        ));
    }
    println!("query\tprofile\tmedian_us\trows\tplan");
    for name in names {
        for (label, pin) in &pins {
            let session = Session::from_defaults(
                Arc::clone(&db),
                SessionSettings::default().with_storage_profile(*pin),
            );
            let params = ParamMap::new();
            let rows = session
                .query(ReadTarget::branch("main"), QUERIES, name, &params)
                .await
                .unwrap()
                .to_rust_json()
                .unwrap();
            let t = median(&session, name, &params, reps).await;
            println!(
                "{name}\t{label}\t{t:.0}\t{rows}\t{}",
                plan_of(&session, name, &params).await
            );
        }
    }
}

#[tokio::main]
async fn main() {
    let args: Vec<String> = std::env::args().collect();
    match args.get(1).map(String::as_str) {
        Some("build") => {
            let persons = args.get(3).map_or(200_000, |n| n.parse().unwrap());
            build(&args[2], persons).await;
        }
        Some("time") => {
            let reps = args.get(3).map_or(15, |n| n.parse().unwrap());
            time(&args[2], reps).await;
        }
        Some("shapes") => {
            let reps = args.get(3).map_or(9, |n| n.parse().unwrap());
            let latencies: Vec<u64> = args[4..].iter().map(|n| n.parse().unwrap()).collect();
            shapes(&args[2], reps, &latencies).await;
        }
        _ => eprintln!("usage: request_cost_calibrate build|time|shapes <dir> [n] [latency...]"),
    }
}
